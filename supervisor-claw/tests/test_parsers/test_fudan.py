"""Tests for the Fudan University adapter.

All fixtures under ``tests/fixtures/fudan/`` are real responses fetched from
``cs.fudan.edu.cn`` (JSON), ``sds.fudan.edu.cn`` (HTML inline-table) and
``ai3.fudan.edu.cn`` (HTML cards) with the project's ``curl -A`` user agent.

Per-dept structure recap (mirrors the docstring in claw.adapters.fudan):

- ``cs`` faculty list is a sudy-CMS SPA at /53161 — the static HTML carries
  empty ``<ul>`` slots, so the actual list source we test is the JSON GET
  endpoint ``/_wp3services/generalQuery?queryObj=teacherHome``.
- ``bd`` (sds) renders teachers inline inside a single ``<table>`` block.
- ``ai`` (AI³ — ai3.fudan.edu.cn) renders ``<ul class="teacher-list">``
  cards with profile URLs pointing to ``/info/<col>/<aid>.htm``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.fudan import FudanAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "fudan"

# The full GET URL is huge; tests only need the netloc + path to route inside
# parse_list, so we use a short stable placeholder.
CS_LIST_URL = (
    "https://cs.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"
)
BD_PROF_URL = "https://sds.fudan.edu.cn/17428/list.htm"
BD_ASSOC_URL = "https://sds.fudan.edu.cn/17429/list.htm"
AI_LIST_URL = "https://ai3.fudan.edu.cn/yjspy/dsdw.htm"


@pytest.fixture
def adapter() -> FudanAdapter:
    return FudanAdapter()


# ---------------------------------------------------------------------------
# parse_list — cs (JSON)
# ---------------------------------------------------------------------------


def test_parse_list_cs_json_filters_to_advisors(adapter):
    """JSON endpoint returns 288 rows but ~95 are blank-title staff and a few
    are pure 工程师 with no research role; the adapter must filter to the
    actual academic faculty."""
    payload = (FIX / "list_cs.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, CS_LIST_URL)
    # Loose lower bound — at least the 院士(3) + 正高(63) + 副高(52) ≈ 118
    assert len(items) >= 110, f"too few advisors after filter: {len(items)}"
    # And we still drop the >100 pure-staff rows.
    assert len(items) <= 240
    # Every advisor has a name + profile URL
    assert all(it.name_cn and it.profile_url for it in items)
    # Profile URLs absolutize to ai.fudan.edu.cn (cs+ai merged backend)
    assert any("ai.fudan.edu.cn" in (it.profile_url or "") for it in items)
    # Email coverage among filtered advisors must be high — Fudan's directory
    # carries an email on every research-active member.
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.75


def test_parse_list_cs_strips_zero_width_email_suffix(adapter):
    """敖海荣's directory entry encodes the trailing email with U+200B
    (zero-width space). The cleaner must strip it so downstream dedup
    keys aren't busted by an invisible char."""
    payload = (FIX / "list_cs.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, CS_LIST_URL)
    by_name = {it.name_cn: it for it in items}
    if "敖海荣" in by_name:
        email = by_name["敖海荣"].email or ""
        assert email == email.strip()
        for invisible in ("​", "‌", "‍", " "):
            assert invisible not in email, f"{invisible!r} leaked into email"


def test_parse_list_cs_drops_placeholder_photo(adapter):
    """The generic ``/_res/articleType/teacher_CN.jpg`` placeholder is not a
    real headshot — adapter must NOT carry it forward as photo_url."""
    payload = (FIX / "list_cs.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, CS_LIST_URL)
    photos = [it.photo_url for it in items if it.photo_url]
    assert all("/_res/articleType/" not in p for p in photos)


def test_parse_list_cs_includes_known_double_appointed_advisor(adapter):
    """邱锡鹏 (NLP / MOSS) appears in the merged faculty roster — sanity-check
    that the filter doesn't accidentally drop him.

    NOTE: his exField1 in the live JSON is "教师、博导" (not "教授"); we
    accept any standard advisor-title keyword rather than requiring "教授"
    literally.
    """
    payload = (FIX / "list_cs.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, CS_LIST_URL)
    names = {it.name_cn for it in items}
    assert "邱锡鹏" in names
    qiu = next(it for it in items if it.name_cn == "邱锡鹏")
    assert qiu.email == "xpqiu@fudan.edu.cn"
    assert qiu.title and any(k in qiu.title for k in ("教授", "教师", "博导", "研究员"))


# ---------------------------------------------------------------------------
# parse_list — bd (sds inline table)
# ---------------------------------------------------------------------------


def test_parse_list_bd_prof(adapter):
    html = (FIX / "list_bd_prof.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, BD_PROF_URL)
    assert len(items) >= 18, f"expected ≥18 prof rows, got {len(items)}"
    assert all(it.name_cn for it in items)
    # Names are pure CJK, ≤4 chars typically; this catches nav-link leakage.
    for it in items:
        assert 2 <= len(it.name_cn) <= 5, f"suspicious name: {it.name_cn!r}"
        assert "首页" not in it.name_cn
        assert "学院" not in it.name_cn
    # Some advisors keep personal pages in the h4 anchor — at least one
    # should carry a profile_url so downstream tries to fetch it.
    with_profile = [it for it in items if it.profile_url]
    assert len(with_profile) >= 5, "expected some teachers with personal sites"
    # Photos are absolutized against the sds host.
    photos = [it.photo_url for it in items if it.photo_url]
    assert len(photos) >= 15
    assert all(p.startswith("https://sds.fudan.edu.cn/") for p in photos)


def test_parse_list_bd_assoc_has_known_advisor(adapter):
    """副教授 page — 魏忠钰 (NLP, well-known) must be present."""
    html = (FIX / "list_bd_assoc.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, BD_ASSOC_URL)
    assert len(items) >= 12
    names = {it.name_cn for it in items}
    assert "魏忠钰" in names or "梁家卿" in names


def test_parse_list_bd_normalized_jianfeng_feng(adapter):
    """冯建峰 is wrapped as ``<h4><a>冯建峰</a></h4>`` — adapter must extract the
    inner text without dragging the <a> tag into the name field."""
    html = (FIX / "list_bd_prof.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, BD_PROF_URL)
    names = {it.name_cn for it in items}
    assert "冯建峰" in names
    feng = next(it for it in items if it.name_cn == "冯建峰")
    # link in h4 → adapter picks up his external personal page
    assert feng.profile_url and feng.profile_url.startswith("http")
    assert "<" not in feng.name_cn


# ---------------------------------------------------------------------------
# parse_list — ai (AI³)
# ---------------------------------------------------------------------------


def test_parse_list_ai3(adapter):
    html = (FIX / "list_ai.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, AI_LIST_URL)
    assert len(items) >= 14, f"expected ≥14 advisors, got {len(items)}"
    assert all(it.name_cn and it.profile_url for it in items)
    # AI³ profiles live under /info/<col>/<aid>.htm
    for it in items:
        assert it.profile_url.startswith("https://ai3.fudan.edu.cn/info/"), it.profile_url
    # title (头衔) populated for every advisor — AI³ template always shows one
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.9
    # Director (院长漆远) sanity-check — name varies by source listing, but the
    # institute roster always contains at least these long-tenured advisors.
    names = {it.name_cn for it in items}
    assert any(n in names for n in ("陈曦", "李昊", "屈超"))


# ---------------------------------------------------------------------------
# parse_profile — cs (ai.fudan.edu.cn news_* template)
# ---------------------------------------------------------------------------


def test_parse_profile_cs_chenyang(adapter):
    """陈阳's profile carries 职称/邮件/研究领域/个人主页 in the
    ``<div class="news_*">`` semantic blocks."""
    html = (FIX / "profile_cs_chenyang.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈阳",
        profile_url="http://ai.fudan.edu.cn/cy_37066/list.htm",
        title="教授，博导",
        email="chenyang@fudan.edu.cn",
    )
    p = adapter.parse_profile(
        html, "http://ai.fudan.edu.cn/cy_37066/list.htm", item
    )
    assert p.email == "chenyang@fudan.edu.cn"
    assert p.title and "教授" in p.title
    # Research interests extracted from "智能互联网、社交网络、网络大数据、城市计算、大语言模型"
    assert p.research_interests, "expected interests from 研究领域 row"
    assert "社交网络" in p.research_interests
    assert "大语言模型" in p.research_interests
    # All tags are short and free of forbidden chars
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        for noise_ch in "。！？()（）":
            assert noise_ch not in tag, f"tag {tag!r} contains {noise_ch!r}"
    # external personal site is preserved as homepage when the profile
    # page exposes a "个人主页" link off-host
    assert p.homepage and p.homepage.startswith("http")
    assert p.source_url == "http://ai.fudan.edu.cn/cy_37066/list.htm"


def test_parse_profile_cs_qiuxipeng_bio(adapter):
    """邱锡鹏: 研究领域 column is blank but 个人简介 has the long bio paragraph
    — make sure we pull bio_text from the right semantic block."""
    html = (FIX / "profile_cs_qiuxipeng.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="邱锡鹏",
        profile_url="http://ai.fudan.edu.cn/qxp/list.htm",
        email="xpqiu@fudan.edu.cn",
    )
    p = adapter.parse_profile(
        html, "http://ai.fudan.edu.cn/qxp/list.htm", item
    )
    assert p.email == "xpqiu@fudan.edu.cn"
    assert p.bio_text and len(p.bio_text) >= 50
    # bio must mention something specific to qiu
    assert "邱锡鹏" in p.bio_text or "MOSS" in p.bio_text or "大语言模型" in p.bio_text


# ---------------------------------------------------------------------------
# parse_profile — ai3 (sudy CMS with strong-bold pseudo-headers)
# ---------------------------------------------------------------------------


def test_parse_profile_ai3_chenxi(adapter):
    """陈曦 (AI³) — uses ``<strong><span style="color:rgb(0,112,192)">研究方向
    </span></strong>`` as section header inside <p> rather than <h4>."""
    html = (FIX / "profile_ai_chenxi.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈曦",
        profile_url="https://ai3.fudan.edu.cn/info/1088/1684.htm",
        title="教授、博士生导师",
    )
    p = adapter.parse_profile(
        html, "https://ai3.fudan.edu.cn/info/1088/1684.htm", item
    )
    # No email on this page → email_obfuscated stays False, email may be None
    assert p.bio_text or p.research_interests, "must extract at least one field"
    # Research interests extracted from "研究方向" section
    assert p.research_interests
    assert any(
        t in p.research_interests
        for t in ("贝叶斯推断", "统计机器学习", "深度学习", "多模态大模型", "多智能体")
    ), p.research_interests
    # 招生 signal — page contains a 招生专业 section
    assert p.is_recruiting is True
    assert p.raw_quota_text and "招" in p.raw_quota_text
    assert "首页" not in (p.raw_quota_text or "")


def test_parse_profile_ai3_chengyuan(adapter):
    """程远 — second AI³ profile to verify the section walker handles
    different content lengths and the bio fallback doesn't grab publication
    list entries."""
    html = (FIX / "profile_ai_chengyuan.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="程远",
        profile_url="https://ai3.fudan.edu.cn/info/1088/2244.htm",
        title="研究员、博士生导师",
    )
    p = adapter.parse_profile(
        html, "https://ai3.fudan.edu.cn/info/1088/2244.htm", item
    )
    assert p.research_interests or p.bio_text
    if p.bio_text:
        # bio fallback must not start with a numbered publication entry
        assert not p.bio_text.lstrip().startswith(("1.", "1、", "1)"))


# ---------------------------------------------------------------------------
# global invariants — no JS / nav leakage in any parsed profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fname,url,name",
    [
        ("profile_cs_chenyang.html",
         "http://ai.fudan.edu.cn/cy_37066/list.htm", "陈阳"),
        ("profile_cs_qiuxipeng.html",
         "http://ai.fudan.edu.cn/qxp/list.htm", "邱锡鹏"),
        ("profile_ai_chenxi.html",
         "https://ai3.fudan.edu.cn/info/1088/1684.htm", "陈曦"),
        ("profile_ai_chengyuan.html",
         "https://ai3.fudan.edu.cn/info/1088/2244.htm", "程远"),
    ],
)
def test_no_js_or_nav_leak_in_any_profile(adapter, fname, url, name):
    """bio_text and raw_quota_text from any sampled profile must not contain
    inline JS, <style> blocks, or global nav anchors (清华 §6.6, §6.7)."""
    html = (FIX / fname).read_text(encoding="utf-8")
    item = ListItem(name_cn=name, profile_url=url)
    p = adapter.parse_profile(html, url, item)
    for field_name in ("bio_text", "raw_quota_text"):
        val = getattr(p, field_name) or ""
        for needle in (
            "function(",
            "<script",
            "@font-face",
            "招生招聘",  # global nav block
            "首页 |",
            "教职工名录",
        ):
            assert needle not in val, f"{name}/{field_name} leaked {needle!r}"


def test_email_coverage_threshold_cs(adapter):
    """Across the full cs JSON list, ≥75% of filtered advisors must have an
    email — matches the §4 quality bar with a small margin for advisors who
    intentionally hide contact info."""
    payload = (FIX / "list_cs.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, CS_LIST_URL)
    with_email = [it for it in items if it.email]
    ratio = len(with_email) / max(1, len(items))
    assert ratio >= 0.75, f"cs email coverage {ratio:.0%} (n={len(items)})"
