"""Xidian University (西安电子科技大学) adapter.

v0.4 covers three departments hosted on three sibling sub-domains and a
common faculty profile portal:

* ``cs``  — 计算机科学与技术学院 (cs.xidian.edu.cn). Dept-site lists three
  rank-style "师资" pages all linking to the central faculty portal
  ``faculty.xidian.edu.cn``:

  - ``szdw/rcpy.htm`` (人才培养总览, ~162 PIs)
  - ``szdw/dsjs.htm`` (导师介绍,   ~91 PIs)
  - ``szdw/xkky.htm`` (学科科研,   ~139 PIs)

  Each page is a giant ``<table>`` whose cells contain
  ``<a href="https://faculty.xidian.edu.cn/<id>/zh_CN/index.htm">姓名</a>``.
  Union ≈ 207 PIs. Pipeline upsert dedupes by (school, name, email).

* ``cse`` — 网络与信息安全学院 (ce.xidian.edu.cn). No dept-side list page;
  faculty are exposed only via ``faculty.xidian.edu.cn/xyjslb.jsp`` which
  caps each sub-tree at ~20 entries. We enumerate four sub-tree ids
  (信息安全系 / 网络工程系 / 密码系 / 教学单位) so the pipeline visits
  each — ≈ 80 unique PIs. List layout: ``<li><a class="sypics">``
  (photo) + ``<div class="text-title">`` containing
  ``.name a``, ``.zc`` (职称), ``.dw`` (单位).

* ``ai``  — 人工智能学院 (sai.xidian.edu.cn). ``yjspy/dszy1.htm`` is a
  ``<ul class="pics"><li>`` grid of 75 PIs. Most cards link to
  ``faculty.xidian.edu.cn/<id>/zh_CN/index.htm``; ~6 senior PIs link to
  ``web.xidian.edu.cn/<userid>/`` personal homepages (kept as-is, v0.3
  enricher fills in research).

Faculty profile pages (faculty.xidian.edu.cn) share a single Sudy-CMS
template with these salient containers:

* ``div.t_jbxx_nr`` — basic info (职称 / 性别 / 学历 / 在职信息 /
  所在单位 / 入职时间 / 学科 / 办公地点 / 联系方式).
* ``div.t_grjj_nr`` — 个人简介 (the bio).
* ``a[href*="yjfx/"]`` — 研究方向 anchor links inside the side panel,
  each anchor text is one research direction (possibly numbered
  "1. xxx" or semicolon-separated).
* ``span[_tsites_encrypt_field]`` — email / phone are hex-encoded in the
  static HTML and decrypted by ``/system/resource/tsites/tsitesencrypt.js``
  at runtime, so we can't recover them; we extract plaintext emails
  only when they appear in the bio prose.

Known limitations
-----------------
1. **国防七子 occasional 403.** Xidian is one of the "Seven Sons of
   National Defence" universities; static GETs from VPS occasionally get
   403/Connection-refused on certain sub-domains (e.g. ``ensai.xidian``).
   The pipeline retries via Playwright fetcher if available.
2. **GBK fallback.** Some legacy Xidian micro-sites are GBK-encoded; the
   pipeline normally passes already-decoded ``str`` HTML, but for raw
   ``bytes`` input we sniff the meta charset and decode with
   ``gb18030`` fallback so the parser never crashes on mojibake.
3. **Email always obfuscated on faculty portal.** Static HTML hides
   emails inside ``<span _tsites_encrypt_field>`` blobs. The adapter sets
   ``email_obfuscated=True`` whenever no plaintext email is found in the
   bio prose. v0.3 DeepSeek enrichment / Playwright fill these in.
4. **CSE list cap.** ``xyjslb.jsp`` shows at most ~20 PIs per sub-tree id
   regardless of ``st``/``pageNum``; the four sub-dept ids in
   ``schools.yaml`` cover ≈ 80 PIs out of 100+ true CSE PIs. v0.3 should
   add a Playwright-driven alphabetical sweep over the portal.
5. **web.xidian.edu.cn personal homepages.** ~6 SAI senior PIs link to
   custom ``web.xidian.edu.cn/<userid>/`` sites instead of the central
   portal — those profiles are not parsable by this adapter; we surface
   them as link-only ``ListItem``s and rely on v0.3 enrichment.
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
    "助理研究员", "工程师", "高级工程师", "实验师", "讲席", "特聘",
    "院士", "Professor", "Lecturer", "Researcher",
)

_NON_FACULTY_HINTS: tuple[str, ...] = (
    "博士后", "博士研究生", "硕士研究生", "在读", "校友", "兼职",
)

_SPLIT_RE = re.compile(r"[、，,；;/\|]+")
_RANK_PREFIX_RE = re.compile(r"^\s*\d+\s*[.\)\．、]\s*")
_NUM_DOT_RE = re.compile(r"\d+\s*[.\)\．、]\s*")

# Faculty profile URL pattern: https://faculty.xidian.edu.cn/<id>/zh_CN/index.htm
_FACULTY_URL_RE = re.compile(
    r"^https?://faculty\.xidian\.edu\.cn/[A-Za-z0-9_-]+/zh_CN/index\.htm",
    re.IGNORECASE,
)

# Gender / dup suffix to strip from list-page names.
_NAME_SUFFIX_RE = re.compile(r"\s*[（(](?:男|女|双聘|外聘|兼聘)[）)]\s*$")

# Detect "黄晓太" style duplicated href: "...index.htmhttps://..." → cut at second http
_DUP_HREF_RE = re.compile(r"^(https?://[^h]*\.htm)https?://", re.IGNORECASE)


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Tag splitter — same conventions as the other v0.4 adapters."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        # Strip leading "1. " / "2) " numbering.
        line = _RANK_PREFIX_RE.sub("", line)
        for p in _SPLIT_RE.split(line):
            p = _RANK_PREFIX_RE.sub("", p)
            p = p.strip(" 　。.；;:：等以及和与的")
            if _is_tag(p):
                out.append(p)
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
    if any(kw in title for kw in _NON_FACULTY_HINTS):
        return False
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS) or len(title) <= 12


def _normalize_name(name: str) -> str:
    """Strip whitespace inside names like '张 南' → '张南'; drop suffixes."""
    if not name:
        return ""
    # Drop trailing gender markers etc.
    name = _NAME_SUFFIX_RE.sub("", name).strip()
    # Collapse internal whitespace (full-width or half-width) between Chinese chars.
    name = re.sub(r"[\s　]+", "", name)
    return name


def _clean_href(href: str) -> str:
    """Fix duplicated-href bugs from xidian list pages (e.g. 'a.htmhttps://b')."""
    m = _DUP_HREF_RE.match(href)
    if m:
        return m.group(1)
    return href


def _decode_html_if_bytes(html: str | bytes) -> str:
    """Defensive GBK/UTF-8 sniffing if upstream passes raw bytes.

    Pipeline normally hands us ``str``; this guard keeps the adapter robust
    against the legacy GBK pages occasionally embedded on Xidian micro-sites.
    """
    if isinstance(html, str):
        return html
    # Sniff meta charset in first 2 KB.
    head = html[:2048].lower()
    if b"gb2312" in head or b"gbk" in head or b"gb18030" in head:
        return html.decode("gb18030", errors="replace")
    return html.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Dept routing
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    path = (urlparse(url).path or "").lower()
    if host.startswith("cs.xidian"):
        return "cs"
    if host.startswith("ce.xidian"):
        return "cse"
    if host.startswith("sai.xidian") or host.startswith("iaiac.xidian"):
        return "ai"
    if host.startswith("faculty.xidian"):
        # faculty.xidian.edu.cn/xyjslb.jsp?...id=<n>  — CSE college-list endpoint.
        # Only id=1596 / 1928 / 1929 / 1930 / 2661 / 3203 belong to CSE list mode.
        if "xyjslb" in path:
            return "cse"
        # individual profile pages — caller passes the originating list URL,
        # not the profile URL, into _dept_from_url for parse_list, so this
        # branch is mostly hit by parse_profile and we don't need to route
        # by dept there (all 3 depts share the same profile template).
        return "cs"
    return "cs"


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


def _parse_list_cs(html: str, list_url: str) -> list[ListItem]:
    """cs.xidian.edu.cn dept list (table layout).

    Each table cell contains ``<p><a href="https://faculty.xidian.edu.cn/.../zh_CN/index.htm">姓名</a></p>``.
    Some cells carry a ``<span>`` instead of an ``<a>`` (no profile yet) — skipped.
    Names are sometimes ``"王 琨（男）"`` with disambiguation suffix; we strip it.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        raw_href = (a.attributes.get("href") or "").strip()
        if not raw_href:
            continue
        href = _clean_href(raw_href)
        if not _FACULTY_URL_RE.match(href):
            continue
        name = _normalize_name(a.text(strip=True))
        if not name:
            continue
        if len(name) > 12:
            continue
        # require at least one CJK char in the name
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
            )
        )
    return items


