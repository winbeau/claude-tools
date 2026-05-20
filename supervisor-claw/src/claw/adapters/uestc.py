"""University of Electronic Science and Technology of China (UESTC) adapter.

v0.4 targets three departments. UESTC switched **every** school sub-domain to
a TS-WAF JS-challenge wall in 2023-2024 except a handful of older sites that
were re-templated before the rollout. As of 2026-05, only
``www.sice.uestc.edu.cn`` (信息与通信工程学院) returns real HTML to a vanilla
``httpx`` GET; ``www.scse.uestc.edu.cn`` (计算机科学与工程学院 / CS) and
``sise.uestc.edu.cn`` (信息与软件工程学院 / SW), plus ``www.auto.uestc.edu.cn``,
all return the TS challenge stub (~2.4 KB, HTTP 202 / 412 with a
``<meta id="sx6c1KC7YLcx">`` token + inline ``$_ts`` script).

Departments (declared in ``schools.yaml``)
------------------------------------------
* ``ai``  — 信息与通信工程学院 (sice). v0.4 primary, the only one the static
  pipeline can crawl. The faculty pages embed the entire roster as a
  ``var ret = [...]`` JSON blob inside the HTML, with rich per-teacher
  fields (zc = 职称, yx = 邮箱, dh = 电话, kxyj = 科研概况, jsjj = 教师简介,
  td = 团队, xb = 系别). 112 教授 + 91 副教授 + 18 研究员 + 18 副研究员 +
  21 讲师 + others ≈ 280 records across the rank-bucket pages.
* ``cs``  — 计算机科学与工程学院 / 网络空间安全学院 (scse). Pipeline path
  via ``www.scse.uestc.edu.cn/szdw/jsml1/<rank>.htm``. Same SiteBuilder
  template family as sice (verified via UA-only fetches that occasionally
  slip through TS), but our static fetcher gets the TS stub. The adapter
  reuses the SICE parser when given a real HTML body; otherwise we return
  an empty list. **Known limitation** — needs browser-rendered fetch.
* ``sw``  — 信息与软件工程学院 / 示范性软件学院 (sise). Same situation as
  ``cs``. The list URL is documented in ``schools.yaml`` for the day
  Playwright-fetch lands in the crawl pipeline.

Page structure (sice template, also used by scse / sise)
--------------------------------------------------------
List page (``/szdw/jsml1/<rank>.htm``)::

    <select id="zimu" ...>...</select>
    <ul id="showdatainfo" class="on"></ul>   <!-- populated client-side -->
    <script>
        var ret = [
            {
                "picUrl": "/__local/.../X.jpg",
                "showTitle": "崔宗勇",
                "showTitlePY": "C",
                "showYear": "2025",
                "showDate": "2025年08月28日",
                "url": "../../info/1450/15583.htm",
                "fields": {
                    "td":   "雷达探测与成像技术团队",
                    "zszy": "硕士：081000信息与通信工程...",
                    "szm":  "C",
                    "jsjj": "2025/05-至今，电子科技大学...",
                    "zc":   "教授",
                    "xb":   "电子工程系",
                    "kxyj": "研究方向：SAR图像智能解译...",
                    "jxky": "主讲课程\\n本科生课程：信号与系统...",
                    "yx":   "zycui@uestc.edu.cn",
                    "dh":   "61830379"
                }
            }, ...
        ];
        // jquery pagination + AJAX render below
    </script>

Profile page (``/info/<treeid>/<id>.htm``)::

    <div class="teacher-info">
        <div class="ti-pic"><div class="pic"><img src="/__local/..."></div></div>
        <div class="ti-tx">
            <div class="ti-info">
                <h3>崔宗勇</h3>
                <p>职称: 教授</p>
                <p>系别: <a href="...">电子工程系</a></p>
                <p>团队: <a href="...">雷达探测与成像技术团队</a></p>
                <p>邮箱: zycui@uestc.edu.cn</p>
            </div>
            <div class="ti-details">
                <ul>
                    <li><h3>教师简介</h3> ...prose with <BR> ...</li>
                    <li><h3> 科学研究</h3> 研究方向：X、Y、Z...</li>
                    <li><h3> 教学与教学研究</h3> ...</li>
                    <li><h3> 招生专业</h3> ...</li>
                </ul>
            </div>
        </div>
    </div>

Known oddities
--------------
1. **TS-WAF wall on scse / sise / auto.** Static GETs return ~2.4 KB stubs;
   the real page only renders after a JS-generated cookie round-trip. Until
   the crawl pipeline gains Playwright-fetch support, ``parse_list`` for
   those depts returns the empty list on a stub body (length < 4 KB +
   ``sx6c1KC7YLcx`` token detected).
2. **List data is in inline JSON, not the DOM.** The ``<ul id="showdatainfo">``
   is empty on first paint — jQuery walks ``var ret`` and injects ``<li>``
   nodes client-side. We parse ``ret`` directly with a regex + ``json.loads``.
3. **Profile prose uses ``<BR>`` line breaks, not ``<br>``.** selectolax
   normalises both, but we strip ``\\xa0`` / multiple spaces explicitly so
   the bio doesn't end up double-spaced.
4. **科学研究 section header has a leading space** (``<h3><img> 科学研究</h3>``),
   so we normalise headers with ``.strip()`` before matching.
5. **Profile email is plain text** after ``邮箱:``; never image-obfuscated
   on the sample we captured. The faculty.uestc.edu.cn unified personal-page
   system (which encrypts emails) is firewalled (HTTP 403 to non-CN IPs),
   so v0.4 doesn't touch it.
"""

