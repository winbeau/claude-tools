"""Stealth crawler for JS-WAF schools (xjtu / uestc.scse / uestc.sise / xidian).

Three-tier cascade per the user's design:

1. **Playwright + playwright-stealth** — load chromium with stealth tweaks
   (no webdriver flag, full Chrome fingerprint, plugins/permissions hints)
   so the WAF JS challenge is treated as a real browser.
2. **Homepage warm-up** — visit ``https://<host>/`` first so the WAF / CDN
   can set its cookie set, *then* navigate to the deep list / profile URL.
   Many .edu.cn WAFs refuse direct-to-internal-page requests.
3. **Fallback** — if the above still yields an empty / WAF-stub page:
   - **Wayback Machine** (``archive.org``) — try the most recent snapshot
     of the same URL; if found, parse that snapshot instead.
   - **DBLP** — for individual advisors we missed, query the dblp author
     index to recover at least name+aff+publications; we don't get the
     standard profile fields but the advisor row gets created.

Pure tooling: doesn't touch the existing Fetcher path so it can be reverted
or extended independently. CLI: ``claw crawl-stealth --school <code>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from ..adapters import get_adapter
from ..config import SchoolConfig, find_school
from ..core.http import make_legacy_friendly_ssl_context
from ..core.logging import get_logger
from ..models.db import init_db
from ..storage.repo import (
    link_department,
    record_snapshot,
    session_scope,
    upsert_advisor,
    upsert_department,
    upsert_school,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# WAF / stub detection
# ---------------------------------------------------------------------------

# Heuristic: pages under ~5 KB are usually WAF stubs / 412 challenge HTML.
_WAF_STUB_MAX_BYTES = 4096

# WAF challenge keywords we have observed on xjtu / uestc / tencent-shielded sites.
_WAF_PATTERNS = (
    "请稍候",
    "正在加载",
    "challenge-platform",
    "TS-WAF",
    "_ts_window_ready",
    "id=\"sx6c1KC7YLcx\"",       # uestc TS-WAF specific
    "id=\"hK5iNqnNcwxO\"",       # nwpu TS-WAF specific
)


def _looks_like_waf_stub(html: str) -> bool:
    if not html:
        return True
    if len(html) < _WAF_STUB_MAX_BYTES and any(p in html for p in _WAF_PATTERNS):
        return True
    # Extra safety: very short page with no Chinese names — WAF stub.
    if len(html) < 2048 and not re.search(r"[一-鿿]{3,}", html):
        return True
    return False


# ---------------------------------------------------------------------------
# Stealth browser pool — separate from core/browser.py for clear separation
# ---------------------------------------------------------------------------


@dataclass
class _StealthSession:
    pw: Playwright
    browser: Browser
    context: BrowserContext
    warmed_hosts: set[str]


@contextlib.asynccontextmanager
async def open_stealth_session(headed: bool = False):
    """Open a single stealth chromium context. Applies playwright-stealth's
    countermeasures to every new page via an init script.
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=not headed,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )

    # Apply the stealth countermeasures.  playwright-stealth's API changed
    # between v1 (stealth_async / stealth_sync) and v2 (Stealth().apply...).
    # Try both shapes.
    try:
        from playwright_stealth import Stealth  # v2

        await Stealth().apply_stealth_async(context)
        log.info("stealth: applied via playwright-stealth v2 Stealth().apply_stealth_async")
    except Exception:
        try:
            from playwright_stealth import stealth_async  # v1

            # v1 only applies to a Page, so we patch each page on creation
            async def _patch(page: Page) -> None:
                with contextlib.suppress(Exception):
                    await stealth_async(page)

            context.on("page", lambda p: asyncio.create_task(_patch(p)))
            log.info("stealth: applied via playwright-stealth v1 stealth_async (per-page)")
        except Exception as e:  # noqa: BLE001
            log.warning("stealth: playwright-stealth not available (%s); using vanilla", e)

    try:
        yield _StealthSession(
            pw=pw, browser=browser, context=context, warmed_hosts=set(),
        )
    finally:
        with contextlib.suppress(Exception):
            await context.close()
        with contextlib.suppress(Exception):
            await browser.close()
        with contextlib.suppress(Exception):
            await pw.stop()


# ---------------------------------------------------------------------------
# Core fetch with warm-up + stub detection
# ---------------------------------------------------------------------------


