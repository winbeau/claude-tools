"""Shanghai Jiao Tong University (SJTU) adapter.

v0.2 covers four departments whose homepages span four sub-domains and three
very different rendering styles. v0.4.1 appends a fifth department
(网络空间安全学院 / infosec.sjtu.edu.cn) that was missed in v0.4.

Departments
-----------
* ``cs``        — 计算机科学与工程系 (cs.sjtu.edu.cn). List landing
  ``/jiaoshiml.html`` is an empty shell; faculty data is loaded via a POST
  AJAX endpoint that returns JSON ``{"tab_html": ..., "content": "<rc-item>...</rc-item>..."}``.
  This adapter accepts EITHER the JSON payload (preferred) OR the shell HTML
  (returns []) so a future fetcher upgrade can drop the JSON straight in.
* ``ai``        — 人工智能学院 (soai.sjtu.edu.cn / sai.sjtu.edu.cn).
  ``/cn/faculty/zzjs`` renders a full ``<li>`` grid per teacher with name,
  title, email and homepage on the list page itself.
* ``see-ai``    — 电院 AI 方向 (sais.sjtu.edu.cn — 自动化与感知学院, the
  electronics-school sub-unit that hosts AI / robotics / vision / control
  faculty). Same POST-AJAX pattern as ``cs``; ``parse_list`` returns every
  teacher and ``parse_profile`` filters by AI-keyword on the 研究方向
  section (non-AI faculty get an ``AdvisorPartial`` flagged with empty
  ``research_interests`` so the pipeline / DeepSeek layer can drop them).
* ``qingyuan``  — 清源研究院 (www.qingyuan.sjtu.edu.cn). 全职教师 page lists
  ~10 tenure-track faculty with names + photos. Same template used for
  profile pages (``div.t_d_title`` + ``div.tel_info`` + ``div.text``).
* ``cse``       — 网络空间安全学院 (infosec.sjtu.edu.cn, v0.4.1). Static .NET
  WebForms template; ``Directory.aspx`` is a 67 KB single-page roster with
  ~75 teachers grouped by 职称 section (``<div class="Faculty">``). Profile
  pages live at ``DirectoryDetail.aspx?id=<int>`` and use a ``<div class="TeamDetail">``
  layout: left column has phone/email/address ``<em>`` icons, right column
  has ``<h2>name</h2><h3>title</h3>`` plus a tabbed content area with
  ``<li id="oneN">研究兴趣 / 教育背景 / ...</li>`` headers and
  ``<div id="con_one_N">body</div>`` panels mapped by index.

  A small fraction (~5) of senior PI in the CSE roster link out to
  ``http://www.cs.sjtu.edu.cn/PeopleDetail.aspx?id=N`` instead of the local
  ``DirectoryDetail.aspx``. The list parser keeps these absolute URLs as-is
  so that ``_dept_from_url`` routes them back to the cs.sjtu profile parser.
  As of 2026-05 the PeopleDetail.aspx endpoint at cs.sjtu redirects to ``/``
  (the cs.sjtu CMS migration retired that URL pattern), so ``_parse_profile_cs``
  yields a stub partial — but the list-level metadata (name + email + title +
  photo, all carried on the infosec Directory.aspx page) survives via the
  ListItem fallback inside ``_empty_partial``.

Known oddities
--------------
1. **CS + see-ai list pages are AJAX-only.** ``www.cs.sjtu.edu.cn`` and
   ``sais.sjtu.edu.cn`` both POST to ``/active/ajax_teacher_list.html``
   and the static landing returns 0 faculty links. We parse the JSON
   response when the body starts with ``{`` so the user can pre-fetch via
   POST and feed the result to the pipeline.
2. **The CS list groups teachers by research institute** — many people
   appear twice (once as 所长/副所长, once in 名单). We dedupe by
   profile URL.
3. **The CS profile lacks an email field for many senior professors**
   (e.g. 陈海波) — only 个人主页 is exposed. ``email`` is left None
   in that case rather than scraping a stale value.
4. **AI-school subdomains are interchangeable**: ``soai.sjtu.edu.cn``
   (the production hostname) and ``sai.sjtu.edu.cn`` (older alias) serve
   the same HTML. We route on path ``/cn/faculty`` / ``/cn/facultydetails``.
5. **Qingyuan list duplicates each teacher**: once in ``div.tech_position``
   (grouped by rank) and again in a secondary ``div.text`` panel; we
   dedupe by profile URL.
6. **see-ai AI filter** is keyword-based on the 研究方向 section. Mis-
   classifications are expected at the margins (e.g. bio-electrophoresis
   researchers like 曹成喜 are correctly excluded; control-with-AI hybrids
   are kept). v0.3 should reconcile with the DeepSeek enrichment layer.
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


# Keywords on 研究方向 that mark a sais (see-ai) teacher as AI-relevant.
# Broad on purpose — false-positives are OK (pipeline dedupes), false
# negatives lose people.
_AI_KEYWORDS: tuple[str, ...] = (
    "人工智能", "机器学习", "深度学习", "强化学习", "神经网络",
    "计算机视觉", "图像", "视频", "模式识别",
    "自然语言", "语音", "语言模型", "大模型", "生成", "多模态",
    "机器人", "无人", "自动驾驶", "智能驾驶", "感知", "决策",
    "智能", "智慧",
    "数据挖掘", "知识图谱", "大数据", "联邦学习", "迁移学习",
    "边缘计算", "AI for", "AI4",
    "vision", "learning", "robot", "intelligent",
)

_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师", "研究员", "副研究员",
    "助理研究员", "工程师", "高级工程师", "讲席", "特聘",
    "院士", "Professor",
)

# Pseudo-section labels on SJTU profile pages.  The CS / SAIS templates use
# explicit ``<div class="tit"><p>label</p></div>`` blocks while AI school
# uses ``<div class="h3">label</div>``; the qingyuan template lumps everything
# into one long ``<div class="text">`` block.
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "Research Interests",
    "个人简介", "简介", "Bio", "Biography",
    "教育背景", "教育经历", "工作经历", "工作履历", "学术经历",
    "学术兼职", "社会兼职", "任职", "学术任职", "学术服务",
    "招生信息", "招生", "招生招聘",
    "教学工作", "教授课程", "讲授课程", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文发表", "研究成果",
    "荣誉奖励", "奖励与荣誉", "荣誉", "获奖", "获奖信息",
    "研究课题", "科研项目", "项目", "项目资助",
}


_SPLIT_RE = re.compile(r"[、，,；;/\n]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
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
    # dedupe preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped[:10]


def _is_ai_relevant(tags: list[str], scope_text: str = "") -> bool:
    """Return True if any AI keyword appears in the research-interest tags,
    falling back to a scan of the bio text. Used by the ``see-ai`` filter."""
    hay = " ".join(tags).lower() + " " + scope_text.lower()
    return any(kw.lower() in hay for kw in _AI_KEYWORDS)


def _looks_like_faculty(title: str | None) -> bool:
    if not title:
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    # CSE first — its host is fully distinct (infosec.sjtu.edu.cn) and we want
    # to route before any catch-all. Cross-linked cs.sjtu PIs from the CSE
    # roster keep their cs.sjtu.edu.cn host string so they still fall into
    # the cs branch on profile_url, which is what we want.
    if "infosec.sjtu.edu.cn" in host:
        return "cse"
    if "cs.sjtu.edu.cn" in host:
        return "cs"
    if "soai.sjtu.edu.cn" in host or "sai.sjtu.edu.cn" in host:
        return "ai"
    if "sais.sjtu.edu.cn" in host:
        return "see-ai"
    if "qingyuan.sjtu.edu.cn" in host:
        return "qingyuan"
    return "cs"  # safe default — cs is the largest


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


def _maybe_unwrap_json_list(body: str) -> str | None:
    """Return the ``content`` HTML payload if ``body`` is the AJAX JSON
    response, else None. Both cs.sjtu and sais.sjtu use the same envelope.

    The envelope keys vary slightly (CS has ``tab_html`` + ``content``,
    sais has ``content`` + ``msg``) but ``content`` is always present.
    """
    body = body.lstrip()
    if not body.startswith("{"):
        return None
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    content = d.get("content")
    if isinstance(content, str):
        return content
    return None


def _parse_list_cs(html: str, list_url: str) -> list[ListItem]:
    """cs.sjtu.edu.cn 教师名录.

    Source HTML is the ``content`` value of the AJAX JSON envelope. Layout:

        <div class="rc-item">
          <div class="tit"><div class="name">研究所名</div>...</div>
          <div class="dt">
            <p>所　长：<a href="https://www.cs.sjtu.edu.cn/jiaoshiml/X.html">姓名</a></p>
            <p>副所长：<a ...>姓名</a></p>
          </div>
          <div class="name-list">
            <span><a href="...jiaoshiml/X.html">姓名</a></span>
            <span>...</span>
          </div>
        </div>

    A teacher may appear in both ``div.dt`` (as 所长 / 副所长) and
    ``div.name-list`` of the same group — we dedupe by profile URL.
    """
    payload = _maybe_unwrap_json_list(html) or html
    tree = parse(payload)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        if "/jiaoshiml/" not in href:
            continue
        # the path /jiaoshiml.html (without /jiaoshiml/<name>.html) is the
        # landing link, not a profile.
        if href.rstrip("/").endswith("/jiaoshiml.html"):
            continue
        name = text_of(a)
        if not name:
            continue
        # CS list pads names with full-width spaces (e.g. 陈　榕). Strip.
        name = name.replace("　", "").strip()
        if not name or len(name) > 20:
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        items.append(ListItem(name_cn=name, profile_url=absurl))
    return items


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """soai.sjtu.edu.cn / sai.sjtu.edu.cn 师资名单.

    Each ``<li>`` under ``div.teamList`` carries name + 职称 + 邮箱 + 个人主页.

        <li><div class="pd">
          <div class="img"><a href="/cn/facultydetails/zzjs/SLUG"><img></a></div>
          <div class="text">
            <div class="h3"><a href="/cn/facultydetails/zzjs/SLUG">姓名</a></div>
            <div class="em">职称：xxx</div>
            <div class="p">
              <p>邮箱：xxx@sjtu.edu.cn</p>
              <p>个人主页：<a target="_blank" href="...">...</a></p>
            </div>
          </div>
        </div></li>
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for li in tree.css("li"):
        text_div = li.css_first("div.text")
        if text_div is None:
            continue
        a = text_div.css_first("div.h3 a") or text_div.css_first("a")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        if "/facultydetails/" not in href:
            continue
        name = text_of(a)
        if not name:
            continue
        title = text_of(text_div.css_first("div.em"))
        if title:
            title = title.split("：", 1)[-1].strip() if "：" in title else title
        email: str | None = None
        photo_url: str | None = None
        for p in text_div.css("div.p p"):
            t = text_of(p)
            if not t:
                continue
            if "@" in t:
                em, _ = extract_email(t)
                if em:
                    email = em
        img = li.css_first("div.img img") or li.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(list_url, src)
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        if not _looks_like_faculty(title):
            continue
        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
                title=title or None,
                email=email,
                photo_url=photo_url,
            )
        )
    return items


