"""Beihang University (BUAA) adapter.

v0.4 covers three departments hosted on three sibling sub-domains:

* ``cs``  — 计算机学院 (scse.buaa.edu.cn). 师资队伍 is split across three
  rank-based listings under ``/szdw/qtjs/``: ``js.htm`` (教授, ~80 / 7 pages),
  ``fjs.htm`` (副教授, ~63 / 6 pages), ``js1.htm`` (讲师/助理教授, ~20 / 2 pages).
  Each list page renders 12 cards as ``<li><a href="../../info/.../X.htm">``
  with a name + 职称 inside ``div.img_out`` and an email inside
  ``<p class="yx">``.

* ``sw``  — 软件学院 (soft.buaa.edu.cn). VSB SiteBuilder shell with
  jsp routes — ``tu-list-1.jsp?wbtreeid={1224|1262|1263}`` for 教授 /
  副教授 / 助理教授. Cards expose only ``<a title>`` + name + photo;
  contact info lives on the profile page.

* ``ai``  — 人工智能研究院 (iai.buaa.edu.cn). Faculty are grouped under
  ``/szdw/ayjsdsjs.htm`` (按研究生导师, ~54 entries, the largest single
  listing) plus ``jcrc.htm`` (杰出人才). The list-page card is a flat
  ``<li><a href="info/.../X.htm" target=_blank title="姓名"><div
  class="pic"><img></div><p>姓名</p></a></li>`` — name + photo only.

Known oddities
--------------
1. **scse.buaa.edu.cn pagination is reversed.** The landing
   ``js.htm`` is page 1 (newest entries on top); deeper pages are
   ``js/6.htm`` (page 2) … ``js/1.htm`` (last page). schools.yaml lists
   the descending sequence explicitly; the adapter treats every page
   uniformly.
2. **scse profiles have no section headers.** The whole bio is one
   ``<p class="vsbcontent_start">`` blob inside ``div.v_news_content``;
   research direction is embedded inline ("主要研究方向包括 X、Y、Z"
   etc.). We surface tags by regex over the bio.
3. **scse head card uses ``<span>label：value</span>``** instead of
   ``<p>`` tags. We pull ``电子邮箱`` / ``职称`` / ``座机`` / ``个人主页``
   from those spans.
4. **soft profile head uses ``<dl><dd><font>label</font>value</dd>``**
   for 职称 / 学历 / 办公室 / 电话 / 岗位 / 电子信箱 — a `<font>` tag
   instead of `<strong>`. The bio sits in ``div.ar_article`` /
   ``div#vsb_content_NNN`` with pseudo-headers ``<font color>label：</font>``.
5. **soft names sometimes carry "（双聘）" suffix** — kept as-is on the
   theory that duplicate-shadow appointments are real research advisors
   too; the pipeline upsert layer dedupes by (school, name, email).
6. **iai profile head uses ``<p>姓　　名：X</p>``** lines inside
   ``div.titbox`` (full-width spaces in label). The body is one
   ``div#vsb_content`` blob with ``<strong>★科研概况</strong>`` /
   ``★联系方式`` pseudo-headers (note the ★ glyph prefix).
7. **iai email** is plain text inside the body after ``邮箱：``; never
   image-obfuscated on the sample we captured.
8. **scse / iai / soft all share VsbCMS plumbing** — same favicon, same
   ``/system/resource/js/*`` scripts, same ``vsb_content`` containers
   (with slightly different surrounding wrappers). The adapter relies
   only on the inner ``v_news_content`` / ``vsb_content`` containers
   plus the per-dept head-card selectors.
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
    "院士", "Professor", "Lecturer", "Researcher",
)

# Generic pseudo-section labels (BUAA mostly uses the ★X / 个人简介 style,
# but we keep the union for forward-compat with sibling sites).
_PSEUDO_HEADERS: set[str] = {
    "研究方向", "研究兴趣", "研究领域", "主要研究方向", "主要研究领域",
    "Research Interests",
    "研究概况", "科研概况", "个人简介", "简介", "Bio", "Biography",
    "教育背景", "工作经历", "经历", "教育教学",
    "学术兼职", "社会兼职", "任职",
    "招生信息", "招生", "招生招聘",
    "讲授课程", "教学概况", "本科课程", "研究生课程", "课程",
    "代表性成果", "代表论著", "代表论文", "学术成果", "论文",
    "奖励与荣誉", "荣誉", "获奖",
    "研究课题", "科研项目", "项目",
    "联系方式",
}

_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Same logic as the tsinghua / pku adapters."""
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


