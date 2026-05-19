"""Tests for the Nanjing University adapter.

All fixtures under ``tests/fixtures/nju/`` are real HTML fetched from the
school's public pages with the project's ``curl -A`` user agent. Tests run
against frozen copies so they are deterministic and offline-safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.nju import NjuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nju"


@pytest.fixture
def adapter() -> NjuAdapter:
    return NjuAdapter()


# ---------------------------------------------------------------------------
# parse_list — cs (5 sub-pages by rank)
# ---------------------------------------------------------------------------


CS_LIST_FIXTURES = [
    ("list_cs_prof.html", "https://cs.nju.edu.cn/2639/listm.htm", 60),
    ("list_cs_assoc.html", "https://cs.nju.edu.cn/2640/listm.htm", 15),
    ("list_cs_zzp.html", "https://cs.nju.edu.cn/zzp/listm.htm", 20),
    ("list_cs_kxkbd.html", "https://cs.nju.edu.cn/kxkbd/listm.htm", 2),
    ("list_cs_lect.html", "https://cs.nju.edu.cn/2641/listm.htm", 10),
]


@pytest.mark.parametrize("fname, url, min_count", CS_LIST_FIXTURES)
def test_parse_list_cs(adapter, fname, url, min_count):
    html = (FIX / fname).read_text(encoding="utf-8")
    items = adapter.parse_list(html, url)
    assert len(items) >= min_count, f"{fname}: expected ≥{min_count}, got {len(items)}"
    assert all(it.name_cn for it in items), "every advisor must have a name"
    assert all(it.profile_url for it in items), "every advisor must have a profile URL"
    # profile URLs absolutize against either the dept host or off-host (周志华
    # is hosted on www.nju.edu.cn — that's still a valid absolute URL).
    assert all(it.profile_url.startswith("http") for it in items)


def test_parse_list_cs_total_across_ranks(adapter):
    """Aggregated CS faculty count across all rank pages should comfortably
    exceed 100 — this catches regressions where one sub-page silently breaks.
    """
    total = 0
    for fname, url, _ in CS_LIST_FIXTURES:
        html = (FIX / fname).read_text(encoding="utf-8")
        total += len(adapter.parse_list(html, url))
    assert total >= 100, f"expected ≥100 CS advisors total, got {total}"


def test_parse_list_cs_drops_titles_into_field(adapter):
    """``<a title="吕建 (院士、博导)">`` should split into name=吕建, title=院士、博导."""
    html = (FIX / "list_cs_prof.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://cs.nju.edu.cn/2639/listm.htm")
    by_name = {it.name_cn: it for it in items}
    assert "吕建" in by_name
    assert by_name["吕建"].title and "院士" in by_name["吕建"].title
    # name itself must not retain the bracketed title
    assert "(" not in by_name["吕建"].name_cn
    assert "（" not in by_name["吕建"].name_cn


# ---------------------------------------------------------------------------
# parse_list — ai
# ---------------------------------------------------------------------------


def test_parse_list_ai_handles_three_url_styles(adapter):
    """AI 学院 list mixes three href styles; all should yield absolute URLs."""
    html = (FIX / "list_ai.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://ai.nju.edu.cn/people/list.htm")
    assert len(items) >= 40
    urls = [it.profile_url for it in items]
    has_redirect = any("/_redirect?" in u for u in urls)
    has_cms = any("/c18540a" in u or "/c18541a" in u for u in urls)
    has_external = any(
        u.startswith("https://") and "ai.nju.edu.cn/_redirect" not in u
        for u in urls
    )
    assert has_redirect, "expected /_redirect?… style"
    assert has_cms, "expected /c18540aNNN/page.htm style"
    assert has_external, "expected external personal pages"
    # title (头衔) populated for most rows — some entries (a handful of
    # research staff with no parenthetical role) legitimately lack one
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.7


# ---------------------------------------------------------------------------
# parse_list — sw
# ---------------------------------------------------------------------------


def test_parse_list_sw(adapter):
    html = (FIX / "list_sw.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://software.nju.edu.cn/szll/szdw/index.html")
    assert len(items) >= 40
    # 软院 page aligns CJK names with double full-width spaces (骆  斌).
    # The normalizer must collapse those — downstream upsert key would break.
    names = [it.name_cn for it in items]
    assert "骆斌" in names, "expected normalized 骆斌 (no interior spaces)"
    assert "陈振宇" in names
    # 兼职博导 / 兼职 markers get pulled into the title field, not the name.
    bracket_names = [n for n in names if "（" in n or "(" in n]
    assert not bracket_names, f"name field should not retain parens: {bracket_names}"
    titled = [it for it in items if it.title]
    assert len(titled) >= 5, "expected at least a handful of 兼职 markers"
    assert any("兼职" in (it.title or "") for it in titled)
    # Nav links (师资力量 / 专业教师 / etc.) must be filtered out
    assert all(it.name_cn not in {"师资力量", "专业教师", "科研团队"} for it in items)


# ---------------------------------------------------------------------------
# parse_profile — cs
# ---------------------------------------------------------------------------


def test_parse_profile_cs_lujian(adapter):
    """CS profile carries contact info in ``<div class="other">`` after the bio."""
    html = (FIX / "profile_cs_lujian.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="吕建", title="院士、博导")
    p = adapter.parse_profile(
        html, "https://cs.nju.edu.cn/58/2a/c2639a153642/pagem.htm", item
    )
    assert p.email == "lj@nju.edu.cn"
    assert p.phone and "83593283" in p.phone
    assert p.bio_text and "吕建教授" in p.bio_text
    assert p.research_interests, "expected interests extracted from '研究方向包括…'"
    assert "软件自动化" in p.research_interests
    # photo URL must be absolutized against the CS host
    assert p.photo_url and p.photo_url.startswith("https://cs.nju.edu.cn/")
    # source / homepage
    assert p.homepage == "https://cs.nju.edu.cn/58/2a/c2639a153642/pagem.htm"
    assert p.source_url == p.homepage


def test_parse_profile_cs_strips_word_residue(adapter):
    """李宣东 profile was pasted in from Word and carries mso conditional
    comments + <style> blocks inside <div class="detail">. None of that may
    leak into bio_text (clean-text invariant from tsinghua adapter §6.6).
    """
    html = (FIX / "profile_cs_lixuandong.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="李宣东", title="博导")
    p = adapter.parse_profile(
        html, "https://cs.nju.edu.cn/58/28/c2639a153640/pagem.htm", item
    )
    assert p.bio_text
    for needle in ("@font-face", "mso-font", "panose-1", "<style", "function("):
        assert needle not in p.bio_text, f"bio leaked: {needle!r}"
    assert "李宣东" in p.bio_text
    assert p.email == "lxd@nju.edu.cn"


# ---------------------------------------------------------------------------
# parse_profile — ai
# ---------------------------------------------------------------------------


def test_parse_profile_ai_cms_extracts_research(adapter):
    """钱超 profile uses the AI 学院 in-CMS template (/c18540aNNN/page.htm).
    Bio is one long paragraph; research interests must be picked up from
    inline '研究方向为A、B、C' phrasing.
    """
    html = (FIX / "profile_ai_qianchao.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="钱超", title="教授")
    p = adapter.parse_profile(
        html, "https://ai.nju.edu.cn/71/c9/c18540a422345/page.htm", item
    )
    assert p.bio_text and len(p.bio_text) > 100
    assert "演化计算" in p.research_interests or "机器学习" in p.research_interests
    # advisor mentions 招收硕士和博士 → recruit signal must be set, and the
    # captured chunk must come from the main body (not the global nav).
    assert p.is_recruiting is True
    assert p.raw_quota_text and "招" in p.raw_quota_text
    assert "首页" not in (p.raw_quota_text or "")


def test_parse_profile_ai_external_english_page(adapter):
    """戴新宇's redirect URL lands on an English personal site with no CMS
    classes whatsoever. The adapter must degrade to body-wide bio extraction
    rather than crashing, and must avoid hallucinating Chinese metadata.
    """
    html = (FIX / "profile_ai_daixinyu.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="戴新宇", title="教授")
    p = adapter.parse_profile(
        html,
        "https://ai.nju.edu.cn/_redirect?siteId=391&columnId=18540&articleId=321836",
        item,
    )
    assert p.name_cn == "戴新宇"
    assert p.title == "教授"
    assert p.homepage and p.source_url
    # Bio may be empty for off-template pages, but if non-empty it must not
    # contain noise like inline JS.
    if p.bio_text:
        assert "<script" not in p.bio_text
        assert "function(" not in p.bio_text


# ---------------------------------------------------------------------------
# parse_profile — sw
# ---------------------------------------------------------------------------


def test_parse_profile_sw_zychen(adapter):
    """软件学院 profile splits contact (sidebar div.mc) from rich text
    (div.middle). Both halves must be tapped.
    """
    html = (FIX / "profile_sw_zychen.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="陈振宇", title=None)
    p = adapter.parse_profile(
        html, "https://software.nju.edu.cn/zychen/index.html", item
    )
    assert p.email == "zychen@nju.edu.cn"
    assert p.phone and "83621360" in p.phone
    assert p.bio_text and "智能软件工程" in p.bio_text
    # bio must come from the '简介' pseudo-header section, not bleed into
    # later sections (荣誉奖励 / 主讲课程).
    assert "荣誉奖励" not in p.bio_text
    assert "主讲课程" not in p.bio_text


def test_parse_profile_sw_no_pseudo_header_fallback(adapter):
    """骆斌's profile has no bold pseudo-headers — the adapter must fall back
    to the longest <p> in div.middle so we still capture a bio.
    """
    html = (FIX / "profile_sw_luobin.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="骆斌", title=None)
    p = adapter.parse_profile(
        html, "https://software.nju.edu.cn/luobin/index.html", item
    )
    assert p.bio_text and len(p.bio_text) > 100
    assert "骆斌" in p.bio_text or "南京大学" in p.bio_text


def test_parse_profile_sw_hezhang_full_extraction(adapter):
    """张贺 is the happy-path SW profile: contact in div.mc, well-marked
    简介 section, comma-separated research interests in the bio.
    """
    html = (FIX / "profile_sw_hezhang.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="张贺", title=None)
    p = adapter.parse_profile(
        html, "https://software.nju.edu.cn/hezhang/index.html", item
    )
    assert p.email == "hezhang@nju.edu.cn"
    assert p.phone
    assert p.research_interests
    # research_interests tag invariants from tsinghua §6.5
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        for noise_ch in "。！？()（）":
            assert noise_ch not in tag, f"tag {tag!r} contains {noise_ch!r}"


# ---------------------------------------------------------------------------
# global invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fname,url,name",
    [
        ("profile_cs_lujian.html",
         "https://cs.nju.edu.cn/58/2a/c2639a153642/pagem.htm", "吕建"),
        ("profile_cs_lixuandong.html",
         "https://cs.nju.edu.cn/58/28/c2639a153640/pagem.htm", "李宣东"),
        ("profile_cs_baiwenyang.html",
         "https://cs.nju.edu.cn/c9/4d/c2640a51533/pagem.htm", "柏文阳"),
        ("profile_ai_qianchao.html",
         "https://ai.nju.edu.cn/71/c9/c18540a422345/page.htm", "钱超"),
        ("profile_sw_zychen.html",
         "https://software.nju.edu.cn/zychen/index.html", "陈振宇"),
        ("profile_sw_luobin.html",
         "https://software.nju.edu.cn/luobin/index.html", "骆斌"),
        ("profile_sw_hezhang.html",
         "https://software.nju.edu.cn/hezhang/index.html", "张贺"),
    ],
)
def test_no_js_or_nav_leak_in_any_profile(adapter, fname, url, name):
    """No bio_text or raw_quota_text from any sampled profile may contain
    inline JS, <style> blocks, or global nav text (清华 §6.6, §6.7).
    """
    html = (FIX / fname).read_text(encoding="utf-8")
    item = ListItem(name_cn=name)
    p = adapter.parse_profile(html, url, item)
    for field_name in ("bio_text", "raw_quota_text"):
        val = getattr(p, field_name) or ""
        for needle in (
            "function(",
            "<script",
            "@font-face",
            "mso-font",
            "招生招聘",  # global nav block
            "首页 |",
        ):
            assert needle not in val, f"{name}/{field_name} leaked {needle!r}"
