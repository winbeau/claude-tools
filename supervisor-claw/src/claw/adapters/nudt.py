"""National University of Defense Technology (NUDT, 国防科技大学) adapter.

NUDT is the flagship military university (军校) of the PLA and one of
the "国防七子". Its public web presence is *deliberately minimal*:

* The college homepages (``yssz/jsjxy/`` 计算机学院, ``yssz/znkxxy/``
  智能科学学院, ``yssz/xtgcxy/`` 系统工程学院) **do not expose a 师资
  directory** — every internal "师资" navigation link bounces back to
  the university-level talent pages under ``www.nudt.edu.cn/szdw/``.
* The per-rank teacher pages on the central portal list only *names*
  (院士、长江、杰青、优青、百千万、求是青年、全国优秀科技工作者) plus
  elected year. There are no ``<a href>`` profile URLs, no emails, no
  research interests, no photos. Many senior researchers also never
  appear on the public site (内网only).
* Per-school sub-domains (``jsjkx.nudt.edu.cn`` etc.) either 404 or
  are not exposed externally.

This adapter therefore declares a single synthetic ``cs`` department
that vacuums every public-honour roster on ``www.nudt.edu.cn/szdw/``
and emits ``ListItem(name_cn, title=<honour label>)`` rows with
``profile_url=None``. Downstream the pipeline upserts on
``(school, name_cn, email)`` so the same person showing up under
multiple honours collapses to one advisor; the title carries the
*highest* honour we observed (priority order in
``_HONOUR_PRIORITY`` below).

Known limitations (v0.4)
------------------------
1. **Coverage is honours-only**, ~10-20 unique PIs after dedup, not
   the full 计算机/智能/系统工程 faculty (which is conservatively
   100+). The non-honoured PI population is not publicly enumerable.
2. **No profile pages.** ``parse_profile`` is essentially a no-op —
   it returns list-item data verbatim with an honour-label bio. The
   pipeline already short-circuits when ``profile_url`` is None.
3. **No emails, no research_interests, no招生 signals.** v0.3
   DeepSeek enricher / v0.5 Playwright (against intranet-aware
   mirrors) are the only paths to fill these in.
4. **Department attribution is unavailable.** The honour pages never
   say which 学院 a person belongs to; we leave dept resolution to
   the v0.3 enricher (which can cross-reference Baidu Scholar /
   public news for affiliations).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..core.parser_utils import parse, text_of
from ..models.pydantic_models import AdvisorPartial
from .base import ListItem, SchoolAdapter, register

# ---------------------------------------------------------------------------
# Honour-label → title mapping. Priority order is highest-honour-first
# so the dedupe logic below picks the most prestigious label when one
# person appears across multiple talent pages.
# ---------------------------------------------------------------------------

_HONOUR_PRIORITY: tuple[tuple[str, str], ...] = (
    # URL path fragment, title hint
    ("/lyys/", "院士"),
    ("/gjjcqnkxjjhdz/", "国家杰青"),
    ("/gjyxqnkxjj/", "国家优青"),
    ("/bqwrcgcgjjrx/", "百千万人才工程国家级人选"),
    ("/gjkxqsjcqnsygcjhdz/", "求是杰出青年实用工程奖"),
    ("/qgyxkjgzz/", "全国优秀科技工作者"),
)

# Section heads inside the 两院院士 page (中国科学院院士 / 中国工程院院士).
_LYYS_SECTION_TITLES = {
    "中国科学院院士": "中国科学院院士",
    "中国工程院院士": "中国工程院院士",
}


def _honour_for_url(url: str) -> str:
    """Return the talent-category title for a list_url path."""
    for frag, title in _HONOUR_PRIORITY:
        if frag in url:
            return title
    return "NUDT 公开师资"


def _is_chinese_name(s: str) -> bool:
    """A faculty name is 2-4 Han characters with no embedded ASCII."""
    s = s.strip()
    if not (2 <= len(s) <= 4):
        return False
    return all("一" <= c <= "鿿" for c in s)


# ---------------------------------------------------------------------------
# List parser
# ---------------------------------------------------------------------------


def _walk_for_year(li_node) -> str | None:
    """Extract the ``<div class="year">YYYY年</div>`` from a faculty li."""
    y = li_node.css_first("div.year")
    if y is None:
        return None
    t = text_of(y)
    if not t:
        return None
    # Strip the trailing "年" so downstream consumers get a 4-digit string.
    m = re.match(r"(\d{4})", t)
    return m.group(1) if m else None


def _parse_talent_list(html: str, list_url: str) -> list[ListItem]:
    """Parse a single talent-roster page on ``www.nudt.edu.cn/szdw/...``.

    Layout (Sudy-style CMS):

        <h3 class="gp-f24">中国科学院院士</h3>     <!-- only on lyys/ -->
        <ul class="facultyList1">
          <li class="gp-f24">
            <div class="year">2025年</div>
            <div class="teachers">
              <a>胡德文</a>
              <a>李四</a>            <!-- some years list multiple names -->
            </div>
          </li>
          ...
        </ul>
        <h3 class="gp-f24">中国工程院院士</h3>     <!-- second section -->
        <ul class="facultyList1">...</ul>

    Strategy: walk every ``ul.facultyList1`` in document order; for
    each preceding ``h3.gp-f24`` (if any) record the section title;
    pull ``div.teachers a`` text as names. ``profile_url`` is
    intentionally None — these pages never link to per-PI pages.
    """
    tree = parse(html)
    items: list[ListItem] = []
    seen: set[str] = set()

    honour = _honour_for_url(list_url)

    # Walk the document recording section state. We approximate the
    # "preceding h3" by iterating top-level nodes inside the main
    # ``.facultyInfo`` container and tracking the latest h3 text.
    container = (
        tree.css_first("div.faculty")
        or tree.css_first("div.academician")
        or tree.body
    )
    if container is None:
        return []

    current_section: str | None = None
    for node in container.traverse(include_text=False):
        tag = node.tag
        if tag == "h3":
            t = text_of(node)
            if t in _LYYS_SECTION_TITLES:
                current_section = _LYYS_SECTION_TITLES[t]
            else:
                # Other h3 (page title etc.) doesn't change section.
                current_section = current_section
            continue
        if tag != "ul":
            continue
        cls = (node.attributes.get("class") or "").strip()
        if "facultyList1" not in cls:
            continue
        for li in node.css("li"):
            year = _walk_for_year(li)
            teachers = li.css_first("div.teachers")
            if teachers is None:
                continue
            for a in teachers.css("a"):
                name = text_of(a)
                if not _is_chinese_name(name):
                    continue
                # Same name on the same page — dedupe (some honours
                # list a person in multiple year buckets).
                if name in seen:
                    continue
                seen.add(name)
                # title: section overrides generic honour for the
                # 院士 page (CAS vs CAE matters); otherwise the
                # talent-page honour label.
                title = current_section or honour
                # Stash the elected-year hint in phone-free metadata
                # — we attach it to the title only when we have a
                # bare honour (the section labels already include
                # 院士 so adding 2025 doesn't help downstream).
                if year and title in ("国家杰青", "国家优青"):
                    title = f"{title}({year})"
                items.append(
                    ListItem(
                        name_cn=name,
                        profile_url=None,
                        title=title,
                    )
                )
    return items


# ---------------------------------------------------------------------------
# Profile parser
# ---------------------------------------------------------------------------


def _empty_partial(list_item: ListItem, profile_url: str) -> AdvisorPartial:
    """Build a list-only AdvisorPartial with an honour-label bio.

    The pipeline normally bypasses ``parse_profile`` when
    ``profile_url`` is None (see ``pipeline/runner.py``), so this
    function is mainly defensive: if a future enricher synthesises a
    per-PI URL and the fetch round-trips back here, we should at
    least not crash, and we should surface the honour label as
    bio_text so the DB has *something* to display.
    """
    bio = None
    if list_item.title:
        bio = f"{list_item.name_cn}，{list_item.title}（来源：国防科技大学公开师资页）"
    return AdvisorPartial(
        name_cn=list_item.name_cn,
        title=list_item.title,
        email=list_item.email,
        phone=list_item.phone,
        photo_url=list_item.photo_url,
        homepage=profile_url,
        bio_text=bio,
        source_url=profile_url,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class NudtAdapter(SchoolAdapter):
    school_code = "nudt"
    # Only one synthetic department — see module docstring.
    supports = {"cs"}

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        host = (urlparse(list_url).hostname or "").lower()
        # Defensive: only the central www.nudt.edu.cn host carries
        # the talent rosters our parser understands.
        if host and "nudt.edu.cn" not in host:
            return []
        return _parse_talent_list(html, list_url)

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        # NUDT has no per-PI profile pages — this is a no-op safety
        # net. We never inspect ``html`` because the only thing
        # behind a synthesised URL would be a school news article or
        # an intranet redirect we can't make sense of.
        return _empty_partial(list_item, profile_url)
