# supervisor-claw v0.2 — 南京大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.2 并行 rollout 的一份；同时还有另外 5 个 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**，你不会跟他们冲突。

## 你的标识

- **学校 code**: `nju`
- **学校名**: 南京大学
- **要支持的学院**: `cs` (计算机科学与技术系), `ai` (人工智能学院), `sw` (软件学院)
- **分支**: `feat/adapter-nju`
- **worktree 路径**: `~/Projects/adapter-nju`

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools
git fetch origin
git worktree add ~/Projects/adapter-nju -b feat/adapter-nju origin/main
cd ~/Projects/adapter-nju
git status              # 应在 feat/adapter-nju 分支，干净
```

## Step 1 — 必读（按顺序）

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务说明 + **11 个清华踩过的坑**（必看）
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现
3. **`supervisor-claw/src/claw/adapters/base.py`** ← SchoolAdapter ABC
4. **`supervisor-claw/src/claw/models/pydantic_models.py`** ← AdvisorPartial 字段
5. **`supervisor-claw/schools.yaml`** ← 你的 `nju` 段 list_urls

## Step 2 — 调研页面结构

南大特有的几个点要注意：

- 南大 AI 学院（`ai.nju.edu.cn/people/list.htm`）列表里**只有姓名+职称**，邮箱必须进个人页才能拿
- 个人页 URL 有两种风格：`/_redirect?...`（跳转）和 `/xx/xx/cXXXXXa<id>/page.htm`，少数老师挂的是**外链个人主页**（不在校域）—— 这种把 `homepage` 设成外链就好，**不要深爬**
- 计算机系 (`cs.nju.edu.cn/szdw/`) 经常按职称分类多页，每类一个 list URL —— 都列进 schools.yaml `list_urls`
- 软件学院的师资页可能用 SPA / JS 渲染，httpx 拉不到 → 在 known limitations 标记，建议 v0.4 Playwright

对每个 dept 用 WebFetch 验证 → 不可达就 WebSearch 找正确入口 → 修回 `schools.yaml` 的 `nju` 段。

```bash
cd ~/Projects/adapter-nju/supervisor-claw
mkdir -p tests/fixtures/nju
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/nju/list_<dept>.html
# 再抓 1-2 个 profile
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/nju.py`

```python
@register
class NjuAdapter(SchoolAdapter):
    school_code = "nju"
    supports = {"cs", "ai", "sw"}

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

3 个学院页面差异大就拆 helper 函数，按 list_url 路由。

## Step 4 — 写测试

文件：`supervisor-claw/tests/test_parsers/test_nju.py`

至少 3 个测试：list 解析数（≥ 10）+ 邮箱率（南大 AI 学院列表无邮箱是正常的，profile 层补就行）+ 无 JS/nav 泄漏。

## Step 5 — 自查

```bash
python3 -m compileall -q src/claw/adapters/nju.py tests/test_parsers/test_nju.py
# 可选: uv run pytest tests/test_parsers/test_nju.py -v
```

## Step 6 — 提交 + 写完成报告

```bash
git add src/claw/adapters/nju.py \
        tests/test_parsers/test_nju.py \
        tests/fixtures/nju/
# 如改了 schools.yaml:
git add schools.yaml

git commit -m "feat(nju): 南京大学 adapter v1

- depts: cs, ai, sw
- fixtures: N list + M profile
- handles: <该校特殊结构>"
```

完成报告 → `supervisor-claw/docs/reports/nju_report.md`：

```markdown
# 南京大学 adapter — completion report

- **branch**: `feat/adapter-nju`
- **commit**: <hash>
- **worktree**: `~/Projects/adapter-nju`

## test results
- compileall: PASS / FAIL
- pytest: <未跑 / N passed>

## fixture coverage
| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| cs | ... | ... | ... | ... |
| ai | ... | ... | ... | ... |
| sw | ... | ... | ... | ... |

## 特殊结构观察
1. ...
2. ...

## schools.yaml 改动
<diff 或 "无">

## known limitations
<比如 "软件学院 SPA 渲染，v0.4 上 Playwright">
```

最后 commit 报告：

```bash
git add docs/reports/nju_report.md
git commit -m "docs(nju): completion report"
```

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py` / `core/` / `models/` / 其他学校的 adapter
- ❌ `uv sync` / 装新依赖
- ❌ 真打学校的网（fixture 测试就够，不要 `uv run claw crawl nju`）
- ❌ commit `data/` / `.env` / API key
- ❌ `git push` 到 origin

## 完成的标志

- 至少 2 个 commit（adapter + report）
- `docs/reports/nju_report.md` 存在且填好
- 报告里说出 1-3 个特殊结构观察

开始吧。质量 > 速度。不确定的字段留空。
