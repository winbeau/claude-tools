"""Southeast University (东南大学) adapter.

v0.4 covers the consolidated 计算机科学与工程学院 / 软件学院 / 人工智能学院
(merged at the institutional level — see ``docs/reports/seu_report.md``).
The three colleges share a single Sudy-CMS portal at
``cse.seu.edu.cn`` (which also responds on the ``cose.seu.edu.cn`` and
``ai.seu.edu.cn`` host names; all three deliver the same HTML). Per-PI
profile pages live on the per-teacher sub-host pattern
``https://cs.seu.edu.cn/<slug>/main.htm`` — a layout very similar to
ShanghaiTech SIST and Fudan-cs (same Sudy WebPlus CMS family).

The roster is published on a single static list page (no SPA/AJAX, no
Playwright needed):

* ``/49355/list.htm`` — 教师按职称 (sections: 正高 / 副高 / 中级)
* ``/49356/list.htm`` — 教师按研究方向 (~6 research-area groups)
* ``/54820/list.htm`` — 教师按系别 (sections: 计算机科学系 / 计算机工程系 /
  影像科学与技术系)
* ``/dsxx/list.htm`` — 研究生导师 (subset of the above, 118 PIs)

All four list pages share the same desktop+mobile dual-table template:

    <h2 ...color:#0f429b...>section heading</h2>
    <div class="desktop-view">
      <table class="table-name">...
        <a href="https://cs.seu.edu.cn/<slug>/main.htm">name</a>
      ...
    </div>
    <div class="mobile-view">  (duplicate of the desktop table)

so we filter to ``desktop-view`` blocks only and dedupe by profile URL.
The 按职称 (``/49355/list.htm``) page is the canonical entry — every PI
appears exactly once with a clear rank-from-section hint. We expose that
URL in ``schools.yaml`` as the single ``list_urls`` entry; ``54820`` and
``49356`` are kept as comments for future extension (按系别 lets v0.5
attach 系 affiliation; 按研究方向 lets v0.5 attach a coarse research-area
tag).

Profile pages carry a structured "carrer" card identifying the
teacher with **all** the contact / research fields up front:

    <div class="carrer">
      <div class="carrer_con">
        <div class="title">胡宇韬</div>
        <div class="text"><b>职称：</b><span>副高</span></div>
        <div class="text"><b>所在院系：</b><span>影像科学与技术系</span></div>
        <div class="text"><b>研究方向：</b><span>计算机视觉、多模态学习、医学图像分析</span></div>
        <div class="text"><b>电话：</b><span></span></div>
        <div class="text"><b>邮箱：</b><span>huyutao@seu.edu.cn</span></div>
        <div class="text"><b>职务：</b><span></span></div>

The free-text body lives in repeated ``<div class="news_box"><div
class="tit">label</div><div class="con">prose</div></div>`` blocks with
labels 个人简介 / 研究方向 / 教育经历 / 工作经历 / 科研项目 / 论文著作 /
专利 / 获奖情况 / (招生招聘 — sometimes inline inside 个人简介).

Known limitations
-----------------
1. **Colleges are merged**. Schools.yaml currently declares a single
   ``cs`` dept whose ``name_cn`` is the long "计算机科学与工程学院、软件
   学院、人工智能学院". The launch prompt asked for three depts
   (``cs / sw / ai``) but the public site treats them as one — splitting
   would just duplicate every row. v0.5 could reintroduce per-系 codes
   using the 按系别 list page (cs.kxx / cs.gcc / cs.yxx).
2. **List page is static HTML**. No Playwright fetcher needed.
3. **Photo on the list page is unavailable** (the table has only names);
   per-profile photo is the third ``<img>`` (after logo + banner) inside
   ``/_upload/article/images/...`` — we grab it on the profile pass.
4. **Some profile cards leave 邮箱 / 电话 blank**. ~10% of PIs sampled.
   We fall back to ``<slug>@seu.edu.cn`` heuristic (the slug in the
   profile URL almost always matches the email local part, verified on
   xfqi/huyutao/luckzpz/baoxianqiang/...). Set ``email_obfuscated=True``
   on the heuristic email so v0.5 enricher can sanity-check via DeepSeek.
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
# Configuration
# ---------------------------------------------------------------------------

# Mapping from the 按职称 page's section labels (正高/副高/中级) to a
# canonical Chinese title. Normalised against full-width spaces and NBSP.
_RANK_TITLE_MAP: dict[str, str] = {
    "正高": "教授",
    "副高": "副教授",
    "中级": "讲师",
}

# The same school site is served on multiple hosts; profile links always
# use the canonical cs.seu.edu.cn host but we accept aliases just in case.
_PROFILE_HOST_PATTERN = re.compile(
    r"^https?://(?:cs|cse|cose|ai)\.seu\.edu\.cn/[^/]+/main\.htm$"
)

# Section labels that may appear as <div class="tit">label</div> headers
# inside per-PI profile pages. Trailing 招生招聘 is the explicit recruit
# section (some PIs embed recruit text inside 个人简介 instead).
_PROFILE_SECTIONS: tuple[str, ...] = (
    "个人简介",
    "研究方向",
    "教育经历",
    "工作经历",
    "科研项目",
    "论文著作",
    "专利",
    "获奖情况",
    "招生招聘",
    "招生信息",
)

# Card-label normalisation — strip trailing colons (both ASCII and CJK)
# and surrounding spaces. The "carrer_con" card uses these labels.
_CARD_LABELS: dict[str, str] = {
    "职称": "title",
    "所在院系": "dept_hint",
    "研究方向": "research",
    "研究领域": "research",
    "电话": "phone",
    "邮箱": "email",
    "Email": "email",
    "职务": "position",
}

# Interest splitting — same heuristic family as fudan/shtech but with a
# slightly wider char cap (SEU 教授 sometimes write "计算机视觉与多模态
# 学习" as a single tag).
_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    out: list[str] = []
    for line in (text or "").split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for part in _SPLIT_RE.split(line):
            part = part.strip(" 　。.；;:：等以及和与")
            if _is_tag(part):
                out.append(part)
        if len(out) >= 15:
            break
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]


def _clean_name(raw: str) -> str:
    """Anchor text often carries decorative NBSP spacing like
    ``陈\xa0\xa0\xa0阳`` — collapse to a clean ``陈阳`` (keeping ASCII space
    out of the result, mirroring how the other adapters store names)."""
    if not raw:
        return ""
    s = raw.replace("\xa0", " ").replace("&nbsp;", " ")
    # Collapse runs of whitespace inside CJK names: remove all ASCII spaces
    # between CJK characters (so "陈   阳" → "陈阳").
    s = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalise_label(raw: str) -> str:
    """Strip surrounding whitespace / NBSP / trailing colons from a card
    label so ``" 邮箱："`` and ``"邮箱"`` both normalise to ``邮箱``."""
    if not raw:
        return ""
    s = raw.replace("\xa0", "").replace("&nbsp;", "").strip()
    return s.rstrip("：:").strip()


def _slug_from_profile(url: str) -> str | None:
    """Extract the per-PI slug from a profile URL. Returns None for
    anything that doesn't look like a SEU teacher subsite path."""
    try:
        path = urlparse(url).path
    except (ValueError, AttributeError):
        return None
    m = re.match(r"^/([^/]+)/main\.htm$", path)
    if not m:
        return None
    return m.group(1)


