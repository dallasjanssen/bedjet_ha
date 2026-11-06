"""The WebSocket commands the sidebar panel uses."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.bedjet_nights.nights import Nights

from conftest import CLIMATE


async def test_panel_commands(hass: HomeAssistant, nights: Nights, hass_ws_client):
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "bedjet_nights/subscribe"})
    assert (await client.receive_json())["success"]
    first = (await client.receive_json())["event"]
    assert first["fahrenheit"] is True
    assert first["time_zone"] == "America/Chicago"
    assert first["units"][0]["id"] == CLIMATE
    assert first["units"][0]["status"]["mode"] == "standby"

    await client.send_json_auto_id({
        "type": "bedjet_nights/schedule/save",
        "schedule": {"name": "Mine", "bedtime": 1350, "phases": [{"mode": "cool", "fan": 35, "minutes": 120}]},
    })
    reply = await _result(client)
    schedule_id = reply["result"]["schedule"]["id"]

    await client.send_json_auto_id({"type": "bedjet_nights/tonight", "unit_id": CLIMATE, "schedule_id": schedule_id})
    assert (await _result(client))["result"]["units"][0]["tonight"] == schedule_id

    await client.send_json_auto_id({"type": "bedjet_nights/start", "unit_id": CLIMATE})
    state = (await _result(client))["result"]
    assert state["units"][0]["run"]["schedule"]["name"] == "Mine"

    await client.send_json_auto_id({"type": "bedjet_nights/start", "unit_id": CLIMATE})
    error = await _result(client)
    assert not error["success"]
    assert "already running" in error["error"]["message"]

    await client.send_json_auto_id({"type": "bedjet_nights/remote", "unit_id": CLIMATE, "fan": 70})
    assert (await _result(client))["success"]

    await client.send_json_auto_id({"type": "bedjet_nights/end", "unit_id": CLIMATE})
    assert (await _result(client))["result"]["units"][0]["run"] is None

    await client.send_json_auto_id({"type": "bedjet_nights/auto", "unit_id": CLIMATE, "enabled": True, "days": [0, 1, 2, 3, 4]})
    assert (await _result(client))["result"]["units"][0]["auto"] == {"enabled": True, "days": [0, 1, 2, 3, 4]}

    await client.send_json_auto_id({"type": "bedjet_nights/history", "unit_id": CLIMATE})
    nights_list = (await _result(client))["result"]["nights"]
    kinds = [entry["kind"] for entry in nights_list[0]["entries"]]
    assert kinds[0] == "schedule_started"
    assert kinds[-1] == "schedule_ended"

    await client.send_json_auto_id({"type": "bedjet_nights/schedule/delete", "schedule_id": schedule_id})
    assert all(s["id"] != schedule_id for s in (await _result(client))["result"]["schedules"])


async def _result(client):
    """The next command result, skipping any live updates from the subscription."""
    while True:
        message = await client.receive_json()
        if message.get("type") == "result":
            return message
