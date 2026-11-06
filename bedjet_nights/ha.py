"""A small Home Assistant WebSocket API client.

Home Assistant keeps the BedJet's single Bluetooth connection through its BedJet integration.
This app never talks to the BedJet directly: it reads the BedJet's entities and calls their
actions, the same way a dashboard or an automation would.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

StateListener = Callable[[str, dict[str, Any] | None, dict[str, Any] | None], None]


class HomeAssistantError(Exception):
    """Home Assistant refused a request, or could not be reached."""


class HomeAssistant:
    def __init__(self, url: str, token: str, session: aiohttp.ClientSession) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._session = session
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self.connected = False
        self.version: str | None = None
        self.config: dict[str, Any] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.entities: list[dict[str, Any]] = []
        self.devices: dict[str, dict[str, Any]] = {}
        self.state_listeners: list[StateListener] = []
        self.ready_listeners: list[Callable[[], Awaitable[None]]] = []
        self.disconnect_listeners: list[Callable[[], None]] = []
        self.last_error: str | None = None

    @property
    def websocket_url(self) -> str:
        if self._url.startswith("https://"):
            return "wss://" + self._url[len("https://"):] + "/api/websocket"
        if self._url.startswith("http://"):
            return "ws://" + self._url[len("http://"):] + "/api/websocket"
        return self._url + "/api/websocket"

    @property
    def fahrenheit(self) -> bool:
        unit = (self.config.get("unit_system") or {}).get("temperature", "°C")
        return "F" in unit

    @property
    def time_zone(self) -> str:
        return self.config.get("time_zone") or "UTC"

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        for task in (self._task, self._refresh_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._ws is not None:
            await self._ws.close()

    async def _run_forever(self) -> None:
        delay = 2
        while True:
            try:
                await self._session_once()
                delay = 2
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - any failure means reconnect
                self.last_error = str(err) or type(err).__name__
                _LOGGER.warning("Home Assistant connection lost: %s", self.last_error)
            self._lost()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def _lost(self) -> None:
        was_connected = self.connected
        self.connected = False
        self._ws = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HomeAssistantError("Lost the connection to Home Assistant"))
        self._pending.clear()
        if was_connected:
            for listener in self.disconnect_listeners:
                listener()

    async def _session_once(self) -> None:
        async with self._session.ws_connect(self.websocket_url, heartbeat=30, max_msg_size=0) as ws:
            hello = await ws.receive_json(timeout=15)
            if hello.get("type") != "auth_required":
                raise HomeAssistantError(f"Unexpected greeting: {hello}")
            await ws.send_json({"type": "auth", "access_token": self._token})
            reply = await ws.receive_json(timeout=15)
            if reply.get("type") != "auth_ok":
                self.last_error = "Home Assistant refused the access token. Check HA_TOKEN."
                raise HomeAssistantError(self.last_error)
            self.version = reply.get("ha_version")
            self._ws = ws
            self._next_id = 1
            reader = asyncio.create_task(self._read(ws))
            try:
                await self._prime()
                self.connected = True
                self.last_error = None
                _LOGGER.info("Connected to Home Assistant %s", self.version)
                for listener in self.ready_listeners:
                    await listener()
                await reader
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            payload = message.json()
            for item in payload if isinstance(payload, list) else [payload]:
                self._dispatch(item)

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "result":
            future = self._pending.pop(message.get("id"), None)
            if future is None or future.done():
                return
            if message.get("success"):
                future.set_result(message.get("result"))
            else:
                error = message.get("error") or {}
                future.set_exception(HomeAssistantError(error.get("message") or str(error)))
        elif kind == "event":
            event = message.get("event") or {}
            event_type = event.get("event_type")
            if event_type == "state_changed":
                data = event.get("data") or {}
                entity_id = data.get("entity_id")
                new_state = data.get("new_state")
                old_state = self.states.get(entity_id)
                if new_state is None:
                    self.states.pop(entity_id, None)
                else:
                    self.states[entity_id] = new_state
                for listener in self.state_listeners:
                    try:
                        listener(entity_id, old_state, new_state)
                    except Exception:  # noqa: BLE001 - a listener bug must not drop the connection
                        _LOGGER.exception("State listener failed")
            elif event_type in ("entity_registry_updated", "device_registry_updated"):
                self._schedule_refresh()

    async def _prime(self) -> None:
        """Everything needed before the BedJets can be found and shown."""
        self.config = await self.call({"type": "get_config"})
        await self.call({"type": "subscribe_events", "event_type": "state_changed"})
        for event_type in ("entity_registry_updated", "device_registry_updated"):
            with contextlib.suppress(HomeAssistantError):
                await self.call({"type": "subscribe_events", "event_type": event_type})
        states = await self.call({"type": "get_states"})
        self.states = {state["entity_id"]: state for state in states}
        await self._load_registries()

    async def _load_registries(self) -> None:
        self.entities = await self.call({"type": "config/entity_registry/list"})
        devices = await self.call({"type": "config/device_registry/list"})
        self.devices = {device["id"]: device for device in devices}

    def _schedule_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            return

        async def refresh() -> None:
            await asyncio.sleep(2)
            try:
                await self._load_registries()
            except HomeAssistantError as err:
                _LOGGER.debug("Registry refresh failed: %s", err)
                return
            for listener in self.ready_listeners:
                await listener()

        self._refresh_task = asyncio.create_task(refresh())

    async def call(self, message: dict[str, Any], timeout: float = 30) -> Any:
        ws = self._ws
        if ws is None or ws.closed:
            raise HomeAssistantError("Not connected to Home Assistant")
        message_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await ws.send_json({**message, "id": message_id})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as err:
            raise HomeAssistantError("Home Assistant didn't answer in time") from err
        finally:
            self._pending.pop(message_id, None)

    async def call_service(self, domain: str, service: str, entity_id: str, **data: Any) -> None:
        _LOGGER.debug("%s.%s %s %s", domain, service, entity_id, data)
        await self.call(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": data,
                "target": {"entity_id": entity_id},
            }
        )

    async def set_config_entry_disabled(self, entry_id: str, disabled: bool) -> None:
        await self.call(
            {"type": "config_entries/disable", "entry_id": entry_id, "disabled_by": "user" if disabled else None}
        )
