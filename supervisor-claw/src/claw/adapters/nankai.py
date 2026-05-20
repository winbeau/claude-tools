"""Nankai University adapter.

v0.4 covers four departments. They share a common cms (vsb Sitebuilder /
custom Bootstrap mixes) but use three very different list/profile templates:

Departments
-----------
* ``cs``    — 计算机学院 (cc.nankai.edu.cn). Custom "img-card" Bootstrap
  grid: each ``<div class="img-card">`` holds a photo + an ``<a>`` whose
  text is ``"姓名      职称"`` followed by ``<p>`` lines for email / phone /
  研究方向. Sub-pages: ``/jswyjy/list.htm``, ``/fjswfyjy/list.htm``,
  ``/js/list.htm`` (教授 / 副教授 / 讲师). Single page each — no pagination.
  Profile pages live at ``/2021/0323/c13619a<num>/page.htm`` and use
  ``div.wp_articlecontent > div.text`` (mostly free-form bio prose).

* ``cse``   — 密码与网络空间安全学院 (cyber.nankai.edu.cn). Identical
  template to ``cs`` (same Bootstrap theme, same per-rank list URLs).
  Profile URLs use the ``c13838a<num>`` slug instead of ``c13619a<num>``.
  Many people are listed on BOTH cc.nankai and cyber.nankai (e.g. 陈森);
  the pipeline dedupes by (school, name, email) so we just emit both and
  let it sort itself out.

* ``ai``    — 人工智能学院 (ai.nankai.edu.cn). Old-school vsb-cms wbnewsfile
  templates: list pages are simple ``<table><tr>`` rows of
  ``[<td><a>name</a></td><td>title</td><td>dept</td><td>research</td></tr>``.
  Profile pages use ``div.v_news_content`` with labelled "字段：值" pairs
  inside a nested ``<table>`` (姓名/职称/电子邮件/研究方向/个人简介/...).

* ``sw``    — 软件学院 (cs.nankai.edu.cn — note the misleading subdomain:
  cs.nankai.edu.cn is the SOFTWARE college, not the cc.nankai CS college).
  List pages render each teacher as ``<a class="item">`` cards with
  ``<div class="name">姓名</div>`` and ``<div class="des"><div>所属部门: ...</div>``.
  Profile pages use the same ``div.v_news_content`` container but lay
  fields out as ``<p><strong>label</strong><span>: value</span></p>``.

Known oddities
--------------
1. **Three completely different list-page templates** between cc/cyber,
   ai and sw — we route on hostname.
2. ``cs.nankai.edu.cn`` is the **software** college; ``cc.nankai.edu.cn``
   is the actual computer-science college. Easy to mix up.
3. cc.nankai / cyber.nankai list anchor text is ``"姓名      职称"`` with
   variable-width unicode spaces (  / full-width / regular). We
   strip those and split on whitespace to recover both fields.
4. ai.nankai profile body is one big nested ``<table>``; we flatten it to
   lines and key-prefix-match labels (same approach as the wangxuan PKU
   profile parser).
5. sw (cs.nankai) lists are paginated (``js.htm`` + ``js/1.htm`` ...);
   schools.yaml enumerates each page explicitly so this adapter is
   pagination-agnostic.
6. cc.nankai email "obfuscation" is just an empty <p></p> for "no email";
   not real obfuscation. We treat missing as None, not obfuscated.
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


# Title keywords used to keep faculty-like entries and reject students /
# alumni / staff. Broad whitelist (cf. pku/sjtu adapters).
_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "院士", "Professor", "Lecturer", "Researcher",
)

# Section labels we recognise as pseudo-headers in profile pages.
_PSEUDO_HEADERS: set[str] = {
    "基本信息", "研究方向", "研究兴趣", "研究领域", "主要研究方向",
    "Research Interests",
    "个人简介", "简介", "Bio", "Biography",
    "教育背景", "教育经历", "工作经历", "工作履历", "学术经历",
    "学术兼职", "社会兼职", "任职", "学术任职", "学术服务",
    "招生信息", "招生", "招生说明", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "撰写论文、专著、教材等",
    "奖励与荣誉", "荣誉", "获奖",
    "科研项目", "项目", "科研项目、成果、获奖、专利",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Split a comma/dunhao-separated research direction string into tag list.

    Mirrors the tsinghua/pku/sjtu adapters' splitter — line first, then
    in-line separators, filter by tag-shape predicate, dedupe.
    """
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 　。.；;:：等以及和与")
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
        # Some list rows omit titles. Keep them; profile-page parse will
        # inherit name and we don't want to silently drop people.
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Dept routing
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    """Identify which Nankai department a URL belongs to.

    Mapping (by hostname):
        cc.nankai.edu.cn       -> cs   (computer science college)
        cyber.nankai.edu.cn    -> cse  (cyber-space security college)
        ai.nankai.edu.cn       -> ai   (artificial intelligence college)
        cs.nankai.edu.cn       -> sw   (software college, NOT cs!)
    """
    host = (urlparse(url).hostname or "").lower()
    if "cc.nankai.edu.cn" in host:
        return "cs"
    if "cyber.nankai.edu.cn" in host:
        return "cse"
    if "ai.nankai.edu.cn" in host:
        return "ai"
    if "cs.nankai.edu.cn" in host:
        return "sw"
    return "cs"


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


