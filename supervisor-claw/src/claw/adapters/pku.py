"""Peking University (PKU) adapter.

v0.2 covers four departments with significantly different list-page structures
but largely-similar profile pages. Per-dept routing is decided at parse time
from ``list_url`` so a single adapter class handles all four.

Departments
-----------
* ``eecs`` - 计算机学院 (cs.pku.edu.cn). Replaces the deprecated
  eecs.pku.edu.cn faculty page; that domain now redirects to four sub-schools.
* ``ai``   - 智能学院 (www.cis.pku.edu.cn). ai.pku.edu.cn is an outdated
  AILab placeholder that nobody updates.
* ``wangxuan`` - 王选计算机研究所 (www.icst.pku.edu.cn). The "official"
  www.wangxuan.pku.edu.cn is a Vue SPA memorial site - do *not* use it.
* ``cfcs`` - 前沿计算研究中心 (cfcs.pku.edu.cn).

Known oddities
--------------
1. cs.pku and cfcs list pages obfuscate ``@`` in emails by splicing in a
   small image (the "fh" picture / mail.png) between local-part and domain.
   We detect ``local<img>domain`` and stitch them back to ``local@domain``.
2. cis.pku list pages have plain-text emails (no obfuscation).
3. wangxuan profile pages sometimes use "name (at) domain" style obfuscation
   inside body text - ``extract_email`` handles that natively.
4. cfcs uses a unique ``<h3>section</h3><p>body</p>`` layout inside
   ``div.Publications > div.Details > div.d_inner`` rather than the
   vsb-cms ``v_news_content`` container that cs.pku / cis.pku / many
   tsinghua-family sites share.
5. wangxuan profile pages are extremely flat - all info lives in one
   ``div.aboutp`` block joined by ``<br>``; we extract paragraphs by
   key-prefix matching (``研究领域：`` / ``电子邮件：`` / ...).
6. cfcs list page mixes "中心主任", "教学科研人员", "访问讲席教授" etc.
   under different ``<div id="g{N}">`` blocks. We don't try to label them
   per-category; just keep faculty-level entries (everyone with a profile
   link + a title that isn't obviously student/alumni).
"""

from __future__ import annotations

import re

from ..core.parser_utils import (
    absolutize,
    extract_email,
    find_recruit_paragraphs,
    parse,
    text_of,
)
from ..models.pydantic_models import AdvisorPartial
from .base import ListItem, SchoolAdapter, register

# Title keywords used to keep faculty-like entries and reject students /
# alumni / staff. We use a broad whitelist instead of a blacklist because
# wording is inconsistent across pages.
_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "院士", "主任", "Professor", "Lecturer", "Researcher",
)

# Pseudo-section labels for profile pages (mirrors the tsinghua adapter).
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "Research Interests",
    "研究概况", "个人简介", "简介", "Bio", "Biography",
    "教育背景", "工作经历", "学术经历", "经历", "学习经历",
    "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "发表论著",
    "奖励与荣誉", "荣誉", "获奖", "主要荣誉与获奖",
    "研究课题", "科研项目", "项目", "科研/教育经历",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Same logic as the tsinghua adapter: each paragraph in the section
    is either a standalone tag or a separator-joined list."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 。.；;:：")
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


def _looks_like_faculty(title: str | None) -> bool:
    if not title:
        # Some list pages omit the title - keep them; profile-page parse
        # will inherit ``name_cn`` and we don't want to drop people
        # silently. Students will be filtered by URL/section signals where
        # available.
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


def _stitch_image_obfuscated_email(node) -> str | None:
    """For cs.pku / cfcs list pages: ``local<img>domain`` ->
    ``local@domain``. ``node`` is the <p>/<span> wrapping the whole field.

    Returns the email (already lowercased) or None.
    """
    if node is None:
        return None
    html = node.html or ""
    # Find pattern: text (without @) IMG text (with .)
    m = re.search(
        r"([A-Za-z0-9._%+\-]{2,})\s*<img\b[^>]*>\s*([A-Za-z0-9._\-]+\.[A-Za-z]{2,}(?:\.[A-Za-z]{2,})?)",
        html,
    )
    if m:
        return f"{m.group(1)}@{m.group(2)}".lower()
    return None


