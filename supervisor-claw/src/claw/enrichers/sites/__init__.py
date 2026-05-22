"""Per-school email backfill strategies.

Each ``<code>_email.py`` module exports an ``async def find_email(advisor,
page, sess, school_name_cn) -> tuple[str | None, str | None]`` that the
``backfill-email`` CLI dispatches on per school.

These modules are *optional* — the generic
:func:`claw.enrichers.email_backfill.backfill_one_advisor` orchestrator
handles schools without a site-specific module.
"""
