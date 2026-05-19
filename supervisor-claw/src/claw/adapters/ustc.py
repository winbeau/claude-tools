"""USTC (中国科学技术大学) adapter.

Two departments are covered, each with its own page template family:

* ``cs``      — 计算机科学与技术学院 (cs.ustc.edu.cn) — list pages render via
  ``ul.news_list > li.news`` rows that carry name / email / research-direction
  inline. Note the class names are *swapped*: ``div.news_tel`` actually holds
  the email, ``div.news_email`` holds one research-direction tag per element.

* ``ai-info`` — 电子工程与信息科学系 (eeis.ustc.edu.cn) — list pages render via
  ``div.card`` blocks; only name + a single research-direction string are on
  the list page. Photo URLs are written into the DOM via inline
  ``<script>document.write(...)</script>`` using a ``defpic`` variable.

Profile pages also come in two flavours:

* cs.ustc — main content lives in ``#wp_articlecontent``; section markers are
  inline pseudo-headers like ``主要研究方向 ：`` and ``招生信息 ：``.
* eeis    — main content lives in ``article.blog-details``; newer pages use
  proper ``<h2>`` section headers (``个人简介`` / ``研究方向`` / ``联系方式`` /
  ``所获荣誉`` / ``代表性论文``). Older pages frequently have an *empty*
  ``div.content``; we degrade gracefully and emit just the list-page fields.
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


# ---------------------------------------------------------------------------
# helpers shared by both templates
# ---------------------------------------------------------------------------

_DEFPIC_RE = re.compile(r"""defpic\s*=\s*["']([^"']+)["']""")
_TITLE_FROM_URL = (
    # cs.ustc.edu.cn
    ("/js_23235/", "教授"),
    ("/fjs_23239/", "副教授"),
    ("/trjs/", "特任教授"),
    ("/trfyjy/", "特任副研究员"),
    ("/ys_23622/", "院士"),
    # eeis.ustc.edu.cn
    ("/2648/", "教授"),
    ("/2615/", "副教授"),
    ("/2593/", "讲师"),
)


def _title_from_list_url(list_url: str) -> str | None:
    for needle, title in _TITLE_FROM_URL:
        if needle in list_url:
            return title
    return None


_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Split a free-form research-direction paragraph into tag-shaped pieces.

    Two layouts in USTC pages:

    * cs.ustc — single comma-separated line followed by bio paragraphs in the
      same section body (inline pseudo-headers don't bound bio cleanly).
      We must NOT bleed bio fragments in.
    * eeis    — newer pages put one phrase per ``<p>``; ``research_interests``
      should be one tag per line, *not* split on internal 、 (which appears
      inside phrases like "感知、决策与交互机制建模").

    Strategy:
      1. Walk lines top-down.
      2. If the whole line is tag-shaped (eeis paragraph), keep it as one tag.
      3. Else split by Chinese/Western separators; if ≥2 pieces are tag-shaped
         (cs.ustc comma list), keep them all and **stop** — bio follows.
      4. A line that yields zero tags ends the run.
    """
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 。.；;:：")
        if not line:
            continue
        # Case A: line is itself a tag-shaped phrase (eeis 1-per-paragraph)
        if _is_tag(line):
            if line not in out:
                out.append(line)
            if len(out) >= 8:
                break
            continue
        # Case B: separator-joined list (cs.ustc style)
        parts = [p.strip(" 。.；;:：") for p in _SPLIT_RE.split(line)]
        valid = [p for p in parts if _is_tag(p)]
        if len(valid) >= 2:
            for t in valid:
                if t not in out:
                    out.append(t)
            break  # bio follows the comma-list line — don't keep walking
        # Case C: nothing useful on this line — stop (avoids bio bleed)
        break
    return out[:10]


_HOMEPAGE_RE = re.compile(r"https?://(?:staff|faculty|home)\.ustc\.edu\.cn/[^\s'\"<>]+")


def _find_external_homepage(text: str) -> str | None:
    m = _HOMEPAGE_RE.search(text or "")
    return m.group(0).rstrip(" .,;。，；") if m else None


# ---------------------------------------------------------------------------
# adapter
# ---------------------------------------------------------------------------


