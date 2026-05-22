"""Xidian-specific email decoder tests.

Three required cases (per the v0.5 R1 xidian agent spec):

a) Browser-JS decode path: a fake page whose ``wait_for_function`` simulates
   tsitesencrypt.js populating the encrypted ``<span _tsites_encrypt_field>``
   with the plain-text email → ``decode_xidian_email`` returns it.

b) Encrypted span never decodes (timeout) → ``decode_xidian_email`` falls
   through to the generic DOM regex, which picks up a plain-text email
   from the bio prose.

c) Page with no email anywhere → returns ``None`` (never raises).

Plus a unit test for the pure-Python ``decrypt_tsites_hex`` covering both
the "lucky" XOR / plain-ASCII case and the typical "ciphered, returns None"
real-world case.

The FakePage here is a deliberately tiny copy of the one in
``test_decoders.py`` — we don't share it because the xidian test asserts
slightly different content shapes (bio prose with a plain email).
"""

from __future__ import annotations

import re

import pytest
from selectolax.parser import HTMLParser

from claw.core.email_decoders import decode_xidian_email, decrypt_tsites_hex


class FakePage:
    """Minimal stand-in for a Playwright ``Page``.

    ``decoded_email``: when non-None, the first ``wait_for_function`` call
    splices it into the first ``<span _tsites_encrypt_field>`` element to
    simulate tsitesencrypt.js decoding. When None, ``wait_for_function``
    raises TimeoutError (matches Playwright behaviour on timeout); the
    decoder swallows it and falls through.
    """

    def __init__(self, html: str, decoded_email: str | None) -> None:
        self._html = html
        self._decoded_email = decoded_email
        self._decoded = False

    async def content(self) -> str:
        return self._html

    async def evaluate(self, js: str, *args):  # noqa: ANN001 - generic
        # (a) raw blob list helper used by decode_xidian_email step 1
        if "querySelectorAll" in js and "_tsites_encrypt_field" in js and not args:
            tree = HTMLParser(self._html)
            out: list[str] = []
            for node in tree.css("span[_tsites_encrypt_field]"):
                t = (node.text(strip=False) or "").strip()
                if t:
                    out.append(t)
            return out

        # (b) body-innerText probe used by extract_email_from_rendered_dom
        if "document.body" in js and "innerText" in js:
            tree = HTMLParser(self._html)
            body = tree.css_first("body")
            return body.text(strip=False) if body else ""

        # (c) the wait-for-function probe (also re-invoked as evaluate-with-arg)
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
        if not self._decoded and self._decoded_email:
            # Replace the first encrypt-field span's content with the
            # plain-text email — mirrors what tsitesencrypt.js would do.
            new_html, n = re.subn(
                r'(<span[^>]*_tsites_encrypt_field[^>]*>)[^<]*(</span>)',
                lambda m: m.group(1) + self._decoded_email + m.group(2),
                self._html,
                count=1,
            )
            if n:
                self._html = new_html
                self._decoded = True
            return True
        # Mirror Playwright's TimeoutError on actual timeouts. The decoder
        # catches Exception and falls through.
        raise TimeoutError("FakePage: nothing to decode")


# ---------------------------------------------------------------------------
# Test fixtures (inline HTML so we don't need a fixture file per case)
# ---------------------------------------------------------------------------

# (a) Encrypted span eventually decoded by simulated browser JS.
_ENCRYPTED_PROFILE_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="/system/resource/tsites/tsitesencrypt.js"></script>
</head><body>
<div class="t_jbxx_nr">
  <p>邮箱 : <span class="encrypt-field" _tsites_encrypt_field>0520d838ac5b3db0544c91bd08fc4088</span></p>
  <p>办公地点 : 北校区主楼II-301</p>
