"""The heart of BedJet Nights: plays schedules through the night and keeps each BedJet's history.

Lull saves a schedule into the BedJet and lets the BedJet run it, because a phone can walk away.
Home Assistant never walks away, so here Home Assistant plays the schedule phase by phase. Each
phase also sets the BedJet's own shut-off timer to the end of the phase (plus a couple of
minutes), so if Home Assistant stopped mid-night the BedJet would still turn itself off on time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .bedjet import Air, BedJetUnit, find_bedjets
from .const import HISTORY_DAYS, SIGNAL_UNITS, SIGNAL_UPDATE
from .model import ActiveRun, Mode, Schedule, describe_run, night_phases, problem, templates
from .storage import NightsStore

_LOGGER = logging.getLogger(__name__)

RUNTIME_MARGIN = 2
"""Minutes a phase's timer runs past the phase, so the next phase takes over before it fires."""
RECORD_SETTLE = 4.0
"""Seconds a change has to hold before history records it; a mode change arrives in several updates."""
AUTO_START_WINDOW = 5
"""Minutes after bedtime a bedtime start still happens, in case Home Assistant was restarting then."""
RETRY_SECONDS = 15


class ActionError(HomeAssistantError):
    """Something the person asked for can't be done, with a reason worth showing them."""


def _air_key(air: Air | None) -> tuple[Any, ...] | None:
    if air is None:
        return None
    if air.mode == Mode.STANDBY:
        return (air.mode,)
    temperature = air.temperature if air.mode.uses_temperature else None
    return (air.mode, temperature, air.fan)


