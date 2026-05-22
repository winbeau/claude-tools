"""Site-specific email-backfill enrichers (v0.5).

Modules under this package expose a single async entry point::

    async def find_email(
        advisor,
        page,
        sess,
        school_name_cn,
    ) -> tuple[str | None, str | None]:
        ...

Returning ``(email, source)`` or ``(None, None)``. The
:mod:`claw.enrichers.email_backfill` orchestrator consults
``_SITE_EMAIL_DISPATCH`` (see that module) and, when a school code matches,
delegates to the per-site module instead of running the generic cascade.

Per-site modules are appropriate for schools where the default
``js → bing → dblp`` chain has the wrong order, calls subordinate helpers
in a tailored way (e.g. wayback before bing), or skips a strategy entirely
because of a hard infrastructure block (e.g. NWPU's TS-WAF wall makes the
``js`` path useless).
"""