# ---------------------------------------------------------------------------
# Dept routing
# ---------------------------------------------------------------------------


def _dept_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "scse.buaa.edu.cn" in host or "cs.buaa.edu.cn" in host:
        return "cs"
    if "soft.buaa.edu.cn" in host or "sse.buaa.edu.cn" in host:
        return "sw"
    if "iai.buaa.edu.cn" in host:
        return "ai"
    return "cs"  # safe default — CS is the largest


# ---------------------------------------------------------------------------
# List parsers
# ---------------------------------------------------------------------------


def _parse_list_cs(html: str, list_url: str) -> list[ListItem]:
    """scse.buaa.edu.cn 师资队伍 list page.

    Layout per card:
        <li>
          <a href="../../info/1078/2627.htm" title="李未">
            <div class="img_out">
              <div class="img">
                <div class="jstbox"><img src="/__local/..."></div>
                <p>
                  李未  <span>教授</span>      <!-- or with extra <span>姓名</span> wrapper -->
                </p>
              </div>
            </div>
            <div class="con">
              <p class="dh"></p>
              <p class="yx">email@buaa.edu.cn</p>
            </div>
          </a>
        </li>

    Profile URLs follow ``/info/<treeid>/<news_id>.htm``; ``treeid=1078`` is
    the dept-wide bucket but a few legacy 杰出人才 entries use other treeids
    — we accept any ``/info/`` URL.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or "/info/" not in href:
            continue
        # require an "img_out" descendant — that's the card structure marker
        if a.css_first("div.img_out") is None:
            continue
        # Prefer the title attribute (always the bare name); fall back to
        # the first <span> with name (李未 page wraps name in a span).
        name = (a.attributes.get("title") or "").strip()
        if not name:
            name_p = a.css_first("div.img p")
            if name_p is not None:
                # take everything before the <span>职称</span>
                # text() of <p> joins with separator='' losing the
                # name/title boundary, so iterate children.
                parts: list[str] = []
                for ch in name_p.iter():
                    if ch.tag == "span":
                        break
                    t = text_of(ch)
                    if t:
                        parts.append(t)
                name = "".join(parts).strip()
        if not name or len(name) > 20:
            continue
        # title is the inner <span> text inside the <p>
        title: str | None = None
        title_node = a.css_first("div.img p span")
        if title_node is not None:
            t = text_of(title_node)
            # Skip the "border:1px solid" decorated 姓名 span (李未 page).
            if t and t != name and any(kw in t for kw in _FACULTY_TITLE_KEYWORDS):
                title = t
            elif t and t != name and len(t) <= 12:
                # Some cards put the title in the *second* span without a
                # faculty keyword — keep if it looks role-like.
                title = t
        # email lives in <p class="yx"> inside div.con
        email: str | None = None
        yx = a.css_first("p.yx")
        if yx is not None:
            ytext = text_of(yx)
            if ytext:
                em, _ = extract_email(ytext)
                if em:
                    email = em
        # photo
        img = a.css_first("div.jstbox img") or a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = img.attributes.get("src") or ""
            if src:
                photo = absolutize(list_url, src)
        if title and not _looks_like_faculty(title):
            continue
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
                photo_url=photo,
            )
        )
    return items


# Strip the soft-school dual-appointment marker so that downstream dedup
# matches across departments.
_SW_NAME_SUFFIX_RE = re.compile(r"\s*[（(](?:双聘|外聘|兼聘)[）)]\s*$")


def _parse_list_sw(html: str, list_url: str) -> list[ListItem]:
    """soft.buaa.edu.cn 师资队伍 list page (``tu-list-1.jsp``).

    Layout per card:
        <li>
          <a href="teachershouw.jsp?urltype=news.NewsContentUrl&wbtreeid=...&wbnewsid=NNNN"
             title="姓名">
            <div class="pic slow"><img src="/__local/..."></div>
            <div class="jsml_name">姓名</div>           <!-- may carry （双聘） suffix -->
          </a>
        </li>

    No title / email on the list page; everything lives on the profile.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or "teachershouw.jsp" not in href:
            continue
        name = (a.attributes.get("title") or "").strip()
        if not name:
            name_node = a.css_first("div.jsml_name")
            name = text_of(name_node)
        if not name:
            continue
        # Drop the （双聘）/（外聘）/（兼聘） suffix so dedup matches across
        # departments; keep the original in raw_quota_text only if needed.
        name = _SW_NAME_SUFFIX_RE.sub("", name).strip()
        if not name or len(name) > 20:
            continue
        img = a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = img.attributes.get("src") or ""
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
                photo_url=photo,
            )
        )
    return items


