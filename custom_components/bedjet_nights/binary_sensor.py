"""Night running, for each BedJet."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightsConfigEntry
from .entity import NightsEntity, setup_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: NightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        setup_platform(hass, entry.runtime_data, async_add_entities, lambda n, u: [NightRunningSensor(n, u, "night_running")])
    )


class NightRunningSensor(NightsEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool:
        return self.unit_id in self._nights.runs