def _parse_list_cse(html: str, list_url: str) -> list[ListItem]:
    """faculty.xidian.edu.cn/xyjslb.jsp college-list (used for cse/CSE).

    Layout per card:
        <li>
          <a class="sypics" target="_blank" href="https://faculty.xidian.edu.cn/<id>/zh_CN/index.htm">
            <img src="/_resource/fileshow/...">
          </a>
          <div class="text-title">
            <div class="name">
              <span class="fl"><a href="https://faculty.xidian.edu.cn/<id>/zh_CN/index.htm">姓名</a></span>
              <span class="fr">访问量</span>
            </div>
            <div class="zc">教授</div>
            <div class="dw">单位：网络与信息安全学院</div>
          </div>
        </li>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for li in tree.css("li"):
        title_box = li.css_first("div.text-title")
        name_a = li.css_first("div.text-title div.name a") or li.css_first(
            "div.name a"
        )
        if not name_a:
            continue
        raw_href = (name_a.attributes.get("href") or "").strip()
        href = _clean_href(raw_href)
        if not _FACULTY_URL_RE.match(href):
            continue
        name = _normalize_name(name_a.text(strip=True))
        if not name or len(name) > 12:
            continue
        if not any("一" <= c <= "鿿" for c in name):
            continue
        title: str | None = None
        if title_box is not None:
            zc = title_box.css_first("div.zc")
            if zc is not None:
                t = text_of(zc)
                if t:
                    title = t
        if not _looks_like_faculty(title):
            continue
        photo: str | None = None
        pic_a = li.css_first("a.sypics img") or li.css_first("img")
        if pic_a is not None:
            src = pic_a.attributes.get("src") or ""
            if src and "defaultteacherimg" not in src:
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


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """sai.xidian.edu.cn 导师主页 list (``yjspy/dszy1.htm``).

    Layout per card (one ``<ul class="pics">`` containing many ``<li>``):
        <li>
          <div class="li-pic"><a href="<profile_url>"><img src="/__local/..."></a></div>
          <div class="li-text"><a href="<profile_url>">姓名</a></div>
        </li>

    ``<profile_url>`` is most often ``https://faculty.xidian.edu.cn/<id>/zh_CN/index.htm``
    but ~6 senior PIs link to ``http://web.xidian.edu.cn/<userid>/`` personal
    sites — we accept either; parse_profile only handles the central template.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    # Scan ul.pics first; fall back to bare li scan if structure is unusual.
    containers = tree.css("ul.pics")
    if not containers:
        containers = [tree.body] if tree.body is not None else []
    for c in containers:
        for li in c.css("li"):
            text_a = li.css_first("div.li-text a") or li.css_first("a")
            if text_a is None:
                continue
            raw_href = (text_a.attributes.get("href") or "").strip()
            if not raw_href:
                continue
            href = _clean_href(raw_href)
            name = _normalize_name(text_a.text(strip=True))
            if not name or len(name) > 12:
                continue
            if not any("一" <= c2 <= "鿿" for c2 in name):
                continue
            # Skip in-page anchors / mailto / javascript: pseudo-links.
            if href.startswith(("#", "mailto:", "javascript:")):
                continue
            absurl = absolutize(list_url, href)
            if absurl in seen:
                continue
            seen.add(absurl)
            # photo
            photo: str | None = None
            img = li.css_first("div.li-pic img") or li.css_first("img")
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


