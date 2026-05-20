"""Sun Yat-sen University (SYSU / 中山大学) adapter.

v0.4 covers three CS/AI-relevant schools spread across the Guangzhou and
Zhuhai campuses. All three sites run **Drupal 10** (the unified ``sysu.edu.cn``
front-end profile) but render their list/profile pages with two different
templates and are hosted on three sub-domains:

Departments
-----------
* ``cs``  -- 计算机学院 (`cse.sysu.edu.cn`, Guangzhou east campus).
  List URL ``/teacher`` renders a single page of ~110 ``<div class="facultyblock">``
  cards: each card has name + title + email + research direction inline.
  Profile pages put the head metadata into ``div.teacherinfoblock`` and the
  long-form body into a ``div[data-block-plugin-id="entity_field:node:body"]``
  block.
  **NOTE**: the historically-documented ``sdcs.sysu.edu.cn`` host does NOT
  resolve - the school renamed to ``cse`` (School of Computer Science & Engineering).

* ``sw``  -- 软件工程学院 (`sse.sysu.edu.cn`, Zhuhai campus). Same Drupal
  template family but the list cards use ``div.list-images-1-1`` /
  ``div.list-content`` instead of facultyblock, and the per-rank pages
  ``/teacher/professor`` / ``/teacher/associate_professor`` /
  ``/teacher/assistant_professor`` aggregate to the same set the ``/teacher``
  landing shows. ``/teacher`` returns the full faculty roster in one page
  so a single list URL is sufficient.
  **List-page emails are sparsely populated** (~13% in fixture); the profile
  parser fills the rest.

* ``ai``  -- 人工智能学院 (`sai.sysu.edu.cn`, Zhuhai campus). Same
  ``list-images-1-1`` template as SSE. ``/teachers`` (plural) is the entry
  point - note the irregular spelling vs SSE's ``/teacher``.

Known oddities
--------------
1. **Two list-card templates**: ``cse`` uses ``div.facultyblock`` while
   ``sse``/``sai`` use ``div.list-images-1-1``. We branch on host inside
   ``parse_list``.
2. **Three host paths**: ``/teacher`` works for cse/sse, ``/teachers`` (with
   trailing s) works for sai.
3. **Section-header style varies by school**: cse uses
   ``<h3><strong>教师简介:</strong></h3>``, sai uses
   ``<h5><strong>个人简介</strong></h5>``, sse uses
   ``<p><strong>教师简介:&nbsp;</strong></p>`` — a bare-<p> pseudo-header.
   The shared ``_split_body_sections`` recognises all three by treating any
   short <h3>/<h4>/<h5>/<p>-with-<strong> whose text matches a known label
   as a section boundary.
4. **Profile name lives in different places**: cse exposes ``<h1>name</h1>``
   inside ``div.teacherinfoblock``; sse/sai only have ``<h2><strong>name</strong></h2>``
   in the page-title bar plus ``<title>name | school</title>``. We fall
   back to the ``<title>`` element when nothing else surfaces.
5. **Email obfuscation**: none. All three sites print plaintext emails -
   often wrapped in a ``<a href="mailto:...">`` element, sometimes with the
   trailing Chinese parenthetical (zheng zibin's profile encodes the
   recruiting hint inside the href). We extract via ``extract_email``.
6. **Recruitment hints**: ``郑子彬`` / ``董润敏`` style profiles embed
   ``招生`` text in the bio or in a dedicated 研究与招生 section. We surface
   the section if found, else fall back to ``find_recruit_paragraphs``.
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


# Pseudo-section labels recognised across all three sysu profile templates.
_PSEUDO_HEADERS: set[str] = {
    "教师简介", "个人简介", "简介", "Bio", "Biography",
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "研究与招生",
    "Research Interests",
    "教育背景", "教育经历", "工作经历", "工作履历",
    "学术兼职", "社会兼职", "任职", "学术任职",
    "招生信息", "招生", "招生招聘", "招生计划",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程", "教学工作",
    "代表性成果", "代表论著", "代表论文", "学术成果", "主要学术论著",
    "论文发表", "研究成果", "发表论著", "学术论著", "出版著作",
    "荣誉奖励", "奖励与荣誉", "荣誉", "获奖", "获奖情况", "获奖及荣誉",
    "研究课题", "科研项目", "项目", "项目资助",
    "联系方式", "联系邮箱", "邮箱",
}


_SPLIT_RE = re.compile(r"[、，,；;/\n]+")

# Phrases that signal a paragraph is *not* a research-interest list (招生
# call-outs, contact instructions, home-page URLs etc.) - we stop parsing
# the section once we hit any of these.
_NOT_TAG_HINTS: tuple[str, ...] = (
    "欢迎", "招生", "请联系", "邮箱", "Email", "电话", "电邮",
    "课题组", "本科生", "硕士研究生", "博士研究生", "实验室",
    "更多信息", "http", "@", "www.", "本科、",
)


def _is_tag(p: str) -> bool:
    if not (2 <= len(p) <= 25):
        return False
    if any(c in p for c in "。！？()（）"):
        return False
    if any(h in p for h in _NOT_TAG_HINTS):
        return False
    return True


def _split_interests(text: str) -> list[str]:
    """Take the leading research-direction snippet from ``text`` and split
    into tag-like fragments.

    sysu profile bodies sometimes lump research direction together with
    recruiting boilerplate or external URLs in one paragraph (e.g. 郑子彬
    writes ``可信大模型，软件可靠性，程序分析，区块链，智能合约，可信
    软件。本科、硕士和博士招生：常年欢迎报名``). We split each paragraph
    on the full-stop ``。`` first, then stop at the first sub-line that
    looks like recruiting prose rather than a tag list.
    """
    out: list[str] = []
    # Flatten input by both line breaks and Chinese full stops so that
    # ``research, blah. recruit ...`` doesn't pollute the tag list with the
    # 招生 part.
    sub_lines: list[str] = []
    for line in text.split("\n"):
        for chunk in line.split("。"):
            chunk = chunk.lstrip("﻿　 ").rstrip(" 　.；;:：")
            if chunk:
                sub_lines.append(chunk)
    for line in sub_lines:
        # Stop at the first line that is clearly not a tag list (招生 prose,
        # contact details, URL etc.).
        if any(h in line for h in _NOT_TAG_HINTS):
            break
        # Strip a leading 研究方向 / 研究领域 / 研究兴趣 / 主要研究方向 prefix.
        line = re.sub(
            r"^(?:主要)?研究(方向|领域|兴趣)\s*[:：]?\s*", "", line
        )
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 　.；;:：﻿")
            if _is_tag(p):
                out.append(p)
        if len(out) >= 12:
            break
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]


def _pick_best_email(email: str | None, scope_text: str) -> str | None:
    """Pick the strongest faculty email in ``scope_text``, biased toward the
    faculty mail domain (``mail.sysu.edu.cn``) over student aliases
    (``mail2.sysu.edu.cn``) and away from obvious 招生联系 student emails
    embedded next to recruiting prose.
    """
    if email:
        return email
    matches = re.findall(
        r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", scope_text
    )
    if not matches:
        return None
    # Prefer @mail.sysu.edu.cn, then any sysu.edu.cn, then the first hit.
    matches_lower = [m.lower() for m in matches]
    for cand in matches_lower:
        if cand.endswith("@mail.sysu.edu.cn"):
            return cand
    for cand in matches_lower:
        if "sysu.edu.cn" in cand and "mail2.sysu.edu.cn" not in cand:
            return cand
    return matches_lower[0]


def _looks_like_faculty(title: str | None) -> bool:
    if not title:
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "cse.sysu.edu.cn" in host or "sdcs.sysu.edu.cn" in host:
        return "cs"
    if "sse.sysu.edu.cn" in host:
        return "sw"
    if "sai.sysu.edu.cn" in host:
        return "ai"
    # default to cs - the largest dept
    return "cs"


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


def _parse_list_cse(html: str, list_url: str) -> list[ListItem]:
    """cse.sysu.edu.cn /teacher list.

    Layout per advisor::

        <li class="col-md-12 col-lg-6">
          <div class="facultyblock">
            <div class="img"><a href="/teacher/SLUG"><img src="..."></a></div>
            <div class="detail">
              <h3><a href="/teacher/SLUG">姓名</a><span>职称</span></h3>
              <p class="one-line ..."><strong>Email：</strong>x@mail.sysu.edu.cn</p>
              <p class="direction"><strong>科研平台: </strong>...</p>
              <p class="area"><strong>研究领域：</strong>...</p>
            </div>
          </div>
        </li>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for block in tree.css("div.facultyblock"):
        a = block.css_first("h3 a") or block.css_first("a")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        name = text_of(a)
        if not name or not href:
            continue
        # Strip trailing whitespace/punct that the CMS sometimes pads.
        name = name.strip()
        title_node = block.css_first("h3 span")
        title = text_of(title_node) or None
        email: str | None = None
        phone: str | None = None
        for p in block.css("p"):
            t = text_of(p)
            if not t:
                continue
            if "Email" in t or "@" in t or "邮箱" in t:
                em, _ = extract_email(t)
                if em:
                    email = em
        img = block.css_first("img")
        photo = img.attributes.get("src") if img is not None else None
        if not _looks_like_faculty(title):
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
                photo_url=absolutize(list_url, photo) if photo else None,
            )
        )
    return items


