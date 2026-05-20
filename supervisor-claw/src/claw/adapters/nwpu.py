"""Northwestern Polytechnical University (NWPU, 西北工业大学) adapter.

v0.4 covers three departments hosted on three sibling sub-domains:

* ``cs``   — 计算机学院 (jsj.nwpu.edu.cn). The "师资名单" page
  ``/snew/szdw/szmd.htm`` is one giant Word-exported table inside
  ``div#vsb_content``. Faculty are grouped by 系 (智能计算系统系 /
  计算机科学与软件系 / 计算机信息工程系 / 网络与机器人系统系 /
  计算机基础教学与实验中心 / 高性能中心) and then by rank
  (正高职称 / 副高职称 / 其他职称). Group headers are
  ``<strong>系名</strong>`` / ``<strong>正高职称</strong>``; each name
  is a plain ``<a href="https://teacher.nwpu.edu.cn/...">姓名</a>``.

* ``sw``   — 软件学院 (ruanjian.nwpu.edu.cn). The 教师队伍 page
  ``/szdw/jsdw.htm`` is a grid of card anchors. Each anchor carries
  ``title="姓名"``, an ``<img>`` (relative ``/__local/...`` thumb), and
  two ``<h3>`` children — the first is the name, the second is the
  honour-prefixed title (e.g. "国家级青年人才 教授" / "长聘副教授").

* ``cse``  — 网络空间安全学院 (wlkjaqxy.nwpu.edu.cn). The real
  ``/szdw/szgk.htm`` is a static brochure page with no individual
  teacher links; ``/szdw.htm`` is a 404 redirect. The only public
  faculty surface is ``/jsfc.htm`` ("教师风采"), ~17 internal
  ``info/1090/<id>.htm`` showcase articles. We parse the anchor text
  ``"...网络空间安全学院XXX教授"`` to recover (name, title); the
  resulting list is a known-incomplete subset of CSE faculty and is
  flagged as a v0.5 Playwright TODO.

Known limitations (v0.4)
------------------------
1. **teacher.nwpu.edu.cn is behind a TS-WAF JS-challenge wall**
   (HTTP 412 + ``<meta id=hK5iNqnNcwxO content=...>`` stub for every
   teacher profile URL). Our list parsers therefore recover everything
   we can from the list page itself (name + title + photo); the
   downstream pipeline / Playwright fetcher needs to crack TS-WAF
   before ``parse_profile`` becomes useful. Until then ``parse_profile``
   returns ``list_item`` data verbatim (no bio / email / research).
2. **CSE has no real faculty directory.** ``jsfc.htm`` profile articles
   are narrative pieces, not a roster — we treat CSE as
   partial coverage (~12 PIs vs ~30+ actual).
3. **No emails on any list page.** NWPU centralised teacher contact
   info into the TS-WAF protected teacher.nwpu.edu.cn portal; the
   per-school sub-domains carry only name/title/photo.
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

# Title keywords used to keep faculty-like entries.
_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "实验师", "讲席",
    "特聘", "长聘", "准聘", "院士", "Professor", "Lecturer",
    "Researcher",
)

# Department / rank headers as they appear on jsj.nwpu.edu.cn 师资名单
_NWPU_CS_RANK_HEADERS: tuple[str, ...] = (
    "正高职称", "副高职称", "其他职称", "博士后",
)

_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "主要研究领域",
    "Research Interests",
    "研究概况", "科研概况", "个人简介", "简介", "Bio", "Biography",
    "教育背景", "工作经历", "经历", "教育教学",
    "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目",
    "联系方式",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
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
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Dept routing
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "jsj.nwpu.edu.cn" in host or "computer.nwpu.edu.cn" in host:
        return "cs"
    if "ruanjian.nwpu.edu.cn" in host or "xinst.nwpu.edu.cn" in host:
        return "sw"
    if "wlkjaqxy.nwpu.edu.cn" in host or "cse.nwpu.edu.cn" in host:
        return "cse"
    return "cs"


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


def _parse_list_cs(html: str, list_url: str) -> list[ListItem]:
    """jsj.nwpu.edu.cn 师资名单 list page (``/snew/szdw/szmd.htm``).

    Document layout inside ``div#vsb_content``:

        <strong>智能计算系统系</strong>
            <strong>正高职称</strong>
                <a href="https://teacher.nwpu.edu.cn/2026010012">边思振</a>
                <a href="...">崔禾磊</a> ...
            <strong>副高职称</strong>
                <a href="...">张三</a> ...
            <strong>其他职称</strong>
                ...
        <strong>计算机科学与软件系</strong>
            ...

    We walk the container in document order, tracking the current
    department (long header) and rank (one of 正高职称 / 副高职称 /
    其他职称 / 博士后). Each ``teacher.nwpu`` anchor inherits both
    labels; the rank is mapped to a generic title so downstream
    consumers can filter faculty vs. postdocs cleanly.
    """
    tree = parse(html)
    container = tree.css_first("div#vsb_content") or tree.css_first(
        "div.v_news_content"
    )
    if container is None:
        return []

    items: list[ListItem] = []
    seen: set[str] = set()
    current_dept: str | None = None
    current_rank: str | None = None

    for n in container.traverse(include_text=False):
        tag = n.tag
        if tag == "strong" or tag == "b":
            t = text_of(n)
            if not t:
                continue
            # Skip the page heading itself.
            if t in ("师资名单", "师资队伍"):
                continue
            if t in _NWPU_CS_RANK_HEADERS:
                current_rank = t
                continue
            # Department header: short Chinese label ending in "系" /
            # "中心" / "实验室" etc. — keep it as long as it looks
            # like a section heading and not a name.
            if 2 <= len(t) <= 20 and any(
                kw in t for kw in ("系", "中心", "院", "所", "实验室", "团队")
            ):
                current_dept = t
                current_rank = None
                continue
        elif tag == "a":
            href = (n.attributes.get("href") or "").strip()
            if not href or "teacher.nwpu.edu.cn" not in href:
                continue
            name = text_of(n)
            if not name or len(name) > 20:
                continue
            # The list page never shows non-Chinese names with brackets;
            # reject obvious noise.
            if not any("一" <= c <= "鿿" or c.isalpha() for c in name):
                continue
            absurl = absolutize(list_url, href)
            if absurl in seen:
                continue
            seen.add(absurl)
            # Map rank header → title (best-effort, list page has no
            # finer-grained title text).
            title: str | None = None
            if current_rank == "正高职称":
                title = "教授"
            elif current_rank == "副高职称":
                title = "副教授"
            elif current_rank == "其他职称":
                title = None  # ambiguous — leave for v0.3 enricher
            elif current_rank == "博士后":
                # Skip postdocs at the list-page layer; they're not
                # faculty PIs.
                continue
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=absurl,
                    title=title,
                )
            )
    return items


def _parse_list_sw(html: str, list_url: str) -> list[ListItem]:
    """ruanjian.nwpu.edu.cn 教师队伍 list page (``/szdw/jsdw.htm``).

    Per-card layout:

        <a href="https://teacher.nwpu.edu.cn/zhengjiangbin.html"
           target="_blank" title="郑江滨">
          <div ...>
            <img src="/__local/.../X.jpg" width="120" height="160">
            <h3 ...>郑江滨</h3>
            <h3 ...>长聘教授</h3>
          </div>
        </a>

    The first ``<h3>`` is the bare name, the second is a possibly
    honour-prefixed title (e.g. "国家级青年人才 教授"). Postdocs are
    filtered out — they share the template but are not PIs.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or "teacher.nwpu.edu.cn" not in href:
            continue
        name = (a.attributes.get("title") or "").strip()
        h3s = a.css("h3")
        if not name and h3s:
            name = text_of(h3s[0])
        if not name or len(name) > 20:
            continue
        title: str | None = None
        if len(h3s) >= 2:
            t = text_of(h3s[1])
            if t:
                title = t
        # Drop postdocs (not faculty PIs).
        if title and "博士后" in title:
            continue
        if title and not _looks_like_faculty(title):
            # Keep ambiguous titles (the SW page sometimes embeds honour
            # phrases before the rank); only reject truly off-topic
            # roles.
            continue
        img = a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = (img.attributes.get("src") or "").strip()
            if src:
                photo = absolutize(list_url, src)
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
                title=title,
                photo_url=photo,
            )
        )
    return items


