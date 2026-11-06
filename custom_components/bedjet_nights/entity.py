"""Entities that put a BedJet's night on its own device page in Home Assistant."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .bedjet import BedJetUnit
from .const import SIGNAL_UNITS, SIGNAL_UPDATE
from .nights import Nights


class NightsEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, nights: Nights, unit: BedJetUnit, key: str) -> None:
        self._nights = nights
        self._registry_id = unit.registry_id
        self._attr_unique_id = f"{unit.registry_id}_{key}"
        self._attr_translation_key = key
        # Shown on the BedJet's own device page. A device belongs to one integration, so the
        # entities link to it as helpers do rather than claiming it with device info.
        self.device_entry = unit.device

    @property
    def unit(self) -> BedJetUnit | None:
        for unit in self._nights.units.values():
            if unit.registry_id == self._registry_id:
                return unit
        return None

    @property
    def unit_id(self) -> str:
        unit = self.unit
        return unit.id if unit else ""

    @property
    def available(self) -> bool:
        return self.unit is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


def setup_platform(
    hass: HomeAssistant,
    nights: Nights,
    async_add_entities: AddConfigEntryEntitiesCallback,
    make: Callable[[Nights, BedJetUnit], Iterable[Entity]],
) -> Callable[[], None]:
    """Adds entities for every BedJet now, and for any that turns up later."""
    added: set[str] = set()

    @callback
    def add() -> None:
        new = [unit for unit in nights.units.values() if unit.registry_id not in added]
        for unit in new:
            added.add(unit.registry_id)
        entities = [entity for unit in new for entity in make(nights, unit)]
        if entities:
            async_add_entities(entities)

    add()
    return async_dispatcher_connect(hass, SIGNAL_UNITS, add)
