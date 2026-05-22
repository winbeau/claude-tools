# Email Backfill v0.5 — 5 并行 agent × 4 轮 查缺补漏

> 目的：把 20 校 4141 advisor 里 email IS NULL 的 ~ 1080 条尽量补齐，
> 同时顺手补任何缺漏字段。**纯增量，不动已有数据**。
> 之前的 v0.4 enrich chain 已完工（详见 git log），cost 已 sunk，
> 这一波 v0.5 重点是 email + 边角。

## 当前状态 (2026-05-22 锁数)

每校 email 覆盖率（按缺口由大到小）：

| 校 | total | with_email | enriched | gap | 主要原因 |
|---|---:|---:|---:|---:|---|
| **xidian** | 276 | **0** (0%) | 275 | **-276** | faculty.xidian.edu.cn 邮箱 JS 加密（`<span _tsites_encrypt_field>`），crawl 静态 HTML 拿不到 |
| **nwpu** | 231 | **0** (0%) | 231 | **-231** | teacher.nwpu.edu.cn TS-WAF stub，profile 都没拿到 |
| **hust** | 299 | 37 (12%) | 293 | **-262** | faculty.hust.edu.cn 邮箱在 `div.blockwhite.Ot-ctact` 里被 JS 加密 |
| **xjtu** | 138 | 42 (30%) | 137 | **-96** | wayback list 没邮箱；profile 95/152 也是 stub |
| **nudt** | 83 | **0** (0%) | 79 | **-83** | 军校 honour list 仅人名 / 极少公开 |
| **fudan** | 184 | 132 (72%) | 181 | -52 | bd 学院 list 阶段 email 抓不齐 |
| **pku** | 279 | 205 (73%) | 265 | -74 | 软微学院 67 个 name-only / 无 profile |
| **nju** | 229 | 172 (75%) | 227 | -57 | listm 子页有 partial |
| **ustc** | 175 | 151 (86%) | 170 | -24 | eeis 部分 lecturer 没 email |
| **bit** | 232 | 205 (88%) | 230 | -27 | 部分团队页无邮箱 |
| zju | 285 | 254 (89%) | 283 | -31 | cadcg / sw 个别没邮箱 |
| sysu | 180 | 166 (92%) | 178 | -14 | sai 几个 |
| tju | 170 | 157 (92%) | 170 | -13 | 学部 jsonl 内部少数 |
| nankai | 196 | 188 (96%) | 192 | -8 | ai 学院的 vsb table |
| sjtu | 349 | 341 (98%) | 345 | -8 | cse 跨链的 5 个 |
| shtech | 147 | 145 (99%) | 141 | -2 | 极个别 |
| buaa | 145 | 144 (99%) | 140 | -1 | 1 个 |
| uestc | 269 | 268 (99%) | 268 | -1 | 1 个 |
| seu | 134 | 134 (100%) | 130 | 0 | ✓ |
| tsinghua | 136 | 136 (100%) | 134 | 0 | ✓ |
| **总** | **4141** | **3070 (74%)** | **4069** | **~1071** | |

## 总体 5x4 编排

按 email 缺口排序，**每轮 5 校 / 共 4 轮 / 20 agent-run**：

| 轮 | 学校 | 累计目标 email |
|---|---|---:|
| **R1** | xidian, nwpu, hust, xjtu, nudt | +948 |
| **R2** | fudan, pku, nju, ustc, bit | +234 |
| **R3** | zju, sysu, tju, nankai, sjtu | +74 |
| **R4** | shtech, buaa, uestc, seu, tsinghua | +4 (主要是 last_enriched 补漏) |

每轮 5 agent **并行** （`Agent` 工具 `isolation: "worktree"`，单条父消息 5 个 tool 调用同时发出）。

## 共用基础设施（每轮起跑前先做）

### Phase 0 — 一次性建好这些模块（committed to main）

放在 `supervisor-claw/src/claw/enrichers/email_backfill.py`：