def _parse_list_ai(html: str, list_url: str) -> list[ListItem]:
    """iai.buaa.edu.cn 师资队伍 list page (``szdw/jcrc.htm``, ``ayjsdsjs.htm`` etc.).

    Layout per card:
        <li>
          <a href="https://iai.buaa.edu.cn/info/1013/3252.htm"
             target="_blank" title="姓名">
            <div class="pic slow"><img src="/__local/..."></div>
            <p>姓名</p>
          </a>
        </li>

    The list page also contains group headers (``<div class="lmmc">两院院士</div>``,
    ``国家级人才及国家奖第一完成人`` …); the same person can show up under
    multiple groups on ``jcrc.htm`` — we dedupe by profile URL.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()
    # Constrain to anchors inside the main faculty container to avoid the
    # mobile nav re-rendering teacher links elsewhere on the page.
    main = tree.css_first("div.listpeople") or tree.css_first("div.mainBox") or tree.body
    if main is None:
        return items
    for a in main.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or "/info/" not in href:
            continue
        # Reject anchors that don't carry a name (e.g. wrappers around <img>
        # alone). title attribute is the canonical name source.
        name = (a.attributes.get("title") or "").strip()
        if not name:
            p_node = a.css_first("p")
            name = text_of(p_node)
        if not name:
            continue
        # Reject obvious non-faculty navigation/breadcrumb text.
        if not any("一" <= c <= "鿿" for c in name):
            continue
        if len(name) > 20:
            continue
        img = a.css_first("img")
        photo: str | None = None
        if img is not None:
            src = img.attributes.get("src") or ""
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
                photo_url=photo,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile parsers
# ---------------------------------------------------------------------------


# Regex helpers shared across profile parsers.
_INLINE_RESEARCH_RE = re.compile(
    r"研究(?:方向|兴趣|领域|概况)[为是:：]?\s*([^。\n]{2,200})"
)
# scse profiles often phrase as: "主要研究方向包括 X、Y、Z 等" / "...等领域的研究"
_INLINE_INCLUDE_RE = re.compile(
    r"研究(?:方向|领域|兴趣)(?:包括|为|是|涉及)[：:]?\s*([^。\n]{2,200})"
)


def _extract_inline_research(text: str) -> list[str]:
    """Find a "主要研究方向：xxx" / "研究方向包括 xxx" phrase in prose."""
    for rx in (_INLINE_INCLUDE_RE, _INLINE_RESEARCH_RE):
        m = rx.search(text)
        if m:
            tags = _split_interests(m.group(1))
            if tags:
                return tags
    return []


def _parse_profile_cs(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """scse.buaa.edu.cn profile.

    Head card:
        <div class="detail2_t">
          <div class="detail2_t_l"><img src="..."></div>
          <div class="detail2_t_r">
            <span>姓名：钱德沛 </span>
            <span>职称：教授</span>
            <span>座机：</span>
            <span>邮编：100191 </span>
            <span>办公地址：</span>
            <span>电子邮箱：depeiq@buaa.edu.cn</span>
            <span>个人主页：</span>
          </div>
        </div>

    Body: one long ``<p class="vsbcontent_start">…</p>`` inside
    ``div.v_news_content``. No section headers — research direction is
    woven into the bio prose.
    """
    tree = parse(html)
    head = tree.css_first("div.detail2_t")
    content = tree.css_first("div.v_news_content")
    if content is None and head is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None

    if head is not None:
        img = head.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        for span in head.css("div.detail2_t_r span"):
            t = text_of(span)
            if not t:
                continue
            val = t.split("：", 1)[-1].strip() if "：" in t else ""
            if t.startswith("姓名") and not name:
                name = val or name
            elif t.startswith("职称") and not title:
                title = val or None
            elif t.startswith("座机") or t.startswith("电话"):
                if val and not phone:
                    phone = val
            elif t.startswith("电子邮箱") or t.startswith("邮箱"):
                if not email:
                    em, _ = extract_email(val or t)
                    if em:
                        email = em
            elif t.startswith("个人主页") or t.startswith("主页"):
                link = span.css_first("a")
                if link is not None:
                    h = link.attributes.get("href") or ""
                    if h.startswith("http"):
                        homepage = h
                elif val.startswith("http"):
                    homepage = val

    bio: str | None = None
    research_tags: list[str] = []
    scope_text = ""
    if content is not None:
        scope_text = content.text(separator=" ", strip=True)
        # bio: take the first .vsbcontent_start <p> if available, else
        # the first non-empty paragraph.
        bio_p = content.css_first("p.vsbcontent_start") or content.css_first("p")
        if bio_p is not None:
            bio_text = bio_p.text(separator=" ", strip=True)
            if bio_text:
                bio = bio_text[:1500]
        # research tags: regex over the whole content.
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
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# Soft-school 头部 dl/dd 键值 (label is wrapped in <font>label</font> value)
_SW_HEAD_LABEL_MAP: dict[str, str] = {
    "职": "title",
    "学": "edu",
    "办": "office",
    "电": "phone_or_email",
    "岗": "post",
    "电子信箱": "email",
}


def _parse_profile_sw(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """soft.buaa.edu.cn profile.

    Head card:
        <div class="fl01">
          <div class="left"><div class="img"><img></div></div>
          <div class="right">
            <div class="ll">姓名</div>
            <dl>
              <dd><font>职&emsp;&emsp;称： </font>教授</dd>
              <dd><font>学&emsp;&emsp;历： </font>博士研究生</dd>
              <dd><font>办&ensp;公&ensp;室： </font>新主楼C304</dd>
              <dd><font>电&emsp;&emsp;话： </font>82339679/...</dd>
              <dd><font>岗&emsp;&emsp;位： </font>院长</dd>
              <dd><font>电子信箱： </font>hucm@buaa.edu.cn</dd>
            </dl>
          </div>
        </div>

    Body: ``div.fl02 > div.ar_article`` contains ``<font color>label：</font>``
    pseudo-headers followed by ``<p>body</p>`` paragraphs; some text is also
    inside a nested ``div#vsb_content_N`` block.
    """
    tree = parse(html)
    head = tree.css_first("div.fl01")
    body_outer = tree.css_first("div.fl02") or tree.css_first("section.n_shizi_detail")
    if head is None and body_outer is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None

    if head is not None:
        img = head.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        ll = head.css_first("div.ll")
        if ll is not None:
            ll_text = text_of(ll)
            if ll_text and not list_item.name_cn:
                name = ll_text
        # Each <dd> has the form "<font>label：</font>value"; text() merges
        # them so we split on the first "：".
        for dd in head.css("dd"):
            t = text_of(dd)
            if not t or "：" not in t:
                continue
            label_raw, value = t.split("：", 1)
            # Collapse full-width spaces / Chinese white-space variants.
            label = label_raw.replace(" ", "").replace(" ", "").replace(
                " ", ""
            ).replace("　", "")
            value = value.strip()
            if not value:
                continue
            if "职" in label and not title:
                if any(kw in value for kw in _FACULTY_TITLE_KEYWORDS) or len(value) <= 12:
                    title = value
            elif "电子信箱" in label or "电子邮箱" in label or "邮箱" in label:
                em, _ = extract_email(value)
                if em and not email:
                    email = em
            elif label.startswith("电") and "信箱" not in label and "邮" not in label:
                # 电话
                if not phone:
                    phone = value
            elif "主页" in label and value.startswith("http"):
                homepage = value

    bio: str | None = None
    research_tags: list[str] = []
    scope_text = ""
    if body_outer is not None:
        # Prefer the ar_article wrapper (drops the duplicate vsb_content
        # inner block that's also inside it).
        article = body_outer.css_first("div.ar_article") or body_outer
        # Walk paragraphs sequentially and treat any <p> whose text matches
        # a known label as a pseudo-header; collect body paragraphs until
        # the next header.
        sections = _split_font_sections(article)
        for key in ("个人简介", "简介", "Bio", "Biography"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1500]
                break
        for key in (
            "主要研究方向", "研究方向", "研究兴趣", "研究领域",
            "Research Interests",
        ):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags:
                    break
        scope_text = article.text(separator=" ", strip=True)
        # Fallback: regex over scope_text if no structured section worked.
        if not research_tags:
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
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


def _split_font_sections(content_node) -> dict[str, str]:
    """Soft-school body uses ``<p><font color="#0060aa">label：</font>body</p>``
    or ``<p><font>label：</font></p><p>body</p>`` style pseudo-headers.

    We walk ``<p>`` nodes in document order: if the paragraph starts with a
    ``<font>`` whose text matches a known label, treat it as a header and
    append subsequent paragraph text to that section until another header
    arrives.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    if content_node is None:
        return {}
    for n in content_node.traverse(include_text=False):
        if n.tag != "p":
            continue
        # Look at first child node — if it's a <font> whose text is a label,
        # the whole paragraph defines (or starts) a section.
        first_font = n.css_first("font")
        ptext = n.text(separator=" ", strip=True)
        if not ptext:
            continue
        # Pseudo-header detection: text up to first "：" matches a known label.
        norm_head: str | None = None
        if "：" in ptext:
            head, rest = ptext.split("：", 1)
            head = head.strip().rstrip(":：")
            if head and len(head) <= 14 and head in _PSEUDO_HEADERS:
                norm_head = head
                sections.setdefault(head, [])
                # If there's body content on the same line, capture it.
                rest = rest.strip()
                if rest:
                    sections[head].append(rest)
                current = head
                continue
        # Even without "：", a <font>...</font>-only paragraph is sometimes a
        # bare header (rare but seen on soft profiles).
        if first_font is not None:
            ftext = text_of(first_font).rstrip("：:").strip()
            if ftext and len(ftext) <= 14 and ftext in _PSEUDO_HEADERS:
                current = ftext
                sections.setdefault(current, [])
                continue
        if current is None:
            continue
        sections[current].append(ptext)
    return {k: "\n".join(v) for k, v in sections.items() if v or k in sections}


