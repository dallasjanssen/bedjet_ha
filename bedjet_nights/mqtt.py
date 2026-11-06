"""Optional: puts the night's controls into Home Assistant as entities, through MQTT discovery.

Each BedJet gets a "Tonight's schedule" select, Start and End night buttons, a "Start at
bedtime" switch, and sensors for the night in progress. They join the BedJet's existing device
in Home Assistant (matched by its Bluetooth address), so automations and dashboards can use them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiomqtt

from .config import Config
from .nights import ActionError, Nights

_LOGGER = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, config: Config, nights: Nights) -> None:
        self.config = config
        self.nights = nights
        self.base = config.mqtt_base_topic
        self.prefix = config.mqtt_discovery_prefix
        self.connected = False
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._dirty = asyncio.Event()
        self._announced: dict[str, str] = {}
        nights.listeners.append(self._dirty.set)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run_forever(self) -> None:
        delay = 2
        while True:
            try:
                await self._session()
                delay = 2
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttError as err:
                _LOGGER.warning("MQTT connection lost: %s", err)
            self.connected = False
            self._client = None
            self._announced.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _session(self) -> None:
        will = aiomqtt.Will(f"{self.base}/status", "offline", qos=1, retain=True)
        async with aiomqtt.Client(
            self.config.mqtt_host or "localhost",
            port=self.config.mqtt_port,
            username=self.config.mqtt_username,
            password=self.config.mqtt_password,
            identifier="bedjet-nights",
            will=will,
        ) as client:
            self._client = client
            self.connected = True
            _LOGGER.info("Connected to MQTT at %s", self.config.mqtt_host)
            await client.publish(f"{self.base}/status", "online", qos=1, retain=True)
            await client.subscribe(f"{self.base}/+/+/set")
            await client.subscribe(f"{self.prefix}/status")
            publisher = asyncio.create_task(self._publish_loop(client))
            try:
                async for message in client.messages:
                    await self._handle(str(message.topic), message.payload)
            finally:
                publisher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await publisher

    async def _publish_loop(self, client: aiomqtt.Client) -> None:
        self._dirty.set()
        while True:
            # The minutes left in a phase change without any event, so refresh once a minute too.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._dirty.wait(), 60)
            self._dirty.clear()
            await asyncio.sleep(0.5)
            try:
                await self._publish(client)
            except aiomqtt.MqttError as err:
                _LOGGER.debug("MQTT publish failed: %s", err)

    async def _handle(self, topic: str, payload: Any) -> None:
        text = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload or "")
        if topic == f"{self.prefix}/status":
            if text == "online":
                # Home Assistant restarted and forgot everything that wasn't retained.
                self._announced.clear()
                self._dirty.set()
            return
        parts = topic.split("/")
        if len(parts) != 4:
            return
        slug, field = parts[1], parts[2]
        unit = next((u for u in self.nights.units.values() if u.slug == slug), None)
        if unit is None:
            return
        try:
            if field == "tonight":
                schedule = next((s for s in self.nights.store.schedules() if s.name == text), None)
                if schedule:
                    self.nights.set_tonight(unit.id, schedule.id)
            elif field == "start":
                await self.nights.start_night(unit.id, source="home_assistant")
            elif field == "end":
                await self.nights.end_night(unit.id)
            elif field == "auto":
                self.nights.set_auto(unit.id, text.upper() == "ON")
        except ActionError as err:
            _LOGGER.warning("%s from Home Assistant: %s", field, err)
            self.nights.errors[unit.id] = str(err)
        self._dirty.set()

    # Publishing

    def _device(self, unit: Any) -> dict[str, Any]:
        connections = unit.bluetooth_connections
        if connections:
            # Joining the BedJet's own device keeps everything about it on one page.
            return {"connections": connections}
        return {
            "identifiers": [f"bedjet_nights_{unit.slug}"],
            "name": unit.name,
            "manufacturer": "BedJet",
            "model": unit.model,
        }

    def _configs(self, unit: Any, names: list[str]) -> dict[str, dict[str, Any]]:
        topic = f"{self.base}/{unit.slug}"
        common = {
            "availability_topic": f"{self.base}/status",
            "device": self._device(unit),
            "origin": {"name": "BedJet Nights", "support_url": "https://github.com/dallasjanssen/bedjet_ha"},
        }

        def entity(component: str, key: str, name: str, **extra: Any) -> tuple[str, dict[str, Any]]:
            return f"{component}/{key}", {
                **common,
                "name": name,
                "unique_id": f"bedjet_nights_{unit.slug}_{key}",
                "default_entity_id": f"{component}.{unit.slug}_{key}",
                **extra,
            }

        configs = dict([
            entity("select", "tonight", "Tonight's schedule", icon="mdi:weather-night",
                   state_topic=f"{topic}/tonight", command_topic=f"{topic}/tonight/set", options=names or ["None"]),
            entity("button", "start_night", "Start night", icon="mdi:play", command_topic=f"{topic}/start/set"),
            entity("button", "end_night", "End night", icon="mdi:stop", command_topic=f"{topic}/end/set"),
            entity("switch", "start_at_bedtime", "Start at bedtime", icon="mdi:bed-clock",
                   state_topic=f"{topic}/auto", command_topic=f"{topic}/auto/set"),
            entity("binary_sensor", "night_running", "Night running", device_class="running",
                   state_topic=f"{topic}/running"),
            entity("sensor", "night", "Night", icon="mdi:sleep", state_topic=f"{topic}/night"),
            entity("sensor", "night_ends", "Night ends", device_class="timestamp", state_topic=f"{topic}/ends"),
        ])
        return configs

    async def _publish(self, client: aiomqtt.Client) -> None:
        names = [schedule.name for schedule in self.nights.store.schedules()]
        for unit in list(self.nights.units.values()):
            for path, config in self._configs(unit, names).items():
                topic = f"{self.prefix}/{path.split('/')[0]}/bedjet_nights_{unit.slug}/{path.split('/')[1]}/config"
                body = json.dumps(config)
                if self._announced.get(topic) != body:
                    await client.publish(topic, body, qos=1, retain=True)
                    self._announced[topic] = body
            snapshot = self.nights.unit_snapshot(unit)
            run = self.nights.runs.get(unit.id)
            tonight = self.nights.tonight(unit.id)
            ends = (
                datetime.fromtimestamp(run.ends_at, timezone.utc).isoformat(timespec="seconds") if run else "None"
            )
            topic = f"{self.base}/{unit.slug}"
            states = {
                "tonight": tonight.name if tonight else "None",
                "auto": "ON" if snapshot["auto"].get("enabled") else "OFF",
                "running": "ON" if run else "OFF",
                "night": snapshot["night"],
                "ends": ends,
            }
            for key, value in states.items():
                await client.publish(f"{topic}/{key}", value, qos=1, retain=True)