```python
# 1. JS-decoded email extractor
async def decode_js_email_on_page(page) -> str | None:
    """For faculty.xidian / faculty.hust / similar JS-encrypted pages:
       wait for the page's onload script to populate the email <span>,
       then read it via page.eval. If the JS does string-XOR or rot13,
       we may need site-specific decode logic.

       Return None if decode fails."""

# 2. Search-based email recovery
async def search_email_via_bing(sess, name: str, school: str, dept: str) -> str | None:
    """Use the stealth chromium session to bing-search the advisor + parse
       top 3 results' DOM for an email matching the school's domain pattern.

       Cascade: bing.com → cn.bing.com → duckduckgo.com / sogou.com"""

# 3. DBLP lookup with email hint
async def dblp_email_lookup(name: str, affiliation: str) -> str | None:
    """dblp's author page sometimes has 'homepage' link or affiliation
       text with email pattern. Parse + verify with regex."""

# 4. Educated guess + verify (LOW PRIORITY)
# def guess_email_from_pinyin(name_cn: str, school_domain: str) -> list[str]:
#     """e.g. 张伟@xjtu.edu.cn → ['zhangwei@xjtu.edu.cn', 'wei.zhang@...', ...]
#        Then verify by SMTP probe? Risky — skip unless needed."""

# 5. Surgical email-only upsert
def update_email_only(session, advisor_id: int, email: str, source: str) -> bool:
    """Only writes if advisor.email IS NULL. Returns True if updated.
       Logs the source ('js_decode' / 'bing' / 'dblp') for audit."""
```

新 CLI（in `cli/__init__.py`）：

```python
@app.command("backfill-email")
def backfill_email(
    school: str = typer.Argument(...),
    limit: int = typer.Option(0, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    strategy: list[str] = typer.Option(None, "--strategy"),
        # any subset of: js, bing, dblp (default = all 3 in cascade)
) -> None:
    """Email-only backfill, --only-missing semantics. Idempotent."""
```

工作流（per advisor with email IS NULL）：

```
1. has profile_url? → fetch via stealth → decode_js_email_on_page
2. miss?            → search_email_via_bing(name, school, dept)
3. miss?            → dblp_email_lookup(name, school)
4. still miss?      → leave NULL，写 backfill_log 标 'unfindable'
5. any hit?         → update_email_only + log source
```

### Phase 0 deliverables（用一个 agent 做完，1 轮单独跑）

1. `core/email_decoders.py` — site-specific JS decode（xidian 的 hex blob 解码等）
2. `enrichers/email_backfill.py` — 上述 5 个 helper
3. `cli/__init__.py` 加 `backfill-email` 命令
4. 测试：用 fixture（xidian 一个含加密 email span 的 profile HTML）验 decode 正确
5. push 到 main

## Phase 1 — 5 并行 agent / round

**触发**：父 agent 在单条消息里发 5 个 `Agent` tool 调用。每个 agent 的 prompt 模板：

```
你是 supervisor-claw v0.5 email backfill 子 agent，目标学校 = <CODE>。

【环境】
- harness 给你了 git worktree（cwd 是 worktree 根），分支自动建好
- repo 里已有 commit b70ec98+ 的 stealth crawler + email_backfill helper
- DB 在 huawei2 上 huawei2:~/projects/claude-tools/supervisor-claw/data/claw.db
  你**不能**直接动 huawei2，只能写代码 + commit；父 agent 在 huawei2 跑

【任务】
- 给学校 <CODE> 设计 email backfill 策略：
  - 看 src/claw/adapters/<code>.py 知其 profile DOM 怎么放 email
  - 看 schools.yaml 拿候选 list_urls
  - 看 docs/reports/<code>_report.md（如果存在）了解 v0.4 时的坑
  - 确定它的 email 缺失原因（JS 加密 / WAF / 无 profile / 邮箱永不公开）
- 在 src/claw/enrichers/sites/<code>_email.py 写 site-specific decoder
  - 必须导出一个 async def find_email(advisor, sess, deepseek_client) -> str | None
  - 实现：profile 重抓 → 解码 / search / dblp 级联
- 在 tests/test_email/test_<code>.py 写 ≥ 2 测试用例：fixture-based，
  - 用 fixture（从该校实际抓的 1 个含 email 的 HTML）验 decoder
  - 边界 case：完全没 email 应返回 None 不抛
- 在 docs/reports/email_backfill_<code>.md 写报告：
  - 缺失原因
  - 选定策略
  - 预期回收率
  - 已知限制

【验收】
- python3 -m compileall -q ... 退出 0
- 至少 2 commit：feat + docs report
- 报告里写明：执行命令应是
  `claw backfill-email <code> --strategy js,bing,dblp`
- 最终 reply 包含分支名 / worktree 路径 / commit hash 列表

【绝不】
- 不动 src/claw/adapters/<code>.py 主路径（不影响已有 crawl）
- 不动其他学校
- 不 push（父 agent 收集 + 合并）
- 不动 DB
```

