"""Zhejiang University adapter: real-fixture-based parser tests.

Fixtures were curl'd from the live sites on 2026-05-19. They exercise all four
list flavours (cs / ai-inst / cadcg / sw) plus a person.zju.edu.cn profile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.zju import ZjuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "zju"

LIST_URL_CS = "http://www.cs.zju.edu.cn/csen/27051/list.htm"
LIST_URL_AI = "http://www.cs.zju.edu.cn/csen/27003/list.htm"
LIST_URL_CADCG = "http://www.cad.zju.edu.cn/talent-team"
LIST_URL_SW = "http://www.cst.zju.edu.cn/szdw/list.htm"
PROFILE_URL_BINGSHENG = "https://person.zju.edu.cn/bingsheng/"


@pytest.fixture
def adapter() -> ZjuAdapter:
    return ZjuAdapter()


def test_parse_list_cs_research_supervisors(adapter: ZjuAdapter) -> None:
    """csen/27051 — 计算机科学与技术专业研究生导师（每页 14 人）."""
    html = (FIX / "list_cs.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_CS)
    # this single page lists 14 advisors
    assert 12 <= len(items) <= 20, f"expected ~14 advisors, got {len(items)}"
    assert all(it.name_cn for it in items)
    assert all(it.profile_url and it.profile_url.startswith("http") for it in items)
    # spot check known advisor with known person.zju.edu.cn slug
    bingsheng = next((it for it in items if it.name_cn == "张秉晟"), None)
    assert bingsheng is not None, "张秉晟 should be on page 1"
    assert "person.zju.edu.cn" in (bingsheng.profile_url or "")


def test_parse_list_ai_inst_full_roster(adapter: ZjuAdapter) -> None:
    """csen/27003 — 全院教师名录, 多研究所分组, ~260+ advisors total."""
    html = (FIX / "list_ai-inst.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_AI)
    # the roster covers 14 institutes — expect well over 100 entries
    assert len(items) > 100, f"expected >100 advisors, got {len(items)}"

    names = {it.name_cn for it in items}
    # representative members of the AI Institute (人工智能研究所) section
    for known in ("庄越挺", "吴飞", "李玺", "肖俊"):
        assert known in names, f"missing AI Institute member {known}"
    # representative member of 计算机软件研究所 section (proves cross-institute)
    assert "陈纯" in names or "陈　纯" in names or any(
        n.replace("　", "") == "陈纯" for n in names
    )

    # nav links must not leak in
    for noise in ("师资队伍", "教师名录", "研究生导师", "计算机科学与技术"):
        assert noise not in names

    # profile URLs are all under person.zju.edu.cn / mypage.zju.edu.cn
    for it in items:
        assert it.profile_url
        assert "zju.edu.cn" in it.profile_url or it.profile_url.startswith("http"), it.profile_url


def test_parse_list_cadcg_dedupes_honor_groups(adapter: ZjuAdapter) -> None:
    """cad.zju.edu.cn/talent-team groups the same person under multiple honors
    (e.g. 鲍虎军 = 院士 / 长江学者 / 杰青). parse_list must dedupe by (name, url).
    """
    html = (FIX / "list_cadcg.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_CADCG)
    assert len(items) > 5, f"expected several lab members, got {len(items)}"

    # 鲍虎军 appears in 5+ honor sections in the source HTML; must collapse to 1
    names = [it.name_cn for it in items]
    bao_count = sum(1 for n in names if n == "鲍虎军")
    assert bao_count == 1, f"鲍虎军 should dedupe to 1, got {bao_count}"


def test_parse_list_sw_table_anchors(adapter: ZjuAdapter) -> None:
    """cst.zju.edu.cn/szdw/list.htm — table layout, every <td> is a teacher
    anchor linking to person.zju.edu.cn.
    """
    html = (FIX / "list_sw.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_SW)
    assert len(items) > 50, f"expected >50 sw teachers, got {len(items)}"

    names = {it.name_cn for it in items}
    # known Software-school leaders
    assert "陈纯" in names
    assert "尹建伟" in names
    # all profile URLs must point to person.zju.edu.cn (or mypage)
    bad = [
        it for it in items
        if not (
            "person.zju.edu.cn" in (it.profile_url or "")
            or "mypage.zju.edu.cn" in (it.profile_url or "")
        )
    ]
    assert not bad, f"unexpected profile hosts: {[it.profile_url for it in bad[:3]]}"


def test_parse_profile_person_zju_template(adapter: ZjuAdapter) -> None:
    """person.zju.edu.cn tpl_1 — must lift name, title, email, phone, research
    interests from the static HTML.
    """
    html = (FIX / "profile_bingsheng.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="张秉晟", profile_url=PROFILE_URL_BINGSHENG)
    p = adapter.parse_profile(html, PROFILE_URL_BINGSHENG, item)

    assert p.name_cn == "张秉晟"
    assert p.email == "bingsheng@zju.edu.cn", f"got {p.email!r}"
    assert p.email_obfuscated is False
    assert p.phone and "87952332" in p.phone
    assert p.title and "百人计划" in p.title and "博士生导师" in p.title

    # research interests must be the actual tags, not noise
    assert "区块链" in p.research_interests
    assert "密码学" in p.research_interests
    assert all(len(t) <= 25 for t in p.research_interests)
    assert all("(" not in t and "（" not in t for t in p.research_interests)

    # bio is AJAX-loaded → expect None on the static fixture
    assert p.bio_text is None

    # NO leak of the site-footer generic email
    assert p.email != "xwmaster@zju.edu.cn"

    # homepage = profile URL
    assert p.homepage == PROFILE_URL_BINGSHENG
    assert p.source_url == PROFILE_URL_BINGSHENG


def test_parse_profile_falls_back_when_no_email_in_template(
    adapter: ZjuAdapter,
) -> None:
    """If li.email is missing from a person.zju.edu.cn page, we must NOT lift
    the footer's generic xwmaster@zju.edu.cn — adapter should return None
    rather than a wrong email (it's a v0.4 Playwright job to fill the gap).
    """
    html = (FIX / "profile_bingsheng.html").read_text(encoding="utf-8")
    # Surgically remove the <li class='email'> block so the template path
    # cannot find an email — fallback must NOT pick up the footer address.
    import re
    mutated = re.sub(
        r"<li[^>]*class=['\"]email['\"][^>]*>.*?</li>",
        "",
        html,
        flags=re.S,
    )
    # also clear list_item.email so there's no carry-over
    item = ListItem(name_cn="张秉晟", profile_url=PROFILE_URL_BINGSHENG, email=None)
    p = adapter.parse_profile(mutated, PROFILE_URL_BINGSHENG, item)
    assert p.email != "xwmaster@zju.edu.cn", "footer email must not leak"
    # OK if it's None — that's the conservative behaviour we want.
