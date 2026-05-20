"""Tests for the Tianjin University adapter (v0.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.tju import TjuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tju"


@pytest.fixture
def adapter() -> TjuAdapter:
    return TjuAdapter()


# ---------------------------------------------------------------------------
# parse_list — both indexes (by-title + by-name) share the cic template
# ---------------------------------------------------------------------------


# (fixture filename, list URL, expected min items)
LIST_CASES = [
    (
        "list_by_title.html",
        "https://cic.tju.edu.cn/szdw/szmd/azcjs.htm",
        140,  # actual 150
    ),
    (
        "list_by_name.html",
        "https://cic.tju.edu.cn/szdw/szmd/azmjs.htm",
        160,  # actual 170
    ),
]


@pytest.mark.parametrize("fname,url,min_items", LIST_CASES)
def test_parse_list_basic(
    adapter: TjuAdapter, fname: str, url: str, min_items: int
) -> None:
    html = (FIX / fname).read_text(encoding="utf-8")
    items = adapter.parse_list(html, url)
    assert len(items) >= min_items, f"{fname}: only got {len(items)} items"
    # Every entry must have a non-empty name and an absolute profile URL.
    for it in items:
        assert it.name_cn and it.name_cn.strip(), f"{fname}: empty name"
        assert it.profile_url and it.profile_url.startswith("http"), (
            f"{fname}: bad profile_url {it.profile_url!r}"
        )
        # No JS function calls or full-width punctuation leaking into name.
        assert "_showDynClickBatch" not in it.name_cn
        assert all(c not in it.name_cn for c in "()[]{}<>")
    # No duplicate profile URLs within a single page.
    urls = [it.profile_url for it in items]
    assert len(set(urls)) == len(urls), f"{fname}: duplicate profile URLs"


def test_parse_list_by_title_groups_titles(adapter: TjuAdapter) -> None:
    """by-title index attaches the section <h3> as the list-item title.

    The page has groups 教授 / 副教授 / 讲师 / 助教 / 研究员 /
    专任助理研究员 / 工程师 / 助理工程师 — at least 教授 and 副教授 should
    yield a non-trivial number of entries each.
    """
    html = (FIX / "list_by_title.html").read_text(encoding="utf-8")
    items = adapter.parse_list(
        html, "https://cic.tju.edu.cn/szdw/szmd/azcjs.htm"
    )
    titled = [it for it in items if it.title]
    assert len(titled) / len(items) >= 0.9, (
        f"only {len(titled)}/{len(items)} got a title from by-title index"
    )
    by_title: dict[str, int] = {}
    for it in items:
        if it.title:
            by_title[it.title] = by_title.get(it.title, 0) + 1
    assert by_title.get("教授", 0) >= 40, by_title
    assert by_title.get("副教授", 0) >= 40, by_title


def test_parse_list_by_name_leaves_titles_blank(adapter: TjuAdapter) -> None:
    """by-name index uses single-letter <h3> headers (A/B/C/...) — those
    must NOT be misread as titles.  Titles come from the profile page."""
    html = (FIX / "list_by_name.html").read_text(encoding="utf-8")
    items = adapter.parse_list(
        html, "https://cic.tju.edu.cn/szdw/szmd/azmjs.htm"
    )
    untitled = [it for it in items if it.title is None]
    # Every entry on by-name should land with title=None.
    assert len(untitled) == len(items), (
        f"{len(items) - len(untitled)} entries got a title from a letter h3"
    )


def test_dedup_across_indexes(adapter: TjuAdapter) -> None:
    """Both indexes carry the same person set; cic uses different
    ``info/<bucket>/`` numbers in each index, so URL dedupe can't catch
    overlaps — name-based dedupe (the pipeline's responsibility) must.

    This test asserts that the union of names across both indexes equals
    the by-name count (i.e. the by-name index is a superset of by-title)
    so the pipeline's (school, name, email) dedupe will collapse them
    cleanly.
    """
    html_t = (FIX / "list_by_title.html").read_text(encoding="utf-8")
    html_n = (FIX / "list_by_name.html").read_text(encoding="utf-8")
    items_t = adapter.parse_list(
        html_t, "https://cic.tju.edu.cn/szdw/szmd/azcjs.htm"
    )
    items_n = adapter.parse_list(
        html_n, "https://cic.tju.edu.cn/szdw/szmd/azmjs.htm"
    )
    names_t = {it.name_cn for it in items_t}
    names_n = {it.name_cn for it in items_n}
    # by-name should cover everything in by-title (by-name has more rows).
    missing = names_t - names_n
    assert not missing, f"by-name index missing names from by-title: {missing}"
    # Total unique faculty across both indexes should match by-name count.
    assert len(names_t | names_n) == len(names_n)


# ---------------------------------------------------------------------------
# parse_profile — uniform kv layout across all 5 fixtures
# ---------------------------------------------------------------------------


# (fixture filename, profile URL, name, list-item title, expected profile title,
#  expected email, expect_research)
PROFILE_CASES = [
    (
        "profile_liuanan.html",
        "https://cic.tju.edu.cn/info/1079/5936.htm",
        "刘安安", "教授", "讲席教授", "anan0422@gmail.com", True,
    ),
    (
        "profile_chenshizhan.html",
        "https://cic.tju.edu.cn/info/1067/4143.htm",
        "陈世展", "教授", "教授", "shizhan@tju.edu.cn", True,
    ),
    (
        "profile_hanyahong.html",
        "https://cic.tju.edu.cn/info/1079/1319.htm",
        "韩亚洪", "教授", "教授", "yahong@tju.edu.cn", True,
    ),
    (
        "profile_fengzhiyong.html",
        "https://cic.tju.edu.cn/info/1071/1172.htm",
        "冯志勇", "教授", "教授", "zyfeng@tju.edu.cn", True,
    ),
    (
        "profile_huqinghua.html",
        "https://cic.tju.edu.cn/info/1079/1323.htm",
        "胡清华", "教授", "教授", "huqinghua@tju.edu.cn", True,
    ),
]


_NAV_FORBIDDEN = (
    "首页",
    "学部概况",
    "组织机构",
    "学部领导",
    "通知公告",
    "新闻动态",
    "招生招聘",  # appears in 师资 nav menus, NOT a real recruitment signal
)


@pytest.mark.parametrize(
    "fname,url,name,list_title,prof_title,email,expect_research",
    PROFILE_CASES,
)
def test_parse_profile_no_js_no_nav(
    adapter: TjuAdapter,
    fname: str,
    url: str,
    name: str,
    list_title: str,
    prof_title: str,
    email: str,
    expect_research: bool,
) -> None:
    html = (FIX / fname).read_text(encoding="utf-8")
    li = ListItem(name_cn=name, profile_url=url, title=list_title)
    p = adapter.parse_profile(html, url, li)

    bio = p.bio_text or ""
    quota = p.raw_quota_text or ""

    # No JS / HTML tag leakage.
    assert "function" not in bio, f"{fname}: JS function leaked into bio"
    assert "<script" not in bio, f"{fname}: <script> tag leaked into bio"
    assert "_showDynClickBatch" not in bio, (
        f"{fname}: DynClick leaked into bio"
    )

    # No nav menu words.
    for nav in _NAV_FORBIDDEN:
        assert nav not in bio, f"{fname}: nav text {nav!r} in bio"
        assert nav not in quota, f"{fname}: nav text {nav!r} in quota"

    # Carry-forward.
    assert p.name_cn == name
    assert p.source_url == url

    # Profile title should override list title when profile gives a more
    # specific bucket (e.g. 讲席教授 over 教授).
    assert p.title == prof_title, f"{fname}: title {p.title!r} != {prof_title!r}"

    # Email extraction (no obfuscation on cic.tju profiles).
    assert p.email == email, f"{fname}: email {p.email!r} != {email!r}"
    assert p.email_obfuscated is False

    # Homepage should be an absolute URL (from 个人主页 field).
    assert p.homepage and p.homepage.startswith(("http://", "https://"))

    # Research tags hygiene.
    for t in p.research_interests:
        assert 2 <= len(t) <= 25, f"{fname}: tag length out of range: {t!r}"
        assert not any(c in t for c in "。！？()（）"), (
            f"{fname}: noisy tag {t!r}"
        )

    if expect_research:
        assert p.research_interests, (
            f"{fname}: expected non-empty research_interests"
        )


def test_parse_profile_has_research_or_bio(adapter: TjuAdapter) -> None:
    """Every profile fixture should yield at least bio_text or research."""
    for fname, url, name, list_title, _, _, _ in PROFILE_CASES:
        html = (FIX / fname).read_text(encoding="utf-8")
        li = ListItem(name_cn=name, profile_url=url, title=list_title)
        p = adapter.parse_profile(html, url, li)
        assert p.bio_text or p.research_interests, (
            f"{fname}: both bio_text and research_interests are empty"
        )


def test_split_label_value_glued(adapter: TjuAdapter) -> None:
    """Profile with label/value on separate DOM lines (韩亚洪 fixture) must
    still produce a clean email + research field — verifying that
    ``_pair_split_labels`` glues the lines back together."""
    html = (FIX / "profile_hanyahong.html").read_text(encoding="utf-8")
    li = ListItem(
        name_cn="韩亚洪",
        profile_url="https://cic.tju.edu.cn/info/1079/1319.htm",
        title="教授",
    )
    p = adapter.parse_profile(
        html, "https://cic.tju.edu.cn/info/1079/1319.htm", li
    )
    assert p.email == "yahong@tju.edu.cn"
    assert "多媒体分析" in p.research_interests
    assert p.homepage == "http://cic.tju.edu.cn/faculty/hanyahong/index.html"


def test_email_carries_from_list_item(adapter: TjuAdapter) -> None:
    """When a list page provides an email, parse_profile must preserve it."""
    html = (FIX / "profile_chenshizhan.html").read_text(encoding="utf-8")
    li = ListItem(
        name_cn="陈世展",
        profile_url="https://cic.tju.edu.cn/info/1067/4143.htm",
        title="教授",
        email="prefilled@tju.edu.cn",
    )
    p = adapter.parse_profile(
        html, "https://cic.tju.edu.cn/info/1067/4143.htm", li
    )
    assert p.email == "prefilled@tju.edu.cn"
    assert p.email_obfuscated is False
