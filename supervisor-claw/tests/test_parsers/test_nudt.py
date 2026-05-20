"""NUDT (国防科技大学) adapter tests.

NUDT is a PLA-affiliated military university. Its public web presence
is deliberately minimal — there are no per-PI profile pages, no
emails, no research interests on any externally reachable URL. The
adapter therefore vacuums *honour rosters* (院士、杰青、优青、百千万、
求是杰青、全国优秀科技工作者) under ``www.nudt.edu.cn/szdw/`` and
emits name-only ListItems.

Test expectations are intentionally loose:

* ``parse_list`` returns ≥ 5 distinct names on each honour page.
* names are clean 2-4 hanzi (no leading/trailing whitespace, no
  bracket bleed-through).
* ``profile_url`` is ``None`` (NUDT never links to per-PI pages).
* ``parse_profile`` is a no-op safety net — it must not crash and
  must surface an honour-label bio.

Known limitations (we deliberately do NOT assert):

* Email / research_interests coverage (always 0 on NUDT).
* Per-department attribution (honour pages never say 学院).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.nudt import NudtAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nudt"

LIST_LYYS_URL = "https://www.nudt.edu.cn/szdw/lyys/index.htm"
LIST_GJJCQN_URL = "https://www.nudt.edu.cn/szdw/gjjcqnkxjjhdz/index.htm"
LIST_GJYXQN_URL = "https://www.nudt.edu.cn/szdw/gjyxqnkxjj/index.htm"
LIST_BQWRC_URL = "https://www.nudt.edu.cn/szdw/bqwrcgcgjjrx/index.htm"
LIST_KXQS_URL = "https://www.nudt.edu.cn/szdw/gjkxqsjcqnsygcjhdz/index.htm"
LIST_QGYX_URL = "https://www.nudt.edu.cn/szdw/qgyxkjgzz/index.htm"


@pytest.fixture
def adapter() -> NudtAdapter:
    return NudtAdapter()


# ---------------------------------------------------------------------------
# parse_list — main coverage tests
# ---------------------------------------------------------------------------


def test_parse_list_lyys_two_sections(adapter: NudtAdapter) -> None:
    """两院院士 page splits into 中国科学院院士 + 中国工程院院士. The
    adapter must respect the ``<h3>`` section header so each name's
    title reflects the correct academy (not a generic "院士")."""
    html = (FIX / "list_lyys.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_LYYS_URL)
    # ≥ 15 academicians total (CAS + CAE combined).
    assert len(items) >= 15, f"only {len(items)} items"
    # Section split must surface both labels.
    cas = [i for i in items if i.title == "中国科学院院士"]
    cae = [i for i in items if i.title == "中国工程院院士"]
    assert len(cas) >= 5
    assert len(cae) >= 5
    # 100% names + zero profile URLs (NUDT never links to per-PI).
    assert all(it.name_cn for it in items)
    assert all(it.profile_url is None for it in items)
    # Sanity: names are short Han hanzi, no whitespace leakage.
    for it in items:
        assert 2 <= len(it.name_cn) <= 4, it.name_cn
        assert all("一" <= c <= "鿿" for c in it.name_cn), it.name_cn
    # No duplicates within a single page.
    names = [it.name_cn for it in items]
    assert len(names) == len(set(names))


def test_parse_list_gjjcqn_includes_year_in_title(adapter: NudtAdapter) -> None:
    """国家杰青 / 国家优青 carry an elected year that's useful for
    downstream dedupe (人 + 当选年). The adapter encodes it as
    ``国家杰青(2020)`` etc."""
    html = (FIX / "list_gjjcqn.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_GJJCQN_URL)
    assert len(items) >= 10
    assert all(it.name_cn for it in items)
    assert all(it.profile_url is None for it in items)
    # ≥ 80% should carry a year-bearing title like 国家杰青(2020).
    with_year = [it for it in items if it.title and "国家杰青" in it.title and "(" in it.title]
    assert len(with_year) / len(items) >= 0.8


def test_parse_list_bqwrc_flat_honour(adapter: NudtAdapter) -> None:
    """百千万人才工程 page has no section headers — all rows share the
    same honour label."""
    html = (FIX / "list_bqwrc.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_BQWRC_URL)
    assert len(items) >= 15
    assert all(it.name_cn for it in items)
    # All entries get the bare honour label (no year suffix for this
    # category — too noisy to surface 13 different years).
    assert all(
        it.title == "百千万人才工程国家级人选" for it in items
    ), [(it.name_cn, it.title) for it in items if it.title != "百千万人才工程国家级人选"]


def test_parse_list_qgyx_basic(adapter: NudtAdapter) -> None:
    """全国优秀科技工作者 — smaller roster but same layout."""
    html = (FIX / "list_qgyx.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_QGYX_URL)
    assert len(items) >= 10
    assert all(it.title == "全国优秀科技工作者" for it in items)


# ---------------------------------------------------------------------------
# parse_list — robustness
# ---------------------------------------------------------------------------


def test_parse_list_handles_unknown_host(adapter: NudtAdapter) -> None:
    """A list URL pointing at a non-NUDT host must short-circuit to
    an empty list rather than parsing arbitrary HTML."""
    items = adapter.parse_list(
        "<html><body><a>张三</a></body></html>",
        "https://example.com/szdw/index.htm",
    )
    assert items == []


def test_parse_list_handles_empty_html(adapter: NudtAdapter) -> None:
    """An empty / truncated body must not raise."""
    items = adapter.parse_list("", LIST_LYYS_URL)
    assert items == []


# ---------------------------------------------------------------------------
# parse_profile — defensive no-op
# ---------------------------------------------------------------------------


def test_parse_profile_safe_with_none_profile(adapter: NudtAdapter) -> None:
    """NUDT list items have profile_url=None; if the pipeline somehow
    still routes them here (e.g. a future enricher synthesises a URL),
    we must produce a list-only AdvisorPartial without crashing."""
    li = ListItem(
        name_cn="胡德文",
        profile_url=None,
        title="中国科学院院士",
    )
    p = adapter.parse_profile("<html></html>", "https://www.nudt.edu.cn/", li)
    assert p.name_cn == "胡德文"
    assert p.title == "中国科学院院士"
    # No real bio is available, but we surface an honour label as a
    # placeholder so the DB row isn't entirely blank.
    assert p.bio_text and "胡德文" in p.bio_text
    assert "院士" in p.bio_text
    # Anti-noise invariants.
    assert "function" not in p.bio_text
    assert "<script" not in p.bio_text
    # Email / research are deliberately empty on NUDT.
    assert p.email is None
    assert p.research_interests == []


def test_parse_profile_safe_with_empty_list_item(adapter: NudtAdapter) -> None:
    """Bare ListItem (no title) — bio falls back to None gracefully."""
    li = ListItem(name_cn="测试", profile_url=None)
    p = adapter.parse_profile("", "https://www.nudt.edu.cn/x", li)
    assert p.name_cn == "测试"
    assert p.title is None
    # No title => no synthetic bio.
    assert p.bio_text is None


# ---------------------------------------------------------------------------
# Cross-page dedupe sanity (known limitation as known-good behaviour)
# ---------------------------------------------------------------------------


def test_dedupe_across_pages_is_pipeline_concern(adapter: NudtAdapter) -> None:
    """The same person (e.g. 胡德文) appears on both 两院院士 and
    全国优秀科技工作者. The adapter does NOT dedupe across pages — the
    pipeline upsert handles that via (school, name_cn, email). This
    test pins that contract so a future refactor can't silently merge."""
    lyys = adapter.parse_list(
        (FIX / "list_lyys.html").read_text(encoding="utf-8"), LIST_LYYS_URL
    )
    qgyx = adapter.parse_list(
        (FIX / "list_qgyx.html").read_text(encoding="utf-8"), LIST_QGYX_URL
    )
    lyys_names = {i.name_cn for i in lyys}
    qgyx_names = {i.name_cn for i in qgyx}
    # 胡德文 should be on both pages — confirms our scraper sees the
    # overlap, and the pipeline must dedupe downstream.
    assert "胡德文" in lyys_names
    assert "胡德文" in qgyx_names
    # Cross-page label divergence (different honour titles on the two
    # pages) — pipeline will pick whichever it processes last.
    hu_lyys = next(i for i in lyys if i.name_cn == "胡德文")
    hu_qgyx = next(i for i in qgyx if i.name_cn == "胡德文")
    assert hu_lyys.title != hu_qgyx.title


# ---------------------------------------------------------------------------
# Known limitations — documented as xfail so they don't silently regress
# if NUDT one day exposes a real roster.
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="NUDT has no public per-PI profile pages — see module docstring."
)
def test_profile_bio_from_real_html() -> None:
    """Placeholder: when v0.5 Playwright fetcher cracks intranet access,
    re-enable this with a real captured profile fixture."""
    pass


@pytest.mark.skip(
    reason="NUDT honour pages never expose emails — DeepSeek enricher fills these."
)
def test_email_coverage() -> None:
    """Placeholder for the email-coverage assertion every other school's
    test suite has. NUDT is the one place we cannot make it."""
    pass
