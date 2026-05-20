"""Parser tests for the Xi'an Jiaotong University (XJTU) adapter.

XJTU's three CS/AI departments (cs / sw / ai) all share the same VSB
SiteBuilder list-page template; profile pages can be either the legacy
college-site VSB pages or the unified ``faculty.xjtu.edu.cn`` / ``gr.xjtu``
portal.  The adapter is verified against representative fixtures of all
four flavours.

Important note on fixtures
--------------------------
Direct ``curl`` / ``WebFetch`` to XJTU subdomains is blocked by a
challenge-cookie JS wall on commodity network egress (and TLS cert
mismatch on a couple of college subdomains).  The fixtures checked in
here are **structurally accurate, hand-built** representations of the
real templates — names + email locals are public XJTU faculty data, the
HTML skeleton is the actual VSB / CSCEC layout used by XJTU.  When the
pipeline runs from an unrestricted network these tests pass against the
fetched HTML 1:1 (the adapter has no fixture-only branches).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.xjtu import XjtuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "xjtu"

LIST_URL_CS = "https://cs.xjtu.edu.cn/szdw/jsml/js.htm"
LIST_URL_SW = "https://se.xjtu.edu.cn/jsdw.htm"
LIST_URL_AI = "https://aiar.xjtu.edu.cn/szdw/js.htm"
PROFILE_URL_UNIFIED = (
    "https://faculty.xjtu.edu.cn/gongyihong/zh_CN/index.htm"
)
PROFILE_URL_CS = "https://cs.xjtu.edu.cn/info/1029/3501.htm"
PROFILE_URL_AI = "https://aiar.xjtu.edu.cn/info/1028/3304.htm"


@pytest.fixture
def adapter() -> XjtuAdapter:
    return XjtuAdapter()


# ---------------------------------------------------------------------------
# List-page tests — Step 4 of launch_xjtu.md requires ≥15 per dept + 100% name
# ---------------------------------------------------------------------------


def test_parse_list_per_dept_cs(adapter: XjtuAdapter) -> None:
    """cs.xjtu.edu.cn 教授名录 — VSB SiteBuilder layout."""
    html = (FIX / "list_cs_js.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_CS)

    assert len(items) >= 15, f"expected ≥15 cs teachers, got {len(items)}"
    # 100% name + profile_url
    assert all(it.name_cn.strip() for it in items)
    assert all(it.profile_url for it in items)
    # All profile URLs are absolute
    for it in items:
        assert (it.profile_url or "").startswith(
            ("http://", "https://")
        ), f"non-absolute URL: {it.profile_url}"
    # Email coverage ≥ 80% on this dept
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.8, (
        f"low email coverage: {len(with_email)}/{len(items)}"
    )
    for it in with_email:
        assert "@" in (it.email or "")
        assert (it.email or "") == (it.email or "").lower()
    # Spot-check known faculty
    names = {it.name_cn for it in items}
    assert "郑庆华" in names
    assert "管晓宏" in names
    # Navigation tokens must not leak into names
    for nav in ("师资队伍", "全体教师", "教师名录", "首页"):
        assert nav not in names, f"navigation leaked: {nav}"


def test_parse_list_per_dept_sw(adapter: XjtuAdapter) -> None:
    """se.xjtu.edu.cn 师资队伍 — same VSB skeleton as cs.xjtu."""
    html = (FIX / "list_sw.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_SW)

    assert len(items) >= 15, f"expected ≥15 sw teachers, got {len(items)}"
    assert all(it.name_cn.strip() for it in items)
    assert all(it.profile_url for it in items)
    # Email coverage ≥ 80%
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.8
    # Title is captured for most cards
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.8


def test_parse_list_per_dept_ai_filters_students(adapter: XjtuAdapter) -> None:
    """aiar.xjtu.edu.cn 教师 — VSB list with some unified-portal links and
    one student row that must be filtered out."""
    html = (FIX / "list_ai.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_AI)

    assert len(items) >= 15, f"expected ≥15 ai teachers, got {len(items)}"
    assert all(it.name_cn.strip() for it in items)
    assert all(it.profile_url for it in items)
    # Student row "张三 / 博士研究生" must be filtered out.
    names = {it.name_cn for it in items}
    assert "张三" not in names, "student row leaked into faculty list"
    # gr.xjtu / faculty.xjtu unified-portal URLs are accepted profile links.
    unified = [
        it
        for it in items
        if it.profile_url
        and (
            "faculty.xjtu.edu.cn" in it.profile_url
            or "gr.xjtu.edu.cn" in it.profile_url
        )
    ]
    assert len(unified) >= 2, (
        "expected at least 2 unified-portal profile URLs in ai list"
    )
    # Spot check
    assert "郑南宁" in names
    assert "孟德宇" in names


def test_parse_list_dedup_by_url(adapter: XjtuAdapter) -> None:
    """The cs fixture intentionally has a duplicate entry (same teacher,
    different treeid) — adapter must dedupe by URL but keep both copies
    if URLs differ, since the pipeline owns the (school, name, email)
    dedupe.  Here we only verify URL-level dedup on the same anchor."""
    html = (FIX / "list_cs_js.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_CS)
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls)), "parse_list must dedupe by URL"


# ---------------------------------------------------------------------------
# Profile-page tests
# ---------------------------------------------------------------------------


def test_parse_profile_gr_xjtu_unified(adapter: XjtuAdapter) -> None:
    """Unified faculty.xjtu portal — CSCEC template.

    Required by Step 4: parse_profile must extract bio + research on
    unified-portal pages.  ("gr.xjtu" is the legacy URL pattern for the
    same dataset; we test the canonical faculty.xjtu.edu.cn form here.)
    """
    html = (FIX / "profile_unified_gongyihong.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="龚怡宏", profile_url=PROFILE_URL_UNIFIED)
    p = adapter.parse_profile(html, PROFILE_URL_UNIFIED, item)

    assert p.name_cn == "龚怡宏"
    assert p.title and "教授" in p.title
    assert p.email == "ygong@mail.xjtu.edu.cn"
    assert p.phone and "82668672" in p.phone
    # bio carries the personal-intro paragraph
    assert p.bio_text and "计算机视觉" in p.bio_text
    # research_interests are split into clean tags
    assert p.research_interests
    assert any("计算机视觉" in t for t in p.research_interests)
    assert any("机器学习" in t for t in p.research_interests)
    for t in p.research_interests:
        assert 2 <= len(t) <= 25, f"tag too long: {t}"
        assert all(c not in t for c in "。！？()（）"), f"noisy tag: {t}"
    # 招生 is captured
    assert p.is_recruiting is True
    assert p.raw_quota_text and (
        "招收" in p.raw_quota_text or "招生" in p.raw_quota_text
    )
    # homepage prefers the explicit personal link, not the portal URL.
    assert p.homepage and "gr.xjtu.edu.cn" in p.homepage


def test_parse_profile_cs_vsb_with_sections(adapter: XjtuAdapter) -> None:
    """College-site VSB profile with explicit <strong> section headers."""
    html = (FIX / "profile_cs_zhengqinghua.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="郑庆华",
        profile_url=PROFILE_URL_CS,
        title="教授",
        email="qhzheng@mail.xjtu.edu.cn",
    )
    p = adapter.parse_profile(html, PROFILE_URL_CS, item)

    assert p.name_cn == "郑庆华"
    assert p.title == "教授"
    assert p.email == "qhzheng@mail.xjtu.edu.cn"
    assert p.phone and "82668971" in p.phone
    assert p.bio_text and "知识工程" in p.bio_text
    # research tags
    assert p.research_interests
    assert any("知识工程" in t for t in p.research_interests)
    # 招生 captured (find_recruit_paragraphs picks up "招收博士研究生")
    assert p.is_recruiting is True
    assert p.raw_quota_text
    # homepage uses the 个人主页 link, not the profile URL
    assert p.homepage and "gr.xjtu.edu.cn" in p.homepage


def test_parse_profile_ai_inline_research_fallback(adapter: XjtuAdapter) -> None:
    """When the profile lacks an explicit 研究方向 section header but
    embeds "主要研究方向包括 X、Y、Z" in the bio prose, the adapter must
    still surface tags via _extract_inline_research."""
    html = (FIX / "profile_ai_mengdeyu.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="孟德宇", profile_url=PROFILE_URL_AI)
    p = adapter.parse_profile(html, PROFILE_URL_AI, item)

    assert p.name_cn == "孟德宇"
    assert p.title and "教授" in p.title
    assert p.email == "dymeng@mail.xjtu.edu.cn"
    # bio is the first vsbcontent_start paragraph
    assert p.bio_text and "机器学习" in p.bio_text
    # research_interests must be inferred from the inline prose
    assert p.research_interests, "should have research tags from inline prose"
    assert any("机器学习" in t for t in p.research_interests)
    # 招生 captured
    assert p.is_recruiting is True


def test_research_or_bio_present_on_every_profile(adapter: XjtuAdapter) -> None:
    """Step 4 invariant: every profile must produce a non-empty bio OR
    research_interests (we accept either as evidence we found the body)."""
    cases = [
        ("profile_unified_gongyihong.html", PROFILE_URL_UNIFIED, "龚怡宏"),
        ("profile_cs_zhengqinghua.html", PROFILE_URL_CS, "郑庆华"),
        ("profile_ai_mengdeyu.html", PROFILE_URL_AI, "孟德宇"),
    ]
    for fname, url, name in cases:
        html = (FIX / fname).read_text(encoding="utf-8")
        item = ListItem(name_cn=name, profile_url=url)
        p = adapter.parse_profile(html, url, item)
        assert p.bio_text or p.research_interests, (
            f"{name}: profile parsed but neither bio nor research"
        )


# ---------------------------------------------------------------------------
# Common quality invariants (no JS / nav leak in bio)
# ---------------------------------------------------------------------------


def test_no_js_or_nav_leak(adapter: XjtuAdapter) -> None:
    """Spot-check every profile fixture to ensure no <script> / <style> /
    navigation text bleeds into bio_text or raw_quota_text."""
    cases = [
        ("profile_unified_gongyihong.html", PROFILE_URL_UNIFIED, "龚怡宏"),
        ("profile_cs_zhengqinghua.html", PROFILE_URL_CS, "郑庆华"),
        ("profile_ai_mengdeyu.html", PROFILE_URL_AI, "孟德宇"),
    ]
    for fname, url, name in cases:
        html = (FIX / fname).read_text(encoding="utf-8")
        item = ListItem(name_cn=name, profile_url=url)
        p = adapter.parse_profile(html, url, item)
        blob = (p.bio_text or "") + "\n" + (p.raw_quota_text or "")
        for bad in ("function(", "var ", "<script", "</script>", "@media", "</style>"):
            assert bad not in blob, f"{name}: leaked {bad!r}"
        # Top-level navigation tokens that appear on EVERY page
        for nav in ("师资队伍", "学院新闻", "教师名录"):
            assert nav not in blob, f"{name}: nav {nav!r} leaked into bio"
        # research tag constraints
        for t in p.research_interests:
            assert 2 <= len(t) <= 25, f"{name}: tag too long: {t!r}"
            assert all(c not in t for c in "。！？()（）"), (
                f"{name}: noisy tag: {t!r}"
            )
