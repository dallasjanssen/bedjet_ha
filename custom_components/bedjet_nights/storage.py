"""Everything kept between restarts: schedules, per-BedJet settings, the night in progress and history.

Kept in Home Assistant's own storage (the .storage folder), in two files so the history, which
changes often, never rewrites the schedules.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .model import Schedule, templates

SAVE_DELAY = 2
HISTORY_SAVE_DELAY = 15


class NightsStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.data")
        self._history_store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.history")
        self.data: dict[str, Any] = {"schedules": [], "settings": {}}
        self.entries: list[dict[str, Any]] = []
        self._next_id = 1

    async def async_load(self) -> None:
        self.data = await self._store.async_load() or {"schedules": [], "settings": {}}
        self.data.setdefault("schedules", [])
        self.data.setdefault("settings", {})
        history = await self._history_store.async_load() or {}
        self.entries = history.get("entries", [])
        self._next_id = max((entry.get("id", 0) for entry in self.entries), default=0) + 1
        if not self.get("seeded"):
            # A first start fills the library with the starter schedules, as Lull does.
            for schedule in templates():
                self.save_schedule(schedule.copy())
            self.put("seeded", True)

    async def async_flush(self) -> None:
        await self._store.async_save(self.data)
        await self._history_store.async_save({"entries": self.entries})

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.data, SAVE_DELAY)

    def _save_history(self) -> None:
        self._history_store.async_delay_save(lambda: {"entries": self.entries}, HISTORY_SAVE_DELAY)

    # Settings

    def get(self, key: str, default: Any = None) -> Any:
        return self.data["settings"].get(key, default)

    def put(self, key: str, value: Any) -> None:
        if value is None:
            self.data["settings"].pop(key, None)
        else:
            self.data["settings"][key] = value
        self._save()

    # Schedules

    def schedules(self) -> list[Schedule]:
        return [Schedule.from_dict(item) for item in self.data["schedules"]]

    def schedule(self, schedule_id: str) -> Schedule | None:
        for item in self.data["schedules"]:
            if item.get("id") == schedule_id:
                return Schedule.from_dict(item)
        return None

    def save_schedule(self, schedule: Schedule) -> None:
        body = schedule.to_dict()
        for index, item in enumerate(self.data["schedules"]):
            if item.get("id") == schedule.id:
                self.data["schedules"][index] = body
                break
        else:
            self.data["schedules"].append(body)
        self._save()

    def delete_schedule(self, schedule_id: str) -> None:
        self.data["schedules"] = [item for item in self.data["schedules"] if item.get("id") != schedule_id]
        self._save()

    # History

    def record(self, unit: str, kind: str, data: dict[str, Any], room: float | None, ts: float | None = None) -> None:
        self.entries.append(
            {"id": self._next_id, "unit": unit, "ts": time.time() if ts is None else ts, "kind": kind, "data": data, "room": room}
        )
        self._next_id += 1
        self._save_history()

    def history(self, unit: str, since: float) -> list[dict[str, Any]]:
        found = [entry for entry in self.entries if entry["unit"] == unit and entry["ts"] >= since]
        return sorted(found, key=lambda entry: (entry["ts"], entry["id"]))

    def last_entry(self, unit: str) -> dict[str, Any] | None:
        for entry in reversed(self.entries):
            if entry["unit"] == unit:
                return entry
        return None

    def prune(self, older_than: float) -> None:
        kept = [entry for entry in self.entries if entry["ts"] >= older_than]
        if len(kept) != len(self.entries):
            self.entries = kept
            self._save_history()
