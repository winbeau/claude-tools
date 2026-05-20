"""Huazhong University of Science and Technology (HUST) adapter.

v0.4 covers three departments. All three school sites (cs/sse/aia) are built
on the same VSB / SiteBuilder template family, but the **profile pages**
follow two distinct templates depending on whether a teacher uses the
unified ``faculty.hust.edu.cn`` personal-page system or the school's own
``info/<treeid>/<id>.htm`` legacy pages.

Departments
-----------
* ``cs``  - 计算机科学与技术学院 (cs.hust.edu.cn). Faculty list is rendered as
  a long flat page grouped by research institute (``szdw/jsml/ayjslb.htm``).
  The alphabet-style sibling ``axmpyszmlb.htm`` looks identical in markup
  but is populated client-side by JS and returns empty groups for static
  fetches - we use ``ayjslb.htm`` instead. The page contains ~150 unique
  PI links across the institute groups.
* ``sw``  - 软件学院 (sse.hust.edu.cn). Faculty are split across rank pages
  under ``/szdw1/``:  ``js_yjy.htm`` (教授/研究员), ``fjs.htm`` (副教授),
  ``js1.htm`` (讲师). The 兼职/产业/双聘 buckets are intentionally skipped
  because they are not full-time CS faculty.
* ``ai``  - 人工智能与自动化学院 (aia.hust.edu.cn). The 按系列表 entry
  (``szdw/xysz/axlb.htm``) groups all ~120 faculty by sub-department
  ("正高" / "副高" / divisions). Because 自动化 + AI 共院, we keep all
  faculty in v0.4 and rely on the v0.3 DeepSeek enricher to drop pure
  control / power-electronics PIs from the v0.4 output. *Known limitation*
  noted in the report.

Page structure (shared list-page template)
------------------------------------------
::

    <div class="munu_js">
        <h6>研究所名 / 系名 / 职称</h6>     <!-- group label -->
        <div class="js_bt">
            <ul>
                <li><a href="http://faculty.hust.edu.cn/.../zh_CN/index.htm"
                       target="_blank">姓名</a></li>
                ...
            </ul>
        </div>
    </div>

SSE/CS pages also use the ``<li id="line_uXX_N"><a title="姓名">`` variant
with an inline photo and ``<div class="info"><p>姓名</p>`` block. We accept
both.

Profile structure
-----------------

1. ``faculty.hust.edu.cn/<slug>/zh_CN/index.htm`` (the dominant template):
   the page is a TPM-style "blockwhite" layout with sections:

   - ``div.blockwhite.JS-display`` - large photo + name
   - ``div.blockwhite.Psl-info`` - 个人信息 (职称 / 性别 / 单位 / 学历 / 学位)
                                 - 个人简介 (long bio prose)  *(same class)*
   - ``div.blockwhite.Ot-ctact``  - 其他联系方式 (CIPHER-encoded email / addr)
   - ``div.blockwhite.Edu-exp``   - 教育/工作/社会兼职 经历
   - ``div.blockwhite.Rsh-focus`` - 研究方向 tags

   **Emails on this template are encrypted client-side** (a long hex blob
   like ``a0fc59827cd5...``).  We do *not* attempt to decrypt them; we
   leave ``email=None, email_obfuscated=True`` for the DeepSeek enricher
   to fill from web search.

2. ``<school>.hust.edu.cn/info/<treeid>/<id>.htm`` (legacy template):
   ``div.v_news_content`` with ``<p><strong>label：</strong>value</p>``
   lines for 职称/电话/邮箱/研究方向 plus a 个人简介 prose block. Emails
   here are *plaintext* mailto-style and easy to extract.

Known oddities
--------------
1. CS ``axmpyszmlb.htm`` is dynamically populated and returns empty groups
   under static GET. We point ``schools.yaml`` at ``ayjslb.htm`` instead.
2. faculty.hust profile URLs occasionally end with extra path suffixes
   like ``index/1036441/list/index.htm`` (when the teacher customised
   their homepage). We tolerate any URL whose prefix matches
   ``faculty.hust.edu.cn/<slug>``.
3. The 三个学院 footer-only emails (sse@hust, scs@hust, aia-president@hust)
   appear inside profile pages as a global mailto. We reject any address
   that starts with those known prefixes when probing for the teacher's
   personal email.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..core.parser_utils import (
    absolutize,
    extract_email,
    find_recruit_paragraphs,
    parse,
    text_of,
)
from ..models.pydantic_models import AdvisorPartial
from .base import ListItem, SchoolAdapter, register


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "院士", "Professor", "Lecturer", "Researcher",
)


# Reject the institute-level mailto/footer addresses that bleed into
# profile pages via shared headers/footers.
_FOOTER_EMAIL_PREFIXES: tuple[str, ...] = (
    "sse@", "ssedean@", "sseshuji@",
    "scs@", "csdean@", "csshuji@",
    "aia@", "aia-president@", "aia-dean@",
)


# Pseudo-section labels seen on HUST templates.
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "Research Focus", "Research Interests",
    "个人简介", "简介", "个人基本情况", "Bio", "Biography",
    "教育经历", "教育背景", "Education", "工作经历", "Work experience",
    "学术兼职", "社会兼职", "Social affiliations", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "教学工作", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "研究成果",
    "奖励与荣誉", "荣誉", "获奖", "曾获荣誉",
    "研究课题", "科研项目", "项目", "团队成员",
}


# Note: we deliberately exclude ``/`` from the separator set because HUST
# faculty pages frequently include "I/O", "AI/ML", "5G/6G" style atomic
# tokens; splitting on ``/`` would mangle them. CN-list separators are
# enough on this site.
_SPLIT_RE = re.compile(r"[、，,；;\n]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Same convention as other adapters: paragraphs are either a single
    tag or a separator-joined list. Always try splitting."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 　。.；;:：等以及和与")
            if _is_tag(p):
                out.append(p)
        if len(out) >= 15:
            break
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]


def _looks_like_faculty(title: str | None) -> bool:
    if not title:
        return True  # don't drop people just because list has no title
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "cs.hust.edu.cn" in host:
        return "cs"
    if "sse.hust.edu.cn" in host or "software.hust.edu.cn" in host:
        return "sw"
    if "aia.hust.edu.cn" in host:
        return "ai"
    if "faculty.hust.edu.cn" in host:
        # faculty.* is shared across all departments; the pipeline always
        # routes profile parsing through ``parse_profile(..., list_item)``,
        # where the list_item carries dept context implicitly via the
        # list URL we used. We route purely on hostname for the profile
        # parser since the body template is identical.
        return "faculty"
    return "cs"  # safe default


# ---------------------------------------------------------------------------
# List parsing
# ---------------------------------------------------------------------------


# Anchors on the list page point to one of three URL shapes:
#   - http://faculty.hust.edu.cn/<slug>/zh_CN/...   (unified faculty system)
#   - ../info/<treeid>/<id>.htm                    (school's own legacy)
#   - http://<school>.hust.edu.cn/info/...         (school's own absolute)
# We accept all three; everything else (nav, social links) is rejected.
_PROFILE_URL_PATTERNS = (
    re.compile(r"faculty\.hust\.edu\.cn/[^/]+/zh_CN"),
    re.compile(r"/info/\d+/\d+\.htm"),
)


def _is_profile_url(href: str) -> bool:
    if not href:
        return False
    if href.startswith("#") or href.startswith("javascript:"):
        return False
    return any(p.search(href) for p in _PROFILE_URL_PATTERNS)


def _parse_list_munu(html: str, list_url: str) -> list[ListItem]:
    """Parser for the ``div.munu_js > h6 + div.js_bt > ul > li > a`` layout
    used by cs.hust ``ayjslb.htm`` and aia.hust ``axlb.htm``.

    The ``<h6>`` text is the institute / sub-department name; we carry it
    forward as a soft ``title`` hint so downstream consumers can show e.g.
    "存储所 (cs)".
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    groups = tree.css("div.munu_js")
    if not groups:
        return items
    for group in groups:
        h6 = group.css_first("h6")
        group_label = text_of(h6) or None
        for li in group.css("li"):
            a = li.css_first("a")
            if a is None:
                continue
            href = (a.attributes.get("href") or "").strip()
            if not _is_profile_url(href):
                continue
            name = text_of(a)
            if not name or len(name) > 20:
                continue
            # Reject English-only / non-CJK names just in case the list
            # injects an English alias row.
            if not any("一" <= c <= "鿿" for c in name):
                continue
            absurl = absolutize(list_url, href)
            if absurl in seen:
                continue
            seen.add(absurl)
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=absurl,
                    # Use the group header as a *soft* title hint. It's the
                    # institute name (存储所 / 数据库所 / 正高), not a real
                    # 职称 — the profile parser may overwrite this.
                    title=group_label,
                )
            )
    return items