# IAI head-card labels: <p>姓　　名：X</p> / <p>现有职称：X</p> / <p>硕博导师：X</p>
_IAI_HEAD_LABEL_NAME = re.compile(r"^\s*姓\s*[　\s]*名\s*[:：]\s*(.+)$")
_IAI_HEAD_LABEL_TITLE = re.compile(r"^\s*现有职称\s*[:：]\s*(.+)$")
_IAI_HEAD_LABEL_GUIDE = re.compile(r"^\s*硕博导师\s*[:：]\s*(.+)$")
_IAI_HEAD_LABEL_HONOR = re.compile(r"^\s*人才称号\s*[:：]\s*(.+)$")


def _parse_profile_ai(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    """iai.buaa.edu.cn profile.

    Head card:
        <div class="titbox">
          <img src="...">
          <div>
            <p>姓　　名：邓岳</p>
            <p>现有职称：教授</p>
            <p>硕博导师：博士生导师</p>
            <p>人才称号：国家级人才</p>
          </div>
        </div>

    Body: ``div#vsb_content`` (or ``vsb_content_N``) containing prose with
    ``<strong>★科研概况</strong>`` / ``★联系方式`` pseudo-headers and a
    plain-text ``邮箱：x@y`` line near the bottom.
    """
    tree = parse(html)
    head = tree.css_first("div.titbox")
    body = tree.css_first("div#vsb_content") or tree.css_first("[id^='vsb_content']")
    if head is None and body is None:
        return _empty_partial(list_item, profile_url)

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    photo_url = list_item.photo_url

    if head is not None:
        img = head.css_first("img")
        if img is not None and photo_url is None:
            src = img.attributes.get("src") or ""
            if src:
                photo_url = absolutize(profile_url, src)
        for p in head.css("p"):
            t = text_of(p)
            if not t:
                continue
            m = _IAI_HEAD_LABEL_NAME.match(t)
            if m and not list_item.name_cn:
                name = m.group(1).strip()
                continue
            m = _IAI_HEAD_LABEL_TITLE.match(t)
            if m and not title:
                title = m.group(1).strip()
                continue
            # 硕博导师 / 人才称号 are kept implicitly in scope_text below.

    bio: str | None = None
    research_tags: list[str] = []
    scope_text = ""
    if body is not None:
        scope_text = body.text(separator="\n", strip=True)
        sections = _split_star_sections(body)
        for key in ("科研概况", "研究概况", "个人简介", "简介"):
            if key in sections and sections[key].strip():
                bio = sections[key].strip()[:1500]
                break
        # If no ★科研概况 section, fall back to the first prose paragraph.
        if bio is None:
            first_p = body.css_first("p")
            if first_p is not None:
                p_text = first_p.text(separator=" ", strip=True)
                if p_text:
                    bio = p_text[:1500]

        # Research direction: regex over the whole body text — IAI bios
        # spell it as "1.研究方向：xxx" or "研究方向包括 xxx".
        research_tags = _extract_inline_research(scope_text)
        # Also try pulling from the 科研概况 section if present.
        if not research_tags:
            for key in ("科研概况", "研究概况"):
                if key in sections:
                    research_tags = _extract_inline_research(sections[key])
                    if research_tags:
                        break

    if not email and scope_text:
        em, _ = extract_email(scope_text)
        if em and not em.startswith("iai@") and not em.startswith("xinxihua@"):
            email = em

    recruit_chunks = find_recruit_paragraphs(scope_text) if scope_text else []
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        phone=list_item.phone,
        photo_url=photo_url,
        homepage=profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=True if recruit_chunks else None,
        source_url=profile_url,
    )


