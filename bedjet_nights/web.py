"""The web UI and the small JSON API behind it.

Live updates go to open pages over Server-Sent Events, so a phone, a tablet and a Home
Assistant dashboard all show the same night at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from .nights import ActionError, Nights

_LOGGER = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"

NIGHTS_KEY = web.AppKey("nights", Nights)
EXTRAS_KEY = web.AppKey("extras", dict)


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _snapshot(request: web.Request) -> dict[str, Any]:
    snapshot = request.app[NIGHTS_KEY].snapshot()
    snapshot.update(request.app[EXTRAS_KEY])
    bridge = request.app[EXTRAS_KEY].get("mqtt_bridge")
    snapshot.pop("mqtt_bridge", None)
    snapshot["mqtt"] = {"enabled": bridge is not None, "connected": bool(bridge and bridge.connected)}
    return snapshot


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def state(request: web.Request) -> web.Response:
    return web.json_response(_snapshot(request))


async def events(request: web.Request) -> web.StreamResponse:
    nights = request.app[NIGHTS_KEY]
    response = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
    await response.prepare(request)
    changed = asyncio.Event()
    nights.listeners.append(changed.set)
    try:
        changed.set()
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(changed.wait(), 25)
            if changed.is_set():
                changed.clear()
                # Several updates arrive together when the BedJet changes mode.
                await asyncio.sleep(0.25)
                changed.clear()
                payload = json.dumps(_snapshot(request))
                await response.write(f"event: state\ndata: {payload}\n\n".encode())
            else:
                await response.write(b": keep-alive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(ValueError):
            nights.listeners.remove(changed.set)
    return response


def _action(handler):  # type: ignore[no-untyped-def]
    async def wrapped(request: web.Request) -> web.Response:
        try:
            result = await handler(request, request.app[NIGHTS_KEY], await _body(request))
        except ActionError as err:
            return _error(str(err))
        return web.json_response(result if result is not None else _snapshot(request))

    return wrapped


@_action
async def save_schedule(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    schedule = nights.save_schedule(body)
    return {"schedule": schedule.to_dict()}


@_action
async def delete_schedule(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    nights.delete_schedule(request.match_info["schedule_id"])


@_action
async def set_tonight(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    nights.set_tonight(request.match_info["unit_id"], str(body.get("schedule_id", "")))


@_action
async def start(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    await nights.start_night(request.match_info["unit_id"], body.get("schedule_id"))


@_action
async def end(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    await nights.end_night(request.match_info["unit_id"])


@_action
async def remote(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    await nights.remote(request.match_info["unit_id"], body)


@_action
async def auto(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    nights.set_auto(request.match_info["unit_id"], bool(body.get("enabled")), body.get("days"))


@_action
async def lend(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    await nights.lend(request.match_info["unit_id"], int(body.get("minutes", 30)))


@_action
async def reclaim(request: web.Request, nights: Nights, body: dict[str, Any]) -> Any:
    await nights.reclaim(request.match_info["unit_id"])


async def history(request: web.Request) -> web.Response:
    nights = request.app[NIGHTS_KEY]
    count = min(max(int(request.query.get("nights", "14")), 1), 90)
    return web.json_response({"nights": nights.history(request.match_info["unit_id"], count)})


def build_app(nights: Nights, extras: dict[str, Any] | None = None) -> web.Application:
    app = web.Application()
    app[NIGHTS_KEY] = nights
    app[EXTRAS_KEY] = extras or {}
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/state", state),
            web.get("/api/events", events),
            web.post("/api/schedules", save_schedule),
            web.delete("/api/schedules/{schedule_id}", delete_schedule),
            web.post("/api/units/{unit_id}/tonight", set_tonight),
            web.post("/api/units/{unit_id}/start", start),
            web.post("/api/units/{unit_id}/end", end),
            web.post("/api/units/{unit_id}/remote", remote),
            web.post("/api/units/{unit_id}/auto", auto),
            web.post("/api/units/{unit_id}/lend", lend),
            web.post("/api/units/{unit_id}/reclaim", reclaim),
            web.get("/api/units/{unit_id}/history", history),
            web.static("/static", STATIC),
        ]
    )
    return app
