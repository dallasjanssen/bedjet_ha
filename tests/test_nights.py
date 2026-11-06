"""Plays schedules through Home Assistant against a simulated BedJet shaped like ha-bedjet's."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.bedjet_nights.model import Mode
from custom_components.bedjet_nights.nights import ActionError, Nights

from conftest import CLIMATE

UNIT = CLIMATE


def schedule_named(nights: Nights, name: str):
    return next(s for s in nights.store.schedules() if s.name == name)


def calls(fake) -> list[str]:
    return [name for name, _ in fake.calls]


async def settle(hass: HomeAssistant, seconds: float = 0.2) -> None:
    await asyncio.sleep(seconds)
    await hass.async_block_till_done()


async def test_finds_the_bedjet_and_its_siblings(hass: HomeAssistant, nights: Nights):
    unit = nights.units[UNIT]
    assert unit.name == "Dallas’s Bedjet"
    assert unit.runtime_entity == "number.dallass_bedjet_runtime_remaining"
    assert unit.ambient_entity == "sensor.dallass_bedjet_ambient_temperature"
    assert unit.led_entity == "switch.dallass_bedjet_enable_led"
    assert unit.generation == "v3"
    assert unit.programs == ["M1: Default", "M2: Cool Night", "M3: Default", "Cool Night"]
    status = unit.status()
    assert status["mode"] == "standby"
    assert status["room"] == pytest.approx(20.0)


async def test_starting_a_night_sets_mode_then_temperature_fan_and_timer(hass: HomeAssistant, nights: Nights, fake):
    await nights.start_night(UNIT, schedule_named(nights, "Warm Welcome").id)

    # Turbo first, for ten minutes, with the timer clamped to Turbo's own ten minute limit.
    assert fake.calls == [("preset_mode", "Turbo"), ("fan_mode", "100%"), ("runtime", 10)]
    assert fake.mode == "turbo"
    assert nights.applied[UNIT] == 0

    # Twelve minutes later: Heat at 34 C (93 F), fan 40%, timer at the phase's end plus two minutes.
    fake.calls.clear()
    nights.runs[UNIT].started_at -= 12 * 60
    await nights.async_tick()
    assert calls(fake) == ["hvac_mode", "temperature", "fan_mode", "runtime"]
    assert (fake.mode, fake.target, fake.fan, fake.runtime) == ("heat", 34.0, 40, 30)

    # Past the end the night finishes and the BedJet is turned off.
    nights.runs[UNIT].started_at -= 10 * 3600
    await nights.async_tick()
    assert UNIT not in nights.runs
    assert fake.mode == "off"
    history = nights.store.history(UNIT, 0)
    assert history[0]["kind"] == "schedule_started"
    assert history[-1]["kind"] == "schedule_ended"
    assert history[-1]["data"]["ending"] == "finished"


async def test_a_pause_phase_turns_the_air_off_and_back_on(hass: HomeAssistant, nights: Nights, fake):
    schedule = nights.save_schedule({"name": "Pausey", "phases": [
        {"mode": "cool", "fan": 30, "minutes": 30},
        {"mode": "wait", "minutes": 30},
        {"mode": "cool", "fan": 50, "minutes": 30},
    ]})
    await nights.start_night(UNIT, schedule.id)
    assert fake.mode == "cool"
    nights.runs[UNIT].started_at -= 31 * 60
    await nights.async_tick()
    assert fake.mode == "off"
    assert UNIT in nights.runs, "a pause must not end the night"
    nights.runs[UNIT].started_at -= 30 * 60
    await nights.async_tick()
    assert (fake.mode, fake.fan) == ("cool", 50)


async def test_turning_the_bedjet_off_elsewhere_ends_the_night(hass: HomeAssistant, nights: Nights):
    await nights.start_night(UNIT, schedule_named(nights, "Hot Sleeper").id)
    await settle(hass)
    nights.units[UNIT].commanded_at = 0  # long after BedJet Nights last touched it
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": UNIT, "hvac_mode": "off"}, blocking=True)
    await settle(hass)
    assert UNIT not in nights.runs
    ended = [e for e in nights.store.history(UNIT, 0) if e["kind"] == "schedule_ended"]
    assert ended[-1]["data"]["ending"] == "turned_off"


async def test_the_bedjets_own_timer_running_out_does_not_end_the_night(hass: HomeAssistant, nights: Nights, fake):
    await nights.start_night(UNIT, schedule_named(nights, "Hot Sleeper").id)
    await settle(hass)
    nights.units[UNIT].commanded_at = 0
    fake.runtime = 1
    fake.changed()
    await settle(hass)
    fake.set_mode("off")
    await settle(hass)
    assert UNIT in nights.runs


async def test_remote(hass: HomeAssistant, nights: Nights, fake):
    await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    await nights.remote(UNIT, {"mode": "heat"})
    assert UNIT not in nights.runs, "choosing a mode ends the schedule, as in Lull"
    assert fake.mode == "heat"
    await nights.remote(UNIT, {"temperature": 70})
    assert fake.target == 35.0
    await nights.remote(UNIT, {"fan": 55})
    assert fake.fan == 55
    await nights.remote(UNIT, {"runtime": 999})
    assert fake.runtime == 240  # clamped to what Heat allows
    await nights.remote(UNIT, {"led": True})
    assert fake.led is True
    await nights.remote(UNIT, {"program": "Cool Night"})
    assert fake.calls[-1] == ("preset_mode", "Cool Night")
    await nights.remote(UNIT, {"mode": "standby"})
    assert fake.mode == "off"
    with pytest.raises(ActionError):
        await nights.remote(UNIT, {"program": "Not a program"})


async def test_targets_stay_inside_what_the_mode_allows(hass: HomeAssistant, nights: Nights, fake):
    await nights.units[UNIT].apply(Mode.COOL, 80, 40, None)  # 40 C is past Cool's 31 C limit
    sent = next(value for name, value in fake.calls if name == "temperature")
    assert 30.5 <= sent <= 31.0


async def test_history_records_changes_made_anywhere(hass: HomeAssistant, nights: Nights, fake):
    fake.set_mode("cool")
    await settle(hass)
    fake.fan = 80
    fake.changed()
    await settle(hass)
    entries = [e for e in nights.store.history(UNIT, 0) if e["kind"] == "air"]
    assert entries[-1]["data"] == {"mode": "cool", "temperature": 44, "fan": 80}
    assert entries[-1]["room"] == pytest.approx(20.0)


async def test_schedule_driven_changes_are_not_repeated_in_history(hass: HomeAssistant, nights: Nights):
    await nights.start_night(UNIT, schedule_named(nights, "Hot Sleeper").id)
    await settle(hass)
    assert [e["kind"] for e in nights.store.history(UNIT, 0)] == ["schedule_started"]


async def test_a_phase_too_long_for_the_bedjet_blocks_starting(hass: HomeAssistant, nights: Nights):
    schedule = nights.save_schedule({"name": "Too hot", "phases": [{"mode": "heat", "temperature": 80, "fan": 100, "minutes": 120}]})
    with pytest.raises(ActionError, match="won't run a phase this long"):
        await nights.start_night(UNIT, schedule.id)


async def test_failed_start_is_reported_and_undone(hass: HomeAssistant, nights: Nights, fake):
    fake.fail = True
    with pytest.raises(ActionError, match="Couldn't start phase 1"):
        await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    assert UNIT not in nights.runs


async def test_lending_pauses_the_integration_and_takes_it_back(hass: HomeAssistant, nights: Nights, bedjet_entry):
    await nights.lend(UNIT, 30)
    await hass.async_block_till_done()
    assert bedjet_entry.state is ConfigEntryState.NOT_LOADED
    assert bedjet_entry.disabled_by is not None
    await settle(hass, 1.5)  # the registry refresh after the entities are disabled
    assert UNIT in nights.units, "a lent BedJet stays listed"
    assert not nights.units[UNIT].available
    with pytest.raises(ActionError, match="lent to a phone"):
        await nights.start_night(UNIT)

    nights.update_unit_settings(UNIT, lend_until=time.time() - 1)
    await nights.async_tick()
    await hass.async_block_till_done()
    assert bedjet_entry.disabled_by is None
    assert bedjet_entry.state is ConfigEntryState.LOADED
    assert "lend_until" not in nights.unit_settings(UNIT)


async def test_lending_is_refused_while_a_night_runs(hass: HomeAssistant, nights: Nights):
    await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    with pytest.raises(ActionError, match="End tonight's schedule first"):
        await nights.lend(UNIT, 30)


async def test_bedtime_start(hass: HomeAssistant, nights: Nights):
    schedule = schedule_named(nights, "Hot Sleeper")
    local = dt_util.now() - timedelta(minutes=1)
    schedule.bedtime = local.hour * 60 + local.minute
    nights.store.save_schedule(schedule)
    nights.set_tonight(UNIT, schedule.id)
    nights.set_auto(UNIT, True, list(range(7)))
    await nights.async_tick()
    assert nights.runs[UNIT].schedule.id == schedule.id
    started = [e for e in nights.store.history(UNIT, 0) if e["kind"] == "schedule_started"]
    assert started[-1]["data"]["source"] == "bedtime"
    # The same evening never starts twice, even after ending it.
    await nights.end_night(UNIT)
    await nights.async_tick()
    assert UNIT not in nights.runs


async def test_bedtime_skips_nights_not_picked(hass: HomeAssistant, nights: Nights):
    schedule = schedule_named(nights, "Hot Sleeper")
    local = dt_util.now()
    schedule.bedtime = local.hour * 60 + local.minute
    nights.store.save_schedule(schedule)
    nights.set_tonight(UNIT, schedule.id)
    nights.set_auto(UNIT, True, [(local.weekday() + 1) % 7])
    await nights.async_tick()
    assert UNIT not in nights.runs


async def test_a_night_survives_a_reload(hass: HomeAssistant, nights: Nights, nights_entry):
    await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    assert await hass.config_entries.async_reload(nights_entry.entry_id)
    await hass.async_block_till_done()
    reloaded: Nights = nights_entry.runtime_data
    assert reloaded is not nights
    assert reloaded.runs[UNIT].schedule.name == "Cool Drift"
    assert reloaded.applied[UNIT] == 0
    assert len(reloaded.store.history(UNIT, 0)) == 1
