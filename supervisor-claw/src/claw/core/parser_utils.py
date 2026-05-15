"""HTML helpers + email deobfuscation."""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# variants like "name [at] tsinghua [dot] edu [dot] cn", "name(at)x.edu.cn"
_AT = r"(?:\s*[\[\(]?\s*(?:at|AT|@)\s*[\]\)]?\s*)"
_DOT = r"(?:\s*[\[\(]?\s*(?:dot|DOT|点|\.)\s*[\]\)]?\s*)"
_OBFUSCATED_RE = re.compile(
    rf"([A-Za-z0-9._%+\-]+){_AT}([A-Za-z0-9\-]+){_DOT}([A-Za-z0-9\-]+){_DOT}([A-Za-z]{{2,}})"
    rf"(?:{_DOT}([A-Za-z]{{2,}}))?"
)


def parse(html: str) -> HTMLParser:
    return HTMLParser(html)


def text_of(node: Node | None) -> str:
    if node is None:
        return ""
    return (node.text(strip=True) or "").strip()


def extract_email(text: str) -> tuple[str | None, bool]:
    """Returns (email, was_obfuscated)."""
    if not text:
        return None, False
    m = _EMAIL_RE.search(text)
    if m:
        return m.group(0).lower(), False
    m2 = _OBFUSCATED_RE.search(text)
    if m2:
        parts = [g for g in m2.groups() if g]
        if len(parts) >= 4:
            local = parts[0]
            domain = ".".join(parts[1:])
            return f"{local}@{domain}".lower(), True
    return None, False


def absolutize(base_url: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base_url, href)


_RECRUIT_KEYWORDS = ("招生", "招收", "招募", "招博", "招硕", "recruit", "PhD position")


def find_recruit_paragraphs(text: str, window: int = 200) -> list[str]:
    """Return slices of `text` around recruitment keywords, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for kw in _RECRUIT_KEYWORDS:
        for m in re.finditer(re.escape(kw), text):
            i = m.start()
            chunk = text[max(0, i - window // 2) : i + window].strip()
            key = chunk[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(chunk)
    return out