_NAME_TITLE_SPLIT_RE = re.compile(r"[\s 　]+")
_EMAIL_LOOSE_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_LOOSE_RE = re.compile(r"^\+?\d[\d\-\s]{5,}\d$")


def _parse_list_imgcard(html: str, list_url: str) -> list[ListItem]:
    """cc.nankai / cyber.nankai Bootstrap "img-card" grid.

    Structure::

        <div class="img-card">
            <div class="view view-seventh"><img src=".../photo.jpg" alt="姓名"></div>
            <div class="img-content">
                <a href="/2021/0323/c13619a<num>/page.htm">姓名      职称</a>
                <p></p>                <!-- optional phone -->
                <p>x@nankai.edu.cn</p> <!-- optional email -->
                <p>研究方向短描述</p>   <!-- optional one-line research -->
            </div>
        </div>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for card in tree.css("div.img-card"):
        a = card.css_first("div.img-content a") or card.css_first("a")
        if a is None:
            continue
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        raw = text_of(a)
        if not raw:
            continue
        # Split "姓名      职称" on any unicode whitespace cluster.
        parts = [p for p in _NAME_TITLE_SPLIT_RE.split(raw) if p]
        if not parts:
            continue
        name = parts[0].strip()
        title = parts[1].strip() if len(parts) >= 2 else None
        if not name or len(name) > 20:
            continue
        # Strip obvious page-nav anchors.
        if not any("一" <= c <= "鿿" for c in name):
            continue
        email: str | None = None
        phone: str | None = None
        photo: str | None = None
        # Card photo
        img = card.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src:
                photo = absolutize(list_url, src)
        # The <p> siblings of <a> in img-content hold contact + research.
        content = card.css_first("div.img-content") or card
        for p in content.css("p"):
            t = text_of(p)
            if not t:
                continue
            if "@" in t:
                em = _EMAIL_LOOSE_RE.search(t)
                if em:
                    email = em.group(0).lower()
            elif phone is None and _PHONE_LOOSE_RE.match(t):
                phone = t.strip()
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
                photo_url=photo,
            )
        )
    return items


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """ai.nankai.edu.cn 师资页. Each row is::

        <tr id="line_u9_N">
          <td><a href="../info/1232/5761.htm">姓名</a></td>
          <td>教授</td>
          <td>自动化系</td>
          <td>研究方向短描述</td>
        </tr>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for tr in tree.css("tr"):
        tds = tr.css("td")
        if len(tds) < 2:
            continue
        a = tds[0].css_first("a")
        if a is None:
            continue
        href = (a.attributes.get("href") or "").strip()
        if not href or ".htm" not in href:
            continue
        # Reject pagination / sort anchors.
        if "info/" not in href and "page.htm" not in href:
            continue
        name = text_of(a).strip()
        if not name or len(name) > 20:
            continue
        if not any("一" <= c <= "鿿" for c in name):
            continue
        title = text_of(tds[1]).strip() if len(tds) >= 2 else None
        if title:
            title = title.strip()
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
                title=title or None,
            )
        )
    return items


