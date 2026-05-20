"""Tests for the ShanghaiTech (上海科技大学) adapter.

All fixtures under ``tests/fixtures/shtech/`` are real responses fetched from
``sist.shanghaitech.edu.cn`` (the only campus-wide CS/AI/EE college):

- ``list_sist_col0_json.json`` — POST response from
  ``/_wp3services/generalQuery?queryObj=teacherHome`` for
  ``exField8=常任教授`` (tenure-track PIs, ~88 rows).
- ``list_sist_col1_json.json`` — same endpoint, ``exField8=特聘教授`` (joint /
  honorary chairs, ~53 rows).
- ``list_sist_col3_json.json`` — ``exField8=研究人员`` (~9 research-rank PIs).
- ``list_sist_col0.html`` — the static SPA shell. Must yield 0 advisors;
  it's only useful to confirm the adapter doesn't return phantom nav links.
- ``profile_<slug>.html`` — per-PI subsite homepages
  (``http://sist.shanghaitech.edu.cn/<slug>/main.htm``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.shtech import ShtechAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "shtech"

SIST_LIST_URL = (
    "https://sist.shanghaitech.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"
)
PROFILE_BAIWB = "http://sist.shanghaitech.edu.cn/baiwb/main.htm"
PROFILE_CWH = "http://sist.shanghaitech.edu.cn/cwh/main.htm"
PROFILE_CHENBL = "http://sist.shanghaitech.edu.cn/chenbl/main.htm"
PROFILE_BYG = "http://sist.shanghaitech.edu.cn/byg/main.htm"


@pytest.fixture
def adapter() -> ShtechAdapter:
    return ShtechAdapter()


# ---------------------------------------------------------------------------
# parse_list — JSON envelope
# ---------------------------------------------------------------------------


def test_parse_list_basic_col0(adapter):
    """The 常任教授 tab should yield ≥50 PIs with names + plain-text emails
    (Step 4 of the launch prompt requires 50+ and ≥70% email coverage)."""
    payload = (FIX / "list_sist_col0_json.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, SIST_LIST_URL)
    assert len(items) >= 50, f"too few tenure-track PIs: {len(items)}"
    # Names + profile URLs are mandatory
    assert all(it.name_cn for it in items)
    assert all(it.profile_url and it.profile_url.startswith("http") for it in items)
    # All profile URLs live on the SIST campus subdomain
    for it in items:
        assert "sist.shanghaitech.edu.cn" in it.profile_url, it.profile_url
    # Email coverage — ShanghaiTech publishes plaintext emails for most PIs.
    with_email = [it for it in items if it.email]
    ratio = len(with_email) / len(items)
    assert ratio >= 0.70, f"email coverage too low: {ratio:.0%} (n={len(items)})"


def test_parse_list_emails_are_normalized(adapter):
    """Plain ``slug@shanghaitech.edu.cn`` should survive lower-casing and no
    invisible-character contamination (mirrors the fudan zero-width-space
    sanity check)."""
    payload = (FIX / "list_sist_col0_json.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, SIST_LIST_URL)
    for it in items:
        if not it.email:
            continue
        assert it.email == it.email.lower()
        for invisible in ("​", "‌", "‍", "\xa0", " "):
            assert invisible not in it.email, f"{invisible!r} in {it.email!r}"
        assert "@" in it.email


def test_parse_list_filter_non_PI(adapter):
    """The PI universe must never contain student / postdoc / visiting-scholar
    titles. Even when ``exField8`` is blank, ``exField1`` must reference a
    faculty rank."""
    payload = (FIX / "list_sist_col0_json.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, SIST_LIST_URL)
    for it in items:
        if not it.title:
            continue
        for bad in ("博士后", "在校生", "硕士研究生", "博士研究生",
                    "研究助理", "Visiting Scholar", "PhD student",
                    "Postdoc", "Postdoctoral"):
            assert bad not in it.title, f"{it.name_cn} has bad title {it.title!r}"


def test_parse_list_special_chair_tab(adapter):
    """特聘教授 (col=1) tab carries 包云岗 (中科院计算所 chair appointment) — a
    well-known systems researcher whose presence sanity-checks the adapter
    doesn't drop joint appointees."""
    payload = (FIX / "list_sist_col1_json.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, SIST_LIST_URL)
    assert len(items) >= 30, f"特聘教授 tab too small: {len(items)}"
    by_name = {it.name_cn: it for it in items}
    assert "包云岗" in by_name
    # 包云岗 email is at his home institution (ICT, CAS) — adapter must NOT
    # rewrite it to @shanghaitech.edu.cn.
    bao = by_name["包云岗"]
    assert bao.email and bao.email.endswith("@ict.ac.cn")


def test_parse_list_research_rank_tab(adapter):
    """研究人员 (col=3) tab — small (~9 rows) but every row must carry an
    explicit 副研究员 / 助理研究员 / 研究员 title."""
    payload = (FIX / "list_sist_col3_json.json").read_text(encoding="utf-8")
    items = adapter.parse_list(payload, SIST_LIST_URL)
    assert len(items) >= 5
    for it in items:
        assert it.name_cn and it.profile_url
        # Research staff have an explicit rank field in exField1.
        assert it.title and "研究员" in it.title


def test_parse_list_shell_html_returns_empty(adapter):
    """The static /szdwx/list.htm shell is empty under plain GET — adapter
    must return ``[]`` rather than salvage nav anchors."""
    html = (FIX / "list_sist_col0.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://sist.shanghaitech.edu.cn/szdwx/list.htm?col=0")
    assert items == [], f"shell SPA must return [] (got {len(items)} items)"


# ---------------------------------------------------------------------------
# parse_profile — per-PI subsite
# ---------------------------------------------------------------------------


def test_parse_profile_research(adapter):
    """A PI's bio-card must surface either bio_text or research_interests
    (launch_shtech.md Step 4 requirement)."""
    html = (FIX / "profile_baiwb.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="白卫邦",
        profile_url=PROFILE_BAIWB,
        title="助理教授、研究员、博导",
        email="wbbai@shanghaitech.edu.cn",
        phone="021-20684556",
    )
    p = adapter.parse_profile(html, PROFILE_BAIWB, item)
    assert p.bio_text or p.research_interests
    # 白卫邦's 研究方向 row carries six discrete tags
    assert p.research_interests
    assert "医疗机器人" in p.research_interests
    assert any(t in p.research_interests for t in ("人工智能", "具身智能"))
    # All tags pass the project's noise filter
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        for noise_ch in "。！？()（）":
            assert noise_ch not in tag, f"{tag!r} contains {noise_ch!r}"


def test_parse_profile_bio_no_js_or_nav(adapter):
    """bio_text and raw_quota_text must NOT leak inline JS, <script>, or the
    global navigation strings (清华 §6.6 / §6.7)."""
    html = (FIX / "profile_baiwb.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="白卫邦", profile_url=PROFILE_BAIWB)
    p = adapter.parse_profile(html, PROFILE_BAIWB, item)
    assert p.bio_text and "白卫邦" in p.bio_text
    for needle in (
        "function(",
        "<script",
        "@font-face",
        "首页",
        "学院概况",
        "师资队伍",
        "通知公告",
    ):
        for field_name in ("bio_text", "raw_quota_text"):
            val = getattr(p, field_name) or ""
            assert needle not in val, (
                f"{field_name} leaked {needle!r}: {val[:120]!r}"
            )


def test_parse_profile_pulls_contact_fields(adapter):
    """The bio-card includes labelled rows for 电话 / 办公室 / 邮箱 / 个人主页.
    parse_profile must promote phone + email from those rows when the
    list_item doesn't already carry them."""
    html = (FIX / "profile_cwh.html").read_text(encoding="utf-8")
    # Intentionally pass an empty list_item to force the profile-side extract.
    item = ListItem(name_cn="曹文翰", profile_url=PROFILE_CWH)
    p = adapter.parse_profile(html, PROFILE_CWH, item)
    assert p.email == "whcao@shanghaitech.edu.cn"
    assert p.phone and "021-2068" in p.phone
    assert p.title and "助理教授" in p.title
    assert p.research_interests
    assert any(t in p.research_interests for t in ("柔性电子器件", "软体机器人"))


def test_parse_profile_inherits_list_metadata(adapter):
    """When the list_item already carries email/title/phone, parse_profile
    must not blank them out even if its own card scrape returns the same
    values."""
    html = (FIX / "profile_chenbl.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈佰乐",
        profile_url=PROFILE_CHENBL,
        title="副教授、研究员、博导",
        email="chenbl@shanghaitech.edu.cn",
        phone="021-20685596",
        photo_url="https://sist.shanghaitech.edu.cn/_upload/article/images/example.jpg",
    )
    p = adapter.parse_profile(html, PROFILE_CHENBL, item)
    assert p.name_cn == "陈佰乐"
    assert p.title and "副教授" in p.title
    assert p.email == "chenbl@shanghaitech.edu.cn"
    assert p.phone == "021-20685596"
    assert p.photo_url == item.photo_url
    assert p.source_url == PROFILE_CHENBL
    # Long bio is always present for senior PIs.
    assert p.bio_text and len(p.bio_text) >= 80


def test_parse_profile_external_homepage_for_chair(adapter):
    """特聘教授 (e.g. 包云岗) carries an external personal page link in the
    ``个人主页`` row — parse_profile must surface it as ``homepage`` rather
    than defaulting to the SIST sub-site URL."""
    html = (FIX / "profile_byg.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="包云岗",
        profile_url=PROFILE_BYG,
        email="baoyg@ict.ac.cn",
    )
    p = adapter.parse_profile(html, PROFILE_BYG, item)
    assert p.email == "baoyg@ict.ac.cn"
    assert p.homepage and p.homepage.startswith("http")
    # Should be the external link, not the SIST subsite default.
    assert "ict.ac.cn" in p.homepage or "acs.ict.ac.cn" in p.homepage


# ---------------------------------------------------------------------------
# Global invariant — no JS / style / nav leakage across all sampled profiles.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fname,url,name",
    [
        ("profile_baiwb.html", PROFILE_BAIWB, "白卫邦"),
        ("profile_cwh.html", PROFILE_CWH, "曹文翰"),
        ("profile_chenbl.html", PROFILE_CHENBL, "陈佰乐"),
        ("profile_byg.html", PROFILE_BYG, "包云岗"),
    ],
)
def test_no_leak_in_profile_invariants(adapter, fname, url, name):
    html = (FIX / fname).read_text(encoding="utf-8")
    item = ListItem(name_cn=name, profile_url=url)
    p = adapter.parse_profile(html, url, item)
    for field_name in ("bio_text", "raw_quota_text"):
        val = getattr(p, field_name) or ""
        for needle in (
            "function(",
            "<script",
            "</script>",
            "@font-face",
            "DOCTYPE",
            "招生招聘",  # nav block
            "通知公告",
        ):
            assert needle not in val, (
                f"{name}/{field_name} leaked {needle!r}: {val[:120]!r}"
            )
    # source_url must round-trip; bio cards always set the homepage to the
    # subsite root (or an external link) — never null.
    assert p.source_url == url
    assert p.homepage and p.homepage.startswith("http")
