"""A simulated BedJet 3 for tests and for trying BedJet Nights without hardware.

It is not shipped with BedJet Nights. It stands in for the ha-bedjet integration: the same entity
kinds, unique IDs, Celsius device units, per-mode limits, presets and runtime timer.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "bedjet"
NAME = "Dallas’s Bedjet"
ADDRESS = "AA:BB:CC:00:00:01"
PLATFORMS = [Platform.CLIMATE, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

# Limits in Celsius by mode, as a BedJet 3 reports them.
LIMITS = {
    "off": (10.0, 40.0), "cool": (19.0, 31.0), "heat": (22.5, 40.0), "dry": (20.0, 40.0),
    "turbo": (43.0, 43.0), "extended_heat": (19.0, 33.5),
}
DEFAULT_TARGET = {"off": 22.0, "cool": 22.0, "heat": 33.0, "dry": 30.0, "turbo": 43.0, "extended_heat": 29.0}
RUNTIME_MAX = {"off": 0, "cool": 719, "heat": 240, "dry": 719, "turbo": 10, "extended_heat": 600}
DEFAULT_RUNTIME = {"off": 0, "cool": 600, "heat": 60, "dry": 60, "turbo": 10, "extended_heat": 600}


class FakeBedJet:
    def __init__(self) -> None:
        self.mode = "off"
        self.target = 22.0
        self.fan = 60
        self.runtime = 0
        self.led = False
        self.muted = True
        self.room = 20.0
        self.listeners: list[Callable[[], None]] = []
        self.calls: list[tuple[str, object]] = []
        self.fail = False

    def changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.target = DEFAULT_TARGET[mode]
        self.runtime = DEFAULT_RUNTIME[mode]
        self.changed()

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={("bluetooth", ADDRESS)}, name=NAME, manufacturer="BedJet", model="BedJet 3", sw_version="V1.20 (140)"
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data = FakeBedJet()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
