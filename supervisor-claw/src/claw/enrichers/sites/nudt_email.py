"""NUDT (国防科技大学) email backfill — DBLP + stealth Bing only.

Rationale
---------
NUDT is the PLA's flagship military university (军校 / 国防七子). Its
public web presence is *deliberately minimal* (see the v0.4 adapter
report ``docs/reports/nudt_report.md``):

* 83 advisors are surfaced from honour-roster pages only (院士 / 杰青 /
  优青 / 百千万 / 求是 / 全国优秀科技工作者).
* Honour pages list **names only** — no profile_url, no email, no
  photo. ``advisor.homepage`` is therefore ``None`` for every NUDT row.
* Per-college sub-domains (``jsjkx.nudt.edu.cn`` etc.) either 404 or are
  intranet-only — wayback snapshots are equally bare.

Consequence: the standard ``js`` strategy is useless (there's no
profile to render) and even ``wayback`` won't help (no profile ever
existed publicly). Only two paths can possibly recover an email:

1. **DBLP** — many senior NUDT CS researchers publish internationally
   and have DBLP author pages with an affiliation matching "National
   University of Defense Technology" or "nudt". DBLP rarely embeds the
   email directly, but when it does (older snapshots, ``<note
   type='affiliation'>...email...</note>``) we can grab it.
2. **Stealth Bing search** — looking for ``<name> 国防科技大学 email
   nudt.edu.cn`` may surface public news articles, conference rosters,
   or third-party academic listings that disclosed an ``@nudt.edu.cn``
   address.

Expected recovery rate
----------------------
**< 10 %** — most NUDT advisors will *never* be publicly reachable by
email. This is explicitly called out in the limitations section of
``docs/reports/email_backfill_nudt.md``.

API
---
The module exposes a single async entry point compatible with the
``claw.enrichers.sites`` contract::

    async def find_email(advisor, page, sess, school_name_cn)
        -> tuple[str | None, str | None]

It returns ``(email, source)``. Both ``None`` means every strategy
missed (the expected common case for NUDT).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

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

# NUDT's central domain. Used as the picker's domain_hint so candidate
# addresses matching ``@nudt.edu.cn`` outrank generic webmail ones.
_NUDT_DOMAIN = "nudt.edu.cn"

# Affiliation hints passed to dblp's author search. We submit both the
# English and the bare ``nudt`` token because DBLP author records vary
# widely in affiliation phrasing across years.
_DBLP_AFFILIATIONS: tuple[str, ...] = (
    "National University of Defense Technology",
    "nudt",
)


async def _try_dblp(
    sess: "httpx.AsyncClient",
    name: str,
) -> Optional[str]:
    """Run :func:`dblp_email_lookup` against each affiliation hint.

    Returns the first non-empty hit, or ``None`` if every variant misses.
    The base helper already prefers candidates whose domain looks
    Chinese-academic — we further filter to ``nudt.edu.cn`` here.
    """
    for aff in _DBLP_AFFILIATIONS:
        try:
            email = await dblp_email_lookup(sess, name=name, affiliation_hint=aff)
        except Exception as e:  # noqa: BLE001 - never blow up the pipeline
            log.warning("nudt dblp lookup %r raised %s — skipping", aff, e)
            continue
        if not email:
            continue
        # Sanity guard: prefer an explicit @nudt.edu.cn match. If the
        # dblp candidate doesn't end in nudt.edu.cn we still accept it
        # (some NUDT folks use gmail / 163 for academic correspondence
        # before joining), but we log so audits can spot the source.
        if _NUDT_DOMAIN not in email:
            log.info(
                "nudt dblp non-nudt domain hit for %s: %s (accepted, low-confidence)",
                name,
                email,
            )
        return email
    return None


async def _try_stealth_bing(
    page: "Page",
    name: str,
    school_name_cn: str,
) -> Optional[str]:
    """Run :func:`search_email_via_stealth_bing` with the nudt domain hint.

    The base helper cascades bing.com → cn.bing.com → DuckDuckGo →
    Sogou and regex-picks the best-looking address in each SERP's HTML.
    For NUDT we pass ``domain_hint=nudt.edu.cn`` so the picker prefers
    those over any random webmail addresses that happen to appear in
    co-occurring news snippets.
    """
    try:
        return await search_email_via_stealth_bing(
            page,
            name=name,
            school_name_cn=school_name_cn,
            domain_hint=_NUDT_DOMAIN,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("nudt bing search for %s raised %s", name, e)
        return None


async def find_email(
    advisor: "Advisor",
    page: "Page",
    sess: "httpx.AsyncClient",
    school_name_cn: str,
) -> tuple[Optional[str], Optional[str]]:
    """Try to recover an email for ``advisor`` using DBLP → Bing.

    No JS decoder, no wayback — see the module docstring for why.

    Returns ``(email, source)`` where ``source`` is ``"dblp"`` or
    ``"bing"`` on success, or ``(None, None)`` if every strategy missed.
    The orchestrator will log the miss as ``"unfindable"`` in the audit
    trail.
    """
    name = getattr(advisor, "name_cn", None) or getattr(advisor, "name_en", None)
    if not name:
        return None, None

    # Strategy 1: DBLP — cheap (single HTTP API call per affiliation),
    # high precision when it hits. Try first to avoid spinning up Bing
    # for advisors with a clean DBLP record.
    email = await _try_dblp(sess, name=name)
    if email:
        return email, "dblp"

    # Strategy 2: stealth Bing cascade. Pages render in the shared
    # Playwright context the caller already owns.
    email = await _try_stealth_bing(page, name=name, school_name_cn=school_name_cn)
    if email:
        return email, "bing"

    return None, None


__all__ = ["find_email"]