def _parse_list_li_pic(html: str, list_url: str) -> list[ListItem]:
    """Parser for the SSE rank pages (``szdw1/js_yjy.htm`` etc).

    Each teacher card looks like::

        <li id="line_uXX_N">
            <a href="..." title="姓名">
                <div class="pic"><img src="..."></div>
                <div class="info"><p>姓名</p></div>
            </a>
        </li>

    The ``title`` attribute is the most reliable name source; the inner
    ``<p>`` mirrors it but is sometimes empty.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a[title]"):
        href = (a.attributes.get("href") or "").strip()
        if not _is_profile_url(href):
            continue
        name = (a.attributes.get("title") or "").strip()
        if not name:
            name = text_of(a.css_first("div.info p")) or text_of(a)
        if not name or len(name) > 20:
            continue
        if not any("一" <= c <= "鿿" for c in name):
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        photo: str | None = None
        img = a.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src:
                photo = absolutize(list_url, src)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
                photo_url=photo,
            )
        )
    return items


def _parse_list_generic(html: str, list_url: str) -> list[ListItem]:
    """Best-effort fallback: grab every anchor whose href looks like a
    profile URL and treat its text as the name.

    We always run ``_parse_list_munu`` first (the dominant CS/AI layout);
    this fallback catches sse-style flat lists that lack ``div.munu_js``.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if not _is_profile_url(href):
            continue
        title_attr = (a.attributes.get("title") or "").strip()
        name = title_attr or text_of(a)
        if not name or len(name) > 20:
            continue
        if not any("一" <= c <= "鿿" for c in name):
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        items.append(ListItem(name_cn=name, profile_url=absurl))
    return items


