"""HUST (华中科技大学) email backfill — per-school strategy.

HUST profile pages live on two hostnames with very different rendering:

* ``faculty.hust.edu.cn/<slug>/zh_CN/index.htm`` — the unified faculty
  system. Email is wrapped in ``<div class="blockwhite Ot-ctact">`` with
  encrypted hex blobs inside ``<span _tsites_encrypt_field>``. Decryption
  is performed by an inline ``ImageScale.addimg()`` call **after** the
  page loads. Lazy fill takes up to ~10s.
* ``<school>.hust.edu.cn/info/<treeid>/<id>.htm`` (cs / sse / aia legacy) —
  plaintext ``mailto:`` or ``邮箱：x@y`` inside ``div.v_news_content``.

Cascade:

  1. ``js`` — navigate the advisor's ``homepage`` / ``source_url`` and let
     :func:`claw.core.email_decoders.decode_hust_email` wait for the lazy
     decryption + read the DOM.
  2. ``js-alt`` — if the advisor only has a list-level link and the
     primary page never decoded, try a small set of alternate HUST
     sub-site URL patterns (``cs.hust.edu.cn`` / ``aia.hust.edu.cn`` /
     etc.) and re-decode there.
  3. ``bing`` — stealth web search with ``domain_hint="hust.edu.cn"``.
  4. ``dblp`` — DBLP author lookup with affiliation
     ``Huazhong University of Science and Technology``.

The exported coroutine signature mirrors the v0.5 plan template::

    async def find_email(advisor, page, sess, school_name_cn) -> (email, source)

Returns ``(None, None)`` when no strategy succeeds. ``source`` is one of
``js_decode`` / ``js_decode_alt`` / ``bing`` / ``dblp``.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from ...core.email_decoders import decode_hust_email
from ...core.logging import get_logger
from ..email_backfill import (
    dblp_email_lookup,
    search_email_via_stealth_bing,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from playwright.async_api import Page

    from ...models.db import Advisor

log = get_logger(__name__)


# Alternate HUST sub-domains that occasionally host the same advisor's
# profile under a different URL than ``advisor.homepage``. When the JS
# decoder fails on the primary URL we re-try these.
_HUST_ALT_HOSTS: tuple[str, ...] = (
    "faculty.hust.edu.cn",
    "cs.hust.edu.cn",
    "sse.hust.edu.cn",
    "aia.hust.edu.cn",
    "ee.hust.edu.cn",
)


def _candidate_alt_urls(primary_url: str | None) -> list[str]:
    """Generate alternate URLs to try when the primary JS decode misses.

    For a primary like ``https://faculty.hust.edu.cn/X/zh_CN/index.htm``
    we don't actually have enough info to construct a counterpart on
    ``cs.hust.edu.cn`` without name → slug lookup, so the alternates are
    limited to URL-shape variants of the same host (HTTP↔HTTPS, trailing
    ``index.htm`` ↔ ``zh_CN``). The list intentionally excludes the
    original to avoid double-fetching.
    """
    if not primary_url:
        return []
    out: list[str] = []
    parsed = urlparse(primary_url)
    if not parsed.hostname:
        return []
    # HTTP ↔ HTTPS swap.
    other_scheme = "http" if parsed.scheme == "https" else "https"
    alt = primary_url.replace(parsed.scheme + "://", other_scheme + "://", 1)
    if alt and alt != primary_url:
        out.append(alt)
    # If URL ends with index.htm, try the dir form (sometimes returns a
    # different template that surfaces a mailto we can scrape).
    if primary_url.endswith("/index.htm"):
        out.append(primary_url[: -len("index.htm")])
    elif primary_url.endswith("/"):
        out.append(primary_url + "index.htm")
    return out


async def _try_js_decode(page: "Page", url: str) -> Optional[str]:
    """Navigate ``page`` to ``url`` and run :func:`decode_hust_email`.

    Returns the decoded address or ``None`` on nav failure / decode miss.
    """
    if not url:
        return None
    try:
        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
    except Exception as e:  # noqa: BLE001
        log.warning("hust js: nav failed %s: %s", url, e)
        return None
    with contextlib.suppress(Exception):
        # Let any lazy ImageScale.addimg / TsitesEncrypt JS settle past
        # the strict DOMContentLoaded barrier.
        await page.wait_for_load_state("networkidle", timeout=10_000)
    try:
        email = await decode_hust_email(page)
    except Exception as e:  # noqa: BLE001
        log.warning("hust js: decoder raised on %s: %s", url, e)
        return None
    return email


async def find_email(
    advisor: "Advisor",
    page: "Page",
    sess: "httpx.AsyncClient",
    school_name_cn: str,
) -> tuple[Optional[str], Optional[str]]:
    """Run the HUST cascade until one strategy yields an email.

    Parameters
    ----------
    advisor
        ORM row. We read ``name_cn`` / ``homepage`` / ``source_url`` —
        never mutated here (writes happen via ``update_email_only``).
    page
        Stealth Playwright page owned by the caller. We reuse it across
        all strategies to avoid context-creation overhead.
    sess
        Shared ``httpx.AsyncClient`` for DBLP.
    school_name_cn
        Display name used as the bing query / DBLP affiliation hint.
    """
    # ------------------------------------------------------------------
    # Strategy 1: js decoder on the advisor's known profile URL.
    # ------------------------------------------------------------------
    primary = getattr(advisor, "homepage", None) or getattr(advisor, "source_url", None)
    if primary:
        email = await _try_js_decode(page, primary)
        if email:
            return email, "js_decode"

    # ------------------------------------------------------------------
    # Strategy 2: js decoder on URL-shape variants of the primary.
    # Cheap (1-2 extra page loads) and rescues profiles whose primary
    # URL serves a stub but whose dir form (or http/https alt) renders
    # the mailto.
    # ------------------------------------------------------------------
    for alt in _candidate_alt_urls(primary):
        email = await _try_js_decode(page, alt)
        if email:
            return email, "js_decode_alt"

    # ------------------------------------------------------------------
    # Strategy 3: stealth web search restricted to hust.edu.cn.
    # ------------------------------------------------------------------
    name = getattr(advisor, "name_cn", "") or ""
    if name:
        try:
            email = await search_email_via_stealth_bing(
                page,
                name=name,
                school_name_cn=school_name_cn,
                domain_hint="hust.edu.cn",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("hust bing failed for %s: %s", name, e)
            email = None
        if email:
            return email, "bing"

    # ------------------------------------------------------------------
    # Strategy 4: DBLP author lookup.
    # ------------------------------------------------------------------
    if name:
        try:
            email = await dblp_email_lookup(
                sess,
                name=name,
                affiliation_hint="Huazhong University of Science and Technology",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("hust dblp failed for %s: %s", name, e)
            email = None
        if email:
            return email, "dblp"

    return None, None


__all__ = ["find_email"]
