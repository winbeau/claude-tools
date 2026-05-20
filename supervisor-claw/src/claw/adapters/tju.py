"""Tianjin University adapter (天津大学 智能与计算学部).

v0.4 covers ONE consolidated unit: the 智能与计算学部 (College of Intelligence
and Computing, CIC), founded in 2018, which absorbed Tianjin's computer
science, software, AI, and cyber-security faculty under a single college
roof.  The school's public faculty roster lives at ``cic.tju.edu.cn`` and
is exposed under two equivalent indexes:

* ``/szdw/szmd/azcjs.htm`` —— "按职称检索" (group by job-title)
* ``/szdw/szmd/azmjs.htm`` —— "按字母检索" (group by name initial)

Both indexes render every advisor as a single ``<li><a href="../../info/
<bucket>/<id>.htm" title="姓名">姓名</a></li>`` inside one of several
``<ul class="ul_jiaoshi">`` blocks.  Each ``<ul>`` is preceded by an
``<h3 class="h3_jiaoshou">职称名</h3>`` header (e.g. 教授 / 副教授 / 讲师 /
研究员 / 专任助理研究员 / 工程师) on the by-title index, or by a one-letter
``<h3>`` (A / B / C / ...) on the by-name index.  The two indexes carry
the same person set — we register both as ``list_urls`` so the pipeline
can fall back on whichever the user can reach; the (school, name_cn,
email) upsert dedupe handles overlap.

Profile pages are pleasantly uniform: a single ``div.v_news_content``
holds ten labelled "字段：值" lines covering 姓名 / 职称 / 所在系别 /
主讲课程 / 导师类型 / 电子邮件 / 研究领域 / 研究方向 / 个人主页.  The
labels and values sit on the same DOM line OR get split across two
DOM lines depending on which sub-department's template was used:

* compact: ``"姓名：刘安安"``  (single line)
* split:   ``"姓名"``  ``"：刘安安"``  or  ``"电子邮件"`` ``"："`` ``"x@y"``

We normalise by walking the flat line stream and pairing a label line
with the following value line (or the suffix-after-colon on the same
line).  This is the same trick the ai.nankai adapter uses.

Single dept_code
----------------
The launch_tju.md spec is explicit: even though CIC formally houses
计算机学院 / 人工智能学院 / 软件学院 / 网络空间安全 / 数学（应用方向），
we keep ``dept_code='ic'`` for the whole roster.  The 所在系别 field
on each profile lets v0.4+ enrich-time enrichers re-tag advisors with
finer-grained appointments without us having to multi-route in v1.

Known oddities
--------------
1. **One person can appear twice** when scraped from both list_urls
   (by-title + by-name).  Profile URLs are identical so we dedupe on
   ``profile_url`` inside ``parse_list``; cross-list dedupe is the
   pipeline's responsibility.
2. **JS click-counter** —— each ``<ul>`` ends with an inline
   ``<script>_showDynClickBatch(...)</script>``.  Selectolax pulls the
   script text into ``ul.text()``, so we MUST select ``ul > li > a``
   and never read raw ul text.
3. **Email line splits** —— roughly a third of profiles render
   ``电子邮件：\n x@tju.edu.cn`` (label and value on separate lines).
   The line-walker glues them back together.
4. **Title precedence** —— 讲席教授 / 英才教授 / 杰出教授 outrank plain
   教授; we honour the header text verbatim (h3 says "教授" so a 讲席
   教授 will be tagged "教授" from the list, then upgraded from the
   profile's ``职称`` field in parse_profile).
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


# Title keywords used to gate faculty entries (broad whitelist).
_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "英才", "杰出", "院士",
    "Professor", "Lecturer", "Researcher",
)


_PSEUDO_HEADERS: set[str] = {
    "基本信息", "研究方向", "研究兴趣", "研究领域", "主要研究方向",
    "Research Interests",
    "个人简介", "简介", "Bio", "Biography",
    "教育背景", "教育经历", "工作经历", "工作履历", "学术经历",
    "学术兼职", "社会兼职", "任职", "学术任职", "学术服务",
    "招生信息", "招生", "招生说明", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果",
    "奖励与荣誉", "荣誉", "获奖",
    "科研项目", "项目",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Split a comma/dunhao-separated research direction string into tags.

    Same shape as the tsinghua / pku / nankai adapters' splitter — line
    first, then in-line separators, filter by tag-shape predicate, dedupe.
    """
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 　。.；;:：等以及和与")
            if _is_tag(p):
                out.append(p)
        if len(out) >= 12:
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
        # Profile-page parse will fill it in; never drop on missing title.
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# List parser
# ---------------------------------------------------------------------------


