"""Fixtures: Home Assistant with a simulated BedJet (dev/fake_bedjet) and BedJet Nights set up."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.bedjet_nights import nights as nights_module
from custom_components.bedjet_nights.nights import Nights

CLIMATE = "climate.dallass_bedjet"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def quick_history(monkeypatch):
    monkeypatch.setattr(nights_module, "RECORD_SETTLE", 0.05)


@pytest.fixture
async def bedjet_entry(hass: HomeAssistant) -> MockConfigEntry:
    """The simulated BedJet, shown in Fahrenheit like the real one in this house."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await hass.config.async_set_time_zone("America/Chicago")
    entry = MockConfigEntry(domain="bedjet", title="Dallas’s Bedjet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE) is not None, sorted(hass.states.async_entity_ids())
    return entry


@pytest.fixture
def fake(bedjet_entry: MockConfigEntry):
    return bedjet_entry.runtime_data


@pytest.fixture
async def nights_entry(hass: HomeAssistant, bedjet_entry: MockConfigEntry) -> MockConfigEntry:
    entry = MockConfigEntry(domain="bedjet_nights", title="BedJet Nights")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def nights(nights_entry: MockConfigEntry) -> Nights:
    return nights_entry.runtime_data
