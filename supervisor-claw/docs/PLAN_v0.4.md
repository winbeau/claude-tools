# v0.4 实施方案：结构化导出 (SQLite snapshot for downstream)

> **本轮目标**：把 v0.3 富化后的 advisor 数据导出为下游可消费的 **SQLite 快照文件**，让 Aurash schools 页（以及未来其它消费者）通过**文件**而不是 HTTP 接入。规模假设：20+ 学校 × 200+ 导师 ≈ 4000+ advisor 行，含嵌套 quotas / evaluations / trace。
>
> **不做**：HTTP 对外 API、在线查询、用户鉴权。这些违反 README 里"禁止公开部署到外网"的红线，也跟 claw 的离线工具定位不符。
>
> **核心交付**：`claw export --format sqlite --out ./exports/` 产出 `schools.sqlite`（带 manifest.json 元数据），schema 严格 versioning（meta 表记录），下游 (Aurash) attach 只读后用 SQL 查询。

---

## 1. 现状盘点

v0.3 落地后，DB 里每位 advisor 已经有：

- 基础字段（name_cn / title / homepage / email / phone / photo_url / bio_text / research_interests_raw / source_url）
- 富化字段（is_recruiting / recruiting_confidence / reputation_tag / enriched_summary / last_enriched_at）
- 卫星表：`appointment`（多院系）、`quota_info`（多招生）、`evaluation`（多评价）、`source_snapshot`（HTML 留档）

**已有但要升级的**：`claw export --format csv`（v0.1 写过雏形，扁平、无法承载嵌套数据，仅 Excel 投递清单用，保留不动）。

**缺的**：
1. **trace 没落库**。`enrichers/web_research.py` 在富化时会产生 search / read / final 三类事件，但只走日志、没持久化。Aurash 的"调研轨迹"Tab 需要这玩意。
2. 没有 versioned export schema，下游解析容易破。
3. 没有面向消费者的 SQLite 快照模式——内部 DB 含 `source_snapshot` gzip 路径等不可外传的字段，不能直接 ship。

---

## 2. 设计

### 2.1 表迁移：`enrichment_trace`

```python
# models/db.py 新表
class EnrichmentTrace(SQLModel, table=True):
    __tablename__ = "enrichment_trace"
    id: int | None = Field(default=None, primary_key=True)
    advisor_id: int = Field(foreign_key="advisor.id", index=True)
    run_id: str = Field(index=True)              # 一次富化 run 的 uuid，方便回放
    step_idx: int                                # 步骤编号 0..N
    kind: str                                    # search | read | final
    label: str                                   # 短标签（"搜索" / "阅读" / "提交"）
    detail: str                                  # 长描述（"\"姚明轩 清华 招生 2026\" → 12 results"）
    created_at: datetime = Field(sa_column=_ts_column())
```

幂等迁移走 `_ensure_enrichment_columns` 同款套路：检测表是否存在，缺则 `CREATE TABLE`。`init_db()` 末尾调一次。

### 2.2 富化器写盘 trace

`enrichers/web_research.py` 里 `research_advisor` 循环已经按 step 跑 tool calling，每次工具调用前后能拿到 kind+label+detail。改动：

- 进 `research_advisor` 时分配 `run_id = uuid4().hex`
- 每个工具调用结束后 `repo.append_trace(advisor, run_id, step_idx, kind, label, detail)`
- `submit_report` 工具触发 final 事件

新仓库函数 `storage/repo.py::append_trace`，跟现有 `append_quota / append_evaluation` 同 shape。

> 注：trace 是**累积**的（每次 re-enrich 追加新 run_id，不删旧的），内部 DB 全留；导出时只取最新一次 run。

### 2.3 Export 产物布局

```
exports/
├── manifest.json        # 元数据 (人可读，下游做版本/完整性校验)
└── schools.sqlite       # 全部数据，下游只读 attach
```

#### `manifest.json`

```json
{
  "schema_version": 1,
  "exported_at": "2026-05-20T10:00:00Z",
  "claw_version": "0.4.0",
  "schools_sqlite_sha256": "ab12cd...",
  "schools_sqlite_bytes": 4829100,
  "counts": {
    "schools": 22,
    "departments": 87,
    "advisors": 4123,
    "quotas": 2901,
    "evaluations": 6788,
    "traces": 28741
  }
}
```

下游用 `schema_version` 决定是否能读；用 `schools_sqlite_sha256` 跳过重复导入；`counts` 给运维做日常体检。

`schema_version` **同时**写在 `schools.sqlite::meta` 表里，下游不读 manifest 也能拿到。

### 2.4 `schools.sqlite` schema (v1)

导出 schema 是与内部 DB **解耦**的稳定契约。字段命名 snake_case，与内部 DB 大部分同名；下游 Aurash 后端用 SQLAlchemy reflection 或显式 model 都行。

