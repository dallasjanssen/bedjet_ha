"""The night in progress, for each BedJet."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightsConfigEntry
from .entity import NightsEntity, setup_platform
from .model import describe_run


async def async_setup_entry(
    hass: HomeAssistant, entry: NightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        setup_platform(
            hass,
            entry.runtime_data,
            async_add_entities,
            lambda n, u: [NightSensor(n, u, "night"), NightEndsSensor(n, u, "night_ends")],
        )
    )


class NightSensor(NightsEntity, SensorEntity):
    """For example "Cool Drift: Cooling, 45 min left (phase 1 of 4)"."""

    @property
    def native_value(self) -> str:
        return describe_run(self._nights.runs.get(self.unit_id))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = self._nights.runs.get(self.unit_id)
        if run is None:
            return {}
        index = run.index_at(time.time())
        return {
            "schedule": run.schedule.name,
            "phase": None if index is None else index + 1,
            "phases": len(run.schedule.phases),
            "mode": None if index is None else run.schedule.phases[index].mode.value,
        }


class NightEndsSensor(NightsEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        run = self._nights.runs.get(self.unit_id)
        return datetime.fromtimestamp(run.ends_at, timezone.utc) if run else None