# Section headers we recognise on the by-title index (we use these to map
# the <h3> a teacher sits under into a list-item title).  Headers from the
# by-name index are single letters A-Z and intentionally fall through —
# those entries land with title=None and let parse_profile fill it.
_TITLE_SECTIONS: set[str] = {
    "教授", "副教授", "助理教授", "讲师", "助教",
    "研究员", "副研究员", "助理研究员", "专任助理研究员",
    "工程师", "高级工程师", "助理工程师",
}


def _name_looks_real(name: str) -> bool:
    """Cheap sanity check — drop pagination text, nav crumbs, JS leakage."""
    if not name:
        return False
    if len(name) < 2 or len(name) > 20:
        return False
    # Every faculty name should have at least one CJK character.
    if not any("一" <= c <= "鿿" for c in name):
        return False
    # Drop anything that looks like JS or punctuation.
    if any(c in name for c in "()[]{}<>,.;:'\"\\/"):
        return False
    return True


def _parse_list_cic(html: str, list_url: str) -> list[ListItem]:
    """Parse a cic.tju.edu.cn faculty index page.

    Both indexes (by-title and by-name) share the same DOM shape:

        <div class="con_list_body">
          <h3 class="h3_jiaoshou">教授</h3>
          <ul class="ul_jiaoshi">
            <li><a href="../../info/1079/5936.htm" title="刘安安">刘安安</a></li>
            <li><a href="../../info/1071/4834.htm" title="毕重科">毕重科</a></li>
            ...
            <script>_showDynClickBatch([...])</script>
          </ul>
          <h3 class="h3_jiaoshou">副教授</h3>
          <ul class="ul_jiaoshi"> ... </ul>
          ...
        </div>

    We walk the children of ``div.con_list_body`` in document order so
    each ``<ul>`` inherits the most recent ``<h3>`` text as its title
    hint.  When that header is a single letter (by-name index) we
    intentionally leave the list-item title as None.
    """
    tree = parse(html)
    container = (
        tree.css_first("div.con_list_body")
        or tree.css_first("div.con_list")
        or tree.css_first("div.con_ny_r")
        or tree.body
    )
    if container is None:
        return []

    items: list[ListItem] = []
    seen_urls: set[str] = set()
    current_title: str | None = None

    for child in container.iter():
        tag = child.tag
        if tag == "h3":
            txt = text_of(child).strip()
            # Only adopt h3 text as a title when it actually matches a
            # known faculty title bucket — by-name letters (A/B/C/...)
            # fall through and leave current_title untouched.
            if txt in _TITLE_SECTIONS:
                current_title = txt
            elif txt and len(txt) == 1 and txt.isalpha():
                # by-name letter group — title comes from profile later.
                current_title = None
            # else: keep previous (could be a layout-only h3).
        elif tag == "ul":
            cls = (child.attributes.get("class") or "")
            if "ul_jiaoshi" not in cls.split():
                continue
            for a in child.css("li > a"):
                href = (a.attributes.get("href") or "").strip()
                if not href or "info/" not in href:
                    continue
                name = (
                    text_of(a)
                    or (a.attributes.get("title") or "").strip()
                )
                # selectolax sometimes folds the trailing <script> click
                # counter into the last <a>'s sibling text — keep the
                # name strictly equal to the <a> content's leading run.
                name = name.split("_showDynClickBatch")[0].strip()
                if not _name_looks_real(name):
                    continue
                title = current_title if _looks_like_faculty(current_title) else None
                # Even if current_title is None (by-name index) we still
                # keep the entry — parse_profile will fill 职称.
                absurl = absolutize(list_url, href)
                if absurl in seen_urls:
                    continue
                seen_urls.add(absurl)
                items.append(
                    ListItem(
                        name_cn=name,
                        profile_url=absurl,
                        title=title,
                    )
                )
    return items


