"""NWPU adapter tests.

NWPU is one of the "国防七子" (Defence-affiliated Seven Sons). The
canonical per-teacher portal ``teacher.nwpu.edu.cn`` is behind a
TS-WAF JS challenge wall — every static GET returns a 2 KB stub —
so ``parse_profile`` against that host is expected to gracefully
degrade to list-item data only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.nwpu import NwpuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nwpu"

LIST_CS_URL = "https://jsj.nwpu.edu.cn/snew/szdw/szmd.htm"
LIST_SW_URL = "https://ruanjian.nwpu.edu.cn/szdw/jsdw.htm"
LIST_CSE_URL = "https://wlkjaqxy.nwpu.edu.cn/jsfc.htm"


@pytest.fixture
def adapter() -> NwpuAdapter:
    return NwpuAdapter()


# ---------------------------------------------------------------------------
# parse_list
# ---------------------------------------------------------------------------


def test_parse_list_cs_recovers_most_faculty(adapter: NwpuAdapter) -> None:
    """jsj.nwpu.edu.cn/snew/szdw/szmd.htm exposes ~175 anchors across 6
    sub-departments — we expect to recover ≥ 170 distinct faculty after
    dropping the single empty/orphan anchor."""
    html = (FIX / "list_cs_szmd.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_CS_URL)
    assert len(items) >= 170, f"only {len(items)} items"
    # Names + profile URLs are mandatory.
    assert all(it.name_cn for it in items)
    assert all(it.profile_url for it in items)
    # All names must be pure Han characters and short.
    for it in items:
        assert all("一" <= c <= "鿿" for c in it.name_cn), it.name_cn
        assert 2 <= len(it.name_cn) <= 4, it.name_cn
    # No duplicate profile URLs.
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls))
    # All profile URLs point at teacher.nwpu.edu.cn (the central portal).
    assert all("teacher.nwpu.edu.cn" in (it.profile_url or "") for it in items)
    # ≥ 90% have an inferred title (正高 → 教授 / 副高 → 副教授);
    # entries under "其他职称" keep title=None.
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.9


def test_parse_list_sw_returns_faculty_only(adapter: NwpuAdapter) -> None:
    """ruanjian.nwpu.edu.cn/szdw/jsdw.htm renders ~60 cards; ~15 are
    postdocs which the adapter drops, leaving ≥ 40 faculty PIs."""
    html = (FIX / "list_sw_jsdw.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_SW_URL)
    assert len(items) >= 40
    assert all(it.name_cn for it in items)
    # SW list cards always include name + title + photo.
    assert all(it.title for it in items), [
        i.name_cn for i in items if not i.title
    ]
    # Postdocs filtered out.
    assert not any("博士后" in (it.title or "") for it in items)
    # ≥ 80% carry a photo_url.
    with_photo = [it for it in items if it.photo_url]
    assert len(with_photo) / len(items) >= 0.8


def test_parse_list_cse_partial_coverage(adapter: NwpuAdapter) -> None:
    """wlkjaqxy.nwpu.edu.cn has no real faculty directory — only the
    教师风采 narrative articles. We recover ≥ 8 PIs from that
    surface and accept the rest as a known v0.5 limitation."""
    html = (FIX / "list_cse_jsfc.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_CSE_URL)
    assert len(items) >= 8
    assert all(it.name_cn for it in items)
    # Names must be 2-4 hanzi without leading prefix bleed-through.
    for it in items:
        assert 2 <= len(it.name_cn) <= 4
        assert all("一" <= c <= "鿿" for c in it.name_cn)
    # CSE profile URLs are intra-domain showcase articles.
    assert all("info/1090/" in (it.profile_url or "") for it in items)


# ---------------------------------------------------------------------------
# parse_profile
# ---------------------------------------------------------------------------


def test_parse_profile_ts_waf_stub_is_safe(adapter: NwpuAdapter) -> None:
    """Static GETs against teacher.nwpu.edu.cn return a TS-WAF stub.
    The adapter must detect the stub and return list-item data
    verbatim — never invent bio_text or scrape the JS challenge code."""
    html = (FIX / "profile_guobin.html").read_text(encoding="utf-8")
    li = ListItem(
        name_cn="郭斌",
        profile_url="https://teacher.nwpu.edu.cn/guobin.html",
        title="教授",
    )
    p = adapter.parse_profile(
        html, "https://teacher.nwpu.edu.cn/guobin.html", li
    )
    assert p.name_cn == "郭斌"
    assert p.title == "教授"
    assert p.bio_text is None
    # The TS-WAF challenge body contains a long $_ts.cd string; verify
    # none of that ends up in surfaced fields.
    for field in (p.bio_text, p.raw_quota_text):
        if field:
            assert "$_ts" not in field
            assert "_$ep" not in field


def test_parse_profile_cse_jsfc_extracts_bio(adapter: NwpuAdapter) -> None:
    """CSE jsfc articles are narrative profiles — we surface the
    article text as ``bio_text`` and ignore noisy "study direction"
    false positives (no "成果丰硕" / "学院教授" sneaking into tags)."""
    html = (FIX / "profile_cse_maobomin.html").read_text(encoding="utf-8")
    li = ListItem(
        name_cn="毛伯敏",
        profile_url="https://wlkjaqxy.nwpu.edu.cn/info/1090/9460.htm",
        title="教授",
    )
    p = adapter.parse_profile(
        html, "https://wlkjaqxy.nwpu.edu.cn/info/1090/9460.htm", li
    )
    assert p.bio_text and "毛伯敏" in p.bio_text
    # Anti-JS / anti-nav invariants.
    assert "function" not in p.bio_text
    assert "<script" not in p.bio_text
    # research_interests should either be empty (no explicit "研究方向：" in
    # this prose) or contain only well-formed tags.
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        assert "(" not in tag and "（" not in tag
        # Reject narrative noise like "成果丰硕".
        assert "丰硕" not in tag
        assert "学院" not in tag


def test_parse_profile_falls_back_for_unknown_host(adapter: NwpuAdapter) -> None:
    """An unrecognised profile host should produce an empty AdvisorPartial
    seeded only from the ListItem — never raise."""
    li = ListItem(
        name_cn="测试",
        profile_url="https://example.com/x",
        title=None,
    )
    p = adapter.parse_profile("<html></html>", "https://example.com/x", li)
    assert p.name_cn == "测试"
    assert p.bio_text is None
    assert p.research_interests == []