from __future__ import annotations

import json
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
    "助理研究员", "工程师", "高级工程师", "实验师", "讲席", "特聘",
    "院士", "Professor", "Lecturer", "Researcher",
)


_SPLIT_RE = re.compile(r"[、，,；;/]+")


# Pseudo-section labels that may show up in the profile bio prose.
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "主要研究领域",
    "Research Interests",
    "研究概况", "科研概况", "科学研究", "个人简介", "教师简介", "简介",
    "Bio", "Biography",
    "教育背景", "工作经历", "经历", "教育教学", "教学与教学研究",
    "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘", "招生专业",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目",
    "联系方式",
}


# Marker that identifies the TS-WAF JS-challenge stub returned by scse/sise/auto.
_TS_CHALLENGE_MARKER = "sx6c1KC7YLcx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Split a research-direction blob into tag list, same convention as
    the tsinghua / pku / buaa / hust adapters."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 　。.；;:：等以及和与")
            # Strip leading section labels that sometimes leak in
            # (e.g. "研究方向：X" → "X")
            if "：" in p:
                p = p.split("：", 1)[-1].strip()
            if ":" in p:
                p = p.split(":", 1)[-1].strip()
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


def _is_ts_challenge(html: str) -> bool:
    """Return True if ``html`` is the TS-WAF JS-challenge stub instead of
    a real list page. The stub is ~2.4 KB and embeds a meta token + an
    inline ``$_ts`` script; we detect either signal."""
    if not html or len(html) < 4096:
        if _TS_CHALLENGE_MARKER in html or "$_ts=window['$_ts']" in html:
            return True
    return False


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "scse.uestc.edu.cn" in host:
        return "cs"
    if "sise.uestc.edu.cn" in host or "sse.uestc.edu.cn" in host:
        return "sw"
    if "sice.uestc.edu.cn" in host:
        return "ai"
    if "auto.uestc.edu.cn" in host:
        # 自动化工程学院: paired with the AI department conceptually
        return "ai"
    return "ai"  # safe default — sice is the only crawlable one today


# ---------------------------------------------------------------------------
# List parsing
# ---------------------------------------------------------------------------


# ``var ret = [...];`` — the inline JSON blob. We match non-greedily up to
# the first ``];`` so we don't accidentally swallow trailing JS.
_RET_RE = re.compile(r"var\s+ret\s*=\s*(\[.*?\])\s*;", re.S)


