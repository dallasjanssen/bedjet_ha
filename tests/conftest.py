"""Shared test fixtures: a pretend Home Assistant and BedJet Nights connected to it."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest_asyncio
from aiohttp import web

from bedjet_nights import nights as nights_module
from bedjet_nights.config import Config
from bedjet_nights.ha import HomeAssistant
from bedjet_nights.nights import Nights
from bedjet_nights.store import Store
from bedjet_nights.web import build_app

from fake_ha import FakeHomeAssistant


class Harness:
    def __init__(self, fake: FakeHomeAssistant, nights: Nights, ha: HomeAssistant, client: aiohttp.ClientSession, base: str):
        self.fake = fake
        self.nights = nights
        self.ha = ha
        self.client = client
        self.base = base

    def services(self) -> list[str]:
        return [call["service"] for call in self.fake.calls if "service" in call]

    async def settle(self, seconds: float = 0.2) -> None:
        await asyncio.sleep(seconds)


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(nights_module, "RECORD_SETTLE", 0.05)
    fake = FakeHomeAssistant()
    runner = web.AppRunner(fake.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    config = Config(ha_url=f"http://127.0.0.1:{port}", ha_token="test-token", data_dir=str(tmp_path))
    store = Store(str(tmp_path / "test.db"))
    session = aiohttp.ClientSession()
    ha = HomeAssistant(config.ha_url, config.ha_token, session)
    nights = Nights(config, store, ha)
    ha.start()
    for _ in range(100):
        if ha.connected and nights.units:
            break
        await asyncio.sleep(0.05)
    assert nights.units, "the BedJet was not found"

    app_runner = web.AppRunner(build_app(nights))
    await app_runner.setup()
    app_site = web.TCPSite(app_runner, "127.0.0.1", 0)
    await app_site.start()
    app_port = app_site._server.sockets[0].getsockname()[1]
    client = aiohttp.ClientSession()

    yield Harness(fake, nights, ha, client, f"http://127.0.0.1:{app_port}")

    await client.close()
    await app_runner.cleanup()
    await nights.stop()
    await ha.stop()
    await session.close()
    await runner.cleanup()
    store.close()
