"""SQLModel schema. SQLite with WAL + FTS5 evaluation index."""

from __future__ import annotations

from datetime import datetime
from functools import cache

from sqlalchemy import Column, DateTime, UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine

from ..config import get_settings


def _utcnow() -> datetime:
    return datetime.utcnow()


def _ts_column(*, onupdate: bool = False) -> Column:
    kwargs = {"default": _utcnow}
    if onupdate:
        kwargs["onupdate"] = _utcnow
    return Column(DateTime, nullable=False, **kwargs)


class School(SQLModel, table=True):
    __tablename__ = "school"
    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name_cn: str
    name_en: str | None = None


class Department(SQLModel, table=True):
    __tablename__ = "department"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_dept_school_code"),)

    id: int | None = Field(default=None, primary_key=True)
    school_id: int = Field(foreign_key="school.id", index=True)
    code: str
    name_cn: str
    name_en: str | None = None
    faculty_list_url: str | None = None


class Advisor(SQLModel, table=True):
    __tablename__ = "advisor"
    __table_args__ = (
        UniqueConstraint("school_id", "name_cn", "email", name="uq_advisor_identity"),
    )

    id: int | None = Field(default=None, primary_key=True)
    school_id: int = Field(foreign_key="school.id", index=True)
    name_cn: str = Field(index=True)
    name_en: str | None = None
    title: str | None = None
    gender: str | None = None
    homepage: str | None = None
    email: str | None = Field(default=None, index=True)
    email_obfuscated: bool = False
    phone: str | None = None
    photo_url: str | None = None
    bio_text: str | None = None
    research_interests_raw: str | None = None  # comma-separated tags, for v0.1
    is_recruiting: bool | None = None
    raw_quota_text: str | None = None
    source_url: str | None = None

    # v0.3 — agent enrichment fields
    recruiting_confidence: float | None = None
    reputation_tag: str | None = None  # positive | neutral | negative | unknown
    enriched_summary: str | None = None
    last_enriched_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))

    first_seen: datetime = Field(sa_column=_ts_column())
    last_updated: datetime = Field(sa_column=_ts_column(onupdate=True))


class Appointment(SQLModel, table=True):
    __tablename__ = "appointment"
    __table_args__ = (
        UniqueConstraint("advisor_id", "department_id", name="uq_appt"),
    )

    id: int | None = Field(default=None, primary_key=True)
    advisor_id: int = Field(foreign_key="advisor.id", index=True)
    department_id: int = Field(foreign_key="department.id", index=True)
    role: str | None = None  # 主聘 / 兼聘 / null
    first_seen: datetime = Field(sa_column=_ts_column())


class QuotaInfo(SQLModel, table=True):
    __tablename__ = "quota_info"

    id: int | None = Field(default=None, primary_key=True)
    advisor_id: int = Field(foreign_key="advisor.id", index=True)
    year: int | None = None
    degree: str | None = None  # PhD / MS / Postdoc
    count: int | None = None
    raw_text: str
    source_url: str | None = None
    confidence: float | None = None
    extractor: str = "regex"  # or "deepseek"
    extracted_at: datetime = Field(sa_column=_ts_column())


class Evaluation(SQLModel, table=True):
    __tablename__ = "evaluation"

    id: int | None = Field(default=None, primary_key=True)
    advisor_id: int = Field(foreign_key="advisor.id", index=True)
    source: str  # mysupervisor / zhihu / web_search / manual / ...
    source_url: str | None = None
    content: str
    rating: float | None = None
    posted_at: datetime | None = None
    fetched_at: datetime = Field(sa_column=_ts_column())


class SourceSnapshot(SQLModel, table=True):
    __tablename__ = "source_snapshot"

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(index=True)
    sha256: str = Field(index=True)
    gzip_path: str
    fetched_at: datetime = Field(sa_column=_ts_column())


@event.listens_for(Engine, "connect")
def _enable_sqlite_pragmas(dbapi_conn, _conn_record):  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


_ENRICHMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("recruiting_confidence", "REAL"),
    ("reputation_tag", "TEXT"),
    ("enriched_summary", "TEXT"),
    ("last_enriched_at", "DATETIME"),
)


def _ensure_enrichment_columns(engine: Engine) -> None:
    """Idempotent: add v0.3 enrichment columns to advisor if missing.

    Existing DBs created under v0.1/v0.2 only have the original advisor schema;
    `SQLModel.metadata.create_all` does NOT add columns to existing tables.
    """
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(advisor)")}
        for col, ddl in _ENRICHMENT_COLUMNS:
            if col not in existing:
                conn.exec_driver_sql(f"ALTER TABLE advisor ADD COLUMN {col} {ddl}")
        conn.commit()


def _ensure_fts(engine: Engine) -> None:
    """Create FTS5 virtual table mirroring evaluation.content."""
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS evaluation_fts "
            "USING fts5(content, content='evaluation', content_rowid='id')"
        )
        conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS evaluation_ai AFTER INSERT ON evaluation BEGIN "
            "INSERT INTO evaluation_fts(rowid, content) VALUES (new.id, new.content); END"
        )
        conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS evaluation_ad AFTER DELETE ON evaluation BEGIN "
            "INSERT INTO evaluation_fts(evaluation_fts, rowid, content) "
            "VALUES('delete', old.id, old.content); END"
        )
        conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS evaluation_au AFTER UPDATE ON evaluation BEGIN "
            "INSERT INTO evaluation_fts(evaluation_fts, rowid, content) "
            "VALUES('delete', old.id, old.content); "
            "INSERT INTO evaluation_fts(rowid, content) VALUES (new.id, new.content); END"
        )
        conn.commit()


@cache
def get_engine():
    s = get_settings()
    s.claw_db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{s.claw_db_path}"
    return create_engine(url, echo=False)


def init_db() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _ensure_enrichment_columns(engine)
    _ensure_fts(engine)


def session_scope() -> Session:
    return Session(get_engine())


def count_rows(model: type[SQLModel]) -> int:
    with session_scope() as s:
        return s.query(model).count()  # type: ignore[attr-defined]