```sql
-- 元数据
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- rows: schema_version=1 / exported_at / claw_version

-- 学校 / 院系
CREATE TABLE school (
    code     TEXT PRIMARY KEY,    -- 'tsinghua'
    name_cn  TEXT NOT NULL,
    name_en  TEXT
);

CREATE TABLE department (
    school_code TEXT NOT NULL REFERENCES school(code),
    code        TEXT NOT NULL,    -- 'cs', 'iiis', ...
    name_cn     TEXT NOT NULL,
    PRIMARY KEY (school_code, code)
);

-- 导师
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
    email_obfuscated       INTEGER NOT NULL DEFAULT 0,  -- bool
    phone                  TEXT,
    photo_url              TEXT,
    bio_text               TEXT,
    research_interests     TEXT,    -- JSON array string: ["LLM 推理","可解释性"]
    is_recruiting          INTEGER, -- bool / NULL=未知
    recruiting_confidence  REAL,
    reputation_tag         TEXT,    -- positive|neutral|negative|unknown
    enriched_summary       TEXT,
    last_enriched_at       TEXT,    -- ISO 8601 UTC
    first_seen             TEXT NOT NULL,
    last_updated           TEXT NOT NULL
);

-- 多院系隶属
CREATE TABLE appointment (
    advisor_id   INTEGER NOT NULL REFERENCES advisor(id),
    school_code  TEXT    NOT NULL,
    dept_code    TEXT    NOT NULL,
    role         TEXT,    -- 主聘 / 兼聘 / NULL
    PRIMARY KEY (advisor_id, school_code, dept_code),
    FOREIGN KEY (school_code, dept_code) REFERENCES department(school_code, code)
);

-- 招生名额
CREATE TABLE quota (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    year        INTEGER,
    degree      TEXT,            -- PhD|MS|Postdoc
    count       INTEGER,
    confidence  REAL,
    raw_text    TEXT NOT NULL,
    source_url  TEXT
);

-- 第三方评价
CREATE TABLE evaluation (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    source      TEXT NOT NULL,
    source_url  TEXT,
    content     TEXT NOT NULL,
    rating      REAL,
    posted_at   TEXT             -- ISO 8601 UTC
);

-- 调研轨迹 (只导出每位 advisor 最新一次 run)
CREATE TABLE trace (
    id          INTEGER PRIMARY KEY,
    advisor_id  INTEGER NOT NULL REFERENCES advisor(id),
    step_idx    INTEGER NOT NULL,
    kind        TEXT NOT NULL,   -- search|read|final
    label       TEXT NOT NULL,
    detail      TEXT NOT NULL
);

-- 索引：覆盖 Aurash 前端所有 filter/sort 路径
CREATE INDEX idx_advisor_school        ON advisor(school_code);
CREATE INDEX idx_advisor_recruiting    ON advisor(is_recruiting);
CREATE INDEX idx_advisor_reputation    ON advisor(reputation_tag);
CREATE INDEX idx_advisor_last_enriched ON advisor(last_enriched_at);
CREATE INDEX idx_appt_school_dept      ON appointment(school_code, dept_code);
CREATE INDEX idx_appt_advisor          ON appointment(advisor_id);
CREATE INDEX idx_quota_advisor         ON quota(advisor_id);
CREATE INDEX idx_eval_advisor          ON evaluation(advisor_id);
CREATE INDEX idx_trace_advisor         ON trace(advisor_id, step_idx);

-- FTS5：全文搜索 advisor (q 输入框)
CREATE VIRTUAL TABLE advisor_fts USING fts5(
    name_cn, bio_text, research_interests, enriched_summary,
    content='advisor', content_rowid='id',
    tokenize='unicode61 remove_diacritics 1'
);
-- 导出末尾一次性 INSERT INTO advisor_fts(rowid,...) SELECT id,... FROM advisor;
```

**字段映射**（claw 内部 DB → schools.sqlite）：
- `advisor.research_interests_raw` (CSV) → `advisor.research_interests` (JSON array)
- 内部 `appointment(advisor_id, department_id)` → 导出 `appointment(advisor_id, school_code, dept_code)`（消除内部数字 ID 依赖）
- `enrichment_trace WHERE run_id = (latest by advisor)` → `trace`
- 内部 `source_snapshot`：**不导出**，是 forensic 数据
- 内部 `advisor.raw_quota_text`：**不导出**，已被结构化成 `quota` 表

### 2.5 Exporter 实现

新文件 `src/claw/export/sqlite_exporter.py`：

```python
def export_sqlite(out_dir: Path) -> ExportSummary:
    """从内部 DB 全量导出到 out_dir/{manifest.json, schools.sqlite}。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "schools.sqlite"
    db_path.unlink(missing_ok=True)  # 总是重建，导出是不可变快照

    with sqlite3.connect(db_path) as out:
        out.executescript(_SCHEMA_DDL)  # 见 2.4
        with session_scope() as src:
            _write_meta(out)
            _copy_schools(out, src)
            _copy_departments(out, src)
            _copy_advisors(out, src)         # 含 CSV → JSON 转换
            _copy_appointments(out, src)     # 内部 dept_id → (school_code, dept_code)
            _copy_quotas(out, src)
            _copy_evaluations(out, src)
            _copy_latest_traces(out, src)    # GROUP BY advisor_id, MAX(run_id)
            _rebuild_fts(out)
        out.execute("PRAGMA optimize")

    sha = _sha256_file(db_path)
    manifest = _build_manifest(db_path, sha)
    (out_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    return ExportSummary(...)
```

