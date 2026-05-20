"""Parser tests for the Huazhong University of Science and Technology (HUST) adapter.

Fixtures were captured by curl with a real User-Agent on 2026-05-20 from
the official department sites:

* ``cs.hust.edu.cn``    (计算机科学与技术学院)
* ``sse.hust.edu.cn``   (软件学院)
* ``aia.hust.edu.cn``   (人工智能与自动化学院, AIA)
* ``faculty.hust.edu.cn`` (统一的教师个人主页系统)

The .html files live under ``tests/fixtures/hust/`` and are checked in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.hust import HustAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "hust"


@pytest.fixture
def adapter() -> HustAdapter:
    return HustAdapter()


# ---------------------------------------------------------------------------
# List-page tests — per-dept thresholds (Step 4 of launch_hust.md)
#   cs ≥ 80, sw ≥ 20, ai ≥ 20
# ---------------------------------------------------------------------------


def test_parse_list_per_dept_cs(adapter: HustAdapter) -> None:
    """cs.hust.edu.cn 师资队伍 按研究所列表 — single ``ayjslb.htm`` page
    holds every PI grouped by research institute.
    """
    html = (FIX / "list_cs_ayjslb.html").read_text(encoding="utf-8")
    items = adapter.parse_list(
        html, "https://cs.hust.edu.cn/szdw/jsml/ayjslb.htm"
    )
    assert len(items) >= 80, f"cs only {len(items)} items"
    assert all(it.name_cn.strip() for it in items)
    assert all(it.profile_url and it.profile_url.startswith("http") for it in items)
    # Group label (研究所名) flows in via the ``title`` field on ListItem.
    titles = {it.title for it in items if it.title}
    assert any("所" in t or "中心" in t for t in titles), (
        f"no research-institute group labels: {sorted(titles)[:5]}"
    )
    # Each profile URL must be CJK-named.
    for it in items:
        assert any("一" <= c <= "鿿" for c in it.name_cn), (
            f"non-Chinese list item: {it.name_cn!r}"
        )


def test_parse_list_per_dept_sw(adapter: HustAdapter) -> None:
    """sse.hust.edu.cn 教授 + 副教授 + 讲师 三页加总 ≥ 20."""
    pages = [
        ("list_sw_js_yjy.html", "https://sse.hust.edu.cn/szdw1/js_yjy.htm"),
        ("list_sw_fjs.html",    "https://sse.hust.edu.cn/szdw1/fjs.htm"),
        ("list_sw_js1.html",    "https://sse.hust.edu.cn/szdw1/js1.htm"),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list(
            (FIX / name).read_text(encoding="utf-8"), url,
        )
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 20, f"sw aggregate only {len(all_items)} items"
    assert all(it.name_cn.strip() for it in all_items)
    assert all(it.profile_url for it in all_items)
    # All entries should be CJK-named.
    for it in all_items:
        assert any("一" <= c <= "鿿" for c in it.name_cn)


def test_parse_list_per_dept_ai(adapter: HustAdapter) -> None:
    """aia.hust.edu.cn 按系列表 — single page holds all ranks (正高/副高/中级)."""
    html = (FIX / "list_ai_axlb.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://aia.hust.edu.cn/szdw/xysz/axlb.htm")
    assert len(items) >= 20, f"ai only {len(items)} items"
    assert all(it.name_cn.strip() for it in items)
    # AIA covers 自动化 + AI — the v0.3 enricher will narrow down. Just
    # check we got non-trivial coverage.
    assert len(items) >= 50, (
        f"AIA expected ~120 (自动化+AI 合院)，actual {len(items)}"
    )


# ---------------------------------------------------------------------------
# Profile-page tests — no nav leaks / no JS leaks / well-formed tags
# ---------------------------------------------------------------------------


def _stub(name: str, **kw) -> ListItem:
    return ListItem(name_cn=name, **kw)


def test_parse_profile_no_nav_faculty(adapter: HustAdapter) -> None:
    """faculty.hust.edu.cn (the blockwhite template) — emails are encrypted,
    so ``email`` is None and ``email_obfuscated`` must be True. Bio prose
    must be clean (no JS / no nav).
    """
    html = (FIX / "profile_faculty_caoqiang.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "http://faculty.hust.edu.cn/caoqiang/zh_CN/index.htm", _stub("曹强"),
    )
    assert p.name_cn == "曹强"
    assert p.title and "教授" in p.title
    # Encrypted email — adapter cannot decode; must flag obfuscated.
    assert p.email is None
    assert p.email_obfuscated is True
    # Bio must contain the actual prose and be free of JS leaks.
    assert p.bio_text
    assert "function" not in p.bio_text
    assert "<script" not in p.bio_text.lower()
    assert "ImageScale" not in p.bio_text
    assert "var _" not in p.bio_text
    assert "TsitesPraiseUtil" not in p.bio_text
    # Nav strings must not leak into bio or quota text.
    for nav in ("师资队伍", "招生招聘", "党群工作", "通知公告"):
        assert nav not in p.bio_text
        if p.raw_quota_text:
            assert nav not in p.raw_quota_text
    # Research tags well-formed.
    assert p.research_interests
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        assert "(" not in tag and "（" not in tag
        assert tag[-1] not in "。！？"
    # Photo URL recovered from the inline ImageScale.addimg() call.
    assert p.photo_url and p.photo_url.startswith("http")


def test_parse_profile_no_nav_internal_aia(adapter: HustAdapter) -> None:
    """aia.hust.edu.cn /info/.../X.htm (the v_news_content legacy template)
    — structured fields + plaintext email.
    """
    html = (FIX / "profile_aia_internal_deng.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "https://aia.hust.edu.cn/info/1691/10467.htm", _stub("邓忠华"),
    )
    assert p.name_cn == "邓忠华"
    assert p.title and "教授" in p.title
    # Structured email and phone.
    assert p.email and p.email == "zhonghua.deng@mail.hust.edu.cn"
    assert p.email_obfuscated is False
    assert p.phone and "027" in p.phone
    # Research interests well-formed (3 tags from the comma-separated line).
    assert len(p.research_interests) >= 2
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        assert "(" not in tag
    # Bio must exist and be clean.
    assert p.bio_text and len(p.bio_text) >= 50
    assert "function" not in p.bio_text
    assert "<script" not in p.bio_text.lower()
    # Nav strings must not leak in.
    for nav in ("学院概况", "组织机构", "本科生教育", "通知公告"):
        assert nav not in p.bio_text


def test_parse_profile_no_nav_internal_sw(adapter: HustAdapter) -> None:
    """sse.hust.edu.cn /info/.../X.htm — 白翔's profile is the prose-only
    variant of the v_news_content template (no structured field <p>'s).
    Bio prose must be extracted and the global footer email must NOT leak in.
    """
    html = (FIX / "profile_sw_info_baixiang.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "http://sse.hust.edu.cn/info/1029/2384.htm", _stub("白翔"),
    )
    assert p.name_cn == "白翔"
    assert p.bio_text and len(p.bio_text) >= 100
    assert "function" not in p.bio_text
    assert "<script" not in p.bio_text.lower()
    # Footer emails (sse@hust, ssedean@hust) must be filtered out as
    # they aren't the teacher's address.
    assert p.email != "sse@hust.edu.cn"
    assert p.email != "ssedean@hust.edu.cn"
    assert p.email != "sseshuji@hust.edu.cn"


# ---------------------------------------------------------------------------
# Research-or-bio invariant — every profile fixture must yield one or both
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,url,name",
    [
        (
            "profile_faculty_caoqiang.html",
            "http://faculty.hust.edu.cn/caoqiang/zh_CN/index.htm",
            "曹强",
        ),
        (
            "profile_aia_internal_deng.html",
            "https://aia.hust.edu.cn/info/1691/10467.htm",
            "邓忠华",
        ),
        (
            "profile_sw_info_baixiang.html",
            "http://sse.hust.edu.cn/info/1029/2384.htm",
            "白翔",
        ),
    ],
)
def test_research_or_bio_present(
    adapter: HustAdapter, fixture: str, url: str, name: str
) -> None:
    html = (FIX / fixture).read_text(encoding="utf-8")
    p = adapter.parse_profile(html, url, _stub(name))
    assert (p.bio_text and len(p.bio_text) >= 50) or p.research_interests, (
        f"{fixture}: bio='{(p.bio_text or '')[:30]}…' ri={p.research_interests}"
    )
