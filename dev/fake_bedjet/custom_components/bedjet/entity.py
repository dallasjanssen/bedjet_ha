"""Shared base for the simulated BedJet's entities."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity

from . import ADDRESS, FakeBedJet


class FakeEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, bedjet: FakeBedJet, key: str | None, name: str | None) -> None:
        self.bedjet = bedjet
        self._attr_unique_id = ADDRESS if key is None else f"{ADDRESS}_{key}"
        self._attr_name = name
        self._attr_device_info = bedjet.device_info()

    async def async_added_to_hass(self) -> None:
        self.bedjet.listeners.append(self.async_write_ha_state)
        self.async_on_remove(lambda: self.bedjet.listeners.remove(self.async_write_ha_state))

    def _check(self, call: str, value: object) -> None:
        self.bedjet.calls.append((call, value))
        if self.bedjet.fail:
            raise HomeAssistantError("Simulated BedJet is not responding")