注意点：
- **总是重建** db_path：导出是不可变快照，避免半成品
- **trace 只导最新一次 run**：用 `SELECT advisor_id, MAX(run_id) ...` 的子查询确定 run_id
- **FTS rebuild 末尾做**：先把 advisor 数据全 INSERT 完再 rebuild，比 trigger-driven 维护快
- **PRAGMA optimize**：导出末尾跑一次，让 ANALYZE 统计落盘，下游查询规划器开箱即用
- **可选 PRAGMA journal_mode=DELETE + VACUUM**：导出文件没有 -wal / -shm 旁路文件，下游 attach 干净

### 2.6 CLI

升级 `cli/__init__.py`：

```bash
uv run claw export --format sqlite --out ./exports          # 全量 (推荐)
uv run claw export --format csv    --out ./advisors.csv     # 保留 v0.1 csv (扁平瘦版，Excel 用)
```

`csv` 模式保留：单文件、扁平、给 Excel 排序投递清单用；不含 quotas/evaluations/trace。`sqlite` 模式给 Aurash 用。

---

## 3. Tasks

| # | 任务 | 改动文件 |
|---|---|---|
| T1 | 加 `EnrichmentTrace` 表 + 幂等迁移 | `models/db.py` |
| T2 | `storage/repo.py::append_trace` | `storage/repo.py` |
| T3 | 富化器写盘 trace（run_id + step_idx） | `enrichers/web_research.py` |
| T4 | DDL 常量 (`_SCHEMA_DDL`)，与 §2.4 严格对齐 | `export/schema.py` (新) |
| T5 | exporter (`export_sqlite` + manifest 生成) | `export/sqlite_exporter.py` (新) |
| T6 | CLI 升级 `claw export --format sqlite` | `cli/__init__.py` |
| T7 | 测试：fixture 内部 DB → 导出 → reopen schools.sqlite → 字段断言 + FTS smoke | `tests/test_export_sqlite.py` (新) |
| T8 | 文档：`docs/EXPORT_SCHEMA_v1.md`（DDL + 字段表 + 字节级示例） | `docs/EXPORT_SCHEMA_v1.md` (新) |

---

## 4. Schema 演进规则

- 加字段（advisor / quota / etc. ALTER ADD COLUMN）：**只能加，不能改**。`meta.schema_version` 保持 1。下游忽略未知列。
- 加新表：等同加字段，schema_version 不变。
- 删字段 / 改语义：升 `schema_version` 到 2，下游需要显式适配。
- 字段重命名：等价于"加新列 + 旧列保留一个 minor 周期"。
- FTS 列变更：FTS5 表 rebuild 不算 schema breaking，下游不感知。

下游 attach 后第一件事：`SELECT value FROM meta WHERE key='schema_version'`，断言 `<= MAX_SUPPORTED`。

---

## 5. 分发渠道（与 Aurash 部署对接）

claw 跑在用户本地（README §"安装"），exports 怎么到 Aurash 生产 (huawei2)？三个选项，由 Aurash 侧决定，claw 这边只负责"把文件吐到 ./exports/"：

1. **HF Dataset**：Aurash 现有 `make sync-push` 通道，最自然。claw 跑完 export → 用户 `make schools-push` → huawei2 `make schools-pull`。
2. **rsync over Tailscale**：`rsync -av ./exports/ huawei2:/home/winbeau/Aurash/backend/data/schools/`
3. **手动 scp**：临时调试用。

claw 不预设渠道，只保证 `./exports/` 是 self-contained（两文件 + sha 自校验，无 -wal/-shm 旁路）。

---

## 6. 性能预期

参考 4000 行 advisor + ~30k trace 行规模：
- 导出耗时：单进程几秒（主要在 FTS rebuild + sha256）
- `schools.sqlite` 体积：估 3-10 MB（trace `detail` 字段是主体；可考虑 zstd 压 detail 但当前不做）
- 下游 attach 后查询：indexed 过滤 + 限 50 行分页 < 1ms；FTS 搜索 < 10ms
- 进程常驻内存：SQLite mmap 几 MB，与数据量解耦

---

## 7. 不在 v0.4 范围

- 增量 export（diff 上次）→ v0.5 再说，先全量。月级别更新节奏下，4000 行重建够便宜。
- photo 二进制下载 → 仍只导 URL，下游自己代理或不显示
- 多语言（name_en 已有但不补齐英文化）
- evaluation 评分聚合（rating 平均值）→ 下游 SQL 算更合适
- detail 字段压缩 → 体积够小，不优化