def _parse_list_dispatch(html: str, list_url: str) -> list[ListItem]:
    """Try the layouts in priority order.

    1. ``div.munu_js`` (cs.hust / aia.hust) — preserves group labels.
    2. ``a[title]`` cards under ``<li>`` (sse.hust rank pages).
    3. Generic anchor scan over the page.
    """
    items = _parse_list_munu(html, list_url)
    if items:
        return items
    items = _parse_list_li_pic(html, list_url)
    if items:
        return items
    return _parse_list_generic(html, list_url)


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------


def _is_footer_email(email: str | None) -> bool:
    if not email:
        return False
    return any(email.startswith(p) for p in _FOOTER_EMAIL_PREFIXES)


def _profile_faculty_hust(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Parse the ``faculty.hust.edu.cn`` "blockwhite" template.

    The page contains several ``<div class="blockwhite XXX">`` blocks. Two
    blocks share the same ``Psl-info`` class — one for 个人信息 metadata,
    one for 个人简介 prose — distinguished only by their inner header
    text. Emails are encrypted hex blobs; we leave ``email=None`` and
    flag ``email_obfuscated=True`` so the enricher knows to look it up.
    """
    tree = parse(html)
    blocks = tree.css("div.blockwhite")
    if not blocks:
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=list_item.email,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=profile_url,
            source_url=profile_url,
        )

    # Each blockwhite contains:
    #   - <div class="title"><i/><div class="info"><h2>中文标签</h2><p>EN</p></div></div>
    #   - <div class="cont">...actual body...</div>
    # The <h2> is the clean Chinese-only label; the body lives entirely
    # inside ``div.cont`` so we never need string-prefix stripping.
    sections: dict[str, str] = {}
    photo_url: str | None = list_item.photo_url
    title: str | None = list_item.title
    name: str = list_item.name_cn

    def _clean_block_body(node) -> str:
        if node is None:
            return ""
        txt = node.text(separator="\n", strip=True)
        # Drop common JS/script-leak lines that selectolax surfaces as
        # plain text (inline ``<script>`` contents are emitted verbatim).
        cleaned: list[str] = []
        for ln in txt.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if (
                "ImageScale" in ln
                or "TsitesPraiseUtil" in ln
                or "_tsites_com_view_mode_type_" in ln
                or "jQuery(document)" in ln
                or ln.startswith("var ")
                or ln.startswith("function(")
                or "= new TsitesPraise" in ln
            ):
                continue
            cleaned.append(ln)
        return "\n".join(cleaned).strip()

    for block in blocks:
        # Preferred: <h2> inside the title block ("个人信息", "研究方向", ...).
        h2 = block.css_first("div.title h2") or block.css_first("h2")
        header = text_of(h2) if h2 is not None else ""
        header = header.rstrip("：:").strip()

        # Body: ``div.cont`` if present, otherwise the whole block minus
        # the title node (we drop the title node from the parsed tree to
        # avoid re-extracting the bilingual header).
        cont = block.css_first("div.cont")
        if cont is not None:
            body_text = _clean_block_body(cont)
        else:
            # Strip the .title subtree so the bilingual header doesn't
            # bleed into the body. selectolax doesn't have a .remove() in
            # all versions; just recompute from cont-less blocks (rare).
            body_text = _clean_block_body(block)
            if header and body_text.startswith(header):
                body_text = body_text[len(header):].strip()

        if header:
            if header in sections:
                sections[header] = sections[header] + "\n" + body_text
            else:
                sections[header] = body_text

        # Photo lives in the JS-display block. The static <img> has no
        # src - it's added via an ``ImageScale.addimg("/path...", ...)``
        # call in an inline ``<script>``. Recover the URL from there.
        if photo_url is None:
            img = block.css_first("img")
            if img is not None:
                src = (
                    img.attributes.get("src")
                    or img.attributes.get("orisrc")
                    or ""
                )
                if src:
                    photo_url = absolutize(profile_url, src)
            if photo_url is None:
                m = re.search(
                    r"\.addimg\(\s*['\"]([^'\"]+)['\"]",
                    block.html or "",
                )
                if m:
                    photo_url = absolutize(profile_url, m.group(1))

    # Title: from 个人信息 - first non-empty line is usually 职称
    # (e.g. "教授\n博士生导师\n硕士生导师").
    info_block = sections.get("个人信息", "")
    if info_block and not title:
        for ln in info_block.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if any(kw in ln for kw in _FACULTY_TITLE_KEYWORDS):
                title = ln
                break

    # Bio: 个人简介 prose.
    bio: str | None = None
    for key in ("个人简介", "简介", "个人基本情况"):
        if key in sections and sections[key].strip():
            bio_text = sections[key].strip()
            # Drop trailing "More+" / "更多" markers that appear after the
            # truncated preview.
            bio_text = re.sub(r"\s*(More\+?|更多)\s*$", "", bio_text).strip()
            if bio_text:
                bio = bio_text[:1500]
                break

    # Research focus tags.
    research_tags: list[str] = []
    for key in ("研究方向", "研究兴趣", "研究领域", "主要研究方向", "Research Focus"):
        if key in sections and sections[key].strip():
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    # Email: the Ot-ctact block carries an encrypted hex blob. We leave
    # ``email`` None and flag obfuscated so the enricher knows.
    email = list_item.email
    email_obf = False
    if not email:
        # Some teachers paste a plaintext mailto inside the bio prose.
        # Try the whole-page scope; if a hit is the institute footer,
        # discard it.
        scope_text = " ".join(sections.values())
        em, was_obf = extract_email(scope_text)
        if em and not _is_footer_email(em):
            email = em
            email_obf = was_obf
        else:
            email = None
            # Mark obfuscated because we KNOW the official email exists
            # but is hidden behind the encryption layer.
            email_obf = True

    # Recruit signals.
    scope_text_for_recruit = " ".join(sections.values())
    recruit_chunks = find_recruit_paragraphs(scope_text_for_recruit)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title or list_item.title,
        email=email,
        email_obfuscated=email_obf,
        phone=list_item.phone,
        photo_url=photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _profile_vsb_info(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Parse the legacy ``<school>.hust.edu.cn/info/.../X.htm`` template.

    Layout::

        <div class="v_news_content">
            <div class="pic fl"><a><img ...></a></div>
            <div class="txt fr">
                <h1>姓名</h1>
                <p><strong>职称：</strong>教授</p>
                <p><strong>电话：</strong>...</p>
                <p><strong>邮箱：</strong>x@y.cn</p>
                <p><strong>研究方向：</strong>方向1，方向2，...</p>
            </div>
            <h2>个人简介</h2>
            ... bio prose ...
        </div>

    Some teachers use the same container but with the bio dumped as
    free-flowing prose with no structured fields (cf. 白翔's profile).
    We extract structured fields first, then fall back to bio prose.
    """
    tree = parse(html)
    content = tree.css_first("div.v_news_content") or tree.body
    if content is None:
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            source_url=profile_url,
            homepage=profile_url,
        )

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    research_tags: list[str] = []
    bio_lines: list[str] = []

    # Head: the txt.fr block has structured <p><strong>label：</strong>val</p>.
    txt = content.css_first("div.txt") or content.css_first("div.fr")
    if txt is not None:
        h1 = txt.css_first("h1")
        if h1 is not None:
            n = text_of(h1)
            if n:
                name = n
        for p in txt.css("p"):
            t = text_of(p)
            if not t:
                continue
            if t.startswith("职称") or t.startswith("职 称"):
                v = t.split("：", 1)[-1].strip() if "：" in t else t
                if v and not title:
                    title = v
            elif t.startswith("电话") or t.startswith("办公电话"):
                v = t.split("：", 1)[-1].strip() if "：" in t else ""
                if v and not phone:
                    phone = v
            elif t.startswith("邮箱") or t.startswith("电子邮件") or t.startswith("E-mail"):
                em, _ = extract_email(t)
                if em and not _is_footer_email(em) and not email:
                    email = em
            elif (
                t.startswith("研究方向")
                or t.startswith("研究领域")
                or t.startswith("研究兴趣")
            ):
                # Inline tag list.
                v = t.split("：", 1)[-1].strip() if "：" in t else t
                tags = _split_interests(v)
                if tags and not research_tags:
                    research_tags = tags

    # Photo
    if photo_url is None:
        img = content.css_first("img")
        if img is not None:
            src = (
                img.attributes.get("src")
                or img.attributes.get("orisrc")
                or ""
            )
            if src and "logo" not in src.lower():
                photo_url = absolutize(profile_url, src)

    # Bio: take the body text from blocks OTHER than the head card.
    # selectolax doesn't have node removal in all versions, so we collect
    # text from every direct child of ``v_news_content`` except the
    # ``div.txt`` / ``div.pic`` head columns. As a safety net we still
    # filter out short structured prefix lines.
    structured_prefixes = (
        "职称", "职 称", "电话", "办公电话", "邮箱", "电子邮件", "E-mail",
        "研究方向", "研究领域", "研究兴趣",
    )
    head_text = txt.text(separator="\n", strip=True) if txt is not None else ""
    head_lines = {ln.strip() for ln in head_text.split("\n") if ln.strip()}
    full_text = content.text(separator="\n", strip=True)
    for ln in full_text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if ln in head_lines:
            continue
        if any(ln.startswith(pref) for pref in structured_prefixes):
            continue
        if ln == name:
            continue
        if "个人主页" in ln and len(ln) < 30:
            continue
        bio_lines.append(ln)
    bio_text = "\n".join(bio_lines).strip()
    bio: str | None = bio_text[:1500] if bio_text else None

    # Search bio prose for inline research direction if we have none yet.
    if not research_tags and bio:
        m = re.search(
            r"研究(?:方向|兴趣|领域)[为是:：]?\s*([^。\n]{2,200})",
            bio,
        )
        if m:
            research_tags = _split_interests(m.group(1))

    # Fallback email scan, with footer-rejection.
    if not email:
        em, _ = extract_email(full_text)
        if em and not _is_footer_email(em):
            email = em

    # Recruit signals (scope = main content only, not page-wide nav).
    scope_text = content.text(separator=" ", strip=True)
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        email_obfuscated=False,
        phone=phone,
        photo_url=photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class HustAdapter(SchoolAdapter):
    school_code = "hust"
    supports = {"cs", "sw", "ai"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        return _parse_list_dispatch(html, list_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        host = (urlparse(profile_url).hostname or "").lower()
        if "faculty.hust.edu.cn" in host:
            return _profile_faculty_hust(html, profile_url, list_item)
        # Everything else (cs/sse/aia /info/.../X.htm) uses the v_news_content
        # legacy template.
        return _profile_vsb_info(html, profile_url, list_item)
