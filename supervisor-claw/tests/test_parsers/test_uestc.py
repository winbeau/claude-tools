"""Tests for the UESTC adapter.

Fixtures were captured 2026-05 from www.sice.uestc.edu.cn (the only UESTC
school sub-domain reachable without a JS-challenge solve). See
``src/claw/adapters/uestc.py`` for site-specific notes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.uestc import UestcAdapter, _is_ts_challenge

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "uestc"

LIST_URL_JS = "https://www.sice.uestc.edu.cn/szdw/jsml1/js.htm"
LIST_URL_FJS = "https://www.sice.uestc.edu.cn/szdw/jsml1/fjs.htm"
LIST_URL_YJY = "https://www.sice.uestc.edu.cn/szdw/jsml1/yjy.htm"

PROFILE_URL_1 = "https://www.sice.uestc.edu.cn/info/1450/15583.htm"
PROFILE_URL_2 = "https://www.sice.uestc.edu.cn/info/1451/13753.htm"


@pytest.fixture
def adapter() -> UestcAdapter:
    return UestcAdapter()


# ---------------------------------------------------------------------------
# List parsing
# ---------------------------------------------------------------------------


def test_parse_list_js_bucket(adapter: UestcAdapter) -> None:
    """教授 list page — sice 信通 has ~112 教授."""
    html = (FIX / "list_ai_js.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_JS)
    assert len(items) >= 100, f"expected >=100 教授, got {len(items)}"
    # Names: 100%
    assert all(it.name_cn for it in items)
    assert all(it.profile_url for it in items)
    # All profile URLs are absolute now.
    assert all(it.profile_url and it.profile_url.startswith("http") for it in items)
    # Email coverage: sice exposes 邮箱 in the JSON; expect ≥ 80%.
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.8, (
        f"email coverage too low: {len(with_email)}/{len(items)}"
    )
    # All emails should be lowercase + contain '@'.
    for it in with_email:
        assert it.email and "@" in it.email
        assert it.email == it.email.lower()
    # Title fields populated (zc=职称) for 教授 bucket — every entry
    # should carry "教授" or a faculty-keyword title.
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.9


def test_parse_list_fjs_bucket(adapter: UestcAdapter) -> None:
    html = (FIX / "list_ai_fjs.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_FJS)
    assert len(items) >= 60, f"expected >=60 副教授, got {len(items)}"
    assert all(it.name_cn for it in items)
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.8


def test_parse_list_yjy_bucket(adapter: UestcAdapter) -> None:
    html = (FIX / "list_ai_yjy.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_YJY)
    # 研究员 bucket is small (~18) but should be non-empty.
    assert len(items) >= 10


def test_parse_list_dedup(adapter: UestcAdapter) -> None:
    """Same fixture twice must not duplicate items in *one* call."""
    html = (FIX / "list_ai_js.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_JS)
    urls = {it.profile_url for it in items}
    assert len(urls) == len(items), "unexpected duplicate profile URLs"


# ---------------------------------------------------------------------------
# TS challenge handling
# ---------------------------------------------------------------------------


_TS_STUB = (
    '<!DOCTYPE html><html><head><meta id="sx6c1KC7YLcx" content="abc">'
    "<script>$_ts=window['$_ts'];if(!$_ts)$_ts={};$_ts.nsd=1;</script>"
    "</head><body></body></html>"
)


def test_is_ts_challenge_detects_stub() -> None:
    assert _is_ts_challenge(_TS_STUB)


def test_is_ts_challenge_false_on_real_list() -> None:
    html = (FIX / "list_ai_js.html").read_text(encoding="utf-8")
    assert not _is_ts_challenge(html)


def test_parse_list_returns_empty_on_ts_stub(adapter: UestcAdapter) -> None:
    items = adapter.parse_list(_TS_STUB, "https://www.scse.uestc.edu.cn/szdw/jsml1/js.htm")
    assert items == []


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------


def _profile_1_item() -> ListItem:
    return ListItem(name_cn="崔宗勇", profile_url=PROFILE_URL_1)


def _profile_2_item() -> ListItem:
    return ListItem(name_cn="安洪阳", profile_url=PROFILE_URL_2)


def test_parse_profile_no_js_no_nav(adapter: UestcAdapter) -> None:
    html = (FIX / "profile_sice_cuizongyong.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(html, PROFILE_URL_1, _profile_1_item())
    assert p.bio_text, "expected non-empty bio"
    assert "function" not in p.bio_text
    assert "<script>" not in p.bio_text
    assert "$_ts" not in p.bio_text
    # Navigation lines like 学院概况 / 党建工作 must not leak into the bio.
    for nav in ("学院概况", "党建工作", "通知公告", "院友之家", "公共服务"):
        assert nav not in p.bio_text, f"nav text {nav!r} leaked into bio"
    if p.raw_quota_text:
        for nav in ("党建工作", "院友之家"):
            assert nav not in p.raw_quota_text


def test_parse_profile_has_research_or_bio(adapter: UestcAdapter) -> None:
    html = (FIX / "profile_sice_cuizongyong.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(html, PROFILE_URL_1, _profile_1_item())
    assert p.bio_text or p.research_interests, (
        "expected at least one of bio_text / research_interests to be non-empty"
    )
    # Tag sanity for the ones we found.
    if p.research_interests:
        assert all(
            2 <= len(t) <= 25 and not any(c in t for c in "()（）")
            for t in p.research_interests
        ), f"bad research tags: {p.research_interests}"


def test_parse_profile_extracts_email_and_title(adapter: UestcAdapter) -> None:
    html = (FIX / "profile_sice_cuizongyong.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(html, PROFILE_URL_1, _profile_1_item())
    assert p.email == "zycui@uestc.edu.cn"
    assert p.title and "教授" in p.title
    assert p.homepage == PROFILE_URL_1
    assert p.source_url == PROFILE_URL_1
    # Photo URL absolutised
    assert p.photo_url and p.photo_url.startswith("http")


def test_parse_profile_second_sample(adapter: UestcAdapter) -> None:
    """副教授 profile — checks the parser is not brittle to one specific page."""
    html = (FIX / "profile_sice_anhongyang.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(html, PROFILE_URL_2, _profile_2_item())
    assert p.email == "hongyang_an@uestc.edu.cn"
    assert p.title and "副教授" in p.title
    assert p.bio_text


def test_parse_profile_research_tags(adapter: UestcAdapter) -> None:
    """The bio text contains '研究方向：SAR图像智能解译、雷达目标识别等' —
    the adapter should split that into a clean tag list."""
    html = (FIX / "profile_sice_cuizongyong.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(html, PROFILE_URL_1, _profile_1_item())
    # The exact wording may shift on the live site, but at least one
    # tag should be present from this profile.
    assert p.research_interests
    joined = "/".join(p.research_interests)
    assert "SAR" in joined or "雷达" in joined or "成像" in joined


def test_parse_profile_returns_stub_on_ts_challenge(adapter: UestcAdapter) -> None:
    """When parse_profile receives the TS stub, it should not crash; it
    should return the partial seeded from list_item only."""
    item = ListItem(
        name_cn="某老师",
        profile_url="https://www.scse.uestc.edu.cn/info/1234/5678.htm",
        title="教授",
        email="someone@uestc.edu.cn",
    )
    p = adapter.parse_profile(
        _TS_STUB, "https://www.scse.uestc.edu.cn/info/1234/5678.htm", item
    )
    assert p.name_cn == "某老师"
    assert p.title == "教授"
    assert p.email == "someone@uestc.edu.cn"
    assert p.bio_text is None
    assert p.research_interests == []