def _parse_list_see_ai(html: str, list_url: str) -> list[ListItem]:
    """sais.sjtu.edu.cn 教师名录 — same POST-AJAX JSON envelope as cs.sjtu.

    Content layout (按字母 type=1):

        <div class="js-list">
          <li><a href="https://sais.sjtu.edu.cn/faculty/SLUG.html" class="name">姓名</a></li>
          ...
        </div>

    No metadata on the list page; everything (title/email/research) lives
    on the profile.
    """
    payload = _maybe_unwrap_json_list(html) or html
    tree = parse(payload)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a.name"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        if "/faculty/" not in href:
            continue
        name = text_of(a)
        if not name or len(name) > 20:
            continue
        absurl = absolutize(list_url, href)
        if absurl in seen:
            continue
        seen.add(absurl)
        items.append(ListItem(name_cn=name, profile_url=absurl))
    # fallback: some payloads use plain <li><a>name</a> without class
    if not items:
        for a in tree.css("li a"):
            href = (a.attributes.get("href") or "").strip()
            if "/faculty/" not in href:
                continue
            name = text_of(a)
            if not name or len(name) > 20:
                continue
            absurl = absolutize(list_url, href)
            if absurl in seen:
                continue
            seen.add(absurl)
            items.append(ListItem(name_cn=name, profile_url=absurl))
    return items


