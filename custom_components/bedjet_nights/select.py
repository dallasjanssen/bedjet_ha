"""Tonight's schedule, for each BedJet."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightsConfigEntry
from .entity import NightsEntity, setup_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: NightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        setup_platform(hass, entry.runtime_data, async_add_entities, lambda n, u: [TonightSelect(n, u, "tonight")])
    )


class TonightSelect(NightsEntity, SelectEntity):
    @property
    def options(self) -> list[str]:
        names: list[str] = []
        for schedule in self._nights.store.schedules():
            if schedule.name not in names:
                names.append(schedule.name)
        return names

    @property
    def current_option(self) -> str | None:
        schedule = self._nights.tonight(self.unit_id) if self.unit else None
        return schedule.name if schedule else None

    async def async_select_option(self, option: str) -> None:
        schedule = next((s for s in self._nights.store.schedules() if s.name == option), None)
        if schedule is None:
            raise HomeAssistantError(f"There's no schedule called {option}.")
        self._nights.set_tonight(self.unit_id, schedule.id)
