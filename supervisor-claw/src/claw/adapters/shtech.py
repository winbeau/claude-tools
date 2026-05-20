"""ShanghaiTech (上海科技大学) adapter.

v0.4 covers a single department:

* ``sist`` — 信息科学与技术学院 (School of Information Science and Technology),
  the home of all CS / AI / EE / communications faculty under ShanghaiTech's
  single-college model. The school site lives at ``sist.shanghaitech.edu.cn``
  and is a **Sudy-CMS SPA** identical in shape to Fudan's ``cs.fudan.edu.cn``:
  the static ``/szdwx/list.htm`` shell carries no teacher rows, and the
  faculty grid is populated client-side from
  ``/_wp3services/generalQuery?queryObj=teacherHome`` (POST) by
  ``/_upload/tpl/01/ec/492/template492/js/cs_search.js``.

  Faculty are partitioned into 6 SPA "rank" tabs via the ``exField8`` field:

      常任教授   (tenure-track Professors / Associate / Assistant — ~88 PIs)
      特聘教授   (visiting / honorary chair professors — ~53)
      访问教授   (visiting professors — hidden by default)
      研究人员   (research staff — ~9, e.g. Senior / Associate Researchers)
      支撑人员   (technical support)
      行政人员   (administrative)

  We treat ``常任教授`` + ``特聘教授`` + ``研究人员`` as the PI universe and
  declare three POST list_urls in ``schools.yaml`` — the runner will fetch
  each and feed the JSON body to :meth:`parse_list`. Support / admin tabs
  are intentionally excluded (no advising role).

Known oddities
--------------
1. The static ``/szdwx/list.htm`` is the SPA shell — under plain GET it
   contains zero teacher anchors. The adapter therefore sniffs whether the
   body is JSON (the AJAX payload) and returns ``[]`` for the shell so a
   misconfigured pipeline doesn't accidentally drop valid faculty.

2. Profile URLs follow the per-PI subsite pattern
   ``http://sist.shanghaitech.edu.cn/<slug>/main.htm`` (e.g. ``/baiwb/main.htm``).
   The subsite homepage hosts the bio card under
   ``div.box > div.box_tp > div.box_fr``: name in ``div.fr_name`` (with a
   ``type="常任教授"`` attribute), title in ``div.fr_position``, and a
   ``div.fr_bt`` panel with labelled rows (博士毕业院校 / 电话 / 办公室 /
   邮箱 / 个人主页 / 专业方向 / 研究方向). Personal-bio prose lives in
   ``div.conn_box > div.conn1`` (the "简介" tab — index 1 of 12).

3. Emails are plain-text ``slug@shanghaitech.edu.cn`` for ~95% of PIs.
   特聘教授 (joint-appointment / honorary) sometimes carry their home-
   institution email instead (e.g. ``baoyg@ict.ac.cn`` for 包云岗 / 中科院计算所).

4. ``exField5`` is the research-center tag (e.g. 视觉与数据智能中心,
   智能网络中心, 后摩尔器件与集成系统中心, 智慧电气科学中心, 自动化与机器
   人中心, 智能医学信息研究中心, 系统与安全中心). We do not use it for
   filtering (everything in SIST is PI-level for v0.4) but surface it via
   the bio if no other prose is available.

5. 研究方向 on the profile is a single comma- or 中文逗号-separated string;
   we re-use the same ``_split_interests`` heuristic as the other adapters.
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


# ``exField8`` values that count as PI-level faculty. Support / admin /
# visiting student tiers are excluded — they don't advise PG students.
_PI_CATEGORIES: set[str] = {"常任教授", "特聘教授", "研究人员"}

# Even within the PI tabs above, the 研究人员 (research staff) bucket mixes
# 副研究员 / 助理研究员 (PI-level) with 博士后 / 博士后研究员 (postdocs, not
# advisors). Reject any row whose exField1 contains a non-PI keyword so the
# downstream pipeline never sees them.
_NON_PI_TITLE_KEYWORDS: tuple[str, ...] = (
    "博士后", "Postdoc", "Postdoctoral",
    "Visiting Scholar", "访问学者", "访问学生",
    "PhD student", "硕士研究生", "博士研究生",
    "在校生", "研究助理",
)

# ``exField1`` keywords that identify a row as faculty even when the
# ``exField8`` category is missing. Mirrors the title-whitelist used in pku /
# sjtu — broad on purpose; the JSON endpoint already filters by category.
_FACULTY_TITLE_KEYWORDS: tuple[str, ...] = (
    "教授", "副教授", "助理教授", "讲师",
    "研究员", "副研究员", "助理研究员",
    "讲席", "特聘", "院士",
    "Professor", "Lecturer", "Researcher",
)

# Generic CMS placeholder image — ignore so downstream doesn't treat it as
# a real headshot.
_PLACEHOLDER_PHOTO_TOKENS: tuple[str, ...] = (
    "/_res/articleType/",
    "/template492/images/teacher",
    "default.png", "default.jpg",
)


_SPLIT_RE = re.compile(r"[、，,；;/]+")


def _is_tag(p: str) -> bool:
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")


def _split_interests(text: str) -> list[str]:
    """Split a research-interest string (single line, mixed separators) into
    discrete tags. Mirrors the tsinghua / pku adapter implementation."""
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


def _clean_email(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().strip("​‌‍ ")
    if "@" not in s:
        return None
    return s.lower()


def _looks_like_faculty(title: str | None) -> bool:
    if not title:
        # Many 特聘教授 / 研究人员 rows leave exField1 blank but still represent
        # PI-level appointments — accept them when the rank-tab filter already
        # ran upstream.
        return True
    return any(kw in title for kw in _FACULTY_TITLE_KEYWORDS)


def _looks_like_teacher_json(body: str) -> bool:
    head = body.lstrip()[:64]
    if not head.startswith("{"):
        return False
    return '"data"' in head[:200] or '"total"' in head[:200]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class ShtechAdapter(SchoolAdapter):
    school_code = "shtech"
    supports = {"sist"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        # The only valid list payload is the teacherHome JSON. Static
        # /szdwx/list.htm shells return [] so the pipeline doesn't surface
        # phantom nav links as faculty.
        if _looks_like_teacher_json(html):
            return _parse_list_json(html, list_url)
        return []

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        return _parse_profile_subsite(html, profile_url, list_item)


# ---------------------------------------------------------------------------
# List — POST /_wp3services/generalQuery?queryObj=teacherHome JSON envelope
# ---------------------------------------------------------------------------


def _parse_list_json(payload: str, list_url: str) -> list[ListItem]:
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return []
    rows = obj.get("data") or []
    items: list[ListItem] = []
    seen_urls: set[str] = set()
    for r in rows:
        name = (r.get("title") or "").strip()
        if not name:
            continue
        # SIST hosts a handful of international PIs (Boris Houska, Laurent
        # Kneip, Sören Schwertfeger, Xavier Lagorce, ...) whose JSON `title`
        # field is the pure Latin name. We accept them as long as the row
        # passes the category / title-rank filter below.
        category = (r.get("exField8") or "").strip()
        # If category is set, only keep PI tiers. If blank, fall through to
        # title-keyword filtering — some legacy rows omit exField8.
        if category and category not in _PI_CATEGORIES:
            continue
        title = (r.get("exField1") or "").strip() or None
        if title and any(bad in title for bad in _NON_PI_TITLE_KEYWORDS):
            # Postdocs / visiting scholars / students appear in the 研究人员
            # tab — they have to be filtered explicitly because the JSON
            # category alone doesn't separate them from PI-level researchers.
            continue
        if not _looks_like_faculty(title) and category not in _PI_CATEGORIES:
            continue
        cn_url = (r.get("cnUrl") or "").strip()
        profile = absolutize(list_url, cn_url) if cn_url else None
        # The JSON URL is the POST endpoint, not a browseable base — when
        # `cnUrl` is already absolute (always the case for SIST), use it
        # verbatim. ``absolutize`` is still safe (urljoin returns the abs
        # URL unchanged).
        header = (r.get("headerPic") or "").strip()
        if header and not any(tok in header for tok in _PLACEHOLDER_PHOTO_TOKENS):
            photo = absolutize(list_url, header) if "://" not in header else header
        else:
            photo = None
        phone = (r.get("phone") or "").strip() or None
        email = _clean_email(r.get("email"))
        if profile and profile in seen_urls:
            continue
        if profile:
            seen_urls.add(profile)
        items.append(
            ListItem(
                name_cn=name,
                profile_url=profile,
                title=title,
                email=email,
                phone=phone,
                photo_url=photo,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Profile — per-PI subsite at /<slug>/main.htm
# ---------------------------------------------------------------------------


# Map the bio-card label text to a logical field. Labels in the markup carry
# trailing full-width / half-width colons + a leading space; we normalise
# before lookup.
_BIO_LABELS: dict[str, str] = {
    "博士毕业院校": "school",
    "电话": "phone",
    "办公室": "office",
    "邮箱": "email",
    "Email": "email",
    "个人主页": "homepage",
    "专业方向": "field",
    "单位": "unit",
    "所属课题组": "group",
    "研究方向": "research",
    "招聘主页": "recruit",
}


def _parse_profile_subsite(
    html: str, profile_url: str, list_item: ListItem
) -> AdvisorPartial:
    tree = parse(html)
    # The PI card is the first ``div.box`` inside the article entry. Falling
    # back to the wider ``div.read`` (or ``div.col_news``) catches templates
    # that drop the inner box wrapper.
    card = (
        tree.css_first("div.box div.box_tp div.box_fr")
        or tree.css_first("div.box_fr")
        or tree.css_first("div.read")
    )
    conn = tree.css_first("div.conn_box div.conn1") or tree.css_first("div.conn1")

    name = list_item.name_cn
    title = list_item.title
    email = list_item.email
    email_obf = False
    phone = list_item.phone
    photo_url = list_item.photo_url
    homepage: str | None = None
    research_text = ""
    bio: str | None = None

    if card is not None:
        name_node = card.css_first("div.fr_name")
        if name_node is not None:
            n = text_of(name_node)
            if n:
                name = n
        pos_node = card.css_first("div.fr_position")
        if pos_node is not None:
            t = text_of(pos_node)
            if t and not title:
                title = t

        # Iterate the labelled rows inside div.fr_bt.
        fr_bt = card.css_first("div.fr_bt") or card
        for row in fr_bt.css("div"):
            cls = (row.attributes.get("class") or "").strip()
            # Skip the wrapper itself + the noise blocks.
            if not cls or cls.startswith("mains"):
                continue
            # row text looks like "电话： 021-20684556" — split on the colon.
            row_text = text_of(row)
            if not row_text:
                continue
            label, sep, value = row_text.partition("：")
            if not sep:
                # Some templates use the half-width ":"; try that too.
                label, sep, value = row_text.partition(":")
            if not sep:
                continue
            label = label.strip()
            value = value.strip()
            field = _BIO_LABELS.get(label)
            if not field:
                continue
            if field == "phone" and value:
                phone = phone or value
            elif field == "email" and value:
                cleaned = _clean_email(value)
                if cleaned and not email:
                    email = cleaned
            elif field == "homepage":
                # The link may be inside a child <a>; prefer the href over
                # the visible text (which often duplicates the URL).
                link = row.css_first("a")
                if link is not None:
                    href = (link.attributes.get("href") or "").strip()
                    if href.startswith("http"):
                        homepage = href
                elif value.startswith("http"):
                    homepage = value
            elif field == "research" and value:
                research_text = value

    # Photo: first ``<img>`` under the article body. The list-item already
    # carries the JSON ``headerPic`` so we only overwrite if missing.
    if photo_url is None:
        img = tree.css_first("div.box_fl img") or tree.css_first("div.img_box img")
        if img is not None:
            src = (img.attributes.get("src") or "").strip()
            if src and not any(tok in src for tok in _PLACEHOLDER_PHOTO_TOKENS):
                photo_url = absolutize(profile_url, src)

    research_tags = _split_interests(research_text)

    if conn is not None:
        bio_text = conn.text(separator=" ", strip=True) or ""
        bio_text = bio_text.strip()
        if bio_text:
            bio = bio_text[:1500]

    # Email fallback: scan the bio-card text. Skip the school footer email
    # ``sist@shanghaitech.edu.cn`` which appears in nav remnants.
    if not email and card is not None:
        scope_text = card.text(separator=" ", strip=True) or ""
        em, was_obf = extract_email(scope_text)
        if em and not em.startswith(("sist@", "shanghaitech@")):
            email = em
            email_obf = was_obf

    # Recruit detection — scan only the card + bio, never the global nav.
    scope_for_recruit = ""
    if card is not None:
        scope_for_recruit += card.text(separator=" ", strip=True) or ""
    if bio:
        scope_for_recruit += " " + bio
    recruit_chunks = find_recruit_paragraphs(scope_for_recruit) if scope_for_recruit else []
    raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None
    is_recruiting = True if recruit_chunks else None

    return AdvisorPartial(
        name_cn=name,
        title=title,
        email=email,
        email_obfuscated=email_obf,
        phone=phone,
        photo_url=photo_url,
        homepage=homepage or profile_url,
        bio_text=bio,
        research_interests=research_tags,
        raw_quota_text=raw_quota_text,
        is_recruiting=is_recruiting,
        source_url=profile_url,
    )