# ---------------------------------------------------------------------------
# Profile parser (single template for all three depts)
# ---------------------------------------------------------------------------


# 基本信息 line patterns inside div.t_jbxx_nr (label key → field name).
# Labels come on lines like "性别：男" / "毕业院校：xxx" / "学历：博士研究生" /
# "在职信息：在岗" / "所在单位：xxx" / "入职时间：YYYY-MM-DD" / "学科：xxx" /
# "办公地点：xxx" / "联系方式：xxx".
_JBXX_LABEL_RE = re.compile(
    r"(姓名|性别|毕业院校|学历|学位|在职信息|所在单位|入职时间|学科|办公地点|联系方式|职称)\s*[:：]\s*([^\n\r]+?)(?=\s{2,}[一-鿿]{2,4}\s*[:：]|$)"
)

_INLINE_RESEARCH_RE = re.compile(
    r"研究(?:方向|兴趣|领域|概况)[包括为是涉及]{0,4}[：:]?\s*([^。\n]{2,200})"
)


def _parse_jbxx(text: str) -> dict[str, str]:
    """Parse 基本信息 paragraph into a label → value dict.

    Xidian's t_jbxx_nr renders one long string with multi-space separators:
    ``教授   博士生导师   性别：男    毕业院校：xxx   学历：博士   在职信息：在岗   ...``.
    The leading non-label segment carries 职称 markers (教授/博士生导师/...).
    """
    out: dict[str, str] = {}
    # Pull <label>: <value> matches greedily but stop at next 2-space gap +
    # next-label-looking head.
    for m in _JBXX_LABEL_RE.finditer(text):
        label = m.group(1).strip()
        value = m.group(2).strip().rstrip(":：")
        if value:
            out[label] = value
    # Title hint: leading prefix before first "label：" — keep the first
    # token that looks like a 职称 keyword.
    head = text.split("：", 1)[0] if "：" in text else text
    head = head.strip()
    if head:
        # head can be "教授   博士生导师   性别"; the last word before "性别"
        # might be a title hint. Split by whitespace.
        parts = re.split(r"[\s　]+", head)
        title_hint: str | None = None
        for p in parts:
            if p and any(kw in p for kw in _FACULTY_TITLE_KEYWORDS):
                title_hint = p
                break
        if title_hint and "职称" not in out:
            out["职称"] = title_hint
    return out


