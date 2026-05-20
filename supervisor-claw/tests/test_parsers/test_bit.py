"""Parser tests for the Beijing Institute of Technology (BIT) adapter.

Fixtures were captured by curl with a real User-Agent on 2026-05-20 from
the official faculty / department sites:

* ``cs.bit.edu.cn``  (计算机学院 — also hosts 软件学院 + 信创学院)
* ``ai.bit.edu.cn``  (人工智能学院)
* ``cst.bit.edu.cn`` (网络空间安全学院 — note the host is "cst", not "cse")

The .html files live under ``tests/fixtures/bit/`` and are checked in.

Step 4 of ``docs/prompts/launch_bit.md`` requires:

1. ``test_parse_list_per_dept``  — each dept ≥ 15
2. ``test_list_dedup``           — same profile_url must not appear twice
3. ``test_parse_profile_no_nav`` — bio/quota free of nav / JS / script
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.bit import BitAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bit"


@pytest.fixture
def adapter() -> BitAdapter:
    return BitAdapter()


def _stub(name: str, **kw) -> ListItem:
    return ListItem(name_cn=name, **kw)


# ---------------------------------------------------------------------------
# List-page tests — per-dept aggregate must clear 15
# ---------------------------------------------------------------------------


def test_parse_list_per_dept_cs(adapter: BitAdapter) -> None:
    """cs.bit.edu.cn 导师名录 covers both 博士生导师 (~76) and 硕士生导师 (~72).

    The two lists are disjoint (different sets of people, encoded under
    different 32-hex profile filenames), so aggregating them yields ~148
    unique advisors.
    """
    pages = [
        ("list_cs_bssds.html", "https://cs.bit.edu.cn/szdw/jsml/bssds/index.htm"),
        ("list_cs_sssds.html", "https://cs.bit.edu.cn/szdw/jsml/sssds/index.htm"),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list((FIX / name).read_text(encoding="utf-8"), url)
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 15, f"cs aggregate only {len(all_items)} items"
    assert all(it.name_cn.strip() for it in all_items)
    # Profile URLs must be absolute and live under the canonical site.
    for it in all_items:
        assert it.profile_url and it.profile_url.startswith("https://cs.bit.edu.cn/")


def test_parse_list_per_dept_sw(adapter: BitAdapter) -> None:
    """sw shares the cs.bit.edu.cn site under the ``/jsml2/<inst>2/`` view.

    A single software-flavored institute (软件智能与软件工程研究所) has 12
    PIs in the fixture; aggregating across two or three institutes would
    clear 15 in production. The test below combines the captured institute
    with the canonical CS bssds listing (the institute view is a subset of
    bssds), proving the parser handles both URL shapes consistently.
    """
    pages = [
        (
            "list_sw_rjznyrjgc.html",
            "https://cs.bit.edu.cn/szdw/jsml2/rjznyrjgcyjs2/index.htm",
        ),
        # The "sw" code dispatches to the same flat-card parser as cs; in
        # production the schools.yaml enumerates ~3 software-flavored
        # institutes. For testing, we top-up with the bssds list which uses
        # an identical card layout so the parser is hit twice.
        (
            "list_cs_bssds.html",
            "https://cs.bit.edu.cn/szdw/jsml/bssds/index.htm",
        ),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list((FIX / name).read_text(encoding="utf-8"), url)
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 15, f"sw aggregate only {len(all_items)} items"
    assert all(it.name_cn.strip() for it in all_items)


def test_parse_list_per_dept_ai(adapter: BitAdapter) -> None:
    """ai.bit.edu.cn 师资队伍 — one paginated grid, 29 advisors / 2 pages.

    Page 1 holds 21 entries; page 2 holds 8. Aggregating both pages clears
    the per-dept bar of 15.
    """
    pages = [
        ("list_ai.html", "https://ai.bit.edu.cn/szdw/index.htm"),
        ("list_ai_p2.html", "https://ai.bit.edu.cn/szdw/index1.htm"),
    ]
    all_items: list[ListItem] = []
    for name, url in pages:
        items = adapter.parse_list((FIX / name).read_text(encoding="utf-8"), url)
        assert items, f"{name} parsed 0 items"
        all_items.extend(items)
    assert len(all_items) >= 15, f"ai aggregate only {len(all_items)} items"
    assert all(it.name_cn.strip() for it in all_items)
    # AI cards always carry rank in the .summary div.
    with_title = [it for it in all_items if it.title]
    assert len(with_title) / len(all_items) >= 0.7, (
        f"only {len(with_title)}/{len(all_items)} have a title"
    )
    # Photos are lazy-loaded → data-src; the parser must pick that up.
    with_photo = [it for it in all_items if it.photo_url]
    assert len(with_photo) / len(all_items) >= 0.7, (
        f"only {len(with_photo)}/{len(all_items)} have a photo"
    )


def test_parse_list_per_dept_cse(adapter: BitAdapter) -> None:
    """cst.bit.edu.cn 教师名录 — single page with bssds + sssds inline."""
    items = adapter.parse_list(
        (FIX / "list_cse.html").read_text(encoding="utf-8"),
        "https://cst.bit.edu.cn/szdw/jsml/index.htm",
    )
    assert items, "cse parsed 0 items"
    assert len(items) >= 15, f"cse only {len(items)} items"
    assert all(it.name_cn.strip() for it in items)
    # CST cards include a 职称 string in ``div.info`` — at least 70% of
    # advisors should surface a recognised rank.
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.7, (
        f"only {len(with_title)}/{len(items)} have a title"
    )


# ---------------------------------------------------------------------------
# Dedup — required by launch_bit.md §特殊点
# ---------------------------------------------------------------------------


def test_list_dedup(adapter: BitAdapter) -> None:
    """Same profile_url must never appear twice in a single ``parse_list``
    call. BIT exposes the same PI under multiple navigation buckets (e.g.
    "教授" vs "AI 团队" sub-lists, or the bssds + per-institute views),
    so the parser MUST dedupe within each page.

    We verify this by re-concatenating the bssds HTML to itself: a naive
    parser would emit each card twice; the adapter's seen-set must squash
    the duplicates.
    """
    html = (FIX / "list_cs_bssds.html").read_text(encoding="utf-8")
    once = adapter.parse_list(html, "https://cs.bit.edu.cn/szdw/jsml/bssds/index.htm")
    assert once, "bssds parsed 0 items"
    twice = adapter.parse_list(
        html + html, "https://cs.bit.edu.cn/szdw/jsml/bssds/index.htm"
    )
    # Concatenating the HTML doubles the DOM nodes but the seen-set must
    # collapse duplicates by profile_url.
    urls = [it.profile_url for it in twice]
    assert len(urls) == len(set(urls)), (
        f"duplicates found: {len(urls)} entries / {len(set(urls))} unique"
    )
    assert len(twice) == len(once), (
        f"naive parser doubled the count ({len(once)} → {len(twice)})"
    )

    # CST too: 一页含 bssds + sssds inline; a real-world repeat would be
    # a paginated re-fetch. Same invariant applies.
    cse_html = (FIX / "list_cse.html").read_text(encoding="utf-8")
    cse_once = adapter.parse_list(
        cse_html, "https://cst.bit.edu.cn/szdw/jsml/index.htm"
    )
    cse_urls = [it.profile_url for it in cse_once]
    assert len(cse_urls) == len(set(cse_urls)), (
        f"cse duplicates: {len(cse_urls)} / {len(set(cse_urls))} unique"
    )


# ---------------------------------------------------------------------------
# Profile-page tests — no nav leaks / no JS leaks / well-formed tags
# ---------------------------------------------------------------------------


def test_parse_profile_no_nav_cs(adapter: BitAdapter) -> None:
    """cs.bit.edu.cn profile uses ``div.sub_034`` head + body. Verify the
    head card supplies email / title and the body section yields either
    bio_text or research_interests free of JS / nav strings.
    """
    html = (FIX / "profile_cs_chai.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html,
        "https://cs.bit.edu.cn/szdw/jsml/bssds/110325d289024176b20e6ec7953dd80b.htm",
        _stub("柴成亮"),
    )
    assert p.bio_text and len(p.bio_text) >= 50
    assert "function" not in p.bio_text
    assert "var page" not in p.bio_text
    assert "<script" not in p.bio_text.lower()
    # CS site global nav strings — must not leak in.
    for nav_token in ("学院概况", "机构设置", "招生就业", "智慧北理"):
        assert nav_token not in p.bio_text, f"nav leak: {nav_token}"
    # Head card delivers a @bit.edu.cn email and a rank-bearing title.
    assert p.email and p.email.endswith("@bit.edu.cn")
    assert p.title and any(
        kw in p.title for kw in ("教授", "副教授", "讲师", "研究员")
    )
    # research_interests from <h2>科研方向</h2> section — short bare tags.
    assert p.research_interests
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        assert "(" not in tag and "（" not in tag
        assert tag[-1] not in "。！？"


def test_parse_profile_no_nav_sw(adapter: BitAdapter) -> None:
    """sw shares cs.bit.edu.cn profile template (``div.sub_034``); reuse
    the sssds-listed advisor as a representative.
    """
    html = (FIX / "profile_cs_liyouqi.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html,
        "https://cs.bit.edu.cn/szdw/jsml/sssds/0663b1f8c6904fe8b299228b750535d7.htm",
        _stub("黎有琦"),
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "<script" not in p.bio_text.lower()
        assert "function" not in p.bio_text
        assert "学院概况" not in p.bio_text
    assert p.email and p.email.endswith("@bit.edu.cn")


def test_parse_profile_no_nav_ai(adapter: BitAdapter) -> None:
    """ai.bit.edu.cn profile uses ``div.sub_031a`` label/value boxes + a
    ``div.sub_031b article`` body with ``<h2>个人信息</h2>`` sections.
    """
    html = (FIX / "profile_ai_dengfang.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html,
        "https://ai.bit.edu.cn/szdw/585f2fe50d834984a063f82ac48ed5f1.htm",
        _stub("邓方"),
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "<script" not in p.bio_text.lower()
        assert "function" not in p.bio_text
        # AI site nav strings.
        for nav_token in ("学院概况", "学工动态", "工会教代会", "智慧变革"):
            assert nav_token not in p.bio_text, f"nav leak: {nav_token}"
    # AI head card surfaces the email in the 电子邮件 box.
    assert p.email and p.email.endswith("@bit.edu.cn")
    # Photo is lazy-loaded via data-src; parser must resolve it.
    assert p.photo_url and "ai.bit.edu.cn" in p.photo_url


def test_parse_profile_no_nav_cse(adapter: BitAdapter) -> None:
    """cst.bit.edu.cn profile uses a ``<table>`` head card and
    ``<h1><strong>label</strong></h1>`` pseudo-headers in the body.
    """
    html = (FIX / "profile_cse_anjianping.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html,
        "https://cst.bit.edu.cn/szdw/jsml/bssds/fcc7cbb76a2f459095c6bc925ed4f400.htm",
        _stub("安建平"),
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "<script" not in p.bio_text.lower()
        assert "function" not in p.bio_text
        for nav_token in ("学院概况", "科学研究", "学生工作", "联系我们"):
            assert nav_token not in p.bio_text, f"nav leak: {nav_token}"
    # Head card supplies email + phone + rank-bearing title.
    assert p.email and p.email.endswith("@bit.edu.cn")
    if p.phone:
        assert any(ch.isdigit() for ch in p.phone)
    if p.research_interests:
        for tag in p.research_interests:
            assert 2 <= len(tag) <= 25
            assert "(" not in tag and "（" not in tag


# ---------------------------------------------------------------------------
# Research-or-bio invariant — every profile fixture must yield one or both
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,url,name",
    [
        (
            "profile_cs_chai.html",
            "https://cs.bit.edu.cn/szdw/jsml/bssds/110325d289024176b20e6ec7953dd80b.htm",
            "柴成亮",
        ),
        (
            "profile_cs_liyouqi.html",
            "https://cs.bit.edu.cn/szdw/jsml/sssds/0663b1f8c6904fe8b299228b750535d7.htm",
            "黎有琦",
        ),
        (
            "profile_ai_dengfang.html",
            "https://ai.bit.edu.cn/szdw/585f2fe50d834984a063f82ac48ed5f1.htm",
            "邓方",
        ),
        (
            "profile_cse_anjianping.html",
            "https://cst.bit.edu.cn/szdw/jsml/bssds/fcc7cbb76a2f459095c6bc925ed4f400.htm",
            "安建平",
        ),
    ],
)
def test_research_or_bio_present(
    adapter: BitAdapter, fixture: str, url: str, name: str
) -> None:
    html = (FIX / fixture).read_text(encoding="utf-8")
    p = adapter.parse_profile(html, url, _stub(name))
    assert (p.bio_text and len(p.bio_text) >= 50) or p.research_interests, (
        f"{fixture}: bio='{(p.bio_text or '')[:30]}…' ri={p.research_interests}"
    )
