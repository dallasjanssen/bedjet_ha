"""BedJet Nights: Lull-style schedules, a remote and night history for BedJets in Home Assistant.

Home Assistant's BedJet integration keeps the BedJet's single Bluetooth connection. BedJet Nights
works through that integration's entities: it plays schedules phase by phase, records history,
adds a sidebar panel, and puts the night's controls on the BedJet's device page.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import panel, websocket
from .const import DOMAIN, PLATFORMS
from .nights import Nights
from .storage import NightsStore

type NightsConfigEntry = ConfigEntry[Nights]

WEBSOCKET_REGISTERED = f"{DOMAIN}_websocket_registered"


async def async_setup_entry(hass: HomeAssistant, entry: NightsConfigEntry) -> bool:
    store = NightsStore(hass)
    await store.async_load()
    nights = Nights(hass, entry, store)
    entry.runtime_data = nights
    await nights.async_start()

    if not hass.data.get(WEBSOCKET_REGISTERED):
        websocket.async_register(hass)
        hass.data[WEBSOCKET_REGISTERED] = True
    await panel.async_register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NightsConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
        panel.async_unregister(hass)
    return unloaded