def _looks_like_profile_url(url: str) -> bool:
    return bool(_PROFILE_HOST_PATTERN.match(url or ""))


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class SeuAdapter(SchoolAdapter):
    school_code = "seu"
    # SEU's CS / SW / AI colleges are merged at the institutional level
    # — schools.yaml declares a single ``cs`` dept covering all three.
    supports = {"cs"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        tree = parse(html)
        items: list[ListItem] = []
        seen_urls: set[str] = set()

        # The Sudy list page nests:
        #   <div class="paging_content">
        #     <h2 ...color:#0f429b...>section label</h2>
        #     <div class="desktop-view"><table class="table-name">...</table></div>
        #     <div class="mobile-view">...same teachers...</div>
        #     (repeat per section)
        #
        # so the <h2> headers are SIBLINGS of the desktop-view tables,
        # not children. We walk the common parent's children in document
        # order: each <h2> updates the running section, each desktop-view
        # contributes anchors. The mobile-view duplicates are skipped via
        # URL-dedup.
        containers = tree.css("div.paging_content")
        if not containers:
            # /dsxx/list.htm and a few alternate templates don't use
            # paging_content; fall back to the whole body.
            containers = [tree.body] if tree.body is not None else []

        for container in containers:
            current_section: str | None = None
            # Direct child iteration is sufficient — we only need to see
            # the section <h2> and the desktop-view block, both top-level
            # children of paging_content. ``iter`` yields direct children.
            for child in container.iter(include_text=False):
                tag = child.tag
                if tag == "h2":
                    label = _clean_name(text_of(child))
                    if label:
                        current_section = label
                    continue
                cls = (child.attributes.get("class") or "").strip()
                if "desktop-view" in cls.split():
                    self._consume_block(
                        child, list_url, current_section, items, seen_urls
                    )
                elif tag in {"table", "ul", "div"} and not containers[0] is tree.body:
                    # Some templates inline the table directly under
                    # paging_content without a wrapper; consume that too.
                    self._consume_block(
                        child, list_url, current_section, items, seen_urls
                    )

            # Fallback: if the structured walk found nothing (e.g. a
            # template variant), scrape all <a href=".../main.htm">
            # inside this container regardless of layout.
            if not any(it.profile_url and it.profile_url not in seen_urls for it in []) and not items:
                self._consume_block(
                    container, list_url, current_section, items, seen_urls
                )

        return items

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _consume_block(
        self,
        scope,
        list_url: str,
        current_section: str | None,
        items: list[ListItem],
        seen_urls: set[str],
    ) -> None:
        """Walk a single scope (a desktop-view div, or a fallback root)
        and add every fresh profile anchor to ``items``."""
        for a in scope.css("a"):
            href = (a.attributes.get("href") or "").strip()
            if not href:
                continue
            abs_url = absolutize(list_url, href)
            if not _looks_like_profile_url(abs_url):
                continue
            if abs_url in seen_urls:
                continue
            name = _clean_name(text_of(a))
            if not name:
                name = _clean_name(a.attributes.get("textvalue") or "")
            if not name or len(name) > 12:
                continue
            if name.isascii() and "@" not in name and len(name) < 4:
                continue
            title_hint = _RANK_TITLE_MAP.get(current_section or "")
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=abs_url,
                    title=title_hint,
                )
            )
            seen_urls.add(abs_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)
        # Prefer the explicit teacher template body; fall back to common
        # Sudy content containers.
        body = tree.body
        if body is None:
            return _empty_partial(list_item, profile_url)

        # Walk the card first — it gives us name / title / email /
        # research / 系 in a structured form.
        card = (
            body.css_first("div.carrer div.carrer_con")
            or body.css_first("div.carrer_con")
            or body.css_first("div.carrer")
        )
        card_fields = _parse_card(card) if card is not None else {}

        # News-box sections (个人简介 / 研究方向 / 教育经历 / ...).
        sections = _parse_news_boxes(body)
        # Limit scope_text to the teacher-content container if we can
        # find one — Sudy templates wrap everything in
        # ``div.tearchContainer`` (note the typo is intentional upstream).
        content_for_scope = (
            body.css_first("div.tearchContainer")
            or body.css_first("div.con")
            or body
        )
        scope_text = content_for_scope.text(separator=" ", strip=True) or ""

        # ---- Research interests ----------------------------------------
        research_tags: list[str] = []
        # Prefer the structured card row; fall back to the 研究方向 section
        for source in (
            card_fields.get("research"),
            sections.get("研究方向"),
            sections.get("研究领域"),
        ):
            if source:
                research_tags = _split_interests(source)
                if research_tags:
                    break

        # ---- Bio --------------------------------------------------------
        bio: str | None = None
        for key in ("个人简介", "简介", "个人介绍"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1500]
                break

        # ---- Recruit signal --------------------------------------------
        recruit_chunks: list[str] = []
        for key in ("招生招聘", "招生信息", "招生"):
            seg = sections.get(key, "").strip()
            if seg:
                recruit_chunks.append(seg)
                break
        # Body-wide scan picks up inline "招生" callouts inside the 简介.
        # Limit scope to the teacher content area (never the nav/footer).
        recruit_chunks.extend(find_recruit_paragraphs(scope_text))
        raw_quota_text = (
            "\n\n".join(_dedupe_short(recruit_chunks)[:3]) if recruit_chunks else None
        )

        # ---- Title ------------------------------------------------------
        title = list_item.title
        if not title and card_fields.get("title"):
            # Card stores the rank label (正高/副高/中级); promote to the
            # canonical 教授 / 副教授 / 讲师 wording.
            title = _RANK_TITLE_MAP.get(card_fields["title"], card_fields["title"])

        # ---- Email ------------------------------------------------------
        email: str | None = list_item.email
        email_obf = False
        if not email:
            raw_email = (card_fields.get("email") or "").strip()
            if raw_email and "@" in raw_email:
                email = raw_email.lower()
        if not email:
            # The free-text bio frequently contains the contact email
            # inline (e.g. "联系邮箱 xianqiang@seu.edu.cn").
            email, email_obf = extract_email(scope_text)
        if not email:
            # Heuristic: the URL slug usually equals the email local part
            # (verified on xfqi / huyutao / luckzpz / baoxianqiang). Only
            # use this if the slug looks like an email local part (ASCII
            # letters/digits, ≥3 chars, ≤32 chars).
            slug = _slug_from_profile(profile_url)
            if slug and re.fullmatch(r"[a-zA-Z0-9._-]{3,32}", slug):
                email = f"{slug.lower()}@seu.edu.cn"
                email_obf = True

        # ---- Phone ------------------------------------------------------
        phone = list_item.phone or (card_fields.get("phone") or None)
        if phone:
            phone = phone.strip() or None

        # ---- Photo ------------------------------------------------------
        photo = list_item.photo_url
        if photo is None:
            # The first 2 imgs are logo + banner from the template; the
            # third is the headshot (when present). Look explicitly for
            # the per-article upload path so we never accidentally pick
            # the template chrome.
            for img in body.css("img"):
                src = (img.attributes.get("src") or "").strip()
                if not src:
                    continue
                if "/_upload/article/images/" in src:
                    photo = absolutize(profile_url, src)
                    break

        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=title,
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


