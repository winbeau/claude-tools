"""XJTU (西安交通大学) site-specific email backfill.

Why a dedicated module
----------------------
Per the v0.5 backfill plan (``docs/plans/email_backfill_5x4.md``) and the
audit in ``docs/reports/email_backfill_xjtu.md``, XJTU has a 70 % gap that
needs different tactics than the generic ``js + bing + dblp`` cascade:

* **JS retry rarely helps** — most missing profiles are either *list-only*
  rows (no profile URL at all) or sit on ``cs.xjtu.edu.cn`` subpages that
  were already proven WAF-blocked during the v0.4 crawl. Re-fetching is
  expensive and overwhelmingly returns the same stub.
* **DBLP is unusually high yield** — XJTU CS / AI faculty publish heavily
  on ICML / CVPR / NeurIPS, so a large fraction have a populated DBLP
  author page whose ``.xml`` carries the homepage URL (and occasionally
  an email).
* **Wayback snapshots sometimes carry plaintext emails** — the live
  ``cs.xjtu.edu.cn/info/...`` page may be JS-WAF'd today but a 2022/2023
  snapshot of the same URL often has the email in plain HTML.

Priority order in :func:`find_email`
------------------------------------
1. **dblp** — cheap, no browser navigation. Use the school's CN/EN name
   as affiliation hint; pick the candidate whose host matches
   ``xjtu.edu.cn``.
2. **wayback** — only when the advisor has a ``profile_url`` that points
   at ``cs.xjtu.edu.cn`` / ``se.xjtu.edu.cn`` / ``aiar.xjtu.edu.cn`` /
   ``faculty.xjtu.edu.cn`` / ``gr.xjtu.edu.cn``. Pull the latest snapshot
   and regex it.
3. **stealth bing** — search ``"<name> 西安交通大学 email"`` with the
   ``xjtu.edu.cn`` domain hint; scrape the SERP HTML for any
   ``@xjtu.edu.cn`` (or sibling) address.
4. **profile re-fetch** — only attempted when the ``profile_url`` is on a
   subdomain that didn't already 100 % WAF-block during v0.4 (i.e. the
   unified faculty portal). For ``cs.xjtu`` we skip this — it's a waste
   of stealth budget per the plan.

Each strategy is a separate async helper so the orchestrator can compose
or reorder them via the ``--strategy`` CLI flag. The exported
:func:`find_email` runs them in the order above and short-circuits.

Return contract
---------------
``(email, source)`` where ``email`` is a lower-cased ASCII string (or
``None``) and ``source`` is one of ``"dblp"`` / ``"wayback"`` /
``"bing"`` / ``"xjtu_profile"`` / ``None``.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from ...core.email_decoders import (
    _is_xjtu_footer,
    extract_email_from_rendered_dom,
)
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

# Domain hint used everywhere in this module. xjtu's senior staff also have
# accounts on ``stu.xjtu.edu.cn`` (PhD/Master) and ``mail.xjtu.edu.cn`` —
# both end with ``xjtu.edu.cn`` so the substring test still matches.
_XJTU_DOMAIN_HINT = "xjtu.edu.cn"

# XJTU subdomains we expect to see in ``advisor.homepage`` /
# ``advisor.source_url``. Used to gate the wayback / profile-refetch path:
# we don't want to wayback-fetch a random external homepage.
_XJTU_HOSTS = (
    "cs.xjtu.edu.cn",
    "se.xjtu.edu.cn",
    "aiar.xjtu.edu.cn",
    "ai.xjtu.edu.cn",
    "iair.xjtu.edu.cn",
    "faculty.xjtu.edu.cn",
    "gr.xjtu.edu.cn",
    "eit.xjtu.edu.cn",
)

# Subdomains for which we still try a fresh stealth fetch as strategy 4.
# ``cs.xjtu.edu.cn`` is deliberately excluded — v0.4 already proved its
# WAF is too sticky to be worth the retry budget for backfill.
_XJTU_RETRY_HOSTS = (
    "faculty.xjtu.edu.cn",
    "gr.xjtu.edu.cn",
)


def _filter_xjtu_candidates(candidates: list[str]) -> list[str]:
    """Drop XJTU footer addresses (cs-office / ai-info / etc.) before
    handing the candidate list to ``_pick_best``."""
    return [c for c in candidates if not _is_xjtu_footer(c)]


def _profile_url_host(advisor: "Advisor") -> str:
    """Return the lower-cased hostname of the advisor's profile URL, or """ ""
    url = getattr(advisor, "homepage", None) or getattr(advisor, "source_url", None)
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Strategy 1 — DBLP
# ---------------------------------------------------------------------------


async def _try_dblp(
    advisor: "Advisor",
    sess: "httpx.AsyncClient",
    school_name_cn: str,
) -> Optional[str]:
    """Look up the advisor on DBLP using both Chinese and English affiliation
    hints. Returns the first ``@xjtu.edu.cn`` candidate, or ``None``.

    XJTU CS / AI faculty have unusually high DBLP coverage; we try the CN
    name first (matches the dblp_email_lookup branch that maps
    ``西安交通`` → ``xjtu.edu.cn``) then fall back to the English form.
    """
    for aff in (school_name_cn, "Xi'an Jiaotong University", "xjtu"):
        email = await dblp_email_lookup(sess, name=advisor.name_cn, affiliation_hint=aff)
        if email and _XJTU_DOMAIN_HINT in email and not _is_xjtu_footer(email):
            return email
    return None


# ---------------------------------------------------------------------------
# Strategy 2 — Wayback Machine
# ---------------------------------------------------------------------------


