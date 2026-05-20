"""Tests for the Xidian (西安电子科技大学) adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.xidian import (
    XidianAdapter,
    _decode_html_if_bytes,
    _dept_from_url,
    _normalize_name,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "xidian"


@pytest.fixture
def adapter() -> XidianAdapter:
    return XidianAdapter()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalize_name_strips_internal_whitespace_and_suffix() -> None:
    assert _normalize_name("张 南") == "张南"
    assert _normalize_name("王 琨（男）") == "王琨"
    assert _normalize_name("  慕建君 ") == "慕建君"
    # Empty / falsy input must not crash.
    assert _normalize_name("") == ""


def test_dept_from_url_routes_per_host() -> None:
    assert _dept_from_url("https://cs.xidian.edu.cn/szdw/rcpy.htm") == "cs"
    assert _dept_from_url("https://sai.xidian.edu.cn/yjspy/dszy1.htm") == "ai"
    assert _dept_from_url("https://ce.xidian.edu.cn/") == "cse"
    # CSE list is hosted on the faculty portal under xyjslb.jsp.
    assert (
        _dept_from_url(
            "https://faculty.xidian.edu.cn/xyjslb.jsp?id=1596&lang=zh_CN"
        )
        == "cse"
    )


def test_decode_html_if_bytes_handles_gbk_and_utf8() -> None:
    # Bytes with a GBK meta should round-trip through gb18030.
    raw_gbk = (
        '<html><head><meta charset="gbk"></head><body>研究方向</body></html>'
    ).encode("gb18030")
    out = _decode_html_if_bytes(raw_gbk)
    assert "研究方向" in out
    # UTF-8 bytes default to utf-8 decoding.
    raw_utf = '<html><body>研究领域</body></html>'.encode("utf-8")
    assert "研究领域" in _decode_html_if_bytes(raw_utf)
    # str passthrough — must be returned unchanged.
    assert _decode_html_if_bytes("已是 utf-8 字符串") == "已是 utf-8 字符串"


# ---------------------------------------------------------------------------
# List parsers — per-dept fixtures
# ---------------------------------------------------------------------------


def test_parse_list_cs_rcpy(adapter: XidianAdapter) -> None:
    """cs.xidian.edu.cn/szdw/rcpy.htm — broadest single CS list."""
    html = (FIX / "list_cs_rcpy.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://cs.xidian.edu.cn/szdw/rcpy.htm")
    # We observed 162 distinct faculty links on this fixture; assert ≥ 100
    # so re-snapshots with minor pruning don't flake the test.
    assert len(items) >= 100, f"expected ≥ 100 PIs on cs rcpy, got {len(items)}"
    # Every item must carry a non-empty Chinese name and a faculty-portal URL.
    assert all(it.name_cn for it in items)
    assert all(
        any("一" <= c <= "鿿" for c in it.name_cn) for it in items
    ), "all CS PI names should contain CJK chars"
    assert all(
        (it.profile_url or "").startswith("https://faculty.xidian.edu.cn/")
        for it in items
    )
    # No nav labels / titles leaking into name_cn.
    forbidden_in_name = ("教授", "博士", "导师", "研究方向", "联系方式")
    for it in items:
        assert not any(
            tok in it.name_cn for tok in forbidden_in_name
        ), f"name leaked label: {it.name_cn!r}"


def test_parse_list_cs_dsjs(adapter: XidianAdapter) -> None:
    """cs.xidian.edu.cn/szdw/dsjs.htm — secondary list, must still parse."""
    html = (FIX / "list_cs_dsjs.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://cs.xidian.edu.cn/szdw/dsjs.htm")
    assert len(items) >= 50, f"expected ≥ 50 PIs on cs dsjs, got {len(items)}"
    assert all(it.profile_url and "faculty.xidian.edu.cn" in it.profile_url for it in items)


def test_parse_list_cse_xyjslb(adapter: XidianAdapter) -> None:
    """faculty.xidian.edu.cn/xyjslb.jsp?id=1596 — CSE college list (capped ≈ 20)."""
    html = (FIX / "list_cse_1596.html").read_text(encoding="utf-8")
    items = adapter.parse_list(
        html,
        "https://faculty.xidian.edu.cn/xyjslb.jsp?urltype=tsites.CollegeTeacherList&id=1596&lang=zh_CN",
    )
    # ≥ 10 is the documented minimum per Step 4 quality gate.
    assert len(items) >= 10
    # Most CSE entries carry a 职称 string from the .zc div.
    with_title = [it for it in items if it.title]
    assert len(with_title) / max(1, len(items)) >= 0.5
    # Titles must be faculty-like.
    for it in items:
        if it.title:
            assert any(
                kw in it.title
                for kw in ("教授", "副教授", "助理教授", "研究员", "讲师", "工程师")
            ), f"unexpected CSE title: {it.title!r}"


def test_parse_list_ai_dszy(adapter: XidianAdapter) -> None:
    """sai.xidian.edu.cn/yjspy/dszy1.htm — AI school dossier list."""
    html = (FIX / "list_ai_dszy1.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://sai.xidian.edu.cn/yjspy/dszy1.htm")
    assert len(items) >= 10, f"expected ≥ 10 PIs on AI list, got {len(items)}"
    assert all(it.name_cn for it in items)
    # AI list mixes faculty-portal links with web.xidian personal homepages —
    # both are valid profile destinations; just require non-empty URL.
    assert all(it.profile_url for it in items)
    # At least one entry should target the faculty portal.
    assert any(
        "faculty.xidian.edu.cn" in (it.profile_url or "") for it in items
    )


def test_parse_list_dedup_within_page(adapter: XidianAdapter) -> None:
    """Same profile_url must never appear twice in a list-page result."""
    html = (FIX / "list_cs_rcpy.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://cs.xidian.edu.cn/szdw/rcpy.htm")
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls))


# ---------------------------------------------------------------------------
# Profile parser — must work on all three sampled depts
# ---------------------------------------------------------------------------


def test_parse_profile_cs_mjj_no_js_no_nav(adapter: XidianAdapter) -> None:
    """CS profile (慕建君) — bio + research interests, no JS leak."""
    html = (FIX / "profile_cs_MJJ.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="慕建君",
        profile_url="https://faculty.xidian.edu.cn/MJJ/zh_CN/index.htm",
    )
    p = adapter.parse_profile(
        html, "https://faculty.xidian.edu.cn/MJJ/zh_CN/index.htm", item
    )
    # No JS/script artifacts in bio.
    assert p.bio_text and "function" not in p.bio_text
    assert "<script>" not in p.bio_text
    assert "jQuery" not in p.bio_text
    # Bio must mention 西安电子科技大学 (anti-mojibake spot check).
    assert "西安电子科技大学" in p.bio_text
    # Title pulled from t_jbxx_nr.
    assert p.title == "教授"
    # Research interests parsed from yjfx anchors.
    assert p.research_interests
    # Tag hygiene: ≤ 25 chars, no parentheses, no terminal periods.
    for tag in p.research_interests:
        assert 1 < len(tag) <= 25
        assert not any(c in tag for c in "()（）。！？")


def test_parse_profile_ai_baijing_extracts_inline_email(
    adapter: XidianAdapter,
) -> None:
    """AI profile (白静) — email is inlined inside the bio (recruiting blurb)."""
    html = (FIX / "profile_ai_BJ2.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="白静",
        profile_url="https://faculty.xidian.edu.cn/BJ2/zh_CN/index.htm",
    )
    p = adapter.parse_profile(
        html, "https://faculty.xidian.edu.cn/BJ2/zh_CN/index.htm", item
    )
    assert p.email and p.email.endswith("@mail.xidian.edu.cn")
    # AI school PIs all teach (so 教授 title pulled from 基本信息).
    assert p.title == "教授"
    # Anti-mojibake check: bio must contain proper CJK.
    assert p.bio_text and "西安电子科技大学" in p.bio_text
    assert "�" not in (p.bio_text or "")
    assert p.research_interests


def test_parse_profile_cse_obfuscated_email(adapter: XidianAdapter) -> None:
    """CSE profile (曾勇) — email hidden inside <span _tsites_encrypt_field>."""
    html = (FIX / "profile_cse_CY8.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="曾勇",
        title="副教授",
        profile_url="https://faculty.xidian.edu.cn/CY8/zh_CN/index.htm",
    )
    p = adapter.parse_profile(
        html, "https://faculty.xidian.edu.cn/CY8/zh_CN/index.htm", item
    )
    # No plaintext email recoverable → must surface as obfuscated.
    assert p.email is None
    assert p.email_obfuscated is True
    # Title carried through from ListItem.
    assert p.title == "副教授"
    # Encoded research direction anchor produced ≥ 3 tags.
    assert p.research_interests and len(p.research_interests) >= 3
    # CJK roundtrip — adapter shouldn't leak replacement chars.
    assert "�" not in (p.bio_text or "")
    assert p.bio_text and "西安电子科技大学" in p.bio_text


def test_parse_profile_quality_thresholds(adapter: XidianAdapter) -> None:
    """Aggregate quality gate over all 3 sampled profiles."""
    samples = [
        ("profile_cs_MJJ.html", "慕建君", "MJJ"),
        ("profile_ai_BJ2.html", "白静", "BJ2"),
        ("profile_cse_CY8.html", "曾勇", "CY8"),
    ]
    bios = 0
    interests = 0
    titles = 0
    for fname, name, fid in samples:
        html = (FIX / fname).read_text(encoding="utf-8")
        url = f"https://faculty.xidian.edu.cn/{fid}/zh_CN/index.htm"
        item = ListItem(name_cn=name, profile_url=url)
        p = adapter.parse_profile(html, url, item)
        if p.bio_text and len(p.bio_text) > 30:
            bios += 1
        if p.research_interests:
            interests += 1
        if p.title:
            titles += 1
    assert bios == 3, f"expected bios on all 3 samples, got {bios}"
    assert interests == 3, f"expected research_interests on all 3 samples, got {interests}"
    assert titles == 3, f"expected titles on all 3 samples, got {titles}"


def test_parse_profile_unknown_host_returns_empty_partial(
    adapter: XidianAdapter,
) -> None:
    """web.xidian.edu.cn personal homepages must not crash the adapter."""
    item = ListItem(
        name_cn="焦李成",
        profile_url="http://web.xidian.edu.cn/lchjiao/",
    )
    p = adapter.parse_profile(
        "<html><body>placeholder</body></html>",
        "http://web.xidian.edu.cn/lchjiao/",
        item,
    )
    assert p.name_cn == "焦李成"
    assert p.source_url == "http://web.xidian.edu.cn/lchjiao/"
    # No bio expected — but obfuscation must default to True since we found no email.
    assert p.bio_text is None
    assert p.email_obfuscated is True


# ---------------------------------------------------------------------------
# Adapter integration smoke
# ---------------------------------------------------------------------------


def test_adapter_metadata() -> None:
    a = XidianAdapter()
    assert a.school_code == "xidian"
    assert a.supports == {"cs", "cse", "ai"}
    assert a.supports_dept("cs")
    assert a.supports_dept("cse")
    assert a.supports_dept("ai")
    assert not a.supports_dept("sw")
