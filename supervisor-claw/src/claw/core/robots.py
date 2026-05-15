"""Per-host robots.txt cache. Conservative: on fetch error we *allow*
(school sites rarely block what we want; we still rate-limit aggressively)."""

from __future__ import annotations

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .logging import get_logger

log = get_logger(__name__)

_CACHE: dict[str, tuple[RobotFileParser, float]] = {}
_TTL = 24 * 3600


def _key(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _fetch(robots_url: str, user_agent: str) -> RobotFileParser:
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        r = httpx.get(robots_url, headers={"User-Agent": user_agent}, timeout=10.0)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        else:
            rp.parse([])  # missing robots = allow
    except Exception as e:
        log.warning("robots.txt fetch failed for %s: %s — defaulting to allow", robots_url, e)
        rp.parse([])
    return rp


def can_fetch(url: str, user_agent: str) -> bool:
    base = _key(url)
    now = time.time()
    cached = _CACHE.get(base)
    if cached is None or now - cached[1] > _TTL:
        rp = _fetch(f"{base}/robots.txt", user_agent)
        _CACHE[base] = (rp, now)
    else:
        rp = cached[0]
    return rp.can_fetch(user_agent, url)
