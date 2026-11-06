"""One BedJet, as Home Assistant's BedJet integration presents it.

Reads the climate entity and its siblings on the same device (runtime, room temperature, LED,
beeps), and turns Lull-style settings (mode, half degrees Celsius, fan percent, minutes) into
Home Assistant actions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .ha import HomeAssistant, HomeAssistantError
from .model import Mode

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = ("unavailable", "unknown")


@dataclass(frozen=True)
class Air:
    """What the BedJet is doing: the setting Lull's history records."""

    mode: Mode
    temperature: int | None
    """Target in half degrees Celsius."""
    fan: int | None


def to_half_celsius(value: float, fahrenheit: bool) -> int:
    celsius = (value - 32) * 5 / 9 if fahrenheit else value
    return int(round(celsius * 2))


def to_celsius(value: Any, fahrenheit: bool) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round((number - 32) * 5 / 9, 2) if fahrenheit else number


def from_half_celsius(half: int, fahrenheit: bool) -> float:
    celsius = half / 2
    return round(celsius * 9 / 5 + 32, 1) if fahrenheit else celsius


def _matches(entry: dict[str, Any], key: str) -> bool:
    return entry.get("translation_key") == key or str(entry.get("unique_id", "")).endswith(f"_{key}")


class BedJetUnit:
    """A BedJet found in Home Assistant, addressed by its climate entity."""

    def __init__(self, ha: HomeAssistant, climate: dict[str, Any]) -> None:
        self.ha = ha
        self.id: str = climate["entity_id"]
        self.slug = self.id.split(".", 1)[1]
        self.registry = climate
        self.device: dict[str, Any] = {}
        self.runtime_entity: str | None = None
        self.ambient_entity: str | None = None
        self.led_entity: str | None = None
        self.mute_entity: str | None = None
        self.lock = asyncio.Lock()
        """Commands go one at a time, so a schedule and a person never interleave half a change."""
        self.commanded_at = 0.0
        self.refresh_links()

    # Discovery

    def refresh_links(self) -> None:
        for entry in self.ha.entities:
            if entry.get("entity_id") == self.id:
                self.registry = entry
        device_id = self.registry.get("device_id")
        self.device = self.ha.devices.get(device_id, {}) if device_id else {}
        siblings = [
            entry for entry in self.ha.entities
            if device_id and entry.get("device_id") == device_id and entry.get("entity_id") != self.id
        ]
        self.runtime_entity = self._find(siblings, "number", "runtime_remaining")
        self.ambient_entity = self._find(siblings, "sensor", "ambient_temperature")
        self.led_entity = self._find(siblings, "switch", "enable_led")
        self.mute_entity = self._find(siblings, "switch", "mute_beeps")

    @staticmethod
    def _find(siblings: list[dict[str, Any]], domain: str, key: str) -> str | None:
        for entry in siblings:
            entity_id = entry.get("entity_id", "")
            if entity_id.startswith(f"{domain}.") and _matches(entry, key) and not entry.get("disabled_by"):
                return entity_id
        return None

    @property
    def config_entry_id(self) -> str | None:
        return self.registry.get("config_entry_id")

    @property
    def bluetooth_connections(self) -> list[list[str]]:
        return [list(pair) for pair in self.device.get("connections") or []]

    @property
    def name(self) -> str:
        state = self.state
        return (
            self.device.get("name_by_user")
            or (state or {}).get("attributes", {}).get("friendly_name")
            or self.device.get("name")
            or self.slug.replace("_", " ").title()
        )

    @property
    def model(self) -> str:
        return self.device.get("model") or "BedJet"

    @property
    def state(self) -> dict[str, Any] | None:
        return self.ha.states.get(self.id)

    @property
    def available(self) -> bool:
        state = self.state
        return bool(self.ha.connected and state and state.get("state") not in UNAVAILABLE)

    @property
    def generation(self) -> str:
        """The V2 has no Dry, which is the easiest thing to read from Home Assistant."""
        model = self.model.upper()
        if "V2" in model:
            return "v2"
        modes = ((self.state or {}).get("attributes") or {}).get("hvac_modes") or []
        if modes and "dry" not in modes:
            return "v2"
        return "v3"

    # Reading

    def _attributes(self) -> dict[str, Any]:
        return (self.state or {}).get("attributes") or {}

    def _preset_named(self, *fragments: str) -> str | None:
        for preset in self._attributes().get("preset_modes") or []:
            lower = preset.lower()
            if any(fragment in lower for fragment in fragments):
                return preset
        return None

    @property
    def programs(self) -> list[str]:
        """Programs stored on the BedJet (M1 to M3 and biorhythms), started through presets."""
        skip = {"none", "turbo", "extended heat", "ext_ht", "extended_heat"}
        return [p for p in self._attributes().get("preset_modes") or [] if p.lower() not in skip]

    def air(self) -> Air | None:
        state = self.state
        if not state or state.get("state") in UNAVAILABLE:
            return None
        hvac = state.get("state")
        attributes = state.get("attributes") or {}
        preset = (attributes.get("preset_mode") or "").lower()
        if hvac == "off":
            mode = Mode.STANDBY
        elif hvac == "heat":
            if "turbo" in preset:
                mode = Mode.TURBO
            elif "ext" in preset:
                mode = Mode.EXTENDED_HEAT
            else:
                mode = Mode.HEAT
        elif hvac == "dry":
            mode = Mode.DRY
        else:
            mode = Mode.COOL
        target = attributes.get("temperature")
        fan = str(attributes.get("fan_mode") or "").rstrip("%")
        return Air(
            mode,
            to_half_celsius(float(target), self.ha.fahrenheit) if target is not None else None,
            int(fan) if fan.isdigit() else None,
        )

    def _number(self, entity_id: str | None) -> float | None:
        state = self.ha.states.get(entity_id) if entity_id else None
        if not state or state.get("state") in UNAVAILABLE:
            return None
        try:
            return float(state["state"])
        except (TypeError, ValueError):
            return None

    @property
    def runtime_minutes(self) -> int | None:
        value = self._number(self.runtime_entity)
        return None if value is None else int(value)

    @property
    def runtime_max(self) -> int | None:
        state = self.ha.states.get(self.runtime_entity) if self.runtime_entity else None
        if not state:
            return None
        try:
            return int(float(state.get("attributes", {}).get("max", 0)))
        except (TypeError, ValueError):
            return None

    @property
    def room_celsius(self) -> float | None:
        state = self.ha.states.get(self.ambient_entity) if self.ambient_entity else None
        if not state or state.get("state") in UNAVAILABLE:
            return None
        unit = (state.get("attributes") or {}).get("unit_of_measurement", "")
        return to_celsius(state.get("state"), "F" in unit)

    def _switch(self, entity_id: str | None) -> bool | None:
        state = self.ha.states.get(entity_id) if entity_id else None
        if not state or state.get("state") in UNAVAILABLE:
            return None
        return state.get("state") == "on"

    def status(self) -> dict[str, Any]:
        """What the web UI shows, with temperatures in Celsius."""
        air = self.air()
        attributes = self._attributes()
        fahrenheit = self.ha.fahrenheit
        return {
            "available": self.available,
            "mode": air.mode.value if air else None,
            "temperature": air.temperature / 2 if air and air.temperature is not None else None,
            "fan": air.fan if air else None,
            "current": to_celsius(attributes.get("current_temperature"), fahrenheit),
            "room": self.room_celsius,
            "min_temp": to_celsius(attributes.get("min_temp"), fahrenheit),
            "max_temp": to_celsius(attributes.get("max_temp"), fahrenheit),
            "runtime": self.runtime_minutes,
            "runtime_max": self.runtime_max,
            "preset": attributes.get("preset_mode"),
            "led": self._switch(self.led_entity),
            "muted": self._switch(self.mute_entity),
        }

    # Commands

    async def _service(self, domain: str, service: str, entity_id: str, **data: Any) -> None:
        self.commanded_at = time.time()
        await self.ha.call_service(domain, service, entity_id, **data)
        self.commanded_at = time.time()

    async def _wait_for_mode(self, mode: Mode, timeout: float = 8) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            air = self.air()
            if air and air.mode == mode:
                return
            await asyncio.sleep(0.25)
        _LOGGER.warning("%s did not report %s in time", self.id, mode.title)

    async def set_mode(self, mode: Mode) -> None:
        async with self.lock:
            await self._set_mode(mode)

    async def _set_mode(self, mode: Mode) -> None:
        if mode in (Mode.STANDBY, Mode.WAIT):
            await self._service("climate", "set_hvac_mode", self.id, hvac_mode="off")
        elif mode == Mode.TURBO:
            await self._service("climate", "set_preset_mode", self.id, preset_mode=self._preset_named("turbo") or "Turbo")
        elif mode == Mode.EXTENDED_HEAT:
            preset = self._preset_named("extended", "ext_ht")
            if preset is None:
                raise HomeAssistantError("This BedJet has no Gentle heat.")
            await self._service("climate", "set_preset_mode", self.id, preset_mode=preset)
        else:
            await self._service("climate", "set_hvac_mode", self.id, hvac_mode=mode.value)
        await self._wait_for_mode(Mode.STANDBY if mode == Mode.WAIT else mode)

    async def set_temperature(self, half_celsius: int) -> None:
        async with self.lock:
            await self._set_temperature(half_celsius)

    async def _set_temperature(self, half_celsius: int) -> None:
        attributes = self._attributes()
        value = from_half_celsius(half_celsius, self.ha.fahrenheit)
        # Home Assistant rejects targets outside what the BedJet reports for the mode it is in.
        low, high = attributes.get("min_temp"), attributes.get("max_temp")
        if low is not None and high is not None and float(low) <= float(high):
            value = min(max(value, float(low)), float(high))
        await self._service("climate", "set_temperature", self.id, temperature=value)

    async def set_fan(self, percent: int) -> None:
        async with self.lock:
            await self._set_fan(percent)

    async def _set_fan(self, percent: int) -> None:
        percent = min(max(int(round(percent / 5) * 5), 5), 100)
        await self._service("climate", "set_fan_mode", self.id, fan_mode=f"{percent}%")

    async def set_runtime(self, minutes: int) -> None:
        async with self.lock:
            await self._set_runtime(minutes)

    async def _set_runtime(self, minutes: int) -> None:
        if not self.runtime_entity:
            return
        longest = self.runtime_max
        if longest:
            minutes = min(minutes, longest)
        await self._service("number", "set_value", self.runtime_entity, value=max(int(minutes), 1))

    async def apply(self, mode: Mode, temperature: int | None, fan: int | None, runtime: int | None) -> None:
        """Puts the BedJet into a setting: mode first, since a new mode starts from its own defaults."""
        async with self.lock:
            if mode in (Mode.STANDBY, Mode.WAIT):
                air = self.air()
                if air is None or air.mode != Mode.STANDBY:
                    await self._set_mode(mode)
                return
            air = self.air()
            if air is None or air.mode != mode:
                await self._set_mode(mode)
                # The BedJet settles the new mode's limits before it takes a target.
                await asyncio.sleep(0.5)
            adjusts = mode.uses_temperature and (self.generation == "v3" or mode == Mode.HEAT)
            if temperature is not None and adjusts:
                await self._set_temperature(temperature)
            if fan is not None and mode.uses_fan:
                await self._set_fan(fan)
            if runtime is not None:
                try:
                    await self._set_runtime(runtime)
                except HomeAssistantError as err:
                    # The timer is only a safety net; the phase itself is already playing.
                    _LOGGER.warning("%s: couldn't set the shut-off timer to %s min: %s", self.id, runtime, err)

    async def turn_off(self) -> None:
        await self.set_mode(Mode.STANDBY)

    async def start_program(self, preset: str) -> None:
        if preset not in self.programs:
            raise HomeAssistantError(f"{preset} isn't a program on this BedJet.")
        async with self.lock:
            await self._service("climate", "set_preset_mode", self.id, preset_mode=preset)

    async def set_switch(self, which: str, on: bool) -> None:
        entity_id = self.led_entity if which == "led" else self.mute_entity
        if not entity_id:
            raise HomeAssistantError("This BedJet has no such setting in Home Assistant.")
        async with self.lock:
            await self._service("switch", "turn_on" if on else "turn_off", entity_id)


def find_bedjets(ha: HomeAssistant, wanted: list[str]) -> list[dict[str, Any]]:
    """Climate entities from a BedJet integration, or exactly the ones named in BEDJET_ENTITIES.

    An entity of an integration that is lent to a phone is disabled by its config entry for a
    while, and is still listed so its schedules and history stay in view.
    """
    entries = [entry for entry in ha.entities if str(entry.get("entity_id", "")).startswith("climate.")]
    if wanted:
        by_id = {entry["entity_id"]: entry for entry in entries}
        return [by_id.get(entity_id, {"entity_id": entity_id}) for entity_id in wanted]
    return [
        entry for entry in entries
        if entry.get("platform") == "bedjet" and entry.get("disabled_by") in (None, "config_entry")
    ]
