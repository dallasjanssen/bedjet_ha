"""The night's controls on the BedJet's own device page."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bedjet_nights.nights import Nights

from conftest import CLIMATE

SELECT = "select.dallass_bedjet_tonight_s_schedule"
START = "button.dallass_bedjet_start_night"
END = "button.dallass_bedjet_end_night"
BEDTIME = "switch.dallass_bedjet_start_at_bedtime"
RUNNING = "binary_sensor.dallass_bedjet_night_running"
NIGHT = "sensor.dallass_bedjet_night"
ENDS = "sensor.dallass_bedjet_night_ends"


async def test_entities_join_the_bedjets_device(hass: HomeAssistant, nights: Nights):
    registry = er.async_get(hass)
    bedjet_device = registry.async_get(CLIMATE).device_id
    for entity_id in (SELECT, START, END, BEDTIME, RUNNING, NIGHT, ENDS):
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.device_id == bedjet_device, entity_id
        assert entry.platform == "bedjet_nights"


async def test_entity_states_follow_the_night(hass: HomeAssistant, nights: Nights):
    select = hass.states.get(SELECT)
    assert select.state == "Cool Drift"
    assert select.attributes["options"][:4] == ["Cool Drift", "Warm Welcome", "Hot Sleeper", "Winter Cocoon"]
    assert hass.states.get(RUNNING).state == "off"
    assert hass.states.get(NIGHT).state == "Not running"
    assert hass.states.get(ENDS).state == "unknown"
    assert hass.states.get(BEDTIME).state == "off"

    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "Hot Sleeper"}, blocking=True)
    assert nights.tonight(CLIMATE).name == "Hot Sleeper"

    await hass.services.async_call("button", "press", {"entity_id": START}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(RUNNING).state == "on"
    night = hass.states.get(NIGHT)
    assert night.state.startswith("Hot Sleeper: Cooling, ")
    assert night.attributes["phase"] == 1
    assert night.attributes["phases"] == 3
    assert hass.states.get(ENDS).state.endswith("+00:00")
    started = nights.store.history(CLIMATE, 0)[-1]
    assert started["data"]["source"] == "home_assistant"

    await hass.services.async_call("button", "press", {"entity_id": END}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(RUNNING).state == "off"

    await hass.services.async_call("switch", "turn_on", {"entity_id": BEDTIME}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(BEDTIME).state == "on"
    assert nights.unit_settings(CLIMATE)["auto"]["enabled"] is True


async def test_new_schedules_appear_as_options(hass: HomeAssistant, nights: Nights):
    nights.save_schedule({"name": "Mine", "phases": [{"mode": "cool", "minutes": 60}]})
    await hass.async_block_till_done()
    assert "Mine" in hass.states.get(SELECT).attributes["options"]
