# supervisor-claw v0.2 — 北京大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.2 并行 rollout 的一份；同时还有另外 5 个 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**，你不会跟他们冲突。

## 你的标识

- **学校 code**: `pku`
- **学校名**: 北京大学
- **要支持的学院**: `eecs` (信息科学技术学院), `ai` (智能学院), `wangxuan` (王选所), `cfcs` (前沿计算研究中心)
- **分支**: `feat/adapter-pku`
- **worktree 路径**: `~/Projects/adapter-pku`（main 仓库的兄弟目录）

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools                              # main repo
git fetch origin
git worktree add ~/Projects/adapter-pku -b feat/adapter-pku origin/main
cd ~/Projects/adapter-pku                               # 之后所有干活都在这里
```

确认就位：

```bash
pwd                       # 应该是 ~/Projects/adapter-pku
git status                # 应该在 feat/adapter-pku 分支，干净
ls supervisor-claw/       # 应该看到 src/ tests/ schools.yaml 等
```

## Step 1 — 必读（按顺序，全部读完再动手）

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务说明 + **11 个清华踩过的坑**（必看）
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现（136/136 跑通）
3. **`supervisor-claw/src/claw/adapters/base.py`** ← SchoolAdapter ABC + `@register`
4. **`supervisor-claw/src/claw/models/pydantic_models.py`** ← AdvisorPartial 字段
5. **`supervisor-claw/schools.yaml`** ← 你的 `pku` 段 list_urls（**可能过期，要验证**）

## Step 2 — 调研页面结构

对每个 dept 的 `list_url` 用 WebFetch 验证：

- 列表是分页还是单页？分页参数名？（北大常见 `_NNNN.htm` 或 `?currPage=N`）
- 每个老师条目里有什么字段（姓名 / 职称 / 邮箱 / 个人页 / 头像）？
- 邮箱是明文 / `[at]` 变体 / 图片？
- 智能学院特殊点：list 同时混了**研究生 / 博士后**条目，要在 parse_list 里过滤（URL 里看 `yjs/ssb/bys` 段，或职称字段判断）

不可达就 WebSearch 找正确入口，把修正写回 `schools.yaml` 的 `pku` 段（**只动 pku 段**）。

然后 `curl` 抓样本到 fixture：

```bash
cd ~/Projects/adapter-pku/supervisor-claw
mkdir -p tests/fixtures/pku
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/pku/list_<dept>.html
# 再抓 1-2 个 profile 页（每种 dept 选 1 个代表）
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/pku.py`

骨架直接照抄 tsinghua.py，按北大的页面结构调整：

```python
@register
class PkuAdapter(SchoolAdapter):
    school_code = "pku"
    supports = {"eecs", "ai", "wangxuan", "cfcs"}

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

**重点**：4 个学院页面结构可能各不相同。如果发现差异大到没法一个 adapter 装下，**给每个 dept 一个小 helper 函数**（比如 `_parse_eecs_list / _parse_ai_list`），用 list_url 或 dept hint 路由。

## Step 4 — 写测试

文件：`supervisor-claw/tests/test_parsers/test_pku.py`

至少 3 个测试：

1. `test_parse_list_basic`：list 解析数 ≥ 10，名字 100%，邮箱率 ≥ 60%（北大邮箱混淆比清华严重）
2. `test_parse_profile_no_js_no_nav`：bio_text 不含 `function`，raw_quota_text 不含 `招生招聘` / `校友会` 之类 nav 词
3. `test_parse_profile_has_research_or_bio`：至少有 bio 或 research_interests 一项非空
4. （可选）`test_filter_students`：list 不应包含明显的研究生条目

## Step 5 — 自查

```bash
cd ~/Projects/adapter-pku/supervisor-claw
python3 -m compileall -q src/claw/adapters/pku.py tests/test_parsers/test_pku.py
# 如果环境装了项目依赖，可选：
# uv run pytest tests/test_parsers/test_pku.py -v
```

## Step 6 — 提交 + 写完成报告

```bash
cd ~/Projects/adapter-pku/supervisor-claw
git add src/claw/adapters/pku.py \
        tests/test_parsers/test_pku.py \
        tests/fixtures/pku/
# 如果改了 schools.yaml:
git add schools.yaml

git commit -m "feat(pku): 北京大学 adapter v1

- depts: eecs, ai, wangxuan, cfcs
- fixtures: N list + M profile (real fetched HTML)
- handles: <列出该校特殊结构，比如 SPA / 邮箱混淆 / 学生条目过滤>

Co-Authored-By: <你的署名或 Claude>"
```

然后落盘完成报告到 `supervisor-claw/docs/reports/pku_report.md`：

```markdown
# 北京大学 adapter — completion report

- **branch**: `feat/adapter-pku`
- **commit**: <git rev-parse HEAD>
- **worktree**: `~/Projects/adapter-pku`

## test results
- compileall: PASS / FAIL
- pytest: <未跑 / N passed / N failed>

## fixture coverage

| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| eecs | ... | ... | ... | ... |
| ai | ... | ... | ... | ... |
| wangxuan | ... | ... | ... | ... |
| cfcs | ... | ... | ... | ... |

## 特殊结构观察（1-3 条，喂给 orchestrator 归纳）
1. ...
2. ...

## schools.yaml 改动
<diff 或 "无改动">

## known limitations
<比如 "cfcs 是 SPA，httpx 拿不到师资列表，建议 v0.4 用 Playwright">
```

最后把报告也 commit 上：

```bash
git add docs/reports/pku_report.md
git commit -m "docs(pku): completion report"
```

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py`（6 agent 都改 = 合并冲突，用户最后统一加 import）
- ❌ 改 `core/`、`models/`、`storage/`、`pipeline/`、`cli/`、其他学校的 adapter
- ❌ `uv sync` / 装新依赖（VPS 资源有限）
- ❌ 真打学校的网做大规模爬取（fixture 测试就够，**绝对不能** `uv run claw crawl pku`）
- ❌ commit `data/` / `.env` / API key
- ❌ `git push` 到 origin（用户手动 merge）

## 完成的标志

- worktree 里 `git log --oneline` 至少 2 个 commit（adapter + report）
- `supervisor-claw/docs/reports/pku_report.md` 存在且填好
- 报告里说出该校 1-3 个特殊结构观察

开始吧。质量 > 速度。不确定的字段留空，让 v0.3 的 DeepSeek agent 补 —— 别 hardcode 编出来。
