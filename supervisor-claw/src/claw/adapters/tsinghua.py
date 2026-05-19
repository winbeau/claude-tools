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


def _split_h4_sections(content_node) -> dict[str, str]:
    """Walk h4/p in document order under content_node, grouping bodies under headers.

    Tsinghua profiles wrap section titles in <h4><p>研究领域</p></h4> and section
    bodies in subsequent <p>...</p> until the next <h4>. selectolax's css('h4, p')
    does NOT preserve document order — it groups by selector — so we walk via
    traverse() and filter by tag.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    # The <h4> wraps a <p> with the same label text, which traverse yields right
    # after the h4. Skip that duplicate.
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
        if skip_next_p:
            skip_next_p = False
            if current and (n.text(strip=True) or "").strip() == current:
                continue
        if current is None:
            continue
        txt = n.text(separator=" ", strip=True)
        if txt:
            sections[current].append(txt)
    return {k: "\n".join(v) for k, v in sections.items()}


_SPLIT_RE = re.compile(r"[、，,；;/\n]+")


def _split_interests(text: str) -> list[str]:
    parts = [p.strip(" 。.；;:：") for p in _SPLIT_RE.split(text)]
    out: list[str] = []
    for p in parts:
        # tag-like: short phrase, no period/sentence-ending punctuation
        if 2 <= len(p) <= 25 and not any(c in p for c in "。！？"):
            out.append(p)
        if len(out) >= 10:
            break
    return out
