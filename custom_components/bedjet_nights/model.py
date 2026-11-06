"""Schedules, phases and the BedJet's own limits.

The phase model, the limits, the starter templates and the way a night is rebuilt from its
history follow Lull (https://github.com/rkanjani/lull, MIT licence, Rohan Kanjani). See NOTICE.

Temperatures are kept in half degrees Celsius, the unit the BedJet itself works in, so every
limit below matches the unit exactly. The web UI converts for display.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MAX_STEP_MINUTES = 11 * 60 + 59
"""The BedJet refuses a step of 12 hours or longer."""

MAX_PHASES = 32
NAME_LENGTH = 40


class Mode(str, Enum):
    STANDBY = "standby"
    HEAT = "heat"
    TURBO = "turbo"
    EXTENDED_HEAT = "extended_heat"
    COOL = "cool"
    DRY = "dry"
    WAIT = "wait"

    @property
    def temperature_range(self) -> tuple[int, int]:
        """Target limits in half degrees Celsius, as the official app enforces them."""
        return _TEMPERATURE_RANGE[self]

    @property
    def uses_temperature(self) -> bool:
        low, high = self.temperature_range
        return low != high

    @property
    def uses_fan(self) -> bool:
        return self not in (Mode.STANDBY, Mode.WAIT)

    @property
    def default_temperature(self) -> int:
        return _DEFAULT_TEMPERATURE.get(self, self.temperature_range[0])

    @property
    def max_minutes(self) -> int:
        """Longest one phase may run. Turbo and Heat follow the unit's own safety timers."""
        return _MODE_MAX_MINUTES.get(self, MAX_STEP_MINUTES)

    @property
    def title(self) -> str:
        return _TITLES[self]

    @property
    def activity(self) -> str:
        return _ACTIVITIES[self]


_TEMPERATURE_RANGE = {
    Mode.HEAT: (45, 80),
    Mode.TURBO: (86, 86),
    Mode.EXTENDED_HEAT: (38, 67),
    Mode.COOL: (38, 62),
    Mode.DRY: (40, 80),
    Mode.STANDBY: (80, 80),
    Mode.WAIT: (80, 80),
}
_DEFAULT_TEMPERATURE = {Mode.HEAT: 66, Mode.EXTENDED_HEAT: 58, Mode.COOL: 44, Mode.DRY: 60}
_MODE_MAX_MINUTES = {Mode.TURBO: 10, Mode.HEAT: 240}
_TITLES = {
    Mode.STANDBY: "Off",
    Mode.HEAT: "Heat",
    Mode.TURBO: "Turbo",
    Mode.EXTENDED_HEAT: "Gentle heat",
    Mode.COOL: "Cool",
    Mode.DRY: "Dry",
    Mode.WAIT: "Pause",
}
_ACTIVITIES = {
    Mode.STANDBY: "Off",
    Mode.HEAT: "Heating",
    Mode.TURBO: "Turbo heating",
    Mode.EXTENDED_HEAT: "Gently heating",
    Mode.COOL: "Cooling",
    Mode.DRY: "Drying",
    Mode.WAIT: "Paused",
}

PHASE_MODES = [Mode.COOL, Mode.HEAT, Mode.EXTENDED_HEAT, Mode.TURBO, Mode.DRY, Mode.WAIT]
REMOTE_MODES = [Mode.COOL, Mode.HEAT, Mode.EXTENDED_HEAT, Mode.TURBO, Mode.DRY]
V2_MODES = {Mode.STANDBY, Mode.HEAT, Mode.TURBO, Mode.COOL, Mode.WAIT}


def _heat_rows() -> list[list[int]]:
    quiet = [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC6, 0x66, 0x66, 0x41]
    strong = [0xCC, 0xCC, 0xCC, 0xCC, 0x44, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11]
    middle = [
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC6, 0x66, 0x64, 0x44, 0x21],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC6, 0x64, 0x44, 0x44, 0x21],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC6, 0x64, 0x42, 0x22, 0x21],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC4, 0x44, 0x42, 0x22, 0x21],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC4, 0x44, 0x42, 0x11, 0x11],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xC4, 0x44, 0x44, 0x42, 0x11, 0x11],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xC4, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11],
        [0xCC, 0xCC, 0xCC, 0xCC, 0xC4, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11],
    ]
    return [quiet] * 5 + middle + [strong] * 6


# Heat hours allowed by fan step (rows) and target from 30 C up in half degree pairs (columns).
# Each byte holds two temperatures: the even half degree in the high nibble, the odd one in the low.
_HEAT_HOURS = _heat_rows()


