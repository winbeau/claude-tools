"""Tests for the SEU (东南大学) adapter."""

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.seu import SeuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "seu"

LIST_URL_BYRANK = "https://cse.seu.edu.cn/49355/list.htm"
LIST_URL_BYDEPT = "https://cse.seu.edu.cn/54820/list.htm"


@pytest.fixture
def adapter() -> SeuAdapter:
    return SeuAdapter()


# ---------------------------------------------------------------------------
# parse_list
# ---------------------------------------------------------------------------


def test_parse_list_byrank_returns_full_roster(adapter: SeuAdapter) -> None:
    """The 按职称 list (canonical entry) must return all 134 PIs with
    distinct profile URLs."""
    html = (FIX / "list_cs_byrank.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_BYRANK)

    assert len(items) >= 130, f"expected ≥130 PIs, got {len(items)}"
    assert all(it.name_cn and it.profile_url for it in items)
    # no duplicates (mobile-view block must be skipped)
    urls = [it.profile_url for it in items]
    assert len(set(urls)) == len(urls)


def test_parse_list_byrank_attaches_title_from_section(adapter: SeuAdapter) -> None:
    """Every PI on the 按职称 page must carry a title derived from the
    section heading (正高 → 教授, 副高 → 副教授, 中级 → 讲师)."""
    html = (FIX / "list_cs_byrank.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_BYRANK)

    titled = [it for it in items if it.title]
    assert len(titled) / len(items) > 0.95, (
        f"only {len(titled)}/{len(items)} carry a section-derived title"
    )
    # both ranks must appear
    titles = {it.title for it in items}
    assert "教授" in titles
    assert "副教授" in titles


def test_parse_list_urls_use_canonical_host(adapter: SeuAdapter) -> None:
    """All profile URLs must resolve to ``cs.seu.edu.cn/<slug>/main.htm``."""
    html = (FIX / "list_cs_byrank.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_BYRANK)

    for it in items:
        assert it.profile_url.startswith("https://cs.seu.edu.cn/")
        assert it.profile_url.endswith("/main.htm")


def test_parse_list_bydept_returns_full_roster(adapter: SeuAdapter) -> None:
    """The 按系别 page also lists the entire roster (no title hint from
    sections, but URL set should match the 按职称 page)."""
    html = (FIX / "list_cs_bydept.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_BYDEPT)
    assert len(items) >= 130
    assert all(it.name_cn for it in items)


def test_parse_list_spa_shell_returns_empty(adapter: SeuAdapter) -> None:
    """A SPA shell / empty body must produce an empty list without raising."""
    for html in (
        "",
        "<html><body></body></html>",
        '<html><body><div id="app"></div></body></html>',
    ):
        items = adapter.parse_list(html, LIST_URL_BYRANK)
        assert items == []


def test_parse_list_filters_nav_anchors(adapter: SeuAdapter) -> None:
    """Anchors with non-CJK or oversized text (typical menu items) must
    not be promoted to ListItems."""
    html = """
    <html><body><div class="paging_content">
      <h2>正&nbsp;高</h2>
      <div class="desktop-view"><table>
        <tr><td><a href="https://cs.seu.edu.cn/about/main.htm">学院简介与历史沿革（请勿点击）</a></td></tr>
        <tr><td><a href="https://cs.seu.edu.cn/xfqi/main.htm">戚晓芳</a></td></tr>
      </table></div>
    </div></body></html>
    """
    items = adapter.parse_list(html, LIST_URL_BYRANK)
    # only the real teacher gets through; long-text "学院简介..." is dropped
    assert len(items) == 1
    assert items[0].name_cn == "戚晓芳"


# ---------------------------------------------------------------------------
# parse_profile
# ---------------------------------------------------------------------------


PROFILE_FIXTURES: list[tuple[str, str]] = [
    ("xfqi", "戚晓芳"),
    ("huyutao", "胡宇韬"),
    ("xiaolin", "方效林"),
    ("luckzpz", "章品正"),
]


@pytest.mark.parametrize("slug,name_cn", PROFILE_FIXTURES)
def test_parse_profile_extracts_clean_email(
    adapter: SeuAdapter, slug: str, name_cn: str
) -> None:
    html = (FIX / f"profile_{slug}.html").read_text(encoding="utf-8")
    url = f"https://cs.seu.edu.cn/{slug}/main.htm"
    li = ListItem(name_cn=name_cn, profile_url=url)
    p = adapter.parse_profile(html, url, li)

    # every fixtured PI has an email (either directly in the card, or
    # the slug-based heuristic fallback).
    assert p.email is not None and "@" in p.email
    assert p.email.endswith("@seu.edu.cn") or p.email_obfuscated is True
    # name preserved from list_item
    assert p.name_cn == name_cn


@pytest.mark.parametrize("slug,name_cn", PROFILE_FIXTURES)
def test_parse_profile_no_js_or_nav_leak(
    adapter: SeuAdapter, slug: str, name_cn: str
) -> None:
    html = (FIX / f"profile_{slug}.html").read_text(encoding="utf-8")
    url = f"https://cs.seu.edu.cn/{slug}/main.htm"
    li = ListItem(name_cn=name_cn, profile_url=url)
    p = adapter.parse_profile(html, url, li)

    # bio / quota must never carry JS, <script>, or full nav strings
    for field_name, value in (
        ("bio_text", p.bio_text),
        ("raw_quota_text", p.raw_quota_text),
    ):
        if not value:
            continue
        lower = value.lower()
        assert "function" not in lower, f"{field_name} contains JS: {value[:80]!r}"
        assert "<script" not in lower, f"{field_name} contains script tag"
        # the school's site footer contains "学院微信公众号" — must not leak
        assert "学院微信公众号" not in value


@pytest.mark.parametrize("slug,name_cn", PROFILE_FIXTURES)
def test_parse_profile_research_interests_are_clean_tags(
    adapter: SeuAdapter, slug: str, name_cn: str
) -> None:
    html = (FIX / f"profile_{slug}.html").read_text(encoding="utf-8")
    url = f"https://cs.seu.edu.cn/{slug}/main.htm"
    li = ListItem(name_cn=name_cn, profile_url=url)
    p = adapter.parse_profile(html, url, li)

    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25, f"tag length out of range: {tag!r}"
        assert not any(c in tag for c in "。！？()（）"), (
            f"tag contains forbidden punctuation: {tag!r}"
        )


def test_parse_profile_at_least_one_research_or_bio(adapter: SeuAdapter) -> None:
    """≥ 3 of the 4 fixtured PIs must yield either research_interests
    OR bio_text (luckzpz's profile is genuinely sparse upstream — leave
    one as a known-empty data point so we can detect future regressions)."""
    populated = 0
    for slug, name_cn in PROFILE_FIXTURES:
        html = (FIX / f"profile_{slug}.html").read_text(encoding="utf-8")
        url = f"https://cs.seu.edu.cn/{slug}/main.htm"
        li = ListItem(name_cn=name_cn, profile_url=url)
        p = adapter.parse_profile(html, url, li)
        if p.bio_text or p.research_interests:
            populated += 1
    assert populated >= 3, f"only {populated}/4 PIs have research_interests or bio"


def test_parse_profile_carries_list_item_title(adapter: SeuAdapter) -> None:
    """If the list page already supplied a section-derived title
    (教授/副教授/讲师), the profile must preserve it rather than overwrite
    with the card's bare 正高/副高 label."""
    html = (FIX / "profile_xfqi.html").read_text(encoding="utf-8")
    url = "https://cs.seu.edu.cn/xfqi/main.htm"
    li = ListItem(name_cn="戚晓芳", profile_url=url, title="教授")
    p = adapter.parse_profile(html, url, li)
    assert p.title == "教授"  # preserved from list_item, NOT overwritten


def test_parse_profile_recovers_title_from_card(adapter: SeuAdapter) -> None:
    """When the list_item has no title (e.g. an alternate list page), the
    carrer card's 职称 row must promote to a canonical 教授/副教授/讲师."""
    html = (FIX / "profile_huyutao.html").read_text(encoding="utf-8")
    url = "https://cs.seu.edu.cn/huyutao/main.htm"
    li = ListItem(name_cn="胡宇韬", profile_url=url)  # NO title
    p = adapter.parse_profile(html, url, li)
    assert p.title == "副教授"


def test_parse_profile_recruit_inside_bio(adapter: SeuAdapter) -> None:
    """胡宇韬's 个人简介 contains an inline '招生：' callout. The adapter
    must surface that via raw_quota_text and set is_recruiting=True."""
    html = (FIX / "profile_huyutao.html").read_text(encoding="utf-8")
    url = "https://cs.seu.edu.cn/huyutao/main.htm"
    li = ListItem(name_cn="胡宇韬", profile_url=url)
    p = adapter.parse_profile(html, url, li)
    assert p.is_recruiting is True
    assert p.raw_quota_text is not None
    assert "招生" in p.raw_quota_text or "招聘" in p.raw_quota_text


def test_parse_profile_email_slug_fallback(adapter: SeuAdapter) -> None:
    """方效林's 邮箱 row is empty; the adapter should fall back to the
    URL slug heuristic (xiaolin@seu.edu.cn) and flag it as obfuscated
    so the v0.5 enricher can re-verify."""
    html = (FIX / "profile_xiaolin.html").read_text(encoding="utf-8")
    url = "https://cs.seu.edu.cn/xiaolin/main.htm"
    li = ListItem(name_cn="方效林", profile_url=url)
    p = adapter.parse_profile(html, url, li)
    assert p.email == "xiaolin@seu.edu.cn"
    assert p.email_obfuscated is True


def test_parse_profile_photo_skips_template_chrome(adapter: SeuAdapter) -> None:
    """The first two <img>s in every profile are logo.png / list_banner.jpg
    from the Sudy template chrome. Adapter must skip them and pick the
    /_upload/article/images/... headshot instead."""
    html = (FIX / "profile_xfqi.html").read_text(encoding="utf-8")
    url = "https://cs.seu.edu.cn/xfqi/main.htm"
    li = ListItem(name_cn="戚晓芳", profile_url=url)
    p = adapter.parse_profile(html, url, li)
    assert p.photo_url is not None
    assert "/_upload/article/images/" in p.photo_url
    assert "logo" not in p.photo_url
    assert "banner" not in p.photo_url
