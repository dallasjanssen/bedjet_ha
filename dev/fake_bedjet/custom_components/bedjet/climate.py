"""The simulated BedJet's climate entity, shaped like ha-bedjet's."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from .entity import FakeEntity
from . import LIMITS

PRESETS = ["Turbo", "Extended Heat", "M1: Default", "M2: Cool Night", "M3: Default", "Cool Night"]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([FakeClimate(entry.runtime_data, None, None)])


class FakeClimate(FakeEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.DRY]
    _attr_fan_modes = [f"{speed}%" for speed in range(5, 101, 5)]
    _attr_preset_modes = PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
    )

    @property
    def hvac_mode(self) -> HVACMode:
        return {"off": HVACMode.OFF, "cool": HVACMode.COOL, "dry": HVACMode.DRY}.get(self.bedjet.mode, HVACMode.HEAT)

    @property
    def preset_mode(self) -> str | None:
        return {"turbo": "Turbo", "extended_heat": "Extended Heat"}.get(self.bedjet.mode)

    @property
    def current_temperature(self) -> float:
        return 23.5 if self.bedjet.mode != "off" else self.bedjet.room

    @property
    def target_temperature(self) -> float:
        return self.bedjet.target

    @property
    def fan_mode(self) -> str:
        return f"{self.bedjet.fan}%"

    def _displayed(self, celsius: float) -> float:
        # As ha-bedjet does: limits that match the whole degree shown in Fahrenheit.
        if self.hass.config.units.temperature_unit != UnitOfTemperature.FAHRENHEIT:
            return celsius
        fahrenheit = TemperatureConverter.convert(celsius, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT)
        return TemperatureConverter.convert(round(fahrenheit), UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS)

    @property
    def min_temp(self) -> float:
        return self._displayed(LIMITS[self.bedjet.mode][0])

    @property
    def max_temp(self) -> float:
        return self._displayed(LIMITS[self.bedjet.mode][1])

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._check("hvac_mode", hvac_mode)
        self.bedjet.set_mode("cool" if hvac_mode == HVACMode.FAN_ONLY else str(hvac_mode))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._check("preset_mode", preset_mode)
        if preset_mode == "Turbo":
            self.bedjet.set_mode("turbo")
        elif preset_mode == "Extended Heat":
            self.bedjet.set_mode("extended_heat")
        else:
            self.bedjet.set_mode("cool")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self._check("temperature", kwargs[ATTR_TEMPERATURE])
        low, high = LIMITS[self.bedjet.mode]
        self.bedjet.target = min(max(round(kwargs[ATTR_TEMPERATURE] * 2) / 2, low), high)
        self.bedjet.changed()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._check("fan_mode", fan_mode)
        self.bedjet.fan = int(fan_mode.rstrip("%"))
        self.bedjet.changed()