# ---------------------------------------------------------------------------
# Profile parser
# ---------------------------------------------------------------------------


# Canonical labels we recognise as kv-fields in profile pages.
_FIELD_LABELS: tuple[str, ...] = (
    "姓名", "性别", "职称", "所在系别", "所属院系", "院系",
    "主讲课程", "导师类型",
    "电子邮件", "邮箱", "E-mail", "Email",
    "办公电话", "电话",
    "研究领域", "研究方向", "研究兴趣",
    "个人主页", "主页",
    "学历", "学位",
)


def _walk_lines(text: str) -> list[str]:
    """Normalise a flattened text blob into a clean list of non-empty lines."""
    out: list[str] = []
    for raw in text.split("\n"):
        ln = raw.strip()
        if ln:
            out.append(ln)
    return out


def _pair_split_labels(lines: list[str]) -> list[str]:
    """Glue back labels that got split from their values across two lines.

    Examples from cic.tju.edu.cn profiles::

        ['姓名', '：刘安安', '职称', '：讲席教授', ...]
        ['电子邮件', '：', 'anan0422@gmail.com', ...]
        ['个人主页', '：', 'http://seea.tju.edu.cn/...']

    Output (for the first sample)::

        ['姓名：刘安安', '职称：讲席教授', ...]
    """
    label_set = set(_FIELD_LABELS)
    glued: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        # Trim any whitespace around colons so the check below is robust.
        bare = ln.rstrip(" 　:：")
        if bare in label_set and i + 1 < len(lines):
            nxt = lines[i + 1]
            # Drop a leading colon from the value if any.
            value = nxt.lstrip(" 　:：").strip()
            if not value and i + 2 < len(lines):
                # "电子邮件" / "：" / "x@y" pattern.
                value = lines[i + 2].lstrip(" 　:：").strip()
                glued.append(f"{bare}：{value}")
                i += 3
                continue
            # If the "next" line is itself a known label, the current
            # line really had no value — emit "label：" and move on.
            if nxt.rstrip(" 　:：") in label_set:
                glued.append(f"{bare}：")
                i += 1
                continue
            glued.append(f"{bare}：{value}")
            i += 2
            continue
        glued.append(ln)
        i += 1
    return glued


