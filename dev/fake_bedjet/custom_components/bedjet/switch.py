"""The simulated BedJet's LED and beeps switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    bedjet = entry.runtime_data
    async_add_entities([FakeSwitch(bedjet, "enable_led", "Enable LED"), FakeSwitch(bedjet, "mute_beeps", "Mute beeps")])


class FakeSwitch(FakeEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.bedjet.led if self._attr_unique_id.endswith("enable_led") else self.bedjet.muted

    async def _set(self, on: bool) -> None:
        self._check("switch", on)
        if self._attr_unique_id.endswith("enable_led"):
            self.bedjet.led = on
        else:
            self.bedjet.muted = on
        self.bedjet.changed()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
