"""Beijing Institute of Technology (BIT) adapter.

v0.4 covers three faculties hosted on three sibling sub-domains:

* ``cs``  — 计算机学院 (cs.bit.edu.cn). The faculty / 师资队伍 area is split
  by *role*: ``szdw/jsml/bssds/index.htm`` (博士生导师, ~76 PIs) and
  ``szdw/jsml/sssds/index.htm`` (硕士生导师, ~72 — heavy overlap with bssds).
  An orthogonal *研究所* listing under ``szdw/jsml2/<inst>2/index.htm`` lists
  the same PIs grouped by institute (12-38 each); the same person therefore
  appears under multiple buckets. We deduplicate by ``profile_url`` (which
  is a 32-char hex hash that's identical across listings for the same person).
  The CS site also hosts 特色化示范性软件学院 and 信创学院 — they share the
  same listings.

* ``sw``  — 软件学院 (also under cs.bit.edu.cn — BIT has no standalone
  sse / sweet subdomain). We surface the software-flavored institutes under
  the same ``szdw/jsml2/<inst>2/`` taxonomy (``rjznyrjgcyjs2`` = 软件智能与
  软件工程, ``jsjx2`` = 计算机系, plus the 教评中心). PIs in this set are a
  strict subset of the ``cs`` advisor list — the pipeline dedupes by
  (school, name, email).

* ``ai``  — 人工智能学院 (ai.bit.edu.cn). 师资队伍 is one paginated grid
  under ``szdw/index.htm`` → ``szdw/index1.htm`` … . Per-card layout uses
  ``a.item.gp-flex`` with ``div.title`` (name), ``div.summary`` (rank /
  导师类型). The profile head card uses a label/value ``div.box >
  div.left + div.right`` pattern (姓名 / 职称 / 导师类型 / 学科方向 /
  电子邮件 / 联系电话).

* ``cse`` — 网络空间安全学院 (cst.bit.edu.cn — note the host is "cst", not
  "cse"). Faculty list under ``szdw/jsml/index.htm`` renders both
  bssds + sssds inline using a ``div.sub_list001 > ul > li > a`` cards
  (``div.left img`` + ``div.right`` with title + info containing 职称
  comma-separated). Profile bodies live in ``div.sub_article0032
  > div.wrapArticle > div.article`` with ``<h1><strong>研究方向</strong>
  </h1>`` / ``<h1><strong>个人简历</strong></h1>`` pseudo-headers.

Known oddities
--------------
1. **CS / SW share one site, one PI list.** ``sw`` is therefore a curated
   software-flavored subset of ``cs`` — the pipeline dedupes them.
2. **CS list pages put name in ``a.item.fs18`` only — no title / email
   on the list.** Both fields come from the profile (``div.sub_034 > div.left
   > div.summary > <p>``).
3. **CS profile body lives in ``div.sub_034 > div.right.article``** and
   uses ``<h2>个人信息</h2>`` / ``<h2>科研方向</h2>`` / ``<h2>代表性学术成果</h2>``
   pseudo-headers (real ``<h2>``, not ``<h4>``).
4. **AI list page uses lazy-loaded images** (``data-src="…"`` not ``src``).
   We pick up ``data-src`` as the canonical photo URL.
5. **CST (网安) profile uses a ``<table>`` head card with text inside
   ``<span>`` elements.** Section headers are ``<h1>`` containing
   ``<strong>research_section_name</strong>``; bio paragraphs are plain
   ``<p>`` text.
6. **国防访问限制**: ai.bit.edu.cn and cst.bit.edu.cn occasionally serve
   403 / require human-in-the-loop captchas from VPS networks. The
   fixtures captured here are the canonical "no-block" responses; if the
   pipeline hits a 403 it should fall back to Playwright (planned v0.4
   work).
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

# Title keywords used to keep faculty-like entries.
_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "特别研究员", "特别副研究员", "长聘", "准聘", "预聘",
    "院士", "Professor", "Lecturer", "Researcher",
)

# Generic pseudo-section labels (BIT mostly uses 个人信息 / 科研方向 /
# 研究方向 / 代表性学术成果, but we keep the union for forward-compat).
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "主要研究领域", "科研方向",
    "Research Interests",
    "研究概况", "科研概况", "个人简介", "简介", "个人信息", "个人简历",
    "Bio", "Biography",
    "教育背景", "工作经历", "经历", "教育教学", "工作经历及学习经历",
    "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文",
    "代表性学术成果", "代表性论著", "代表性论文",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目",
    "联系方式", "所在学科",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")

# 32-char lowercase hex hash that names every BIT profile page filename.
_HEX_PROFILE_RE = re.compile(r"^[0-9a-f]{32}\.htm$", re.IGNORECASE)


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Same logic as the tsinghua / buaa adapters."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" 　。.；;:：")
        if not line:
            continue
        for p in _SPLIT_RE.split(line):
            p = p.strip(" 　。.；;:：等以及和与")
            if _is_tag(p):
                out.append(p)
        if len(out) >= 15:
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
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


def _title_from_info(info_text: str) -> str | None:
    """Extract the leading 职称 from a comma/punctuation-separated info line.

    The CST list cards put a string like "特聘教授，博士生导师，学院院长" inside
    ``div.info``; the *first* fragment is the academic rank.
    """
    if not info_text:
        return None
    parts = re.split(r"[，,、；;/]+", info_text.strip())
    for p in parts:
        p = p.strip()
        if p and len(p) <= 12 and any(kw in p for kw in _FACULTY_TITLE_KEYWORDS):
            return p
    # Fallback: first non-empty fragment if short.
    if parts:
        p = parts[0].strip()
        if p and len(p) <= 12:
            return p
    return None


# ---------------------------------------------------------------------------
# Dept routing
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    """Route a list / profile URL to one of the supported dept codes.

    The path is the discriminator for the CS-hosted multi-faculty site:
      * ``/szdw/jsml/bssds|sssds|index.htm`` → cs (unified advisor list)
      * ``/szdw/jsml2/<inst>2/...``          → sw (institute view, used by
        the software-flavored institutes; same profile template as cs)
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host.endswith("ai.bit.edu.cn"):
        return "ai"
    if host.endswith("cst.bit.edu.cn") or host.endswith("cse.bit.edu.cn"):
        return "cse"
    if host.endswith("cs.bit.edu.cn"):
        if "/jsml2/" in path:
            return "sw"
        return "cs"
    return "cs"  # safe default — CS hosts the most PIs


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


