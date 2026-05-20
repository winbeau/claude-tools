"""Export internal DB → downstream-consumable schools.sqlite + manifest.json.

Reads from the live claw DB via ``session_scope()``; writes a fresh, immutable
snapshot to ``<out_dir>/schools.sqlite`` plus a ``manifest.json`` sibling that
carries schema_version / sha256 / row counts for the downstream attach.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field
from sqlmodel import select

from .. import __version__ as _CLAW_VERSION
from ..models.db import (
    Advisor,
    Appointment,
    Department,
    EnrichmentTrace,
    Evaluation,
    QuotaInfo,
    School,
    session_scope,
)
from .schema import SCHEMA_DDL_V1, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Public DTOs
# ---------------------------------------------------------------------------


class ExportCounts(BaseModel):
    schools: int = 0
    departments: int = 0
    advisors: int = 0
    appointments: int = 0
    quotas: int = 0
    evaluations: int = 0
    traces: int = 0


class ExportManifest(BaseModel):
    schema_version: int
    exported_at: str
    claw_version: str
    schools_sqlite_sha256: str
    schools_sqlite_bytes: int
    counts: ExportCounts = Field(default_factory=ExportCounts)


@dataclass
class ExportSummary:
    db_path: Path
    manifest_path: Path
    sha256: str
    bytes: int
    counts: ExportCounts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def export_sqlite(out_dir: Path) -> ExportSummary:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "schools.sqlite"
    db_path.unlink(missing_ok=True)

    out = sqlite3.connect(db_path)
    try:
        out.executescript(SCHEMA_DDL_V1)
        counts = ExportCounts()
        with session_scope() as src:
            _write_meta(out)
            counts.schools = _copy_schools(out, src)
            counts.departments = _copy_departments(out, src)
            counts.advisors = _copy_advisors(out, src)
            counts.appointments = _copy_appointments(out, src)
            counts.quotas = _copy_quotas(out, src)
            counts.evaluations = _copy_evaluations(out, src)
            counts.traces = _copy_latest_traces(out, src)
            _rebuild_fts(out)
        out.execute("PRAGMA optimize")
        out.commit()
    finally:
        out.close()

    sha = _sha256_file(db_path)
    size = db_path.stat().st_size
    manifest = ExportManifest(
        schema_version=SCHEMA_VERSION,
        exported_at=_now_iso(),
        claw_version=_CLAW_VERSION,
        schools_sqlite_sha256=sha,
        schools_sqlite_bytes=size,
        counts=counts,
    )
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    return ExportSummary(
        db_path=db_path,
        manifest_path=manifest_path,
        sha256=sha,
        bytes=size,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Internal copiers
# ---------------------------------------------------------------------------


def _write_meta(out: sqlite3.Connection) -> None:
    rows = [
        ("schema_version", str(SCHEMA_VERSION)),
        ("exported_at", _now_iso()),
        ("claw_version", _CLAW_VERSION),
    ]
    out.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", rows)


def _copy_schools(out: sqlite3.Connection, src) -> int:
    rows = src.exec(select(School)).all()
    out.executemany(
        "INSERT INTO school(code, name_cn, name_en) VALUES (?, ?, ?)",
        [(s.code, s.name_cn, s.name_en) for s in rows],
    )
    return len(rows)


def _copy_departments(out: sqlite3.Connection, src) -> int:
    rows = src.exec(
        select(Department, School).join(School, Department.school_id == School.id)
    ).all()
    out.executemany(
        "INSERT INTO department(school_code, code, name_cn, name_en) VALUES (?, ?, ?, ?)",
        [(school.code, d.code, d.name_cn, d.name_en) for d, school in rows],
    )
    return len(rows)


def _copy_advisors(out: sqlite3.Connection, src) -> int:
    rows = src.exec(
        select(Advisor, School).join(School, Advisor.school_id == School.id)
    ).all()
    payload = [
        (
            a.id,
            school.code,
            a.name_cn,
            a.name_en,
            a.title,
            a.gender,
            a.homepage,
            a.source_url,
            a.email,
            1 if a.email_obfuscated else 0,
            a.phone,
            a.photo_url,
            a.bio_text,
            _research_interests_json(a.research_interests_raw),
            _bool_to_int(a.is_recruiting),
            a.recruiting_confidence,
            a.reputation_tag,
            a.enriched_summary,
            _dt_to_iso(a.last_enriched_at),
            _dt_to_iso(a.first_seen),
            _dt_to_iso(a.last_updated),
        )
        for a, school in rows
    ]
    out.executemany(
        """
        INSERT INTO advisor(
            id, school_code, name_cn, name_en, title, gender,
            homepage, source_url, email, email_obfuscated, phone, photo_url,
            bio_text, research_interests,
            is_recruiting, recruiting_confidence, reputation_tag, enriched_summary,
            last_enriched_at, first_seen, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        payload,
    )
    return len(rows)


