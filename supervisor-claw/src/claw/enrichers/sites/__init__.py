"""Per-school email backfill site adapters.

Each module here exports an ``async def find_email(advisor, page, sess,
school_name_cn) -> tuple[str | None, str | None]`` callable that the
``claw backfill-email`` orchestrator dispatches to when the advisor's
school code matches the module name (``<code>_email``).

The contract is intentionally narrow:

* ``advisor``: a :class:`claw.models.db.Advisor` row (or anything with
  ``name_cn`` / ``homepage`` / ``source_url`` attributes).
* ``page``:    a Playwright stealth ``Page`` opened by the caller.
* ``sess``:    a shared ``httpx.AsyncClient`` (for non-browser HTTP, e.g.
  DBLP API).
* ``school_name_cn``: the school's display name (e.g. "国防科技大学")
  used as a hint for search / DBLP affiliation queries.

Returns ``(email, source)``. ``source`` mirrors the labels used by
:func:`claw.enrichers.email_backfill.update_email_only` (``dblp`` /
``bing`` / ``js_decode`` / …) so the audit log stays consistent.
"""
