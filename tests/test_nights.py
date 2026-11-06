"""Plays schedules against a pretend Home Assistant that behaves like the ha-bedjet integration."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bedjet_nights import nights as nights_module
from bedjet_nights.ha import HomeAssistant
from bedjet_nights.nights import ActionError, Nights

from conftest import Harness
from fake_ha import CLIMATE, ENTRY_ID, RUNTIME

UNIT = CLIMATE


def schedule_named(nights: Nights, name: str):
    return next(s for s in nights.store.schedules() if s.name == name)


async def test_finds_the_bedjet_and_its_siblings(harness: Harness):
    unit = harness.nights.units[UNIT]
    assert unit.name == "Dallas’s Bedjet"
    assert unit.runtime_entity == RUNTIME
    assert unit.ambient_entity == "sensor.dallass_bedjet_ambient_temperature"
    assert unit.generation == "v3"
    assert unit.programs == ["M1: Default", "M2: Cool Night", "M3: Default", "Cool Night"]
    status = unit.status()
    assert status["mode"] == "standby"
    assert status["room"] == pytest.approx(20.0)


async def test_starting_a_night_sets_mode_then_temperature_fan_and_timer(harness: Harness):
    nights = harness.nights
    schedule = schedule_named(nights, "Warm Welcome")
    await nights.start_night(UNIT, schedule.id)

    # Turbo first, for ten minutes, with the timer running two minutes past the phase.
    assert harness.services() == ["climate.set_preset_mode", "climate.set_fan_mode", "number.set_value"]
    assert harness.fake.calls[0]["preset_mode"] == "Turbo"
    assert harness.fake.calls[-1]["value"] == 10  # clamped to Turbo's own 10 minute limit
    assert harness.fake.bedjet.mode == "turbo"
    assert nights.applied[UNIT] == 0

    # Twelve minutes later the scheduler moves on to Heat at 34 C (93 F), fan 40%, 30 minutes.
    harness.fake.calls.clear()
    nights.runs[UNIT].started_at -= 12 * 60
    await nights._tick()
    assert harness.services() == ["climate.set_hvac_mode", "climate.set_temperature", "climate.set_fan_mode", "number.set_value"]
    bj = harness.fake.bedjet
    assert (bj.mode, bj.temperature, bj.fan) == ("heat", 68, 40)
    assert bj.runtime == 30  # 28 minutes left in the phase plus two

    # Past the end the night finishes and the BedJet is turned off.
    nights.runs[UNIT].started_at -= 10 * 3600
    await nights._tick()
    assert UNIT not in nights.runs
    assert harness.fake.bedjet.mode == "off"
    kinds = [e["kind"] for e in nights.store.history(UNIT, 0)]
    assert kinds[0] == "schedule_started"
    assert kinds[-1] == "schedule_ended"
    assert nights.store.history(UNIT, 0)[-1]["data"]["ending"] == "finished"


async def test_a_pause_phase_turns_the_air_off_and_back_on(harness: Harness):
    nights = harness.nights
    schedule = nights.save_schedule({"name": "Pausey", "phases": [
        {"mode": "cool", "fan": 30, "minutes": 30},
        {"mode": "wait", "minutes": 30},
        {"mode": "cool", "fan": 50, "minutes": 30},
    ]})
    await nights.start_night(UNIT, schedule.id)
    assert harness.fake.bedjet.mode == "cool"
    nights.runs[UNIT].started_at -= 31 * 60
    await nights._tick()
    assert harness.fake.bedjet.mode == "off"
    assert UNIT in nights.runs, "a pause must not end the night"
    nights.runs[UNIT].started_at -= 30 * 60
    await nights._tick()
    assert (harness.fake.bedjet.mode, harness.fake.bedjet.fan) == ("cool", 50)


async def test_turning_the_bedjet_off_elsewhere_ends_the_night(harness: Harness):
    nights = harness.nights
    await nights.start_night(UNIT, schedule_named(nights, "Hot Sleeper").id)
    await harness.settle()
    nights.units[UNIT].commanded_at = 0  # long after this app last touched it

    def press_off(bj):
        bj.runtime = 50
    await harness.fake.change(press_off)
    await harness.settle()
    await harness.fake.change(lambda bj: bj.set_mode("off"))
    await harness.settle(0.3)
    assert UNIT not in nights.runs
    ended = [e for e in nights.store.history(UNIT, 0) if e["kind"] == "schedule_ended"]
    assert ended[-1]["data"]["ending"] == "turned_off"


async def test_the_bedjets_own_timer_running_out_does_not_end_the_night(harness: Harness):
    nights = harness.nights
    await nights.start_night(UNIT, schedule_named(nights, "Hot Sleeper").id)
    await harness.settle()
    nights.units[UNIT].commanded_at = 0

    def last_minute(bj):
        bj.runtime = 1
    await harness.fake.change(last_minute)
    await harness.settle()
    await harness.fake.change(lambda bj: bj.set_mode("off"))
    await harness.settle(0.3)
    assert UNIT in nights.runs


async def test_remote_mode_change_ends_the_running_schedule(harness: Harness):
    nights = harness.nights
    await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    await nights.remote(UNIT, {"mode": "heat"})
    assert UNIT not in nights.runs
    assert harness.fake.bedjet.mode == "heat"
    await nights.remote(UNIT, {"temperature": 70})
    assert harness.fake.bedjet.temperature == 70
    await nights.remote(UNIT, {"fan": 55})
    assert harness.fake.bedjet.fan == 55
    await nights.remote(UNIT, {"runtime": 999})
    assert harness.fake.bedjet.runtime == 240  # clamped to what Heat allows
    await nights.remote(UNIT, {"mode": "standby"})
    assert harness.fake.bedjet.mode == "off"


async def test_history_records_changes_made_anywhere(harness: Harness):
    nights = harness.nights
    await harness.fake.change(lambda bj: bj.set_mode("cool"))
    await harness.settle()

    def faster(bj):
        bj.fan = 80
    await harness.fake.change(faster)
    await harness.settle()
    entries = [e for e in nights.store.history(UNIT, 0) if e["kind"] == "air"]
    assert entries[-1]["data"] == {"mode": "cool", "temperature": 44, "fan": 80}
    assert entries[-1]["room"] == pytest.approx(20.0)


async def test_schedule_driven_changes_are_not_repeated_in_history(harness: Harness):
    nights = harness.nights
    await nights.start_night(UNIT, schedule_named(nights, "Hot Sleeper").id)
    await harness.settle()
    kinds = [e["kind"] for e in nights.store.history(UNIT, 0)]
    assert kinds == ["schedule_started"]


async def test_heat_targets_are_clamped_to_what_the_mode_allows(harness: Harness):
    unit = harness.nights.units[UNIT]
    await unit.apply(nights_module.Mode.COOL, 80, 40, None)  # 40 C is past Cool's limit
    call = next(c for c in harness.fake.calls if c["service"] == "climate.set_temperature")
    assert call["temperature"] == 88  # Cool tops out at 31 C, shown as 88 F


async def test_a_bedjet_v2_schedule_problem_blocks_starting(harness: Harness):
    nights = harness.nights
    schedule = nights.save_schedule({"name": "Too hot", "phases": [{"mode": "heat", "temperature": 80, "fan": 100, "minutes": 120}]})
    with pytest.raises(ActionError, match="won't run a phase this long"):
        await nights.start_night(UNIT, schedule.id)


async def test_failed_start_is_reported_and_undone(harness: Harness):
    harness.fake.fail_services = True
    with pytest.raises(ActionError, match="Couldn't start phase 1"):
        await harness.nights.start_night(UNIT, schedule_named(harness.nights, "Cool Drift").id)
    assert UNIT not in harness.nights.runs


async def test_lending_pauses_the_integration_and_takes_it_back(harness: Harness):
    nights = harness.nights
    await nights.lend(UNIT, 30)
    assert ENTRY_ID in harness.fake.disabled_entries
    await harness.settle(2.5)  # the registry refresh after Home Assistant reports the change
    assert UNIT in nights.units, "a lent BedJet stays listed"
    assert nights.unit_settings(UNIT)["lend_until"] > time.time()
    with pytest.raises(ActionError, match="lent to a phone"):
        await nights.start_night(UNIT)
    nights.update_unit_settings(UNIT, lend_until=time.time() - 1)
    await nights._tick()
    assert ENTRY_ID not in harness.fake.disabled_entries
    assert "lend_until" not in nights.unit_settings(UNIT)


async def test_lending_is_refused_while_a_night_runs(harness: Harness):
    nights = harness.nights
    await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    with pytest.raises(ActionError, match="End tonight's schedule first"):
        await nights.lend(UNIT, 30)


async def test_bedtime_start(harness: Harness):
    nights = harness.nights
    schedule = schedule_named(nights, "Hot Sleeper")
    local = datetime.now(ZoneInfo("America/Chicago")) - timedelta(minutes=1)
    schedule.bedtime = local.hour * 60 + local.minute
    nights.store.save_schedule(schedule)
    nights.set_tonight(UNIT, schedule.id)
    nights.set_auto(UNIT, True, list(range(7)))
    await nights._tick()
    assert nights.runs[UNIT].schedule.id == schedule.id
    started = [e for e in nights.store.history(UNIT, 0) if e["kind"] == "schedule_started"]
    assert started[-1]["data"]["source"] == "bedtime"
    # The same evening never starts twice, even after ending it.
    await nights.end_night(UNIT)
    await nights._tick()
    assert UNIT not in nights.runs


async def test_bedtime_skips_nights_not_picked(harness: Harness):
    nights = harness.nights
    schedule = schedule_named(nights, "Hot Sleeper")
    local = datetime.now(ZoneInfo("America/Chicago"))
    schedule.bedtime = local.hour * 60 + local.minute
    nights.store.save_schedule(schedule)
    nights.set_tonight(UNIT, schedule.id)
    nights.set_auto(UNIT, True, [(local.weekday() + 1) % 7])
    await nights._tick()
    assert UNIT not in nights.runs


async def test_a_run_survives_a_restart(harness: Harness, tmp_path):
    nights = harness.nights
    await nights.start_night(UNIT, schedule_named(nights, "Cool Drift").id)
    reloaded = Nights(nights.config, nights.store, HomeAssistant("http://unused", "x", harness.client))
    assert reloaded.runs[UNIT].schedule.name == "Cool Drift"
    assert reloaded.applied[UNIT] == 0


async def test_web_api(harness: Harness):
    async with harness.client.get(harness.base + "/api/state") as response:
        state = await response.json()
    assert state["connected"] is True
    assert state["fahrenheit"] is True
    assert state["units"][0]["id"] == UNIT
    assert [s["name"] for s in state["schedules"]][:4] == ["Cool Drift", "Warm Welcome", "Hot Sleeper", "Winter Cocoon"]

    body = {"name": "Mine", "bedtime": 1350, "phases": [{"mode": "cool", "fan": 35, "minutes": 120}]}
    async with harness.client.post(harness.base + "/api/schedules", json=body) as response:
        saved = (await response.json())["schedule"]
    async with harness.client.post(harness.base + f"/api/units/{UNIT}/tonight", json={"schedule_id": saved["id"]}) as response:
        assert (await response.json())["units"][0]["tonight"] == saved["id"]
    async with harness.client.post(harness.base + f"/api/units/{UNIT}/start", json={}) as response:
        state = await response.json()
    assert state["units"][0]["run"]["schedule"]["name"] == "Mine"
    async with harness.client.post(harness.base + f"/api/units/{UNIT}/start", json={}) as response:
        assert response.status == 400
        assert "already running" in (await response.json())["error"]
    async with harness.client.post(harness.base + f"/api/units/{UNIT}/end", json={}) as response:
        assert (await response.json())["units"][0]["run"] is None
    async with harness.client.get(harness.base + f"/api/units/{UNIT}/history") as response:
        nights = (await response.json())["nights"]
    assert nights[0]["entries"][0]["kind"] == "schedule_started"
    async with harness.client.delete(harness.base + f"/api/schedules/{saved['id']}") as response:
        assert all(s["id"] != saved["id"] for s in (await response.json())["schedules"])
    async with harness.client.get(harness.base + "/") as response:
        assert "BedJet Nights" in await response.text()