async def stealth_fetch(
    sess: _StealthSession,
    url: str,
    *,
    warmup: bool = True,
    nav_timeout_ms: int = 30000,
    settle_wait_ms: int = 1500,
) -> str:
    """Navigate to ``url`` and return its HTML string.

    If ``warmup`` is True and the URL's host hasn't been visited yet, hit
    ``https://<host>/`` first so the WAF/CDN gets a chance to set cookies
    before we deep-link.

    Returns "" if the page never resolved beyond a WAF stub.
    """
    host = urlparse(url).hostname or ""
    if warmup and host and host not in sess.warmed_hosts:
        home = f"https://{host}/"
        log.info("stealth warmup: %s", home)
        page = await sess.context.new_page()
        try:
            with contextlib.suppress(Exception):
                await page.goto(home, timeout=nav_timeout_ms, wait_until="domcontentloaded")
                await asyncio.sleep(settle_wait_ms / 1000)
                sess.warmed_hosts.add(host)
        finally:
            await page.close()

    page = await sess.context.new_page()
    try:
        try:
            await page.goto(url, timeout=nav_timeout_ms, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            log.warning("stealth nav failed for %s: %s", url, e)
            return ""
        # let JS-challenge finish + any post-load XHR populate the DOM
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(settle_wait_ms / 1000)
        # page.content() races with JS-driven navigation (very common on
        # wayback's toolbar iframe). Retry once after a longer settle.
        html = ""
        for attempt in range(2):
            try:
                html = await page.content()
                break
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "stealth content() failed for %s (attempt %d): %s",
                    url, attempt + 1, e,
                )
                await asyncio.sleep(2.0)
        if not html:
            return ""
        if _looks_like_waf_stub(html):
            log.warning("stealth: WAF stub for %s (len=%d)", url, len(html))
            return ""
        return html
    except Exception as e:  # noqa: BLE001
        log.warning("stealth_fetch outer error for %s: %s", url, e)
        return ""
    finally:
        with contextlib.suppress(Exception):
            await page.close()


# ---------------------------------------------------------------------------
# Wayback Machine fallback
# ---------------------------------------------------------------------------


async def wayback_latest(url: str) -> str | None:
    """Ask the Wayback Availability API for the most recent snapshot URL."""
    api = f"https://archive.org/wayback/available?url={url}"
    try:
        async with httpx.AsyncClient(
            timeout=20, verify=make_legacy_friendly_ssl_context(), http2=True,
        ) as client:
            r = await client.get(api)
            r.raise_for_status()
            payload = r.json()
            snap = (
                payload.get("archived_snapshots", {}).get("closest", {})
            )
            if not snap or not snap.get("available"):
                return None
            return snap.get("url")
    except Exception as e:  # noqa: BLE001
        log.warning("wayback lookup failed for %s: %s", url, e)
        return None


async def wayback_fetch_html(sess: _StealthSession, original_url: str) -> str:
    """Try wayback for a previously-WAF'd URL. Returns HTML or "" if unavailable."""
    snap_url = await wayback_latest(original_url)
    if not snap_url:
        return ""
    log.info("wayback: %s → %s", original_url, snap_url)
    # Wayback wraps the snapshot in an iframe + toolbar; we want the raw
    # archived page. Adding the `id_` modifier returns the original HTML
    # without rewriting. Pattern: …/web/<TS>id_/<URL>
    snap_url = re.sub(r"(web/\d+)/", r"\1id_/", snap_url, count=1)
    # Wayback isn't WAF-blocked, but reuse the stealth pool for one TCP path.
    html = await stealth_fetch(sess, snap_url, warmup=False)
    return html


# ---------------------------------------------------------------------------
# DBLP fallback (per-advisor)
# ---------------------------------------------------------------------------


async def dblp_search(name: str, affiliation_hint: str | None = None) -> list[dict]:
    """Query dblp for an author by name; optionally filter by affiliation token.

    Returns a list of dicts: {name, url, affiliations[]}.
    """
    q = name
    api = f"https://dblp.org/search/author/api?q={q}&format=json&h=10"
    try:
        async with httpx.AsyncClient(timeout=15, http2=True) as client:
            r = await client.get(api)
            r.raise_for_status()
            payload = r.json()
            hits = (
                payload.get("result", {})
                .get("hits", {})
                .get("hit", [])
            )
            out: list[dict] = []
            for h in hits:
                info = h.get("info", {})
                aff_text = info.get("notes", {}).get("note", "")
                if isinstance(aff_text, list):
                    aff_text = " ".join(
                        a.get("text", "") if isinstance(a, dict) else str(a)
                        for a in aff_text
                    )
                if affiliation_hint and affiliation_hint.lower() not in str(aff_text).lower():
                    continue
                out.append({
                    "name": info.get("author", name),
                    "url": info.get("url", ""),
                    "affiliations": aff_text,
                })
            return out
    except Exception as e:  # noqa: BLE001
        log.warning("dblp search failed for %s: %s", name, e)
        return []


# ---------------------------------------------------------------------------
# Top-level crawl orchestration
# ---------------------------------------------------------------------------


@dataclass
class StealthCrawlStats:
    list_pages_ok: int = 0
    list_pages_stub: int = 0
    wayback_fallbacks: int = 0
    profiles_ok: int = 0
    profiles_stub: int = 0
    dblp_fallbacks: int = 0
    advisors_upserted: int = 0
    errors: int = 0


