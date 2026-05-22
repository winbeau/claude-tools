"""Email-only backfill helpers for v0.5.

This module is the **infrastructure** layer for ``claw backfill-email``.
Each helper is small, async, and takes whatever shared resource it needs
(``Page`` / ``httpx.AsyncClient`` / ``Session``) so the CLI layer can hold
the Playwright + DB lifecycles centrally.

Strategies
----------
* ``js`` — load ``advisor.homepage`` (a.k.a. profile_url) in the stealth
  Playwright context and let any site-specific decryption JS populate the
  DOM, then extract the email. Dispatches on ``school_code`` to the right
  decoder in :mod:`claw.core.email_decoders`.
* ``bing`` — stealth-search ``"<name> <school> email"`` across Bing →
  cn.bing.com → DuckDuckGo → Sogou and regex-match the first SERP's snippet
  HTML. **Public Bing web only** — no paid SerpAPI / Bing Web Search API.
* ``dblp`` — query ``https://dblp.org/search/author/api`` with the advisor's
  name + affiliation, then fetch the matched author's ``.xml`` and regex
  any embedded email.

All hits are funnelled through :func:`update_email_only`, which is the
single point that writes ``advisor.email`` — and only when it is currently
``NULL``. Every write is mirrored to ``data/email_backfill_audit.jsonl``.

The browser is **never** opened by helpers in this module; callers (CLI)
own the stealth session and pass ``page`` and ``sess`` in.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import quote_plus

from ..core.email_decoders import (
    decode_hust_email,
    decode_xidian_email,
    extract_email_from_rendered_dom,
)
from ..core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from playwright.async_api import Page
    from sqlmodel import Session

    from ..models.db import Advisor

log = get_logger(__name__)

# Same regex as core.email_decoders — duplicated here so we don't have a
# cross-module dependency just for the pattern.
_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"
)

# Local-parts that look like contact forms / footers (mirrors core.email_decoders).
_FOOTER_LOCAL_PARTS: tuple[str, ...] = (
    "webmaster", "admin", "service", "office", "info", "contact",
    "support", "noreply", "no-reply", "postmaster",
    "sse", "scs", "aia-president",
)

# Bing / DuckDuckGo / Sogou search URL templates. We hit the public web
# pages — *not* any paid API.
_SEARCH_ENGINES: list[tuple[str, str]] = [
    ("bing.com",       "https://www.bing.com/search?q={q}"),
    ("cn.bing.com",    "https://cn.bing.com/search?q={q}&ensearch=0"),
    ("duckduckgo.com", "https://duckduckgo.com/?q={q}"),
    ("sogou.com",      "https://www.sogou.com/web?query={q}"),
]

# Per-school decoder dispatch table. School codes match those in schools.yaml.
_DECODER_DISPATCH = {
    "xidian": decode_xidian_email,
    "hust":   decode_hust_email,
}

# Per-school *full-orchestrator* override. When a school code is in this
# table, ``backfill_one_advisor`` delegates to the site module's
# ``find_email`` instead of running the generic js → bing → dblp cascade.
#
# Use this for schools where the default ordering is wrong, where a
# strategy is permanently broken (e.g. nwpu's TS-WAF wall makes ``js``
# useless), or where a site-specific recovery (wayback, IR DOI lookup,
# group-page fan-out) needs to slot in.
#
# Resolved lazily via importlib so we don't take an import-time hit for
# 19 schools that don't need any of this. The lookup is keyed on
# ``school_code`` and returns the module-level ``find_email`` coroutine,
# or ``None`` if no override exists.
_SITE_EMAIL_OVERRIDES: tuple[str, ...] = (
    "nwpu",
)

# Default audit log path.
_DEFAULT_AUDIT_PATH = "data/email_backfill_audit.jsonl"


# ---------------------------------------------------------------------------
# Small utility helpers
# ---------------------------------------------------------------------------


def _is_footer_like(addr: str) -> bool:
    return addr.split("@", 1)[0].lower() in _FOOTER_LOCAL_PARTS


def _pick_best(candidates: list[str], domain_hint: str | None) -> Optional[str]:
    """Same scoring rules as :mod:`core.email_decoders._pick_email`.

    1. domain_hint match
    2. ``.edu.cn`` / ``.ac.cn`` / ``.edu``
    3. first non-footer candidate
    """
    seen: set[str] = set()
    clean: list[str] = []
    for c in candidates:
        a = c.strip().lower()
        if not a or a in seen or _is_footer_like(a):
            continue
        seen.add(a)
        clean.append(a)
    if not clean:
        return None
    if domain_hint:
        hint = domain_hint.lower()
        for a in clean:
            if hint in a:
                return a
    for a in clean:
        host = a.split("@", 1)[1]
        if host.endswith(".edu.cn") or host.endswith(".ac.cn") or host.endswith(".edu"):
            return a
    return clean[0]


# ---------------------------------------------------------------------------
# 1. JS-decoded email entry point
# ---------------------------------------------------------------------------


async def decode_js_email_on_page(
    page: "Page",
    school_code: str,
) -> Optional[str]:
    """Run the school-specific decoder against ``page`` (already navigated).

    For schools without a registered decoder, falls back to the generic
    rendered-DOM extractor (which works for any plain-text emails surfaced
    after JS has run).
    """
    decoder = _DECODER_DISPATCH.get(school_code)
    if decoder is not None:
        try:
            email = await decoder(page)
            if email:
                return email.lower()
        except Exception as e:  # noqa: BLE001
            log.warning("decoder for %s raised %s", school_code, e)

    # Generic fallback
    domain_hint = _guess_school_domain(school_code)
    try:
        email = await extract_email_from_rendered_dom(page, domain_hint=domain_hint)
    except Exception as e:  # noqa: BLE001
        log.warning("generic dom-extract failed for %s: %s", school_code, e)
        return None
    return email


def _guess_school_domain(school_code: str) -> str | None:
    """Best-effort school domain hint (used for picking the right candidate).

    Schools follow the ``<code>.edu.cn`` convention with a few exceptions.
    """
    overrides = {
        "tsinghua": "tsinghua.edu.cn",
        "shtech": "shanghaitech.edu.cn",
        "uestc": "uestc.edu.cn",
        "nudt": "nudt.edu.cn",
    }
    if school_code in overrides:
        return overrides[school_code]
    # generic guess
    return f"{school_code}.edu.cn"


# ---------------------------------------------------------------------------
# 2. Stealth Bing / DuckDuckGo / Sogou search
# ---------------------------------------------------------------------------


async def search_email_via_stealth_bing(
    page: "Page",
    name: str,
    school_name_cn: str,
    domain_hint: str | None = None,
) -> Optional[str]:
    """Search ``<name> <school> email`` across public web search engines.

    Cascade: bing.com → cn.bing.com → DuckDuckGo → Sogou. For each engine we
    navigate via the supplied stealth ``page`` and regex-match any email in
    the rendered HTML. If ``domain_hint`` is set, that domain is preferred.

    No paid APIs are touched. Returns ``None`` if nothing usable is found.
    """
    query_parts: list[str] = [name, school_name_cn, "email"]
    if domain_hint:
        # Adding the domain as a bare token (not site:) keeps the search
        # robust against engines that ignore site:-operators.
        query_parts.append(domain_hint)
    query = " ".join(query_parts)
    q_encoded = quote_plus(query)

    for engine, tmpl in _SEARCH_ENGINES:
        url = tmpl.format(q=q_encoded)
        try:
            await page.goto(url, timeout=20_000, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            log.warning("search nav failed for %s (%s): %s", engine, url, e)
            continue
        # Some engines load results lazily — small settle.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)
        await asyncio.sleep(1.0)

        html = ""
        with contextlib.suppress(Exception):
            html = await page.content()
        if not html:
            continue

        candidates = _EMAIL_RE.findall(html)
        # Reject the engine's own contact addresses
        candidates = [c for c in candidates if engine.split(".")[0] not in c.lower()]
        picked = _pick_best(candidates, domain_hint=domain_hint)
        if picked:
            log.info("search hit via %s for %s: %s", engine, name, picked)
            return picked

        # Politeness: sleep a bit between engines to avoid hammering.
        await asyncio.sleep(1.5)

    return None


# ---------------------------------------------------------------------------
# 3. DBLP lookup
# ---------------------------------------------------------------------------


async def dblp_email_lookup(
    sess: "httpx.AsyncClient",
    name: str,
    affiliation_hint: str,
) -> Optional[str]:
    """Best-effort DBLP author → email lookup.

    1. Query ``/search/author/api?q=<name> <aff>&format=json`` for up to 5
       authors.
    2. For each hit, GET its ``.xml`` page and regex-match an email.

    Notes
    -----
    DBLP rarely surfaces personal emails directly, but author pages often
    link to a homepage that does. We **only** regex within the XML payload
    here; pulling the linked homepage is best left to the bing/stealth
    pipeline because it can be on any random WAF-blocked host.
    """
    q = f"{name} {affiliation_hint}".strip()
    api = f"https://dblp.org/search/author/api?q={quote_plus(q)}&format=json&h=5"
    try:
        r = await sess.get(api, timeout=15.0)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("dblp api failed for %s: %s", name, e)
        return None

    hits = payload.get("result", {}).get("hits", {}).get("hit", [])
    if not isinstance(hits, list):
        return None

    domain_hint = None
    aff_lower = affiliation_hint.lower()
    # Map common Chinese school names to their domain so the picker can
    # prefer the right candidate. Keep this short — DBLP rarely returns
    # an email anyway, the hint just biases the regex picker.
    if "西安电子" in aff_lower or "xidian" in aff_lower:
        domain_hint = "xidian.edu.cn"
    elif "华中科技" in aff_lower or "hust" in aff_lower:
        domain_hint = "hust.edu.cn"
    elif "xjtu" in aff_lower or "西安交通" in aff_lower:
        domain_hint = "xjtu.edu.cn"

    candidates: list[str] = []
    for h in hits:
        info = h.get("info", {}) if isinstance(h, dict) else {}
        url = info.get("url")
        if not url:
            continue
        xml_url = url if url.endswith(".xml") else url.rstrip("/") + ".xml"
        try:
            xr = await sess.get(xml_url, timeout=15.0)
            xr.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.debug("dblp xml fetch failed for %s: %s", xml_url, e)
            continue
        candidates.extend(_EMAIL_RE.findall(xr.text))

    return _pick_best(candidates, domain_hint=domain_hint)


# ---------------------------------------------------------------------------
# 4. Surgical email-only upsert + audit
# ---------------------------------------------------------------------------


def update_email_only(
    session: "Session",
    advisor_id: int,
    new_email: str,
    source: str,
    audit_path: str = _DEFAULT_AUDIT_PATH,
) -> bool:
    """Set ``advisor.email = new_email`` **only if currently NULL**.

    Returns ``True`` when a write actually happens. Appends a JSON line to
    ``audit_path`` on each successful write::

        {"ts","advisor_id","school","name","old_email":null,"new_email","source","confidence"}

    Never overwrites an existing email. Confidence is a fixed 0.9 — this
    helper does not score sources; callers can switch on ``source`` if they
    need a finer grade later.
    """
    from ..models.db import Advisor, School  # local import: avoid heavy import at module load

    if not new_email or "@" not in new_email:
        return False
    new_email = new_email.strip().lower()

    advisor = session.get(Advisor, advisor_id)
    if advisor is None:
        log.warning("update_email_only: advisor %s not found", advisor_id)
        return False
    if advisor.email:
        # idempotent — don't touch existing values.
        return False

    school = session.get(School, advisor.school_id)
    school_code = school.code if school else "?"

    advisor.email = new_email
    advisor.email_obfuscated = False
    session.add(advisor)
    try:
        session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("update_email_only commit failed for advisor %s: %s", advisor_id, e)
        session.rollback()
        return False

    # Audit log (best-effort — log file is not in the critical path).
    audit_record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "advisor_id": advisor_id,
        "school": school_code,
        "name": advisor.name_cn,
        "old_email": None,
        "new_email": new_email,
        "source": source,
        "confidence": 0.9,
    }
    try:
        p = Path(audit_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit_record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("audit write failed for advisor %s: %s", advisor_id, e)

    return True


# ---------------------------------------------------------------------------
# 5. Per-advisor orchestrator
# ---------------------------------------------------------------------------


_DEFAULT_STRATEGIES: tuple[str, ...] = ("js", "bing", "dblp")


def _resolve_site_override(school_code: str):
    """Lazy-import ``claw.enrichers.sites.<code>_email.find_email``.

    Returns the ``find_email`` coroutine function or ``None`` if no site
    module is registered for this school code. Import errors are logged
    and silently downgraded to ``None`` so a broken site module never
    crashes the whole backfill run.
    """
    if school_code not in _SITE_EMAIL_OVERRIDES:
        return None
    import importlib

    mod_name = f"claw.enrichers.sites.{school_code}_email"
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001
        log.warning("site override %s import failed: %s", mod_name, e)
        return None
    fn = getattr(mod, "find_email", None)
    if fn is None:
        log.warning("site module %s has no find_email()", mod_name)
        return None
    return fn


async def backfill_one_advisor(
    advisor: "Advisor",
    page: "Page",
    sess: "httpx.AsyncClient",
    school_code: str,
    school_name_cn: str,
    strategies: list[str] | None = None,
) -> tuple[Optional[str], Optional[str]]:
    """Run strategies in order until one yields an email.

    Returns ``(email, source)``. Both ``None`` means no strategy succeeded.

    Strategy semantics
    ------------------
    * ``js``:   navigate ``advisor.homepage`` in the stealth ``page`` and call
                :func:`decode_js_email_on_page`. Skipped if ``homepage`` is
                falsy.
    * ``bing``: :func:`search_email_via_stealth_bing` with ``school_name_cn``
                and the school's guessed domain.
    * ``dblp``: :func:`dblp_email_lookup` with ``school_name_cn`` as the
                affiliation hint.

    Site overrides
    --------------
    If ``school_code`` has a registered site module (see
    ``_SITE_EMAIL_OVERRIDES``), that module's ``find_email`` is called
    instead of the generic cascade. The site module is free to ignore /
    reorder / extend the strategy list as it sees fit.
    """
    site_fn = _resolve_site_override(school_code)
    if site_fn is not None:
        try:
            email, source = await site_fn(
                advisor,
                page,
                sess,
                school_name_cn,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "site override %s_email.find_email raised %s; falling back to generic",
                school_code, e,
            )
        else:
            if email:
                return email, source
            # site override returned (None, None) → still fall through to
            # the generic cascade below, so callers don't lose recall when
            # a site module conservatively refuses to guess.

    order = list(strategies) if strategies else list(_DEFAULT_STRATEGIES)
    domain_hint = _guess_school_domain(school_code)

    for strat in order:
        if strat == "js":
            url = getattr(advisor, "homepage", None) or getattr(advisor, "source_url", None)
            if not url:
                continue
            try:
                await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                log.warning("js: nav failed %s: %s", url, e)
                continue
            email = await decode_js_email_on_page(page, school_code)
            if email:
                return email, "js_decode"

        elif strat == "bing":
            email = await search_email_via_stealth_bing(
                page,
                name=advisor.name_cn,
                school_name_cn=school_name_cn,
                domain_hint=domain_hint,
            )
            if email:
                return email, "bing"

        elif strat == "dblp":
            email = await dblp_email_lookup(
                sess,
                name=advisor.name_cn,
                affiliation_hint=school_name_cn,
            )
            if email:
                return email, "dblp"

        else:
            log.warning("unknown strategy %r — skipping", strat)

    return None, None


__all__ = [
    "backfill_one_advisor",
    "dblp_email_lookup",
    "decode_js_email_on_page",
    "search_email_via_stealth_bing",
    "update_email_only",
]
