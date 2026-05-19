"""Tsinghua adapter.

v0.1 covers cs (计算机系). Other departments declared in schools.yaml fall through
to a generic best-effort parser; refine per-dept in later versions.
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


@register
class TsinghuaAdapter(SchoolAdapter):
    school_code = "tsinghua"
    supports = {"cs", "iiis", "air", "au-ai", "ee-ai"}

    # --- list page (CS 系 教职工名录) ---
    # Structure per advisor:
    #   <li>
    #     <div class="pic"><a href="../info/.../X.htm"><img/></a></div>
    #     <div class="text">
    #       <h2><a href="../info/.../X.htm">姓名</a></h2>
    #       <p>职称</p>
    #       <p>电话</p>
    #       <p>邮箱</p>
    #     </div>
    #   </li>
    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        tree = parse(html)
        items: list[ListItem] = []
        for li in tree.css("li"):
            text_div = li.css_first("div.text")
            if text_div is None:
                continue
            a = text_div.css_first("h2 a") or text_div.css_first("a")
            if a is None:
                continue
            href = a.attributes.get("href")
            name = text_of(a)
            if not name or not href:
                continue
            paras = [text_of(p) for p in text_div.css("p")]
            title = paras[0] if paras else None
            phone = None
            email = None
            for p in paras[1:]:
                if "@" in p:
                    email = p
                elif re.search(r"\d{6,}", p):
                    phone = p
            email_clean, _ = extract_email(email or "")
            pic_a = li.css_first("div.pic img")
            photo = pic_a.attributes.get("src") if pic_a is not None else None
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=absolutize(list_url, href),
                    title=title,
                    email=email_clean,
                    phone=phone,
                    photo_url=absolutize(list_url, photo) if photo else None,
                )
            )
        return items

    # --- profile page ---
    # Tsinghua CS profile structure:
    #   <div class="v_news_content"> contains body
    #     <h4><p>研究领域</p></h4>   -> <p>tag, tag, tag</p>
    #     <h4><p>研究概况</p></h4>   -> <p>long bio...</p>
    #     <h4><p>招生信息</p></h4>   -> <p>...</p>  (if present)
    #     ...
    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)
        # main content area; falls back to body
        content_node = tree.css_first("div.v_news_content")
        if content_node is None:
            content_node = tree.body
        sections = _split_h4_sections(content_node) if content_node is not None else {}

        research_tags: list[str] = []
        for key in ("研究方向", "研究兴趣", "研究领域", "Research Interests"):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break

        bio = None
        for key in ("研究概况", "个人简介", "简介", "Bio", "Biography"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1000]
                break

        # recruit signal — scan only main content, not nav
        scope_text = (
            content_node.text(separator=" ", strip=True)
            if content_node is not None
            else ""
        )
        recruit_chunks = find_recruit_paragraphs(scope_text)
        # also check a dedicated "招生信息" section
        for key in ("招生信息", "招生", "招生招聘"):
            if key in sections:
                recruit_chunks.insert(0, sections[key].strip())
                break
        raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None
        is_recruiting = True if recruit_chunks else None

        # email — prefer list_item.email; fall back to scope text
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


# Section labels we recognise as pseudo-headers when an advisor's page omits
# <h4> tags and uses plain <p>研究领域</p> as a section separator (李涓子-style).
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域",
    "研究概况", "个人简介", "简介", "Bio", "Biography",
    "教育背景", "工作经历", "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目",
}


def _split_h4_sections(content_node) -> dict[str, str]:
    """Group section body text by their preceding section header.

    Two header styles on tsinghua.cs:
    1. ``<h4><p>研究领域</p></h4>`` with body in following <p>'s (李国良 etc.)
    2. plain ``<p>研究领域</p>`` followed by body <p>'s (李涓子)

    We walk via traverse() (document order) and treat short <p>'s whose text
    matches a known section label as pseudo-headers.
    """
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
                # duplicate label from <h4><p>label</p></h4>
                continue
        # pseudo-header detection (李涓子 style)
        if txt and len(txt) <= 10 and txt in _PSEUDO_HEADERS:
            current = txt
            if current not in sections:
                sections[current] = []
            continue
        if current is None:
            continue
        if txt:
            sections[current].append(txt)
    return {k: "\n".join(v) for k, v in sections.items()}


_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Each paragraph in the section is either a standalone tag (李国良 style)
    or a separator-joined list (冯建华 style). Always try to split; if a
    paragraph has no separators, it falls through to a single-element split
    and is kept verbatim when tag-like."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 。.；;:：")
            if _is_tag(p):
                out.append(p)
        if len(out) >= 10:
            break
    # dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]
