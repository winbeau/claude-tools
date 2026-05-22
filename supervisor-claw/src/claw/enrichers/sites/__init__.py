"""Site-specific email backfill strategies.

Each module in this package exposes a ``find_email`` coroutine that orchestrates
the per-school decode → search → dblp cascade. Modules here are intentionally
thin wrappers around :mod:`claw.enrichers.email_backfill` and
:mod:`claw.core.email_decoders`; they encode the per-school strategy ordering
and the school-specific URL / domain hints.
"""