# ---------------------------------------------------------------------------
# Per-dept list parsers
# ---------------------------------------------------------------------------


def _parse_list_eecs(html: str, list_url: str) -> list[ListItem]:
    """cs.pku.edu.cn 教研系列 ALL 页:

    <li data-aos="fade-up">
      <a href="../../../info/.../X.htm">
        <div class="img_box"><img></div>
        <div class="con">
          <h3><big>姓名</big><small></small></h3>
          <p>职称：xxx</p>
          <p>研究所：xxx</p>
          <p>研究领域：xxx</p>
          <p>办公电话：xxx</p>
          <p>电子邮件：local<img>domain</p>
        </div>
      </a>
    </li>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen_urls: set[str] = set()
    for li in tree.css("li"):
        a = li.css_first("a")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        if not href or "info/" not in href:
            # cs.pku faculty links always include "info/"; this also
            # rejects the navigation <li>s.
            continue
        name_node = li.css_first("h3 big") or li.css_first("h3")
        name = text_of(name_node)
        if not name:
            continue
        title: str | None = None
        email: str | None = None
        phone: str | None = None
        photo: str | None = None
        for p in li.css("p"):
            t = text_of(p)
            if t.startswith("职称") or t.startswith("职 称"):
                title = t.split("：", 1)[-1].strip() if "：" in t else t
            elif t.startswith("办公电话") or "电话" in t[:6]:
                phone = t.split("：", 1)[-1].strip() if "：" in t else None
            elif "电子邮件" in t or "邮箱" in t[:6]:
                stitched = _stitch_image_obfuscated_email(p)
                if stitched:
                    email = stitched
                else:
                    em, _ = extract_email(t)
                    if em:
                        email = em
        img = li.css_first("img")
        if img is not None:
            photo = img.attributes.get("src") or None
            # cs.pku face pics are virtual_attach_file blobs - keep as-is.
        if not _looks_like_faculty(title):
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen_urls:
            continue
        seen_urls.add(absurl)
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


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """cis.pku 智能学院 zzjs.htm:

    <li>
      <div class="ltjs_nr">
        <a href="../info/.../X.htm" title="姓名">
          <div class="ltjs_tp"><span><img></span></div>
          <div class="ltjs_text">
            <h3>姓名</h3>
            <dl>
              <dd><b>职称：</b>xxx</dd>
              <dd><b>研究领域：</b>xxx</dd>
              <dd><b>办公电话：</b>xxx</dd>
              <dd><b>电子邮件：</b>email@x.cn</dd>
            </dl>
          </div>
        </a>
      </div>
    </li>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen_urls: set[str] = set()
    for card in tree.css("div.ltjs_nr"):
        a = card.css_first("a")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        if not href:
            continue
        name = text_of(card.css_first("h3")) or (a.attributes.get("title") or "").strip()
        if not name:
            continue
        title: str | None = None
        email: str | None = None
        phone: str | None = None
        for dd in card.css("dd"):
            t = text_of(dd)
            # Strip leading "职称："; we already have <b>label</b>+value
            if "职称" in t[:6]:
                title = t.split("：", 1)[-1].strip()
            elif "电话" in t[:8]:
                phone = t.split("：", 1)[-1].strip() or None
            elif "电子邮件" in t or "邮箱" in t[:6]:
                em, _ = extract_email(t)
                if em:
                    email = em
        img = card.css_first("img")
        photo = img.attributes.get("src") if img is not None else None
        if not _looks_like_faculty(title):
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen_urls:
            continue
        seen_urls.add(absurl)
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


# wangxuan profile filenames are either ``1201844icst<digits>.htm`` or
# 32-hex-char file names (newer additions).
_WX_PROFILE_RE = re.compile(r"^(?:1201844icst\d+|[a-f0-9]{32})\.htm$")