# Section labels appearing on IAI profiles after the ★ glyph.
_STAR_HEADER_LABELS: set[str] = {
    "科研概况", "教育教学", "联系方式", "个人简介",
    "研究方向", "代表论著", "学术兼职", "招生信息",
}


def _split_star_sections(content_node) -> dict[str, str]:
    """IAI body has ``<p><strong>★科研概况</strong></p>`` pseudo-headers
    followed by body paragraphs until the next ★ block.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    if content_node is None:
        return {}
    for n in content_node.traverse(include_text=False):
        if n.tag != "p":
            continue
        ptext = n.text(separator=" ", strip=True)
        if not ptext:
            continue
        # Detect ★header. Body sometimes has trailing whitespace.
        m = re.match(r"^\s*[★☆]\s*(.+?)\s*$", ptext)
        if m:
            label = m.group(1).strip().rstrip("：:")
            # Also accept "★科研概况：xxx" form (with inline body)
            if "：" in label:
                label_head, rest = label.split("：", 1)
                label_head = label_head.strip()
                if label_head in _STAR_HEADER_LABELS:
                    current = label_head
                    sections.setdefault(current, [])
                    if rest.strip():
                        sections[current].append(rest.strip())
                    continue
            if label in _STAR_HEADER_LABELS:
                current = label
                sections.setdefault(current, [])
                continue
        if current is None:
            continue
        sections[current].append(ptext)
    return {k: "\n".join(v) for k, v in sections.items() if v or k in sections}


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
class BuaaAdapter(SchoolAdapter):
    school_code = "buaa"
    supports = {"cs", "sw", "ai"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        dept = _dept_from_url(list_url)
        if dept == "cs":
            return _parse_list_cs(html, list_url)
        if dept == "sw":
            return _parse_list_sw(html, list_url)
        if dept == "ai":
            return _parse_list_ai(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        dept = _dept_from_url(profile_url)
        if dept == "cs":
            return _parse_profile_cs(html, profile_url, list_item)
        if dept == "sw":
            return _parse_profile_sw(html, profile_url, list_item)
        if dept == "ai":
            return _parse_profile_ai(html, profile_url, list_item)
        return _empty_partial(list_item, profile_url)