async def crawl_school_with_stealth(
    school_code: str,
    *,
    headed: bool = False,
    snapshot: bool = True,
    limit: int | None = None,
) -> StealthCrawlStats:
    init_db()
    cfg = find_school(school_code)
    if cfg is None:
        raise ValueError(f"school {school_code!r} not in schools.yaml")
    adapter = get_adapter(school_code)
    stats = StealthCrawlStats()

    async with open_stealth_session(headed=headed) as sess:
        with session_scope() as s:
            school_row = upsert_school(s, cfg.code, cfg.name_cn, cfg.name_en)

            for dept in cfg.departments:
                if not adapter.supports_dept(dept.code):
                    log.info("stealth: adapter does not support dept %s", dept.code)
                    continue
                primary_list = dept.list_urls[0].url if dept.list_urls else None
                dept_row = upsert_department(s, school_row, dept.code, dept.name_cn, primary_list)

                for spec in dept.list_urls:
                    if spec.method.upper() != "GET":
                        log.info("stealth: skipping non-GET list spec for %s (use Fetcher)", spec.url)
                        continue
                    list_url = spec.url

                    html = await stealth_fetch(sess, list_url)
                    if not html:
                        stats.list_pages_stub += 1
                        log.info("stealth: list WAF'd, trying wayback for %s", list_url)
                        html = await wayback_fetch_html(sess, list_url)
                        if html:
                            stats.wayback_fallbacks += 1
                    if not html:
                        log.warning("stealth: list unrecoverable %s — skipping", list_url)
                        continue
                    stats.list_pages_ok += 1

                    if snapshot:
                        from ..core.snapshot import write_snapshot
                        sha, path = write_snapshot(
                            f"{school_row.code}-{dept_row.code}", list_url, html.encode("utf-8"),
                        )
                        record_snapshot(s, list_url, sha, str(path))

                    try:
                        items = adapter.parse_list(html, list_url)
                    except Exception as e:  # noqa: BLE001
                        log.exception("parse_list failed for %s: %s", list_url, e)
                        stats.errors += 1
                        continue
                    log.info("[%s/%s] stealth parsed %d list items", school_row.code, dept_row.code, len(items))

                    for item in items:
                        if limit and stats.advisors_upserted >= limit:
                            return stats
                        try:
                            await _process_profile_stealth(
                                sess=sess,
                                adapter=adapter,
                                school=school_row,
                                dept=dept_row,
                                item=item,
                                session=s,
                                stats=stats,
                                snapshot=snapshot,
                            )
                        except Exception as e:  # noqa: BLE001
                            log.exception(
                                "stealth: profile crash for %s (%s): %s",
                                item.name_cn, item.profile_url, e,
                            )
                            stats.errors += 1

    log.info("stealth crawl done for %s: %s", school_code, stats)
    return stats


async def _process_profile_stealth(
    *,
    sess: _StealthSession,
    adapter,
    school,
    dept,
    item,
    session,
    stats: StealthCrawlStats,
    snapshot: bool,
) -> None:
    from ..models.pydantic_models import AdvisorPartial
    from ..core.snapshot import write_snapshot

    partial: AdvisorPartial | None = None
    profile_html: str = ""

    if item.profile_url:
        profile_html = await stealth_fetch(sess, item.profile_url)
        if not profile_html:
            stats.profiles_stub += 1
            log.info("stealth: profile WAF'd, trying wayback for %s", item.profile_url)
            profile_html = await wayback_fetch_html(sess, item.profile_url)
            if profile_html:
                stats.wayback_fallbacks += 1
        if profile_html:
            stats.profiles_ok += 1
            if snapshot:
                sha, path = write_snapshot(
                    f"{school.code}-{dept.code}",
                    item.profile_url,
                    profile_html.encode("utf-8"),
                )
                record_snapshot(session, item.profile_url, sha, str(path))
            try:
                partial = adapter.parse_profile(profile_html, item.profile_url, item)
            except Exception as e:  # noqa: BLE001
                log.warning("profile parse failed for %s: %s", item.name_cn, e)
                stats.errors += 1

    if partial is None:
        # Final fallback: enrich identity from dblp so the advisor row at
        # least exists in DB. The agent enricher will fill in more later.
        dblp_hits = await dblp_search(item.name_cn, affiliation_hint=school.name_cn)
        if dblp_hits:
            stats.dblp_fallbacks += 1
            top = dblp_hits[0]
            partial = AdvisorPartial(
                name_cn=item.name_cn,
                homepage=top.get("url"),
                bio_text=f"[dblp] {top.get('affiliations','')[:200]}",
                source_url=top.get("url") or item.profile_url,
                email_obfuscated=True,
            )
        else:
            # last-resort: list-only insert
            partial = AdvisorPartial(
                name_cn=item.name_cn,
                title=item.title,
                email=item.email,
                phone=item.phone,
                photo_url=item.photo_url,
                homepage=item.profile_url,
                source_url=item.profile_url,
                email_obfuscated=item.email is None,
            )

    advisor = upsert_advisor(session, school, partial)
    link_department(session, advisor, dept)
    stats.advisors_upserted += 1


def crawl_school_with_stealth_sync(school_code: str, **kw) -> StealthCrawlStats:
    return asyncio.run(crawl_school_with_stealth(school_code, **kw))
