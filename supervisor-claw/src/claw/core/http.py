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

from ..config import ListUrlSpec, get_settings
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

    # --- low-level retry-wrapped raw calls ---

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=30),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _post_raw(
        self, url: str, data: dict | None, headers: dict | None
    ) -> httpx.Response:
        r = await self._client.post(url, data=data or {}, headers=headers or {})
        if r.status_code >= 500:
            r.raise_for_status()
        return r

    # --- high-level: GET / POST with robots + rate-limit ---

    async def get(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        if check_robots and not can_fetch(url, self._ua):
            log.warning("robots.txt disallows %s — skipping", url)
            raise ForbiddenByRobots(url)
        host = urlparse(url).netloc
        await self._limiter.acquire(host)
        r = await self._get_raw(url)
        log.debug("GET %s -> %s (%d bytes)", url, r.status_code, len(r.content))
        return r

    async def post(
        self,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
        *,
        check_robots: bool = True,
    ) -> httpx.Response:
        if check_robots and not can_fetch(url, self._ua):
            log.warning("robots.txt disallows %s — skipping", url)
            raise ForbiddenByRobots(url)
        host = urlparse(url).netloc
        await self._limiter.acquire(host)
        r = await self._post_raw(url, data, headers)
        log.debug("POST %s -> %s (%d bytes)", url, r.status_code, len(r.content))
        return r

    async def fetch(
        self, spec: ListUrlSpec, *, check_robots: bool = True
    ) -> httpx.Response:
        """Dispatch GET/POST based on a ListUrlSpec."""
        if spec.method == "POST":
            return await self.post(
                spec.url, data=spec.data, headers=spec.headers, check_robots=check_robots
            )
        # default GET (custom headers applied via per-request override)
        if spec.headers:
            host = urlparse(spec.url).netloc
            if check_robots and not can_fetch(spec.url, self._ua):
                log.warning("robots.txt disallows %s — skipping", spec.url)
                raise ForbiddenByRobots(spec.url)
            await self._limiter.acquire(host)
            r = await self._client.get(spec.url, headers=spec.headers)
            if r.status_code >= 500:
                r.raise_for_status()
            return r
        return await self.get(spec.url, check_robots=check_robots)
