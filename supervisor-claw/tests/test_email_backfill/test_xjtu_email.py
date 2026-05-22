"""Tests for :mod:`claw.enrichers.sites.xjtu_email`.

These tests drive the public :func:`find_email` entry point against a
minimal FakePage / FakeSess pair so we never need real Playwright or
network access. We verify three scenarios that mirror the priority order
of the strategy cascade:

1. **wayback hit** — DBLP returns nothing, but a stealth ``wayback_fetch_html``
   monkey-patched onto the pipeline module returns an HTML blob containing
   the advisor's email. Expected: ``(email, "wayback")``.
2. **bing hit** — DBLP and wayback both miss; the stealth bing helper is
   monkey-patched to return an ``@xjtu.edu.cn`` email. Expected:
   ``(email, "bing")``.
3. **all miss** — every strategy returns ``None`` / empty. Expected:
   ``(None, None)`` without raising.

The DBLP path is also exercised indirectly: we monkey-patch the helper to
return ``None`` in cases 1 / 2 so we know wayback / bing are reached, and
we patch it to return a value in a fourth test to verify the short-circuit.
"""

from __future__ import annotations

import types
from typing import Optional

import pytest

from claw.enrichers.sites import xjtu_email as mod


class FakePage:
    """Tiny stand-in for a Playwright ``Page``.

    The xjtu_email module only calls ``goto`` / ``content`` /
    ``wait_for_load_state`` on a real page, and only inside helpers we
    plan to monkey-patch in these tests, so the bodies here are minimal.
    """

    def __init__(self) -> None:
        self.last_url: Optional[str] = None
        self._html = ""

    async def goto(self, url, **_kw):  # noqa: ANN001
        self.last_url = url
        return None

    async def content(self) -> str:
        return self._html

    async def wait_for_load_state(self, *_a, **_kw):  # noqa: ANN001
        return None

    async def evaluate(self, *_a, **_kw):  # noqa: ANN001
        return ""


class FakeSess:
    """Stand-in for ``httpx.AsyncClient`` — we never hit it directly in
    these tests because ``dblp_email_lookup`` is monkey-patched."""

    async def get(self, *_a, **_kw):  # noqa: ANN001 - generic
        raise AssertionError("FakeSess.get must not be called; monkey-patch dblp_email_lookup")


class _Advisor:
    """Minimal duck-typed Advisor for tests (real db.Advisor needs an engine)."""

    def __init__(
        self,
        name_cn: str,
        homepage: Optional[str] = None,
        source_url: Optional[str] = None,
        email: Optional[str] = None,
    ) -> None:
        self.name_cn = name_cn
        self.homepage = homepage
        self.source_url = source_url
        self.email = email


# ---------------------------------------------------------------------------
# 1. wayback hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_email_wayback_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    advisor = _Advisor(
        name_cn="张三",
        homepage="https://cs.xjtu.edu.cn/info/1024/12345.htm",
    )

    # 1. DBLP miss
    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "dblp_email_lookup", fake_dblp)

    # 2. wayback path: monkey-patch the lazy-imported pipeline.
    fake_pipeline = types.ModuleType("claw.pipeline.stealth_crawler")

    class _FakeSession:
        pass

    import contextlib as _ctx

    @_ctx.asynccontextmanager
    async def fake_open_stealth_session(headed: bool = False):
        yield _FakeSession()

    async def fake_wayback_fetch_html(sess_obj, url):  # noqa: ANN001
        # Snapshot includes the advisor's email in plain text + a footer
        # office address that must be filtered out.
        return (
            "<html><body>"
            "<p>张三, 教授, 邮箱: zhangsan@xjtu.edu.cn 电话: 029-1234</p>"
            "<footer>cs-office@xjtu.edu.cn</footer>"
            "</body></html>"
        )

    fake_pipeline.open_stealth_session = fake_open_stealth_session
    fake_pipeline.wayback_fetch_html = fake_wayback_fetch_html

    import sys

    monkeypatch.setitem(sys.modules, "claw.pipeline.stealth_crawler", fake_pipeline)

    page = FakePage()
    sess = FakeSess()

    email, source = await mod.find_email(advisor, page, sess, "西安交通大学")
    assert email == "zhangsan@xjtu.edu.cn"
    assert source == "wayback"


# ---------------------------------------------------------------------------
# 2. bing hit (wayback also miss; ensures cascade order)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_email_bing_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    advisor = _Advisor(
        name_cn="李四",
        # Use a non-XJTU host so the wayback gate trips closed without
        # needing to monkey-patch the pipeline module.
        homepage="https://example.com/lisi",
    )

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        return None

    async def fake_bing(page, name, school_name_cn, domain_hint=None):  # noqa: ANN001
        assert "xjtu" in (domain_hint or "")
        assert name == "李四"
        return "lisi@xjtu.edu.cn"

    monkeypatch.setattr(mod, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(mod, "search_email_via_stealth_bing", fake_bing)

    page = FakePage()
    sess = FakeSess()

    email, source = await mod.find_email(advisor, page, sess, "西安交通大学")
    assert email == "lisi@xjtu.edu.cn"
    assert source == "bing"


# ---------------------------------------------------------------------------
# 3. dblp short-circuit hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_email_dblp_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    advisor = _Advisor(
        name_cn="王五",
        homepage="https://cs.xjtu.edu.cn/info/1024/99999.htm",
    )

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        return "wangwu@xjtu.edu.cn"

    called: dict[str, bool] = {"bing": False, "wayback": False}

    async def trip_bing(*_a, **_kw):  # noqa: ANN001
        called["bing"] = True
        return "should-not-be-used@xjtu.edu.cn"

    monkeypatch.setattr(mod, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(mod, "search_email_via_stealth_bing", trip_bing)

    page = FakePage()
    sess = FakeSess()

    email, source = await mod.find_email(advisor, page, sess, "西安交通大学")
    assert email == "wangwu@xjtu.edu.cn"
    assert source == "dblp"
    # cascade short-circuited: bing must not have been reached
    assert called["bing"] is False


# ---------------------------------------------------------------------------
# 4. all miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_email_all_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    advisor = _Advisor(
        name_cn="赵六",
        # No homepage at all — wayback and profile-refetch gates close.
        homepage=None,
        source_url=None,
    )

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        return None

    async def fake_bing(*_a, **_kw):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(mod, "search_email_via_stealth_bing", fake_bing)

    page = FakePage()
    sess = FakeSess()

    email, source = await mod.find_email(advisor, page, sess, "西安交通大学")
    assert email is None
    assert source is None


# ---------------------------------------------------------------------------
# 5. xjtu-footer filter (regression — never write cs-office / ai-info)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_email_rejects_xjtu_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    """If bing only finds ``cs-office@xjtu.edu.cn`` we must NOT accept it
    even though it matches the domain hint."""
    advisor = _Advisor(name_cn="孙七", homepage=None)

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        return None

    async def fake_bing(*_a, **_kw):  # noqa: ANN001
        return "cs-office@xjtu.edu.cn"

    monkeypatch.setattr(mod, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(mod, "search_email_via_stealth_bing", fake_bing)

    page = FakePage()
    sess = FakeSess()

    email, source = await mod.find_email(advisor, page, sess, "西安交通大学")
    assert email is None
    assert source is None
