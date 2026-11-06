"""The BedJet Nights sidebar panel."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_ELEMENT, PANEL_ICON, PANEL_TITLE, PANEL_URL_PATH, STATIC_URL, VERSION

FRONTEND = Path(__file__).parent / "frontend"
STATIC_REGISTERED = f"{DOMAIN}_static_registered"


async def async_register(hass: HomeAssistant) -> None:
    if "frontend" not in hass.config.components:
        # Home Assistant always loads its frontend; only a bare test setup goes without.
        return
    # A static path can't be removed again, so it is registered once per Home Assistant run.
    if not hass.data.get(STATIC_REGISTERED):
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(FRONTEND), False)])
        hass.data[STATIC_REGISTERED] = True
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        # The version busts browser caches after an update.
        module_url=f"{STATIC_URL}/bedjet-nights-panel.js?v={VERSION}",
        require_admin=False,
    )


def async_unregister(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
