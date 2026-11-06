"""Start night and End night, for each BedJet."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightsConfigEntry
from .entity import NightsEntity, setup_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: NightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        setup_platform(
            hass,
            entry.runtime_data,
            async_add_entities,
            lambda n, u: [StartNightButton(n, u, "start_night"), EndNightButton(n, u, "end_night")],
        )
    )


class StartNightButton(NightsEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self._nights.start_night(self.unit_id, source="home_assistant")


class EndNightButton(NightsEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self._nights.end_night(self.unit_id)
