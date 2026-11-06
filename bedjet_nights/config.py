"""Settings, read from the environment (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass
class Config:
    ha_url: str
    ha_token: str
    data_dir: str = "/data"
    port: int = 8099
    bedjet_entities: list[str] = field(default_factory=list)
    """Climate entities to use. Empty finds every BedJet in Home Assistant by itself."""
    mqtt_host: str | None = None
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_discovery_prefix: str = "homeassistant"
    mqtt_base_topic: str = "bedjet_nights"
    history_days: int = 90
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if env is None else env)
        url = env.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")
        token = env.get("HA_TOKEN", "")
        if not token:
            raise SystemExit(
                "HA_TOKEN is not set. Create a long-lived access token in Home Assistant "
                "(your profile, Security tab) and put it in the .env file."
            )
        return cls(
            ha_url=url,
            ha_token=token,
            data_dir=env.get("DATA_DIR", "/data"),
            port=int(env.get("PORT", "8099")),
            bedjet_entities=_list(env.get("BEDJET_ENTITIES")),
            mqtt_host=env.get("MQTT_HOST") or None,
            mqtt_port=int(env.get("MQTT_PORT", "1883")),
            mqtt_username=env.get("MQTT_USERNAME") or None,
            mqtt_password=env.get("MQTT_PASSWORD") or None,
            mqtt_discovery_prefix=env.get("MQTT_DISCOVERY_PREFIX", "homeassistant"),
            mqtt_base_topic=env.get("MQTT_BASE_TOPIC", "bedjet_nights"),
            history_days=int(env.get("HISTORY_DAYS", "90")),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
        )
