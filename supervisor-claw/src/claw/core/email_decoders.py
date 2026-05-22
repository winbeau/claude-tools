"""Site-specific JS-encrypted email decoders.

For schools whose faculty portals encrypt the email value in static HTML and
populate the plain-text via a runtime ``<script>`` (e.g. faculty.xidian /
faculty.hust), the only reliable strategy is to **let the encryption JS run
in a real browser** and read the post-render DOM. This module gives the
backfill enricher a small set of helpers that take a Playwright ``page`` and
return the decoded email string (or ``None``).

The helpers here are intentionally permissive — they prefer waiting a few
seconds and falling back to DOM-wide regex over implementing the encryption
algorithm in Python. The encrypted blobs (e.g. xidian ``_tsites_encrypt_field``
hex blobs, hust ``Ot-ctact`` cipher) are produced by JS that runs against
``window`` and is best executed in-browser.

Design contract
---------------
- Every public function is ``async`` and accepts a Playwright ``Page``.
- Functions never raise on missing/malformed input — they return ``None``.
- ``extract_email_from_rendered_dom`` is the generic fallback used after we
  give the page enough time for any decryption JS to populate the DOM.
- The browser lifecycle is owned by the caller (see
  ``pipeline.stealth_crawler.open_stealth_session``); helpers here just use
  whatever ``page`` they're handed.

Recognised email regex matches plain ASCII academic addresses; we lowercase
the matched string before returning.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import TYPE_CHECKING

from .logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page

log = get_logger(__name__)

# Generic email regex (case-insensitive). We deliberately exclude common
# image/asset extensions so we don't pick up "logo@2x.png" etc.
_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"
)

# Footer / contact-form addresses that show up site-wide and aren't the
# advisor's personal email. Reject any address whose local-part matches.
_FOOTER_LOCAL_PARTS: tuple[str, ...] = (
    "webmaster",
    "admin",
    "service",
    "office",
    "info",
    "contact",
    "support",
    "noreply",
    "no-reply",
    "postmaster",
    "sse",
    "scs",
    "aia-president",
)

# XJTU-specific footer / department-contact local-parts. These show up on the
# bottom of cs.xjtu / se.xjtu / aiar.xjtu pages and on wayback snapshots of the
# same; they are NOT individual advisor emails. Listed as a prefix tuple so
# subclasses (e.g. ``cs-recruit01@xjtu.edu.cn``) are also rejected.
_XJTU_FOOTER_PREFIXES: tuple[str, ...] = (
    "cs-office",      # cs.xjtu 学院综合办
    "ai-info",        # aiar.xjtu 信息员
    "se-office",      # se.xjtu 软件学院办公室
    "ai-office",
    "cs-recruit",     # 招生办公室
    "xjtucs",         # 学院公邮
)


def _is_xjtu_footer(addr: str) -> bool:
    """Return True if ``addr``'s local-part looks like an XJTU footer email.

    Matches any of :data:`_XJTU_FOOTER_PREFIXES` as a prefix of the local part
    (case-insensitive). Helper for :mod:`claw.enrichers.sites.xjtu_email`.
    """
    if "@" not in addr:
        return False
    local = addr.split("@", 1)[0].lower()
    return any(local.startswith(p) for p in _XJTU_FOOTER_PREFIXES)

# How long to wait for the page's encryption JS to populate the DOM.
_JS_DECODE_TIMEOUT_MS = 15_000


def _is_footer_like(addr: str) -> bool:
    local = addr.split("@", 1)[0].lower()
    return local in _FOOTER_LOCAL_PARTS


def _pick_email(
    candidates: list[str],
    domain_hint: str | None = None,
) -> str | None:
    """From a list of candidate addresses pick the best one.

    Preference order:
    1. matches ``domain_hint`` (substring, e.g. ``xjtu.edu.cn``)
    2. ends with an academic TLD ``.edu`` / ``.edu.cn`` / ``.ac.cn``
    3. first non-footer-like address
    4. None
    """
    if not candidates:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        a = c.strip().lower()
        if not a or a in seen:
            continue
        seen.add(a)
        if _is_footer_like(a):
            continue
        cleaned.append(a)
    if not cleaned:
        return None

    if domain_hint:
        hint = domain_hint.lower()
        for a in cleaned:
            if hint in a:
                return a

    for a in cleaned:
        host = a.split("@", 1)[1]
        if host.endswith(".edu.cn") or host.endswith(".ac.cn") or host.endswith(".edu"):
            return a
    return cleaned[0]


async def extract_email_from_rendered_dom(
    page: "Page",
    domain_hint: str | None = None,
    *,
    settle_ms: int = 1500,
) -> str | None:
    """Generic post-render extractor.

    Waits a short settle, grabs ``page.content()`` plus the body innerText,
    and regex-matches all email-looking strings. If ``domain_hint`` is given,
    candidates matching that domain are preferred.

    Never raises — returns ``None`` on any failure.
    """
    try:
        await asyncio.sleep(max(0.0, settle_ms / 1000))
    except Exception:  # noqa: BLE001
        pass

    html = ""
    with contextlib.suppress(Exception):
        html = await page.content()

    body_text = ""
    with contextlib.suppress(Exception):
        body_text = await page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )

    haystack = (html or "") + "\n" + (body_text or "")
    if not haystack.strip():
        return None

    candidates = _EMAIL_RE.findall(haystack)
    return _pick_email(candidates, domain_hint=domain_hint)


async def _wait_for_decoded_span(
    page: "Page",
    span_selector: str,
    *,
    timeout_ms: int = _JS_DECODE_TIMEOUT_MS,
) -> str | None:
    """Wait for any element matching ``span_selector`` to contain an ``@``,
    then return its textContent. Returns ``None`` on timeout / no match.
    """
    # Use Playwright's wait_for_function so we don't busy-loop here.
    js = (
        "(sel) => { const els = document.querySelectorAll(sel);"
        " for (const e of els) {"
        "   const t = (e.innerText || e.textContent || '').trim();"
        "   if (t.includes('@') && /\\.[a-zA-Z]{2,}/.test(t)) return t;"
        " } return false; }"
    )
    try:
        await page.wait_for_function(js, arg=span_selector, timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return None

    with contextlib.suppress(Exception):
        text = await page.evaluate(js, span_selector)
        if isinstance(text, str) and "@" in text:
            return text.strip()
    return None


async def decode_xidian_email(page: "Page") -> str | None:
    """Decode an email from a ``faculty.xidian.edu.cn`` profile page.

    The static HTML carries ``<span class="encrypt-field" _tsites_encrypt_field>
    HEXBLOB</span>``; ``/system/resource/tsites/tsitesencrypt.js`` is loaded
    in the page and at runtime replaces the span's text with the plain-text
    email. We wait for that replacement, then read the span's innerText.

    Fallback: if the span never decodes within the timeout window, run the
    generic DOM scan (some advisors expose a plain-text email elsewhere on
    the profile, e.g. in the bio prose).

    Returns ``None`` if no email can be recovered.
    """
    # All encrypt-field spans share the marker attribute; their actual
    # text gets set by tsitesencrypt.js on page load.
    span_selector = "span[_tsites_encrypt_field]"
    decoded = await _wait_for_decoded_span(page, span_selector, timeout_ms=_JS_DECODE_TIMEOUT_MS)
    if decoded:
        candidates = _EMAIL_RE.findall(decoded)
        picked = _pick_email(candidates, domain_hint="xidian.edu.cn")
        if picked:
            return picked

    # Generic fallback — bio prose sometimes has the email in plain text
    # even when the encrypted span never decoded (e.g. WAF stripped the JS).
    return await extract_email_from_rendered_dom(page, domain_hint="xidian.edu.cn")


async def decode_hust_email(page: "Page") -> str | None:
    """Decode an email from a ``faculty.hust.edu.cn`` profile page.

    HUST faculty profiles ship the email cipher inside
    ``div.blockwhite.Ot-ctact`` and rely on ``ImageScale.addimg()`` /
    SiteBuilder runtime JS to populate the text. We wait until the Ot-ctact
    block contains an ``@`` then read it.
    """
    # The Ot-ctact block contains multiple <span _tsites_encrypt_field> just
    # like xidian; same wait_for_function pattern works.
    ot_selector = "div.Ot-ctact span[_tsites_encrypt_field], div.Ot-ctact"
    decoded = await _wait_for_decoded_span(page, ot_selector, timeout_ms=_JS_DECODE_TIMEOUT_MS)
    if decoded:
        candidates = _EMAIL_RE.findall(decoded)
        picked = _pick_email(candidates, domain_hint="hust.edu.cn")
        if picked:
            return picked

    # HUST sometimes hosts the email in plain text on the legacy
    # <school>.hust.edu.cn/info/<treeid>/<id>.htm template.
    return await extract_email_from_rendered_dom(page, domain_hint="hust.edu.cn")


__all__ = [
    "decode_hust_email",
    "decode_xidian_email",
    "extract_email_from_rendered_dom",
    "_XJTU_FOOTER_PREFIXES",
    "_is_xjtu_footer",
]
