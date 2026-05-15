"""Shared async HTTP client with rate-limiting, retries and robots check."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_settings
from .logging import get_logger
from .ratelimit import DomainRateLimiter
from .robots import can_fetch

log = get_logger(__name__)


class ForbiddenByRobots(Exception):
    pass


class Fetcher:
    def __init__(
        self,
        rps: float | None = None,
        timeout: float = 20.0,
        user_agent: str | None = None,
    ) -> None:
        s = get_settings()
        self._ua = user_agent or s.user_agent
        self._limiter = DomainRateLimiter(default_rps=rps or s.claw_rps)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self._ua, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            http2=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=30),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _get_raw(self, url: str) -> httpx.Response:
        r = await self._client.get(url)
        if r.status_code >= 500:
            r.raise_for_status()
        return r

    async def get(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        if check_robots and not can_fetch(url, self._ua):
            log.warning("robots.txt disallows %s — skipping", url)
            raise ForbiddenByRobots(url)
        host = urlparse(url).netloc
        await self._limiter.acquire(host)
        r = await self._get_raw(url)
        log.debug("GET %s -> %s (%d bytes)", url, r.status_code, len(r.content))
        return r