# CSE jsfc anchor text patterns:
#   "...网络空间安全学院XXX教授"   → name=XXX, title=教授
#   "...XXX副教授"                  → name=XXX, title=副教授
#   "记XXX老师的入校十五年"         → name=XXX, title=老师
# We require the segment to be preceded by a recognisable noun marker
# (学院 / 学者 / 个人 / 记) so we don't truncate the name. Longest-
# match titles (长聘教授 / 副教授 / 副研究员) are listed first so the
# alternation doesn't lop the "副" / "长聘" prefix into the name.
_CSE_JSFC_NAME_TITLE_RE = re.compile(
    r"(?:学院|个人|学者|工作者|记|奖章[—\- ]+)"  # noun marker
    r"([一-龥]{2,4}?)"                           # name (2-4 hanzi, non-greedy)
    r"(长聘教授|准聘教授|长聘副教授|副教授|助理教授|助理研究员"
    r"|副研究员|研究员|高级工程师|教授|讲师|工程师|老师)"
)


def _parse_list_cse(html: str, list_url: str) -> list[ListItem]:
    """wlkjaqxy.nwpu.edu.cn 教师风采 list page (``/jsfc.htm``).

    Each ``<a href="info/1090/<id>.htm" title="文章标题">`` is a
    narrative showcase article whose anchor text ends with the
    teacher's name + title (e.g. "...网络空间安全学院毛伯敏教授").

    This is the *only* public faculty surface on the CSE site —
    ``/szdw/szgk.htm`` carries no individual teacher pages and
    ``/szdw.htm`` is 404. We extract (name, title) via regex and treat
    coverage as a known-partial subset of true CSE faculty.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen_names: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        # Faculty showcase articles all live under /info/1090/.
        if not href or "info/1090/" not in href:
            continue
        text = text_of(a)
        if not text:
            continue
        m = _CSE_JSFC_NAME_TITLE_RE.search(text)
        if not m:
            continue
        name = m.group(1).strip()
        title = m.group(2).strip()
        # Skip leading-noun bleed-through (e.g. "学院" eaten into name).
        if not (2 <= len(name) <= 4):
            continue
        # Same person can have multiple showcase articles — dedupe by name.
        if name in seen_names:
            continue
        seen_names.add(name)
        # "老师" is a polite catch-all; downgrade to None so the v0.3
        # enricher resolves the actual rank.
        if title == "老师":
            title = None
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absolutize(list_url, href),
                title=title,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile parser
# ---------------------------------------------------------------------------


# Most NWPU teacher profiles live at teacher.nwpu.edu.cn which is
# behind TS-WAF (HTTP 412 + JS challenge). Static GETs land on a 2 KB
# stub like:
#
#   <meta id="hK5iNqnNcwxO" content="..." r='m'>
#   <script src="/jcGbaIA7dRsZ/eU4pnslpsr1i.<hash>.js" r='m'></script>
#
# We detect that stub and return list-item data verbatim so the
# pipeline doesn't fabricate spurious bios.
_TS_WAF_STUB_MARKERS: tuple[str, ...] = (
    "id=\"hK5iNqnNcwxO\"",
    "$_ts=window['$_ts']",
    "_$ep()",
)


def _is_ts_waf_stub(html: str) -> bool:
    if not html or len(html) > 8000:
        return False
    return any(m in html for m in _TS_WAF_STUB_MARKERS)


def _parse_profile_cse_jsfc(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """CSE 教师风采 narrative article profile.

    The article body lives in ``div.v_news_content``. There's no
    structured 研究方向 / 邮箱 section — it's a profile piece. We
    surface the prose as ``bio_text`` (capped at 1500 chars) and try a
    "研究方向/研究领域：xxx" inline regex for tags. No email is
    expected; the CSE site never exposes one.
    """
    tree = parse(html)
    content = tree.css_first("div.v_news_content") or tree.css_first(
        "div#vsb_content"
    )
    if content is None:
        return _empty_partial(list_item, profile_url)
    scope_text = content.text(separator=" ", strip=True)
    bio = scope_text[:1500] if scope_text else None
    research_tags = _extract_inline_research(scope_text)
    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=list_item.email,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text="\n\n".join(recruit_chunks[:3]) if recruit_chunks else None,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# Strict inline-research patterns: require an explicit anchor token
# ("：" / "为" / "包括" / "是" / "涉及") so we don't capture incidental
# noun phrases like "在其研究领域成果丰硕".
_INLINE_INCLUDE_RE = re.compile(
    r"(?:主要)?研究(?:方向|领域|兴趣)(?:包括|为|是|涉及|主要是)[：:]?\s*"
    r"([^。；;\n]{2,200})"
)
_INLINE_RESEARCH_RE = re.compile(
    r"(?:主要)?研究(?:方向|兴趣|领域|概况)\s*[:：]\s*([^。；;\n]{2,200})"
)


def _extract_inline_research(text: str) -> list[str]:
    for rx in (_INLINE_INCLUDE_RE, _INLINE_RESEARCH_RE):
        m = rx.search(text)
        if m:
            tags = _split_interests(m.group(1))
            if tags:
                return tags
    return []


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


def _parse_profile_teacher_portal(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """teacher.nwpu.edu.cn profile (centralised teacher portal).

    Behind TS-WAF for static GETs — Playwright integration is a v0.5
    item. If we somehow receive a non-stub body (e.g. via a future
    JS-challenge solver), do a best-effort scrape of the standard
    teacher portal layout: a head card with labelled rows and a body
    container with section headers.
    """
    if _is_ts_waf_stub(html):
        return _empty_partial(list_item, profile_url)
    tree = parse(html)
    # The portal's templates have changed multiple times — try a few
    # generic containers in priority order.
    content = (
        tree.css_first("div.main")
        or tree.css_first("div.content")
        or tree.css_first("div#vsb_content")
        or tree.body
    )
    if content is None:
        return _empty_partial(list_item, profile_url)
    scope_text = content.text(separator=" ", strip=True)
    email = list_item.email
    if not email and scope_text:
        em, _ = extract_email(scope_text)
        if em:
            email = em
    bio: str | None = None
    if scope_text:
        bio = scope_text[:1500]
    research_tags = _extract_inline_research(scope_text) if scope_text else []
    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=email,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text="\n\n".join(recruit_chunks[:3]) if recruit_chunks else None,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class NwpuAdapter(SchoolAdapter):
    school_code = "nwpu"
    supports = {"cs", "sw", "cse"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept == "cs":
            return _parse_list_cs(html, list_url)
        if dept == "sw":
            return _parse_list_sw(html, list_url)
        if dept == "cse":
            return _parse_list_cse(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        host = (urlparse(profile_url).hostname or "").lower()
        if "wlkjaqxy.nwpu.edu.cn" in host or "cse.nwpu.edu.cn" in host:
            return _parse_profile_cse_jsfc(html, profile_url, list_item)
        if "teacher.nwpu.edu.cn" in host:
            return _parse_profile_teacher_portal(html, profile_url, list_item)
        # Unknown host — empty stub so the pipeline keeps the
        # list-page info instead of crashing.
        return _empty_partial(list_item, profile_url)
