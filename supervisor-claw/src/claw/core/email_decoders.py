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

# Known TLDs. SERPs/HTML routinely glue an email to the next Chinese word
# with no space ("nwpu.edu.cn 慧信" → "nwpu.edu.cnhuixin"); without a TLD
# whitelist the regex happily eats those 5+ char suffixes as a valid TLD.
# Listed: common gTLDs + ccTLDs + the academic-relevant ones.
_VALID_TLDS = (
    "cn", "com", "org", "net", "edu", "gov", "mil", "info", "biz",
    "io", "ai", "co", "me", "us", "uk", "jp", "kr", "hk", "tw", "mo",
    "de", "fr", "ru", "ca", "au", "sg", "il", "in", "it", "es", "nl",
    "se", "no", "fi", "dk", "ch", "at", "be", "pl", "br", "mx", "tr",
    "name", "pro", "xyz", "app", "tech", "cloud", "online",
)
_TLD_GROUP = "|".join(_VALID_TLDS)
_EMAIL_RE = re.compile(
    rf"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.(?:{_TLD_GROUP}))\b",
    re.IGNORECASE,
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
    # HUST-style institute aliases
    "sse",
    "scs",
    "cs-help",
    "cs-info",
    "csdean",
    "csshuji",
    "ssedean",
    "sseshuji",
    "sse-info",
    "aia",
    "aia-info",
    "aia-dean",
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

# ---------------------------------------------------------------------------
# tsites hex-blob decryption (best-effort pure Python)
# ---------------------------------------------------------------------------
#
# faculty.xidian.edu.cn (and other Sudy-CMS / tsites portals) ship
# ``<span class="encrypt-field" _tsites_encrypt_field>HEXBLOB</span>``. The
# plain-text is written into the span at runtime by
# ``/system/resource/tsites/tsitesencrypt.js``. The actual cipher used by
# tsitesencrypt.js is **not** publicly documented and the key is buried
# inside the obfuscated JS shipped per-page (likely a per-site SM4/AES
# session key — observed blobs are 128–160 bytes which suggests a block
# cipher rather than a fixed-key XOR stream).
#
# We tried to derive the key from public info in this repo (adapter
# comments, a captured fixture blob) and could not — the blob length and
# byte-distribution do not match a single-byte or short-repeated XOR
# pattern. So :func:`decrypt_tsites_hex` is deliberately conservative:
#
# 1. If the hex string is short enough to *be* a plain email (≤ 64 hex
#    chars = ≤ 32 bytes, i.e. ≤ 1 block), try interpreting it as ASCII —
#    occasionally tsites pages embed *plaintext* hex of the email (its
#    "encryption" layer is bypassed for some fields).
# 2. Try single-byte XOR — for every key in ``0x00..0xff`` decode the bytes,
#    check whether the result looks like a valid email (regex match,
#    printable ASCII). Return the first hit.
# 3. Otherwise return ``None`` — the caller falls back to letting the page
#    JS run in a real browser and reading the post-render DOM.
#
# This means in practice the pure-Python path almost always returns
# ``None`` on real faculty.xidian blobs (they are AES/SM4-ciphered), but
# it costs ~0.1 ms and saves a 15s browser wait on the lucky pages where
# the blob is trivially encoded. The browser path is still the source of
# truth.

_TSITES_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _looks_like_email(text: str) -> bool:
    """Cheap plausibility check for XOR-decode outputs."""
    if not text or "@" not in text:
        return False
    # All chars must be printable ASCII.
    if any(ord(c) < 0x20 or ord(c) > 0x7e for c in text):
        return False
    return bool(_EMAIL_RE.search(text))


def decrypt_tsites_hex(hex_blob: str) -> str | None:
    """Best-effort pure-Python decryption of a tsites hex email blob.

    ``hex_blob`` is the inner text of ``<span _tsites_encrypt_field>...</span>``
    on a faculty.xidian.edu.cn (and similar tsites/Sudy-CMS) profile page.

    Returns the decoded ASCII email if one of the trivial decode paths
    matches, otherwise ``None``. **Most real blobs return ``None``** —
    callers must fall back to the browser-render path (see
    :func:`decode_xidian_email`).
    """
    if not hex_blob:
        return None
    s = hex_blob.strip()
    if not s or not _TSITES_HEX_RE.match(s):
        return None
    # Hex strings are even-length pairs.
    if len(s) % 2 != 0:
        return None
    try:
        raw = bytes.fromhex(s)
    except ValueError:
        return None

    # (1) Plain ASCII hex (rare — but cheap to check)
    with contextlib.suppress(UnicodeDecodeError):
        as_ascii = raw.decode("ascii")
        if _looks_like_email(as_ascii):
            return as_ascii.lower()

    # (2) Single-byte XOR sweep — try every possible key.
    for key in range(256):
        decoded = bytes(b ^ key for b in raw)
        try:
            text = decoded.decode("ascii")
        except UnicodeDecodeError:
            continue
        if _looks_like_email(text):
            m = _EMAIL_RE.search(text)
            if m:
                return m.group(1).lower()

    # Could attempt longer repeating XOR keys here (length 2..8) but in
    # observed blobs that hasn't matched either — and the cost grows fast.
    return None


def _is_footer_like(addr: str) -> bool:
    local = addr.split("@", 1)[0].lower()
    return local in _FOOTER_LOCAL_PARTS


# Org-list / department-mailbox localpart prefixes seen on .edu.cn sites.
# An address with localpart equal to one of these or ``<prefix>{-,_,.}…`` is
# almost certainly a bureau mailbox, not the advisor's personal email.
_ORG_LIST_PREFIXES: tuple[str, ...] = (
    "info", "service", "office", "admin", "dean", "help",
    "contact", "master", "noreply", "no-reply", "teach", "recruit",
    "zs", "yzs", "yjs", "yzc", "jx", "yz", "tw", "tuanwei", "dangwei",
    "zhaosheng", "zhaopin", "secretary", "mishu", "lib", "library",
    "centre", "center", "news", "report", "press", "edu", "kyc",
    "kjc", "rsc", "cwc", "xsc", "yzb", "yjsb", "jwc",
)

# Chinese-administrative-term pinyin spotted in the wild as bureau mailboxes
# (硕士班 → shiziban, 研究生班 → yjsban, etc.). Exact-match only — anything
# longer than these usually IS a personal name with one of these as substring
# (e.g. "yanjiuxxx" — only reject "yanjiusheng" exact).
_ADMIN_PINYIN_EXACT: tuple[str, ...] = (
    "shiziban",       # 硕士班 — caught FP: 樊晓桠 → shiziban@nwpu
    "boshiban",       # 博士班
    "yjsban",         # 研究生班
    "yjsh",           # 研究生
    "yanjiusheng",    # 研究生
    "banzhuren",      # 班主任
    "fudaoyuan",      # 辅导员
    "jiaowu",         # 教务
    "jiaoxue",        # 教学
    "xueyuan",        # 学院
    "xueke",          # 学科
    "shiyanshi",      # 实验室
    "danwei",         # 单位
    "yanshengyuan",   # 研生院
    "xueshengchu",    # 学生处
    "xuegongbu",      # 学工部
    "jwc",            # 教务处
    "rsb",            # 人事部
    "kyc",            # 科研处
    "graduate",       # 'graduate' / english variant
    "alumni",         # 'alumni' admin list
    "webmaster",      # already in _FOOTER_LOCAL_PARTS but be safe
)

# 20 school codes — combined with admin suffixes catches accounts like
# 'xjtunic', 'hust-info', 'nwpu_help', 'tsinghuaservice' (real FP: 朱利 →
# xjtunic@mail.xjtu.edu.cn was xjtu's NIC, not 朱利's mailbox).
_SCHOOL_CODE_PREFIXES: tuple[str, ...] = (
    "xjtu", "xidian", "nwpu", "hust", "nudt", "fudan", "pku", "nju",
    "ustc", "bit", "zju", "sysu", "tju", "nankai", "sjtu",
    "shanghaitech", "shtech", "buaa", "uestc", "seu", "tsinghua",
)
_ADMIN_SUFFIX_TOKENS: tuple[str, ...] = (
    "admin", "info", "nic", "help", "office", "service", "support",
    "master", "dean", "recruit", "secretary", "zs", "yzs", "yzc", "yjs",
    "news", "lib", "library", "press", "contact", "feedback",
)


def _looks_like_personal_localpart(localpart: str) -> bool:
    """Cheap heuristic: does the localpart look like a person, not a list?

    Rejects:
    * empty / very short (≤ 3 chars) — too generic
    * known bureaucratic prefixes (info-…, recruit-…, yzs, yzc, dean, …)
    * Chinese admin-term exact matches (shiziban, boshiban, jiaowu, …)
    * school-code-prefixed admin accounts (xjtunic, hust-info, …)
    * all-consonant Chinese acronyms (zero vowels) like ``gfkdyzc``,
      which are how academic departments name their group mailboxes
      (国防 + 科大 + 研招/院招 → "gfkdyzc")

    A False return means "very likely org-list, skip". A True return
    does NOT prove the address is personal — it just clears this filter.
    """
    if not localpart:
        return False
    lp = localpart.lower()
    if len(lp) <= 3:
        return False
    for p in _ORG_LIST_PREFIXES:
        if lp == p:
            return False
        if lp.startswith(p + "-") or lp.startswith(p + "_") or lp.startswith(p + "."):
            return False
    if lp in _ADMIN_PINYIN_EXACT:
        return False
    # School-code-prefixed admin (xjtunic, hust-info, …)
    for sp in _SCHOOL_CODE_PREFIXES:
        if lp == sp:
            return False
        if lp.startswith(sp):
            rest = lp[len(sp):]
            if rest in _ADMIN_SUFFIX_TOKENS:
                return False
            if rest and rest[0] in "-_." and rest[1:] in _ADMIN_SUFFIX_TOKENS:
                return False
    # ``"gfkdyzc"`` (0 vowels, all consonants) → reject. We treat 'y' as a
    # consonant on purpose so e.g. ``"yzs"`` / ``"yjsb"`` get caught.
    if not any(c in "aeiou" for c in lp):
        return False
    return True


def _pick_email(
    candidates: list[str],
    domain_hint: str | None = None,
    *,
    strict_domain: bool = False,
) -> str | None:
    """From a list of candidate addresses pick the best one.

    When ``strict_domain=True`` and ``domain_hint`` is given, **only**
    addresses matching that domain are eligible — no fall-through to
    other academic TLDs or unrelated personal mailboxes. This is the
    safe mode for off-site search (bing / dblp), where SERPs routinely
    surface unrelated people's gmail / qq / outlook addresses.

    Default (``strict_domain=False``) preserves the legacy preference:
    1. matches ``domain_hint`` (substring, e.g. ``xjtu.edu.cn``)
    2. ends with an academic TLD ``.edu`` / ``.edu.cn`` / ``.ac.cn``
    3. first non-footer-like address
    4. None

    Org-list-like localparts (see :func:`_looks_like_personal_localpart`)
    are filtered out before any ranking — so a ``gfkdyzc@nudt.edu.cn``
    candidate never wins, even if it's the only domain match.
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
        local = a.split("@", 1)[0]
        if not _looks_like_personal_localpart(local):
            continue
        cleaned.append(a)
    if not cleaned:
        return None

    if domain_hint:
        hint = domain_hint.lower()
        domain_matches = [a for a in cleaned if hint in a.split("@", 1)[1]]
        if domain_matches:
            return domain_matches[0]
        if strict_domain:
            # No domain match → refuse rather than fall through. This is
            # how we avoid bing SERPs handing us a stranger's gmail.
            return None

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


async def _extract_mailto(page: "Page") -> str | None:
    """Generic ``mailto:`` extractor.

    Many faculty pages — including HUST's legacy ``info/<treeid>/<id>.htm``
    template and a chunk of xjtu / nju / bit profile pages — render the
    instructor's address as a plain ``<a href="mailto:foo@bar">…</a>``
    link even when the visible text is encrypted (or just an icon).

    Walks every ``a[href^="mailto:"]`` in the live DOM, strips ``mailto:``
    + query params (``?subject=…``), filters out obvious footer-like
    addresses, and returns the first plausible one. Returns ``None`` on
    any failure — never raises.
    """
    try:
        hrefs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href^=\"mailto:\"]'))"
            ".map(a => a.getAttribute('href') || '')"
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(hrefs, list):
        return None

    candidates: list[str] = []
    for h in hrefs:
        if not isinstance(h, str):
            continue
        addr = h.strip()
        # strip leading scheme (any case)
        if addr.lower().startswith("mailto:"):
            addr = addr[7:]
        # drop URL params (?subject=…&body=…)
        addr = addr.split("?", 1)[0].strip()
        # some sites HTML-encode the @ or wrap the address in <>
        addr = addr.strip("<> ")
        if "@" not in addr:
            continue
        # mailto can carry multiple recipients separated by ',' — take the
        # first non-footer-looking one.
        for piece in addr.split(","):
            piece = piece.strip()
            if not piece or "@" not in piece:
                continue
            candidates.append(piece)

    return _pick_email(candidates)


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


async def _read_tsites_hex_blobs(page: "Page") -> list[str]:
    """Pull the raw hex contents of every ``<span _tsites_encrypt_field>``.

    We read the *raw* textContent of each marker span — before any
    tsitesencrypt.js decode runs, this is the original hex blob; after
    decode it would be plain text. Either way, the consumer
    (:func:`decrypt_tsites_hex`) handles non-hex input gracefully by
    returning ``None``.
    """
    js = (
        "() => Array.from(document.querySelectorAll('span[_tsites_encrypt_field]'))"
        ".map(e => (e.textContent || '').trim()).filter(t => t.length > 0)"
    )
    try:
        result = await page.evaluate(js)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(result, list):
        return []
    return [t for t in result if isinstance(t, str)]


async def decode_xidian_email(page: "Page") -> str | None:
    """Decode an email from a ``faculty.xidian.edu.cn`` profile page.

    The static HTML carries ``<span class="encrypt-field" _tsites_encrypt_field>
    HEXBLOB</span>``; ``/system/resource/tsites/tsitesencrypt.js`` is loaded
    in the page and at runtime replaces the span's text with the plain-text
    email.

    Decode cascade
    --------------
    1. **Pure-Python attempt** — pull every encrypt-field span's raw text,
       feed each through :func:`decrypt_tsites_hex` (trivial-XOR /
       plain-ASCII probe). Most real blobs fail this, but it's cheap
       (~0.1 ms each) and saves a 15s wait on the rare plaintext-hex case.
    2. **Browser-render wait** — call :func:`_wait_for_decoded_span` and
       read whatever the page's own JS placed in the span. This is the
       only reliable path for real-world AES/SM4 ciphered blobs.
    3. **Generic DOM fallback** — last-ditch regex scan for emails in the
       rendered DOM (bio prose often surfaces an email even when the
       encrypted span never decodes).

    Returns ``None`` if no email can be recovered.
    """
    # (1) pure-Python decrypt attempt on every raw blob
    for blob in await _read_tsites_hex_blobs(page):
        decoded = decrypt_tsites_hex(blob)
        if decoded and "xidian.edu.cn" in decoded:
            return decoded
        if decoded:
            # Decoded but not the school domain — still likely valid
            # (some advisors use personal Gmail). Accept it.
            return decoded

    # (2) browser-decoded DOM read
    span_selector = "span[_tsites_encrypt_field]"
    decoded_text = await _wait_for_decoded_span(
        page, span_selector, timeout_ms=_JS_DECODE_TIMEOUT_MS
    )
    if decoded_text:
        candidates = _EMAIL_RE.findall(decoded_text)
        picked = _pick_email(candidates, domain_hint="xidian.edu.cn")
        if picked:
            return picked

    # (3) Generic fallback — bio prose sometimes has the email in plain text
    # even when the encrypted span never decoded (e.g. WAF stripped the JS).
    return await extract_email_from_rendered_dom(page, domain_hint="xidian.edu.cn")


async def decode_hust_email(page: "Page") -> str | None:
    """Decode an email from a ``faculty.hust.edu.cn`` profile page.

    HUST faculty profiles ship the email cipher inside
    ``div.blockwhite.Ot-ctact`` and rely on ``ImageScale.addimg()`` /
    SiteBuilder runtime JS to populate the text. The decryption JS is
    **lazy** — it can take 8-10s after ``DOMContentLoaded`` to finish
    populating the span, so we use a longer timeout than xidian and try
    multiple selectors in priority order.

    Strategy:
      1. Wait (long) for any of several candidate selectors to contain
         a decoded ``user@host`` string.
      2. If step 1 misses, sniff for ``a[href^="mailto:"]`` — HUST often
         hides the address there even when the visible text never decodes.
      3. Final fallback: scan the whole rendered DOM with the generic
         extractor (catches the legacy ``info/<id>.htm`` plain-text
         template).
    """
    # The Ot-ctact block contains multiple <span _tsites_encrypt_field>;
    # most pages also render a fully-decoded ``.email`` or
    # ``a[href^=mailto]`` once JS settles. Probe the more specific
    # selectors first so we don't accidentally pick up the "邮编"/"地址"
    # spans that share the encrypt-field marker.
    candidate_selectors = (
        "div.Ot-ctact a[href^='mailto:']",
        "div.Ot-ctact .email",
        "div.Ot-ctact span[_tsites_encrypt_field]",
        "div.Ot-ctact",
    )
    # HUST's ImageScale.addimg is fired from <script> at the end of <body>
    # but the decrypt loop is debounced; we give it ~25s (xidian needs 15s,
    # HUST routinely takes 8-12s but a stressed VPS sometimes lags into
    # the 15-20s range).
    hust_timeout_ms = 25_000
    for sel in candidate_selectors:
        decoded = await _wait_for_decoded_span(page, sel, timeout_ms=hust_timeout_ms)
        if not decoded:
            continue
        candidates = _EMAIL_RE.findall(decoded)
        picked = _pick_email(candidates, domain_hint="hust.edu.cn")
        if picked:
            return picked
        # First selector that yielded *something* but no clean address —
        # don't bother re-waiting on the broader selectors, fall through
        # to the mailto + DOM scan below.
        break

    # Many HUST profiles wrap the email in a real mailto link even when
    # the rendered text stays encrypted. Cheap to probe.
    mailto = await _extract_mailto(page)
    if mailto and "hust.edu.cn" in mailto:
        return mailto
    # Even if domain doesn't match (e.g. teacher uses a gmail account), the
    # mailto link is still authoritative — accept it as a fallback.
    if mailto:
        return mailto

    # HUST sometimes hosts the email in plain text on the legacy
    # <school>.hust.edu.cn/info/<treeid>/<id>.htm template. Longer settle
    # since the lazy fill may still be running.
    return await extract_email_from_rendered_dom(
        page, domain_hint="hust.edu.cn", settle_ms=3000
    )


__all__ = [
    "_extract_mailto",
    "decode_hust_email",
    "decode_xidian_email",
    "decrypt_tsites_hex",
    "extract_email_from_rendered_dom",
    "_XJTU_FOOTER_PREFIXES",
    "_is_xjtu_footer",
]
