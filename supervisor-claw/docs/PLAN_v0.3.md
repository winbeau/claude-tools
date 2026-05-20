# v0.3 实施方案：招生信号抽取（quota enricher）

> 范围：把 v0.2 落库的 ~3000 名导师的 `bio_text` / 主页文本里的"招生段"喂给 DeepSeek，结构化产出 `{degree, year, count, is_recruiting, confidence}`，写入 `quota_info` 表 + 回填 `advisor.is_recruiting`。
>
> 不在范围：第三方评价（v0.4）、Web UI（v0.5）。

---

## 1. 现状盘点

已有基础设施（不重复造）：

- `core/llm.py::chat_json(system, user)` — DeepSeek (openai SDK + base_url)，`response_format={"type": "json_object"}`，`temperature=0.0`。直接复用。
- `models/db.py::QuotaInfo` — 表已建好，字段齐全：`advisor_id / year / degree / count / raw_text / source_url / confidence / extractor`。`extractor="regex"` 是默认值，本期写 `"deepseek"`。
- `models/db.py::Advisor.raw_quota_text` / `is_recruiting` — 字段就位，目前大多为空。
- `storage/repo.py::append_quota` — 已存在（research agent 在用）。
- `enrichers/web_research.py` — 已有自主研究 agent，写过 `quota_info`；本期只做"基于本地 bio 文本的批量抽取"，路径上独立、互不依赖。

缺口：

1. 没有 quota 抽取器模块。
2. crawl 期间没有从 bio 中切出"招生段"，`raw_quota_text` 大多 NULL。
3. CLI 没有 `enrich` 子命令。
4. 没有评估脚本（人工标注 → 准确率回归）。

---

## 2. 模块设计

### 2.1 招生段定位（不调 LLM，纯本地启发式）

新增 `src/claw/core/quota_text.py`：

```python
KEYWORDS = (
    "招生", "招收", "招聘", "招博", "招硕",
    "名额", "指标", "保研", "推免",
    "欢迎报考", "欢迎加入", "欢迎咨询",
    "recruit", "Ph.D", "PhD", "Master",
    "openings", "positions",
)

def extract_quota_segment(bio_text: str, *, window: int = 400) -> str | None:
    """在 bio 里找第一处关键词命中，向前后各取 window 字符。
    返回的段落保证 <= 2 * window + len(keyword) 字符；多关键词命中合并相邻窗口。
    无命中时返回 None。"""
```

要求：
- 多个关键词窗口若重叠或相邻 (<100 字符)，合并成一段。
- 命中段最长截到 2KB（DeepSeek prompt 预算）。
- 单测覆盖：(a) 单关键词、(b) 多关键词合并、(c) 无命中、(d) 跨段落（按 `\n\n` 切片不能破坏窗口）。

### 2.2 回填 `raw_quota_text`

新增 CLI 子命令 `claw backfill-quota-text`：扫所有 `bio_text IS NOT NULL AND raw_quota_text IS NULL` 的 advisor，跑 `extract_quota_segment`，写 `advisor.raw_quota_text`。

也在 `pipeline/runner._process_profile` 里加一次调用——下次 crawl 直接产出，老数据用 backfill 命令补。

### 2.3 LLM 抽取器

新增 `src/claw/enrichers/quota_extractor.py`：

```python
SYSTEM = """你是中国高校研究生招生信息抽取助手。给定一段导师主页的自我介绍文本，
判断该导师当前是否招生，并给出每个学位类型（博士/硕士/博后）的年度名额。
严格输出 JSON，schema 见 user。无证据宁可留空也不要编造。"""

USER_TEMPLATE = """导师：{name}（{school} {dept}）
主页文本片段：
\"\"\"{quota_segment}\"\"\"

请输出如下 JSON：
{{
  "is_recruiting": true | false | null,         # null = 无信号
  "items": [
    {{
      "degree": "PhD" | "MS" | "Postdoc",
      "year": 2026 | null,
      "count": 1 | null,
      "evidence": "原文片段（≤80字）"
    }}
  ],
  "confidence": 0.0~1.0,                        # 综合置信度
  "notes": "..."                                 # 可选
}}"""

@dataclass
class QuotaResult:
    is_recruiting: bool | None
    items: list[QuotaItem]
    confidence: float
    notes: str = ""

def extract_quota(advisor_name: str, school: str, dept: str,
                  quota_segment: str) -> QuotaResult: ...
```

约束：
- 入参 `quota_segment` 必须非空，否则跳过，不调 LLM。
- 调用 `llm.chat_json(SYSTEM, USER_TEMPLATE.format(...))`，model 默认 `deepseek-chat`（V4）。
- 解析失败（JSON 损坏 / schema 不匹配）→ 重试 1 次（temperature=0.1）→ 仍失败记 `confidence=0.0`、空 items。
- `confidence < 0.5`：**只**写 `raw_quota_text`，不写 `is_recruiting`，不写 `quota_info` 行（避免污染）。
- `confidence >= 0.5`：写 `advisor.is_recruiting` + 每个 `items[i]` 一行 `quota_info`（`extractor="deepseek"`）。

### 2.4 批量 pipeline