## 各轮 agent 配对 + 学校特殊点提示（给 agent 用的）

### R1（最重）

| agent slot | school | 关键提示 |
|---|---|---|
| A | **xidian** | `faculty.xidian.edu.cn` 邮箱用 `<span class="encrypt-field" _tsites_encrypt_field>HEXBLOB</span>`，需要逆向 ts-encrypt.js 里的 xor key。提示：从 `<script src="..encrypt.js">` 抓 key, JS layer 简单 XOR + base64 |
| B | **nwpu** | `teacher.nwpu.edu.cn` TS-WAF 顽固；优先用 stealth_crawler 已有的 wayback 路径 + bing search "<name> nwpu.edu.cn site:" |
| C | **hust** | `faculty.hust.edu.cn` 用 `ImageScale.addimg()` JS 填 email，需要在 page.goto 后 await `page.wait_for_function("document.querySelector('.Ot-ctact').innerText.includes('@')")` |
| D | **xjtu** | 95 个 advisor 只有 list-level + wayback 不全；主力靠 bing/google 搜 "<name> 西安交通大学 email" |
| E | **nudt** | 军校公开极少；strategy 只能 dblp + bing。预期回收率 < 10%，写到 known limitations |

### R2

| agent | school | 提示 |
|---|---|---|
| A | fudan | bd 学院 list-only 的 52 个，profile 已 enriched，可尝试 enriched_summary 文本里 regex 邮箱 |
| B | pku | 软微 67 个 name-only，profile_url=None；只能 search/dblp |
| C | nju | 部分子页（zzp / kxkbd）邮箱在 plain text，重新解析即可 |
| D | ustc | eeis 讲师段，按 dept_code='ai-info' 子集 search |
| E | bit | 部分 jsml2 团队页 list-only，profile re-fetch + decoder |

### R3, R4

R3 (zju / sysu / tju / nankai / sjtu): 缺口都 < 35，主要是 enrich agent 漏掉的；
strategy 简化为 bing + dblp，不写专用 decoder。

R4 (shtech / buaa / uestc / seu / tsinghua): 缺口 ≤ 4，
agent 主要任务是验证 + 跑 cleanup pass，输出"已 100% 覆盖"报告即可。

## Phase 2 — 父 agent 收口（每轮跑完后）

每轮 5 agent 都返回后，父 agent：

1. **rename worktree 分支** → `feat/email-backfill-<code>`
2. **merge 5 个分支到 main**（按字母序，schools.yaml 不应冲突因为不动它）
3. **统一 push** origin/main
4. **huawei2 pull** + 跑 5 校的 `claw backfill-email <code>`：
   ```bash
   for s in <round-5-codes>; do
     LOG=data/email_backfill_${s}_$(date +%Y%m%dT%H%M%SZ).log
     nohup uv run claw backfill-email $s > $LOG 2>&1 < /dev/null &
   done
   wait
   ```
   （5 个并行；都只读+写 advisor 单字段，WAL 安全）