class Nights:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: NightsStore) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.units: dict[str, BedJetUnit] = {}
        self.runs: dict[str, ActiveRun] = {}
        self.applied: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._record_timers: dict[str, CALLBACK_TYPE] = {}
        self._last_air: dict[str, Air | None] = {}
        self._seeded: set[str] = set()
        self._runtime_seen: dict[str, int] = {}
        self._quiet_until: dict[str, float] = {}
        self._pruned_at = 0.0
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_states: CALLBACK_TYPE | None = None
        self._unsub_refresh: CALLBACK_TYPE | None = None

    # Lifecycle

    async def async_start(self) -> None:
        for unit_id, saved in (self.store.get("runs") or {}).items():
            with contextlib.suppress(KeyError, ValueError, TypeError):
                self.runs[unit_id] = ActiveRun.from_dict(saved)
                self.applied[unit_id] = int(saved.get("applied", -1))
        self._refresh_units()
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed))
        self._unsubs.append(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._registry_changed))
        self._unsubs.append(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._started))
        # The minutes left in a phase change without any state change, so refresh once a minute.
        self._unsubs.append(async_track_time_interval(self.hass, self._minute, timedelta(minutes=1)))
        self._task = self.hass.async_create_background_task(self._loop(), "bedjet_nights scheduler")

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._unsub_states:
            self._unsub_states()
        if self._unsub_refresh:
            self._unsub_refresh()
        for cancel in self._record_timers.values():
            cancel()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.store.async_flush()

    @callback
    def _changed(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def _minute(self, _now: datetime) -> None:
        if self.runs:
            self._changed()

    @callback
    def _started(self, _event: Event) -> None:
        self._refresh_units()

    @callback
    def _registry_changed(self, _event: Event) -> None:
        if self._unsub_refresh:
            self._unsub_refresh()

        @callback
        def refresh(_now: datetime) -> None:
            self._unsub_refresh = None
            self._refresh_units()

        self._unsub_refresh = async_call_later(self.hass, 1, refresh)

    @callback
    def _refresh_units(self) -> None:
        units: dict[str, BedJetUnit] = {}
        for entry in find_bedjets(self.hass):
            unit = next((u for u in self.units.values() if u.registry_id == entry.id), None) or BedJetUnit(self.hass, entry)
            unit.refresh_links()
            units[unit.id] = unit
        added = set(units) - set(self.units)
        self.units = units
        if added:
            _LOGGER.info("BedJets: %s", ", ".join(f"{u.name} ({u.id})" for u in units.values()))
        watched = [e for u in units.values() for e in (u.id, u.runtime_entity, u.ambient_entity, u.led_entity, u.mute_entity) if e]
        if self._unsub_states:
            self._unsub_states()
        self._unsub_states = async_track_state_change_event(self.hass, watched, self._on_state) if watched else None
        for unit in units.values():
            # Anything changed while Home Assistant was away is noticed now.
            self._record_air(unit)
        self._wake.set()
        async_dispatcher_send(self.hass, SIGNAL_UNITS)
        self._changed()

    # Watching the BedJets

    def _air_from_history(self, unit_id: str) -> Air | None:
        last = self.store.last_entry(unit_id)
        if not last:
            return None
        data = last["data"]
        if last["kind"] == "air":
            return Air(Mode(data["mode"]), data.get("temperature"), data.get("fan"))
        if last["kind"] == "off":
            return Air(Mode.STANDBY, None, None)
        return None

    def _unit_owning(self, entity_id: str) -> tuple[BedJetUnit, str] | None:
        for unit in self.units.values():
            if entity_id == unit.id:
                return unit, "climate"
            if entity_id == unit.runtime_entity:
                return unit, "runtime"
            if entity_id in (unit.ambient_entity, unit.led_entity, unit.mute_entity):
                return unit, "other"
        return None

    @callback
    def _on_state(self, event: Event) -> None:
        owner = self._unit_owning(event.data["entity_id"])
        if owner is None:
            return
        unit, role = owner
        if role == "runtime":
            air = unit.air()
            minutes = unit.runtime_minutes
            if air and air.mode != Mode.STANDBY and minutes is not None:
                self._runtime_seen[unit.id] = minutes
        elif role == "climate":
            self._watch_for_turn_off(unit, event.data.get("old_state"), event.data.get("new_state"))
            self._record_later(unit)
        self._changed()

    def _record_later(self, unit: BedJetUnit) -> None:
        cancel = self._record_timers.pop(unit.id, None)
        if cancel:
            cancel()

        @callback
        def record(_now: datetime) -> None:
            self._record_timers.pop(unit.id, None)
            self._record_air(unit)

        self._record_timers[unit.id] = async_call_later(self.hass, RECORD_SETTLE, record)

    def _watch_for_turn_off(self, unit: BedJetUnit, old: State | None, new: State | None) -> None:
        """A BedJet turned off mid-phase, on its own remote or elsewhere in Home Assistant, ends the night.

        Its own timer running out does not: the schedule carries on with the next phase.
        """
        run = self.runs.get(unit.id)
        if run is None or old is None or new is None:
            return
        if old.state in ("off", "unavailable", "unknown") or new.state != "off":
            return
        now = time.time()
        index = run.index_at(now)
        if index is None or run.schedule.phases[index].mode == Mode.WAIT:
            return
        if unit.lock.locked() or now - unit.commanded_at < 10:
            return
        if self._runtime_seen.get(unit.id, 99) <= 1:
            _LOGGER.info("%s timer ran out mid-phase; the schedule resumes at the next phase", unit.id)
            return
        _LOGGER.info("%s was turned off elsewhere; ending %s", unit.id, run.schedule.name)
        self.hass.async_create_task(self.end_night(unit.id, ending="turned_off", turn_off=False))

    @callback
    def _record_air(self, unit: BedJetUnit) -> None:
        if unit.lock.locked():
            # A change is still being made; record where it lands, not the steps on the way.
            self._record_later(unit)
            return
        air = unit.air()
        if air is None:
            return
        if unit.id not in self._seeded:
            self._seeded.add(unit.id)
            last = self._air_from_history(unit.id)
            if last is None:
                # A BedJet seen for the first time starts its history at its first change.
                self._last_air[unit.id] = air
                return
            self._last_air[unit.id] = last
        if _air_key(air) == _air_key(self._last_air.get(unit.id)):
            return
        self._last_air[unit.id] = air
        now = time.time()
        if now < self._quiet_until.get(unit.id, 0):
            return
        run = self.runs.get(unit.id)
        if run is not None and self._is_schedule_doing(run, air, now):
            return
        if air.mode == Mode.STANDBY:
            data: dict[str, Any] = {"timer_ran_out": self._runtime_seen.get(unit.id, 99) <= 1}
            self.store.record(unit.id, "off", data, unit.room_celsius)
        else:
            data = {"mode": air.mode.value, "temperature": air.temperature, "fan": air.fan}
            self.store.record(unit.id, "air", data, unit.room_celsius)
        self._changed()

    @staticmethod
    def _is_schedule_doing(run: ActiveRun, air: Air, now: float) -> bool:
        """True when the BedJet is only doing what the running schedule told it to."""
        index = run.index_at(now)
        if index is None:
            return air.mode == Mode.STANDBY
        phase = run.schedule.phases[index]
        if phase.mode == Mode.WAIT:
            return air.mode == Mode.STANDBY
        if air.mode != phase.mode or (air.fan is not None and air.fan != phase.fan):
            return False
        if phase.mode.uses_temperature and air.temperature is not None:
            return abs(air.temperature - phase.temperature) <= 1
        return True

    # Playing schedules

    async def _loop(self) -> None:
        while True:
            try:
                await self.async_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must keep the night going
                _LOGGER.exception("Scheduler tick failed")
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._next_wake())

    def _next_wake(self) -> float:
        now = time.time()
        wait = 30.0
        for unit_id, run in self.runs.items():
            index = run.index_at(now)
            if index is None:
                return 0.5
            if self.applied.get(unit_id) != index:
                wait = min(wait, RETRY_SECONDS)
            wait = min(wait, run.phase_ends()[index] - now + 0.5)
        return max(wait, 0.5)

    async def async_tick(self) -> None:
        now = time.time()
        for unit_id in list(self.runs):
            await self._play(unit_id, now)
        await self._bedtime_starts()
        await self._return_lent()
        if now - self._pruned_at > 86400:
            self._pruned_at = now
            self.store.prune(now - HISTORY_DAYS * 86400)

    async def _play(self, unit_id: str, now: float) -> None:
        run = self.runs.get(unit_id)
        if run is None:
            return
        index = run.index_at(now)
        if index is None:
            await self.end_night(unit_id, ending="finished")
            return
        if self.applied.get(unit_id) == index:
            return
        unit = self.units.get(unit_id)
        if unit is None or not unit.available:
            self.errors[unit_id] = "Waiting for Home Assistant to reach your BedJet."
            self._changed()
            return
        phase = run.schedule.phases[index]
        left = max(math.ceil(run.seconds_left_in_phase(now) / 60), 1)
        last = index == len(run.schedule.phases) - 1
        try:
            await unit.apply(phase.mode, phase.temperature, phase.fan, left if last else left + RUNTIME_MARGIN)
        except HomeAssistantError as err:
            self.errors[unit_id] = f"Couldn't start phase {index + 1}: {err}. Trying again shortly."
            _LOGGER.warning("%s phase %s failed: %s", unit_id, index + 1, err)
            self._changed()
            return
        self.errors.pop(unit_id, None)
        self.applied[unit_id] = index
        self._save_runs()
        _LOGGER.info("%s: %s phase %s of %s", unit_id, run.schedule.name, index + 1, len(run.schedule.phases))
        self._changed()

    def _save_runs(self) -> None:
        self.store.put(
            "runs",
            {unit_id: {**run.to_dict(), "applied": self.applied.get(unit_id, -1)} for unit_id, run in self.runs.items()}
            or None,
        )

    # Bedtime starts

    async def _bedtime_starts(self) -> None:
        local = dt_util.now()
        minute = local.hour * 60 + local.minute
        for unit_id, unit in self.units.items():
            settings = self.unit_settings(unit_id)
            auto = settings.get("auto") or {}
            if not auto.get("enabled") or unit_id in self.runs or not unit.available:
                continue
            schedule = self.tonight(unit_id)
            if schedule is None:
                continue
            late = (minute - schedule.bedtime) % 1440
            if late >= AUTO_START_WINDOW:
                continue
            evening = (local - timedelta(minutes=late)).date()
            if evening.weekday() not in auto.get("days", list(range(7))):
                continue
            if settings.get("last_auto") == evening.isoformat():
                continue
            self.update_unit_settings(unit_id, last_auto=evening.isoformat())
            try:
                await self.start_night(unit_id, schedule.id, source="bedtime", strict=False)
            except ActionError as err:
                self.errors[unit_id] = f"Bedtime start skipped: {err}"
                self._changed()

    # Lending to a phone

    async def lend(self, unit_id: str, minutes: int) -> None:
        unit = self._unit(unit_id)
        if unit_id in self.runs:
            raise ActionError("End tonight's schedule first. Home Assistant can't play it while a phone has your BedJet.")
        if not unit.config_entry_id:
            raise ActionError("Couldn't find this BedJet's integration in Home Assistant.")
        minutes = min(max(int(minutes), 5), 240)
        self.update_unit_settings(unit_id, lend_until=time.time() + minutes * 60, lend_entry=unit.config_entry_id)
        try:
            await self.hass.config_entries.async_set_disabled_by(unit.config_entry_id, ConfigEntryDisabler.USER)
        except Exception as err:  # noqa: BLE001 - any failure is shown to the person
            self.update_unit_settings(unit_id, lend_until=None, lend_entry=None)
            raise ActionError(f"Home Assistant wouldn't let go of the BedJet: {err}") from err
        self._wake.set()

    async def reclaim(self, unit_id: str) -> None:
        settings = self.unit_settings(unit_id)
        entry = settings.get("lend_entry") or (self.units[unit_id].config_entry_id if unit_id in self.units else None)
        if not entry:
            return
        if self.hass.config_entries.async_get_entry(entry) is not None:
            try:
                await self.hass.config_entries.async_set_disabled_by(entry, None)
            except Exception as err:  # noqa: BLE001 - any failure is shown to the person
                raise ActionError(f"Home Assistant couldn't take the BedJet back: {err}") from err
        self.update_unit_settings(unit_id, lend_until=None, lend_entry=None)

    async def _return_lent(self) -> None:
        now = time.time()
        for unit_id in list((self.store.get("unit_settings") or {}).keys()):
            until = self.unit_settings(unit_id).get("lend_until")
            if until and until <= now:
                with contextlib.suppress(ActionError):
                    await self.reclaim(unit_id)

    # Settings

    def unit_settings(self, unit_id: str) -> dict[str, Any]:
        return dict((self.store.get("unit_settings") or {}).get(unit_id) or {})

    def update_unit_settings(self, unit_id: str, **changes: Any) -> dict[str, Any]:
        settings = {**self.unit_settings(unit_id), **changes}
        settings = {key: value for key, value in settings.items() if value is not None}
        everything = dict(self.store.get("unit_settings") or {})
        everything[unit_id] = settings
        self.store.put("unit_settings", everything)
        self._changed()
        return settings

    def tonight(self, unit_id: str) -> Schedule | None:
        chosen = self.unit_settings(unit_id).get("tonight")
        schedule = self.store.schedule(chosen) if chosen else None
        if schedule is None:
            schedules = self.store.schedules()
            schedule = schedules[0] if schedules else None
        return schedule

    def _unit(self, unit_id: str) -> BedJetUnit:
        unit = self.units.get(unit_id)
        if unit is None:
            raise ActionError("That BedJet isn't in Home Assistant right now.")
        return unit

    # Actions

    async def start_night(
        self, unit_id: str, schedule_id: str | None = None, source: str = "you", strict: bool = True
    ) -> None:
        unit = self._unit(unit_id)
        if unit_id in self.runs:
            raise ActionError("A schedule is already running. End it first.")
        schedule = self.store.schedule(schedule_id) if schedule_id else self.tonight(unit_id)
        if schedule is None:
            raise ActionError("Pick a schedule first.")
        reason = problem(schedule, unit.generation, unit.name)
        if reason:
            raise ActionError(reason)
        if self.unit_settings(unit_id).get("lend_until"):
            raise ActionError("Your BedJet is lent to a phone. Take it back first.")
        if strict and not unit.available:
            raise ActionError("Home Assistant can't reach your BedJet right now. Check that it's plugged in.")
        self.runs[unit_id] = ActiveRun(schedule, time.time())
        self.applied[unit_id] = -1
        self._save_runs()
        self.store.record(
            unit_id,
            "schedule_started",
            {"name": schedule.name, "phases": [p.to_dict() for p in schedule.phases], "source": source},
            unit.room_celsius,
        )
        _LOGGER.info("%s: starting %s (%s)", unit_id, schedule.name, source)
        await self._play(unit_id, time.time())
        if strict and self.applied.get(unit_id) != 0 and unit_id in self.runs:
            error = self.errors.pop(unit_id, "Your BedJet didn't respond.")
            await self.end_night(unit_id, ending="failed", turn_off=False)
            raise ActionError(error)
        self._wake.set()
        self._changed()

    async def end_night(self, unit_id: str, ending: str = "by_you", turn_off: bool = True) -> None:
        run = self.runs.pop(unit_id, None)
        self.applied.pop(unit_id, None)
        if run is None:
            return
        self._save_runs()
        self.errors.pop(unit_id, None)
        unit = self.units.get(unit_id)
        self.store.record(unit_id, "schedule_ended", {"name": run.schedule.name, "ending": ending},
                          unit.room_celsius if unit else None)
        self._quiet_until[unit_id] = time.time() + 20
        _LOGGER.info("%s: %s ended (%s)", unit_id, run.schedule.name, ending)
        self._changed()
        if turn_off and unit is not None and unit.available:
            try:
                await unit.turn_off()
            except HomeAssistantError as err:
                raise ActionError(f"The schedule ended, but your BedJet didn't turn off: {err}") from err

    async def remote(self, unit_id: str, body: dict[str, Any]) -> None:
        """The Remote tab. Choosing a mode or a program ends the schedule that's running, as in Lull."""
        unit = self._unit(unit_id)
        if not unit.available:
            raise ActionError("Home Assistant can't reach your BedJet right now.")
        try:
            if "mode" in body:
                mode = Mode(body["mode"])
                if unit_id in self.runs:
                    await self.end_night(unit_id, turn_off=False)
                if mode == Mode.STANDBY:
                    await unit.turn_off()
                else:
                    await unit.apply(mode, None, None, None)
            elif "program" in body:
                if unit_id in self.runs:
                    await self.end_night(unit_id, turn_off=False)
                await unit.start_program(str(body["program"]))
            elif "temperature" in body:
                await unit.set_temperature(int(body["temperature"]))
            elif "fan" in body:
                await unit.set_fan(int(body["fan"]))
            elif "runtime" in body:
                await unit.set_runtime(int(body["runtime"]))
            elif "led" in body:
                await unit.set_switch("led", bool(body["led"]))
            elif "muted" in body:
                await unit.set_switch("muted", bool(body["muted"]))
            else:
                raise ActionError("Nothing to change.")
        except ActionError:
            raise
        except (HomeAssistantError, ValueError) as err:
            raise ActionError(str(err)) from err

    def save_schedule(self, data: dict[str, Any]) -> Schedule:
        try:
            schedule = Schedule.from_dict(data)
        except (ValueError, TypeError) as err:
            raise ActionError(f"That schedule doesn't look right: {err}") from err
        self.store.save_schedule(schedule)
        self._changed()
        return schedule

    def delete_schedule(self, schedule_id: str) -> None:
        for run in self.runs.values():
            if run.schedule.id == schedule_id:
                raise ActionError("That schedule is running. End the night first.")
        self.store.delete_schedule(schedule_id)
        self._changed()

    def set_tonight(self, unit_id: str, schedule_id: str) -> None:
        if self.store.schedule(schedule_id) is None:
            raise ActionError("That schedule no longer exists.")
        self.update_unit_settings(unit_id, tonight=schedule_id)

    def set_auto(self, unit_id: str, enabled: bool, days: list[int] | None = None) -> None:
        current = self.unit_settings(unit_id).get("auto") or {}
        chosen = days if days is not None else current.get("days", list(range(7)))
        self.update_unit_settings(
            unit_id, auto={"enabled": bool(enabled), "days": sorted({int(d) for d in chosen if 0 <= int(d) <= 6})}
        )

    # Reading

    def unit_snapshot(self, unit: BedJetUnit) -> dict[str, Any]:
        settings = self.unit_settings(unit.id)
        run = self.runs.get(unit.id)
        tonight = self.tonight(unit.id)
        return {
            "id": unit.id,
            "name": unit.name,
            "model": unit.model,
            "generation": unit.generation,
            "status": unit.status(),
            "programs": unit.programs,
            "has_runtime": unit.runtime_entity is not None,
            "has_led": unit.led_entity is not None,
            "has_mute": unit.mute_entity is not None,
            "tonight": tonight.id if tonight else None,
            "auto": settings.get("auto") or {"enabled": False, "days": list(range(7))},
            "run": {**run.to_dict(), "phase": run.index_at(time.time()), "ends_at": run.ends_at} if run else None,
            "night": describe_run(run),
            "lend_until": settings.get("lend_until"),
            "error": self.errors.get(unit.id),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "fahrenheit": self.hass.config.units.temperature_unit == "°F",
            "time_zone": self.hass.config.time_zone,
            "now": time.time(),
            "units": [self.unit_snapshot(unit) for unit in self.units.values()],
            "schedules": [schedule.to_dict() for schedule in self.store.schedules()],
            "templates": [schedule.to_dict() for schedule in templates()],
        }

    def history(self, unit_id: str, nights: int = 14) -> list[dict[str, Any]]:
        """Everything recorded between one noon and the next, so 3 a.m. belongs with the evening before."""
        tz = dt_util.get_default_time_zone()
        entries = self.store.history(unit_id, time.time() - (nights + 1) * 86400)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            evening = (datetime.fromtimestamp(entry["ts"], tz) - timedelta(hours=12)).date().isoformat()
            grouped.setdefault(evening, []).append(entry)
        return [
            {"id": evening, "entries": grouped[evening], "phases": night_phases(grouped[evening])}
            for evening in sorted(grouped, reverse=True)[:nights]
        ]
