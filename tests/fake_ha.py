"""A pretend Home Assistant with one BedJet, for tests and for trying the UI without hardware.

It speaks enough of the WebSocket API for BedJet Nights and behaves like the ha-bedjet
integration: modes, presets, Fahrenheit display, per-mode limits and a runtime timer.

Run it on its own:  python tests/fake_ha.py --port 8123
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import time
from typing import Any

from aiohttp import web

CLIMATE = "climate.dallass_bedjet"
RUNTIME = "number.dallass_bedjet_runtime_remaining"
AMBIENT = "sensor.dallass_bedjet_ambient_temperature"
LED = "switch.dallass_bedjet_enable_led"
MUTE = "switch.dallass_bedjet_mute_beeps"
ADDRESS = "AA:BB:CC:00:00:01"
ENTRY_ID = "entry-bedjet"
DEVICE_ID = "device-bedjet"

# Half degrees Celsius, as the BedJet reports its per-mode limits.
LIMITS = {"cool": (38, 62), "heat": (45, 80), "dry": (40, 80), "turbo": (86, 86), "extended_heat": (38, 67), "off": (20, 80)}
DEFAULTS = {"cool": 44, "heat": 66, "dry": 60, "turbo": 86, "extended_heat": 58, "off": 44}
RUNTIME_MAX = {"cool": 719, "heat": 240, "dry": 719, "turbo": 10, "extended_heat": 600, "off": 0}
DEFAULT_RUNTIME = {"cool": 600, "heat": 60, "dry": 60, "turbo": 10, "extended_heat": 600, "off": 0}


def to_f(half: int) -> float:
    return round(half / 2 * 9 / 5 + 32)


class FakeBedJet:
    def __init__(self) -> None:
        self.mode = "off"
        self.temperature = 44
        self.fan = 60
        self.runtime = 0
        self.led = False
        self.muted = True
        self.ambient_f = 68.0

    def hvac(self) -> str:
        return {"off": "off", "cool": "cool", "dry": "dry"}.get(self.mode, "heat")

    def preset(self) -> str | None:
        return {"turbo": "Turbo", "extended_heat": "Extended Heat"}.get(self.mode)

    def states(self) -> list[dict[str, Any]]:
        low, high = LIMITS[self.mode]
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())

        def state(entity_id: str, value: Any, attributes: dict[str, Any]) -> dict[str, Any]:
            return {"entity_id": entity_id, "state": str(value), "attributes": attributes, "last_changed": now, "last_updated": now}

        return [
            state(CLIMATE, self.hvac(), {
                "hvac_modes": ["off", "cool", "heat", "dry"],
                "min_temp": to_f(low), "max_temp": to_f(high),
                "fan_modes": [f"{s}%" for s in range(5, 101, 5)],
                "preset_modes": ["Turbo", "Extended Heat", "M1: Default", "M2: Cool Night", "M3: Default", "Cool Night"],
                "current_temperature": 74 if self.mode != "off" else 68,
                "temperature": to_f(self.temperature),
                "fan_mode": f"{self.fan}%",
                "preset_mode": self.preset(),
                "temperature_unit": "°F",
                "friendly_name": "Dallas’s Bedjet",
            }),
            state(RUNTIME, self.runtime, {"min": 0, "max": RUNTIME_MAX[self.mode], "step": 1, "unit_of_measurement": "min",
                                          "friendly_name": "Dallas’s Bedjet Runtime remaining"}),
            state(AMBIENT, self.ambient_f, {"unit_of_measurement": "°F", "device_class": "temperature",
                                            "friendly_name": "Dallas’s Bedjet Ambient temperature"}),
            state(LED, "on" if self.led else "off", {"friendly_name": "Dallas’s Bedjet Enable LED"}),
            state(MUTE, "on" if self.muted else "off", {"friendly_name": "Dallas’s Bedjet Mute beeps"}),
        ]

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.temperature = DEFAULTS[mode]
        self.runtime = DEFAULT_RUNTIME[mode]


def registry() -> list[dict[str, Any]]:
    def entry(entity_id: str, key: str | None, unique: str) -> dict[str, Any]:
        return {
            "entity_id": entity_id, "platform": "bedjet", "device_id": DEVICE_ID, "config_entry_id": ENTRY_ID,
            "translation_key": key, "unique_id": unique, "disabled_by": None,
        }

    return [
        entry(CLIMATE, None, ADDRESS),
        entry(RUNTIME, "runtime_remaining", f"{ADDRESS}_runtime_remaining"),
        entry(AMBIENT, "ambient_temperature", f"{ADDRESS}_ambient_temperature"),
        entry(LED, "enable_led", f"{ADDRESS}_enable_led"),
        entry(MUTE, "mute_beeps", f"{ADDRESS}_mute_beeps"),
        {"entity_id": "light.kitchen", "platform": "hue", "device_id": "other", "translation_key": None, "unique_id": "x"},
    ]


DEVICES = [
    {"id": DEVICE_ID, "name": "Dallas’s BJ (0001)", "name_by_user": "Dallas’s Bedjet", "model": "BedJet 3",
     "manufacturer": "BedJet", "connections": [["bluetooth", ADDRESS]], "identifiers": []},
]


class FakeHomeAssistant:
    def __init__(self, token: str = "test-token") -> None:
        self.token = token
        self.bedjet = FakeBedJet()
        self.calls: list[dict[str, Any]] = []
        self.disabled_entries: set[str] = set()
        self.sockets: list[tuple[web.WebSocketResponse, int]] = []
        self.fail_services = False
        self.app = web.Application()
        self.app.add_routes([web.get("/api/websocket", self.websocket)])

    def snapshot(self) -> dict[str, dict[str, Any]]:
        if ENTRY_ID in self.disabled_entries:
            return {s["entity_id"]: {**s, "state": "unavailable"} for s in self.bedjet.states()}
        return {s["entity_id"]: s for s in self.bedjet.states()}

    async def broadcast(self, before: dict[str, dict[str, Any]]) -> None:
        after = self.snapshot()
        for entity_id, new in after.items():
            old = before.get(entity_id)
            if old and old["state"] == new["state"] and old["attributes"] == new["attributes"]:
                continue
            for ws, sub_id in list(self.sockets):
                if ws.closed:
                    continue
                await ws.send_json({"id": sub_id, "type": "event", "event": {
                    "event_type": "state_changed",
                    "data": {"entity_id": entity_id, "old_state": old, "new_state": copy.deepcopy(new)},
                }})

    async def change(self, mutate) -> None:  # type: ignore[no-untyped-def]
        before = self.snapshot()
        mutate(self.bedjet)
        await self.broadcast(before)

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.11.0"})
        auth = await ws.receive_json()
        if auth.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.0"})
        async for message in ws:
            data = message.json()
            await self.handle(ws, data)
        self.sockets = [(s, i) for s, i in self.sockets if s is not ws]
        return ws

    async def reply(self, ws: web.WebSocketResponse, message_id: int, result: Any = None, error: str | None = None) -> None:
        if error:
            await ws.send_json({"id": message_id, "type": "result", "success": False, "error": {"code": "invalid", "message": error}})
        else:
            await ws.send_json({"id": message_id, "type": "result", "success": True, "result": result})

    async def handle(self, ws: web.WebSocketResponse, data: dict[str, Any]) -> None:
        kind, message_id = data.get("type"), data.get("id")
        if kind == "get_config":
            await self.reply(ws, message_id, {"unit_system": {"temperature": "°F"}, "time_zone": "America/Chicago", "version": "2026.11.0"})
        elif kind == "subscribe_events":
            if data.get("event_type") == "state_changed":
                self.sockets.append((ws, message_id))
            await self.reply(ws, message_id, None)
        elif kind == "get_states":
            await self.reply(ws, message_id, list(self.snapshot().values()))
        elif kind == "config/entity_registry/list":
            entries = registry()
            if ENTRY_ID in self.disabled_entries:
                for entry in entries:
                    if entry.get("config_entry_id") == ENTRY_ID:
                        entry["disabled_by"] = "config_entry"
            await self.reply(ws, message_id, entries)
        elif kind == "config/device_registry/list":
            await self.reply(ws, message_id, DEVICES)
        elif kind == "config_entries/disable":
            before = self.snapshot()
            if data.get("disabled_by"):
                self.disabled_entries.add(data["entry_id"])
            else:
                self.disabled_entries.discard(data["entry_id"])
            self.calls.append({"type": kind, "entry_id": data["entry_id"], "disabled_by": data.get("disabled_by")})
            await self.reply(ws, message_id, {"require_restart": False})
            await self.broadcast(before)
        elif kind == "call_service":
            error = await self.service(data)
            await self.reply(ws, message_id, {"context": {}} if not error else None, error)
        else:
            await self.reply(ws, message_id, None, f"Unknown command {kind}")

    async def service(self, data: dict[str, Any]) -> str | None:
        domain, service = data["domain"], data["service"]
        entity_id = data["target"]["entity_id"]
        payload = data.get("service_data") or {}
        self.calls.append({"service": f"{domain}.{service}", "entity_id": entity_id, **payload})
        if self.fail_services or ENTRY_ID in self.disabled_entries:
            return "Entity is unavailable"
        bj = self.bedjet
        before = self.snapshot()
        if (domain, service) == ("climate", "set_hvac_mode"):
            bj.set_mode("cool" if payload["hvac_mode"] == "fan_only" else payload["hvac_mode"])
        elif (domain, service) == ("climate", "set_preset_mode"):
            preset = payload["preset_mode"]
            if preset == "Turbo":
                bj.set_mode("turbo")
            elif preset == "Extended Heat":
                bj.set_mode("extended_heat")
            elif preset.startswith("M") or preset == "Cool Night":
                bj.set_mode("cool")
            else:
                return f"Unknown preset {preset}"
        elif (domain, service) == ("climate", "set_temperature"):
            value = float(payload["temperature"])
            low, high = LIMITS[bj.mode]
            if not to_f(low) <= value <= to_f(high):
                return f"Provided temperature {value} is not valid. Accepted range is {to_f(low)} to {to_f(high)}."
            bj.temperature = round((value - 32) * 5 / 9 * 2)
        elif (domain, service) == ("climate", "set_fan_mode"):
            bj.fan = int(payload["fan_mode"].rstrip("%"))
        elif (domain, service) == ("number", "set_value"):
            value = float(payload["value"])
            if value > RUNTIME_MAX[bj.mode]:
                return f"Value {value} for {entity_id} is outside valid range"
            bj.runtime = int(value)
        elif domain == "switch":
            on = service == "turn_on"
            if entity_id == LED:
                bj.led = on
            else:
                bj.muted = on
        await self.broadcast(before)
        return None


async def serve(port: int, token: str) -> None:
    fake = FakeHomeAssistant(token)
    runner = web.AppRunner(fake.app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"Fake Home Assistant on http://127.0.0.1:{port} (token: {token})")
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--token", default="test-token")
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.token))
