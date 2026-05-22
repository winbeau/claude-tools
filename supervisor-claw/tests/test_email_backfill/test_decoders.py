"""Tests for :mod:`claw.core.email_decoders`.

We can't reasonably spin up real Playwright + Chromium in CI here (the VPS
doesn't have browser binaries and we're not supposed to install them in this
phase). Instead, we drive the decoders with a tiny **FakePage** that
implements the small async surface our helpers actually call:

- ``content()`` — returns the current HTML string
- ``evaluate(js, *args)`` — executes a single hard-coded JS-like operation
  by inspecting the JS source string (we only need to support the few
  patterns ``email_decoders`` calls — innerText and the wait_for_function
  decode probe)
- ``wait_for_function(js, arg, timeout)`` — for the encryption-decode
  pattern. Our fake "decodes" the encrypted spans by replacing their
  textContent with the seeded plaintext email when this is called.

This is intentionally minimal but mirrors the real Playwright contract
closely enough that the helpers behave the same.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from claw.core.email_decoders import (
    decode_hust_email,
    decode_xidian_email,
    extract_email_from_rendered_dom,
)

FIXTURES = Path(__file__).parent / "fixtures"


class FakePage:
    """Stand-in for a Playwright ``Page``.

    On instantiation it holds the static encrypted HTML. The first call to
    ``wait_for_function`` simulates "the encryption JS finished running" —
    we splice ``decoded_email`` into the post-decode rendered HTML so that
    subsequent ``content()`` / ``evaluate('innerText')`` calls observe the
    plaintext, matching the real browser's behaviour.
    """

    def __init__(self, html: str, decoded_email: str | None) -> None:
        self._html = html
        self._decoded_email = decoded_email
        self._decoded = False

    async def content(self) -> str:
        return self._html

    async def evaluate(self, js: str, *args):  # noqa: ANN001 - generic
        # Helpers either ask for body innerText or run the wait-for-decode
        # probe. We only implement the two shapes we actually use.
        if "document.body" in js and "innerText" in js:
            tree = HTMLParser(self._html)
            body = tree.css_first("body")
            return body.text(strip=False) if body else ""

        # The wait-for-function probe (also used as evaluate-with-arg).
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
        # Simulate decryption: on first call, splice the decoded email into
        # the HTML by replacing the first encrypt-field span's content.
        if not self._decoded and self._decoded_email:
            # Find the first <span _tsites_encrypt_field>...</span> after a
            # "邮箱" / email marker, replace its inner content.
            import re

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
                # Fall back: replace the first encrypt-field span we see.
                new_html = re.sub(
                    r'(<span[^>]*_tsites_encrypt_field[^>]*>)[^<]*(</span>)',
                    r"\g<1>" + self._decoded_email + r"\g<2>",
                    self._html,
                    count=1,
                )
            self._html = new_html
            self._decoded = True
            return True
        # If there's nothing to decode, raise like Playwright does on
        # timeout. The decoder catches Exception.
        raise TimeoutError("FakePage: nothing to decode")


@pytest.mark.asyncio
async def test_decode_xidian_email_via_fake_page() -> None:
    html = (FIXTURES / "xidian_profile_encrypted.html").read_text(encoding="utf-8")
    page = FakePage(html, decoded_email="zhang.test@xidian.edu.cn")
    email = await decode_xidian_email(page)
    assert email == "zhang.test@xidian.edu.cn"


@pytest.mark.asyncio
async def test_decode_hust_email_via_fake_page() -> None:
    html = (FIXTURES / "hust_profile_encrypted.html").read_text(encoding="utf-8")
    page = FakePage(html, decoded_email="li.test@hust.edu.cn")
    email = await decode_hust_email(page)
    assert email == "li.test@hust.edu.cn"


@pytest.mark.asyncio
async def test_decoders_return_none_on_empty_page() -> None:
    """A page with no email anywhere must return None, never raise."""
    page = FakePage("<html><body><p>no email here</p></body></html>", decoded_email=None)
    assert await decode_xidian_email(page) is None
    assert await decode_hust_email(page) is None


@pytest.mark.asyncio
async def test_extract_email_from_rendered_dom_prefers_domain_hint() -> None:
    html = (
        "<html><body>"
        "<p>contact: foo@bar.com</p>"
        "<p>real: prof@xidian.edu.cn</p>"
        "</body></html>"
    )
    page = FakePage(html, decoded_email=None)
    email = await extract_email_from_rendered_dom(
        page, domain_hint="xidian.edu.cn", settle_ms=0
    )
    assert email == "prof@xidian.edu.cn"


@pytest.mark.asyncio
async def test_extract_email_skips_footer_addresses() -> None:
    html = (
        "<html><body>"
        "<footer>webmaster@xidian.edu.cn</footer>"
        "<p>info@xidian.edu.cn</p>"
        "<p>realprof@xidian.edu.cn</p>"
        "</body></html>"
    )
    page = FakePage(html, decoded_email=None)
    email = await extract_email_from_rendered_dom(
        page, domain_hint="xidian.edu.cn", settle_ms=0
    )
    assert email == "realprof@xidian.edu.cn"