_FIELD_RE = re.compile(
    r"^(姓\s*名|性\s*别|职\s*称|所在系别|所属院系|院\s*系|主讲课程|导师类型|"
    r"电子邮件|邮\s*箱|E-?mail|"
    r"办公电话|电\s*话|"
    r"研究领域|研究方向|研究兴趣|"
    r"个人主页|主\s*页|"
    r"学\s*历|学\s*位)"
    r"\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)


def _build_kv_sections(lines: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Walk lines and split into (kv, sections).

    * ``kv``        — single-line "label：value" fields
    * ``sections``  — multi-line content keyed by pseudo-headers (招生信息 /
                      个人简介 / 教育背景 / ...).
    """
    kv: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in lines:
        # Pseudo-header? Strip trailing colon before lookup.
        norm = line.rstrip("：:").strip()
        if 1 < len(norm) <= 18 and norm in _PSEUDO_HEADERS:
            current = norm
            sections.setdefault(current, [])
            continue
        m = _FIELD_RE.match(line)
        if m:
            label = re.sub(r"\s+", "", m.group(1))
            value = m.group(2).strip()
            # Canonicalise label aliases.
            if label == "姓名":
                kv.setdefault("姓名", value)
            elif label == "职称":
                kv.setdefault("职称", value)
            elif label in ("所在系别", "所属院系", "院系"):
                kv.setdefault("所在系别", value)
            elif label == "主讲课程":
                kv.setdefault("主讲课程", value)
            elif label == "导师类型":
                kv.setdefault("导师类型", value)
            elif label in ("电子邮件", "邮箱") or label.lower() in ("e-mail", "email"):
                kv.setdefault("电子邮件", value)
            elif label in ("办公电话", "电话"):
                kv.setdefault("电话", value)
            elif label in ("研究领域",):
                kv.setdefault("研究领域", value)
            elif label in ("研究方向", "研究兴趣"):
                kv.setdefault("研究方向", value)
            elif label in ("个人主页", "主页"):
                kv.setdefault("个人主页", value)
            else:
                kv.setdefault(label, value)
            continue
        if current is not None:
            sections[current].append(line)
    return kv, {k: "\n".join(v).strip() for k, v in sections.items() if v}


def _parse_profile_cic(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """Parse a cic.tju.edu.cn profile page.

    Profiles live at ``info/<bucket>/<id>.htm`` and render a single
    ``div.v_news_content`` containing ten or so ``"label：value"`` lines.
    Some templates split label / value across two DOM lines —
    ``_pair_split_labels`` re-glues them.
    """
    tree = parse(html)
    content = (
        tree.css_first("div.v_news_content")
        or tree.css_first("div.wp_articlecontent")
        or tree.css_first("div.con_news_body")
        or tree.css_first("div.con_ny_r")
        or tree.body
    )
    if content is None:
        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=list_item.email,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=profile_url,
            source_url=profile_url,
        )

    scope_text = content.text(separator=" ", strip=True)
    lines = _pair_split_labels(_walk_lines(content.text(separator="\n", strip=True)))
    kv, sections = _build_kv_sections(lines)

    # Title — prefer profile-page 职称 over list-page header (so 讲席教授 wins
    # over the generic 教授 bucket label).
    title = kv.get("职称") or list_item.title

    # Email — list-page email first (cic list pages don't carry one, but
    # keep the contract uniform with other adapters); then kv; then a
    # body-wide regex.
    email = list_item.email
    email_obf = False
    if not email:
        v = kv.get("电子邮件")
        if v:
            em, was_obf = extract_email(v)
            if em:
                email = em
                email_obf = was_obf
    if not email:
        em2, obf2 = extract_email(scope_text)
        if em2:
            email = em2
            email_obf = obf2

    phone_raw = list_item.phone or kv.get("电话")
    phone = phone_raw.strip() if phone_raw else None
    if phone == "":
        phone = None

    homepage_raw = (kv.get("个人主页") or "").strip()
    homepage = (
        homepage_raw
        if homepage_raw.startswith(("http://", "https://"))
        else profile_url
    )

    # Research tags — prefer 研究方向 (specific) over 研究领域 (broader theme
    # like "人工智能").  Fall back to whichever we have.
    research_tags: list[str] = []
    for key in ("研究方向", "研究兴趣"):
        if kv.get(key):
            research_tags = _split_interests(kv[key])
            if research_tags:
                break
    if not research_tags and kv.get("研究领域"):
        research_tags = _split_interests(kv["研究领域"])
    # Section fallback (e.g. profiles with explicit "研究方向" section header).
    if not research_tags:
        for key in ("研究方向", "研究兴趣", "研究领域"):
            if key in sections and sections[key].strip():
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break

    # Bio — pull a 个人简介 / 基本信息 section if any; cic profiles rarely
    # have one in v0.4 (mostly kv only), so we fall back to the labelled-
    # field aggregate so downstream LLM enrichment has SOMETHING to chew on.
    bio: str | None = None
    for key in ("个人简介", "简介", "基本信息", "Bio", "Biography",
                "教育经历", "工作经历"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break
    if bio is None:
        # Compose a synthetic bio from the labelled fields so downstream
        # enrichment has context.  Skip empty / link-only fields.
        bio_parts: list[str] = []
        for label in ("所在系别", "导师类型", "主讲课程", "研究领域", "研究方向"):
            v = kv.get(label)
            if v and v.strip():
                bio_parts.append(f"{label}：{v.strip()}")
        if bio_parts:
            bio = "\n".join(bio_parts)[:1500]

    # Recruitment signals — only scan the profile container, never the body
    # (avoids nav-bar "招生信息" false positives).
    recruit_chunks = find_recruit_paragraphs(scope_text)
    for key in ("招生信息", "招生", "招生说明", "招生招聘"):
        if key in sections and sections[key].strip():
            recruit_chunks.insert(0, sections[key].strip())
            break
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=kv.get("姓名") or list_item.name_cn,
        title=title,
        email=email,
        email_obfuscated=email_obf,
        phone=phone,
        photo_url=list_item.photo_url,
        homepage=homepage,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class TjuAdapter(SchoolAdapter):
    school_code = "tju"
    supports = {"ic"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        return _parse_list_cic(html, list_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        return _parse_profile_cic(html, profile_url, list_item)
