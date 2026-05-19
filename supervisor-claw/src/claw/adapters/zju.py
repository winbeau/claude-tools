"""Zhejiang University adapter.

Covers 4 departments declared in schools.yaml:

- ``cs``       — 计算机科学与技术学院 (research-supervisor list ``/csen/27051``)
- ``ai-inst``  — 人工智能研究所 (full-college roster ``/csen/27003``, grouped by 所室)
- ``cadcg``    — CAD&CG 国家重点实验室 (``cad.zju.edu.cn/talent-team`` honor groups)
- ``sw``       — 软件学院 (``cst.zju.edu.cn/szdw/list.htm`` table-of-anchors)

All four list flavours share one trait: each advisor is rendered as
``<a href="https://person.zju.edu.cn/...">中文姓名</a>`` (a few sw cells point to
``mypage.zju.edu.cn``; cadcg also links to external lab pages such as
``cad.zju.edu.cn/home/X`` or personal github sites). So ``parse_list`` reduces
to "harvest faculty-profile anchors, drop nav noise, dedupe by (name, href)".
The same teacher appearing in two list pages (cs vs ai-inst, or multiple cadcg
honor groups) is fine — pipeline upserts by ``(school, name, email)``.

Profile parsing splits by host:

- ``person.zju.edu.cn`` → fixed ``tpl_1`` template — span.userBaseName / div.zc /
  li.telephone|email|address / li.yjfx > ul.second_research > li(tag).
  Bio body is AJAX-loaded into ``#tab_nav`` content panes, so static HTML has
  no bio; left empty for v0.4 Playwright to fill.
- everything else → generic h*/pseudo-header section walker reusing the
  tsinghua patterns (works for mypage.zju.edu.cn and most lab pages).
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


# Anchor text that looks like a navigation label rather than a person's name.
# Names on these list pages are always 2-5 Han chars (full-width space allowed:
# "陈　纯"); navigation entries are nouns or composite labels that happen to be
# in the same length range, so we keep an explicit blocklist.
_NAV_NOISE: set[str] = {
    "师资队伍", "教师名录", "研究生导师", "计算机科学与技术", "软件工程",
    "数字化艺术与设计", "电子服务", "人工智能研究所", "计算机软件研究所",
    "机关", "学院领导", "师资概况", "兼职教授", "导师团队", "专业领域",
    "院士", "教授", "副教授", "研究员", "讲师",
    "中文", "English", "ENGLISH", "首页", "返回", "下一页", "上一页",
    "尾页", "第一页", "登录", "退出", "管理主页", "换一批", "点击查看",
    "Menu", "Search",
}


def _is_chinese_name(text: str) -> bool:
    """A 2-5 Han-char string, not in the nav blocklist.

    Strips U+3000 (full-width space) since zju pages center-pad two-char names
    as "陈　纯".
    """
    raw = (text or "").strip()
    if not raw or raw in _NAV_NOISE:
        return False
    compact = raw.replace("　", "").replace(" ", "")
    if not (2 <= len(compact) <= 5):
        return False
    if compact in _NAV_NOISE:
        return False
    return all("一" <= c <= "鿿" for c in compact)


def _normalize_name(text: str) -> str:
    return (text or "").replace("　", "").strip()


def _is_faculty_profile_href(href: str, list_url: str) -> bool:
    """A href is a teacher profile if it links to one of zju's personal-page
    hosts, or (for the cadcg lab) to its own ``/home/X`` personal-page space.
    """
    if not href:
        return False
    h = href.strip()
    if not h or h.startswith("#") or h.lower().startswith("javascript:"):
        return False
    abs_url = absolutize(list_url, h)
    host = (urlparse(abs_url).hostname or "").lower()
    path = (urlparse(abs_url).path or "").lower()
    if host in ("person.zju.edu.cn", "mypage.zju.edu.cn"):
        return True
    if host == "www.cad.zju.edu.cn" and path.startswith("/home/"):
        return True
    # external personal sites are linked from cadcg too — only accept when
    # the cardinal "host/home/<slug>" pattern matches some lab convention.
    if path.startswith("/home/") and host.endswith("zju.edu.cn"):
        return True
    return False


@register
class ZjuAdapter(SchoolAdapter):
    school_code = "zju"
    supports = {"cs", "cadcg", "ai-inst", "sw"}

    # ---------------- list page ----------------
    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        tree = parse(html)
        items: list[ListItem] = []
        seen: set[tuple[str, str]] = set()

        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            raw_text = text_of(a)
            if not raw_text or not _is_chinese_name(raw_text):
                continue
            if not _is_faculty_profile_href(href, list_url):
                continue
            name = _normalize_name(raw_text)
            profile_url = absolutize(list_url, href.strip())
            key = (name, profile_url)
            if key in seen:
                continue
            seen.add(key)
            items.append(ListItem(name_cn=name, profile_url=profile_url))
        return items

    # ---------------- profile page ----------------
    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        host = (urlparse(profile_url).hostname or "").lower()
        if host == "person.zju.edu.cn":
            return self._parse_person_zju(html, profile_url, list_item)
        return self._parse_generic(html, profile_url, list_item)

    # person.zju.edu.cn — fixed tpl_1 template
    def _parse_person_zju(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)

        name_node = tree.css_first("span.userBaseName")
        name = text_of(name_node) or list_item.name_cn

        title_parts: list[str] = []
        zc = tree.css_first("div.zc")
        if zc is not None:
            for sp in zc.css("span"):
                t = text_of(sp)
                if t and t != "|":
                    title_parts.append(t)
        title = " / ".join(title_parts) if title_parts else list_item.title

        phone = _strip_label(_li_text(tree, "telephone"), ("电话",)) or list_item.phone
        email_raw = _strip_label(_li_text(tree, "email"), ("邮箱", "Email", "E-mail"))
        email, email_obf = extract_email(email_raw or "")
        # No body-wide fallback: the footer carries a generic xwmaster@zju.edu.cn
        # that would otherwise leak into every advisor.
        if not email:
            email = list_item.email

        photo_url: str | None = list_item.photo_url
        photo_node = tree.css_first("div.personal_img img")
        if photo_node is not None:
            src = photo_node.attributes.get("src")
            if src:
                photo_url = absolutize(profile_url, src)

        research_tags: list[str] = []
        for li in tree.css("li.yjfx ul.second_research li"):
            t = text_of(li).lstrip("·•· ").strip()
            if _is_tag(t):
                research_tags.append(t)
        # dedupe preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for t in research_tags:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        research_tags = deduped[:10]

        # person.zju.edu.cn loads 个人简介 / 教学 / 研究 panes via columnData()
        # AJAX — static HTML carries no bio; leave None for v0.4 Playwright.
        bio = None

        # Recruit signal is unreliable on this template (no dedicated section);
        # skip to avoid false positives from generic site chrome.
        raw_quota_text: str | None = None
        is_recruiting: bool | None = None

        return AdvisorPartial(
            name_cn=name,
            title=title,
            email=email,
            email_obfuscated=bool(email_obf),
            phone=phone,
            photo_url=photo_url,
            homepage=profile_url,
            bio_text=bio,
            research_interests=research_tags,
            raw_quota_text=raw_quota_text,
            is_recruiting=is_recruiting,
            source_url=profile_url,
        )

    # mypage.zju.edu.cn, cad.zju.edu.cn/home/X, external personal sites
    def _parse_generic(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        tree = parse(html)
        content = (
            tree.css_first("div.v_news_content")
            or tree.css_first("div.main")
            or tree.css_first("div#main")
            or tree.css_first("main")
            or tree.css_first("div.content")
            or tree.body
        )
        scope_text = (
            content.text(separator=" ", strip=True) if content is not None else ""
        )

        sections = _split_sections(content) if content is not None else {}

        research_tags: list[str] = []
        for key in ("研究方向", "研究兴趣", "研究领域", "Research Interests"):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break

        bio: str | None = None
        for key in ("研究概况", "个人简介", "简介", "Bio", "Biography", "About"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1000]
                break

        recruit_chunks = find_recruit_paragraphs(scope_text)
        for key in ("招生信息", "招生", "招生招聘"):
            if key in sections:
                recruit_chunks.insert(0, sections[key].strip())
                break
        raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None
        is_recruiting = True if recruit_chunks else None

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


# ---------------- helpers ----------------

_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "Research Interests",
    "研究概况", "个人简介", "简介", "Bio", "Biography", "About",
    "教育背景", "工作经历", "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目",
}


def _li_text(tree, klass: str) -> str | None:
    node = tree.css_first(f"li.{klass}")
    if node is None:
        return None
    return text_of(node) or None


_LABEL_STRIP_RE = re.compile(r"^[\s:：]+")


def _strip_label(text: str | None, labels: tuple[str, ...]) -> str | None:
    if not text:
        return text
    out = text
    for label in labels:
        if out.startswith(label):
            out = out[len(label):]
            break
    return _LABEL_STRIP_RE.sub("", out).strip() or None


def _split_sections(content_node) -> dict[str, str]:
    """Walk in document order, group text by preceding pseudo-header.

    Mirrors the tsinghua heuristic (see ``adapters/tsinghua.py``): accept both
    ``<h*>label</h*>`` and bare ``<p>label[：]</p>`` as section dividers when
    the label matches ``_PSEUDO_HEADERS``.
    """
    if content_node is None:
        return {}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    skip_next_p = False
    for n in content_node.traverse(include_text=False):
        tag = n.tag
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            label = (n.text(strip=True) or "").strip().rstrip("：:").strip()
            if label and len(label) <= 12 and label in _PSEUDO_HEADERS:
                current = label
                if current not in sections:
                    sections[current] = []
                skip_next_p = True
            continue
        if tag != "p":
            continue
        txt = (n.text(strip=True) or "").strip()
        if skip_next_p:
            skip_next_p = False
            if current and txt == current:
                continue
        norm = txt.rstrip("：:").strip()
        if norm and len(norm) <= 12 and norm in _PSEUDO_HEADERS:
            current = norm
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
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 。.；;:：·•")
        if not line:
            continue
        for part in _SPLIT_RE.split(line):
            part = part.strip(" 。.；;:：·•")
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
