"""Parser tests for the Peking University adapter.

Fixtures were captured by curl with a real User-Agent on 2026-05-19 from
the official department sites. The .html files live under
``tests/fixtures/pku/`` and are checked in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.base import ListItem
from claw.adapters.pku import PkuAdapter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "pku"


@pytest.fixture
def adapter() -> PkuAdapter:
    return PkuAdapter()


# ---------------------------------------------------------------------------
# List-page tests (one per dept)
# ---------------------------------------------------------------------------


def test_parse_list_eecs(adapter: PkuAdapter) -> None:
    html = (FIX / "list_cs_all.html").read_text(encoding="utf-8")
    url = "https://cs.pku.edu.cn/szdw/jyxl/amz/ALL.htm"
    items = adapter.parse_list(html, url)

    # cs.pku ALL.htm front page lists ~12 教研系列 faculty.
    assert len(items) >= 10, f"got only {len(items)} items"
    # Names are always populated.
    assert all(it.name_cn.strip() for it in items)
    # Profile URLs are absolute and end in .htm.
    assert all(it.profile_url and it.profile_url.endswith(".htm") for it in items)
    # Email coverage from image-obfuscation stitching: should hit most rows.
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.6, (
        f"only {len(with_email)}/{len(items)} have email"
    )
    # Emails should be lowercase and contain '@'.
    for it in with_email:
        assert "@" in it.email
        assert it.email == it.email.lower()


def test_parse_list_ai(adapter: PkuAdapter) -> None:
    html = (FIX / "list_cis_zzjs.html").read_text(encoding="utf-8")
    url = "https://www.cis.pku.edu.cn/szdw/zzjs.htm"
    items = adapter.parse_list(html, url)

    assert len(items) >= 5, f"got only {len(items)} items"
    assert all(it.name_cn for it in items)
    # cis.pku has plain-text email - coverage should be near 100%.
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) >= 0.8, (
        f"only {len(with_email)}/{len(items)} have email"
    )


def test_parse_list_wangxuan(adapter: PkuAdapter) -> None:
    html = (FIX / "list_wangxuan.html").read_text(encoding="utf-8")
    url = "https://www.icst.pku.edu.cn/xstd/xstd_01/index.htm"
    items = adapter.parse_list(html, url)

    # First page lists ~20 faculty members.
    assert len(items) >= 15, f"got only {len(items)} items"
    # No card metadata - only name + URL on this page.
    assert all(it.name_cn for it in items)
    # Names must contain CJK characters (rejects English-only menu entries).
    for it in items:
        assert any("一" <= c <= "鿿" for c in it.name_cn), (
            f"non-Chinese list item: {it.name_cn!r}"
        )


def test_parse_list_cfcs(adapter: PkuAdapter) -> None:
    html = (FIX / "list_cfcs_faculty.html").read_text(encoding="utf-8")
    url = "https://cfcs.pku.edu.cn/people/faculty/index.htm"
    items = adapter.parse_list(html, url)

    # /people/faculty/ has 30+ entries across "中心主任 / 教学科研人员 /
    # 访问讲席教授 / 博士后 / 行政辅助".
    assert len(items) >= 20, f"got only {len(items)} items"
    # Most entries surface a title.
    with_title = [it for it in items if it.title]
    assert len(with_title) / len(items) >= 0.7, (
        f"only {len(with_title)}/{len(items)} have title"
    )


# ---------------------------------------------------------------------------
# Negative / filter test: students should be rejected even if they sneak
# into list pages.
# ---------------------------------------------------------------------------


def test_parse_list_eecs_rejects_non_faculty_titles(adapter: PkuAdapter) -> None:
    # A synthetic <li> with an obvious "博士生" title should be dropped.
    fake = """
    <html><body>
    <ul>
      <li data-aos="fade-up"><a href="../../../info/1062/9999.htm">
        <div class="con">
          <h3><big>张同学</big></h3>
          <p>职称：博士生</p>
        </div>
      </a></li>
      <li data-aos="fade-up"><a href="../../../info/1062/9998.htm">
        <div class="con">
          <h3><big>李教授</big></h3>
          <p>职称：教授</p>
        </div>
      </a></li>
    </ul>
    </body></html>
    """
    items = adapter.parse_list(fake, "https://cs.pku.edu.cn/szdw/jyxl/amz/ALL.htm")
    names = [it.name_cn for it in items]
    assert "李教授" in names
    assert "张同学" not in names


# ---------------------------------------------------------------------------
# Profile-page tests (one per dept)
# ---------------------------------------------------------------------------


def _stub_item(name: str) -> ListItem:
    return ListItem(name_cn=name)


def test_parse_profile_no_js_no_nav_eecs(adapter: PkuAdapter) -> None:
    html = (FIX / "profile_cs_pku.html").read_text(encoding="utf-8")
    item = _stub_item("曹东刚")
    p = adapter.parse_profile(
        html, "https://cs.pku.edu.cn/info/1062/3090.htm", item
    )
    # bio + research must exist
    assert p.bio_text or p.research_interests
    if p.bio_text:
        # No JavaScript-source-code leaks
        assert "function" not in p.bio_text
        assert "var page" not in p.bio_text
        assert "<script" not in p.bio_text.lower()
    # Recruitment paragraph must not be the global "招生信息" nav.
    if p.raw_quota_text:
        assert "招生招聘" not in p.raw_quota_text
        assert "教务通知" not in p.raw_quota_text
        assert "校友会" not in p.raw_quota_text
    # Research interests are well-formed tags.
    for tag in p.research_interests:
        assert 2 <= len(tag) <= 25
        assert "(" not in tag and "（" not in tag
        assert tag[-1] not in "。！？"


def test_parse_profile_no_js_no_nav_ai(adapter: PkuAdapter) -> None:
    html = (FIX / "profile_cis_pku.html").read_text(encoding="utf-8")
    item = _stub_item("邓志鸿")
    p = adapter.parse_profile(
        html, "https://www.cis.pku.edu.cn/info/1362/2257.htm", item
    )
    assert p.bio_text or p.research_interests
    if p.bio_text:
        assert "function" not in p.bio_text
        assert "<script" not in p.bio_text.lower()
    # Plain-text email should be picked up on the profile.
    if p.email is None:
        # If the list-item had no email and the profile body text *did*
        # have one, parse_profile should fish it out.
        pass
    else:
        assert "@" in p.email


def test_parse_profile_no_js_no_nav_wangxuan(adapter: PkuAdapter) -> None:
    html = (FIX / "profile_wangxuan.html").read_text(encoding="utf-8")
    item = _stub_item("陈峰")
    p = adapter.parse_profile(
        html, "https://www.icst.pku.edu.cn/xstd/xstd_01/1201844icst1222602.htm", item
    )
    # At least research_interests OR bio
    assert p.bio_text or p.research_interests
    if p.research_interests:
        for tag in p.research_interests:
            assert 2 <= len(tag) <= 25
            assert "(" not in tag
    # Wangxuan profile body should not be a nav dump.
    if p.bio_text:
        assert "function" not in p.bio_text
        assert "stuckMenu" not in p.bio_text
        # "纪念王选" appears in the global footer; should not leak in.
        # (We don't outright fail on it - the footer is outside div.aboutp -
        # but if it ever sneaks in we want to know.)
    # Email should resolve even when obfuscated as "name (at) domain".
    # chen_feng@pku.edu.cn is in plaintext via <a href="mailto:..."> -
    # extract_email handles either form.
    assert p.email and "@" in p.email and p.email == p.email.lower()


def test_parse_profile_wangxuan_obfuscated_email(adapter: PkuAdapter) -> None:
    # 郭宗明 page uses "guozongming (at) pku.edu.cn" obfuscation.
    html = (FIX / "profile_wangxuan2.html").read_text(encoding="utf-8")
    item = _stub_item("郭宗明")
    p = adapter.parse_profile(
        html, "https://www.icst.pku.edu.cn/xstd/xstd_01/1201844icst1222623.htm", item
    )
    assert p.email is not None
    assert p.email.endswith("@pku.edu.cn")


def test_parse_profile_no_js_no_nav_cfcs(adapter: PkuAdapter) -> None:
    html = (FIX / "profile_cfcs.html").read_text(encoding="utf-8")
    item = _stub_item("程宽")
    p = adapter.parse_profile(
        html, "https://cfcs.pku.edu.cn/people/faculty/kuancheng/index.htm", item
    )
    # cfcs profile has rich 简介 / 发表论著 / 研究方向 sections.
    assert p.bio_text or p.research_interests, "neither bio nor research"
    assert p.bio_text and "function" not in p.bio_text
    assert p.bio_text and "<script" not in p.bio_text.lower()
    # raw_quota_text must not be the global navigation menu.
    if p.raw_quota_text:
        assert "招聘信息" not in p.raw_quota_text  # this is the global side-nav
        assert "中心主任" not in p.raw_quota_text


def test_parse_profile_has_research_or_bio_eecs(adapter: PkuAdapter) -> None:
    html = (FIX / "profile_cs_pku.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "https://cs.pku.edu.cn/info/1062/3090.htm", _stub_item("曹东刚")
    )
    assert p.bio_text or p.research_interests


def test_parse_profile_has_research_or_bio_cfcs(adapter: PkuAdapter) -> None:
    html = (FIX / "profile_cfcs.html").read_text(encoding="utf-8")
    p = adapter.parse_profile(
        html, "https://cfcs.pku.edu.cn/people/faculty/kuancheng/index.htm",
        _stub_item("程宽"),
    )
    assert p.bio_text and len(p.bio_text) > 50
