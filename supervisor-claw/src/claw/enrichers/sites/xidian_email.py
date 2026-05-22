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

Calls the helpers in :mod:`claw.enrichers.email_backfill` directly rather
than re-entering :func:`backfill_one_advisor` — re-entering would recurse
indefinitely via the ``_SITE_EMAIL_OVERRIDES`` dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...core.logging import get_logger
from ..email_backfill import (
    decode_js_email_on_page,
    dblp_email_lookup,
    search_email_via_stealth_bing,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from playwright.async_api import Page

    from ...models.db import Advisor

log = get_logger(__name__)

_DOMAIN_HINT = "xidian.edu.cn"


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
    return ["bing", "dblp"]


async def find_email(
    advisor: "Advisor",
    page: "Page",
    sess: "httpx.AsyncClient",
    school_name_cn: str,
) -> tuple[Optional[str], Optional[str]]:
    """Run the xidian-tuned email backfill cascade for one advisor.

    Returns ``(email, source)``; both ``None`` if nothing was found.
    """
    strategies = _pick_strategies(advisor)
    name = getattr(advisor, "name_cn", None)
    if not name:
        return None, None

    for strat in strategies:
        try:
            if strat == "js":
                url = (getattr(advisor, "homepage", None)
                       or getattr(advisor, "source_url", None))
                if not url:
                    continue
                try:
                    await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    log.warning("xidian js nav failed %s: %s", url, e)
                    continue
                email = await decode_js_email_on_page(page, "xidian")
                if email:
                    return email, "js_decode"

            elif strat == "bing":
                email = await search_email_via_stealth_bing(
                    page,
                    name=name,
                    school_name_cn=school_name_cn,
                    domain_hint=_DOMAIN_HINT,
                )
                if email:
                    return email, "bing"

            elif strat == "dblp":
                email = await dblp_email_lookup(
                    sess,
                    name=name,
                    affiliation_hint=school_name_cn,
                )
                if email:
                    return email, "dblp"
        except Exception as e:  # noqa: BLE001
            log.warning("xidian strategy %s raised for %s: %s", strat, name, e)
            continue

    return None, None


__all__ = ["find_email"]
