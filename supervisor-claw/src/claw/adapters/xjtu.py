"""Xi'an Jiaotong University (XJTU) adapter.

v0.4 covers three departments inside the 电子与信息学部 (School of Electronics
and Information Engineering) umbrella:

* ``cs`` — 计算机科学与技术学院 (cs.xjtu.edu.cn).  师资名录 is split across
  three rank pages under ``/szdw/jsml/``: ``js.htm`` (教授),
  ``fjs1.htm`` (副教授), ``jsjqt.htm`` (讲师/其他).  Cards follow the
  VSB SiteBuilder template — anchors point at ``/info/<treeid>/<id>.htm``
  inside the college site, with name + 职称 inside a ``div.img_out`` /
  ``div.con`` pair (identical to scse.buaa.edu.cn).
* ``sw`` — 软件学院 (se.xjtu.edu.cn).  ``/jsdw.htm`` is the aggregate
  教师队伍 page.  Some senior faculty link out to the unified
  ``faculty.xjtu.edu.cn`` portal instead of an internal ``/info/`` page.
* ``ai`` — 人工智能学院 / 人工智能与机器人研究所 (aiar.xjtu.edu.cn).
  ``/szdw/js.htm`` lists 专职教师.  Same VSB layout; profile links go
  back to the unified faculty.xjtu portal for most PIs.

Profile-page templates we recognise
------------------------------------
1. **Unified XJTU faculty portal** (``faculty.xjtu.edu.cn/<slug>/zh_CN/...``)
   — CSCEC ZJ-derived template common across many Chinese universities
   (HUST / HZAU / ECNU / XJTU all share the same skeleton).  The teacher
   name lives in ``div.userName`` / ``h1.title``; the headshot in
   ``div.showpic img``; the contact / 职称 block in ``div.userIntro``
   or ``div.showCenterText`` with ``<li>`` rows ``label：value``.
   Section bodies are inside the right-rail ``div.list_main_content``
   block, one section per ``<div class="newslist">`` keyed by the menu
   item name (个人简介 / 研究方向 / 教育经历 / 招生信息).
2. **Legacy 学者主页 portal** (``gr.xjtu.edu.cn/web/<slug>``) — older
   front-end on the same dataset; we accept either path.
3. **College-site VSB profile** (``cs.xjtu.edu.cn/info/...``, etc.) —
   VSB CMS with ``div.v_news_content`` body and ``<h2>`` / ``<strong>``
   pseudo-section headers.  Identical shape to buaa scse and zju cst.

Known XJTU oddities (v0.4)
--------------------------
* The 电子与信息学部 (学部制) shares many sub-units under the same VSB
  CMS skin — the same teacher may appear on the umbrella site as well
  as the home-college site.  We rely on the pipeline's
  ``(school, name_cn, email)`` upsert dedupe rather than guessing.
* Some senior PIs (郑南宁、龚怡宏、辛景民 etc.) keep their canonical bio
  on ``gr.xjtu.edu.cn`` and link out from the college site; both are
  treated as profile URLs.
* College list pages sit behind a JS-challenge wall on commodity
  network egress — the pipeline / Playwright fetcher must pass that
  before our parser sees real HTML.  The parser itself does NOT try to
  solve the challenge; it just refuses to mis-parse the challenge page
  (which has no ``info/`` anchors anyway).
* Research-graduate-only navigation entries (e.g. 研究生导师) sometimes
  reuse the same VSB template but with student-list rows.  We filter on
  ``title`` keywords + ``/info/`` link gate to avoid scooping students.
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


_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "院士", "Professor", "Lecturer", "Researcher",
)

_NON_FACULTY_KEYWORDS: tuple[str, ...] = (
    "博士生", "硕士生", "研究生", "本科生", "学生", "校友", "实习",
)

_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "主要研究领域",
    "Research Interests",
    "研究概况", "科研概况", "个人简介", "简介", "基本信息", "Bio", "Biography",
    "教育背景", "教育经历", "工作经历", "工作履历", "学习经历",
    "学术兼职", "社会兼职", "任职", "学术任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "教学研究", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文", "科研成果",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目", "科学研究",
    "联系方式",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Standard tag splitter (same logic as the tsinghua / buaa adapters)."""
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
        return True
    if any(kw in title for kw in _NON_FACULTY_KEYWORDS):
        return False
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Dept routing
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    """Route a URL to one of cs / sw / ai.

    ``faculty.xjtu.edu.cn`` and ``gr.xjtu.edu.cn`` are the *unified* portals —
    they don't carry a dept tag in the URL.  Callers should pass the list_url
    of the originating dept when re-routing into ``parse_profile``; if a bare
    portal URL arrives we default to ``cs`` (largest dept) so the profile is
    still extractable.
    """
    host = (urlparse(url).hostname or "").lower()
    if "cs.xjtu.edu.cn" in host:
        return "cs"
    if "se.xjtu.edu.cn" in host:
        return "sw"
    if "aiar.xjtu.edu.cn" in host or "ai.xjtu.edu.cn" in host or "aii.xjtu.edu.cn" in host:
        return "ai"
    # Unified portals — best-effort default.
    if "faculty.xjtu.edu.cn" in host or "gr.xjtu.edu.cn" in host:
        return "unified"
    return "cs"