def _parse_list_sice_json(html: str, list_url: str) -> list[ListItem]:
    """Parse the inline ``var ret = [...]`` JSON blob found on sice /
    scse / sise rank pages.

    Each entry has::

        {
          "picUrl": "/__local/...",
          "showTitle": "崔宗勇",
          "url": "../../info/1450/15583.htm",
          "fields": {"zc": "教授", "yx": "zycui@uestc.edu.cn",
                     "dh": "61830379", ...}
        }

    We reject entries whose ``zc`` (职称) doesn't match the faculty
    keyword list (drops 行政 / 博士后 wrapper rows on the rare pages
    that mix them in).
    """
    m = _RET_RE.search(html)
    if m is None:
        return []
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    items: list[ListItem] = []
    seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("showTitle") or "").strip()
        if not name or len(name) > 30:
            continue
        href = (entry.get("url") or "").strip()
        if not href:
            continue
        fields = entry.get("fields") or {}
        title = (fields.get("zc") or "").strip() or None
        # Drop entries whose title doesn't look like faculty (rare). For
        # name validation, accept either CJK or a 2+-word Latin name; this
        # is needed for the small handful of international faculty
        # (e.g. "Inserra Daniele" at sice).
        if not any("一" <= c <= "鿿" for c in name):
            if not re.match(r"^[A-Za-z][A-Za-z .\-']{2,}$", name):
                continue
        if title and not _looks_like_faculty(title):
            continue
        email_raw = (fields.get("yx") or "").strip()
        email: str | None = None
        if email_raw:
            em, _ = extract_email(email_raw)
            if em:
                email = em
        phone_raw = (fields.get("dh") or "").strip()
        phone = phone_raw or None
        pic = (entry.get("picUrl") or "").strip()
        photo: str | None = absolutize(list_url, pic) if pic else None
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