新增 `src/claw/pipeline/enrich_runner.py`：

```python
async def enrich_quota(
    *,
    school_code: str | None = None,
    only_missing: bool = True,    # 已有 deepseek 抽取结果就跳过
    limit: int | None = None,
    concurrency: int = 3,         # DeepSeek 并发上限
) -> EnrichStats:
    """1. 选目标 advisor（bio_text 非空，且 raw_quota_text 非空 或 现场抽段）
       2. asyncio.Semaphore(concurrency) 控并发
       3. 每条 advisor 调 extract_quota → 写库
       4. 失败重试 2 次（指数退避，基于 tenacity）"""
```

`EnrichStats`：`{candidates, processed, written, low_confidence, errors}`。

### 2.5 CLI

新增 `src/claw/cli/__init__.py` 里：

```bash
claw enrich --source quota [--school <code>] [--limit N] [--concurrency 3] [--redo]
claw backfill-quota-text [--school <code>]
```

`--redo` 强制对已有 `extractor="deepseek"` 的 advisor 重抽（覆盖前先 DELETE 旧 quota_info 行）。

---

## 3. 数据流

```
crawl (已落库)
  └─ advisor.bio_text
        └─ backfill-quota-text  →  advisor.raw_quota_text
              └─ enrich --source quota
                    ├─ raw_quota_text 非空? 否 → skip
                    ├─ DeepSeek chat_json → QuotaResult
                    ├─ confidence < 0.5  → 仅留 raw_quota_text
                    └─ confidence ≥ 0.5  → write advisor.is_recruiting
                                         + write quota_info rows
```

---

## 4. 测试与评估

### 4.1 单元测试（`tests/test_quota.py`）

- `extract_quota_segment`：4 个用例（见 2.1）。
- `QuotaResult` schema 解析：mock `chat_json` 返回 JSON dict，验证字段映射与 `confidence` 阈值分支。
- `enrich_runner` 主流程：mock `extract_quota`，验证 only_missing / limit / 并发不会重复写入。

### 4.2 端到端评估（`tests/test_quota_eval.py`，可选 marker `@pytest.mark.live`）

- 准备 `tests/fixtures/quota_labels.jsonl`：30 条人工标注样本，字段 `{name, school, dept, raw_quota_text, expected_is_recruiting, expected_count_phd}`。
- 跑真实 DeepSeek，比对预期。验收线：`is_recruiting` 准确率 ≥ 85%，`count` 误差 ≤ 1 的比例 ≥ 70%。
- 默认 `pytest -m "not live"` 跳过。

### 4.3 成本预估

- 命中段平均 ~800 字符 ≈ 400 tokens；输出 ~200 tokens。
- DeepSeek V4 价格：输入 ¥2/M tokens，输出 ¥8/M tokens（按 deepseek.com 公开价）。
- 3000 advisor × (400 in + 200 out) ≈ 1.2M in + 0.6M out ≈ **¥7 / 全量一轮**。

---

## 5. 风险与降级

| 风险 | 应对 |
|---|---|
| 招生信息在子页面（不在 bio 里） | v0.3 不处理；标记 `raw_quota_text=NULL` 走 v0.4 web_research |
| LLM 编造（hallucination） | 强制要求 `evidence` 字段必须是原文子串；后处理校验，校验失败 → confidence × 0.3 |
| YAML / 字段变动后历史抽取需要重跑 | `--redo` + `extractor` 字段做版本标记 |
| DeepSeek 限流 | concurrency 默认 3；tenacity 指数退避 |
| 隐私（误抽出个人手机号等） | 抽取器只关心招生字段；prompt 显式禁止输出联系方式 |

---

## 6. 落地步骤（按顺序提交）

1. `core/quota_text.py` + 单测 → commit `feat(quota): bio 招生段定位启发式`
2. `pipeline/runner._process_profile` 接入 quota_text 抽取 + `cli backfill-quota-text` → commit `feat(quota): 入库即抽段 + backfill 命令`
3. `enrichers/quota_extractor.py` + 单测（mock LLM） → commit `feat(quota): DeepSeek 招生抽取器`
4. `pipeline/enrich_runner.py` + `cli enrich --source quota` → commit `feat(quota): 批量 enrich pipeline + CLI`
5. `tests/fixtures/quota_labels.jsonl`（30 条手标）+ live 评估脚本 → commit `test(quota): 人工标注样本 + live 评估`
6. 全量跑一遍（WSL 本地）→ 出 `docs/reports/quota_v0.3_report.md`：覆盖率、confidence 分布、抽样错例。

---

## 7. 验收清单

- [ ] `uv run pytest tests/test_quota.py` 全绿
- [ ] `uv run claw backfill-quota-text` 在 7 校全量库上不报错，`raw_quota_text` 命中率 ≥ 60%
- [ ] `uv run claw enrich --source quota --school tsinghua --limit 20` 端到端成功，至少 50% 写入 `is_recruiting`
- [ ] 全量跑完后：DB 里 `advisor.is_recruiting IS NOT NULL` 的占比 ≥ 30%
- [ ] live 评估 `is_recruiting` 准确率 ≥ 85%
- [ ] 全量一轮 DeepSeek 费用 ≤ ¥10
