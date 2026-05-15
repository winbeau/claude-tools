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
    # Extract bio, research interests (heuristic), email confirmation,
    # recruit signal.
    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)
        full_text = tree.body.text(separator=" ", strip=True) if tree.body is not None else ""

        # research interests: pull text after "研究方向" up to next chinese section header
        ri = _extract_after_header(
            full_text,
            headers=("研究方向", "研究兴趣", "研究领域", "Research Interests"),
            terminators=(
                "教育背景", "工作经历", "学术兼职", "教育经历", "代表性成果",
                "代表论著", "代表论文", "联系方式", "Bio", "Biography",
            ),
            max_len=800,
        )
        research_tags = _split_interests(ri) if ri else []

        # email — prefer list_item.email; fall back to scanning page
        email = list_item.email
        email_obf = False
        if not email:
            email, email_obf = extract_email(full_text)

        # bio (first ~600 chars after the title section, dedup whitespace)
        bio = _first_bio_paragraph(full_text)

        # recruit signal
        recruit_chunks = find_recruit_paragraphs(full_text)
        raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None
        is_recruiting = True if recruit_chunks else None

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


def _extract_after_header(
    text: str,
    headers: tuple[str, ...],
    terminators: tuple[str, ...],
    max_len: int,
) -> str | None:
    for h in headers:
        idx = text.find(h)
        if idx < 0:
            continue
        start = idx + len(h)
        # skip a trailing colon / whitespace
        while start < len(text) and text[start] in "：: \t\n\r":
            start += 1
        end_candidates = [text.find(t, start) for t in terminators]
        end_candidates = [e for e in end_candidates if e > 0]
        end = min(end_candidates) if end_candidates else start + max_len
        end = min(end, start + max_len, len(text))
        chunk = text[start:end].strip()
        if chunk:
            return chunk
    return None


_SPLIT_RE = re.compile(r"[、，,；;/\n]+")


def _split_interests(text: str) -> list[str]:
    parts = [p.strip(" 。.；;") for p in _SPLIT_RE.split(text)]
    out: list[str] = []
    for p in parts:
        if 2 <= len(p) <= 40:
            out.append(p)
        if len(out) >= 8:
            break
    return out


def _first_bio_paragraph(text: str, limit: int = 500) -> str | None:
    # collapse whitespace
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return None
    return t[:limit]