@register
class UstcAdapter(SchoolAdapter):
    school_code = "ustc"
    supports = {"cs", "ai-info"}

    # -- list pages ---------------------------------------------------------
    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        if "eeis.ustc.edu.cn" in list_url:
            return self._parse_list_eeis(html, list_url)
        # default: cs.ustc.edu.cn template (also any non-eeis ustc page)
        return self._parse_list_cs(html, list_url)

    def _parse_list_cs(self, html: str, list_url: str) -> list[ListItem]:
        tree = parse(html)
        title_from_url = _title_from_list_url(list_url)
        items: list[ListItem] = []
        for li in tree.css("ul.news_list > li.news, ul.news_list li.news"):
            title_a = li.css_first("div.news_title a")
            if title_a is None:
                title_a = li.css_first("div.news_images a")
            if title_a is None:
                continue
            href = title_a.attributes.get("href")
            name = text_of(title_a)
            # name often contains internal whitespace e.g. "安  虹"
            name = re.sub(r"\s+", "", name) if name else ""
            if not name or not href:
                continue
            # `.news_tel` carries email despite the class name
            email_node = li.css_first("div.news_tel")
            email_raw = text_of(email_node)
            email_clean, _ = extract_email(email_raw) if email_raw else (None, False)
            # photo (may live in a <script> defpic= variable on a few rows)
            photo_url: str | None = None
            img = li.css_first("div.news_images img")
            if img is not None:
                photo_url = img.attributes.get("src")
            if not photo_url:
                script_node = li.css_first("div.news_images script")
                if script_node is not None:
                    m = _DEFPIC_RE.search(script_node.text() or "")
                    if m:
                        photo_url = m.group(1)
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=absolutize(list_url, href),
                    title=title_from_url,
                    email=email_clean,
                    phone=None,
                    photo_url=absolutize(list_url, photo_url) if photo_url else None,
                )
            )
        return items

    def _parse_list_eeis(self, html: str, list_url: str) -> list[ListItem]:
        tree = parse(html)
        title_from_url = _title_from_list_url(list_url)
        items: list[ListItem] = []
        for card in tree.css("div.card"):
            # ignore sidebar / recent-posts cards; faculty cards have an h5
            # title and a research-direction <p class="card-text">.
            name_a = card.css_first("h5.card-title a")
            if name_a is None:
                continue
            if card.css_first("p.card-text") is None:
                continue
            href = name_a.attributes.get("href")
            name = text_of(name_a)
            if not name or not href:
                continue
            # photo lives inside a <script> defpic= block
            photo_url: str | None = None
            script_node = card.css_first("div.cardimg_staff script")
            if script_node is None:
                script_node = card.css_first("div.cardimg script")
            if script_node is not None:
                m = _DEFPIC_RE.search(script_node.text() or "")
                if m:
                    photo_url = m.group(1)
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=absolutize(list_url, href),
                    title=title_from_url,
                    email=None,
                    phone=None,
                    photo_url=absolutize(list_url, photo_url) if photo_url else None,
                )
            )
        return items

    # -- profile pages ------------------------------------------------------
    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        if "eeis.ustc.edu.cn" in profile_url:
            return self._parse_profile_eeis(html, profile_url, list_item)
        return self._parse_profile_cs(html, profile_url, list_item)

    # ---- cs.ustc profile -------------------------------------------------
    def _parse_profile_cs(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)
        content_node = tree.css_first("#wp_articlecontent")
        if content_node is None:
            content_node = tree.css_first("div.wp_articlecontent")
        if content_node is None:
            content_node = tree.body
        scope_text = (
            content_node.text(separator="\n", strip=True) if content_node is not None else ""
        )

        sections = _split_inline_sections(scope_text)

        research_tags: list[str] = []
        for key in (
            "主要研究方向",
            "研究方向",
            "研究兴趣",
            "研究领域",
            "Research Interests",
        ):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break

        bio: str | None = None
        for key in (
            "个人简介",
            "简介",
            "研究概况",
            "科研工作",
            "Biography",
            "Bio",
        ):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1000]
                break

        recruit_chunks: list[str] = []
        for key in ("招生信息", "招生", "招生招聘", "招收博士生"):
            if key in sections and sections[key].strip():
                recruit_chunks.append(sections[key].strip())
                break
        recruit_chunks.extend(find_recruit_paragraphs(scope_text))
        # dedupe by 60-char prefix
        seen: set[str] = set()
        deduped: list[str] = []
        for c in recruit_chunks:
            k = c[:60]
            if k in seen:
                continue
            seen.add(k)
            deduped.append(c)
        raw_quota_text = "\n\n".join(deduped[:3]) if deduped else None
        is_recruiting = True if deduped else None

        email = list_item.email
        email_obf = False
        if not email:
            email, email_obf = extract_email(scope_text)
        # filter out group/lab placeholder mail (e.g. recruiter contact)
        if email and email.startswith("mwave@"):
            email = None

        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=email,
            email_obfuscated=email_obf,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=_find_external_homepage(scope_text) or profile_url,
            bio_text=bio,
            research_interests=research_tags,
            raw_quota_text=raw_quota_text,
            is_recruiting=is_recruiting,
            source_url=profile_url,
        )

    # ---- eeis profile ----------------------------------------------------
    def _parse_profile_eeis(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)
        article = tree.css_first("article.blog-details")
        if article is None:
            article = tree.body
        # strip the sidebar/recent-posts area which lives outside the article,
        # so scope_text only reflects this advisor's content.
        scope_text = (
            article.text(separator="\n", strip=True) if article is not None else ""
        )

        # build h2/h3-keyed sections within the article (newer eeis layout)
        sections: dict[str, list[str]] = {}
        current: str | None = None
        if article is not None:
            for n in article.traverse(include_text=False):
                if n.tag in {"h2", "h3", "h4"}:
                    label = (n.text(strip=True) or "").rstrip("：:").strip()
                    if label and label != list_item.name_cn:
                        current = label
                        sections.setdefault(current, [])
                    continue
                if n.tag != "p":
                    continue
                txt = (n.text(strip=True) or "").strip()
                if not txt or current is None:
                    continue
                sections[current].append(txt)
        merged = {k: "\n".join(v) for k, v in sections.items()}

        research_tags: list[str] = []
        for key in (
            "研究方向",
            "研究兴趣",
            "研究领域",
            "主要研究方向",
            "Research Interests",
        ):
            if key in merged:
                research_tags = _split_interests(merged[key])
                if research_tags:
                    break
        # list-page card sometimes has a single research label — keep it as a
        # fallback when the profile body is empty.
        if not research_tags and list_item.title is None and not merged:
            pass  # nothing to add
        # if still empty, fall back to inline split on whole article text
        if not research_tags:
            inline = _split_inline_sections(scope_text)
            for key in ("研究方向", "主要研究方向", "研究兴趣", "研究领域"):
                if key in inline:
                    research_tags = _split_interests(inline[key])
                    if research_tags:
                        break

        bio: str | None = None
        for key in ("个人简介", "简介", "研究概况", "Biography", "Bio"):
            if key in merged and merged[key].strip():
                bio = merged[key].strip()[:1000]
                break
        if not bio:
            inline = _split_inline_sections(scope_text)
            for key in ("个人简介", "简介", "研究概况"):
                if key in inline and inline[key].strip():
                    bio = inline[key].strip()[:1000]
                    break

        # recruit signal — only scan within the article scope to avoid the
        # sidebar's "最新内容" leakage.
        recruit_chunks = find_recruit_paragraphs(scope_text)
        raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None
        is_recruiting = True if recruit_chunks else None

        email = list_item.email
        email_obf = False
        if not email:
            email, email_obf = extract_email(scope_text)
        # eeis sidebar carries a department contact (mwave@ustc.edu.cn) — not
        # a per-advisor address.
        if email and email.startswith("mwave@"):
            email = None

        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=email,
            email_obfuscated=email_obf,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=_find_external_homepage(scope_text) or profile_url,
            bio_text=bio,
            research_interests=research_tags,
            raw_quota_text=raw_quota_text,
            is_recruiting=is_recruiting,
            source_url=profile_url,
        )