def _parse_list_sse_sai(html: str, list_url: str) -> list[ListItem]:
    """sse.sysu.edu.cn / sai.sysu.edu.cn faculty grid.

    Layout per advisor::

        <div class="list-images-1-1 inside-tb">
          <div class="list-left">
            <a href="/teacher/123"><img src="..."></a>
          </div>
          <div class="list-content">
            <h4 class="list-title one-line">
              <strong>姓名 </strong>
              <span class="text-light">  职称  </span>
            </h4>
            <div class="list-text inside-tb">
              <p><strong>现任职务：</strong>...</p>
              <p><strong>研究方向：</strong>...</p>
              <p><strong>Email：</strong><a href="mailto:x@mail.sysu.edu.cn">x@mail.sysu.edu.cn</a></p>
            </div>
          </div>
        </div>

    Some cards omit the email row entirely - the profile parser fills it in.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for block in tree.css("div.list-images-1-1"):
        # First <a> with /teacher/ or /teachers/ prefix.
        href = ""
        for a in block.css("a"):
            h = a.attributes.get("href") or ""
            if h.startswith("/teacher/") or h.startswith("/teachers/"):
                href = h
                break
        if not href:
            continue
        h4 = block.css_first("h4.list-title")
        if h4 is None:
            continue
        strong = h4.css_first("strong")
        name = text_of(strong) if strong is not None else text_of(h4)
        if not name:
            continue
        # Title text is everything in h4 after the <strong> child.
        title: str | None = None
        h4_text = h4.text(separator="|", strip=True)
        parts = [p.strip() for p in h4_text.split("|") if p.strip()]
        # Drop the name token from the parts; the remainder is the title.
        for p in parts:
            if p and p != name:
                title = p
                break
        email: str | None = None
        phone: str | None = None
        for p in block.css("p"):
            t = text_of(p)
            if not t:
                continue
            if "Email" in t or "@" in t or "邮箱" in t:
                em, _ = extract_email(t)
                if em:
                    email = em
        img = block.css_first("img")
        photo = img.attributes.get("src") if img is not None else None
        if not _looks_like_faculty(title):
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
                photo_url=absolutize(list_url, photo) if photo else None,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------


def _select_node_body(tree) -> object | None:
    """Return the Drupal ``entity_field:node:body`` block content node.

    The page has three ``div.field-body`` matches (header banner, page body,
    footer); we want the middle one. The reliable selector is the
    ``data-block-plugin-id`` attribute on the wrapper.
    """
    wrappers = tree.css('div[data-block-plugin-id="entity_field:node:body"]')
    for w in wrappers:
        fb = w.css_first("div.field-body")
        if fb is not None:
            return fb
    # Fallback: pick the 2nd field-body (the page body); use 1st if only one
    # exists.
    fbs = tree.css("div.field-body")
    if len(fbs) >= 2:
        return fbs[1]
    if fbs:
        return fbs[0]
    return None


def _split_body_sections(content_node) -> dict[str, str]:
    """Walk the body in document order, treating known section labels (in
    <h3>/<h4>/<h5> headings or in bare <p><strong>label:</strong></p>
    pseudo-headers) as section boundaries.

    Each section value is the joined text of the <p>'s following the
    header until the next header. We tolerate the cse style
    ``<h3><strong>教师简介:&nbsp;</strong></h3>`` and the sse style
    ``<p><strong>教师简介:&nbsp;</strong></p>`` simultaneously.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    if content_node is None:
        return {}
    for n in content_node.traverse(include_text=False):
        tag = n.tag
        if tag in ("h2", "h3", "h4", "h5", "h6"):
            label = (n.text(strip=True) or "").strip().rstrip("：:").strip()
            if label and (len(label) <= 16 or label in _PSEUDO_HEADERS):
                current = label
                sections.setdefault(current, [])
            continue
        if tag != "p":
            continue
        txt = (n.text(strip=True) or "").strip()
        if not txt:
            continue
        # Pseudo-header: <p><strong>研究领域:</strong></p>
        strong = n.css_first("strong")
        if strong is not None:
            strong_txt = (strong.text(strip=True) or "").strip().rstrip("：:").strip()
            # The <strong> covers the whole <p> text? Then it's a header.
            full_no_colon = txt.rstrip("：:").strip()
            if (
                strong_txt
                and (full_no_colon == strong_txt or full_no_colon == strong_txt + ":")
                and (len(strong_txt) <= 16 or strong_txt in _PSEUDO_HEADERS)
                and strong_txt in _PSEUDO_HEADERS
            ):
                current = strong_txt
                sections.setdefault(current, [])
                continue
            # Inline label like ``<p><strong>研究领域:</strong>tag, tag, ...</p>``
            if (
                strong_txt in _PSEUDO_HEADERS
                and not txt.startswith(strong_txt) is False  # always True; just for clarity
            ):
                # Open a new section iff we are not already in this one.
                inline_body = txt[len(strong_txt):].lstrip("：: \xa0").strip()
                current = strong_txt
                sections.setdefault(current, [])
                if inline_body:
                    sections[current].append(inline_body)
                continue
        if current is None:
            continue
        sections[current].append(txt)
    return {k: "\n".join(v).strip() for k, v in sections.items() if v}