def longest_minutes(mode: Mode, temperature: int, fan_step: int) -> int:
    """Longest the unit lets a step run. Heat is cut off sooner the hotter and faster the air."""
    if mode != Mode.HEAT or temperature < 60:
        return MAX_STEP_MINUTES
    row = _HEAT_HOURS[min(max(fan_step, 0), len(_HEAT_HOURS) - 1)]
    offset = temperature - 60
    pair = row[min(offset >> 1, len(row) - 1)]
    hours = pair >> 4 if offset & 1 == 0 else pair & 0x0F
    return min(hours * 60, MAX_STEP_MINUTES)


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Phase:
    """One stretch of the night: what the BedJet does and for how long."""

    mode: Mode
    temperature: int
    fan: int
    minutes: int
    id: str = field(default_factory=_new_id)

    @classmethod
    def make(cls, mode: Mode, temperature: int | None = None, fan: int = 40, minutes: int = 60) -> Phase:
        return cls(mode, mode.default_temperature if temperature is None else temperature, fan, minutes)

    @property
    def fan_step(self) -> int:
        return self.fan // 5 - 1

    @property
    def max_minutes(self) -> int:
        """The mode's own ceiling and, for Heat, the safety cut-off for this temperature and fan."""
        return min(self.mode.max_minutes, longest_minutes(self.mode, self.temperature, self.fan_step))

    @property
    def fits(self) -> bool:
        return 0 < self.minutes <= self.max_minutes

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode.value,
            "temperature": self.temperature,
            "fan": self.fan,
            "minutes": self.minutes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Phase:
        """Reads a phase sent by the web UI, pulling every value back inside what the BedJet takes."""
        mode = Mode(data.get("mode", Mode.COOL.value))
        if mode not in PHASE_MODES:
            raise ValueError(f"A phase can't use {mode.title}.")
        low, high = mode.temperature_range
        temperature = _clamp(int(data.get("temperature", mode.default_temperature)), low, high)
        fan = _clamp(int(round(int(data.get("fan", 40)) / 5) * 5), 5, 100)
        minutes = _clamp(int(data.get("minutes", 60)), 1, MAX_STEP_MINUTES)
        return cls(mode, temperature, fan, minutes, str(data.get("id") or _new_id()))


