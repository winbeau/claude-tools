"""SQL DDL for the schools.sqlite snapshot (export schema v1).

Evolution rules:
- Adding columns / tables: SCHEMA_VERSION stays at 1; downstream ignores unknowns
- Removing / renaming / changing semantics: bump SCHEMA_VERSION
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_DDL_V1 = """
-- meta: schema version + export metadata
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- school / department
CREATE TABLE school (
    code     TEXT PRIMARY KEY,
    name_cn  TEXT NOT NULL,
    name_en  TEXT
);

CREATE TABLE department (
    school_code TEXT NOT NULL REFERENCES school(code),
    code        TEXT NOT NULL,
    name_cn     TEXT NOT NULL,
    name_en     TEXT,
    PRIMARY KEY (school_code, code)
);

-- advisor (one row per person)
CREATE TABLE advisor (
    id                     INTEGER PRIMARY KEY,
    school_code            TEXT NOT NULL REFERENCES school(code),
    name_cn                TEXT NOT NULL,
    name_en                TEXT,
    title                  TEXT,
    gender                 TEXT,
    homepage               TEXT,
    source_url             TEXT,
    email                  TEXT,
    email_obfuscated       INTEGER NOT NULL DEFAULT 0,
    phone                  TEXT,
    photo_url              TEXT,
    bio_text               TEXT,
    research_interests     TEXT,
    is_recruiting          INTEGER,
    recruiting_confidence  REAL,
    reputation_tag         TEXT,
    enriched_summary       TEXT,
    last_enriched_at       TEXT,
    first_seen             TEXT NOT NULL,
    last_updated           TEXT NOT NULL
);

-- multi-department affiliations
CREATE TABLE appointment (
    advisor_id   INTEGER NOT NULL REFERENCES advisor(id),
    school_code  TEXT    NOT NULL,
    dept_code    TEXT    NOT NULL,
    role         TEXT,
    PRIMARY KEY (advisor_id, school_code, dept_code),
    FOREIGN KEY (school_code, dept_code) REFERENCES department(school_code, code)
);

-- recruitment quota
CREATE TABLE quota (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    year        INTEGER,
    degree      TEXT,
    count       INTEGER,
    confidence  REAL,
    raw_text    TEXT NOT NULL,
    source_url  TEXT
);

-- third-party evaluations
CREATE TABLE evaluation (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    source      TEXT NOT NULL,
    source_url  TEXT,
    content     TEXT NOT NULL,
    rating      REAL,
    posted_at   TEXT
);

-- enrichment research trace (only latest run per advisor exported)
CREATE TABLE trace (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    step_idx    INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL,
    detail      TEXT NOT NULL
);

-- indexes for common filter / sort paths
CREATE INDEX idx_advisor_school        ON advisor(school_code);
CREATE INDEX idx_advisor_recruiting    ON advisor(is_recruiting);
CREATE INDEX idx_advisor_reputation    ON advisor(reputation_tag);
CREATE INDEX idx_advisor_last_enriched ON advisor(last_enriched_at);
CREATE INDEX idx_appt_school_dept      ON appointment(school_code, dept_code);
CREATE INDEX idx_appt_advisor          ON appointment(advisor_id);
CREATE INDEX idx_quota_advisor         ON quota(advisor_id);
CREATE INDEX idx_eval_advisor          ON evaluation(advisor_id);
CREATE INDEX idx_trace_advisor         ON trace(advisor_id, step_idx);

-- FTS5 over advisor (powering the q= search box downstream)
CREATE VIRTUAL TABLE advisor_fts USING fts5(
    name_cn, bio_text, research_interests, enriched_summary,
    content='advisor', content_rowid='id',
    tokenize='unicode61 remove_diacritics 1'
);
"""
