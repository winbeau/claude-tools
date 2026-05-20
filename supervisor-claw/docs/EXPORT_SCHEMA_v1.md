# Export schema v1 — `schools.sqlite`

> Produced by `claw export --format sqlite --out ./exports/`. Two files:
> - `schools.sqlite` — self-contained SQLite, no `-wal`/`-shm` sidecars
> - `manifest.json` — schema_version + sha256 + row counts for downstream gating
>
> `schema_version=1` is asserted both in the DB (`meta` table) and in `manifest.json`.

---

## 1. DDL

```sql
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- rows: schema_version=1, exported_at (ISO 8601 Z), claw_version

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
    research_interests     TEXT,  -- JSON array string
    is_recruiting          INTEGER,  -- 0/1/NULL
    recruiting_confidence  REAL,
    reputation_tag         TEXT,  -- positive|neutral|negative|unknown
    enriched_summary       TEXT,
    last_enriched_at       TEXT,  -- ISO 8601 Z, nullable
    first_seen             TEXT NOT NULL,
    last_updated           TEXT NOT NULL
);

CREATE TABLE appointment (
    advisor_id   INTEGER NOT NULL REFERENCES advisor(id),
    school_code  TEXT    NOT NULL,
    dept_code    TEXT    NOT NULL,
    role         TEXT,
    PRIMARY KEY (advisor_id, school_code, dept_code),
    FOREIGN KEY (school_code, dept_code) REFERENCES department(school_code, code)
);

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

CREATE TABLE evaluation (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    source      TEXT NOT NULL,
    source_url  TEXT,
    content     TEXT NOT NULL,
    rating      REAL,
    posted_at   TEXT
);

CREATE TABLE trace (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    step_idx    INTEGER NOT NULL,
    kind        TEXT NOT NULL,  -- search|read|submit|final
    label       TEXT NOT NULL,
    detail      TEXT NOT NULL
);

CREATE VIRTUAL TABLE advisor_fts USING fts5(
    name_cn, bio_text, research_interests, enriched_summary,
    content='advisor', content_rowid='id',
    tokenize='unicode61 remove_diacritics 1'
);

-- indexes
CREATE INDEX idx_advisor_school        ON advisor(school_code);
CREATE INDEX idx_advisor_recruiting    ON advisor(is_recruiting);
CREATE INDEX idx_advisor_reputation    ON advisor(reputation_tag);
CREATE INDEX idx_advisor_last_enriched ON advisor(last_enriched_at);
CREATE INDEX idx_appt_school_dept      ON appointment(school_code, dept_code);
CREATE INDEX idx_appt_advisor          ON appointment(advisor_id);
CREATE INDEX idx_quota_advisor         ON quota(advisor_id);
CREATE INDEX idx_eval_advisor          ON evaluation(advisor_id);
CREATE INDEX idx_trace_advisor         ON trace(advisor_id, step_idx);
```

Authoritative source: `src/claw/export/schema.py::SCHEMA_DDL_V1`. Any change to the table list **must** be reflected in this doc.

---

## 2. Column reference

| Table | Column | Type | Source (internal DB) | Notes |
|---|---|---|---|---|
| meta | key | TEXT | — | `schema_version` / `exported_at` / `claw_version` |
| meta | value | TEXT | — | stringified |
| school | code | TEXT PK | `school.code` | stable opaque id (e.g. `tsinghua`) |
| school | name_cn / name_en | TEXT | `school.name_cn/name_en` | |
| department | (school_code, code) | composite PK | `(school.code, department.code)` | replaces internal numeric dept id |
| department | name_cn / name_en | TEXT | `department.name_cn/name_en` | |
| advisor | id | INTEGER PK | `advisor.id` | internal id reused — stable across exports if rows aren't deleted |
| advisor | school_code | TEXT FK | join via `advisor.school_id → school.code` | |
| advisor | research_interests | TEXT | `advisor.research_interests_raw` | CSV → JSON array string; `NULL` if empty |
| advisor | is_recruiting | INTEGER | `advisor.is_recruiting` | `0`/`1`/`NULL` |
| advisor | email_obfuscated | INTEGER | `advisor.email_obfuscated` | bool→int |
| advisor | last_enriched_at / first_seen / last_updated | TEXT | datetime | ISO 8601 UTC with trailing `Z` |
| appointment | (advisor_id, school_code, dept_code) | composite PK | join via internal `department_id → department.code` | |
| quota | year/degree/count/confidence/raw_text/source_url | — | `quota_info.*` | `extractor` / `extracted_at` not exported |
| evaluation | source/source_url/content/rating/posted_at | — | `evaluation.*` | `fetched_at` not exported |
| trace | (advisor_id, step_idx, kind, label, detail) | — | `enrichment_trace.*` | **only the latest run per advisor** is exported |
| advisor_fts | name_cn / bio_text / research_interests / enriched_summary | FTS5 virtual | rebuilt from `advisor` at export end | `tokenize='unicode61 remove_diacritics 1'` |