# ---------------------------------------------------------------------------
# inline pseudo-header section splitter
# ---------------------------------------------------------------------------

# cs.ustc profiles encode sections as inline text "主要研究方向 ： <list>" inside
# a flat paragraph stream, rather than as <h4> blocks. We slice the text on
# any known label followed by a colon.

_INLINE_LABELS = (
    "主要研究方向",
    "研究方向",
    "研究兴趣",
    "研究领域",
    "Research Interests",
    "个人简介",
    "简介",
    "研究概况",
    "科研工作",
    "教学工作",
    "招生信息",
    "招生招聘",
    "招生",
    "招收博士生",
    "教育背景",
    "工作经历",
    "学术兼职",
    "联系方式",
    "Biography",
    "Bio",
    "所获荣誉",
    "代表性论文",
    "代表性成果",
)


def _split_inline_sections(text: str) -> dict[str, str]:
    """Slice a flat text blob into label -> body using known label markers.

    A label is recognised when it appears at a line start (after we normalise
    whitespace) and is immediately followed by a colon (``：`` or ``:``),
    optionally with surrounding spaces. The body extends until the next
    recognised label.
    """
    if not text:
        return {}
    # normalise: collapse repeated whitespace into a single space within lines
    norm = re.sub(r"[ \t　]+", " ", text)
    # build alternation pattern; sort by length DESC so '主要研究方向' wins over '研究方向'
    labels_sorted = sorted(set(_INLINE_LABELS), key=len, reverse=True)
    alt = "|".join(re.escape(lbl) for lbl in labels_sorted)
    label_re = re.compile(rf"(?:^|\n|\s)({alt})\s*[：:]")
    matches = list(label_re.finditer(norm))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        label = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(norm)
        body = norm[start:end].strip(" \n\t,;。.：:")
        if body and label not in out:
            out[label] = body
    return out