def _parse_profile_cse(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    tree = parse(html)
    info = tree.css_first("div.teacherinfoblock")
    body = _select_node_body(tree)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url

    if info is not None:
        nm = text_of(info.css_first("h1"))
        if nm:
            name = nm
        # title is in the first <p> of div.teacherinfo
        tinfo = info.css_first("div.teacherinfo")
        if tinfo is not None:
            t_p = tinfo.css_first("p")
            if t_p is not None and not title:
                t_text = text_of(t_p)
                if t_text and "：" not in t_text and len(t_text) <= 20:
                    title = t_text
            for p in tinfo.css("div.teacherinfodetail p"):
                t = text_of(p)
                if not t:
                    continue
                if "邮箱" in t or "Email" in t or "@" in t:
                    em, _ = extract_email(t)
                    if em and not email:
                        email = em
                elif "电话" in t and not phone:
                    phone = t.split("：", 1)[-1].strip() if "：" in t else None
        img = info.css_first("img")
        if img is not None and not photo_url:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)

    sections = _split_body_sections(body)

    research_tags: list[str] = []
    for key in ("研究领域", "研究方向", "研究兴趣", "主要研究方向", "Research Interests"):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    bio: str | None = None
    for key in ("教师简介", "个人简介", "简介", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    scope_text = body.text(separator=" ", strip=True) if body is not None else ""
    email = _pick_best_email(email, scope_text)
    recruit_chunks = find_recruit_paragraphs(scope_text)
    for key in ("招生信息", "招生", "招生招聘", "研究与招生", "招生计划"):
        if key in sections:
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=phone,
        photo_url=photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _parse_profile_sse_sai(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """sse / sai profile - both omit ``div.teacherinfoblock`` and instead
    only have ``<h2><strong>name</strong></h2>`` in the page-title bar.
    Everything else lives in the entity_field:node:body block."""
    tree = parse(html)
    body = _select_node_body(tree)

    name = list_item.name_cn
    if not name:
        # The page-title <h2><strong>name</strong></h2> is reliable.
        for h2 in tree.css("h2"):
            s = h2.css_first("strong")
            if s is not None:
                nm = text_of(s)
                if nm and "导航" not in nm and len(nm) <= 20:
                    name = nm
                    break
        if not name:
            t_el = tree.css_first("title")
            if t_el is not None:
                title_txt = text_of(t_el)
                # "name | school" - take the first part.
                if "|" in title_txt:
                    name = title_txt.split("|", 1)[0].strip()

    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url

    sections = _split_body_sections(body)

    research_tags: list[str] = []
    for key in ("研究领域", "研究方向", "研究兴趣", "主要研究方向", "研究与招生"):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    bio: str | None = None
    for key in ("教师简介", "个人简介", "简介", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    scope_text = body.text(separator=" ", strip=True) if body is not None else ""
    email = _pick_best_email(email, scope_text)
    recruit_chunks = find_recruit_paragraphs(scope_text)
    for key in ("招生信息", "招生", "招生招聘", "研究与招生", "招生计划"):
        if key in sections:
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    # If still no research tags, scan the bio for an inline "研究方向：..." phrase.
    if not research_tags and bio:
        m = re.search(
            r"研究(?:方向|兴趣|领域)[为是:：]?\s*([^。\n]{2,200})",
            bio,
        )
        if m:
            research_tags = _split_interests(m.group(1))

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
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
class SysuAdapter(SchoolAdapter):
    school_code = "sysu"
    supports = {"cs", "sw", "ai"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept == "cs":
            return _parse_list_cse(html, list_url)
        if dept in ("sw", "ai"):
            return _parse_list_sse_sai(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        dept = _dept_from_url(profile_url)
        if dept == "cs":
            return _parse_profile_cse(html, profile_url, list_item)
        if dept in ("sw", "ai"):
            return _parse_profile_sse_sai(html, profile_url, list_item)
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=list_item.email,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=profile_url,
            source_url=profile_url,
        )
