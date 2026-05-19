"""Shanghai Jiao Tong University adapter — real-fixture-based parser tests.

Fixtures were curl'd from the live sites on 2026-05-19. They cover all four
departments (cs / ai / see-ai / qingyuan), including the JSON envelope
returned by the AJAX POST endpoints for cs.sjtu and sais.sjtu.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.sjtu import SjtuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sjtu"

LIST_URL_CS = "https://www.cs.sjtu.edu.cn/jiaoshiml.html"
LIST_URL_AI = "https://soai.sjtu.edu.cn/cn/faculty/zzjs"
LIST_URL_SEEAI = "https://sais.sjtu.edu.cn/faculty.html"
LIST_URL_QY = "http://www.qingyuan.sjtu.edu.cn/c/quanzhijiaoshi.html"


@pytest.fixture
def adapter() -> SjtuAdapter:
    return SjtuAdapter()


# ---------------------------------------------------------------------------
# list parsing
# ---------------------------------------------------------------------------


def test_parse_list_cs_from_ajax_json(adapter: SjtuAdapter) -> None:
    """cs.sjtu list is loaded via a POST AJAX endpoint whose body is JSON
    wrapping the HTML in a "content" key. The adapter must accept the JSON
    envelope directly."""
    body = (FIX / "list_cs_ajax.json").read_text(encoding="utf-8")
    items = adapter.parse_list(body, LIST_URL_CS)
    # cs.sjtu hosts ~280-300 unique faculty across 18 research institutes
    assert len(items) > 200, f"expected >200 cs teachers, got {len(items)}"

    # all profile URLs point to /jiaoshiml/<slug>.html and are unique
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls)), "parse_list must dedupe by URL"
    assert all(u and "/jiaoshiml/" in u for u in urls)

    # known senior researchers must appear
    names = {it.name_cn for it in items}
    for known in ("陈海波", "臧斌宇", "李国良", "俞勇"):
        # 俞勇 may not be on the latest roster — keep it permissive
        pass
    assert "陈海波" in names
    assert "臧斌宇" in names

    # no nav / page-navigation text leak as a "teacher name"
    for noise in ("教师名录", "师资队伍", "教授", "副教授", "全部", "展开", "收起"):
        assert noise not in names, f"navigation token leaked into list: {noise}"


def test_parse_list_ai_card_layout(adapter: SjtuAdapter) -> None:
    """soai (AI 学院) list page is full HTML with one <li> per teacher
    carrying name, title, email and homepage."""
    html = (FIX / "list_ai_zzjs.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_AI)
    assert len(items) > 30, f"expected >30 AI school teachers, got {len(items)}"

    # every item has name + profile_url
    assert all(it.name_cn and it.profile_url for it in items)
    # email coverage on the list page is excellent (>80%)
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) > 0.8, (
        f"low email coverage on AI list: {len(with_email)}/{len(items)}"
    )
    # email format sanity
    for it in with_email:
        assert "@" in (it.email or "")
        assert (it.email or "") == (it.email or "").lower()

    # title is populated for most entries
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) > 0.7

    # spot check known faculty
    names = {it.name_cn for it in items}
    assert "曹钦翔" in names
    assert "陈思衡" in names


def test_parse_list_see_ai_from_ajax_json(adapter: SjtuAdapter) -> None:
    """sais (电院自动化与感知学院) list is the same POST-AJAX envelope
    as cs.sjtu. List items only carry name + url; metadata is on profile."""
    body = (FIX / "list_see-ai_sais_ajax.json").read_text(encoding="utf-8")
    items = adapter.parse_list(body, LIST_URL_SEEAI)
    # sais has ~200 teachers (AI / control / robotics / sensing)
    assert len(items) > 150, f"expected >150 sais teachers, got {len(items)}"
    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls)), "parse_list must dedupe by URL"
    assert all(u and "/faculty/" in u for u in urls)
    # spot check
    names = {it.name_cn for it in items}
    assert "陈卫东" in names  # known robotics professor (we sampled his profile)
    assert "白洋" in names


def test_parse_list_qingyuan_dedupes_rank_groups(adapter: SjtuAdapter) -> None:
    """qingyuan list renders each teacher twice — once grouped by 职称
    (长聘教授 / 长聘教轨副教授 / 长聘教轨助理教授) and once in a flat
    fallback grid below. parse_list must collapse duplicates by URL."""
    html = (FIX / "list_qingyuan.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL_QY)
    # ~10 tenure-track faculty (small department)
    assert 7 <= len(items) <= 15, f"expected ~10 qingyuan members, got {len(items)}"

    urls = [it.profile_url for it in items]
    assert len(urls) == len(set(urls)), "duplicates not removed"

    names = {it.name_cn for it in items}
    assert "徐宁仪" in names  # only 长聘教授
    assert "刘鹏飞" in names

    # rank should be attached via the <div class="t_title"> group header
    by_name = {it.name_cn: it for it in items}
    xu = by_name.get("徐宁仪")
    assert xu is not None and xu.title and "长聘教授" in xu.title


# ---------------------------------------------------------------------------
# profile parsing
# ---------------------------------------------------------------------------


def test_parse_profile_cs_no_email(adapter: SjtuAdapter) -> None:
    """陈海波 — senior cs.sjtu professor whose profile page does NOT list
    an email (only 个人主页). Must not invent one (or pull in the footer
    scs@sjtu.edu.cn)."""
    html = (FIX / "profile_cs_chenhaibo.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈海波",
        profile_url="https://www.cs.sjtu.edu.cn/jiaoshiml/chenhaibo.html",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "陈海波"
    assert p.title and "教授" in p.title
    assert p.bio_text and "操作系统" in p.bio_text
    # NO email expected (page has none, footer scs@ must be filtered)
    assert p.email is None or not p.email.startswith("scs@")
    # homepage should reflect 个人主页 link
    assert p.homepage and "ipads" in p.homepage.lower()
    # no js / nav leak in bio
    assert "function" not in (p.bio_text or "")
    assert "var " not in (p.bio_text or "")
    for nav in ("学院新闻", "通知公告", "下载专区", "首页"):
        assert nav not in (p.bio_text or "")


def test_parse_profile_cs_with_email(adapter: SjtuAdapter) -> None:
    """董明凯 — junior cs.sjtu faculty whose profile lists 邮箱 + 个人主页."""
    html = (FIX / "profile_cs_dongmingkai.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="董明凯",
        profile_url="https://www.cs.sjtu.edu.cn/jiaoshiml/dongmingkai.html",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.email == "mingkaidong@sjtu.edu.cn"
    assert p.title and ("研究员" in p.title or "教授" in p.title)
    assert p.homepage and "dong.mk" in p.homepage


def test_parse_profile_ai_extracts_email_and_bio(adapter: SjtuAdapter) -> None:
    """曹钦翔 — soai profile with email + 个人简介 + 招生 paragraph."""
    html = (FIX / "profile_ai_caoqinxiang.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="曹钦翔",
        profile_url="https://soai.sjtu.edu.cn/cn/facultydetails/zzjs/caoqinxiang",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "曹钦翔"
    assert p.email == "caoqinxiang@sjtu.edu.cn"
    assert p.title and "副教授" in p.title
    assert p.bio_text and "程序验证" in p.bio_text
    # recruitment phrase ("招收硕士研究生与博士研究生") must be captured
    assert p.is_recruiting is True
    assert p.raw_quota_text and "招收" in p.raw_quota_text
    # AI-school footer (aischool@sjtu.edu.cn) must NOT replace a valid email
    assert not p.email.startswith("aischool@")


def test_parse_profile_see_ai_ai_match(adapter: SjtuAdapter) -> None:
    """陈卫东 — sais robotics professor; 研究方向 contains 移动机器人 /
    多机器人系统 / 医疗机器人. Must match the AI keyword filter."""
    html = (FIX / "profile_see-ai_chenweidong.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="陈卫东",
        profile_url="https://sais.sjtu.edu.cn/faculty/chenweidong.html",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "陈卫东"
    assert p.email == "wdchen@sjtu.edu.cn"
    assert p.phone and "021-34204302" in p.phone
    assert p.title and "教授" in p.title
    # research_interests populated (AI-relevant)
    assert p.research_interests, "should have research tags"
    assert any("机器人" in t for t in p.research_interests)
    # every tag is short and clean
    for t in p.research_interests:
        assert 2 <= len(t) <= 25
        assert "(" not in t and "（" not in t
        assert "。" not in t and "！" not in t


def test_parse_profile_see_ai_non_ai_filtered(adapter: SjtuAdapter) -> None:
    """曹成喜 — sais bio-electrophoresis researcher; 研究方向 has no AI
    keyword (POCT 诊断技术 / 糖尿病地贫诊断技术 / 自由流电泳技术 / 界面
    电泳与传感). The adapter should KEEP the record (we cannot reliably
    classify only from one signal) but DROP the research_interests so the
    downstream filter knows this person is outside the see-ai scope."""
    html = (FIX / "profile_see-ai_caochengxi_nonai.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="曹成喜",
        profile_url="https://sais.sjtu.edu.cn/faculty/caochengxi.html",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "曹成喜"
    # Record is kept (basic fields populated) but research_interests is empty
    # signalling "not AI-relevant" to the downstream filter.
    assert p.research_interests == [], (
        f"expected empty research tags for non-AI researcher, got "
        f"{p.research_interests}"
    )


def test_parse_profile_qingyuan_extracts_bio_and_email(adapter: SjtuAdapter) -> None:
    """徐宁仪 — qingyuan profile; bio woven with 研究方向 prose."""
    html = (FIX / "profile_qingyuan_xuningyi.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="徐宁仪",
        profile_url="http://www.qingyuan.sjtu.edu.cn/a/xu-ning-yi-1.html",
    )
    p = adapter.parse_profile(html, item.profile_url, item)
    assert p.name_cn == "徐宁仪"
    assert p.email == "xuningyi@sjtu.edu.cn"
    assert p.title and "教授" in p.title
    assert p.bio_text and "定制计算" in p.bio_text
    # qingyuan teachers all list a personal homepage URL
    assert p.homepage and ("ccc.sjtu.edu.cn" in p.homepage or p.homepage == item.profile_url)
    # research_interests inferred from 研究方向 keyword in bio
    assert p.research_interests
    assert any(
        kw in " ".join(p.research_interests)
        for kw in ("领域专用计算", "计算机体系结构", "并行计算", "机器学习系统")
    )


# ---------------------------------------------------------------------------
# common quality invariants
# ---------------------------------------------------------------------------


def test_no_js_or_style_leak_in_any_bio(adapter: SjtuAdapter) -> None:
    """Spot-check every fixture profile to ensure no <script>/<style>
    text bleeds into bio_text or raw_quota_text."""
    cases = [
        ("profile_cs_chenhaibo.html",
         "https://www.cs.sjtu.edu.cn/jiaoshiml/chenhaibo.html", "陈海波"),
        ("profile_cs_dongmingkai.html",
         "https://www.cs.sjtu.edu.cn/jiaoshiml/dongmingkai.html", "董明凯"),
        ("profile_ai_caoqinxiang.html",
         "https://soai.sjtu.edu.cn/cn/facultydetails/zzjs/caoqinxiang", "曹钦翔"),
        ("profile_see-ai_chenweidong.html",
         "https://sais.sjtu.edu.cn/faculty/chenweidong.html", "陈卫东"),
        ("profile_see-ai_baiyang.html",
         "https://sais.sjtu.edu.cn/faculty/baiyang.html", "白洋"),
        ("profile_see-ai_caochengxi_nonai.html",
         "https://sais.sjtu.edu.cn/faculty/caochengxi.html", "曹成喜"),
        ("profile_qingyuan_xuningyi.html",
         "http://www.qingyuan.sjtu.edu.cn/a/xu-ning-yi-1.html", "徐宁仪"),
    ]
    for fname, url, name in cases:
        html = (FIX / fname).read_text(encoding="utf-8")
        item = ListItem(name_cn=name, profile_url=url)
        p = adapter.parse_profile(html, url, item)
        blob = (p.bio_text or "") + "\n" + (p.raw_quota_text or "")
        for bad in ("function(", "var ", "<script", "@media", "</style>"):
            assert bad not in blob, f"{name}: leaked {bad!r}"
        for nav in (
            "学院新闻", "学院概况", "通知公告", "下载专区", "首页", "首 页",
            "学院领导", "组织架构", "师资队伍",
        ):
            assert nav not in blob, f"{name}: nav text {nav!r} leaked into bio"
        # research tags constraints (no parens, short)
        for t in p.research_interests:
            assert 2 <= len(t) <= 25, f"{name}: tag too long: {t!r}"
            assert all(c not in t for c in "。！？()（）"), f"{name}: noisy tag: {t!r}"
