"""The MQTT entities Home Assistant gets, checked without a broker."""

from __future__ import annotations

import json

from bedjet_nights.config import Config
from bedjet_nights.mqtt import MqttBridge

from conftest import Harness
from fake_ha import ADDRESS, CLIMATE


class RecordingClient:
    def __init__(self) -> None:
        self.published: dict[str, str] = {}

    async def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        assert retain, f"{topic} should be retained"
        self.published[topic] = payload


def bridge_for(harness: Harness) -> MqttBridge:
    config = Config(ha_url="http://unused", ha_token="x", mqtt_host="broker")
    return MqttBridge(config, harness.nights)


async def test_discovery_joins_the_bedjets_own_device(harness: Harness):
    bridge = bridge_for(harness)
    client = RecordingClient()
    await bridge._publish(client)

    select = json.loads(client.published["homeassistant/select/bedjet_nights_dallass_bedjet/tonight/config"])
    assert select["device"] == {"connections": [["bluetooth", ADDRESS]]}
    assert select["unique_id"] == "bedjet_nights_dallass_bedjet_tonight"
    assert select["default_entity_id"] == "select.dallass_bedjet_tonight"
    assert select["options"][:2] == ["Cool Drift", "Warm Welcome"]
    for path in (
        "button/bedjet_nights_dallass_bedjet/start_night",
        "button/bedjet_nights_dallass_bedjet/end_night",
        "switch/bedjet_nights_dallass_bedjet/start_at_bedtime",
        "binary_sensor/bedjet_nights_dallass_bedjet/night_running",
        "sensor/bedjet_nights_dallass_bedjet/night",
        "sensor/bedjet_nights_dallass_bedjet/night_ends",
    ):
        assert f"homeassistant/{path}/config" in client.published

    assert client.published["bedjet_nights/dallass_bedjet/tonight"] == "Cool Drift"
    assert client.published["bedjet_nights/dallass_bedjet/running"] == "OFF"
    assert client.published["bedjet_nights/dallass_bedjet/night"] == "Not running"
    assert client.published["bedjet_nights/dallass_bedjet/ends"] == "None"

    # Unchanged discovery isn't sent again.
    client.published.clear()
    await bridge._publish(client)
    assert not any(topic.endswith("/config") for topic in client.published)


async def test_home_assistant_can_pick_start_and_end_the_night(harness: Harness):
    bridge = bridge_for(harness)
    await bridge._handle("bedjet_nights/dallass_bedjet/tonight/set", b"Hot Sleeper")
    assert harness.nights.tonight(CLIMATE).name == "Hot Sleeper"

    await bridge._handle("bedjet_nights/dallass_bedjet/start/set", b"PRESS")
    assert harness.nights.runs[CLIMATE].schedule.name == "Hot Sleeper"
    client = RecordingClient()
    await bridge._publish(client)
    assert client.published["bedjet_nights/dallass_bedjet/running"] == "ON"
    assert client.published["bedjet_nights/dallass_bedjet/night"].startswith("Hot Sleeper: Cooling, ")
    assert client.published["bedjet_nights/dallass_bedjet/ends"].endswith("+00:00")

    await bridge._handle("bedjet_nights/dallass_bedjet/end/set", b"PRESS")
    assert CLIMATE not in harness.nights.runs

    await bridge._handle("bedjet_nights/dallass_bedjet/auto/set", b"ON")
    assert harness.nights.unit_settings(CLIMATE)["auto"]["enabled"] is True


async def test_home_assistant_restart_resends_discovery(harness: Harness):
    bridge = bridge_for(harness)
    client = RecordingClient()
    await bridge._publish(client)
    await bridge._handle("homeassistant/status", b"online")
    client.published.clear()
    await bridge._publish(client)
    assert any(topic.endswith("/config") for topic in client.published)