def _is_unified_profile(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "faculty.xjtu.edu.cn" in host or "gr.xjtu.edu.cn" in host


# ---------------------------------------------------------------------------
# List parsers — XJTU college sites all use the same VSB SiteBuilder template
# ---------------------------------------------------------------------------


def _is_valid_profile_href(href: str) -> bool:
    """Accept anchor hrefs that look like a teacher profile.

    The VSB faculty list pages use ``/info/<treeid>/<id>.htm``; the unified
    portal links out as ``faculty.xjtu.edu.cn/<slug>/zh_CN/index.htm`` or
    ``gr.xjtu.edu.cn/web/<slug>``.  We accept any of those plus a defensive
    catch-all for sites that bury the link inside ``teacher.jsp?...id=N``.
    """
    if not href:
        return False
    h = href.lower()
    if "/info/" in h and h.endswith(".htm"):
        return True
    if "faculty.xjtu.edu.cn" in h and "/zh_cn/" in h:
        return True
    if "gr.xjtu.edu.cn/web/" in h or "gr.xjtu.edu.cn/en/web/" in h:
        return True
    if "teacher_content.jsp" in h or "units_teacher.jsp" in h:
        return True
    return False


def _parse_list_vsb(html: str, list_url: str) -> list[ListItem]:
    """Generic XJTU VSB SiteBuilder faculty-list parser.

    Layout (shared across cs / sw / ai college sites):

        <li>
          <a href="../../info/<tree>/<id>.htm" title="姓名">
            <div class="img_out">
              <div class="img">
                <div class="jstbox"><img src="..."></div>
                <p>姓名 <span>职称</span></p>
              </div>
            </div>
            <div class="con">
              <p class="dh"></p>             <!-- 电话, often empty -->
              <p class="yx">email@xjtu.edu.cn</p>
            </div>
          </a>
        </li>

    Some XJTU pages drop the ``div.con`` block and only carry name + 职称;
    others (typically the senior-PI 公共首页) link directly to
    ``faculty.xjtu.edu.cn`` and skip the photo block.  We treat all of
    these uniformly — the only hard requirement is "anchor + valid
    profile href + at least a name".
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not _is_valid_profile_href(href):
            continue

        # Name: prefer the title attribute (a stable XJTU convention); fall
        # back to the visible text content.
        name = (a.attributes.get("title") or "").strip()
        if not name:
            name_p = a.css_first("div.img p") or a.css_first("p")
            if name_p is not None:
                # Take everything up to the first <span> (which holds 职称).
                parts: list[str] = []
                for ch in name_p.iter():
                    if ch.tag == "span":
                        break
                    t = text_of(ch)
                    if t:
                        parts.append(t)
                name = "".join(parts).strip() or text_of(name_p)
        if not name:
            name = text_of(a)
        # Drop honorific/role prefixes that sometimes leak in (e.g. "院士 张三")
        name = name.replace("　", " ").strip()
        # Reject overly-long or empty values — these are usually nav rows.
        if not name or len(name) > 20:
            continue
        # Reject anchors that are clearly navigation. The first set is the
        # original live-site nav. The second set covers stub/teaser text that
        # appears on Wayback Machine snapshots (where ``claw crawl-stealth``
        # falls back) and CMS "read-more" buttons.
        if name in {
            # live-site nav
            "师资队伍", "全体教师", "教授", "副教授", "讲师", "教师名录",
            "博士生导师", "硕士生导师", "讲师及其他", "教师", "首页",
            # snapshot / "read-more" widgets
            "了解详细", "了解更多", "查看更多", "查看详情", "详情",
            "更多", "More", "Read more", "Show more",
            # 信息标签 / 邮箱占位
            "电话", "邮箱", "办公地址", "研究方向", "个人主页",
        }:
            continue
        # Require at least one CJK character — drops English-only nav strings
        # and any leaked URL/path fragments that slipped past length check.
        if not any("一" <= ch <= "鿿" for ch in name):
            continue

        # 职称 lives in the first <span> inside the name <p>.
        title: str | None = None
        title_span = a.css_first("div.img p span") or a.css_first("p span")
        if title_span is not None:
            t = text_of(title_span)
            if t and t != name and len(t) <= 12:
                title = t

        # Email lives in <p class="yx"> inside div.con.  We accept either the
        # exact yx class or any <p> whose text contains "@".
        email: str | None = None
        yx = a.css_first("p.yx") or a.css_first("div.con p")
        if yx is not None:
            ytext = text_of(yx)
            if ytext and "@" in ytext:
                em, _ = extract_email(ytext)
                if em:
                    email = em

        # Phone, when present, sits in <p class="dh">.
        phone: str | None = None
        dh = a.css_first("p.dh")
        if dh is not None:
            dtext = text_of(dh)
            if dtext and re.search(r"\d{6,}", dtext):
                phone = dtext

        # Photo
        img = a.css_first("div.jstbox img") or a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = img.attributes.get("src") or ""
            if src and not src.startswith("data:"):
                photo = absolutize(list_url, src)

        if title and not _looks_like_faculty(title):
            continue

        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
                title=title,
                email=email,
                phone=phone,
                photo_url=photo,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile parsers
# ---------------------------------------------------------------------------


def _empty_partial(list_item: ListItem, profile_url: str) -> AdvisorPartial:
    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=list_item.email,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        source_url=profile_url,
    )


# Inline-prose research-direction extractor (shared by both profile parsers).
_INLINE_RESEARCH_RE = re.compile(
    r"研究(?:方向|兴趣|领域|概况)[为是:：]?\s*([^。\n]{2,200})"
)
_INLINE_INCLUDE_RE = re.compile(
    r"(?:主要)?研究(?:方向|领域|兴趣)(?:包括|为|是|涉及|集中在|侧重于)[：:]?\s*([^。\n]{2,200})"
)


def _extract_inline_research(text: str) -> list[str]:
    if not text:
        return []
    for rx in (_INLINE_INCLUDE_RE, _INLINE_RESEARCH_RE):
        m = rx.search(text)
        if m:
            tags = _split_interests(m.group(1))
            if tags:
                return tags
    return []


def _split_pseudo_p_sections(content_node) -> dict[str, str]:
    """Group section bodies by their preceding pseudo-header.

    Recognises three flavours of pseudo-header all seen on XJTU sites:

    1. ``<p><strong>个人简介</strong></p>`` — bold-wrapped header.
    2. ``<p>个人简介：</p>`` — bare paragraph with trailing colon.
    3. ``<h2>个人简介</h2>`` / ``<h3>...</h3>`` — true heading tag (rare on
       legacy VSB pages but used by the unified faculty.xjtu portal).

    Bodies are the following <p>/<div> siblings until the next header.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    if content_node is None:
        return {}
    for n in content_node.traverse(include_text=False):
        if n.tag in {"h1", "h2", "h3", "h4", "h5"}:
            label = text_of(n).rstrip("：:").strip()
            if label and label in _PSEUDO_HEADERS:
                current = label
                sections.setdefault(current, [])
                continue
        if n.tag != "p":
            continue
        txt = text_of(n)
        if not txt:
            continue
        norm = txt.rstrip("：:").strip()
        if norm and len(norm) <= 14 and norm in _PSEUDO_HEADERS:
            current = norm
            sections.setdefault(current, [])
            continue
        # Pseudo-header where the body lives on the same <p> ("个人简介：xxx").
        if "：" in txt:
            head, rest = txt.split("：", 1)
            head = head.strip()
            rest = rest.strip()
            if head and len(head) <= 14 and head in _PSEUDO_HEADERS:
                current = head
                sections.setdefault(current, [])
                if rest:
                    sections[current].append(rest)
                continue
        if current is None:
            continue
        sections[current].append(txt)
    return {k: "\n".join(v) for k, v in sections.items() if v or k in sections}


# Unified faculty.xjtu portal: the head-card "label：value" rows live in
# either <li> (CSCEC) or <p> (legacy) form.  We accept both.
_UNIFIED_HEAD_LABEL_KEYS = {
    "姓名": "name",
    "教师姓名": "name",
    "职称": "title",
    "职务": "post",
    "电子邮箱": "email",
    "邮箱": "email",
    "Email": "email",
    "电话": "phone",
    "办公电话": "phone",
    "联系电话": "phone",
    "个人主页": "homepage",
    "主页": "homepage",
}


def _parse_profile_unified(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """faculty.xjtu.edu.cn / gr.xjtu.edu.cn unified profile (CSCEC template).

    The head card carries the name in ``div.userName`` (or ``h1.title``) and
    the side-rail contact rows inside ``div.userIntro`` /
    ``div.showCenterText``.  Bodies live inside ``div.list_main_content``
    (right rail) or ``div.newslist`` blocks each keyed by a sidebar label.
    """
    tree = parse(html)

    # Head card — try the canonical CSCEC selectors first, then fall back.
    head = (
        tree.css_first("div.userIntro")
        or tree.css_first("div.userinfo")
        or tree.css_first("div.user_info")
        or tree.css_first("div.showCenterText")
        or tree.css_first("div.teacher_info")
        or tree.body
    )
    body_node = (
        tree.css_first("div.list_main_content")
        or tree.css_first("div.newslist")
        or tree.css_first("div.list_zw")
        or tree.css_first("div.right_main")
        or tree.body
    )

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None

    # Name — try several CSCEC variants
    if not name:
        for sel in ("div.userName", "h1.title", "div.userNameCn", "h1.userName"):
            n = tree.css_first(sel)
            if n is not None:
                t = text_of(n)
                if t:
                    name = t
                    break

    # Photo
    img = (
        tree.css_first("div.showpic img")
        or tree.css_first("div.userPic img")
        or tree.css_first("div.pic img")
    )
    if img is not None and photo_url is None:
        src = img.attributes.get("src") or ""
        if src and not src.startswith("data:"):
            photo_url = absolutize(profile_url, src)

    # Walk every "label：value" pair, regardless of whether it's a <li> or <p>.
    if head is not None:
        for n in head.css("li, p, dd, span"):
            t = text_of(n)
            if not t or "：" not in t:
                continue
            label_raw, value = t.split("：", 1)
            label = label_raw.strip().rstrip(":：")
            value = value.strip()
            if not label or not value:
                continue
            kind = _UNIFIED_HEAD_LABEL_KEYS.get(label)
            if kind is None:
                continue
            if kind == "name" and not name:
                name = value
            elif kind == "title" and not title:
                title = value if any(
                    kw in value for kw in _FACULTY_TITLE_KEYWORDS
                ) or len(value) <= 12 else None
            elif kind == "email" and not email:
                em, _ = extract_email(value or t)
                if em:
                    email = em
            elif kind == "phone" and not phone:
                if re.search(r"\d{6,}", value):
                    phone = value
            elif kind == "homepage" and value.startswith("http"):
                homepage = value

    # Body sections
    sections = _split_pseudo_p_sections(body_node) if body_node is not None else {}

    research_tags: list[str] = []
    for key in (
        "主要研究方向", "研究方向", "研究兴趣", "研究领域",
        "Research Interests", "科学研究",
    ):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    bio: str | None = None
    for key in ("个人简介", "简介", "基本信息", "研究概况", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    scope_text = (
        body_node.text(separator=" ", strip=True) if body_node is not None else ""
    )
    head_text = head.text(separator=" ", strip=True) if head is not None else ""

    # Fallback inline-prose research extractor.
    if not research_tags:
        research_tags = _extract_inline_research(scope_text)
        if not research_tags:
            research_tags = _extract_inline_research(bio or "")

    # Fallback email scan (last resort — many CSCEC pages render the email
    # outside the head card as a plain "邮箱：x@y" line within the bio).
    if not email:
        em, _ = extract_email(scope_text + " " + head_text)
        if em:
            email = em

    recruit_chunks = (
        find_recruit_paragraphs(scope_text) if scope_text else []
    )
    for key in ("招生信息", "招生", "招生招聘"):
        if key in sections:
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name or list_item.name_cn,
        title=title,
        email=email,
        phone=phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# Head-card label keys for college-site VSB profiles ("电子邮箱：x@y" etc.).
_VSB_HEAD_LABEL_KEYS = {
    "姓名": "name",
    "职称": "title",
    "职务": "post",
    "电子邮箱": "email",
    "邮箱": "email",
    "Email": "email",
    "电话": "phone",
    "座机": "phone",
    "办公电话": "phone",
    "联系电话": "phone",
    "办公地址": "office",
    "个人主页": "homepage",
    "主页": "homepage",
}


def _parse_profile_vsb(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """College-site VSB SiteBuilder profile (cs.xjtu / se.xjtu / aiar.xjtu).

    Layout:
        <div class="detail2_t"> or <div class="titbox">
          <div class="detail2_t_l"><img></div>
          <div class="detail2_t_r">
            <span>姓名：张三</span>
            <span>职称：教授</span>
            <span>电子邮箱：a@b</span>
            ...
          </div>
        </div>
        <div class="v_news_content">    <!-- main body -->
          <p><strong>个人简介</strong></p><p>...</p>
          <p><strong>研究方向</strong></p><p>...</p>
          <p><strong>招生信息</strong></p><p>...</p>
        </div>
    """
    tree = parse(html)
    head = (
        tree.css_first("div.detail2_t")
        or tree.css_first("div.titbox")
        or tree.css_first("div.teacher_info")
    )
    content = (
        tree.css_first("div.v_news_content")
        or tree.css_first("div#vsb_content")
        or tree.css_first("[id^='vsb_content']")
        or tree.body
    )

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None

    if head is not None:
        img = head.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src and not src.startswith("data:"):
                photo_url = absolutize(profile_url, src)
        for n in head.css("span, p, li, dd"):
            t = text_of(n)
            if not t or "：" not in t:
                continue
            label_raw, value = t.split("：", 1)
            label = label_raw.strip().rstrip(":：")
            value = value.strip()
            if not label or not value:
                continue
            kind = _VSB_HEAD_LABEL_KEYS.get(label)
            if kind is None:
                continue
            if kind == "name" and not name:
                name = value
            elif kind == "title" and not title:
                if any(kw in value for kw in _FACULTY_TITLE_KEYWORDS) or len(value) <= 12:
                    title = value
            elif kind == "email" and not email:
                em, _ = extract_email(value or t)
                if em:
                    email = em
            elif kind == "phone" and not phone:
                if re.search(r"\d{6,}", value):
                    phone = value
            elif kind == "homepage":
                link = n.css_first("a")
                if link is not None:
                    h = link.attributes.get("href") or ""
                    if h.startswith("http"):
                        homepage = h
                elif value.startswith("http"):
                    homepage = value

    sections = _split_pseudo_p_sections(content) if content is not None else {}

    research_tags: list[str] = []
    for key in (
        "主要研究方向", "研究方向", "研究兴趣", "研究领域",
        "Research Interests",
    ):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    bio: str | None = None
    for key in ("个人简介", "简介", "研究概况", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    scope_text = (
        content.text(separator=" ", strip=True) if content is not None else ""
    )
    # No structured section worked — try the first <p> as bio (legacy VSB
    # 简介 pages often skip explicit section headers).
    if bio is None and content is not None:
        first_p = content.css_first("p.vsbcontent_start") or content.css_first("p")
        if first_p is not None:
            p_text = text_of(first_p)
            if p_text and len(p_text) >= 20:
                bio = p_text[:1500]

    if not research_tags:
        research_tags = _extract_inline_research(scope_text)
        if not research_tags:
            research_tags = _extract_inline_research(bio or "")

    if not email and scope_text:
        em, _ = extract_email(scope_text)
        if em:
            email = em

    recruit_chunks = (
        find_recruit_paragraphs(scope_text) if scope_text else []
    )
    for key in ("招生信息", "招生", "招生招聘"):
        if key in sections:
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name or list_item.name_cn,
        title=title,
        email=email,
        phone=phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
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
class XjtuAdapter(SchoolAdapter):
    school_code = "xjtu"
    supports = {"cs", "sw", "ai"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        # cs / sw / ai all share the same VSB SiteBuilder template; the
        # parser doesn't need to branch on dept.
        return _parse_list_vsb(html, list_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        if _is_unified_profile(profile_url):
            return _parse_profile_unified(html, profile_url, list_item)
        return _parse_profile_vsb(html, profile_url, list_item)
