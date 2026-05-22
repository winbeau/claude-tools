"""NWPU (西北工业大学) site-specific email backfill.

Why this exists
---------------
``teacher.nwpu.edu.cn`` is behind a TS-WAF JS challenge that the
:mod:`claw.pipeline.stealth_crawler` consistently fails to crack — even
warm-up + playwright-stealth lands on the WAF stub (``<meta id="hK5iNqnNcwxO"
...>``). As a result the generic ``js`` strategy in
:mod:`claw.enrichers.email_backfill` can never reach a real profile DOM on
which to run a decoder. NWPU also embeds **no** emails in any of the
sub-domain list pages (jsj / ruanjian / wlkjaqxy), so we have nothing local
to decode either.

Strategy (in order)
-------------------
1. **Wayback** — fetch the most recent ``web.archive.org`` snapshot of the
   advisor's profile URL via :func:`claw.pipeline.stealth_crawler.wayback_latest`
   and ``httpx``. Wayback isn't WAF-blocked and ~10-20% of teacher pages have
   archived copies that pre-date TS-WAF, with plain-text email visible.
2. **Stealth Bing** — :func:`claw.enrichers.email_backfill.search_email_via_stealth_bing`
   with ``domain_hint="nwpu.edu.cn"`` and ``school_name_cn="西北工业大学"``.
   Engine cascade falls through to cn.bing / DuckDuckGo / Sogou.
3. **DBLP** — :func:`claw.enrichers.email_backfill.dblp_email_lookup` with
   affiliation hint "Northwestern Polytechnical University". DBLP rarely
   publishes raw emails but is cheap and occasionally hits homepage redirects.

Returns ``(email, source)`` where ``source`` is one of:
``"wayback"`` / ``"bing"`` / ``"dblp"``. Returns ``(None, None)`` when every
tier misses. Never raises; all I/O errors are downgraded to log warnings
and a tier-skip.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Optional

from ...core.logging import get_logger
from ..email_backfill import (
    _EMAIL_RE,
    _pick_best,
    dblp_email_lookup,
    search_email_via_stealth_bing,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from playwright.async_api import Page

    from ...models.db import Advisor

log = get_logger(__name__)

# School-wide domain hint — every NWPU faculty email we have ever seen
# lives at one of these hosts. ``mail.nwpu.edu.cn`` is the staff webmail
# host but the visible address is always ``...@nwpu.edu.cn``.
_NWPU_DOMAIN_HINT = "nwpu.edu.cn"
_NWPU_SCHOOL_NAME_CN = "西北工业大学"
_NWPU_AFFILIATION_HINT = "Northwestern Polytechnical University"

# Wayback availability + raw-fetch templates. The raw-fetch URL uses the
# ``id_`` modifier so we get the archived page body, not the wayback
# toolbar/iframe wrapper.
_WAYBACK_AVAILABLE = "https://archive.org/wayback/available?url={url}"


async def _wayback_latest(url: str, sess: httpx.AsyncClient) -> Optional[str]:
    """Resolve the most recent wayback snapshot URL for ``url``.

    Mirrors :func:`claw.pipeline.stealth_crawler.wayback_latest` but reuses
    the caller-supplied ``httpx.AsyncClient`` so we don't open a new TLS
    context per advisor.
    """
    api = _WAYBACK_AVAILABLE.format(url=url)
    try:
        r = await sess.get(api, timeout=15.0)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("nwpu/wayback: availability lookup failed for %s: %s", url, e)
        return None
    snap = payload.get("archived_snapshots", {}).get("closest", {}) or {}
    if not snap.get("available"):
        return None
    snap_url = snap.get("url")
    if not snap_url:
        return None
    # /web/<TS>/<URL> → /web/<TS>id_/<URL>  (raw page mode)
    return re.sub(r"(web/\d+)/", r"\1id_/", snap_url, count=1)


async def _wayback_fetch_html(url: str, sess: httpx.AsyncClient) -> str:
    """Best-effort GET of a wayback snapshot. Returns HTML or "" on miss."""
    snap_url = await _wayback_latest(url, sess)
    if not snap_url:
        return ""
    try:
        r = await sess.get(snap_url, timeout=20.0)
        r.raise_for_status()
        return r.text or ""
    except Exception as e:  # noqa: BLE001
        log.warning("nwpu/wayback: fetch failed for %s: %s", snap_url, e)
        return ""


async def _try_wayback_email(advisor: "Advisor", sess: httpx.AsyncClient) -> Optional[str]:
    """Try to pull an email out of the wayback snapshot for the advisor's
    profile URL. Returns the email string or ``None``.
    """
    url = getattr(advisor, "homepage", None) or getattr(advisor, "source_url", None)
    if not url:
        return None
    html = await _wayback_fetch_html(url, sess)
    if not html:
        return None
    candidates = _EMAIL_RE.findall(html)
    if not candidates:
        return None
    return _pick_best(candidates, domain_hint=_NWPU_DOMAIN_HINT)


async def find_email(
    advisor: "Advisor",
    page: "Page",
    sess: httpx.AsyncClient,
    school_name_cn: str = _NWPU_SCHOOL_NAME_CN,
) -> tuple[Optional[str], Optional[str]]:
    """NWPU-specific email backfill.

    Order: wayback → stealth bing → dblp. The ``js`` strategy is **not**
    run — TS-WAF stubs every teacher.nwpu.edu.cn page even via stealth
    chromium (verified on 230+ advisors during v0.4 crawl), so loading
    ``advisor.homepage`` for a JS-decoder pass is wasted I/O.

    All I/O failures are non-fatal; we log + fall through to the next tier.
    """
    name = getattr(advisor, "name_cn", None) or ""
    log.debug("nwpu_email: starting backfill for %s", name)

    # Tier 1 — wayback snapshot of the profile URL
    with contextlib.suppress(Exception):
        email = await _try_wayback_email(advisor, sess)
        if email:
            log.info("nwpu_email[wayback] %s → %s", name, email)
            return email, "wayback"

    # Tier 2 — stealth Bing / cn.bing / DuckDuckGo / Sogou cascade
    with contextlib.suppress(Exception):
        email = await search_email_via_stealth_bing(
            page,
            name=name,
            school_name_cn=school_name_cn or _NWPU_SCHOOL_NAME_CN,
            domain_hint=_NWPU_DOMAIN_HINT,
        )
        if email:
            log.info("nwpu_email[bing] %s → %s", name, email)
            return email, "bing"

    # Tier 3 — DBLP author API
    with contextlib.suppress(Exception):
        email = await dblp_email_lookup(
            sess,
            name=name,
            affiliation_hint=_NWPU_AFFILIATION_HINT,
        )
        if email:
            log.info("nwpu_email[dblp] %s → %s", name, email)
            return email, "dblp"

    log.debug("nwpu_email: no email recovered for %s", name)
    return None, None


__all__ = ["find_email"]