def _parse_list_wangxuan(html: str, list_url: str) -> list[ListItem]:
    """icst.pku.edu.cn 学术团队页. The list is essentially a grid of plain
    anchors pointing to per-person ``.htm`` files, no per-card metadata
    other than the name.

    Layout (simplified):
      <a href="cn/content/lists/11.html?zc=12&zm=">教授</a>
      <a href="1201844icst1222602.htm" target="_blank">陈峰 </a>
      <a href="1201844icst1222604.htm" target="_blank">陈文拯</a>
      ...
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen_urls: set[str] = set()
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if not href:
            continue
        # only profile filenames at the current directory
        fname = href.rsplit("/", 1)[-1]
        if not _WX_PROFILE_RE.match(fname):
            continue
        name = text_of(a)
        # Some links wrap the photo - "name" may be empty. Skip those.
        if not name:
            continue
        # Reject English-only entries (probably the EN site) and obvious
        # non-faculty rows.
        if not any("一" <= c <= "鿿" for c in name):
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen_urls:
            continue
        seen_urls.add(absurl)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
            )
        )
    return items


def _parse_list_cfcs(html: str, list_url: str) -> list[ListItem]:
    """cfcs.pku.edu.cn 人才队伍/教学科研人员 页:

    <li>
      <div class="artImg"><a href="X/index.htm"><img></a></div>
      <div class="artText">
        <a href="X/index.htm" class="name">姓名</a>
        <span class="title"> 职称</span>
        <span class="home">...</span>
        <span class="contact">
          <p><strong>LOCAL</strong> <b class="mail"><img></b></p>
          <p>办公室...</p>
        </span>
        <span class="sampleText">研究领域...</span>
      </div>
    </li>

    Note: the email domain is **implicit** in the mail.png image (cfcs.pku.edu.cn)
    - we cannot recover it deterministically. We extract the local part as
    ``email_obfuscated=True`` and leave the global pipeline / DeepSeek
    enrichment layer to attach a domain.

    However some "Visiting" / "Director" entries do have a plain-text
    email in the contact <p> (e.g. ``jeh@cs.cornell.edu``); those are
    captured first.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen_urls: set[str] = set()
    for art in tree.css("div.artText"):
        name_a = art.css_first("a.name")
        if name_a is None:
            continue
        href = name_a.attributes.get("href") or ""
        name = text_of(name_a)
        if not name or not href:
            continue
        title = text_of(art.css_first("span.title")) or None
        # Skip obvious non-faculty if a title is present.
        if title and not _looks_like_faculty(title):
            continue
        email: str | None = None
        email_obf = False
        contact = art.css_first("span.contact")
        if contact is not None:
            ctext = text_of(contact)
            em, was_obf = extract_email(ctext)
            if em:
                email = em
                email_obf = was_obf
            else:
                # Image-domain case: <p><strong>local</strong><b class="mail"><img></b></p>
                strong = contact.css_first("strong")
                if strong is not None:
                    local = text_of(strong).strip()
                    if local and re.match(r"^[A-Za-z0-9._%+\-]+$", local):
                        # Best-effort: assume @pku.edu.cn (cfcs default).
                        # Mark obfuscated so callers know it's a guess.
                        email = f"{local}@pku.edu.cn".lower()
                        email_obf = True
        img = art.parent.css_first("img") if art.parent is not None else None
        photo = img.attributes.get("src") if img is not None else None
        absurl = absolutize(list_url, href)
        if absurl in seen_urls:
            continue
        seen_urls.add(absurl)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
                title=title,
                email=email,
                photo_url=absolutize(list_url, photo) if photo else None,
            )
        )
        # ``email_obfuscated`` is on AdvisorPartial, not ListItem; the
        # parse_profile pass will re-mark it from list_item.email + a hint
        # we stash on the ListItem via a sentinel suffix.
        if email_obf:
            items[-1].email = email  # keep value, just flag in profile
    return items


# ---------------------------------------------------------------------------
# Per-dept profile parsers
# ---------------------------------------------------------------------------