def _parse_list_cs_simple(html: str, list_url: str) -> list[ListItem]:
    """cs.bit.edu.cn flat ``a.item.fs18`` advisor list (jsml/bssds, jsml/sssds,
    jsml2/<inst>2).

    Card layout (jsml/bssds):
        <div class="sub_033a ul-inline">
          <ul class="gp-avg-md-5 gp-avg-3">
            <li><a class="item fs18" href="<hex>.htm" title="">姓名</a></li>
            ...
          </ul>
        </div>

    All three listings (jsml/bssds, jsml/sssds, jsml2/<inst>2) share this
    structure and use the same profile_url filename (``<32-hex>.htm``), so
    deduping by absolute URL collapses the same person across listings.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    # Restrict to the main content container (``div.sub_033``) so the side-nav
    # ``ul1`` / ``ul2`` (which also contains <a>) does not leak.
    container = tree.css_first("div.sub_033") or tree.css_first("div.sub_033a") or tree.body
    if container is None:
        return items
    for a in container.css("a.item"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        # Profile filenames are 32-hex .htm
        last = href.rsplit("/", 1)[-1]
        if not _HEX_PROFILE_RE.match(last):
            continue
        name = (a.attributes.get("title") or "").strip() or text_of(a)
        if not name:
            continue
        # Skip overlong / non-CJK noise — BIT names are pure Chinese.
        if len(name) > 20:
            continue
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


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """ai.bit.edu.cn 师资队伍 list page.

    Card layout:
        <li>
          <a class="item gp-flex" href="<hex>.htm" title="" target="_blank">
            <div class="img_box">
              <div class="gp-img"><img class="lazy" data-src="..."></div>
            </div>
            <div class="info_box">
              <div class="title fs16">邓方</div>
              <div class="summary fs14 ">教授</div>
              <div class="summary fs12 lh18x5">博士生导师</div>
            </div>
          </a>
        </li>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    container = tree.css_first("div.sub_03") or tree.body
    if container is None:
        return items
    for a in container.css("a.item"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        last = href.rsplit("/", 1)[-1]
        if not _HEX_PROFILE_RE.match(last):
            continue
        name_node = a.css_first("div.title")
        name = text_of(name_node)
        if not name:
            continue
        if len(name) > 20 or not any("一" <= c <= "鿿" for c in name):
            continue
        # First .summary is the rank; second is 导师类型. Title = rank.
        title: str | None = None
        for summ in a.css("div.summary"):
            t = text_of(summ)
            if t and len(t) <= 12 and any(kw in t for kw in _FACULTY_TITLE_KEYWORDS):
                title = t
                break
        # Photo (lazy-loaded → data-src)
        img = a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = (
                img.attributes.get("data-src")
                or img.attributes.get("src")
                or ""
            )
            if src and not src.endswith(".gif"):  # gif = placeholder
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


def _parse_list_cse(html: str, list_url: str) -> list[ListItem]:
    """cst.bit.edu.cn 教师名录 list page (一页含 bssds + sssds).

    Card layout:
        <div class="sub_list001 …">
          <ul>
            <li><a href="bssds/<hex>.htm">
              <div class="left">
                <div class="img"><img src="..."></div>
              </div>
              <div class="right">
                <div class="title fs20">  安建平 </div>
                <div class="info fs14">特聘教授，博士生导师，学院院长<br></div>
              </div>
            </a></li>
            ...
          </ul>
        </div>

    The page also paginates client-side (``href="#"`` numeric links); we
    only parse what's statically present. Aggregation is at schools.yaml.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    container = tree.css_first("div.sub_list001") or tree.body
    if container is None:
        return items
    for a in container.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        last = href.rsplit("/", 1)[-1]
        if not _HEX_PROFILE_RE.match(last):
            continue
        right = a.css_first("div.right")
        if right is None:
            continue
        title_node = right.css_first("div.title")
        name = text_of(title_node)
        if not name:
            continue
        if len(name) > 20 or not any("一" <= c <= "鿿" for c in name):
            continue
        info_node = right.css_first("div.info")
        title = _title_from_info(text_of(info_node)) if info_node is not None else None
        img = a.css_first("div.left img") or a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = (
                img.attributes.get("data-src")
                or img.attributes.get("src")
                or ""
            )
            if src:
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


# ---------------------------------------------------------------------------
# Profile parsers
# ---------------------------------------------------------------------------


# Common label patterns inside the CS / SW head card (``div.sub_034 > div.left
# > div.summary > <p>``).
_HEAD_LABEL_TITLE = re.compile(r"^\s*职称\s*[:：]\s*(.+)$")
_HEAD_LABEL_PHONE = re.compile(r"^\s*(?:联系)?电话\s*[:：]\s*(.+)$")
_HEAD_LABEL_EMAIL = re.compile(r"^\s*E-?mail\s*[:：]\s*(.+)$", re.IGNORECASE)
_HEAD_LABEL_ADDRESS = re.compile(r"^\s*通信地址\s*[:：]\s*(.+)$")


def _split_h2_sections(content_node) -> dict[str, str]:
    """CS / SW profile body uses real ``<h2>label</h2>`` pseudo-headers
    followed by ``<p>body</p>`` paragraphs and a ``<div class="line">``
    separator between sections.

    We walk children in document order: an ``<h2>`` opens a new section, any
    subsequent text-bearing node (``<p>``, ``<div>``, ``<ul>``, ``<table>``)
    is appended to the current section until the next ``<h2>``.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    if content_node is None:
        return {}
    for n in content_node.iter():
        if n.tag == "h2":
            label = text_of(n).strip().rstrip("：:")
            if label and len(label) <= 14 and label in _PSEUDO_HEADERS:
                current = label
                sections.setdefault(current, [])
            else:
                current = None
            continue
        if current is None:
            continue
        if n.tag in ("p", "div", "ul", "ol", "table"):
            t = n.text(separator=" ", strip=True)
            if t:
                sections[current].append(t)
    return {k: "\n".join(v) for k, v in sections.items() if v or k in sections}


def _parse_profile_cs(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """cs.bit.edu.cn (and the jsml2 institute view) profile.

    Page layout:
        <div class="sub_034">
          <div class="left">
            <div class="img_box"><img src="..."></div>
            <div class="vicetitle fs16">计算机科学与技术</div>
            <div class="title fs20">柴成亮</div>
            <div class="summary fs16">
              <p>职称：预聘副教授（特别研究员）、博士生导师</p>
              <p>联系电话：</p>
              <p>E-mail：ccl@bit.edu.cn</p>
              <p>通信地址：</p>
            </div>
          </div>
          <div class="right article fs18 lh28">
            <h2>个人信息</h2>     <p>bio…</p>  <p>...</p>
            <div class="line"></div>
            <h2>科研方向</h2>     <p>tag list…</p>
            <div class="line"></div>
            <h2>代表性学术成果</h2> <p>refs…</p>
          </div>
        </div>
    """
    tree = parse(html)
    head = tree.css_first("div.sub_034 div.left")
    body = tree.css_first("div.sub_034 div.right") or tree.css_first(
        "div.sub_034"
    )
    if head is None and body is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None

    if head is not None:
        img = head.css_first("div.img_box img") or head.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        title_node = head.css_first("div.title")
        if not name:
            name = text_of(title_node) or name
        summary = head.css_first("div.summary")
        if summary is not None:
            for p in summary.css("p"):
                t = text_of(p)
                if not t:
                    continue
                m = _HEAD_LABEL_TITLE.match(t)
                if m and not title:
                    val = m.group(1).strip()
                    if val:
                        title = val
                    continue
                m = _HEAD_LABEL_EMAIL.match(t)
                if m and not email:
                    em, _ = extract_email(m.group(1))
                    if em:
                        email = em
                    continue
                m = _HEAD_LABEL_PHONE.match(t)
                if m and not phone:
                    val = m.group(1).strip()
                    if val:
                        phone = val
                    continue
                # 通信地址 — we don't carry it as a field, but keep it out of
                # the bio extractor.

    bio: str | None = None
    research_tags: list[str] = []
    scope_text = ""
    if body is not None:
        scope_text = body.text(separator=" ", strip=True)
        sections = _split_h2_sections(body)
        for key in ("个人信息", "个人简介", "简介", "研究概况", "科研概况", "个人简历"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1500]
                break
        for key in ("研究方向", "科研方向", "主要研究方向", "研究兴趣", "研究领域"):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break

    if not email and scope_text:
        em, _ = extract_email(scope_text)
        if em:
            email = em

    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# AI head card uses label/value boxes; map labels we care about.
_AI_HEAD_LABELS: dict[str, str] = {
    "姓名": "name",
    "职称": "title",
    "导师类型": "guide_type",
    "学科方向": "discipline",
    "研究方向": "research",
    "电子邮件": "email",
    "邮箱": "email",
    "联系电话": "phone",
    "电话": "phone",
}


def _parse_profile_ai(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """ai.bit.edu.cn profile.

    Page layout:
        <div class="sub_031a gp-flex">
          <div class="img_box"><img></div>
          <div class="info_box fs24">
            <div class="box">
              <div class="left">姓名：</div>
              <div class="right">邓方</div>
            </div>
            <div class="box">…职称…</div>
            <div class="box">…导师类型…</div>
            <div class="box">…学科方向…</div>
            <div class="box">…电子邮件…</div>
          </div>
        </div>

    Body lives in subsequent ``div.article`` (one long ``<p>`` blob) — or
    falls back to the inline 学科方向 line in the head card for research
    interests.
    """
    tree = parse(html)
    head = tree.css_first("div.sub_031a")
    body = tree.css_first("div.sub_031a ~ div.article") or tree.css_first(
        "div.article"
    )
    # Fallback: find any block containing 个人简介 prose.
    if head is None and body is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    research_tags: list[str] = []
    discipline: str | None = None

    if head is not None:
        img = head.css_first("img")
        if img is not None and photo_url is None:
            src = (
                img.attributes.get("data-src")
                or img.attributes.get("src")
                or ""
            )
            if src and not src.endswith(".gif"):
                photo_url = absolutize(profile_url, src)
        for box in head.css("div.box"):
            left = box.css_first("div.left")
            right = box.css_first("div.right")
            if left is None or right is None:
                continue
            label_raw = text_of(left).rstrip("：:").strip()
            value = text_of(right).strip()
            if not label_raw or not value:
                continue
            label = _AI_HEAD_LABELS.get(label_raw)
            if label == "name" and not name:
                name = value
            elif label == "title" and not title:
                title = value
            elif label == "email" and not email:
                em, _ = extract_email(value)
                if em:
                    email = em
            elif label == "phone" and not phone:
                phone = value
            elif label == "discipline":
                discipline = value
            elif label == "research":
                research_tags = _split_interests(value)

    scope_text = ""
    bio: str | None = None
    if body is not None:
        scope_text = body.text(separator=" ", strip=True)
        # Bio is the first non-empty paragraph.
        for p in body.css("p"):
            t = p.text(separator=" ", strip=True)
            if t and len(t) >= 30:
                bio = t[:1500]
                break

    # Fall back to discipline as the research-interests source.
    if not research_tags and discipline:
        research_tags = _split_interests(discipline)
    # And inline "主要研究方向：…" in body prose if still empty.
    if not research_tags and scope_text:
        research_tags = _extract_inline_research(scope_text)

    if not email and scope_text:
        em, _ = extract_email(scope_text)
        if em:
            email = em

    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=phone,
        photo_url=photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# Inline regex helpers shared with the AI / CSE parsers below.
_INLINE_RESEARCH_RE = re.compile(
    r"研究(?:方向|兴趣|领域|概况)[为是:：]?\s*([^。\n]{2,200})"
)
_INLINE_INCLUDE_RE = re.compile(
    r"研究(?:方向|领域|兴趣)(?:包括|为|是|涉及|主要(?:为|是|集中在|包括))[：:]?\s*([^。\n]{2,200})"
)


def _extract_inline_research(text: str) -> list[str]:
    for rx in (_INLINE_INCLUDE_RE, _INLINE_RESEARCH_RE):
        m = rx.search(text)
        if m:
            tags = _split_interests(m.group(1))
            if tags:
                return tags
    return []


def _split_h1_strong_sections(content_node) -> dict[str, str]:
    """CST profile body uses ``<h1>...<strong>label</strong>...</h1>``
    pseudo-headers followed by ``<p>body</p>`` paragraphs. We treat any
    ``<h1>`` (or ``<h2>``) whose innermost text matches a known label as a
    section header.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    if content_node is None:
        return {}
    for n in content_node.iter():
        if n.tag in ("h1", "h2", "h3", "h4"):
            label = text_of(n).strip().rstrip("：:")
            if label and len(label) <= 14 and label in _PSEUDO_HEADERS:
                current = label
                sections.setdefault(current, [])
            else:
                current = None
            continue
        if current is None:
            continue
        if n.tag in ("p", "div", "ul", "ol"):
            t = n.text(separator=" ", strip=True)
            if t:
                sections[current].append(t)
    return {k: "\n".join(v) for k, v in sections.items() if v or k in sections}


def _parse_profile_cse(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """cst.bit.edu.cn (网络空间安全学院) profile.

    Page layout:
        <div class="sub_article0032">
          <div class="wrapArticle">
            <div class="article fs16">
              <table>
                <tr>
                  <td><img></td>
                  <td></td>
                  <td>
                    <p><strong><span>安建平（An Jianping）</span></strong></p>
                    <p><span>特聘教授，博士生导师，学院院长</span></p>
                    <p><span>办公电话：010-...</span></p>
                    <p><span>电子邮件：an@bit.edu.cn</span></p>
                    <p><span>办公地点：…</span></p>
                  </td>
                </tr>
              </table>
              <h1><strong>所在学科</strong></h1>
              <p><span>网络空间安全、信息与通信工程</span></p>
              <h1><strong>研究方向</strong></h1>
              <p><span>空天信息网络与安全、空间信号处理</span></p>
              <h1><strong>个人简历</strong></h1>
              <p>2020/6~至今 …</p> …
              <h1><strong>代表性论著</strong></h1>
              …
            </div>
          </div>
        </div>
    """
    tree = parse(html)
    article = tree.css_first("div.sub_article0032 div.article") or tree.css_first(
        "div.article"
    )
    if article is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url

    # Head card lives inside the first <table>.
    head_table = article.css_first("table")
    if head_table is not None:
        img = head_table.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        for p in head_table.css("p"):
            t = p.text(separator=" ", strip=True)
            if not t:
                continue
            # First non-empty paragraph with bold large text = name (often
            # like "安建平（An Jianping）"). We trust list_item.name_cn first.
            if not name:
                m = re.match(r"^\s*([一-鿿·\s]{2,12})", t)
                if m:
                    name = m.group(1).strip()
            # Title: a comma-separated string with 教授 / 副教授 etc.
            if not title and any(kw in t for kw in _FACULTY_TITLE_KEYWORDS):
                # take everything up to the first 。 / ; / line break
                clean = re.split(r"[。；;]", t, 1)[0].strip()
                if 2 <= len(clean) <= 60:
                    title = clean
            if not email:
                if "邮件" in t or "邮箱" in t or "@" in t:
                    em, _ = extract_email(t)
                    if em:
                        email = em
            if not phone and ("电话" in t or "Tel" in t):
                m = re.search(r"[\d\-\s]{7,20}", t)
                if m:
                    val = m.group(0).strip()
                    if val:
                        phone = val

    sections = _split_h1_strong_sections(article)

    bio: str | None = None
    for key in ("个人简介", "简介", "个人简历", "研究概况", "科研概况"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    research_tags: list[str] = []
    for key in ("研究方向", "主要研究方向", "研究兴趣", "研究领域", "科研方向"):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    scope_text = article.text(separator=" ", strip=True)
    if not research_tags:
        research_tags = _extract_inline_research(scope_text)
    if not email:
        em, _ = extract_email(scope_text)
        if em:
            email = em

    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
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
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class BitAdapter(SchoolAdapter):
    school_code = "bit"
    supports = {"cs", "sw", "ai", "cse"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept == "ai":
            return _parse_list_ai(html, list_url)
        if dept == "cse":
            return _parse_list_cse(html, list_url)
        # cs and sw share the same site / template; only the list-url path
        # differs (``/jsml/{bssds,sssds}`` vs ``/jsml2/<inst>2``).
        return _parse_list_cs_simple(html, list_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        dept = _dept_from_url(profile_url)
        if dept == "ai":
            return _parse_profile_ai(html, profile_url, list_item)
        if dept == "cse":
            return _parse_profile_cse(html, profile_url, list_item)
        return _parse_profile_cs(html, profile_url, list_item)