def _parse_list_sw(html: str, list_url: str) -> list[ListItem]:
    """cs.nankai.edu.cn (软件学院) 师资页. Each teacher is an anchor card::

        <a class="di_bl item wow fadeInUp" href="../info/X/Y.htm" title="姓名">
          <div class="pic"><img src="..."></div>
          <div class="name te_c">姓名</div>
          <div class="des te_c">
            <div>所属部门: 软件工程系</div>
            <div>职 称: 讲席教授</div>
            <div>电子邮件: x@y</div>
            <div>研究方向: ...</div>
          </div>
        </a>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        cls = a.attributes.get("class") or ""
        if "item" not in cls.split():
            continue
        href = (a.attributes.get("href") or "").strip()
        if not href or "info/" not in href:
            continue
        name = (
            text_of(a.css_first("div.name"))
            or (a.attributes.get("title") or "").strip()
        )
        if not name or len(name) > 20:
            continue
        if not any("一" <= c <= "鿿" for c in name):
            continue
        title: str | None = None
        email: str | None = None
        phone: str | None = None
        photo: str | None = None
        # Photo
        img = a.css_first("div.pic img") or a.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src:
                photo = absolutize(list_url, src)
        # Each <div> in .des holds one labelled field.
        for d in a.css("div.des > div"):
            t = text_of(d)
            if not t:
                continue
            # Most labels use 全角冒号 ":" — but a stray full-width "：" appears too.
            sep_idx = -1
            for sep in (":", "：", " :", " ："):
                idx = t.find(sep)
                if idx != -1:
                    sep_idx = idx
                    break
            if sep_idx == -1:
                continue
            label = t[:sep_idx].strip().replace(" ", "")
            value = t[sep_idx + 1 :].lstrip(" ：:").strip()
            if not value:
                continue
            if label in ("职称", "职 称"):
                title = value
            elif label in ("电子邮件", "邮箱"):
                em = _EMAIL_LOOSE_RE.search(value)
                if em:
                    email = em.group(0).lower()
            elif label in ("办公电话", "电话"):
                if _PHONE_LOOSE_RE.match(value):
                    phone = value
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
                photo_url=photo,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile parsers
# ---------------------------------------------------------------------------


def _build_kv_sections(lines: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Walk a flat list of cleaned text lines and split into (kv, sections).

    * ``kv``       — single-line "label：value" fields (姓名/职称/电子邮件/...).
    * ``sections`` — multi-line content keyed by recognised pseudo-headers
                     (基本信息 / 个人简介 / 招生说明 / ...). Subsequent lines
                     accumulate into the current section until the next
                     pseudo-header is hit.
    """
    kv: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    field_re = re.compile(
        r"^(姓\s*名|性\s*别|所属部门|行政职务|职\s*称|学\s*历|所学专业|"
        r"个人主页|主页|电子邮件|邮箱|办公电话|电话|研究方向|研究兴趣|研究领域|导\s*师)"
        r"\s*[:：]\s*(.*)$"
    )
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Pseudo-header? Strip trailing colons before lookup.
        norm = line.rstrip("：:").strip()
        if 1 < len(norm) <= 18 and norm in _PSEUDO_HEADERS:
            current = norm
            sections.setdefault(current, [])
            continue
        m = field_re.match(line)
        if m:
            label = re.sub(r"\s+", "", m.group(1))
            value = m.group(2).strip()
            # Map common label aliases to canonical keys.
            if label == "姓名":
                kv.setdefault("姓名", value)
            elif label == "职称":
                kv.setdefault("职称", value)
            elif label in ("电子邮件", "邮箱"):
                kv.setdefault("电子邮件", value)
            elif label in ("办公电话", "电话"):
                kv.setdefault("电话", value)
            elif label in ("个人主页", "主页"):
                kv.setdefault("个人主页", value)
            elif label in ("研究方向", "研究兴趣", "研究领域"):
                kv.setdefault("研究方向", value)
                # Also seed the section if it's empty so _split_interests
                # has something to work with even without a real header.
                if "研究方向" not in sections:
                    sections["研究方向"] = [value]
            else:
                kv.setdefault(label, value)
            continue
        if current is not None:
            sections[current].append(line)
    return kv, {k: "\n".join(v).strip() for k, v in sections.items() if v}


