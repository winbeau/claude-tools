"""Playwright wrapper used by the research agent.

Owns one shared Chromium instance + browser context per process. Loads / saves
storage state at data/sessions/<source>.json so any login captured via `claw
login` is reused on headless runs.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from ..config import get_settings
from .logging import get_logger

log = get_logger(__name__)


class BrowserPool:
    def __init__(self, headless: bool = True) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._headless = headless
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._pw is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        log.info("playwright chromium launched (headless=%s)", self._headless)

    async def stop(self) -> None:
        for ctx in self._contexts.values():
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def context(self, source: str) -> BrowserContext:
        """Get-or-create a context for a logical source (bing / zhihu / ...).

        If `data/sessions/<source>.json` exists, it's loaded as storage_state."""
        async with self._lock:
            if self._browser is None:
                await self.start()
            if source in self._contexts:
                return self._contexts[source]
            state_path = get_settings().claw_session_dir / f"{source}.json"
            kwargs: dict = {
                "user_agent": get_settings().user_agent,
                "viewport": {"width": 1280, "height": 800},
                "locale": "zh-CN",
            }
            if state_path.exists():
                kwargs["storage_state"] = str(state_path)
                log.info("loaded storage_state for %s from %s", source, state_path)
            ctx = await self._browser.new_context(**kwargs)
            self._contexts[source] = ctx
            return ctx

    async def save_state(self, source: str) -> Path:
        """Persist current cookies/localStorage for a source (e.g. after `claw login`)."""
        ctx = self._contexts.get(source)
        if ctx is None:
            raise RuntimeError(f"no active context for {source}")
        out = get_settings().claw_session_dir
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{source}.json"
        await ctx.storage_state(path=str(path))
        log.info("saved storage_state for %s -> %s", source, path)
        return path


@asynccontextmanager
async def browser_pool(headless: bool = True):
    pool = BrowserPool(headless=headless)
    try:
        await pool.start()
        yield pool
    finally:
        await pool.stop()