# ---------------------------------------------------------------------------
# Helpers — profile parsing
# ---------------------------------------------------------------------------


def _parse_card(card_node) -> dict[str, str]:
    """Parse the ``div.carrer_con`` block into a label→value dict.

    The block uses two child kinds:

      <div class="title">姓名</div>
      <div class="text"><b>label：</b><span>value</span></div>

    Some templates wrap the label/value in additional <span>'s; we just
    grab the visible text and split on the first colon.
    """
    if card_node is None:
        return {}
    out: dict[str, str] = {}

    title_node = card_node.css_first("div.title")
    if title_node is not None:
        name = _clean_name(text_of(title_node))
        if name:
            out["name"] = name

    for row in card_node.css("div.text"):
        text = text_of(row)
        if not text:
            continue
        label, sep, value = text.partition("：")
        if not sep:
            label, sep, value = text.partition(":")
        if not sep:
            continue
        norm = _normalise_label(label)
        field = _CARD_LABELS.get(norm)
        if not field:
            continue
        value = value.strip()
        if not value:
            continue
        # Don't overwrite an earlier field with a later (empty/junk) value.
        if field not in out:
            out[field] = value
    return out


def _parse_news_boxes(body_node) -> dict[str, str]:
    """Walk every ``div.news_box`` and pair its ``div.tit`` label with
    the concatenated text of the sibling ``div.con``.

    The Sudy template emits one ``news_box`` per section; the ``con``
    contains arbitrary inline tags (``<span>``, ``<br>``, ``<a>``...).
    We pull text only — no scripts / no nav.
    """
    sections: dict[str, str] = {}
    for box in body_node.css("div.news_box"):
        tit_node = box.css_first("div.tit")
        if tit_node is None:
            continue
        label_raw = text_of(tit_node)
        if not label_raw:
            continue
        # Normalise label (some are "招生招聘" or "招生招聘\xa0" or
        # "个人简介" with a trailing icon <em>).
        label = _normalise_label(label_raw)
        if label not in _PROFILE_SECTIONS:
            continue
        con_node = box.css_first("div.con")
        if con_node is None:
            continue
        # Skip the bio's hidden 招生 sub-block — sometimes the
        # 个人简介 con contains an inline recruit panel with
        # ``<div class="tit on">招生/招聘</div>`` inside; the standalone
        # ``招生招聘`` section catches that via its own news_box.
        text = con_node.text(separator=" ", strip=True) or ""
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        # Last-wins is fine because each section appears at most once
        # in the template; if it ever duplicates, keep the longer body.
        prev = sections.get(label)
        if prev is None or len(text) > len(prev):
            sections[label] = text
    return sections


def _dedupe_short(chunks: list[str]) -> list[str]:
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