def _extract_research_interests(tree, scope_text: str) -> list[str]:
    """Pull research directions from yjfx anchors first, regex fallback otherwise."""
    tags: list[str] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if "/yjfx/" not in href:
            continue
        t = a.text(strip=True)
        if not t:
            continue
        # Strip leading "1. " / "2) " numbering
        t = _RANK_PREFIX_RE.sub("", t).strip()
        # If anchor text crams multiple tags joined by ；/、/，/space, split.
        if any(sep in t for sep in "；、，;,") or "  " in t:
            for p in _split_interests(t):
                if p not in seen:
                    seen.add(p)
                    tags.append(p)
        else:
            t = t.rstrip("。；;,，")
            if _is_tag(t) and t not in seen:
                seen.add(t)
                tags.append(t)
    if tags:
        return tags[:10]
    # Regex over scope_text fallback.
    m = _INLINE_RESEARCH_RE.search(scope_text)
    if m:
        return _split_interests(m.group(1))
    return []


def _parse_profile_faculty(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Profile parser for the faculty.xidian.edu.cn template."""
    tree = parse(html)
    grjj = tree.css_first("div.t_grjj_nr")
    jbxx = tree.css_first("div.t_jbxx_nr")
    photo_block = tree.css_first("div.t_photo") or tree.css_first("div.p_l_nr")

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    email_obfuscated = False
    phone = list_item.phone
    photo_url = list_item.photo_url

    # ---- basic info ----
    if jbxx is not None:
        # Use separator='  ' (2 spaces) to keep "label：value" pairs distinct.
        jb_text = jbxx.text(separator="  ", strip=True)
        info = _parse_jbxx(jb_text)
        if "职称" in info and not title:
            t = info["职称"]
            if any(kw in t for kw in _FACULTY_TITLE_KEYWORDS):
                title = t
        # Phone occasionally lurks in 联系方式 (mostly obfuscated, but try).
        if "联系方式" in info and not phone:
            val = info["联系方式"].strip()
            # Phone: 7-13 digits, optional dashes/spaces.
            pm = re.search(r"[\d][\d\-\s]{6,15}\d", val)
            if pm:
                phone = pm.group(0).strip()

    # ---- photo ----
    if photo_block is not None and not photo_url:
        img = photo_block.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src and "defaultteacherimg" not in src:
                photo_url = absolutize(profile_url, src)

    # ---- bio ----
    bio: str | None = None
    if grjj is not None:
        bio_text = grjj.text(separator=" ", strip=True)
        # Drop common JS leak strings that occasionally land here.
        bio_text = re.sub(r"jQuery\([^)]*\)[^;]*;?", "", bio_text)
        bio_text = re.sub(r"var\s+\w+\s*=[^;]+;", "", bio_text)
        bio_text = bio_text.strip()
        if bio_text:
            bio = bio_text[:1500]

    # ---- research interests ----
    scope_text_parts: list[str] = []
    if grjj is not None:
        scope_text_parts.append(grjj.text(separator=" ", strip=True))
    # Also include the side panel that contains 研究方向 anchors.
    side = tree.css_first("div.slideTxtBox") or tree.css_first("div.p_r_nr")
    if side is not None:
        scope_text_parts.append(side.text(separator=" ", strip=True))
    scope_text = " ".join(scope_text_parts)
    research_tags = _extract_research_interests(tree, scope_text)

    # ---- email ----
    if not email and scope_text:
        em, was_obf = extract_email(scope_text)
        if em:
            email = em
            email_obfuscated = was_obf
    if not email:
        # Email is hidden inside <span _tsites_encrypt_field> — mark obfuscated.
        if tree.css_first("span[_tsites_encrypt_field]") is not None:
            email_obfuscated = True

    # ---- recruiting signal ----
    recruit_chunks: list[str] = []
    if scope_text:
        recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        email_obfuscated=email_obfuscated,
        phone=phone,
        photo_url=photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _empty_partial(list_item: ListItem, profile_url: str) -> AdvisorPartial:
    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=list_item.email,
        email_obfuscated=False if list_item.email else True,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class XidianAdapter(SchoolAdapter):
    school_code = "xidian"
    supports = {"cs", "cse", "ai"}

    def parse_list(self, html: str | bytes, list_url: str) -> list[ListItem]:
        html_s = _decode_html_if_bytes(html)
        dept = _dept_from_url(list_url)
        if dept == "cs":
            return _parse_list_cs(html_s, list_url)
        if dept == "cse":
            return _parse_list_cse(html_s, list_url)
        if dept == "ai":
            return _parse_list_ai(html_s, list_url)
        return []

    def parse_profile(
        self, html: str | bytes, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        html_s = _decode_html_if_bytes(html)
        host = (urlparse(profile_url).hostname or "").lower()
        if host.startswith("faculty.xidian"):
            return _parse_profile_faculty(html_s, profile_url, list_item)
        # web.xidian.edu.cn personal homepages — outside this template's scope.
        return _empty_partial(list_item, profile_url)
