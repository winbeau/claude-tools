"""Tests for the Nankai University adapter (v0.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.nankai import NankaiAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nankai"


@pytest.fixture
def adapter() -> NankaiAdapter:
    return NankaiAdapter()


# ---------------------------------------------------------------------------
# parse_list — per-department
# ---------------------------------------------------------------------------


# (fixture filename, list URL, expected min items, expected min email-coverage)
LIST_CASES = [
    # cc.nankai (cs college) — img-card grid, 3 ranks
    ("list_cs_prof.html", "https://cc.nankai.edu.cn/jswyjy/list.htm", 40, 0.85),
    ("list_cs_assoc.html", "https://cc.nankai.edu.cn/fjswfyjy/list.htm", 40, 0.85),
    ("list_cs_lect.html", "https://cc.nankai.edu.cn/js/list.htm", 10, 0.85),
    # cyber.nankai (cse college) — same img-card template
    ("list_cyber_all.html", "https://cyber.nankai.edu.cn/13336/list.htm", 25, 0.80),
    ("list_cyber_prof.html", "https://cyber.nankai.edu.cn/jswyjy/list.htm", 25, 0.80),
    # ai.nankai — table rows (no email on list page)
    ("list_ai_prof.html", "https://ai.nankai.edu.cn/szdw/js_yjy_.htm", 30, 0.0),
    ("list_ai_assoc.html", "https://ai.nankai.edu.cn/szdw/fjs_fyjy_.htm", 20, 0.0),
    ("list_ai_lect.html", "https://ai.nankai.edu.cn/szdw/j_s.htm", 5, 0.0),
    # cs.nankai (sw college) — item cards, paginated (no email on list page)
    ("list_sw_prof.html", "https://cs.nankai.edu.cn/szdw/js.htm", 5, 0.0),
    ("list_sw_assoc.html", "https://cs.nankai.edu.cn/szdw/fjs.htm", 5, 0.0),
    ("list_sw_lect.html", "https://cs.nankai.edu.cn/szdw/js2.htm", 5, 0.0),
]


@pytest.mark.parametrize("fname,url,min_items,min_email_frac", LIST_CASES)
def test_parse_list_per_dept(
    adapter: NankaiAdapter,
    fname: str,
    url: str,
    min_items: int,
    min_email_frac: float,
) -> None:
    html = (FIX / fname).read_text(encoding="utf-8")
    items = adapter.parse_list(html, url)
    assert len(items) >= min_items, f"{fname}: only got {len(items)} items"
    # Every entry must have a non-empty name and an absolute profile_url.
    for it in items:
        assert it.name_cn and it.name_cn.strip(), f"{fname}: empty name"
        assert it.profile_url and it.profile_url.startswith("http"), (
            f"{fname}: bad profile_url {it.profile_url!r}"
        )
    if min_email_frac > 0:
        with_email = [it for it in items if it.email]
        frac = len(with_email) / len(items) if items else 0.0
        assert frac >= min_email_frac, (
            f"{fname}: email coverage {frac:.0%} < {min_email_frac:.0%}"
        )
    # No duplicate profile URLs in a single page.
    urls = [it.profile_url for it in items]
    assert len(set(urls)) == len(urls), f"{fname}: duplicate profile URLs"


def test_parse_list_routes_by_host(adapter: NankaiAdapter) -> None:
    """All four hosts return a non-empty list when fed their own fixture."""
    pairs = [
        ("list_cs_prof.html", "https://cc.nankai.edu.cn/jswyjy/list.htm"),
        ("list_cyber_prof.html", "https://cyber.nankai.edu.cn/jswyjy/list.htm"),
        ("list_ai_prof.html", "https://ai.nankai.edu.cn/szdw/js_yjy_.htm"),
        ("list_sw_prof.html", "https://cs.nankai.edu.cn/szdw/js.htm"),
    ]
    for fname, url in pairs:
        html = (FIX / fname).read_text(encoding="utf-8")
        items = adapter.parse_list(html, url)
        assert items, f"{fname} -> empty list (routing failed?)"


# ---------------------------------------------------------------------------
# parse_profile — no JS / no nav contamination
# ---------------------------------------------------------------------------


# (fixture filename, profile URL, name, title, expect_email, expect_research)
PROFILE_CASES = [
    (
        "profile_cs_chengmm.html",
        "https://cc.nankai.edu.cn/2021/0323/c13619a548889/page.htm",
        "程明明",
        "教授",
        False,  # cc.nankai has no email line on this profile (only on list page)
        True,
    ),
    (
        "profile_cs_chensen.html",
        "https://cc.nankai.edu.cn/2021/0323/c13619a569225/page.htm",
        "陈森",
        "教授",
        False,
        True,
    ),
    (
        "profile_cyber_chensen.html",
        "https://cyber.nankai.edu.cn/2021/0323/c13838a569226/page.htm",
        "陈森",
        "教授",
        False,
        True,
    ),
    (
        "profile_ai_chenfei.html",
        "https://ai.nankai.edu.cn/info/1232/5761.htm",
        "陈飞",
        "教授",
        True,
        True,
    ),
    (
        "profile_ai_chenzengqiang.html",
        "https://ai.nankai.edu.cn/info/1033/2799.htm",
        "陈增强",
        "教授",
        True,
        True,
    ),
    (
        "profile_sw_zhanghn.html",
        "https://cs.nankai.edu.cn/info/1084/1127.htm",
        "张海宁",
        "讲席教授",
        True,
        True,
    ),
    (
        "profile_sw_tianjun.html",
        "https://cs.nankai.edu.cn/info/1140/2585.htm",
        "田军",
        "英才教授",
        True,
        True,
    ),
]


# Nav text that should NEVER leak into bio_text or raw_quota_text.
_NAV_FORBIDDEN = (
    "首页",
    "学院概况",
    "学院领导",
    "组织机构",
    "对外合作",
    "工会组织",
    "规章制度",
)


@pytest.mark.parametrize(
    "fname,url,name,title,expect_email,expect_research", PROFILE_CASES
)
def test_parse_profile_no_nav(
    adapter: NankaiAdapter,
    fname: str,
    url: str,
    name: str,
    title: str,
    expect_email: bool,
    expect_research: bool,
) -> None:
    html = (FIX / fname).read_text(encoding="utf-8")
    li = ListItem(name_cn=name, profile_url=url, title=title)
    p = adapter.parse_profile(html, url, li)
    bio = p.bio_text or ""
    quota = p.raw_quota_text or ""
    # No JS or HTML tag leakage.
    assert "function" not in bio, f"{fname}: JS function leaked into bio"
    assert "<script" not in bio, f"{fname}: <script> tag leaked into bio"
    # No nav menu words in bio / quota.
    for nav in _NAV_FORBIDDEN:
        assert nav not in bio, f"{fname}: nav text {nav!r} in bio"
        assert nav not in quota, f"{fname}: nav text {nav!r} in quota"
    # Carry-forward from ListItem
    assert p.name_cn == name
    assert p.source_url == url
    # Research tags hygiene.
    for t in p.research_interests:
        assert 2 <= len(t) <= 25, f"{fname}: tag length out of range: {t!r}"
        assert not any(c in t for c in "。！？()（）"), (
            f"{fname}: noisy tag {t!r}"
        )


def test_research_or_bio_present(adapter: NankaiAdapter) -> None:
    """For every profile fixture, at least one of bio / research must be
    populated — otherwise the parser silently lost data."""
    for fname, url, name, title, _, expect_research in PROFILE_CASES:
        html = (FIX / fname).read_text(encoding="utf-8")
        li = ListItem(name_cn=name, profile_url=url, title=title)
        p = adapter.parse_profile(html, url, li)
        assert p.bio_text or p.research_interests, (
            f"{fname}: both bio_text and research_interests are empty"
        )
        if expect_research:
            assert p.research_interests, (
                f"{fname}: expected non-empty research_interests"
            )


def test_email_carries_from_list_item(adapter: NankaiAdapter) -> None:
    """When the list page provides an email, parse_profile must preserve it
    rather than re-extracting (and possibly losing it) from the profile body."""
    html = (FIX / "profile_cs_chensen.html").read_text(encoding="utf-8")
    li = ListItem(
        name_cn="陈森",
        profile_url="https://cc.nankai.edu.cn/2021/0323/c13619a569225/page.htm",
        title="教授",
        email="senchen@nankai.edu.cn",
    )
    p = adapter.parse_profile(
        html, "https://cc.nankai.edu.cn/2021/0323/c13619a569225/page.htm", li
    )
    assert p.email == "senchen@nankai.edu.cn"
    assert p.email_obfuscated is False
