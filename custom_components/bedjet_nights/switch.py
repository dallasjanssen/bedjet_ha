"""Start at bedtime, for each BedJet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightsConfigEntry
from .entity import NightsEntity, setup_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: NightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        setup_platform(hass, entry.runtime_data, async_add_entities, lambda n, u: [BedtimeSwitch(n, u, "start_at_bedtime")])
    )


class BedtimeSwitch(NightsEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return bool((self._nights.unit_settings(self.unit_id).get("auto") or {}).get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._nights.set_auto(self.unit_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._nights.set_auto(self.unit_id, False)