**Not exported** (intentionally):
- `source_snapshot` — forensic raw HTML cache, internal only
- `advisor.raw_quota_text` — superseded by structured `quota` table
- enrichment runs other than the most recent — `trace` keeps only the latest run per advisor (by `created_at`)
- `quota_info.extractor` / `quota_info.extracted_at` / `evaluation.fetched_at` — internal provenance

---

## 3. Downstream attach example (Aurash schools page)

```sql
-- Open a fresh in-memory DB and mount the snapshot read-only.
ATTACH DATABASE 'schools.sqlite' AS schools;

-- 1. Version gate
SELECT value FROM schools.meta WHERE key = 'schema_version';  -- must be <= MAX_SUPPORTED

-- 2. List advisors of one school, with primary dept
SELECT a.id, a.name_cn, a.title, d.name_cn AS dept,
       a.is_recruiting, a.reputation_tag
FROM schools.advisor a
JOIN schools.appointment ap ON ap.advisor_id = a.id
JOIN schools.department  d  ON d.school_code = ap.school_code AND d.code = ap.dept_code
WHERE a.school_code = 'tsinghua'
ORDER BY a.name_cn
LIMIT 50;

-- 3. Free-text search
SELECT a.id, a.name_cn, snippet(advisor_fts, 1, '<b>', '</b>', '…', 32) AS hl
FROM schools.advisor a
JOIN schools.advisor_fts f ON f.rowid = a.id
WHERE advisor_fts MATCH '可解释性'
LIMIT 20;

-- 4. Research trace tab
SELECT step_idx, kind, label, detail
FROM schools.trace
WHERE advisor_id = ?
ORDER BY step_idx;
```

Downstream should `ATTACH` with `MODE=ro` if the bound API supports it; otherwise treat the file as read-only by convention.

---

## 4. Schema evolution rules

| Change | Action |
|---|---|
| Add column / new table | `SCHEMA_VERSION` stays at 1; downstream ignores unknown columns. |
| Remove column / change semantics | Bump `SCHEMA_VERSION` (= 2). Downstream must explicitly opt into v2. |
| Rename column | Equivalent to "add new + retain old for one minor". |
| FTS5 column changes | FTS5 table rebuild — not breaking, downstream unaffected. |

**Compatibility contract**: downstream **must** read `meta.schema_version` first and assert it falls within its supported range; never assume the schema is current.

---

## 5. Distribution

The exporter only writes `./exports/{schools.sqlite, manifest.json}`. How the bundle reaches downstream production is policy-only (claw does not ship any transport):

1. **HF Dataset** — use Aurash's existing `make schools-push / schools-pull` channel.
2. **rsync over Tailscale** — `rsync -av ./exports/ huawei2:/home/winbeau/Aurash/backend/data/schools/`
3. **Manual scp** — debugging only.

Whatever transport is chosen, the consumer should verify:

```python
import hashlib, json
m = json.load(open("exports/manifest.json"))
h = hashlib.sha256(open("exports/schools.sqlite", "rb").read()).hexdigest()
assert h == m["schools_sqlite_sha256"], "checksum mismatch — re-download"
assert m["schema_version"] <= MAX_SUPPORTED_SCHEMA_VERSION
```
