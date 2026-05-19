"""USTC adapter: real-fixture-based parser tests.

Two template families are exercised:

* cs.ustc.edu.cn (CS 学院): list pages use ``ul.news_list``; profile pages
  use ``#wp_articlecontent`` with inline pseudo-headers.
* eeis.ustc.edu.cn (信息学院): list pages use ``div.card`` blocks; profile
  pages use ``article.blog-details`` with newer pages using ``<h2>``
  section headers.

All HTML fixtures were captured with ``curl`` from the live site on
2026-05-19; expected counts below reflect that snapshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.ustc import UstcAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ustc"

CS_PROF_LIST_URL = "https://cs.ustc.edu.cn/js_23235/list.htm"
CS_ASSOC_LIST_URL = "https://cs.ustc.edu.cn/fjs_23239/list.htm"
EEIS_PROF_LIST_URL = "https://eeis.ustc.edu.cn/2648/list.htm"
EEIS_ASSOC_LIST_URL = "https://eeis.ustc.edu.cn/2615/list.htm"

CS_PROFILE_URL = "https://cs.ustc.edu.cn/2020/0426/c23235a460072/page.htm"
EEIS_PROFILE_RECENT_URL = "https://eeis.ustc.edu.cn/2025/0704/c2648a690186/page.htm"
EEIS_PROFILE_EMPTY_URL = "https://eeis.ustc.edu.cn/2017/0807/c2648a190436/page.htm"
EEIS_PROFILE_FREEFORM_URL = "https://eeis.ustc.edu.cn/2018/0929/c2648a340670/page.htm"


@pytest.fixture
def adapter() -> UstcAdapter:
    return UstcAdapter()


# ---------------------------------------------------------------------------
# cs.ustc list pages
# ---------------------------------------------------------------------------


def test_parse_list_cs_prof_basic(adapter: UstcAdapter) -> None:
    html = (FIX / "list_cs_prof_p1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, CS_PROF_LIST_URL)

    # snapshot recorded 18 records on page 1
    assert len(items) >= 15, f"expected ~18 professors on page 1, got {len(items)}"
    assert all(it.name_cn for it in items)
    assert all(
        it.profile_url and it.profile_url.startswith("http") for it in items
    )

    # title is inferred from the list URL
    assert all(it.title == "教授" for it in items)

    # cs.ustc lists carry inline emails — most rows should be populated
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.8, (
        f"low email coverage on cs prof page: {len(with_email)}/{len(items)}"
    )

    # spot-check: 安虹 should be on page 1 with han@ustc.edu.cn
    anhong = next((it for it in items if it.name_cn == "安虹"), None)
    assert anhong is not None, "expected 安虹 in cs prof page 1"
    assert anhong.email == "han@ustc.edu.cn"
    assert anhong.title == "教授"
    assert anhong.profile_url.endswith("/c23235a460072/page.htm")


def test_parse_list_cs_assoc_title(adapter: UstcAdapter) -> None:
    html = (FIX / "list_cs_assoc_p1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, CS_ASSOC_LIST_URL)
    assert len(items) >= 10
    assert all(it.title == "副教授" for it in items)
    # name should be whitespace-collapsed (cs.ustc renders "安  虹")
    for it in items:
        assert it.name_cn == it.name_cn.strip()
        assert "  " not in it.name_cn


# ---------------------------------------------------------------------------
# eeis list pages
# ---------------------------------------------------------------------------


def test_parse_list_eeis_prof_basic(adapter: UstcAdapter) -> None:
    html = (FIX / "list_ai_info_prof_p1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, EEIS_PROF_LIST_URL)

    # eeis renders 8 cards per page
    assert len(items) >= 6, f"expected ~8 cards on eeis prof page 1, got {len(items)}"
    assert all(it.name_cn for it in items)
    assert all(
        it.profile_url and "eeis.ustc.edu.cn" in it.profile_url for it in items
    )
    # eeis list pages do NOT carry emails — every list_item.email is None
    assert all(it.email is None for it in items)
    # title inferred from list_url
    assert all(it.title == "教授" for it in items)

    chenchang = next((it for it in items if it.name_cn == "陈畅"), None)
    assert chenchang is not None
    assert chenchang.profile_url.endswith("/c2648a190436/page.htm")


def test_parse_list_eeis_assoc_title(adapter: UstcAdapter) -> None:
    html = (FIX / "list_ai_info_assoc_p1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, EEIS_ASSOC_LIST_URL)
    assert len(items) >= 6
    assert all(it.title == "副教授" for it in items)


def test_parse_list_eeis_ignores_sidebar_cards(adapter: UstcAdapter) -> None:
    """eeis's `div.card` selector also matches "最新内容" sidebar cards on
    profile pages; the list-page filter (require h5.card-title + p.card-text)
    keeps us to faculty rows."""
    # profile pages include sidebar cards but we should still parse list pages
    # correctly. Smoke this by ensuring all parsed items have a research-direction
    # by virtue of being filtered through `p.card-text`.
    html = (FIX / "list_ai_info_prof_p1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, EEIS_PROF_LIST_URL)
    assert all(it.name_cn and it.profile_url for it in items)


# ---------------------------------------------------------------------------
# profile parsing — cs.ustc (rich body)
# ---------------------------------------------------------------------------


def test_parse_profile_cs_basic(adapter: UstcAdapter) -> None:
    html = (FIX / "profile_cs_anhong.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="安虹",
        profile_url=CS_PROFILE_URL,
        title="教授",
        email="han@ustc.edu.cn",
    )
    partial = adapter.parse_profile(html, CS_PROFILE_URL, item)

    assert partial.name_cn == "安虹"
    assert partial.email == "han@ustc.edu.cn"
    assert partial.title == "教授"
    # homepage either profile or staff.ustc.edu.cn external — never empty
    assert partial.homepage and partial.homepage.startswith("http")

    # research_interests from "主要研究方向 ：" — at least 3 well-formed tags
    assert len(partial.research_interests) >= 3, partial.research_interests
    assert all(len(t) <= 25 for t in partial.research_interests)
    assert all(
        "(" not in t and "（" not in t and "。" not in t
        for t in partial.research_interests
    )
    # 高性能计算 should be one of them
    joined = " ".join(partial.research_interests)
    assert "高性能计算" in joined or "并行" in joined

    # bio should be a real paragraph, not JS or nav
    assert partial.bio_text and len(partial.bio_text) > 50
    assert "function" not in partial.bio_text
    assert "<script" not in partial.bio_text
    assert "招生信息" not in partial.bio_text

    # 招生 signal should be detected via the "招生信息 ：" pseudo-header
    assert partial.is_recruiting is True
    assert partial.raw_quota_text and "招生" in partial.raw_quota_text
    # nav text must not leak in
    assert "学院概况" not in (partial.raw_quota_text or "")


# ---------------------------------------------------------------------------
# profile parsing — eeis (three flavours: empty / freeform / structured)
# ---------------------------------------------------------------------------


def test_parse_profile_eeis_structured(adapter: UstcAdapter) -> None:
    """Newer eeis profile (常晓军) uses <h2> section headers; we should pull
    a bio, research_interests, and an email."""
    html = (FIX / "profile_ai_info_recent.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="常晓军",
        profile_url=EEIS_PROFILE_RECENT_URL,
        title="教授",
    )
    partial = adapter.parse_profile(html, EEIS_PROFILE_RECENT_URL, item)

    assert partial.name_cn == "常晓军"
    # email should be picked up from the 联系方式 section (xjchang@ustc.edu.cn)
    assert partial.email == "xjchang@ustc.edu.cn"
    # mwave@ ... is the department address — must NOT win
    assert not (partial.email or "").startswith("mwave@")

    # research interests from the 研究方向 section, 3 paragraphs
    assert len(partial.research_interests) >= 2
    assert all(len(t) <= 25 for t in partial.research_interests)
    joined = " ".join(partial.research_interests)
    assert (
        "多模态" in joined or "具身" in joined or "类脑" in joined
    ), partial.research_interests

    assert partial.bio_text and "function" not in partial.bio_text
    assert "<script" not in partial.bio_text
    # the sidebar's "最新内容" must NOT have leaked into the article scope text
    if partial.raw_quota_text:
        assert "最新内容" not in partial.raw_quota_text


def test_parse_profile_eeis_freeform(adapter: UstcAdapter) -> None:
    """陈力 — profile is a single freeform paragraph with no section
    headers. We can't get a structured bio; we degrade to list-page fields
    plus whatever we can scrape from the text (external homepage link)."""
    html = (FIX / "profile_ai_info_chenli.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈力",
        profile_url=EEIS_PROFILE_FREEFORM_URL,
        title="教授",
    )
    partial = adapter.parse_profile(html, EEIS_PROFILE_FREEFORM_URL, item)

    # the freeform body lists an external staff page — homepage should pick it up
    assert partial.homepage and "staff.ustc.edu.cn" in partial.homepage


def test_parse_profile_eeis_empty(adapter: UstcAdapter) -> None:
    """陈畅 — profile body is empty (older eeis layout). The parser must
    not blow up and must preserve list-page fields."""
    html = (FIX / "profile_ai_info_chenchang.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈畅",
        profile_url=EEIS_PROFILE_EMPTY_URL,
        title="教授",
    )
    partial = adapter.parse_profile(html, EEIS_PROFILE_EMPTY_URL, item)

    assert partial.name_cn == "陈畅"
    assert partial.title == "教授"
    # no body content → no bio, no research interests, no email
    assert partial.research_interests == []
    # email should NOT be the department-wide mwave@ address
    assert not (partial.email or "").startswith("mwave@")
    # homepage should still be a valid URL (profile_url fallback)
    assert partial.homepage and partial.homepage.startswith("http")


# ---------------------------------------------------------------------------
# regression: name normalisation
# ---------------------------------------------------------------------------


def test_cs_list_name_no_double_space(adapter: UstcAdapter) -> None:
    """cs.ustc renders names with two spaces (e.g. ``安  虹``); the adapter
    must collapse them so downstream dedup keys are stable."""
    html = (FIX / "list_cs_prof_p1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, CS_PROF_LIST_URL)
    for it in items:
        assert " " not in it.name_cn, f"unexpected space in name: {it.name_cn!r}"