async def _try_wayback(
    advisor: "Advisor",
    page: "Page",
    sess_stealth=None,
) -> Optional[str]:
    """Pull the most-recent wayback snapshot of the advisor's profile URL
    and regex it for an email. Only fires when the URL points at an XJTU
    host — for non-XJTU homepages we skip (the snapshot is unlikely to
    carry the school email).

    Two import paths:
    * If a ``sess_stealth`` (``_StealthSession``) is supplied we reuse the
      pool's ``wayback_fetch_html`` (best — preserves stealth context).
    * Otherwise we lazy-import + call it ourselves; the helper itself
      handles ``httpx`` for the availability API.
    """
    url = getattr(advisor, "homepage", None) or getattr(advisor, "source_url", None)
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if not any(h in host for h in _XJTU_HOSTS):
        return None

    # Lazy import to keep this module importable without Playwright.
    try:
        from ...pipeline.stealth_crawler import (
            open_stealth_session,
            wayback_fetch_html,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("wayback path unavailable: %s", e)
        return None

    html = ""
    try:
        if sess_stealth is not None:
            html = await wayback_fetch_html(sess_stealth, url)
        else:
            async with open_stealth_session(headed=False) as s:
                html = await wayback_fetch_html(s, url)
    except Exception as e:  # noqa: BLE001
        log.warning("wayback fetch raised for %s: %s", url, e)
        return None

    if not html:
        return None

    candidates = _EMAIL_RE.findall(html)
    candidates = _filter_xjtu_candidates(candidates)
    return _pick_best(candidates, domain_hint=_XJTU_DOMAIN_HINT)


# ---------------------------------------------------------------------------
# Strategy 3 — Stealth Bing
# ---------------------------------------------------------------------------


async def _try_bing(
    advisor: "Advisor",
    page: "Page",
    school_name_cn: str,
) -> Optional[str]:
    """Wrap :func:`search_email_via_stealth_bing` with the XJTU domain hint
    and an extra footer-filter so we never write a school office email
    into an advisor row."""
    email = await search_email_via_stealth_bing(
        page,
        name=advisor.name_cn,
        school_name_cn=school_name_cn,
        domain_hint=_XJTU_DOMAIN_HINT,
    )
    if email and _is_xjtu_footer(email):
        log.info("bing: rejected xjtu-footer email %s for %s", email, advisor.name_cn)
        return None
    return email


# ---------------------------------------------------------------------------
# Strategy 4 — fresh stealth profile re-fetch (faculty.xjtu / gr.xjtu only)
# ---------------------------------------------------------------------------


async def _try_profile_refetch(
    advisor: "Advisor",
    page: "Page",
) -> Optional[str]:
    """Last-resort: re-navigate to the advisor's profile URL via the
    stealth ``page`` and run the generic DOM regex extractor.

    Gated to ``faculty.xjtu.edu.cn`` / ``gr.xjtu.edu.cn`` because those are
    the only XJTU subdomains where v0.4 occasionally succeeded — re-trying
    ``cs.xjtu.edu.cn`` is wasted budget per the plan.
    """
    url = getattr(advisor, "homepage", None) or getattr(advisor, "source_url", None)
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if not any(h in host for h in _XJTU_RETRY_HOSTS):
        return None

    try:
        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
    except Exception as e:  # noqa: BLE001
        log.warning("xjtu profile refetch nav failed for %s: %s", url, e)
        return None

    email = None
    with contextlib.suppress(Exception):
        email = await extract_email_from_rendered_dom(
            page, domain_hint=_XJTU_DOMAIN_HINT
        )
    if email and _is_xjtu_footer(email):
        return None
    return email


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def find_email(
    advisor: "Advisor",
    page: "Page",
    sess: "httpx.AsyncClient",
    school_name_cn: str,
) -> tuple[Optional[str], Optional[str]]:
    """Run XJTU's site-specific email backfill cascade.

    Order: ``dblp`` → ``wayback`` → ``bing`` → ``xjtu_profile``. The
    orchestrator that calls this is expected to have already verified
    ``advisor.email is None``; we just hunt for a candidate and return.

    Returns ``(email, source)``. Either both ``None`` (no candidate found
    that survives the footer / non-xjtu filters) or both populated.
    """
    name = advisor.name_cn
    log.info("xjtu_email.find_email start: %s (host=%s)", name, _profile_url_host(advisor))

    # 1. dblp
    try:
        email = await _try_dblp(advisor, sess, school_name_cn)
    except Exception as e:  # noqa: BLE001
        log.warning("xjtu dblp strategy raised for %s: %s", name, e)
        email = None
    if email:
        return email.lower(), "dblp"

    # 2. wayback
    try:
        email = await _try_wayback(advisor, page)
    except Exception as e:  # noqa: BLE001
        log.warning("xjtu wayback strategy raised for %s: %s", name, e)
        email = None
    if email:
        return email.lower(), "wayback"

    # 3. bing
    try:
        email = await _try_bing(advisor, page, school_name_cn)
    except Exception as e:  # noqa: BLE001
        log.warning("xjtu bing strategy raised for %s: %s", name, e)
        email = None
    if email:
        return email.lower(), "bing"

    # 4. fresh profile refetch (faculty.xjtu / gr.xjtu only)
    try:
        email = await _try_profile_refetch(advisor, page)
    except Exception as e:  # noqa: BLE001
        log.warning("xjtu profile-refetch strategy raised for %s: %s", name, e)
        email = None
    if email:
        return email.lower(), "xjtu_profile"

    return None, None


__all__ = ["find_email"]
