"""Tests for the improved HUST email decoder + mailto helper.

These tests cover the three failure modes the R1 plan flagged:

a. The standard ``Ot-ctact`` decryption path — JS settles, decoded text
   contains the real personal email.
b. The fallback path where the encrypted span never decodes but the page
   carries an ``<a href="mailto:…">`` link with the same address. Common
   on the legacy ``info/<treeid>/<id>.htm`` HUST sub-site template.
c. The negative path: page only exposes the institute-level footer alias
   (``scs@hust.edu.cn``) — we must filter it and return ``None`` instead
   of mistaking the footer for a personal email.

We drive everything with a hand-rolled ``FakePage`` that implements the
small async surface the decoder + ``_extract_mailto`` touch:

* ``content()``        — returns the current rendered HTML
* ``evaluate(js, *a)`` — supports the body-innerText probe, the
  wait-for-function decode probe (``querySelectorAll(sel)`` walk), and
  the mailto sniffer (``a[href^="mailto:"]`` extraction).
* ``wait_for_function(js, arg, timeout)`` — simulates the lazy JS
  decryption by splicing the seeded decoded text into the encrypted
  span on the first invocation.
"""

from __future__ import annotations

import re

import pytest
from selectolax.parser import HTMLParser

from claw.core.email_decoders import _extract_mailto, decode_hust_email


# ---------------------------------------------------------------------------
# Fake Playwright page
# ---------------------------------------------------------------------------


class FakePage:
    """Stand-in for ``playwright.async_api.Page``.

    The minimal contract is: HTML in, HTML out, with a one-shot
    ``wait_for_function`` that "decrypts" encrypted spans by replacing
    their text with the seeded plaintext. Everything else is plain
    selectolax parsing.
    """

    def __init__(self, html: str, decoded_email: str | None = None) -> None:
        self._html = html
        self._decoded_email = decoded_email
        self._decoded = False

    async def content(self) -> str:
        return self._html

    async def evaluate(self, js: str, *args):  # noqa: ANN001 - generic
        # 1) body.innerText probe
        if "document.body" in js and "innerText" in js:
            tree = HTMLParser(self._html)
            body = tree.css_first("body")
            return body.text(strip=False) if body else ""

        # 2) mailto extraction probe (Array.from(querySelectorAll('a[href^=mailto:]'))…)
        if "mailto" in js and "querySelectorAll" in js:
            tree = HTMLParser(self._html)
            out: list[str] = []
            for a in tree.css('a[href^="mailto:"]'):
                href = a.attributes.get("href") or ""
                out.append(href)
            return out

        # 3) wait_for_function decode probe — also called via evaluate(js, sel)
        if "querySelectorAll" in js and args:
            sel = args[0]
            tree = HTMLParser(self._html)
            for node in tree.css(sel):
                text = node.text(strip=True)
                if "@" in text and "." in text.split("@", 1)[1]:
                    return text
            return False
        return None

    async def wait_for_function(self, js: str, arg=None, timeout: int = 0):  # noqa: ANN001
        # Simulate the lazy decryption by replacing the first encrypt-field
        # span (typically the email span — fixtures place it after the
        # "邮箱：" label).
        if not self._decoded and self._decoded_email:
            pattern = re.compile(
                r'(邮箱[^<]*<span[^>]*_tsites_encrypt_field[^>]*>)[^<]*(</span>)',
                re.IGNORECASE,
            )
            new_html, n = pattern.subn(
                r"\g<1>" + self._decoded_email + r"\g<2>",
                self._html,
                count=1,
            )
            if n == 0:
                new_html = re.sub(
                    r'(<span[^>]*_tsites_encrypt_field[^>]*>)[^<]*(</span>)',
                    r"\g<1>" + self._decoded_email + r"\g<2>",
                    self._html,
                    count=1,
                )
            self._html = new_html
            self._decoded = True
            return True
        # Nothing to decode — Playwright would raise on timeout.
        raise TimeoutError("FakePage: nothing to decode for selector")


# ---------------------------------------------------------------------------
# Fixtures (small, self-contained snippets that exercise each branch)
# ---------------------------------------------------------------------------


_HUST_OT_CTACT_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>测试教师</title></head><body>
<div class="blockwhite Ot-ctact">
  <h2>其他联系方式</h2>
  <p>·  邮箱：<span _tsites_encrypt_field="_tsites_encrypt_field" style="display:none;">aabbccdd11223344</span></p>
</div>
</body></html>
"""


_HUST_MAILTO_ONLY_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>测试教师 - 计算机学院</title></head><body>
<div class="v_news_content">
  <h1>王测试</h1>
  <p>职称：副教授</p>
  <p>邮箱：<a href="mailto:wang.test@hust.edu.cn">wang.test@hust.edu.cn</a></p>
</div>
</body></html>
"""


_HUST_FOOTER_ONLY_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>测试教师</title></head><body>
<div class="blockwhite Psl-info">
  <h1>张某某</h1>
  <p>职称：教授</p>
</div>
<footer>
  联系学院：<a href="mailto:scs@hust.edu.cn">scs@hust.edu.cn</a>
  <p>院办公室邮箱：scs@hust.edu.cn</p>
</footer>
</body></html>
"""


# ---------------------------------------------------------------------------
# Case a) standard Ot-ctact decryption path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hust_decoder_reads_decrypted_ot_ctact() -> None:
    page = FakePage(_HUST_OT_CTACT_HTML, decoded_email="prof@hust.edu.cn")
    email = await decode_hust_email(page)
    assert email == "prof@hust.edu.cn"


# ---------------------------------------------------------------------------
# Case b) mailto-only fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hust_decoder_falls_back_to_mailto() -> None:
    # No encrypted spans — wait_for_function will time out for every
    # selector. The mailto-extraction branch must pick the address up.
    page = FakePage(_HUST_MAILTO_ONLY_HTML, decoded_email=None)
    email = await decode_hust_email(page)
    assert email == "wang.test@hust.edu.cn"


@pytest.mark.asyncio
async def test_extract_mailto_helper_picks_first_plausible() -> None:
    html = (
        '<html><body>'
        '<a href="mailto:webmaster@hust.edu.cn">webmaster</a>'
        '<a href="mailto:zhang@hust.edu.cn?subject=hi">contact me</a>'
        '</body></html>'
    )
    page = FakePage(html)
    addr = await _extract_mailto(page)
    # webmaster is filtered as footer-like; the subject param is stripped.
    assert addr == "zhang@hust.edu.cn"


# ---------------------------------------------------------------------------
# Case c) footer-only page → must return None, never the institute alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hust_decoder_filters_footer_only_address() -> None:
    page = FakePage(_HUST_FOOTER_ONLY_HTML, decoded_email=None)
    email = await decode_hust_email(page)
    assert email is None, f"expected None for footer-only page, got {email!r}"
