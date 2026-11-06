"""Constants for BedJet Nights."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "bedjet_nights"
VERSION = "0.2.1"

PANEL_URL_PATH = "bedjet-nights"
PANEL_ELEMENT = "bedjet-nights-panel"
PANEL_TITLE = "BedJet Nights"
PANEL_ICON = "mdi:weather-night"
STATIC_URL = "/bedjet_nights_static"

SIGNAL_UPDATE = f"{DOMAIN}_update"
"""Something about a night, a schedule or a BedJet changed."""
SIGNAL_UNITS = f"{DOMAIN}_units"
"""The set of BedJets changed, so entity platforms may need to add some."""

PLATFORMS = [Platform.SELECT, Platform.BUTTON, Platform.SWITCH, Platform.BINARY_SENSOR, Platform.SENSOR]

BEDJET_DOMAIN = "bedjet"
"""The integration whose climate entities are BedJets: ha-bedjet from HACS, or the built-in one."""

HISTORY_DAYS = 90
