# v0.3 实施方案（已修订）：agent 综合 enrichment

> **关键转向**：放弃"bio 启发式定位招生段 → DeepSeek 抽取"。理由：pku 207 人样本里 bio_text 内含"招生/招收/欢迎报考"等关键词的只有 **3 人（1.4%）**，且 91/207 bio 完全为空。中国导师不在官网写招生计划——写在课题组站、知乎、公众号、师兄师姐口碑里。
>
> 改为：把 `enrichers/web_research.py` 升级成 production-grade **批量 agent enrichment**，DeepSeek tool calling 自主上网搜+综合，产出"可投递依靠"的结构化报告。

---

## 1. 目标产出

每位 advisor 跑完 agent enrichment 后，DB 中固化：

- `advisor.is_recruiting` (bool|null) — 是否在当前周期招生
- `advisor.recruiting_confidence` (float 0-1) — **新增列**
- `advisor.reputation_tag` (enum, **新增列**) — positive / neutral / negative / unknown
- `advisor.enriched_summary` (text, **新增列**) — agent 写的 1-2 句话综合判断
- `advisor.last_enriched_at` (datetime, **新增列**) — 上次跑 enrich 的时间，用于增量
- `evaluation` 表：每条证据一行（已有，agent 已经在写）
- `quota_info` 表：明确招生名额一行（已有，agent 已经在写）

CLI 一条 `claw enrich --source agent --school pku` 就能把整校跑出来。

最终 `claw export --recruiting-only --format csv` 出来的表能直接拿来排投递优先级。

---

## 2. 现状盘点（已有的）

`enrichers/web_research.py` 框架已经成熟，**不重写**，只补功能：

- ✅ DeepSeek tool calling 循环 (`research_advisor`)
- ✅ Tools: `search_web`, `read_page`, `submit_evaluation`, `submit_quota`, `finish`
- ✅ Playwright + cn.bing.com（无登录、低验证码）+ BrowserContext pool
- ✅ BLOCKED_DOMAINS 黑名单（百度百科、Wikipedia 等）
- ✅ TUI 展示（`enrichers/research_display.py` + Nerd Font icons）
- ✅ CLI `claw research --school pku` 已能跑

缺口：

1. agent 没有总结性输出 — 现在只往 evaluation/quota_info 写零散行，没有"该导师值不值得投"的总结字段
2. Advisor 表缺 4 列（confidence / reputation_tag / enriched_summary / last_enriched_at）
3. 没有增量恢复：跑一半挂了重启会重复跑（`only_missing` 只判断有没有 evaluation 行，不够精细）
4. 没有 token / 成本统计
5. 命令是 `research`，从命名上看像"调研"工具，不像批量 enrichment 入口
6. agent 现在偏保守，对"已确认招生 / 无招生信号"两种情况都倾向不提交 — 需要新的 `submit_report` 工具明确收口

---

## 3. 设计

### 3.1 数据库迁移

SQLite + SQLModel，直接 `ALTER TABLE` 加列，做幂等迁移函数：

```python
# models/db.py 新增字段
class Advisor(SQLModel, table=True):
    ...
    is_recruiting: bool | None = None
    recruiting_confidence: float | None = None      # NEW
    reputation_tag: str | None = None               # NEW: positive|neutral|negative|unknown
    enriched_summary: str | None = None             # NEW: agent 写的 1-2 句话
    last_enriched_at: datetime | None = None        # NEW

def _ensure_enrichment_columns(engine):
    """幂等：检查 advisor 表是否缺新列，缺则 ALTER ADD。"""
    cols = {row[1] for row in engine.execute("PRAGMA table_info(advisor)")}
    for col, ddl in [
        ("recruiting_confidence", "REAL"),
        ("reputation_tag", "TEXT"),
        ("enriched_summary", "TEXT"),
        ("last_enriched_at", "DATETIME"),
    ]:
        if col not in cols:
            engine.execute(f"ALTER TABLE advisor ADD COLUMN {col} {ddl}")
```

`init_db()` 末尾调用一次。

### 3.2 新的 agent 工具：`submit_report`

在 `enrichers/web_research.py` 的 tools schema 加：

```python
{
    "type": "function",
    "function": {
        "name": "submit_report",
        "description": "最终总结：必须在 finish 之前调用一次。综合本轮搜索读到的所有证据，给出该导师投递参考的结构化判断。",
        "parameters": {
            "type": "object",
            "required": ["is_recruiting", "recruiting_confidence", "reputation_tag", "summary"],
            "properties": {
                "is_recruiting": {"type": ["boolean", "null"], "description": "true=明确招生 / false=明确不招 / null=未知"},
                "recruiting_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reputation_tag": {"enum": ["positive", "neutral", "negative", "unknown"]},
                "summary": {"type": "string", "maxLength": 400, "description": "1-2 句话给读者的投递参考"}
            }
        }
    }
}
```

入参校验后写回 `advisor` 表 4 个新列 + 更新 `last_enriched_at = utcnow()`。

System prompt 加强：
- "最终轮必须先调用 `submit_report`，再调用 `finish`"
- "evidence 不足时 `confidence` 给低分但仍然要提交"

### 3.3 Seed prompt（省 token + 提高召回）

在拼 user prompt 时附带：

- 已有 bio_text（截 800 字）
- crawler 抽出的 keyword 命中段（如有）— 用现有 `bio_text` 跑一遍 keyword 扫描（不调 LLM）
- 已有 evaluation 行的简短摘要（如果是增量重跑）
- 提示 agent 优先去课题组官网（搜 "{name} {school} 课题组" / "{name} group"）和知乎