def _parse_list_dom_fallback(html: str, list_url: str) -> list[ListItem]:
    """Fallback DOM parser for list pages that *don't* embed ``var ret``.

    Targets the SiteBuilder ``<li id="line_uXX_N"><a><div class="pic"><img>
    </div><p>姓名</p></a></li>`` layout that scse / sise *might* serve once
    the TS challenge is solved.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    # Anchor-driven scan inside any list container.
    main = (
        tree.css_first("ul#showdatainfo")
        or tree.css_first("div.nymain")
        or tree.css_first("div.contentx")
        or tree.body
    )
    if main is None:
        return items
    for a in main.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or "/info/" not in href:
            continue
        if href.endswith(".pdf") or href.endswith(".doc"):
            continue
        # Name: prefer title attr, fall back to a <p> inside the anchor.
        name = (a.attributes.get("title") or "").strip()
        if not name:
            p_node = a.css_first("p") or a.css_first("div.info p")
            name = text_of(p_node)
        if not name:
            # Last resort: anchor text
            name = text_of(a)
        if not name or len(name) > 20:
            continue
        if not any("一" <= c <= "鿿" for c in name):
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        img = a.css_first("img")
        photo: str | None = None
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


def _parse_list_dispatch(html: str, list_url: str) -> list[ListItem]:
    """Try the JSON-blob path first (works on sice + any TS-bypassed
    scse/sise page), then fall back to DOM scanning."""
    if _is_ts_challenge(html):
        return []
    items = _parse_list_sice_json(html, list_url)
    if items:
        return items
    return _parse_list_dom_fallback(html, list_url)


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------


# Head-card label regex (``<p>职称: 教授</p>`` / ``<p>邮箱: x@y</p>``).
_HEAD_LABEL_RE = re.compile(r"^\s*(职称|系别|团队|邮箱|电话|办公|主页)\s*[:：]\s*(.*)$")


def _normalise_section_header(text: str) -> str:
    """Strip leading whitespace / 空 image-alt artefacts from a ``<h3>``."""
    return text.strip(" 　\n\t").rstrip("：:").strip()


def _profile_sice(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Parse the ``div.teacher-info`` profile template used by sice/scse/sise."""
    tree = parse(html)
    head = tree.css_first("div.teacher-info") or tree.css_first("div.ti-tx")
    if head is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url

    # Photo
    if photo_url is None:
        pic_img = head.css_first("div.ti-pic img") or head.css_first("img")
        if pic_img is not None:
            src = pic_img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)

    # Head card (ti-info): <h3>name</h3> + <p>label: value</p> rows.
    ti_info = head.css_first("div.ti-info")
    if ti_info is not None:
        h3 = ti_info.css_first("h3")
        if h3 is not None:
            n = text_of(h3)
            if n and not name:
                name = n
        for p in ti_info.css("p"):
            t = text_of(p)
            if not t:
                continue
            m = _HEAD_LABEL_RE.match(t)
            if m is None:
                continue
            label, value = m.group(1), m.group(2).strip()
            if label == "职称" and value and not title:
                title = value
            elif label == "邮箱" and value:
                em, _ = extract_email(value)
                if em and not email:
                    email = em
            elif label == "电话" and value and not phone:
                phone = value

    # Body sections (ti-details > ul > li > <h3>label</h3> + prose).
    sections: dict[str, str] = {}
    details = head.css_first("div.ti-details")
    if details is not None:
        for li in details.css("li"):
            h3 = li.css_first("h3")
            label = _normalise_section_header(text_of(h3)) if h3 is not None else ""
            # The body of the section is whatever follows the h3 inside
            # the <li>. selectolax doesn't give us a clean "next siblings"
            # API, so we take the full li text and strip the header prefix.
            full = li.text(separator="\n", strip=True)
            if not full:
                continue
            body_text = full
            if label and full.startswith(label):
                body_text = full[len(label):].strip()
            if label:
                if label in sections:
                    sections[label] = sections[label] + "\n" + body_text
                else:
                    sections[label] = body_text

    # Bio: prefer 教师简介 / 个人简介 over 科学研究 (which doubles as
    # research summary).
    bio: str | None = None
    for key in ("教师简介", "个人简介", "简介", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = _clean_bio(sections[key])[:1500]
            break
    if bio is None and "科学研究" in sections:
        bio = _clean_bio(sections["科学研究"])[:1500]

    # Research direction: scan 科学研究 / 研究方向 / 招生专业 for an inline
    # "研究方向：X、Y" phrase, then fall back to the whole text.
    research_tags: list[str] = []
    for key in ("科学研究", "研究方向", "研究领域", "招生专业"):
        if key in sections and sections[key].strip():
            tags = _extract_inline_research(sections[key])
            if tags:
                research_tags = tags
                break

    # Recruit signals — only inside teacher-info to avoid the page-wide nav.
    scope_text = head.text(separator=" ", strip=True)
    if not email and scope_text:
        em, _ = extract_email(scope_text)
        if em:
            email = em

    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
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


_INLINE_RESEARCH_RE = re.compile(
    r"研究(?:方向|兴趣|领域|概况)[为是:：]?\s*([^。\n]{2,200})"
)
_INLINE_INCLUDE_RE = re.compile(
    r"研究(?:方向|领域|兴趣)(?:包括|为|是|涉及)[：:]?\s*([^。\n]{2,200})"
)


def _extract_inline_research(text: str) -> list[str]:
    """Find a '研究方向：xxx' / '研究方向包括 xxx' phrase in prose."""
    for rx in (_INLINE_INCLUDE_RE, _INLINE_RESEARCH_RE):
        m = rx.search(text)
        if m:
            tags = _split_interests(m.group(1))
            if tags:
                return tags
    return []


_WHITESPACE_RUN_RE = re.compile(r"[  　\t]+")


def _clean_bio(text: str) -> str:
    """Collapse leading <BR>/space artefacts and runs of whitespace."""
    lines: list[str] = []
    for raw in text.split("\n"):
        line = _WHITESPACE_RUN_RE.sub(" ", raw).strip()
        if not line:
            continue
        # Drop section-label echoes that sometimes leak in.
        if line.rstrip("：:") in _PSEUDO_HEADERS and len(line) <= 14:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


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


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class UestcAdapter(SchoolAdapter):
    school_code = "uestc"
    supports = {"cs", "sw", "ai"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        # All three depts use (or are expected to use) the SiteBuilder
        # template family — the JSON-blob path works on a real body
        # regardless of dept, and returns [] on a TS challenge stub.
        return _parse_list_dispatch(html, list_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        if _is_ts_challenge(html):
            return _empty_partial(list_item, profile_url)
        return _profile_sice(html, profile_url, list_item)