@dataclass
class Schedule:
    name: str
    phases: list[Phase]
    bedtime: int = 22 * 60 + 30
    """Minutes after midnight. Labels the timeline, and is when a bedtime start happens."""
    id: str = field(default_factory=_new_id)

    @property
    def total_minutes(self) -> int:
        return sum(phase.minutes for phase in self.phases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "bedtime": self.bedtime,
            "phases": [phase.to_dict() for phase in self.phases],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        name = " ".join(str(data.get("name") or "").split())[:NAME_LENGTH] or "Untitled"
        phases = [Phase.from_dict(phase) for phase in data.get("phases") or []][:MAX_PHASES]
        bedtime = int(data.get("bedtime", 22 * 60 + 30)) % 1440
        return cls(name, phases, bedtime, str(data.get("id") or _new_id()))

    def copy(self, name: str | None = None) -> Schedule:
        return Schedule(
            name or self.name,
            [Phase(p.mode, p.temperature, p.fan, p.minutes) for p in self.phases],
            self.bedtime,
        )


def problem(schedule: Schedule, generation: str = "v3", unit_name: str = "This BedJet") -> str | None:
    """Why a BedJet can't run a schedule, in words for the person, or None when it can."""
    if not schedule.phases:
        return "Add at least one phase first."
    if not all(phase.fits for phase in schedule.phases):
        return "Your BedJet won't run a phase this long. Shorten the Heat phases or use Gentle heat."
    if generation == "v2":
        missing = sorted({p.mode.title for p in schedule.phases if p.mode not in V2_MODES})
        if missing:
            names = " or ".join(missing)
            return (
                f"{unit_name} is a BedJet V2, which can't run {names}. "
                f'Edit "{schedule.name}" or pick another schedule for it.'
            )
    return None


def templates() -> list[Schedule]:
    """Starter schedules, the same four Lull offers."""
    p = Phase.make
    return [
        Schedule("Cool Drift", [
            p(Mode.COOL, fan=60, minutes=45),
            p(Mode.COOL, fan=30, minutes=300),
            p(Mode.COOL, fan=15, minutes=90),
            p(Mode.HEAT, temperature=54, fan=30, minutes=30),
        ], id="template-cool-drift"),
        Schedule("Warm Welcome", [
            p(Mode.TURBO, fan=100, minutes=10),
            p(Mode.HEAT, temperature=68, fan=40, minutes=30),
            p(Mode.COOL, fan=20, minutes=360),
            p(Mode.HEAT, temperature=58, fan=30, minutes=40),
        ], id="template-warm-welcome"),
        Schedule("Hot Sleeper", [
            p(Mode.COOL, fan=100, minutes=60),
            p(Mode.COOL, fan=70, minutes=240),
            p(Mode.COOL, fan=40, minutes=150),
        ], id="template-hot-sleeper"),
        Schedule("Winter Cocoon", [
            p(Mode.HEAT, temperature=72, fan=50, minutes=30),
            p(Mode.EXTENDED_HEAT, temperature=62, fan=30, minutes=210),
            p(Mode.EXTENDED_HEAT, temperature=58, fan=20, minutes=240),
        ], id="template-winter-cocoon"),
    ]


@dataclass
class ActiveRun:
    """A schedule running on a BedJet right now. Home Assistant plays it phase by phase."""

    schedule: Schedule
    started_at: float

    @property
    def ends_at(self) -> float:
        return self.started_at + self.schedule.total_minutes * 60

    def phase_ends(self) -> list[float]:
        ends, end = [], self.started_at
        for phase in self.schedule.phases:
            end += phase.minutes * 60
            ends.append(end)
        return ends

    def index_at(self, now: float) -> int | None:
        """The phase playing at a moment, or None once the night is over."""
        if now < self.started_at:
            return 0
        for index, end in enumerate(self.phase_ends()):
            if now < end:
                return index
        return None

    def seconds_left_in_phase(self, now: float) -> float:
        index = self.index_at(now)
        if index is None:
            return 0
        return self.phase_ends()[index] - now

    def to_dict(self) -> dict[str, Any]:
        return {"schedule": self.schedule.to_dict(), "started_at": self.started_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActiveRun:
        return cls(Schedule.from_dict(data["schedule"]), float(data["started_at"]))


def duration(minutes: int) -> str:
    hours, rest = divmod(max(int(minutes), 0), 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} hr" if rest == 0 else f"{hours} hr {rest} min"


def describe_run(run: ActiveRun | None, now: float | None = None) -> str:
    """One line for the night as it stands, as Home Assistant shows it."""
    if run is None:
        return "Not running"
    now = time.time() if now is None else now
    index = run.index_at(now)
    if index is None:
        return "Finishing"
    phase = run.schedule.phases[index]
    left = max(int(run.seconds_left_in_phase(now) + 59) // 60, 1)
    total = len(run.schedule.phases)
    return f"{run.schedule.name}: {phase.mode.activity}, {duration(left)} left (phase {index + 1} of {total})"


# History


def night_phases(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A night redrawn as phases, so it can be drawn like a schedule.

    Each entry lasts until the next. A schedule plays out its own phases, and a change made
    during one lasts until the schedule moves to its next phase. Entries are oldest first and
    look like {"ts": epoch seconds, "kind": ..., "data": {...}}.
    """
    phases: list[dict[str, Any]] = []
    schedule: tuple[float, list[dict[str, Any]]] | None = None
    for index, entry in enumerate(entries):
        ts = entry["ts"]
        nxt = entries[index + 1]["ts"] if index + 1 < len(entries) else None
        kind, data = entry["kind"], entry.get("data") or {}
        if kind == "schedule_started" and data.get("phases"):
            played = data["phases"]
            schedule = (ts, played)
            end = nxt if nxt is not None else ts + sum(p["minutes"] for p in played) * 60
            phases += _play(played, ts, ts, end)
        elif kind == "air":
            # How long the last setting lasted is unknown, so it gets a short stretch to be visible.
            end = nxt if nxt is not None else ts + 30 * 60

            def tweak(until: float) -> dict[str, Any]:
                return {
                    "mode": data["mode"],
                    "temperature": data.get("temperature") or Mode(data["mode"]).default_temperature,
                    "fan": data.get("fan") or 40,
                    "minutes": _minutes(ts, until),
                }

            phase_end = _phase_end(schedule[1], schedule[0], ts) if schedule else None
            if schedule is None or phase_end is None or phase_end >= end:
                phases.append(tweak(end))
                continue
            phases.append(tweak(phase_end))
            phases += _play(schedule[1], schedule[0], phase_end, end)
        else:
            schedule = None
            if nxt is not None:
                phases.append({"mode": Mode.WAIT.value, "temperature": 80, "fan": 5, "minutes": _minutes(ts, nxt)})
    return [p for p in phases if p["minutes"] > 0]


def _play(schedule: list[dict[str, Any]], started_at: float, start: float, end: float) -> list[dict[str, Any]]:
    """The stretch of a schedule between two times, idle once the schedule is over."""
    played = []
    phase_start = started_at
    for phase in schedule:
        phase_end = phase_start + phase["minutes"] * 60
        if phase_end > start and phase_start < end:
            played.append({**phase, "minutes": _minutes(max(phase_start, start), min(phase_end, end))})
        phase_start = phase_end
    if phase_start < end:
        played.append({"mode": Mode.WAIT.value, "temperature": 80, "fan": 5, "minutes": _minutes(max(phase_start, start), end)})
    return played


def _phase_end(schedule: list[dict[str, Any]], started_at: float, after: float) -> float | None:
    end = started_at
    for phase in schedule:
        end += phase["minutes"] * 60
        if end > after:
            return end
    return None


def _minutes(start: float, end: float) -> int:
    return max(round((end - start) / 60), 0)
