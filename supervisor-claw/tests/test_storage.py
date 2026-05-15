"""Storage layer: upsert is idempotent + dept linking works."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claw.models.pydantic_models import AdvisorPartial


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Each test gets its own SQLite file."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("CLAW_DB_PATH", str(db))
    # bust caches
    from claw import config as cfg
    from claw.models import db as dbmod
    cfg.get_settings.cache_clear()
    dbmod.get_engine.cache_clear()
    dbmod.init_db()
    return db


def test_upsert_advisor_is_idempotent() -> None:
    from claw.models.db import Advisor, session_scope
    from claw.storage.repo import upsert_advisor, upsert_school

    partial = AdvisorPartial(
        name_cn="测试老师",
        email="test@tsinghua.edu.cn",
        title="教授",
    )
    with session_scope() as s:
        school = upsert_school(s, "tsinghua", "清华大学")
        a1 = upsert_advisor(s, school, partial)
        a2 = upsert_advisor(s, school, partial)
        assert a1.id == a2.id
        # only one row
        assert s.query(Advisor).count() == 1


def test_upsert_advisor_merges_new_fields() -> None:
    from claw.models.db import session_scope
    from claw.storage.repo import upsert_advisor, upsert_school

    with session_scope() as s:
        school = upsert_school(s, "tsinghua", "清华大学")
        a1 = upsert_advisor(
            s, school,
            AdvisorPartial(name_cn="王五", email="ww@x.cn", title="副教授"),
        )
        a2 = upsert_advisor(
            s, school,
            AdvisorPartial(
                name_cn="王五", email="ww@x.cn",
                title="教授", bio_text="bio", phone="123",
            ),
        )
        assert a1.id == a2.id
        assert a2.title == "教授"
        assert a2.bio_text == "bio"
        assert a2.phone == "123"


def test_link_department_dedup() -> None:
    from claw.models.db import Appointment, session_scope
    from claw.storage.repo import link_department, upsert_advisor, upsert_department, upsert_school

    with session_scope() as s:
        school = upsert_school(s, "tsinghua", "清华大学")
        dept = upsert_department(s, school, "cs", "计算机系")
        advisor = upsert_advisor(
            s, school,
            AdvisorPartial(name_cn="张三", email="z@x.cn"),
        )
        link_department(s, advisor, dept)
        link_department(s, advisor, dept)  # duplicate
        assert s.query(Appointment).count() == 1
