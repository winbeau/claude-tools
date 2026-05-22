"""Tests for ``claw.enrichers.sites.nwpu_email.find_email``.

NWPU's TS-WAF wall makes the generic ``js`` strategy permanently useless,
so the site module runs:

    wayback → stealth bing → dblp

We can't drive real Playwright + a live ``httpx.AsyncClient`` against
archive.org / bing.com in CI on the VPS, so each test wires up a tiny
async fake for the two collaborators:

* ``FakeSess``  — mimics ``httpx.AsyncClient.get(url)`` with a routed
  response dict. Wayback availability JSON, wayback raw HTML, and DBLP
  endpoints are routed by URL prefix.
* ``FakePage`` — mimics the minimum Playwright surface used by
  :func:`search_email_via_stealth_bing` (``goto`` / ``content`` /
  ``wait_for_load_state``).

Each test asserts both the returned email and the source string so a
regression in tier ordering is caught.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from claw.enrichers.sites.nwpu_email import find_email


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, *, status: int = 200, text: str = "", payload: Any = None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):  # noqa: ANN201 - mimics httpx
        return self._payload


class FakeSess:
    """``httpx.AsyncClient`` stand-in.

    Build it with a list of (url_substring, _FakeResponse) tuples. Each
    ``get`` returns the first response whose substring is in the URL. If
    nothing matches, returns an empty 200 response. Records every URL it
    was asked for on ``self.urls`` so tests can assert call ordering.
    """

    def __init__(self, routes: list[tuple[str, _FakeResponse]] | None = None) -> None:
        self.routes: list[tuple[str, _FakeResponse]] = routes or []
        self.urls: list[str] = []

    async def get(self, url: str, timeout: float | None = None):  # noqa: ARG002
        self.urls.append(url)
        for substr, resp in self.routes:
            if substr in url:
                return resp
        return _FakeResponse(status=200, text="", payload={})


class FakePage:
    """Minimum Playwright page surface for stealth-bing search.

    On ``goto`` we record the URL. ``content()`` returns whatever HTML the
    test seeded — by default empty, but the second test seeds a bing-style
    SERP with an embedded NWPU email so the bing tier hits.
    """

    def __init__(self, html_by_url: dict[str, str] | None = None) -> None:
        self.html_by_url = html_by_url or {}
        self.current_url: str | None = None
        self.goto_calls: list[str] = []

    async def goto(self, url: str, timeout: int = 0, wait_until: str = "") -> None:  # noqa: ARG002
        self.current_url = url
        self.goto_calls.append(url)

    async def wait_for_load_state(self, state: str, timeout: int = 0) -> None:  # noqa: ARG002
        return None

    async def content(self) -> str:
        # Return the seeded HTML for the most recent URL, or the empty
        # string by default.
        if self.current_url is None:
            return ""
        for substr, html in self.html_by_url.items():
            if substr in self.current_url:
                return html
        return ""

    async def evaluate(self, *_args, **_kwargs) -> Any:  # noqa: ANN401
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_advisor(name: str = "张三", homepage: str = "https://teacher.nwpu.edu.cn/2026010012") -> Any:
    # ``find_email`` only reads ``name_cn`` / ``homepage`` / ``source_url``
    # off the advisor, so a SimpleNamespace is sufficient.
    return SimpleNamespace(
        id=1,
        name_cn=name,
        homepage=homepage,
        source_url=homepage,
        email=None,
    )


_WAYBACK_OK_PAYLOAD = {
    "archived_snapshots": {
        "closest": {
            "available": True,
            "url": "http://web.archive.org/web/20210101000000/https://teacher.nwpu.edu.cn/2026010012",
            "status": "200",
            "timestamp": "20210101000000",
        }
    }
}

_WAYBACK_MISS_PAYLOAD: dict[str, Any] = {"archived_snapshots": {}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nwpu_wayback_hit_returns_email() -> None:
    """Tier 1 success: wayback snapshot returns HTML with a plain-text email."""
    snapshot_html = (
        "<html><body>"
        "<h1>张三 教授</h1>"
        "<p>邮箱：zhang.san@nwpu.edu.cn</p>"
        "</body></html>"
    )
    routes = [
        ("archive.org/wayback/available", _FakeResponse(payload=_WAYBACK_OK_PAYLOAD)),
        ("web.archive.org/web/", _FakeResponse(text=snapshot_html)),
    ]
    sess = FakeSess(routes=routes)
    page = FakePage()

    email, source = await find_email(
        _fake_advisor(),
        page,
        sess,
        school_name_cn="西北工业大学",
    )

    assert email == "zhang.san@nwpu.edu.cn"
    assert source == "wayback"
    # Bing tier must not have been touched.
    assert page.goto_calls == [], "bing tier should not have run after wayback hit"


@pytest.mark.asyncio
async def test_nwpu_wayback_miss_then_bing_hit() -> None:
    """Tier 1 miss (wayback 404) → tier 2 bing SERP carries the email."""
    bing_serp = (
        "<html><body><ol id='b_results'>"
        "<li>Some unrelated result foo@bar.com</li>"
        "<li>Prof Zhang San — homepage at NWPU, email: zhang.san@nwpu.edu.cn</li>"
        "</ol></body></html>"
    )
    routes = [
        # wayback availability says "not archived"
        ("archive.org/wayback/available", _FakeResponse(payload=_WAYBACK_MISS_PAYLOAD)),
        # dblp returns nothing useful — but we should never reach it
        ("dblp.org/search/author/api", _FakeResponse(payload={"result": {}})),
    ]
    sess = FakeSess(routes=routes)
    page = FakePage(html_by_url={"bing.com": bing_serp})

    email, source = await find_email(
        _fake_advisor(),
        page,
        sess,
        school_name_cn="西北工业大学",
    )

    assert email == "zhang.san@nwpu.edu.cn"
    assert source == "bing"
    # Wayback availability must have been queried first
    assert any("archive.org/wayback/available" in u for u in sess.urls), \
        "wayback availability must be checked before bing"
    # And bing must have been visited
    assert any("bing.com" in u for u in page.goto_calls), "bing tier did not run"
    # dblp must NOT have been touched since bing succeeded
    assert not any("dblp.org" in u for u in sess.urls), "dblp ran despite bing hit"


@pytest.mark.asyncio
async def test_nwpu_all_tiers_miss_returns_none() -> None:
    """All three tiers come up empty → returns (None, None) cleanly."""
    routes = [
        ("archive.org/wayback/available", _FakeResponse(payload=_WAYBACK_MISS_PAYLOAD)),
        ("dblp.org/search/author/api", _FakeResponse(payload={"result": {"hits": {}}})),
    ]
    sess = FakeSess(routes=routes)
    # Empty HTML on every search engine
    page = FakePage(html_by_url={})

    email, source = await find_email(
        _fake_advisor(),
        page,
        sess,
        school_name_cn="西北工业大学",
    )

    assert email is None
    assert source is None
