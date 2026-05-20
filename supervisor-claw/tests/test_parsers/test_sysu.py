"""Sun Yat-sen University adapter — real-fixture-based parser tests.

Fixtures were curl'd from the live cse / sse / sai sysu sub-sites on
2026-05-20. They cover the three CS/AI-relevant schools spread across the
Guangzhou and Zhuhai campuses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.sysu import SysuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sysu"

LIST_URL_CS = "https://cse.sysu.edu.cn/teacher"
LIST_URL_SW = "https://sse.sysu.edu.cn/teacher"
LIST_URL_SW_ASSOC = "https://sse.sysu.edu.cn/teacher/associate_professor"
LIST_URL_AI = "https://sai.sysu.edu.cn/teachers"


@pytest.fixture
def adapter() -> SysuAdapter:
    return SysuAdapter()


# ---------------------------------------------------------------------------
# list parsing — one test per dept (≥ 15 each per launch_sysu.md Step 4)
# ---------------------------------------------------------------------------


def test_parse_list_per_dept_cs(adapter: SysuAdapter) -> None:
    """cse.sysu.edu.cn /teacher renders 110 ``div.facultyblock`` cards in one
    page. Each card carries name + title + email + research direction
    inline so the list page alone covers > 95% of the data we want."""
    html = (FIX / "list_cs.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_CS)

    assert len(items) >= 100, f"cs: expected >=100 cards, got {len(items)}"
    assert all(it.name_cn and it.profile_url for it in items)
    # URLs must be absolute and unique
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls))
    assert all(u.startswith("https://cse.sysu.edu.cn/teacher/") for u in urls)

    # CSE prints plaintext emails on the list page — coverage must be very high
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) > 0.9, (
        f"cs email coverage too low: {len(with_email)}/{len(items)}"
    )
    for it in with_email:
        assert it.email == it.email.lower()
        assert "@" in it.email

    # spot-check known faculty
    names = {it.name_cn for it in items}
    for known in ("郑伟诗", "余超", "陈鹏飞", "蔡穗华"):
        assert known in names, f"missing CS faculty {known}"

    # no navigation text leaked as a teacher row
    for noise in ("教师名录", "师资队伍", "全部", "教授", "副教授"):
        assert noise not in names


def test_parse_list_per_dept_sw(adapter: SysuAdapter) -> None:
    """sse.sysu.edu.cn /teacher is the aggregate landing for all ranks
    (教授 / 副教授 / 助理教授 / 讲师 / 研究员). list-images-1-1 card layout.

    Email coverage on this list page is intentionally sparse (only ~15%);
    the profile parser fills in the rest."""
    html = (FIX / "list_sw.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_SW)

    assert len(items) >= 30, f"sw: expected >=30 cards, got {len(items)}"
    assert all(it.name_cn and it.profile_url for it in items)
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls))
    assert all(u.startswith("https://sse.sysu.edu.cn/teacher/") for u in urls)

    # title coverage should be high — every card has the rank in the h4 span
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) > 0.8

    # known dean / department head
    names = {it.name_cn for it in items}
    assert "郑子彬" in names


def test_parse_list_sw_associate_subpage(adapter: SysuAdapter) -> None:
    """Sanity-check that the per-rank sub-pages parse with the same layout.
    The associate_professor sub-page has only ~20 entries, all 副教授."""
    html = (FIX / "list_sw_associate.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_SW_ASSOC)
    assert len(items) >= 15
    # all titles should be 副教授 on this sub-page
    for it in items:
        if it.title:
            assert "副教授" in it.title


def test_parse_list_per_dept_ai(adapter: SysuAdapter) -> None:
    """sai.sysu.edu.cn /teachers (note plural). Same list-images-1-1 cards
    as sse, but emails are plaintext on the list page so coverage is high."""
    html = (FIX / "list_ai.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_AI)

    assert len(items) >= 25, f"ai: expected >=25 cards, got {len(items)}"
    assert all(it.name_cn and it.profile_url for it in items)
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls))
    # sai uses /teacher/<id> (singular path inside the dept), not /teachers/
    assert all(u.startswith("https://sai.sysu.edu.cn/teacher/") for u in urls)

    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) > 0.8, (
        f"ai email coverage too low: {len(with_email)}/{len(items)}"
    )

    # spot-check known faculty
    names = {it.name_cn for it in items}
    for known in ("董润敏", "谷德峰"):
        assert known in names, f"missing AI faculty {known}"


# ---------------------------------------------------------------------------
# profile parsing — no nav / no JS / has research or bio
# ---------------------------------------------------------------------------


def test_parse_profile_cs_full_metadata(adapter: SysuAdapter) -> None:
    """蔡穗华 — cse profile with full top-card metadata + body sections."""
    html = (FIX / "profile_cs_caisuihua.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="蔡穗华",
        profile_url="https://cse.sysu.edu.cn/teacher/CaiSuihua",
        title="副教授",
        email="caish23@mail.sysu.edu.cn",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "蔡穗华"
    assert p.title and "副教授" in p.title
    assert p.email == "caish23@mail.sysu.edu.cn"
    assert p.bio_text and "信息论" in p.bio_text
    assert p.research_interests
    assert any("信息论" in t for t in p.research_interests)


def test_parse_profile_cs_recruiting(adapter: SysuAdapter) -> None:
    """余超 — cse profile with explicit 招生信息 hints in the body."""
    html = (FIX / "profile_cs_yuchao.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="余超",
        profile_url="https://cse.sysu.edu.cn/teacher/YuChao",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "余超"
    # email recovered from the profile head card even when list_item lacks it
    assert p.email and p.email.endswith("@mail.sysu.edu.cn")
    assert p.bio_text and "强化学习" in p.bio_text
    assert p.research_interests
    # recruiting flag fires
    assert p.is_recruiting is True
    assert p.raw_quota_text and "招" in p.raw_quota_text


def test_parse_profile_sw_dean(adapter: SysuAdapter) -> None:
    """郑子彬 — sse dean. Profile has both 研究领域 + recruiting prose
    bundled in a single paragraph; the parser must split them so neither
    pollutes the other. Also tests the faculty-email vs student-email
    preference (zhzibin@mail.sysu.edu.cn over chenzhx69@mail2.sysu.edu.cn)."""
    html = (FIX / "profile_sw_zhengzibin.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="郑子彬",
        profile_url="https://sse.sysu.edu.cn/teacher/100",
        title="教授",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "郑子彬"
    assert p.title and "教授" in p.title
    # must pick the faculty mail (mail.sysu.edu.cn), NOT the student mail2
    assert p.email == "zhzibin@mail.sysu.edu.cn"
    assert p.bio_text and "区块链" in p.bio_text
    assert p.research_interests
    # research tags are clean — no recruiting text bled in
    for t in p.research_interests:
        assert 2 <= len(t) <= 25
        assert "招生" not in t and "欢迎" not in t and "本科" not in t
    assert p.is_recruiting is True


def test_parse_profile_ai_research_and_recruit(adapter: SysuAdapter) -> None:
    """董润敏 — sai profile using <h5><strong>label</strong></h5> section
    headers and a combined 研究与招生 block."""
    html = (FIX / "profile_ai_dongrunmin.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="董润敏",
        profile_url="https://sai.sysu.edu.cn/teacher/546",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "董润敏"
    assert p.bio_text and "计算机视觉" in p.bio_text
    assert p.email == "dongrm3@mail.sysu.edu.cn"
    assert p.research_interests
    # AI school's 研究与招生 mixes research + recruiting + URL in one section;
    # the tag list must reject the URL / 招生 boilerplate.
    blob = " ".join(p.research_interests)
    assert "http" not in blob
    assert "欢迎" not in blob and "招生" not in blob


# ---------------------------------------------------------------------------
# common quality invariants
# ---------------------------------------------------------------------------


def test_no_js_or_nav_leak_in_any_bio(adapter: SysuAdapter) -> None:
    """Every fixture profile must produce a clean bio + recruit string —
    no inline JS, no school footer / navigation text."""
    cases = [
        ("profile_cs_caisuihua.html",
         "https://cse.sysu.edu.cn/teacher/CaiSuihua", "蔡穗华"),
        ("profile_cs_yuchao.html",
         "https://cse.sysu.edu.cn/teacher/YuChao", "余超"),
        ("profile_sw_zhengzibin.html",
         "https://sse.sysu.edu.cn/teacher/100", "郑子彬"),
        ("profile_ai_dongrunmin.html",
         "https://sai.sysu.edu.cn/teacher/546", "董润敏"),
    ]
    for fname, url, name in cases:
        html = (FIX / fname).read_text(encoding="utf-8")
        item = ListItem(name_cn=name, profile_url=url)
        p = adapter.parse_profile(html, url, item)

        blob = (p.bio_text or "") + "\n" + (p.raw_quota_text or "")
        for bad in ("function(", "var ", "<script", "</style>", "@media"):
            assert bad not in blob, f"{name}: leaked JS/CSS token {bad!r}"
        for nav in (
            "教务部", "统一门户", "研究生院", "学生工作部", "人力资源",
            "学院新闻", "学院概况", "学院领导", "首 页", "联系我们",
            "院长信箱", "中山大学计算机学院 版权所有",
        ):
            assert nav not in blob, f"{name}: nav token {nav!r} leaked"

        for t in p.research_interests:
            assert 2 <= len(t) <= 25, f"{name}: tag too long: {t!r}"
            assert all(c not in t for c in "。！？()（）"), (
                f"{name}: noisy tag: {t!r}"
            )

        # Each profile must have either bio_text or research_interests
        assert p.bio_text or p.research_interests, (
            f"{name}: neither bio nor research extracted"
        )