### 3.4 批量 driver + 增量

新增 `pipeline/enrich_runner.py`（轻量包装 `research_advisor`）：

```python
async def enrich_with_agent(
    *,
    school_code: str,
    dept_codes: list[str] | None = None,
    only_missing: bool = True,        # last_enriched_at 为空 或 > N 天前
    stale_days: int = 30,
    limit: int | None = None,
    max_iter: int = 8,
    concurrency: int = 2,             # 并发跑 advisor（每个独占一个 BrowserContext）
    headed: bool = False,
) -> EnrichStats:
    ...
```

`EnrichStats`：candidates / processed / submitted_report / no_report / errors / total_tokens / wall_seconds。

跑完打印一行总结，并把详细 token usage 落到 `data/enrich_logs/<timestamp>.jsonl`（每行 = 一位 advisor 的 token 用量 + 报告摘要）。

### 3.5 CLI

`cli/__init__.py` 新增：

```bash
# 主入口
claw enrich --source agent --school pku [--dept ai] [--limit 20] [--max-iter 8] \
            [--concurrency 2] [--all]            # --all 强制重跑（忽略 last_enriched_at）
            [--headed]                            # 调试用

# 旧的 research 子命令保留为别名，但内部走同一个 enrich_runner
```

### 3.6 BLOCKED_DOMAINS 微调

继续黑名单百度百科 / Wikipedia / 维基。**新增白名单优先级**（agent 看到这些来源时把 confidence 加权）：

- `zhihu.com/question/*` / `zhihu.com/answer/*` — 学生评价高密度
- `xiaohongshu.com` — 近年招生海报转发
- `*.edu.cn/group/*` / `*.lab.*` — 课题组官网

不强制，只在 system prompt 里给 agent 提示。

---

## 4. 落地步骤（按 commit 提交）

| step | 内容 | 验收 |
|---|---|---|
| 1 | DB 迁移：4 个新列 + `_ensure_enrichment_columns()` | `claw doctor` 后用 `PRAGMA table_info(advisor)` 看到新列 |
| 2 | `submit_report` 工具 + system prompt 改造 + 写回 advisor 4 列 | pku 抽 3 人手动跑 `claw research --school pku --name X` 能看到 enriched_summary 入库 |
| 3 | Seed prompt：bio 关键词段 + 已有 evaluation 摘要 | 同 3 人重跑，token 用量明显下降（基线对比） |
| 4 | `pipeline/enrich_runner.py` + token telemetry + jsonl 日志 | 小批 5 人跑通，日志写出 |
| 5 | CLI `claw enrich --source agent` | `claw enrich --source agent --school pku --limit 5` 跑通 |
| 6 | pku 全量跑（207 人，预计 ~1.5 小时 × concurrency=2）| `claw stats` recruiting 列从 4 跳到 ≥40，export csv 抽样合理 |
| 7 | `docs/reports/enrich_v0.3_report.md` | 覆盖率、风评分布、成本、典型错例 |

---

## 5. 成本预估（DeepSeek V4）

- 单 advisor 6-8 轮 tool calling，每轮 ~2-3K input + ~300 output tokens
- ≈ 20K input + 2K output / advisor
- DeepSeek 价格：输入 ¥2/M、输出 ¥8/M
- 单 advisor ≈ ¥0.06，pku 207 人 ≈ **¥12-15**
- 7 校 ~3000 人全量一轮 ≈ **¥150-200**

第一次跑只跑 pku 单校做对照（¥15 试错成本可控）。

---

## 6. 验收清单

- [ ] DB 迁移幂等（重复跑 `claw doctor` 不报错）
- [ ] 单人 `claw research --school pku --name "张三"` 跑完后 advisor 表 4 列全填
- [ ] `claw enrich --source agent --school pku --limit 10 --concurrency 2` 端到端成功
- [ ] 增量：第二次跑同一批人，默认 skip（除非加 `--all`）
- [ ] `data/enrich_logs/*.jsonl` 含 token usage + 报告摘要
- [ ] pku 207 人全量跑完：recruiting 字段非空率 ≥ 60%，reputation_tag 非 unknown 率 ≥ 40%
- [ ] 成本 ≤ ¥20 / pku

---

## 7. 暂不做（明确划线 v0.4+）

- 知乎登录态复用（agent 当前用未登录访问，部分内容会被遮挡 —— 接受这个损失）
- mysupervisor / urfire 等评价站（需登录或 IP 限制，留 v0.4 人工辅助）
- 手工 CSV 评价导入（v0.4）
- Web UI（v0.5）
- 跨学院聘任去重的二次合并（已有 appointment 表能用，UI 出来再说）

---

## 8. 风险

| 风险 | 应对 |
|---|---|
| agent 跑 8 轮还没拿到信号 | `submit_report(is_recruiting=null, confidence=0.1, reputation_tag=unknown)` 也算成功，下次手工补 |
| Bing 验证码 | 已有 BrowserContext 切 headed + 人工通过；超过 N 次 captcha 自动暂停 |
| 知乎反爬 | agent 看 snippet 即可，不强求完整答案 |
| DeepSeek 限流 | concurrency 默认 2；tenacity 已封装重试 |
| 跑到一半电脑挂了 | 增量机制 (`last_enriched_at` + `--only-missing`) 直接续跑 |
