"""Tests for the NUDT (国防科技大学) email backfill adapter.

NUDT's adapter only uses DBLP + stealth Bing — there is no JS decoder
and no wayback path (see ``src/claw/enrichers/sites/nudt_email.py`` and
``docs/reports/email_backfill_nudt.md`` for why).

We don't drive real Playwright / DBLP here. Each test monkeypatches the
two helpers (``dblp_email_lookup`` / ``search_email_via_stealth_bing``)
on the ``nudt_email`` module so we can assert the cascade ordering and
the (email, source) return contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from claw.enrichers.sites import nudt_email


def _advisor(name_cn: str = "胡德文") -> SimpleNamespace:
    """Build a minimal advisor-like object.

    ``find_email`` only reads ``name_cn`` / ``name_en``, so a
    SimpleNamespace is enough — no DB / model dependencies.
    """
    return SimpleNamespace(
        name_cn=name_cn,
        name_en=None,
        homepage=None,
        source_url=None,
    )


@pytest.mark.asyncio
async def test_find_email_dblp_hit_short_circuits_bing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful DBLP hit must return ``(email, 'dblp')`` and never
    invoke the stealth Bing helper.
    """
    calls = {"dblp": 0, "bing": 0}

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        calls["dblp"] += 1
        # Return a hit on the first affiliation hint variant.
        if affiliation_hint == "National University of Defense Technology":
            return "huduwen@nudt.edu.cn"
        return None

    async def fake_bing(page, name, school_name_cn, domain_hint):  # noqa: ANN001
        calls["bing"] += 1
        return "should-not-be-used@example.com"

    monkeypatch.setattr(nudt_email, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(nudt_email, "search_email_via_stealth_bing", fake_bing)

    email, source = await nudt_email.find_email(
        _advisor("胡德文"),
        page=object(),       # unused — bing never called
        sess=object(),       # unused — fake_dblp ignores it
        school_name_cn="国防科技大学",
    )

    assert email == "huduwen@nudt.edu.cn"
    assert source == "dblp"
    assert calls["dblp"] == 1
    assert calls["bing"] == 0


@pytest.mark.asyncio
async def test_find_email_all_miss_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both DBLP and Bing miss (the common case for NUDT — military
    advisors deliberately not in public web), the function must return
    ``(None, None)`` without raising.
    """
    dblp_calls = []
    bing_calls = []

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        dblp_calls.append(affiliation_hint)
        return None

    async def fake_bing(page, name, school_name_cn, domain_hint):  # noqa: ANN001
        bing_calls.append((name, school_name_cn, domain_hint))
        return None

    monkeypatch.setattr(nudt_email, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(nudt_email, "search_email_via_stealth_bing", fake_bing)

    email, source = await nudt_email.find_email(
        _advisor("某军校老师"),
        page=object(),
        sess=object(),
        school_name_cn="国防科技大学",
    )

    assert email is None
    assert source is None
    # Both English and short affiliations should have been tried.
    assert dblp_calls == [
        "National University of Defense Technology",
        "nudt",
    ]
    # Bing called exactly once with the nudt domain hint.
    assert len(bing_calls) == 1
    name, school, hint = bing_calls[0]
    assert name == "某军校老师"
    assert school == "国防科技大学"
    assert hint == "nudt.edu.cn"


@pytest.mark.asyncio
async def test_find_email_falls_through_to_bing_when_dblp_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DBLP miss → Bing hit must return ``(email, 'bing')``."""

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        return None

    async def fake_bing(page, name, school_name_cn, domain_hint):  # noqa: ANN001
        # Pretend a public conference roster surfaced the address.
        return "li.test@nudt.edu.cn"

    monkeypatch.setattr(nudt_email, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(nudt_email, "search_email_via_stealth_bing", fake_bing)

    email, source = await nudt_email.find_email(
        _advisor("李某"),
        page=object(),
        sess=object(),
        school_name_cn="国防科技大学",
    )

    assert email == "li.test@nudt.edu.cn"
    assert source == "bing"


@pytest.mark.asyncio
async def test_find_email_safe_when_advisor_has_no_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An advisor row with neither name_cn nor name_en must short-circuit
    to ``(None, None)`` without calling any external helper.
    """
    called = {"dblp": False, "bing": False}

    async def fake_dblp(sess, name, affiliation_hint):  # noqa: ANN001
        called["dblp"] = True
        return None

    async def fake_bing(page, name, school_name_cn, domain_hint):  # noqa: ANN001
        called["bing"] = True
        return None

    monkeypatch.setattr(nudt_email, "dblp_email_lookup", fake_dblp)
    monkeypatch.setattr(nudt_email, "search_email_via_stealth_bing", fake_bing)

    nameless = SimpleNamespace(name_cn=None, name_en=None)
    email, source = await nudt_email.find_email(
        nameless,
        page=object(),
        sess=object(),
        school_name_cn="国防科技大学",
    )

    assert email is None
    assert source is None
    assert called == {"dblp": False, "bing": False}
