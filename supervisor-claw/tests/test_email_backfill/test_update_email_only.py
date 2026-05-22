"""Tests for the surgical email-only writer.

Covers:
* writes an email when the advisor row currently has ``email=None``
* refuses to overwrite an existing email (idempotent)
* appends a JSON line to the audit file on each successful write
* returns False when the advisor id doesn't exist
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw.enrichers.email_backfill import update_email_only
from claw.models.pydantic_models import AdvisorPartial


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test SQLite file."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("CLAW_DB_PATH", str(db))
    from claw import config as cfg
    from claw.models import db as dbmod
    cfg.get_settings.cache_clear()
    dbmod.get_engine.cache_clear()
    dbmod.init_db()
    return db


def _seed_advisor(*, name_cn: str = "测试老师", email: str | None = None) -> int:
    """Insert one school + advisor row; return advisor id."""
    from claw.models.db import session_scope
    from claw.storage.repo import upsert_advisor, upsert_school

    with session_scope() as s:
        school = upsert_school(s, "xidian", "西安电子科技大学")
        adv = upsert_advisor(
            s, school,
            AdvisorPartial(name_cn=name_cn, email=email, email_obfuscated=email is None),
        )
        s.flush()
        return adv.id


def test_update_email_only_writes_when_null(tmp_path: Path) -> None:
    from claw.models.db import Advisor, session_scope

    audit_path = str(tmp_path / "audit.jsonl")
    advisor_id = _seed_advisor(email=None)

    with session_scope() as s:
        ok = update_email_only(
            s,
            advisor_id=advisor_id,
            new_email="prof@xidian.edu.cn",
            source="js_decode",
            audit_path=audit_path,
        )
    assert ok is True

    # row should now have the email
    with session_scope() as s:
        adv = s.get(Advisor, advisor_id)
        assert adv is not None
        assert adv.email == "prof@xidian.edu.cn"
        assert adv.email_obfuscated is False

    # audit file should have one line
    lines = Path(audit_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["advisor_id"] == advisor_id
    assert rec["new_email"] == "prof@xidian.edu.cn"
    assert rec["source"] == "js_decode"
    assert rec["old_email"] is None
    assert rec["school"] == "xidian"


def test_update_email_only_refuses_to_overwrite(tmp_path: Path) -> None:
    from claw.models.db import Advisor, session_scope

    audit_path = str(tmp_path / "audit.jsonl")
    advisor_id = _seed_advisor(email="existing@xidian.edu.cn")

    with session_scope() as s:
        ok = update_email_only(
            s,
            advisor_id=advisor_id,
            new_email="newhere@xidian.edu.cn",
            source="bing",
            audit_path=audit_path,
        )
    assert ok is False

    with session_scope() as s:
        adv = s.get(Advisor, advisor_id)
        assert adv is not None
        assert adv.email == "existing@xidian.edu.cn"

    # No audit entry should have been written.
    assert not Path(audit_path).exists() or Path(audit_path).read_text() == ""


def test_update_email_only_missing_advisor(tmp_path: Path) -> None:
    from claw.models.db import session_scope

    audit_path = str(tmp_path / "audit.jsonl")
    with session_scope() as s:
        ok = update_email_only(
            s,
            advisor_id=99999,
            new_email="ghost@xidian.edu.cn",
            source="bing",
            audit_path=audit_path,
        )
    assert ok is False


def test_update_email_only_rejects_invalid_email(tmp_path: Path) -> None:
    from claw.models.db import session_scope

    audit_path = str(tmp_path / "audit.jsonl")
    advisor_id = _seed_advisor(email=None)

    with session_scope() as s:
        ok = update_email_only(
            s,
            advisor_id=advisor_id,
            new_email="not-an-email",
            source="bing",
            audit_path=audit_path,
        )
    assert ok is False
