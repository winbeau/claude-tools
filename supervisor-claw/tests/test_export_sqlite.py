"""End-to-end smoke tests for ``claw.export.sqlite_exporter``.

Builds a tiny internal DB via the upsert + append helpers, runs ``export_sqlite``
against it, and re-attaches the resulting ``schools.sqlite`` to verify schema,
field mapping, latest-run trace selection, and FTS5 indexing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from claw.models.pydantic_models import AdvisorPartial


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "test.db"
    monkeypatch.setenv("CLAW_DB_PATH", str(db))
    from claw import config as cfg
    from claw.models import db as dbmod
    cfg.get_settings.cache_clear()
    dbmod.get_engine.cache_clear()
    dbmod.init_db()
    return db


def _seed_minimal() -> tuple[int, int]:
    """Insert 1 school, 1 dept, 2 advisors with quota+eval+trace; return ids."""
    from claw.models.db import session_scope
    from claw.storage.repo import (
        append_evaluation,
        append_quota,
        append_trace,
        link_department,
        upsert_advisor,
        upsert_department,
        upsert_school,
    )

    with session_scope() as s:
        school = upsert_school(s, "tsinghua", "清华大学", "Tsinghua University")
        dept = upsert_department(s, school, "cs", "计算机系")
        a1 = upsert_advisor(
            s, school,
            AdvisorPartial(
                name_cn="张三", email="zhang@x.cn", title="教授",
                research_interests=["LLM 推理", "可解释性"],
                bio_text="研究 LLM 推理与可解释性",
            ),
        )
        a1.is_recruiting = True
        a1.recruiting_confidence = 0.85
        a1.reputation_tag = "positive"
        a1.enriched_summary = "口碑佳，正常招生"
        a1.last_enriched_at = datetime(2026, 5, 19, 12, 0, 0)
        s.add(a1)

        a2 = upsert_advisor(
            s, school,
            AdvisorPartial(name_cn="李四", email="li@x.cn", title="副教授"),
        )
        link_department(s, a1, dept)
        link_department(s, a2, dept)

        append_quota(
            s, a1, raw_text="2026年招收博士2人", year=2026, degree="PhD",
            count=2, confidence=0.9, source_url="https://example.com/q1",
        )
        append_evaluation(
            s, a1, source="web_research", content="风格友好",
            source_url="https://example.com/e1", rating=4.5,
        )
        append_trace(
            s, a1, run_id="run-old", step_idx=0, kind="search",
            label="搜索", detail='"old run" → 1 results',
        )
        return a1.id, a2.id


def test_export_smoke(tmp_path: Path) -> None:
    from claw.export.sqlite_exporter import export_sqlite

    _seed_minimal()
    out_dir = tmp_path / "exports"
    summary = export_sqlite(out_dir)

    assert summary.db_path.exists()
    assert summary.bytes > 0
    assert (out_dir / "manifest.json").exists()

    # sha256 in manifest matches the file on disk
    file_sha = hashlib.sha256(summary.db_path.read_bytes()).hexdigest()
    assert summary.sha256 == file_sha
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["schools_sqlite_sha256"] == file_sha
    assert manifest["schema_version"] == 1
    assert manifest["counts"]["advisors"] == 2
    assert manifest["counts"]["schools"] == 1


def test_meta_table(tmp_path: Path) -> None:
    from claw.export.sqlite_exporter import export_sqlite

    _seed_minimal()
    summary = export_sqlite(tmp_path / "exports")
    with sqlite3.connect(summary.db_path) as conn:
        rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert rows["schema_version"] == "1"
    assert rows["claw_version"]
    assert rows["exported_at"].endswith("Z")


def test_field_mapping(tmp_path: Path) -> None:
    """research_interests_raw CSV → JSON array; bool → INTEGER; datetimes ISO."""
    from claw.export.sqlite_exporter import export_sqlite

    _seed_minimal()
    summary = export_sqlite(tmp_path / "exports")
    with sqlite3.connect(summary.db_path) as conn:
        row = conn.execute(
            "SELECT research_interests, is_recruiting, email_obfuscated, "
            "       reputation_tag, last_enriched_at, first_seen "
            "FROM advisor WHERE name_cn = '张三'"
        ).fetchone()
    interests_json, is_rec, obfuscated, tag, last_enr, first_seen = row
    assert json.loads(interests_json) == ["LLM 推理", "可解释性"]
    assert is_rec == 1
    assert obfuscated == 0
    assert tag == "positive"
    assert last_enr.endswith("Z")
    assert first_seen.endswith("Z")


def test_appointment_mapping(tmp_path: Path) -> None:
    """appointment uses (school_code, dept_code) — joinable to department."""
    from claw.export.sqlite_exporter import export_sqlite

    _seed_minimal()
    summary = export_sqlite(tmp_path / "exports")
    with sqlite3.connect(summary.db_path) as conn:
        rows = conn.execute(
            "SELECT a.name_cn, d.name_cn "
            "FROM advisor a "
            "JOIN appointment ap ON ap.advisor_id = a.id "
            "JOIN department d ON d.school_code = ap.school_code AND d.code = ap.dept_code "
            "ORDER BY a.name_cn"
        ).fetchall()
    names = {(adv, dept) for adv, dept in rows}
    assert ("张三", "计算机系") in names
    assert ("李四", "计算机系") in names


def test_latest_trace_only(tmp_path: Path) -> None:
    """Same advisor with two runs (old + new): export keeps only the new run."""
    from claw.export.sqlite_exporter import export_sqlite
    from claw.models.db import EnrichmentTrace, session_scope
    from claw.storage.repo import append_trace
    from sqlmodel import select

    a1_id, _ = _seed_minimal()

    # add a newer run with two steps; bump created_at past the existing "old" row
    with session_scope() as s:
        from claw.models.db import Advisor
        a1 = s.get(Advisor, a1_id)
        assert a1 is not None
        r1 = append_trace(
            s, a1, run_id="run-new", step_idx=0, kind="search",
            label="搜索", detail='"new run step0" → 3 results',
        )
        r2 = append_trace(
            s, a1, run_id="run-new", step_idx=1, kind="final",
            label="提交综合判断", detail="招生 · conf=0.8",
        )
        # Force chronological ordering: old run timestamps must predate new
        old_rows = s.exec(
            select(EnrichmentTrace).where(EnrichmentTrace.run_id == "run-old")
        ).all()
        anchor = datetime(2026, 5, 1, 12, 0, 0)
        for row in old_rows:
            row.created_at = anchor
            s.add(row)
        r1.created_at = anchor + timedelta(days=10)
        r2.created_at = anchor + timedelta(days=10, seconds=1)
        s.add(r1)
        s.add(r2)
        s.commit()

    summary = export_sqlite(tmp_path / "exports")
    with sqlite3.connect(summary.db_path) as conn:
        rows = conn.execute(
            "SELECT step_idx, kind, detail FROM trace WHERE advisor_id = ? ORDER BY step_idx",
            (a1_id,),
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] == "search"
    assert "new run step0" in rows[0][2]
    assert rows[1][1] == "final"


def test_fts_smoke(tmp_path: Path) -> None:
    from claw.export.sqlite_exporter import export_sqlite

    _seed_minimal()
    summary = export_sqlite(tmp_path / "exports")
    with sqlite3.connect(summary.db_path) as conn:
        rows = conn.execute(
            "SELECT a.name_cn FROM advisor a "
            "JOIN advisor_fts f ON f.rowid = a.id "
            "WHERE advisor_fts MATCH 'LLM'"
        ).fetchall()
    assert rows
    assert rows[0][0] == "张三"