def _copy_appointments(out: sqlite3.Connection, src) -> int:
    rows = src.exec(
        select(Appointment, Department, School)
        .join(Department, Appointment.department_id == Department.id)
        .join(School, Department.school_id == School.id)
    ).all()
    seen: set[tuple[int, str, str]] = set()
    payload: list[tuple] = []
    for appt, dept, school in rows:
        key = (appt.advisor_id, school.code, dept.code)
        if key in seen:
            continue
        seen.add(key)
        payload.append((appt.advisor_id, school.code, dept.code, appt.role))
    out.executemany(
        "INSERT INTO appointment(advisor_id, school_code, dept_code, role) VALUES (?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _copy_quotas(out: sqlite3.Connection, src) -> int:
    rows = src.exec(select(QuotaInfo)).all()
    payload = [
        (q.id, q.advisor_id, q.year, q.degree, q.count, q.confidence, q.raw_text, q.source_url)
        for q in rows
    ]
    out.executemany(
        "INSERT INTO quota(id, advisor_id, year, degree, count, confidence, raw_text, source_url) "
        "VALUES (?,?,?,?,?,?,?,?)",
        payload,
    )
    return len(rows)


def _copy_evaluations(out: sqlite3.Connection, src) -> int:
    rows = src.exec(select(Evaluation)).all()
    payload = [
        (e.id, e.advisor_id, e.source, e.source_url, e.content, e.rating, _dt_to_iso(e.posted_at))
        for e in rows
    ]
    out.executemany(
        "INSERT INTO evaluation(id, advisor_id, source, source_url, content, rating, posted_at) "
        "VALUES (?,?,?,?,?,?,?)",
        payload,
    )
    return len(rows)


def _copy_latest_traces(out: sqlite3.Connection, src) -> int:
    """For each advisor, keep only the most recent run_id (by created_at).

    Implemented as a Python pass since we already hold a SQLModel session —
    avoids juggling raw SQL across two different SQLite handles.
    """
    rows = src.exec(select(EnrichmentTrace)).all()
    # bucket per (advisor_id, run_id) and remember the run's MAX(created_at)
    last_ts: dict[tuple[int, str], datetime] = {}
    for r in rows:
        key = (r.advisor_id, r.run_id)
        ts = r.created_at
        if key not in last_ts or (ts is not None and ts > last_ts[key]):
            last_ts[key] = ts
    # for each advisor, pick the run_id with the greatest MAX(created_at)
    chosen: dict[int, str] = {}
    chosen_ts: dict[int, datetime] = {}
    for (advisor_id, run_id), ts in last_ts.items():
        if ts is None:
            continue
        if advisor_id not in chosen_ts or ts > chosen_ts[advisor_id]:
            chosen[advisor_id] = run_id
            chosen_ts[advisor_id] = ts
    payload: list[tuple] = []
    for r in rows:
        if chosen.get(r.advisor_id) != r.run_id:
            continue
        payload.append((r.advisor_id, r.step_idx, r.kind, r.label, r.detail))
    payload.sort(key=lambda t: (t[0], t[1]))
    out.executemany(
        "INSERT INTO trace(advisor_id, step_idx, kind, label, detail) VALUES (?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _rebuild_fts(out: sqlite3.Connection) -> None:
    out.executescript(
        """
        INSERT INTO advisor_fts(rowid, name_cn, bio_text, research_interests, enriched_summary)
        SELECT id,
               COALESCE(name_cn, ''),
               COALESCE(bio_text, ''),
               COALESCE(research_interests, ''),
               COALESCE(enriched_summary, '')
        FROM advisor;
        """
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _research_interests_json(raw: str | None) -> str | None:
    if not raw:
        return None
    items = [t.strip() for t in raw.split(",") if t.strip()]
    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


def _bool_to_int(b: bool | None) -> int | None:
    if b is None:
        return None
    return 1 if b else 0


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # internal DB stores naive UTC (datetime.utcnow()); stamp Z explicitly
    if dt.tzinfo is None:
        return dt.replace(microsecond=0).isoformat() + "Z"
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
