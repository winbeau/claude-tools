"""Per-school email backfill strategies.

Each module under this package exports::

    async def find_email(advisor, page, sess, school_name_cn) -> tuple[str | None, str | None]

returning ``(email, source)``. The CLI default routes through
:func:`claw.enrichers.email_backfill.backfill_one_advisor`; these site
modules are an optional, more-opinionated hook for follow-up tuning.
"""