5. **后查**：
   ```sql
   SELECT s.code, COUNT(*) total,
          SUM(CASE WHEN email IS NOT NULL THEN 1 ELSE 0 END) AS with_email
   FROM advisor a JOIN school s ON s.id=a.school_id
   WHERE s.code IN (<5 codes>) GROUP BY s.code;
   ```
   对比 round 起跑前的基线数，看回收率。
6. **删 worktree + 分支**（同 v0.4 收口流程）
7. ScheduleWakeup 跟进 / 进下一轮

## 增量安全保证

* `update_email_only(session, advisor_id, email)` 函数**强制** `WHERE email IS NULL`，绝不覆盖已有
* `--dry-run` 模式只 SELECT + 打印候选 email，不写库
* 每个 advisor 的 email 写入都记到 `data/email_backfill_audit.jsonl`：
  ```json
  {"ts": "...", "advisor_id": 4216, "school": "xjtu", "name": "王志",
   "old_email": null, "new_email": "zhiwang@xjtu.edu.cn",
   "source": "bing", "confidence": 0.9}
  ```
* 出错可 `UPDATE advisor SET email=NULL WHERE id IN (<audit ids in roll-back window>)`

## 时间 & 成本预算

* Phase 0（基础设施）：1 个 agent 30-60 min，单独跑
* 每轮 5 agent 并行设计 + 报告：每 agent 30-60 min → 整轮 ~60 min
* 每轮 huawei2 执行：5 校并行 × (~30s × 200 advisor) ≈ 100 min/轮
* 4 轮总 wall：~10h（agents 设计 + 父 agent 执行 + 等待）
* DeepSeek token：bing search 不耗 token；只有 dblp + 部分页面 read 进 LLM
  预估 ≤ 2M token，cost < $1

## 成功标准

| 指标 | R1 后 | R4 后 |
|---|---:|---:|
| 全 20 校 email 覆盖率 | ≥ 85% | **≥ 92%** |
| xidian/nwpu/hust 三大缺口 | 各 ≥ 40% | 各 ≥ 60% |
| advisor 总数（不变）| 4141 | 4141 |
| email_backfill_audit.jsonl | ≥ 700 行 | ≥ 1000 行 |

无法到 100% 是预期（军校 / 部分双聘 / 仅 list 无 profile 的人本来就不公开）。

## 启动指令（新会话用）

打开新会话后，让 Claude：

```
/loadplan supervisor-claw/docs/plans/email_backfill_5x4.md
```

或者直接说："按 docs/plans/email_backfill_5x4.md 执行 Phase 0。"

执行顺序：
1. Phase 0（1 agent 单独写 email_backfill 框架，commit + push）
2. R1（5 agent 并行设计 xidian/nwpu/hust/xjtu/nudt 的 decoder）
3. 父 agent 收口 + huawei2 跑 R1 backfill
4. R2 设计 → 收口 → 跑
5. R3 → 收口 → 跑
6. R4 → 收口 → 跑
7. 全完写 final report：每校最终 email 覆盖 + 历史对比

## 上下文锚点（避免重复探路）

* 当前 git HEAD: `7344d77` (上次 push) — 后续如有新 commit 在此后
* huawei2 项目路径: `/home/winbeau/projects/claude-tools/supervisor-claw`
* huawei2 venv: 3.12 (locked in .python-version)
* DeepSeek model: `deepseek-v4-flash` (.env)
* `claw watch` 已有，可监 backfill 进度（虽然 watch 主显示 enrich，可补一个 email_backfill 视图）
* tmux session: `claude-tools-huawei2` (VPS → WSL → huawei2，自动 reconnect)
* 已完成 chain：crawl 20 校 / enrich 4071 advisor / 总 cost ~$57
* v0.4 final summary 在 `git log --grep="enrich chain COMPLETE"` 之前的 commit message

> 下次会话 Claude 应先看 `git log -10` + 本文件 + huawei2 上跑 `claw stats` 拿当前基线，再开干。
