"""WebSocket commands behind the BedJet Nights panel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_UPDATE
from .nights import Nights

PREFIX = f"{DOMAIN}/"


def _nights(hass: HomeAssistant) -> Nights | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


Handler = Callable[[HomeAssistant, websocket_api.ActiveConnection, dict[str, Any], Nights], Awaitable[Any]]


def _action(handler: Handler) -> websocket_api.AsyncWebSocketCommandHandler:
    """Runs a command and answers with the new state, or with the reason it couldn't be done."""

    @wraps(handler)
    async def run(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        nights = _nights(hass)
        if nights is None:
            connection.send_error(msg["id"], "not_loaded", "BedJet Nights isn't running.")
            return
        try:
            result = await handler(hass, connection, msg, nights)
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "failed", str(err))
            return
        connection.send_result(msg["id"], result if result is not None else nights.snapshot())

    return run


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "state"})
@websocket_api.async_response
@_action
async def ws_state(hass, connection, msg, nights: Nights) -> Any:
    return nights.snapshot()


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Sends the whole state now and again whenever it changes, a little after a burst of changes."""
    nights = _nights(hass)
    if nights is None:
        connection.send_error(msg["id"], "not_loaded", "BedJet Nights isn't running.")
        return
    pending: asyncio.TimerHandle | None = None

    @callback
    def send() -> None:
        nonlocal pending
        pending = None
        current = _nights(hass)
        if current is not None:
            connection.send_message(websocket_api.event_message(msg["id"], current.snapshot()))

    @callback
    def changed() -> None:
        nonlocal pending
        if pending is None:
            pending = hass.loop.call_later(0.25, send)

    unsub = async_dispatcher_connect(hass, SIGNAL_UPDATE, changed)

    @callback
    def unsubscribe() -> None:
        unsub()
        if pending is not None:
            pending.cancel()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    send()


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "schedule/save", vol.Required("schedule"): dict})
@websocket_api.async_response
@_action
async def ws_save_schedule(hass, connection, msg, nights: Nights) -> Any:
    return {"schedule": nights.save_schedule(msg["schedule"]).to_dict()}


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "schedule/delete", vol.Required("schedule_id"): str})
@websocket_api.async_response
@_action
async def ws_delete_schedule(hass, connection, msg, nights: Nights) -> Any:
    nights.delete_schedule(msg["schedule_id"])


@websocket_api.websocket_command(
    {vol.Required("type"): PREFIX + "tonight", vol.Required("unit_id"): str, vol.Required("schedule_id"): str}
)
@websocket_api.async_response
@_action
async def ws_tonight(hass, connection, msg, nights: Nights) -> Any:
    nights.set_tonight(msg["unit_id"], msg["schedule_id"])


@websocket_api.websocket_command(
    {vol.Required("type"): PREFIX + "start", vol.Required("unit_id"): str, vol.Optional("schedule_id"): str}
)
@websocket_api.async_response
@_action
async def ws_start(hass, connection, msg, nights: Nights) -> Any:
    await nights.start_night(msg["unit_id"], msg.get("schedule_id"))


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "end", vol.Required("unit_id"): str})
@websocket_api.async_response
@_action
async def ws_end(hass, connection, msg, nights: Nights) -> Any:
    await nights.end_night(msg["unit_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): PREFIX + "remote",
        vol.Required("unit_id"): str,
        vol.Optional("mode"): str,
        vol.Optional("program"): str,
        vol.Optional("temperature"): vol.Coerce(int),
        vol.Optional("fan"): vol.Coerce(int),
        vol.Optional("runtime"): vol.Coerce(int),
        vol.Optional("led"): bool,
        vol.Optional("muted"): bool,
    }
)
@websocket_api.async_response
@_action
async def ws_remote(hass, connection, msg, nights: Nights) -> Any:
    body = {key: value for key, value in msg.items() if key not in ("type", "id", "unit_id")}
    await nights.remote(msg["unit_id"], body)


@websocket_api.websocket_command(
    {
        vol.Required("type"): PREFIX + "auto",
        vol.Required("unit_id"): str,
        vol.Required("enabled"): bool,
        vol.Optional("days"): [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))],
    }
)
@websocket_api.async_response
@_action
async def ws_auto(hass, connection, msg, nights: Nights) -> Any:
    nights.set_auto(msg["unit_id"], msg["enabled"], msg.get("days"))


@websocket_api.websocket_command(
    {vol.Required("type"): PREFIX + "lend", vol.Required("unit_id"): str, vol.Required("minutes"): vol.Coerce(int)}
)
@websocket_api.async_response
@_action
async def ws_lend(hass, connection, msg, nights: Nights) -> Any:
    # Lending pauses an integration, which only an administrator may do.
    if not connection.user.is_admin:
        raise HomeAssistantError("Only an administrator can lend the BedJet to a phone.")
    await nights.lend(msg["unit_id"], msg["minutes"])


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "reclaim", vol.Required("unit_id"): str})
@websocket_api.async_response
@_action
async def ws_reclaim(hass, connection, msg, nights: Nights) -> Any:
    await nights.reclaim(msg["unit_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): PREFIX + "history",
        vol.Required("unit_id"): str,
        vol.Optional("nights", default=14): vol.All(vol.Coerce(int), vol.Range(min=1, max=90)),
    }
)
@websocket_api.async_response
@_action
async def ws_history(hass, connection, msg, nights: Nights) -> Any:
    return {"nights": nights.history(msg["unit_id"], msg["nights"])}


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in (
        ws_state, ws_subscribe, ws_save_schedule, ws_delete_schedule, ws_tonight, ws_start, ws_end,
        ws_remote, ws_auto, ws_lend, ws_reclaim, ws_history,
    ):
        websocket_api.async_register_command(hass, command)
