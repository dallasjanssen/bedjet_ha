"""Everything kept between restarts: schedules, per-BedJet settings, the night in progress and history.

A single SQLite file in the data folder. It is small and written rarely, so plain sqlite3 calls
from the event loop are fine.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from .model import Schedule, templates


class Store:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                position INTEGER NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit TEXT NOT NULL,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                data TEXT NOT NULL,
                room REAL
            );
            CREATE INDEX IF NOT EXISTS history_unit_ts ON history (unit, ts);
            """
        )
        self._db.commit()
        if self.get("seeded") is None:
            # A first start fills the library with the starter schedules, as Lull does.
            for schedule in templates():
                self.save_schedule(schedule.copy())
            self.put("seeded", True)

    def close(self) -> None:
        self._db.close()

    # Settings

    def get(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT body FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["body"])

    def put(self, key: str, value: Any) -> None:
        if value is None:
            self._db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self._db.execute(
                "INSERT INTO settings (key, body) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET body = excluded.body",
                (key, json.dumps(value)),
            )
        self._db.commit()

    # Schedules

    def schedules(self) -> list[Schedule]:
        rows = self._db.execute("SELECT body FROM schedules ORDER BY position, rowid").fetchall()
        return [Schedule.from_dict(json.loads(row["body"])) for row in rows]

    def schedule(self, schedule_id: str) -> Schedule | None:
        row = self._db.execute("SELECT body FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return None if row is None else Schedule.from_dict(json.loads(row["body"]))

    def save_schedule(self, schedule: Schedule) -> None:
        body = json.dumps(schedule.to_dict())
        updated = self._db.execute("UPDATE schedules SET body = ? WHERE id = ?", (body, schedule.id)).rowcount
        if not updated:
            position = self._db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM schedules").fetchone()[0]
            self._db.execute(
                "INSERT INTO schedules (id, position, body) VALUES (?, ?, ?)", (schedule.id, position, body)
            )
        self._db.commit()

    def delete_schedule(self, schedule_id: str) -> None:
        self._db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self._db.commit()

    # History

    def record(self, unit: str, kind: str, data: dict[str, Any], room: float | None, ts: float | None = None) -> None:
        self._db.execute(
            "INSERT INTO history (unit, ts, kind, data, room) VALUES (?, ?, ?, ?, ?)",
            (unit, time.time() if ts is None else ts, kind, json.dumps(data), room),
        )
        self._db.commit()

    def history(self, unit: str, since: float) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, ts, kind, data, room FROM history WHERE unit = ? AND ts >= ? ORDER BY ts, id",
            (unit, since),
        ).fetchall()
        return [
            {"id": row["id"], "ts": row["ts"], "kind": row["kind"], "data": json.loads(row["data"]), "room": row["room"]}
            for row in rows
        ]

    def last_entry(self, unit: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT ts, kind, data, room FROM history WHERE unit = ? ORDER BY ts DESC, id DESC LIMIT 1", (unit,)
        ).fetchone()
        if row is None:
            return None
        return {"ts": row["ts"], "kind": row["kind"], "data": json.loads(row["data"]), "room": row["room"]}

    def prune(self, older_than: float) -> None:
        self._db.execute("DELETE FROM history WHERE ts < ?", (older_than,))
        self._db.commit()
