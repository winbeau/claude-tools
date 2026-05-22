"""xidian-specific email backfill strategy.

faculty.xidian.edu.cn encrypts every email field as
``<span class="encrypt-field" _tsites_encrypt_field>HEXBLOB</span>`` and
populates the plain-text only after ``/system/resource/tsites/tsitesencrypt.js``
runs in the browser. The Sudy-CMS tsites cipher is not publicly documented
and the per-page session key is buried in obfuscated JS — see
:func:`claw.core.email_decoders.decrypt_tsites_hex` for the (best-effort)
pure-Python attempts.

Strategy
--------
* If the advisor has a ``faculty.xidian.edu.cn`` profile URL → ``js`` first
  (tries pure-Python decode, then waits for browser JS, then generic DOM).
* Web-only personal homepages (``web.xidian.edu.cn/<userid>/``) → skip js;
  go straight to bing+dblp because the page template doesn't carry the
  tsites blobs.
* List-only advisors (``homepage`` IS NULL) → bing + dblp; the dept list
  pages never expose the email.

This module is a thin wrapper around
:func:`claw.enrichers.email_backfill.backfill_one_advisor` — it chooses a
sensible per-advisor strategy list and forces ``domain_hint="xidian.edu.cn"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...core.logging import get_logger
from ..email_backfill import backfill_one_advisor

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from playwright.async_api import Page

    from ...models.db import Advisor

log = get_logger(__name__)


def _pick_strategies(advisor: "Advisor") -> list[str]:
    """Decide which strategies are usable for this advisor.

    * No homepage URL → list-only entry, no JS can run → ``[bing, dblp]``.
    * homepage on ``web.xidian.edu.cn`` (personal sites) → JS decode path
      doesn't apply (template differs) → ``[bing, dblp]``.
    * homepage on ``faculty.xidian.edu.cn`` (the central template) → full
      cascade ``[js, bing, dblp]``.
    """
    url = (getattr(advisor, "homepage", None) or
           getattr(advisor, "source_url", None) or "")
    url_l = url.lower()
    if not url_l:
        return ["bing", "dblp"]
    if "faculty.xidian.edu.cn" in url_l:
        return ["js", "bing", "dblp"]
    # web.xidian.edu.cn personal sites + any non-tsites template
    return ["bing", "dblp"]


async def find_email(
    advisor: "Advisor",
    page: "Page",
    sess: "httpx.AsyncClient",
    school_name_cn: str,
) -> tuple[Optional[str], Optional[str]]:
    """Run the xidian-tuned email backfill cascade for one advisor.

    Returns ``(email, source)``; both ``None`` if nothing was found.

    ``source`` mirrors :func:`backfill_one_advisor` values:
    ``js_decode`` / ``bing`` / ``dblp``.
    """
    strategies = _pick_strategies(advisor)
    log.debug(
        "xidian.find_email: name=%s homepage=%s strategies=%s",
        getattr(advisor, "name_cn", "?"),
        getattr(advisor, "homepage", None),
        strategies,
    )
    return await backfill_one_advisor(
        advisor=advisor,
        page=page,
        sess=sess,
        school_code="xidian",
        school_name_cn=school_name_cn,
        strategies=strategies,
    )


__all__ = ["find_email"]