def _profile_freeform(
    html: str, profile_url: str, list_item: ListItem, container_sel: str
) -> AdvisorPartial:
    """cc.nankai / cyber.nankai profile.

    These profiles are mostly free-form bio prose inside
    ``div.wp_articlecontent > div.text``. Email/phone if present are in
    ``list_item`` already (carried over from the list-page card). We
    cap bio at 1500 chars and skim the body for an inline 研究方向 phrase
    when the list page didn't give us one.
    """
    tree = parse(html)
    content = tree.css_first(container_sel)
    if content is None:
        content = tree.css_first("div.wp_articlecontent") or tree.css_first("div.v_news_content") or tree.body
    if content is None:
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=list_item.email,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=profile_url,
            source_url=profile_url,
        )

    scope_text = content.text(separator=" ", strip=True)
    bio_text = content.text(separator="\n", strip=True)

    # Trim repeated empty lines / boilerplate.
    bio_lines = [ln.strip() for ln in bio_text.split("\n") if ln.strip()]
    bio = "\n".join(bio_lines)[:1500] if bio_lines else None

    # Look for an inline "研究方向" phrase (e.g. "研究方向是计算机视觉…").
    # cc/cyber profiles are mostly free-form prose without a dedicated
    # research-direction section, so we have to be conservative: stop at
    # the FIRST clause-end punctuation (commas / periods / "。") so we
    # don't suck citation counts and h-index numbers into the tag list.
    research_tags: list[str] = []
    m = re.search(
        r"研究(?:方向|兴趣|领域)(?:主要)?[为是:：]?\s*([^。\n，,；;]{2,80})",
        scope_text,
    )
    if m:
        candidate = m.group(1).strip(" 　。.；;:：")
        research_tags = _split_interests(candidate)

    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

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
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _profile_vsb_kv(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """ai.nankai / sw.nankai profile.

    Both render labelled "字段：值" lines + section pseudo-headers inside
    ``div.v_news_content``. We flatten the container to lines, split into
    a key/value dict + a per-section dict, then assemble the
    ``AdvisorPartial``.
    """
    tree = parse(html)
    content = tree.css_first("div.v_news_content")
    if content is None:
        content = tree.css_first("div.wp_articlecontent") or tree.body
    if content is None:
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=list_item.email,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=profile_url,
            source_url=profile_url,
        )

    scope_text = content.text(separator=" ", strip=True)
    raw_lines = content.text(separator="\n", strip=True).split("\n")
    lines = [ln.strip() for ln in raw_lines if ln.strip()]
    kv, sections = _build_kv_sections(lines)

    title = list_item.title or kv.get("职称") or None
    email = list_item.email
    email_obf = False
    if not email:
        v = kv.get("电子邮件")
        if v:
            em, was_obf = extract_email(v)
            if em:
                email = em
                email_obf = was_obf
        if not email:
            email, email_obf = extract_email(scope_text)
    phone_raw = list_item.phone or (kv.get("电话") or None)
    phone = phone_raw.strip() if phone_raw else None
    if phone == "":
        phone = None
    homepage_raw = kv.get("个人主页") or ""
    homepage_raw = homepage_raw.strip()
    homepage = homepage_raw if homepage_raw.startswith(("http://", "https://")) else profile_url

    research_tags: list[str] = []
    for key in ("研究方向", "研究兴趣", "研究领域"):
        if key in sections and sections[key].strip():
            research_tags = _split_interests(sections[key])
            if research_tags:
                break
    if not research_tags and kv.get("研究方向"):
        research_tags = _split_interests(kv["研究方向"])

    bio: str | None = None
    for key in ("个人简介", "简介", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break
    # Surface 基本信息 as bio fallback when 个人简介 is absent — many ai pages
    # cram a long narrative there.
    if bio is None:
        for key in ("基本信息", "教育经历", "工作经历"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1500]
                break

    recruit_chunks = find_recruit_paragraphs(scope_text)
    for key in ("招生说明", "招生信息", "招生", "招生招聘"):
        if key in sections and sections[key].strip():
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=title,
        email=email,
        email_obfuscated=email_obf,
        phone=phone,
        photo_url=list_item.photo_url,
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
class NankaiAdapter(SchoolAdapter):
    school_code = "nankai"
    supports = {"cs", "cse", "ai", "sw"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept in ("cs", "cse"):
            return _parse_list_imgcard(html, list_url)
        if dept == "ai":
            return _parse_list_ai(html, list_url)
        if dept == "sw":
            return _parse_list_sw(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        dept = _dept_from_url(profile_url)
        if dept in ("cs", "cse"):
            return _profile_freeform(
                html, profile_url, list_item, "div.wp_articlecontent"
            )
        if dept in ("ai", "sw"):
            return _profile_vsb_kv(html, profile_url, list_item)
        # Defensive: fall back to the vsb-kv parser (covers the most
        # common Nankai layout).
        return _profile_vsb_kv(html, profile_url, list_item)
