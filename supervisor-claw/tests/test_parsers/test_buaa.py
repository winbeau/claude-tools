"""Parser tests for the Beihang University (BUAA) adapter.

Fixtures were captured by curl with a real User-Agent on 2026-05-20 from
the official department sites:

* ``scse.buaa.edu.cn`` (计算机学院)
* ``soft.buaa.edu.cn`` (软件学院)
* ``iai.buaa.edu.cn`` (人工智能研究院)

The .html files live under ``tests/fixtures/buaa/`` and are checked in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.buaa import BuaaAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "buaa"


@pytest.fixture
def adapter() -> BuaaAdapter:
    return BuaaAdapter()


# ---------------------------------------------------------------------------
# List-page tests — per-dept aggregate must clear 15 (Step 4 of launch_buaa.md)
# ---------------------------------------------------------------------------


def test_parse_list_per_dept_cs(adapter: BuaaAdapter) -> None:
    """scse.buaa.edu.cn 师资队伍 is split across three rank pages.

    Aggregate across the three (教授 / 副教授 / 讲师) must clear the per-dept
    bar of 15 entries.
    """
    pages = [
        ("list_cs_js.html",  "https://scse.buaa.edu.cn/szdw/qtjs/js.htm"),
        ("list_cs_js1.html", "https://scse.buaa.edu.cn/szdw/qtjs/js1.htm"),
        ("list_cs_fjs.html", "https://scse.buaa.edu.cn/szdw/qtjs/fjs.htm"),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list((FIX / name).read_text(encoding="utf-8"), url)
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 15, f"cs aggregate only {len(all_items)} items"
    # Names always populated; profile URLs absolute .htm.
    assert all(it.name_cn.strip() for it in all_items)
    assert all(
        it.profile_url and it.profile_url.endswith(".htm") for it in all_items
    )
    # scse exposes email via <p class="yx"> on most cards.
    with_email = [it for it in all_items if it.email]
    assert len(with_email) / len(all_items) >= 0.5, (
        f"only {len(with_email)}/{len(all_items)} have email"
    )
    for it in with_email:
        assert "@" in it.email and it.email == it.email.lower()


def test_parse_list_per_dept_sw(adapter: BuaaAdapter) -> None:
    """soft.buaa.edu.cn — VSB SiteBuilder list with rank-keyed wbtreeid."""
    pages = [
        ("list_sw_prof.html",  "https://soft.buaa.edu.cn/tu-list-1.jsp?wbtreeid=1224"),
        ("list_sw_assoc.html", "https://soft.buaa.edu.cn/tu-list-1.jsp?wbtreeid=1262"),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list((FIX / name).read_text(encoding="utf-8"), url)
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 15, f"sw aggregate only {len(all_items)} items"
    assert all(it.name_cn.strip() for it in all_items)
    # All cards link to a profile page.
    assert all(it.profile_url for it in all_items)


def test_parse_list_per_dept_ai(adapter: BuaaAdapter) -> None:
    """iai.buaa.edu.cn — 按研究生导师 page is the dense single listing."""
    pages = [
        ("list_ai_ayjsdsjs.html", "https://iai.buaa.edu.cn/szdw/ayjsdsjs.htm"),
        ("list_ai_jcrc.html",     "https://iai.buaa.edu.cn/szdw/jcrc.htm"),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list((FIX / name).read_text(encoding="utf-8"), url)
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 15, f"ai aggregate only {len(all_items)} items"
    assert all(it.name_cn.strip() for it in all_items)
    # iai cards always carry a CJK name.
    for it in all_items:
        assert any("一" <= c <= "鿿" for c in it.name_cn), (
            f"non-Chinese list item: {it.name_cn!r}"
        )


# ---------------------------------------------------------------------------
# Profile-page tests — no nav leaks / no JS leaks / well-formed tags
# ---------------------------------------------------------------------------


def _stub(name: str, **kw) -> ListItem:
    return ListItem(name_cn=name, **kw)


def test_parse_profile_no_nav_cs(adapter: BuaaAdapter) -> None:
    html = (FIX / "profile_cs_qiandepei.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "https://scse.buaa.edu.cn/info/1078/2627.htm", _stub("钱德沛"),
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "function" not in p.bio_text
        assert "var page" not in p.bio_text
        assert "<script" not in p.bio_text.lower()
    if p.raw_quota_text:
        # scse global nav strings that must not leak in.
        assert "教务信息" not in p.raw_quota_text
        assert "学生工作" not in p.raw_quota_text
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        assert "(" not in tag and "（" not in tag
        assert tag[-1] not in "。！？"


def test_parse_profile_no_nav_sw(adapter: BuaaAdapter) -> None:
    html = (FIX / "profile_sw_huchunming.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "https://soft.buaa.edu.cn/info/1086/3001.htm", _stub("胡春明"),
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "function" not in p.bio_text
        assert "<script" not in p.bio_text.lower()
    # soft profile email is in the <dl> head — should be picked up.
    assert p.email and "@buaa.edu.cn" in p.email


def test_parse_profile_no_nav_ai(adapter: BuaaAdapter) -> None:
    html = (FIX / "profile_ai_dengyue.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "https://iai.buaa.edu.cn/info/1086/3001.htm", _stub("邓岳"),
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "function" not in p.bio_text
        assert "<script" not in p.bio_text.lower()
        # iai global side-nav strings that must not leak in.
        assert "新闻通知" not in p.bio_text
    # iai body carries plain-text "邮箱：x@buaa.edu.cn"; must be extracted.
    assert p.email and p.email.endswith("@buaa.edu.cn")


# ---------------------------------------------------------------------------
# Research-or-bio invariant — every profile fixture must yield one or both
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,url,name",
    [
        ("profile_cs_qiandepei.html",  "https://scse.buaa.edu.cn/info/1078/2627.htm", "钱德沛"),
        ("profile_cs_liuxudong.html",  "https://scse.buaa.edu.cn/info/1078/2628.htm", "刘旭东"),
        ("profile_sw_huchunming.html", "https://soft.buaa.edu.cn/info/1086/3001.htm", "胡春明"),
        ("profile_ai_wuwenjun.html",   "https://iai.buaa.edu.cn/info/1086/3001.htm", "吴文峻"),
        ("profile_ai_dengyue.html",    "https://iai.buaa.edu.cn/info/1086/3002.htm", "邓岳"),
    ],
)
def test_research_or_bio_present(
    adapter: BuaaAdapter, fixture: str, url: str, name: str
) -> None:
    html = (FIX / fixture).read_text(encoding="utf-8")
    p = adapter.parse_profile(html, url, _stub(name))
    assert (p.bio_text and len(p.bio_text) >= 50) or p.research_interests, (
        f"{fixture}: bio='{(p.bio_text or '')[:30]}…' ri={p.research_interests}"
    )