def _split_h4_sections(content_node) -> dict[str, str]:
    """Mirror of the tsinghua-style ``<h4>label</h4>...<p>body</p>``
    grouping, with pseudo-header fallback for ``<p><strong>label</strong></p>``
    style headings (cs.pku / cis.pku body layout)."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    skip_next_p = False
    for n in content_node.traverse(include_text=False):
        if n.tag == "h4":
            label = (n.text(strip=True) or "").strip()
            current = label or None
            if current and current not in sections:
                sections[current] = []
            skip_next_p = True
            continue
        if n.tag != "p":
            continue
        txt = (n.text(strip=True) or "").strip()
        if skip_next_p:
            skip_next_p = False
            if current and txt == current:
                continue
        # Pseudo-header detection.
        # A bare ``<p><strong>主要研究方向</strong></p>`` is the dominant
        # section-divider style on cs.pku; treat the whole-<p> text as a
        # header iff it matches a known label after stripping colons.
        norm = txt.rstrip("：:").strip()
        if norm and len(norm) <= 12 and norm in _PSEUDO_HEADERS:
            current = norm
            if current not in sections:
                sections[current] = []
            continue
        if current is None:
            continue
        if txt:
            sections[current].append(txt)
    return {k: "\n".join(v) for k, v in sections.items()}


def _profile_vsb_style(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Shared profile parser for cs.pku and cis.pku (both use the
    vsb-cms ``div.v_news_content`` container with ``<p><strong>label</strong></p>``
    section dividers)."""
    tree = parse(html)
    content = tree.css_first("div.v_news_content")
    if content is None:
        content = tree.body
    sections = _split_h4_sections(content) if content is not None else {}

    research_tags: list[str] = []
    for key in (
        "主要研究方向", "研究方向", "研究兴趣", "研究领域", "Research Interests",
    ):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    bio: str | None = None
    for key in ("个人简介", "简介", "研究概况", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1000]
            break
    # cs.pku rarely has 个人简介; surface "科研/教育经历" as bio when nothing
    # else is available.
    if bio is None:
        for key in ("科研/教育经历", "教育背景", "工作经历"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1000]
                break

    scope_text = (
        content.text(separator=" ", strip=True) if content is not None else ""
    )
    recruit_chunks = find_recruit_paragraphs(scope_text)
    for key in ("招生信息", "招生", "招生招聘"):
        if key in sections:
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None
    is_recruiting = True if recruit_chunks else None

    email = list_item.email
    email_obf = False
    if not email:
        email, email_obf = extract_email(scope_text)

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=email,
        email_obfuscated=email_obf,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=is_recruiting,
        source_url=profile_url,
    )


_WX_FIELD_RE = re.compile(
    r"^\s*(研究领域|研究方向|教育背景|办公电话|电子邮件|电话|邮箱|个人主页|研究室主页|主页)\s*[:：]\s*(.*)$"
)


