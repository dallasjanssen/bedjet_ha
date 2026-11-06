"""The simulated BedJet's runtime remaining, shaped like ha-bedjet's."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RUNTIME_MAX
from .entity import FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([FakeRuntime(entry.runtime_data, "runtime_remaining", "Runtime remaining")])


class FakeRuntime(FakeEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_step = 1

    @property
    def native_max_value(self) -> float:
        return RUNTIME_MAX[self.bedjet.mode]

    @property
    def native_value(self) -> float:
        return self.bedjet.runtime

    async def async_set_native_value(self, value: float) -> None:
        self._check("runtime", value)
        self.bedjet.runtime = int(value)
        self.bedjet.changed()