</div>
<div class="t_grjj_nr"><p>这是一位教授的简介。</p></div>
</body></html>"""

# (b) Encrypted span that never decodes — but bio prose contains a plain email.
_BIO_PLAINTEXT_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<div class="t_jbxx_nr">
  <p>邮箱 : <span class="encrypt-field" _tsites_encrypt_field>aa00bb11cc22</span></p>
</div>
<div class="t_grjj_nr">
  <p>欢迎联系 abc@xidian.edu.cn 讨论合作。</p>
</div>
</body></html>"""

# (c) No email anywhere.
_EMPTY_PROFILE_HTML = """\
<!DOCTYPE html>
<html><body>
<div class="t_jbxx_nr"><p>办公地点 : 北校区主楼II-301</p></div>
<div class="t_grjj_nr"><p>这位老师没有公开邮箱。</p></div>
</body></html>"""


# ---------------------------------------------------------------------------
# Async decoder cases (a) / (b) / (c)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xidian_decoder_browser_path_returns_email() -> None:
    """(a) Simulated browser JS decodes the span → decoder returns the email."""
    page = FakePage(_ENCRYPTED_PROFILE_HTML, decoded_email="prof@xidian.edu.cn")
    email = await decode_xidian_email(page)
    assert email == "prof@xidian.edu.cn"


@pytest.mark.asyncio
async def test_xidian_decoder_falls_back_to_bio_plaintext() -> None:
    """(b) Span never decodes; bio prose has a plain xidian.edu.cn email."""
    page = FakePage(_BIO_PLAINTEXT_HTML, decoded_email=None)
    email = await decode_xidian_email(page)
    assert email == "abc@xidian.edu.cn"


@pytest.mark.asyncio
async def test_xidian_decoder_returns_none_when_no_email() -> None:
    """(c) Nothing to decode anywhere → None, never raises."""
    page = FakePage(_EMPTY_PROFILE_HTML, decoded_email=None)
    email = await decode_xidian_email(page)
    assert email is None


# ---------------------------------------------------------------------------
# Pure-Python decrypt_tsites_hex
# ---------------------------------------------------------------------------


def test_decrypt_tsites_hex_returns_none_on_real_ciphered_blob() -> None:
    """The real fixture blob is AES/SM4-ciphered → pure-Python returns None.

    Covers the documented "happy path of failure" where the trivial XOR
    sweep doesn't match and the caller must fall back to a real browser.
    """
    real_blob = (
        "0520d838ac5b3db0544c91bd08fc4088"
        "474b2015fd63d3f5a6ed75006b6aa482"
        "3266ea8eafb502e572f244ad4dcd2c00"
    )
    assert decrypt_tsites_hex(real_blob) is None


def test_decrypt_tsites_hex_returns_none_on_bad_input() -> None:
    """Empty / non-hex / odd-length input must return None, never raise."""
    assert decrypt_tsites_hex("") is None
    assert decrypt_tsites_hex(None) is None  # type: ignore[arg-type]
    assert decrypt_tsites_hex("not-hex!!") is None
    assert decrypt_tsites_hex("abc") is None  # odd length


def test_decrypt_tsites_hex_single_byte_xor_recovers_email() -> None:
    """Synthesised blob: XOR every byte of 'prof@xidian.edu.cn' with 0x42.

    Demonstrates that the single-byte XOR sweep handles the (uncommon)
    case where tsites was reduced to a trivial cipher.
    """
    plain = b"prof@xidian.edu.cn"
    key = 0x42
    cipher_hex = "".join(f"{b ^ key:02x}" for b in plain)
    out = decrypt_tsites_hex(cipher_hex)
    assert out == "prof@xidian.edu.cn"


def test_decrypt_tsites_hex_handles_plain_ascii_hex() -> None:
    """If a profile mistakenly ships hex-of-plaintext (key=0), decode it."""
    plain = b"abc@xidian.edu.cn"
    cipher_hex = plain.hex()
    out = decrypt_tsites_hex(cipher_hex)
    assert out == "abc@xidian.edu.cn"
