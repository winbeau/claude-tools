"""Fudan University adapter.

Covers three departments declared in schools.yaml as ``fudan.{cs,bd,ai}``:

- ``cs`` (计算与智能创新学院, formerly 计算机科学技术学院 — merged with the AI
  faculty under ``cs.fudan.edu.cn``). The public 师资 page (``/53161/list.htm``
  by 职称 / ``/53162/list.htm`` by 拼音) is a **Sudy-CMS SPA** whose teacher
  ``<ul>`` slots are populated client-side from a POST AJAX endpoint
  ``/_wp3services/generalQuery?queryObj=teacherHome``. The endpoint also
  accepts ``GET`` with the same querystring, returning a single JSON payload
  ``{total, data:[{title,cnUrl,headerPic,exField1,exField9,email,...}]}`` for
  all 288 entries. We therefore point ``list_urls[0]`` at the JSON URL and
  branch on response content-type / starting byte in :meth:`parse_list`.

  Individual profile pages live at ``ai.fudan.edu.cn/<slug>/list.htm`` and use
  a structured ``<div class="news_*">`` template carrying ``<span class="bt">
  label：</span><span class="nr">value</span>`` rows for 职称 / 邮件 / 研究领
  域 / 个人简介, plus a ``<div class="news_gr">`` block holding any external
  personal-page link.

- ``bd`` (大数据学院 — ``sds.fudan.edu.cn``). The 师资 page is **not** a SPA
  but uses a single inline ``<table>`` where each ``<tr>`` is one teacher,
  composed of a 130×163 photo (``<img alt="English Name">``), a 中文 name
  inside ``<h4>`` (sometimes wrapping an ``<a>`` to a personal site), and a
  long ``<p>`` bio. Most teachers do **not** have an internal profile page
  (``profile_url=None``); the list cell already contains the bio. We surface
  bio_text + the embedded ``主要研究方向：`` snippet from the list row itself
  so the pipeline still records useful data even without a profile fetch.

- ``ai`` (人工智能创新与产业研究院 — ``ai3.fudan.edu.cn``, **not** the
  ``aiii.fudan.edu.cn`` stub URL that v0.1 schools.yaml shipped with). The
  research institute publishes its 16-person 导师队伍 under
  ``/yjspy/dsdw.htm`` as ``<ul class="teacher-list"><li>`` cards. Profile
  pages live at ``/info/<col>/<aid>.htm`` and use the **standard sudy CMS
  template** (``<div class="v_news_content">``) where section headers are
  styled inline as ``<strong><span style="color:rgb(0,112,192)">...</span>
  </strong>`` (blue bold) inside ordinary ``<p>``s — different from Tsinghua
  where ``<h4>`` is used.

Notes on cross-school overlap:
  Fudan has many CS+AI **double-appointment** advisors (e.g. 邱锡鹏 listed in
  both the merged ``cs`` faculty and the ``ai`` (AI³) institute). The upstream
  pipeline deduplicates by ``(school, name_cn, email)`` upsert, so we deliberately
  return all advisors without filtering for school crossover.
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
# Title categories that count as "actual faculty" for the cs JSON filter.
# The endpoint returns 288 rows but ~96 are blank exField1 (administrators,
# library staff) and ~9 are 工程师/高级工程师/实验员 (pure engineering staff
# with no research advising). We keep:
#   - any row with a non-blank exField1 that contains 教授/研究员/讲师/导师
#   - any row where exField9 == 院士
# This still includes 工程师...导 entries (a few engineers do advise PG students).
_CS_TITLE_KEEP_KEYWORDS = ("教授", "研究员", "讲师", "博导", "硕导", "导师", "教师")
_CS_TITLE_KEEP_EXFIELD9 = {"院士", "正高", "副高"}


@register
class FudanAdapter(SchoolAdapter):
    school_code = "fudan"
    supports = {"cs", "bd", "ai"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        host = (urlparse(list_url).hostname or "").lower()
        # cs JSON endpoint is on cs.fudan.edu.cn; sniff response body to avoid
        # mis-classifying static cs.fudan.edu.cn pages that happen to share the
        # hostname.
        if _looks_like_cs_json(html):
            return _parse_list_cs_json(html, list_url)
        if host.endswith("sds.fudan.edu.cn"):
            return _parse_list_bd_table(html, list_url)
        if host.endswith("ai3.fudan.edu.cn"):
            return _parse_list_ai3(html, list_url)
        # fall through: SPA / unknown — return nothing rather than misparse nav.
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        host = (urlparse(profile_url).hostname or "").lower()
        if host.endswith("ai3.fudan.edu.cn"):
            return _parse_profile_ai3(html, profile_url, list_item)
        if host.endswith("ai.fudan.edu.cn") or host.endswith("cs.fudan.edu.cn"):
            return _parse_profile_cs(html, profile_url, list_item)
        # bd: most entries don't have a profile_url, but if one was set
        # (personal page), fall back to a best-effort extraction.
        return _empty_partial(list_item, profile_url)


# ---------------------------------------------------------------------------
# CS — JSON list from sudy-cms /_wp3services/generalQuery?queryObj=teacherHome
# ---------------------------------------------------------------------------


def _looks_like_cs_json(html: str) -> bool:
    head = html.lstrip()[:64]
    if not head.startswith("{"):
        return False
    return '"data"' in head[:200] or '"total"' in head[:200]


# Sudy CMS encodes some emails with a trailing zero-width space (U+200B) so
# the rendered string looks identical but copy-paste breaks downstream — clean
# it on the way in.
_EMAIL_CLEAN_RE = re.compile(r"[​‌‍ \s]")


def _clean_email(raw: str | None) -> str | None:
    if not raw:
        return None
    s = _EMAIL_CLEAN_RE.sub("", raw).strip()
    if not s or "@" not in s:
        return None
    return s.lower()


def _parse_list_cs_json(payload: str, list_url: str) -> list[ListItem]:
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return []
    rows = obj.get("data") or []
    items: list[ListItem] = []
    for r in rows:
        name = (r.get("title") or "").strip()
        if not name:
            continue
        title = (r.get("exField1") or "").strip() or None
        category = (r.get("exField9") or "").strip()
        # filter out non-advising staff
        keep = False
        if category in _CS_TITLE_KEEP_EXFIELD9:
            keep = True
        elif title and any(k in title for k in _CS_TITLE_KEEP_KEYWORDS):
            keep = True
        if not keep:
            continue
        cn_url = (r.get("cnUrl") or "").strip()
        profile = absolutize(list_url, cn_url) if cn_url else None
        header = (r.get("headerPic") or "").strip()
        # the placeholder ``/_res/articleType/teacher_CN.jpg`` is the generic
        # "no photo" sentinel — drop it so downstream doesn't treat it as a
        # real headshot.
        if header and "/_res/articleType/" not in header:
            photo = absolutize(list_url, header)
        else:
            photo = None
        items.append(
            ListItem(
                name_cn=name,
                profile_url=profile,
                title=title,
                email=_clean_email(r.get("email")),
                photo_url=photo,
            )
        )
    return items


# ---------------------------------------------------------------------------
# BD — sds.fudan.edu.cn inline table
# ---------------------------------------------------------------------------


_BD_TR_OPEN_RE = re.compile(r"<tr\b[^>]*>", re.I)
_BD_H4_BLOCK_RE = re.compile(r"<h4\b[^>]*>(.*?)</h4>", re.S | re.I)
_BD_H4_LINK_RE = re.compile(
    r"<h4\b[^>]*>.*?<a\b[^>]*href=[\"']([^\"']+)[\"']", re.S | re.I
)
_BD_IMG_RE = re.compile(
    r"<img\b[^>]*(?:src=[\"']([^\"']+)[\"'])(?:[^>]*alt=[\"']([^\"']+)[\"'])?",
    re.I,
)
_BD_IMG_ALT_RE = re.compile(r"<img\b[^>]*alt=[\"']([^\"']+)[\"']", re.I)
_BD_P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_STRIP_RE.sub(" ", html)).strip()


def _parse_list_bd_table(html: str, list_url: str) -> list[ListItem]:
    """One <tr> per teacher; name in <h4>, optional personal-page <a> inside h4,
    photo via the first <img alt=...>, bio in the first <p>."""
    items: list[ListItem] = []
    matches = list(_BD_TR_OPEN_RE.finditer(html))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        chunk = html[start:end]
        h4_m = _BD_H4_BLOCK_RE.search(chunk)
        if h4_m is None:
            continue
        name = _strip_tags(h4_m.group(1))
        if not name or len(name) > 30:
            continue
        # 兼职 / honorary / postdoc list pages share the table template — drop
        # rows whose name field is suspiciously long (Org-block headers).
        link_m = _BD_H4_LINK_RE.search(chunk)
        profile = (
            absolutize(list_url, link_m.group(1).strip()) if link_m else None
        )
        # Photo: first img inside the chunk
        img_m = _BD_IMG_RE.search(chunk)
        photo: str | None = None
        if img_m:
            src = img_m.group(1)
            if src and "/_upload/" in src:
                photo = absolutize(list_url, src)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=profile,
                title=None,  # bd rows don't carry a dedicated title field
                photo_url=photo,
            )
        )
    return items


# ---------------------------------------------------------------------------
# AI³ — ai3.fudan.edu.cn /yjspy/dsdw.htm
# ---------------------------------------------------------------------------


# Junk text seen in the right-rail teacher-list area on nav-only pages
_AI3_LIST_NOISE = {"导师队伍", "科学指导委员会"}


def _parse_list_ai3(html: str, list_url: str) -> list[ListItem]:
    tree = parse(html)
    items: list[ListItem] = []
    for li in tree.css("ul.teacher-list li"):
        a = li.css_first("a")
        if a is None:
            continue
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        name_node = li.css_first("div.teacher-name")
        if name_node is None:
            continue
        name = text_of(name_node)
        if not name or name in _AI3_LIST_NOISE:
            continue
        title_node = li.css_first("div.teacher-p p")
        title_text = text_of(title_node) if title_node is not None else ""
        title = _normalize_title(title_text) or None
        photo_node = li.css_first("div.teacher-img img")
        photo = None
        if photo_node is not None:
            src = photo_node.attributes.get("src") or ""
            if src:
                photo = absolutize(list_url, src)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absolutize(list_url, href),
                title=title,
                photo_url=photo,
            )
        )
    return items


def _normalize_title(s: str) -> str:
    """Title cells sometimes carry stray <br/> + leading <span></span>; the
    text extractor collapses these into ``" 教授、博导"`` with leading space
    and embedded newlines. Squash whitespace and drop pure punctuation."""
    if not s:
        return ""
    s = _WS_RE.sub(" ", s).strip()
    return s.strip("、，,/ ")


# ---------------------------------------------------------------------------
# CS profile (ai.fudan.edu.cn / cs.fudan.edu.cn news_* template)
# ---------------------------------------------------------------------------


# Mapping from sudy ``<span class="bt">label：</span>`` labels to logical
# field names. We never trust the same label twice in case the markup leaks
# multiple bt's into one block (e.g. nav remnants).
_CS_PROFILE_LABELS: dict[str, str] = {
    "职称": "title",
    "邮件": "email",
    "电子邮件": "email",
    "Email": "email",
    "电话": "phone",
    "研究领域": "research",
    "研究方向": "research",
    "研究兴趣": "research",
    "个人简介": "bio",
    "简介": "bio",
    "招生信息": "recruit",
    "招生": "recruit",
}

# Split on the usual CJK comma family **plus** full-stop, so a research
# description that mixes "A、B、C。研究涉及 D、E、F" doesn't glue the first
# half of the second sentence onto an unrelated tag.
_INTEREST_SPLIT_RE = re.compile(r"[、，,；;/。]+")
# Suffixes like "...等研究领域" / "...等方向" / "...等交叉学科场景" are noise
# tails — strip them so "多智能体等研究领域" becomes "多智能体". The two
# variants cover (a) the trailing "等XX" tail and (b) bare "...场景"/"...相关"
# survivors that lost their separator in the upstream prose.
_INTEREST_SUFFIX_RE = re.compile(
    r"(?:等(?:研究方向|研究领域|研究内容|领域|方向|相关|场景|学科场景|交叉学科场景)?|"
    r"等交叉学科场景|学科场景|场景|相关)$"
)
# Common sentence-lead stopwords that survive a comma split because they
# precede a real tag with no separator (e.g. "并应用于生命科学" → "生命科学").
_INTEREST_LEAD_RE = re.compile(
    r"^(?:研究涉及|研究包括|主要包括|主要|包括|涉及|应用于|并应用于|"
    r"以提高|致力于|为|是|的|与|及)"
)


def _is_tag(p: str) -> bool:
    """A research-interest tag is 2–15 CJK/ASCII chars with no end-of-sentence
    punctuation. Length cap intentionally tighter than tsinghua (25) — Fudan's
    "研究方向为..." prose generates many noise candidates from sentence
    fragments; the discrete-tag style (e.g. 智能互联网/社交网络/大语言模型)
    rarely exceeds 12 chars. Anything longer is almost certainly a sentence
    fragment surviving the comma-split."""
    return 2 <= len(p) <= 15 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Split a research-interest string into discrete tags."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for part in _INTEREST_SPLIT_RE.split(line):
            part = part.strip(" 　。.；;:：和与")
            part = _INTEREST_SUFFIX_RE.sub("", part).strip()
            part = _INTEREST_LEAD_RE.sub("", part).strip()
            if _is_tag(part):
                out.append(part)
        if len(out) >= 10:
            break
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]


def _parse_profile_cs(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    tree = parse(html)
    # The cs/ai.fudan profile body lives inside <div class="news_wz"> (the
    # right column of the teacher card). Falling back to wp_articlecontent /
    # the inner ``vsbcontent_start`` div catches the standard CMS profile
    # template too (in case a teacher's page uses that layout instead).
    content_node = (
        tree.css_first("div.news_wz")
        or tree.css_first("div.v_news_content")
        or tree.css_first("div.wp_articlecontent")
        or tree.body
    )
    if content_node is None:
        return _empty_partial(list_item, profile_url)

    scope_text = content_node.text(separator=" ", strip=True) or ""

    extracted: dict[str, str] = {}

    # Walk news_info > div.news_cara/news_email/news_ex/news_jj and pick up
    # values from <span class="bt">label：</span><span class="nr"|nr1|nr2>val</span>
    info = content_node.css_first("div.news_info") or content_node
    if info is not None:
        for div in info.css("div"):
            cls = (div.attributes.get("class") or "").strip()
            if "news_" not in cls or cls.startswith("news_imgs"):
                continue
            bt = div.css_first("span.bt")
            if bt is None:
                continue
            label = (bt.text(strip=True) or "").rstrip("：:").strip()
            field = _CS_PROFILE_LABELS.get(label)
            if not field:
                continue
            # Collect every nr / nr1 / nr2 / nr3 child text in order. Most
            # rows wrap the value in <span class="nr">; the 个人简介 row uses
            # <p class="nr"> instead — handle both tag types.
            parts: list[str] = []
            for child in div.iter(include_text=False):
                ccls = (child.attributes.get("class") or "").strip()
                if child.tag in {"span", "p"} and ccls.startswith("nr"):
                    t = child.text(strip=True)
                    if t:
                        parts.append(t)
            value = " ".join(parts).strip()
            if not value:
                continue
            if field == "title":
                # news_cara renders "教授、博导" as
                #   <span class="nr1">教授</span><span class="dd">、</span>
                #   <span class="nr2">博导</span>
                # We collect only nr* spans, so the separator <span class="dd">
                # is lost — re-insert "、" between the assembled parts so the
                # title reads "教授、博导" rather than "教授 博导".
                if "title" not in extracted:
                    joined = "、".join(p for p in parts if p)
                    extracted["title"] = _normalize_title(joined)
            elif field == "email":
                cleaned = _clean_email(value)
                if cleaned:
                    extracted["email"] = cleaned
            elif field == "phone":
                extracted.setdefault("phone", value)
            elif field == "research":
                if value not in {"", "无", "none"}:
                    extracted.setdefault("research", value)
            elif field == "bio":
                extracted.setdefault("bio", value)
            elif field == "recruit":
                extracted.setdefault("recruit", value)

    # external personal page link in div.news_gr
    homepage_link: str | None = None
    gr = content_node.css_first("div.news_gr a")
    if gr is not None:
        href = (gr.attributes.get("href") or "").strip()
        if href and "://" in href and "fudan.edu.cn" not in href:
            homepage_link = href

    research_tags = _split_interests(extracted.get("research", ""))

    bio_text = extracted.get("bio") or None
    if bio_text:
        # truncate to keep raw-text invariants
        bio_text = bio_text.strip()[:1500] or None

    # recruit signal — prefer the explicit 招生信息 field; otherwise scan body
    recruit_chunks: list[str] = []
    if extracted.get("recruit"):
        recruit_chunks.append(extracted["recruit"])
    recruit_chunks.extend(find_recruit_paragraphs(scope_text))
    raw_quota_text = "\n\n".join(_dedupe_short(recruit_chunks)[:3]) or None

    email = list_item.email or extracted.get("email")
    email_obf = False
    if not email:
        email, email_obf = extract_email(scope_text)

    title = list_item.title or extracted.get("title")

    photo = list_item.photo_url
    if photo is None:
        img = content_node.css_first("img.img_vsb_content") or content_node.css_first(
            "div.news_imgs img"
        )
        if img is not None:
            src = img.attributes.get("src") or ""
            if src and "/_upload/" in src:
                photo = absolutize(profile_url, src)

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=title,
        email=email,
        email_obfuscated=email_obf,
        phone=extracted.get("phone") or list_item.phone,
        photo_url=photo,
        homepage=homepage_link or profile_url,
        bio_text=bio_text,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# AI³ profile (ai3.fudan.edu.cn /info/<col>/<aid>.htm — sudy CMS but no <h4>)
# ---------------------------------------------------------------------------


# Same labels the cs profile uses, plus a couple of ai3-only phrasings.
_AI3_PSEUDO_HEADERS: set[str] = {
    "教育经历", "工作经历", "教育背景", "学习与工作经历",
    "研究方向", "研究兴趣", "研究领域", "研究内容",
    "个人简介", "简介", "个人介绍",
    "招生", "招生信息", "招生专业",
    "主要成果", "代表性成果", "学术成果", "代表论文", "代表论著",
    "发表论文", "论文成果",
    "奖励与荣誉", "荣誉", "获奖", "奖项",
    "学术兼职", "社会兼职", "兼职",
    "科研项目", "研究项目", "项目",
    "讲授课程", "教学",
}


def _parse_profile_ai3(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    tree = parse(html)
    content = (
        tree.css_first("div.v_news_content")
        or tree.css_first("div.wp_articlecontent")
        or tree.body
    )
    if content is None:
        return _empty_partial(list_item, profile_url)

    scope_text = content.text(separator=" ", strip=True) or ""
    sections = _split_strong_sections(content)

    research_tags: list[str] = []
    for key in ("研究方向", "研究兴趣", "研究领域", "研究内容"):
        if key in sections:
            seg = sections[key]
            # First sentence often starts with "研究方向为..." — strip the lead
            # phrase before tag-splitting.
            seg = re.sub(r"^研究方向(?:为|是|包括|涉及|主要)?[:：]?\s*", "", seg)
            research_tags = _split_interests(seg)
            if research_tags:
                break

    bio: str | None = None
    for key in ("个人简介", "简介", "个人介绍"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break
    if not bio:
        # Fallback: first long paragraph block ≥ 80 chars that isn't a list of
        # publication entries (those start with "1. " / "2. ").
        for p in content.css("p"):
            t = (p.text(strip=True) or "").strip()
            if 80 <= len(t) <= 1500 and not re.match(r"^\d+[.、)]\s", t):
                bio = t[:1500]
                break

    # recruitment signal — main body only, never global nav
    recruit_chunks: list[str] = []
    for key in ("招生", "招生信息", "招生专业"):
        if key in sections and sections[key].strip():
            recruit_chunks.append(sections[key].strip())
            break
    recruit_chunks.extend(find_recruit_paragraphs(scope_text))
    raw_quota_text = "\n\n".join(_dedupe_short(recruit_chunks)[:3]) or None

    email = list_item.email
    email_obf = False
    if not email:
        email, email_obf = extract_email(scope_text)

    photo = list_item.photo_url
    if photo is None:
        for img in content.css("img"):
            src = img.attributes.get("src") or ""
            if src and src.startswith("/__local/") and src.lower().endswith(
                (".jpg", ".jpeg", ".png")
            ):
                # the first non-bmp inline image is usually a headshot
                photo = absolutize(profile_url, src)
                break

    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=email,
        email_obfuscated=email_obf,
        phone=list_item.phone,
        photo_url=photo,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _split_strong_sections(content_node) -> dict[str, str]:
    """ai3 profiles place section labels in ``<p><strong>...</strong></p>``
    (often nested under styled <span>'s). Walk top-level <p>'s in document
    order; a <p> whose visible text matches a known label opens a new
    section, subsequent <p>'s feed the body until the next header.

    Trailing-colon / NBSP tolerant: ``\xa0教育经历`` and ``教育经历：`` both
    normalise to the bare label.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for p in content_node.css("p"):
        ptext = (p.text(strip=True) or "").strip()
        if not ptext:
            continue
        norm = _label_norm(ptext)
        # A pseudo-header is a short <p> matching a known label exactly
        # (or that label plus a few junk characters from styling glyphs).
        if 2 <= len(norm) <= 12 and norm in _AI3_PSEUDO_HEADERS:
            current = norm
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append(ptext)
    return {k: "\n".join(v) for k, v in sections.items()}


_LABEL_CLEAN_RE = re.compile(r"[\s\xa0​&;a-zA-Z]")


def _label_norm(s: str) -> str:
    """Drop NBSPs, ASCII junk and ``&nbsp;`` / styling spaces around a CJK
    label so ``" \xa0教育经历"`` and ``"&nbsp;教育经历"`` both match ``教育经历``.
    """
    # Strip leading non-CJK punctuation/spaces and trailing colons
    out = s.replace("\xa0", "").replace("​", "").strip()
    out = out.lstrip(" \xa0&nbsp;").rstrip("：:").strip()
    # If embedded glyphs are present (e.g. lead space + label), trim to first
    # CJK run.
    m = re.search(r"[一-鿿][一-鿿]+", out)
    if m:
        return m.group(0)
    return out


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _empty_partial(list_item: ListItem, profile_url: str) -> AdvisorPartial:
    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=list_item.email,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url or None,
        source_url=profile_url or None,
    )


def _dedupe_short(chunks: list[str]) -> list[str]:
    """Dedupe by first 80 chars, preserving order — used to collapse the
    main 招生 paragraph against the find_recruit_paragraphs() echo from the
    same body."""
    seen: set[str] = set()
    out: list[str] = []
    for c in chunks:
        c = (c or "").strip()
        if not c:
            continue
        key = c[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