def _parse_list_qingyuan(html: str, list_url: str) -> list[ListItem]:
    """www.qingyuan.sjtu.edu.cn 全职教师.

    Two sibling sections render the same teachers:

        <div class="tech_position">
          <div class="t_title">长聘教授</div>
          <div class="people_list">
            <div class="item"><div class="item01">
              <a href="/a/xu-ning-yi-1.html">
                <div class="img"><img class="photo_img" src="..."></div>
                <div class="text"><h3 class="teacher-name">徐宁仪</h3></div>
              </a>
            </div></div>
            ...
          </div>
        </div>
        <!-- and again below, in a different .text panel — dedup by URL -->
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    # track which rank section we are currently inside, so we can attach
    # title from <div class="t_title"> for the teachers in that group.
    current_title: str | None = None
    for n in tree.css("div.tech_position"):
        title_node = n.css_first("div.t_title")
        current_title = text_of(title_node) or None
        for a in n.css("div.item a"):
            href = (a.attributes.get("href") or "").strip()
            if not href or "/a/" not in href:
                continue
            name = text_of(a.css_first("h3.teacher-name")) or text_of(a)
            if not name:
                continue
            absurl = absolutize(list_url, href)
            if absurl in seen:
                continue
            seen.add(absurl)
            img = a.css_first("img.photo_img") or a.css_first("img")
            photo_url = None
            if img is not None:
                src = img.attributes.get("src") or ""
                if src:
                    photo_url = absolutize(list_url, src)
            items.append(
                ListItem(
                    name_cn=name,
                    profile_url=absurl,
                    title=current_title,
                    photo_url=photo_url,
                )
            )
    # Defensive fallback: if the structured walk produced nothing, scan
    # globally for teacher-name anchors.
    if not items:
        for h3 in tree.css("h3.teacher-name"):
            parent_a = h3.parent
            # walk up until we find an <a>
            while parent_a is not None and parent_a.tag != "a":
                parent_a = parent_a.parent
            if parent_a is None:
                continue
            href = parent_a.attributes.get("href") or ""
            if not href or "/a/" not in href:
                continue
            name = text_of(h3)
            if not name:
                continue
            absurl = absolutize(list_url, href)
            if absurl in seen:
                continue
            seen.add(absurl)
            items.append(ListItem(name_cn=name, profile_url=absurl))
    return items


def _parse_list_cse(html: str, list_url: str) -> list[ListItem]:
    """infosec.sjtu.edu.cn 教师名录 (Directory.aspx).

    Layout: <div class="Faculty"> sections grouped by 职称 (院士, 讲席教授,
    特聘教授, ...), each containing a <ul><li><a href="..."><img>+<h2>name</h2>
    +<h5>职称：...</h5>+<p>研究兴趣：...</p>+<p>邮箱：...</p></a></li></ul>.

    A small fraction of senior PI cross-link to www.cs.sjtu.edu.cn:

        <a href="http://www.cs.sjtu.edu.cn/PeopleDetail.aspx?id=96" target="_blank">

    For these we keep the absolute cs.sjtu URL so ``_dept_from_url`` routes
    them to the cs.sjtu profile parser. List metadata (name / title / email
    / photo / research_interests) is still scraped from the infosec card.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("li a"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        # Profile shapes accepted:
        #   DirectoryDetail.aspx?id=N          (local infosec teacher)
        #   http://www.cs.sjtu.edu.cn/PeopleDetail.aspx?id=N  (cross-link)
        is_local = "DirectoryDetail.aspx" in href
        is_xlink = "PeopleDetail.aspx" in href
        if not (is_local or is_xlink):
            continue

        # name lives in <h2> directly under the <a>
        h2 = a.css_first("h2")
        name = text_of(h2) if h2 is not None else ""
        name = name.replace("　", "").strip()
        if not name or len(name) > 20:
            continue

        # title: <h5>职称：xxx</h5> — strip leading 职称：
        h5 = a.css_first("h5")
        title: str | None = None
        if h5 is not None:
            raw_title = text_of(h5).strip().rstrip("。")
            if raw_title.startswith("职称"):
                raw_title = raw_title.split("：", 1)[-1].strip() if "：" in raw_title else raw_title
            title = raw_title or None

        # email + photo + a hint of research_interests in <p> tags
        email: str | None = None
        photo_url: str | None = None
        for p in a.css("p"):
            t = text_of(p)
            if "@" in t:
                em, _ = extract_email(t)
                if em and not email:
                    email = em
        img = a.css_first("img")
        if img is not None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(list_url, src)

        # Absolutise: keep cross-link absolute, normalise local relatives.
        if is_xlink:
            absurl = href if href.startswith("http") else absolutize(list_url, href)
        else:
            absurl = absolutize(list_url, href)

        if absurl in seen:
            continue
        seen.add(absurl)

        items.append(
            ListItem(
                name_cn=name,
                profile_url=absurl,
                title=title,
                email=email,
                photo_url=photo_url,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile parsers
# ---------------------------------------------------------------------------


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


def _split_sections_titled(content_node) -> dict[str, str]:
    """Group section bodies by their preceding ``<div class="tit">label</div>``
    or pseudo-header. Used by the CS and SAIS profile templates which use
    ``<div class="item"><div class="name|tit"><p>label</p></div><div class="txt|detail">body</div></div>``
    blocks.

    Returns a dict mapping label -> joined body text.
    """
    sections: dict[str, str] = {}
    if content_node is None:
        return sections
    # Each <div class="item"> on CS profiles and each <div class="js-item">
    # on SAIS profiles contains exactly one label + body pair.
    for item in content_node.css("div.item, div.js-item"):
        label_node = (
            item.css_first("div.name p")
            or item.css_first("div.tit p")
            or item.css_first("div.name")
            or item.css_first("div.tit")
        )
        body_node = (
            item.css_first("div.txt")
            or item.css_first("div.detail div.txt")
            or item.css_first("div.detail")
        )
        if label_node is None or body_node is None:
            continue
        label = text_of(label_node).strip().rstrip("：:")
        if not label:
            continue
        body_text = body_node.text(separator="\n", strip=True)
        if not body_text:
            continue
        # 如果 body 已包含 label 前缀，剥掉
        if body_text.startswith(label):
            body_text = body_text[len(label):].lstrip("：: \n")
        # 多次同 label 时拼接
        if label in sections:
            sections[label] = f"{sections[label]}\n{body_text}"
        else:
            sections[label] = body_text
    return sections


# CS profile head card: <div class="js-info"><div class="txt">
#   <div class="name">陈海波</div>
#   <div class="zw">特聘教授</div>
#   <div class="dt">
#     <p>所在研究所：xxx</p>
#     <p>邮箱：xxx@sjtu.edu.cn</p>   (may be missing)
#     <p>个人主页：<a href="...">...</a></p>
#   </div>
# </div></div>
def _parse_profile_cs(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    tree = parse(html)
    info_node = tree.css_first("div.js-info")
    dt_node = tree.css_first("div.js-dt")
    if info_node is None and dt_node is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    homepage: str | None = None
    photo_url = list_item.photo_url

    if info_node is not None:
        name = text_of(info_node.css_first("div.name")) or name
        title = title or text_of(info_node.css_first("div.zw")) or None
        # head image
        img = info_node.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        # contact lines: <p>邮箱：xxx@y.cn</p> / <p>个人主页：<a>url</a></p>
        for p in info_node.css("div.dt p"):
            t = text_of(p)
            if not t:
                continue
            if "@" in t and not email:
                em, _ = extract_email(t)
                if em:
                    email = em
            elif t.startswith("个人主页"):
                link = p.css_first("a")
                if link is not None:
                    h = link.attributes.get("href") or ""
                    if h.startswith("http"):
                        homepage = h

    sections = _split_sections_titled(dt_node) if dt_node is not None else {}

    research_tags: list[str] = []
    for key in ("研究方向", "研究兴趣", "研究领域"):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break

    bio: str | None = None
    for key in ("个人简介", "简介"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    # CS pages rarely have a dedicated 招生 section; scan whole content
    scope_text = (
        dt_node.text(separator=" ", strip=True) if dt_node is not None else ""
    ) + " " + (
        info_node.text(separator=" ", strip=True) if info_node is not None else ""
    )
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    if not email:
        em, _ = extract_email(scope_text)
        if em:
            # ignore the school-level footer email (scs@sjtu.edu.cn) — it's
            # the same for every page.
            if not em.startswith("scs@"):
                email = em

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=list_item.phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _parse_profile_ai(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """soai/sai profile.

    Layout:
        <div class="facultyInfoHead"><div class="text">
            <div class="h3">姓名</div>
            <div class="p"><p>职称：副教授</p><p>邮箱：x@y</p>...</div>
        </div></div>
        <div class="facultyInfoBottom">
            <div class="li">
                <div class="h3">个人简介</div>
                <div class="p"><p>...</p></div>
            </div>
            ... (more <div class="li"> blocks)
        </div>

    NOTE: AI school profiles do NOT have a dedicated 研究方向 section —
    research direction is embedded in the 个人简介 prose. We surface it
    by keyword-grep ("研究方向", "研究兴趣", "研究领域") inside the bio.
    """
    tree = parse(html)
    head = tree.css_first("div.facultyInfoHead")
    body = tree.css_first("div.facultyInfoBottom")
    if head is None and body is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    photo_url = list_item.photo_url
    homepage: str | None = None

    if head is not None:
        name = text_of(head.css_first("div.h3")) or name
        img = head.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        for p in head.css("div.p p"):
            t = text_of(p)
            if not t:
                continue
            if t.startswith("职称"):
                if not title:
                    title = t.split("：", 1)[-1].strip() if "：" in t else None
            elif "@" in t and not email:
                em, _ = extract_email(t)
                if em and not em.startswith("aischool@"):
                    email = em

    sections: dict[str, str] = {}
    if body is not None:
        for li in body.css("div.li"):
            label_node = li.css_first("div.h3")
            body_node = li.css_first("div.p")
            if label_node is None or body_node is None:
                continue
            label = text_of(label_node).strip().rstrip("：:")
            if not label:
                continue
            sections[label] = body_node.text(separator="\n", strip=True)

    bio: str | None = None
    for key in ("个人简介", "简介", "Bio", "Biography"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    research_tags: list[str] = []
    for key in ("研究方向", "研究兴趣", "研究领域"):
        if key in sections:
            research_tags = _split_interests(sections[key])
            if research_tags:
                break
    if not research_tags and bio:
        # Try to pull research keywords from inline phrasing in bio
        m = re.search(
            r"研究(?:方向|兴趣|领域)[为是:：]?\s*([^。\n]{2,200})",
            bio,
        )
        if m:
            research_tags = _split_interests(m.group(1))

    scope_text = " ".join(sections.values()) + " " + (bio or "")
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    if not email:
        em, _ = extract_email(scope_text)
        if em and not em.startswith("aischool@"):
            email = em

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=list_item.phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _parse_profile_see_ai(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """sais.sjtu.edu.cn profile.

    Layout (very similar to cs.sjtu but with different wrapper class names):
        <div class="js-info">
            <div class="txt">
                <div class="tit"><p>姓名</p><span>职称</span></div>
                <div class="detail">
                    <p>电子邮件：x@y</p>
                    <p>办公电话：xxx</p>
                    <p>个人主页：http://...</p>
                </div>
            </div>
        </div>
        <div class="js-box-item on">
            <div class="js-list">
                <div class="js-item">
                    <div class="tit"><p>研究方向</p></div>
                    <div class="detail"><div class="txt"><ul>...</ul></div></div>
                </div>
                <div class="js-item">...</div>
            </div>
        </div>

    AI relevance is decided AFTER extraction: if research_interests + bio
    contain no AI keyword, return with empty ``research_interests`` so the
    pipeline / downstream filter can drop the row.
    """
    tree = parse(html)
    info = tree.css_first("div.js-info")
    box = tree.css_first("div.js-box-item.on") or tree.css_first("div.js-box-item")

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None

    if info is not None:
        name = text_of(info.css_first("div.tit p")) or name
        title = title or text_of(info.css_first("div.tit span")) or None
        img = info.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        for p in info.css("div.detail p"):
            t = text_of(p)
            if not t:
                continue
            if t.startswith("电子邮件") or t.startswith("邮箱"):
                em, _ = extract_email(t)
                if em and not email:
                    email = em
            elif t.startswith("办公电话") or t.startswith("电话"):
                v = t.split("：", 1)[-1].strip() if "：" in t else ""
                if v and not phone:
                    phone = v
            elif t.startswith("个人主页") or t.startswith("主页"):
                link = p.css_first("a")
                if link is not None:
                    h = link.attributes.get("href") or ""
                    if h.startswith("http"):
                        homepage = h
                else:
                    v = t.split("：", 1)[-1].strip() if "：" in t else ""
                    if v.startswith("http"):
                        homepage = v

    sections = _split_sections_titled(box) if box is not None else {}

    research_tags: list[str] = []
    research_raw_text = ""
    for key in ("研究方向", "研究兴趣", "研究领域"):
        if key in sections:
            research_raw_text = sections[key]
            research_tags = _split_interests(research_raw_text)
            if research_tags:
                break

    bio: str | None = None
    for key in ("个人简介", "简介"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    scope_text = " ".join(sections.values())
    if not email:
        em, _ = extract_email(scope_text)
        if em and not em.startswith("seiee@"):
            email = em
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    # AI relevance filter — keep the record but strip research_interests when
    # this person is clearly outside AI scope. We err on the side of
    # inclusion: any keyword match (even "智能"/"感知"/"机器人") passes.
    if research_tags and not _is_ai_relevant(research_tags, research_raw_text):
        # signal exclusion by clearing tags + leaving a hint in bio
        research_tags = []

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


def _parse_profile_qingyuan(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """www.qingyuan.sjtu.edu.cn profile.

    Layout (very different from the other three departments):
        <div class="teaher_detail">
          <div class="t_d_title">徐宁仪 长聘教授</div>
          <div class="t_d_content">
            <div class="tel_info">
              <div class="img"><img></div>
              <div class="contactor">
                <p>地址：...</p>
                <p>邮箱：xxx@sjtu.edu.cn</p>
                <p>主页：<a href="...">...</a></p>
              </div>
            </div>
            <div class="text"><p>... long bio with research direction ...</p></div>
          </div>
        </div>

    Research direction is woven into the bio prose; we extract via regex.
    """
    tree = parse(html)
    detail = tree.css_first("div.teaher_detail") or tree.css_first("div.t_d_content")
    if detail is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    photo_url = list_item.photo_url
    homepage: str | None = None

    title_node = detail.css_first("div.t_d_title")
    if title_node is not None:
        raw = text_of(title_node)
        if raw:
            # "徐宁仪 长聘教授" — first whitespace token is the name
            parts = re.split(r"\s+", raw, maxsplit=1)
            if not list_item.name_cn:
                name = parts[0] or name
            if len(parts) == 2 and not title:
                title = parts[1].strip() or None

    contactor = detail.css_first("div.contactor")
    if contactor is not None:
        contactor_text = contactor.text(separator="\n", strip=True)
        for line in contactor_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "@" in line and not email:
                em, _ = extract_email(line)
                if em:
                    email = em
            elif line.startswith("主页") or line.startswith("个人主页"):
                # The link is rendered as <a href> inside the <p>; pick it up
                # via DOM rather than text.
                pass
        link = contactor.css_first("a[href]")
        if link is not None:
            h = link.attributes.get("href") or ""
            if h.startswith("http"):
                homepage = h
    img = detail.css_first("img.photo_img") or detail.css_first("img")
    if img is not None and photo_url is None:
        src = img.attributes.get("src") or ""
        if src:
            photo_url = absolutize(profile_url, src)

    text_node = detail.css_first("div.text")
    bio: str | None = None
    if text_node is not None:
        bio_text = text_node.text(separator="\n", strip=True)
        bio = bio_text.strip()[:1500] if bio_text else None

    research_tags: list[str] = []
    if bio:
        m = re.search(
            r"研究(?:方向|兴趣|领域)[为是:：]?\s*([^。\n]{2,200})",
            bio,
        )
        if m:
            research_tags = _split_interests(m.group(1))

    scope_text = (
        detail.text(separator=" ", strip=True) if detail is not None else ""
    )
    if not email:
        em, _ = extract_email(scope_text)
        if em:
            email = em
    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=list_item.phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


_CSE_TAB_LABELS_FALLBACK: tuple[str, ...] = (
    "研究兴趣",  # con_one_1
    "教育背景",  # con_one_2
    "工作经验",  # con_one_3
    "教授课程",  # con_one_4
    "论文发表",  # con_one_5
    "项目资助",  # con_one_6
    "获奖信息",  # con_one_7
    "学术服务",  # con_one_8
)


def _cse_tab_labels(tree) -> list[str]:
    """Return tab labels in display order for the .Tab block on a
    DirectoryDetail.aspx profile.

    The template uses ``<li id="one1" ...>研究兴趣</li>`` through
    ``<li id="oneN" ...>...</li>``; we read them positionally and fall back
    to the canonical labels seen across all sampled fixtures if a page has
    a non-standard ordering or count.
    """
    labels: list[str] = []
    tab_box = tree.css_first("div.Tab")
    if tab_box is None:
        return list(_CSE_TAB_LABELS_FALLBACK)
    for li in tab_box.css("li"):
        lid = (li.attributes.get("id") or "").strip()
        if not lid.startswith("one"):
            continue
        label = text_of(li).strip().rstrip("：:")
        if label:
            labels.append(label)
    return labels or list(_CSE_TAB_LABELS_FALLBACK)


def _parse_profile_cse(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """infosec.sjtu.edu.cn DirectoryDetail.aspx?id=N profile.

    Layout:
        <div class="TeamDetail">
          <div class="w180 fl">
            <em><i class="fa fa-phone"></i>021-...</em>
            <em><i class="fa fa-envelope-o"></i>x@sjtu.edu.cn</em>
            <em><i class="fa fa-map-marker"></i>address</em>
            <img src="...">  (some pages)
          </div>
          <div class="w510 fr">
            <h2>姓名</h2>
            <h3>职称</h3>
            <p><span>...bio prose...</span></p>
          </div>
          <div class="Tab">
            <li id="one1" class="Current">研究兴趣</li>
            <li id="one2">教育背景</li>
            ...
          </div>
          <div id="con_one_1" class="lh250">研究方向 tag list</div>
          <div id="con_one_2" class="lh250 none">...</div>
          ...
        </div>
    """
    tree = parse(html)
    detail = tree.css_first("div.TeamDetail")
    if detail is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url

    # --- right column: name / title / bio prose ----------------------------
    right = detail.css_first("div.w510")
    bio: str | None = None
    if right is not None:
        h2 = right.css_first("h2")
        if h2 is not None:
            n = text_of(h2)
            if n:
                name = n
        h3 = right.css_first("h3")
        if h3 is not None and not title:
            t = text_of(h3)
            if t:
                title = t
        # bio: collect <p> direct children but skip the contact-icon emboss
        bio_chunks: list[str] = []
        for p in right.css("p"):
            txt = p.text(separator=" ", strip=True)
            if not txt:
                continue
            # skip lines that are just labels or contact remnants
            if txt.startswith("邮箱") or txt.startswith("电话") or txt.startswith("地址"):
                continue
            bio_chunks.append(txt)
        if bio_chunks:
            joined = "\n".join(bio_chunks).strip()
            if joined:
                bio = joined[:1500]

    # --- left column: contact icons ----------------------------------------
    left = detail.css_first("div.w180")
    if left is not None:
        for em in left.css("em"):
            t = em.text(separator=" ", strip=True)
            if not t:
                continue
            if "@" in t and not email:
                e, _ = extract_email(t)
                if e and not e.startswith("scs@"):
                    email = e
            # phone: <em><i></i>021-34208292</em>
            elif not phone and re.search(r"\d{3,4}-?\d{6,}", t):
                m = re.search(r"[\d\-]{6,}", t)
                if m:
                    phone = m.group(0)
        img = left.css_first("img")
        if img is not None and not photo_url:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)

    # --- tab contents (con_one_1 ..) ---------------------------------------
    labels = _cse_tab_labels(tree)
    sections: dict[str, str] = {}
    for idx, label in enumerate(labels, start=1):
        node = tree.css_first(f"#con_one_{idx}")
        if node is None:
            continue
        body_text = node.text(separator="\n", strip=True)
        if not body_text:
            continue
        key = label.strip().rstrip("：:")
        if key:
            sections[key] = body_text

    research_tags: list[str] = []
    research_raw = ""
    for key in ("研究兴趣", "研究方向", "研究领域"):
        if key in sections and sections[key].strip():
            research_raw = sections[key]
            research_tags = _split_interests(research_raw)
            if research_tags:
                break

    # bio_text preference: explicit 个人简介 (rare on infosec) > prose collected
    # from <div class="w510"> right column above.
    for key in ("个人简介", "简介"):
        if key in sections and sections[key].strip():
            bio = sections[key].strip()[:1500]
            break

    # --- scoped recruit/email/phone scan -----------------------------------
    scope_text = detail.text(separator=" ", strip=True)

    if not email:
        em, _ = extract_email(scope_text)
        if em and not em.startswith("scs@"):
            email = em

    recruit_chunks = find_recruit_paragraphs(scope_text)
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    email_obfuscated = email is None  # no plaintext email anywhere

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        email_obfuscated=email_obfuscated,
        phone=phone,
        photo_url=photo_url,
        homepage=profile_url,
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
class SjtuAdapter(SchoolAdapter):
    school_code = "sjtu"
    supports = {"cs", "see-ai", "ai", "qingyuan", "cse"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept == "cs":
            return _parse_list_cs(html, list_url)
        if dept == "ai":
            return _parse_list_ai(html, list_url)
        if dept == "see-ai":
            return _parse_list_see_ai(html, list_url)
        if dept == "qingyuan":
            return _parse_list_qingyuan(html, list_url)
        if dept == "cse":
            return _parse_list_cse(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        dept = _dept_from_url(profile_url)
        if dept == "cs":
            return _parse_profile_cs(html, profile_url, list_item)
        if dept == "ai":
            return _parse_profile_ai(html, profile_url, list_item)
        if dept == "see-ai":
            return _parse_profile_see_ai(html, profile_url, list_item)
        if dept == "qingyuan":
            return _parse_profile_qingyuan(html, profile_url, list_item)
        if dept == "cse":
            return _parse_profile_cse(html, profile_url, list_item)
        return _empty_partial(list_item, profile_url)
