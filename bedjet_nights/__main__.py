"""Starts BedJet Nights: python -m bedjet_nights"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import aiohttp
from aiohttp import web

from .config import Config
from .ha import HomeAssistant
from .nights import Nights
from .store import Store
from .web import build_app

_LOGGER = logging.getLogger("bedjet_nights")


async def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = Store(os.path.join(config.data_dir, "bedjet_nights.db"))
    async with aiohttp.ClientSession() as session:
        ha = HomeAssistant(config.ha_url, config.ha_token, session)
        nights = Nights(config, store, ha)
        bridge = None
        if config.mqtt_host:
            from .mqtt import MqttBridge

            bridge = MqttBridge(config, nights)
        app = build_app(nights, {"mqtt_bridge": bridge})
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.port)
        await site.start()
        _LOGGER.info("BedJet Nights is on port %s, talking to Home Assistant at %s", config.port, config.ha_url)

        ha.start()
        nights.start()
        if bridge:
            bridge.start()

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await stop.wait()

        _LOGGER.info("Stopping")
        if bridge:
            await bridge.stop()
        await nights.stop()
        await ha.stop()
        await runner.cleanup()
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