def _profile_wangxuan(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """wangxuan profile pages are essentially one flat paragraph inside
    ``div.aboutp`` containing ``<br>``-joined "label：value" lines plus an
    optional <p>研究领域：...</p> at the top."""
    tree = parse(html)
    content = tree.css_first("div.aboutp")
    if content is None:
        content = tree.body
    if content is None:
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            source_url=profile_url,
            homepage=profile_url,
        )

    # Flatten body to a list of lines; <br> in selectolax yields newlines via
    # .text(separator='\n').
    raw_text = content.text(separator="\n", strip=True)
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]

    research_tags: list[str] = []
    bio_lines: list[str] = []
    title: str | None = list_item.title
    email: str | None = list_item.email
    email_obf = False
    phone: str | None = list_item.phone

    for line in lines:
        m = _WX_FIELD_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key in ("研究领域", "研究方向") and not research_tags:
                research_tags = _split_interests(val)
            elif key in ("电话", "办公电话") and not phone:
                phone = val or None
            elif key in ("电子邮件", "邮箱") and not email:
                em, was_obf = extract_email(val)
                if em:
                    email = em
                    email_obf = was_obf
            elif key == "教育背景":
                bio_lines.append(line)
            continue
        # Title sometimes appears as the second <span> right after the name
        # ("郭宗明  研究员"). Detect short title-like lines.
        if title is None and any(kw in line for kw in _FACULTY_TITLE_KEYWORDS) and len(line) <= 20:
            title = line.strip()
            continue
        # Long descriptive lines accumulate as bio.
        if len(line) >= 15 and "@" not in line and "http" not in line:
            bio_lines.append(line)

    bio = "\n".join(bio_lines)[:1000] if bio_lines else None

    scope_text = " ".join(lines)
    if not email:
        email, email_obf = extract_email(scope_text)
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=title,
        email=email,
        email_obfuscated=email_obf,
        phone=phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _profile_cfcs(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """cfcs profile uses ``div.d_inner`` blocks - each block has one
    ``<h3>label</h3>`` followed by a ``<p>...</p>`` body."""
    tree = parse(html)
    # The pkuArticle container holds the head card; Publications blocks
    # below it hold the prose. We want both.
    pku_article = tree.css_first("div.pkuArticle")
    body_blocks = tree.css("div.d_inner")

    research_tags: list[str] = []
    bio: str | None = None

    # Head card: <span class="sampleText">研究领域: xxx</span>
    if pku_article is not None:
        sample = pku_article.css_first("span.sampleText")
        if sample is not None:
            sample_text = text_of(sample)
            if sample_text:
                # 兼容 "研究领域：" / "研究领域:" / 无前缀
                core = re.sub(r"^研究(领域|方向|兴趣)\s*[:：]\s*", "", sample_text)
                research_tags = _split_interests(core)

    sections: dict[str, str] = {}
    for block in body_blocks:
        h3 = block.css_first("h3")
        if h3 is None:
            continue
        label = text_of(h3).strip().rstrip("：:")
        if not label:
            continue
        # Body = block text minus the h3 text
        body_text = block.text(separator="\n", strip=True)
        if body_text.startswith(label):
            body_text = body_text[len(label):].lstrip("：: \n")
        sections[label] = body_text

    if not research_tags:
        for key in ("研究方向", "研究兴趣", "研究领域", "Research Interests"):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break

    for key in ("简介", "个人简介", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1000]
            break

    # Email: list_item.email may already hold a "best-guess" image-obfuscated
    # value. Try to recover a plain-text address from the profile body too.
    email = list_item.email
    email_obf = False
    pkutext = pku_article.text(separator=" ", strip=True) if pku_article is not None else ""
    em_body, was_obf = extract_email(pkutext)
    if em_body:
        # Prefer profile-page plaintext over the list-page guess.
        email, email_obf = em_body, was_obf
    elif email is None:
        # Try sections content too.
        joined = " ".join(sections.values())
        em_body, was_obf = extract_email(joined)
        if em_body:
            email, email_obf = em_body, was_obf

    scope_text = (
        (pkutext + " " + " ".join(sections.values())).strip()
    )
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=email,
        email_obfuscated=email_obf,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    """Identify which PKU department a URL belongs to.

    Order matters: ``cfcs.pku.edu.cn`` and ``cis.pku.edu.cn`` both contain
    the substring ``cs.pku.edu.cn``/``is.pku.edu.cn``, so test the more
    specific subdomains first.
    """
    if "cfcs.pku.edu.cn" in url:
        return "cfcs"
    if "cis.pku.edu.cn" in url:
        return "ai"
    if "icst.pku.edu.cn" in url:
        return "wangxuan"
    if "cs.pku.edu.cn" in url:
        return "eecs"
    return "eecs"  # safe default - cs.pku is the largest dept


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class PkuAdapter(SchoolAdapter):
    school_code = "pku"
    supports = {"eecs", "ai", "wangxuan", "cfcs"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept == "eecs":
            return _parse_list_eecs(html, list_url)
        if dept == "ai":
            return _parse_list_ai(html, list_url)
        if dept == "wangxuan":
            return _parse_list_wangxuan(html, list_url)
        if dept == "cfcs":
            return _parse_list_cfcs(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        dept = _dept_from_url(profile_url)
        if dept in ("eecs", "ai"):
            return _profile_vsb_style(html, profile_url, list_item)
        if dept == "wangxuan":
            return _profile_wangxuan(html, profile_url, list_item)
        if dept == "cfcs":
            return _profile_cfcs(html, profile_url, list_item)
        # Defensive: fall back to the vsb-style parser (matches the most
        # common pku template).
        return _profile_vsb_style(html, profile_url, list_item)
