"""Nanjing University adapter.

Covers three departments declared in schools.yaml:

- ``cs``: 计算机科学与技术系 — old NJU CMS list (``<li class="list_item ...">``)
  hosting per-rank pages (教授 / 副教授 / 准长聘 / 跨学科博导 / 讲师组).
- ``ai``: 人工智能学院 — ``<li class="news ...">`` list. Profile URLs come in
  three flavours: ``/_redirect?siteId=...``, internal ``/X/X/cNNNNNaNNNN/page.htm``,
  and full external links (some advisors maintain personal sites under
  ``cs.nju.edu.cn`` or a custom slug).
- ``sw``: 软件学院 — static page where each teacher is a single
  ``<a href="/SLUG/index.html" style="color:#333;">姓名</a>``; profile pages use
  a different template (``div.mc`` with ``post-N mbox`` blocks and bold
  pseudo-headers like 简介 / 研究方向 / 荣誉奖励).
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


@register
class NjuAdapter(SchoolAdapter):
    school_code = "nju"
    supports = {"cs", "ai", "sw"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        host = (urlparse(list_url).hostname or "").lower()
        if host.endswith("software.nju.edu.cn"):
            return _parse_list_sw(html, list_url)
        if host.endswith("ai.nju.edu.cn"):
            return _parse_list_ai(html, list_url)
        if host.endswith("cs.nju.edu.cn"):
            return _parse_list_cs(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        html = _preprocess(html)
        host = (urlparse(profile_url).hostname or "").lower()
        if host.endswith("software.nju.edu.cn"):
            return _parse_profile_sw(html, profile_url, list_item)
        # cs.nju.edu.cn / ai.nju.edu.cn / www.nju.edu.cn → share the old CMS template
        return _parse_profile_njucms(html, profile_url, list_item)


_PRE_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_PRE_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
_PRE_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)


def _preprocess(html: str) -> str:
    """Strip <!-- ... -->, <style>, and <script> blocks before parsing.

    NJU profiles are frequently pasted in from MS Word and carry conditional
    comments (``<!--[if gte mso 9]>...``) plus inline <style> blocks. selectolax
    surfaces both as text via ``Node.text()``, which would dump huge
    ``@font-face`` / ``mso-font-pitch`` strings into ``bio_text``.
    """
    html = _PRE_COMMENT_RE.sub("", html)
    html = _PRE_STYLE_RE.sub("", html)
    html = _PRE_SCRIPT_RE.sub("", html)
    return html


# ---------------------------------------------------------------------------
# name / title helpers
# ---------------------------------------------------------------------------

_TITLE_PAREN_RE = re.compile(r"\s*[（(]([^（）()]+)[）)]\s*$")


def _normalize_name(name: str) -> str:
    """Collapse whitespace; if the result is pure CJK, drop interior spaces.

    Software school list aligns names with double full-width spaces (``骆  斌``),
    which would otherwise break ``(school, name) → upsert`` matching against
    other lists where the same person appears as ``骆斌``.
    """
    s = re.sub(r"\s+", " ", name.strip())
    no_space = s.replace(" ", "")
    if no_space and re.fullmatch(r"[一-鿿·•]+", no_space):
        return no_space
    return s


def _split_name_title(raw: str) -> tuple[str, str | None]:
    """Pull "name (title)" apart. Returns (name, title|None)."""
    s = re.sub(r"\s+", " ", raw.strip())
    m = _TITLE_PAREN_RE.search(s)
    if m:
        return _normalize_name(s[: m.start()]), (m.group(1).strip() or None)
    return _normalize_name(s), None


# Non-teacher anchors that share the same template/selectors as advisor links
# on the software school 师资 page.
_SW_LIST_NOISE = {
    "师资力量",
    "专业教师",
    "科研团队",
    "首页",
    "本科招生",
    "研究生招生",
    "联系我们",
}


# ---------------------------------------------------------------------------
# list parsers
# ---------------------------------------------------------------------------


def _parse_list_cs(html: str, list_url: str) -> list[ListItem]:
    """CS 系: per-rank pages — ``<li class="list_item iN">`` with one anchor."""
    tree = parse(html)
    items: list[ListItem] = []
    for li in tree.css("li.list_item"):
        a = li.css_first("span.Article_Title a") or li.css_first("a")
        if a is None:
            continue
        raw = (a.attributes.get("title") or "").strip() or text_of(a)
        if not raw:
            continue
        name, title = _split_name_title(raw)
        if not name:
            continue
        href = a.attributes.get("href") or ""
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absolutize(list_url, href) if href else None,
                title=title,
            )
        )
    return items


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """AI 学院: ``<li class="news ...">`` entries; href has three styles."""
    tree = parse(html)
    items: list[ListItem] = []
    for li in tree.css("li.news"):
        a = li.css_first("span.news_title a") or li.css_first("a")
        if a is None:
            continue
        raw = (a.attributes.get("title") or "").strip() or text_of(a)
        if not raw:
            continue
        name, title = _split_name_title(raw)
        if not name:
            continue
        href = a.attributes.get("href") or ""
        if not href:
            continue
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absolutize(list_url, href),
                title=title,
            )
        )
    return items


def _parse_list_sw(html: str, list_url: str) -> list[ListItem]:
    """软件学院: single anchor per teacher under category sections."""
    tree = parse(html)
    main = tree.css_first("div.mc") or tree.body
    if main is None:
        return []
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in main.css('a[style*="color:#333"]'):
        href = a.attributes.get("href") or ""
        if not href.endswith("/index.html"):
            continue
        raw = text_of(a)
        if not raw:
            continue
        if raw in _SW_LIST_NOISE:
            continue
        name, title = _split_name_title(raw)
        if not name or len(name) > 30:
            continue
        url = absolutize(list_url, href)
        if url in seen:
            continue
        seen.add(url)
        items.append(ListItem(name_cn=name, profile_url=url, title=title))
    return items


# ---------------------------------------------------------------------------
# profile parsers
# ---------------------------------------------------------------------------


def _parse_profile_njucms(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Old NJU CMS template used by 计算机系 + many AI 学院 profiles.

    Layout (simplified):

        <div class="wp_articlecontent">
          <div class="content">
            <img class="..." src="/_upload/..."/>
            <div class="detail">…一段中文 bio…</div>
          </div>
          <div class="other">
            <span>电话：NNN</span>
            <span>电子邮件：x@y</span>
          </div>
        </div>

    Some profiles redirect off-host to a personal site whose markup looks
    nothing like the above — in that case we fall back to body-wide regex.
    """
    tree = parse(html)
    content = (
        tree.css_first("div.wp_articlecontent")
        or tree.css_first("div.article")
        or tree.css_first("div.detail")
        or tree.body
    )
    if content is None:
        return _empty_partial(list_item, profile_url)

    scope_text = content.text(separator=" ", strip=True) or ""

    # bio: prefer the dedicated <div class="detail">, else first long paragraph
    bio: str | None = None
    detail = content.css_first("div.detail")
    if detail is not None:
        bio = (detail.text(separator="\n", strip=True) or "").strip() or None
    if bio is None:
        bio = _longest_paragraph(content)
    if bio:
        bio = bio[:1500]

    # contact: <div class="other"><span>电话：…</span><span>电子邮件：…</span></div>
    email = list_item.email
    email_obf = False
    phone = list_item.phone
    other = content.css_first("div.other")
    if other is not None:
        for span in other.css("span"):
            t = text_of(span)
            if not t:
                continue
            if "@" in t and not email:
                e, _ = extract_email(t)
                if e:
                    email = e
            elif re.search(r"\d{6,}", t) and not phone:
                phone = _strip_label(t)
    if not email:
        e, obf = extract_email(scope_text)
        if e:
            email = e
            email_obf = obf

    # research interests — extract from text near "研究方向/兴趣/领域"
    research_tags = _interests_from_text(bio or "")
    if not research_tags:
        research_tags = _interests_from_text(scope_text)

    # photo
    photo = list_item.photo_url
    if photo is None:
        img = content.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src and ("/_upload/" in src or src.lower().endswith((".jpg", ".png"))):
                photo = absolutize(profile_url, src)

    # recruitment
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=email,
        email_obfuscated=email_obf,
        phone=phone,
        photo_url=photo,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _parse_profile_sw(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """软件学院 profile.

    Two separate containers matter:

    - ``<div class="mc">``: a sidebar carrying contact metadata as
      ``<div class="aa"><span>标签：值</span></div>`` (邮箱 / 电话 / 地址 / 邮编).
    - ``<div class="middle">``: the main rich-text article. Long-form content
      (简介 / 荣誉奖励 / 主讲课程 / 研究方向) is separated by bold pseudo-
      headers — typically ``<p><strong>label</strong></p>`` (often with extra
      nested <span> tags for styling).

    Some profiles (e.g. 骆斌) skip pseudo-headers entirely and just dump bio
    into a single long ``<p>`` — fall back to the longest paragraph there.
    """
    tree = parse(html)
    sidebar = tree.css_first("div.mc")
    main = tree.css_first("div.middle") or tree.body

    if main is None and sidebar is None:
        return _empty_partial(list_item, profile_url)

    scope_main = main.text(separator=" ", strip=True) if main is not None else ""
    scope_side = sidebar.text(separator=" ", strip=True) if sidebar is not None else ""
    scope_text = f"{scope_main} {scope_side}".strip()

    email = list_item.email
    email_obf = False
    phone = list_item.phone
    contact_root = sidebar if sidebar is not None else main
    if contact_root is not None:
        for span in contact_root.css("div.aa span"):
            t = text_of(span)
            if not t:
                continue
            if t.startswith(("邮箱", "电子邮件", "Email", "E-mail")) and not email:
                e, _ = extract_email(t)
                if e:
                    email = e
            elif t.startswith(("电话", "Tel", "Phone")) and not phone:
                cleaned = _strip_label(t)
                if cleaned:
                    phone = cleaned
    if not email:
        e, obf = extract_email(scope_text)
        if e:
            email = e
            email_obf = obf

    # bio: pseudo-header → fallback to the longest <p> in main
    bio = _section_after_pseudo_header(
        main, {"简介", "个人简介", "个人介绍", "Biography", "Bio"}
    )
    if not bio and main is not None:
        bio = _longest_paragraph(main)
    research_text = _section_after_pseudo_header(
        main, {"研究方向", "研究兴趣", "研究领域"}
    )
    research_tags = _interests_from_text(research_text or "")
    if not research_tags:
        research_tags = _interests_from_text(bio or "")
    if not research_tags:
        research_tags = _interests_from_text(scope_main or scope_text)
    if bio:
        bio = bio[:1500]

    photo = list_item.photo_url
    if photo is None and main is not None:
        for img in main.css("img"):
            src = img.attributes.get("src") or ""
            if not src:
                continue
            low = src.lower()
            if low.endswith((".jpg", ".jpeg", ".png")) and (
                "/_upload/" in src or "/dfs/" in low or "/file/" in low
            ):
                photo = absolutize(profile_url, src)
                break

    recruit_chunks = find_recruit_paragraphs(scope_main or scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=email,
        email_obfuscated=email_obf,
        phone=phone,
        photo_url=photo,
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
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def _strip_label(text: str) -> str:
    """``电话：025-83621360`` → ``025-83621360``."""
    return re.sub(r"^[^：:]+[：:]\s*", "", text).strip()


def _longest_paragraph(node) -> str | None:
    candidates: list[str] = []
    for p in node.css("p"):
        t = (p.text(strip=True) or "").strip()
        if t and len(t) >= 50:
            candidates.append(t)
    if not candidates:
        return None
    return max(candidates, key=len)


_INTEREST_INTRO = re.compile(
    r"研究(?:方向|兴趣|领域|课题|内容)"
    r"\s*(?:主要|包括|涉及|为|是|有)?"
    r"\s*[:：，,]?"
    r"\s*([^。\n]{2,250})"
)
_INTEREST_SPLIT = re.compile(r"[、，,；;/]+")


def _interests_from_text(text: str) -> list[str]:
    if not text:
        return []
    m = _INTEREST_INTRO.search(text)
    if not m:
        return []
    seg = m.group(1).strip()
    out: list[str] = []
    for part in _INTEREST_SPLIT.split(seg):
        part = part.strip(" 　。.；;:：等以及和与")
        if not part:
            continue
        if 2 <= len(part) <= 25 and not any(c in part for c in "。！？()（）"):
            out.append(part)
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]


def _section_after_pseudo_header(node, labels: set[str]) -> str | None:
    """软件学院 rich-text: scan top-level <p>s, treat ``<p><strong>label</strong></p>``
    as a section start and collect following <p>s until the next such header.
    """
    if node is None:
        return None
    matched = False
    chunks: list[str] = []
    total = 0
    for p in node.css("p"):
        strong = p.css_first("strong") or p.css_first("b")
        ptext = (p.text(strip=True) or "").strip()
        strong_text = ""
        if strong is not None:
            strong_text = (strong.text(strip=True) or "").strip().rstrip("：:")
        is_header = (
            strong is not None
            and strong_text != ""
            and len(strong_text) <= 12
            and (ptext == strong_text or ptext.rstrip("：:") == strong_text)
        )
        if is_header:
            if strong_text in labels:
                matched = True
                chunks = []
                total = 0
                continue
            if matched:
                break
            continue
        if matched and ptext:
            chunks.append(ptext)
            total += len(ptext)
            if total > 1500:
                break
    text = "\n".join(chunks)
    return text or None
