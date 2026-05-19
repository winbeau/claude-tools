# supervisor-claw v0.2 — 中国科学技术大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.2 并行 rollout 的一份；同时还有另外 5 个 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**。

## 你的标识

- **学校 code**: `ustc`
- **学校名**: 中国科学技术大学
- **要支持的学院**: `cs` (计算机科学与技术学院), `ai-info` (信息学院 AI 方向)
- **分支**: `feat/adapter-ustc`
- **worktree 路径**: `~/Projects/adapter-ustc`

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools
git fetch origin
git worktree add ~/Projects/adapter-ustc -b feat/adapter-ustc origin/main
cd ~/Projects/adapter-ustc
git status
```

## Step 1 — 必读（按顺序）

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务说明 + **11 个清华踩过的坑**（必看）
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现
3. **`supervisor-claw/src/claw/adapters/base.py`** ← SchoolAdapter ABC
4. **`supervisor-claw/src/claw/models/pydantic_models.py`** ← AdvisorPartial 字段
5. **`supervisor-claw/schools.yaml`** ← 你的 `ustc` 段 list_urls

## Step 2 — 调研页面结构

中科大特有的几个点：

- 中科大 CS 学院（`cs.ustc.edu.cn/2013/1224/c2496a30007/page.htm`）的 URL 风格挺老旧，可能是 `aXXXa30007/page.htm` 这种 —— 验证一下是否还存活
- 信息学院（`eeis.ustc.edu.cn`）里只有部分老师方向是 AI —— 你需要在 parse_list 里**按研究方向关键词过滤**（"人工智能/机器学习/深度学习/CV/NLP/语音/模式识别" 等），或者把这个过滤交给 v0.3 LLM 做，这里就先全收
- 中科大老师常在主页用纯英文 / 中英混排，注意你的 section header 关键词要兼容 "Research Interests" / "研究兴趣" 等
- 部分老师挂个人主页在 staff.ustc.edu.cn —— `homepage` 字段用外链

对每个 dept 用 WebFetch 验证 → 不可达就 WebSearch 找正确入口 → 修回 `schools.yaml` 的 `ustc` 段。

```bash
cd ~/Projects/adapter-ustc/supervisor-claw
mkdir -p tests/fixtures/ustc
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/ustc/list_<dept>.html
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/ustc.py`

```python
@register
class UstcAdapter(SchoolAdapter):
    school_code = "ustc"
    supports = {"cs", "ai-info"}

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

只有 2 个学院，简单。如果 ai-info 的 AI 方向过滤逻辑复杂，可以拆个 `_filter_ai_advisor(item) -> bool` 助手。

## Step 4 — 写测试

文件：`supervisor-claw/tests/test_parsers/test_ustc.py`

至少 3 个测试：list 解析数 + 邮箱率 + 无 JS/nav 泄漏。若 ai-info 有方向过滤，再加一个 test 确认非 AI 老师被剔除。

## Step 5 — 自查

```bash
python3 -m compileall -q src/claw/adapters/ustc.py tests/test_parsers/test_ustc.py
```

## Step 6 — 提交 + 写完成报告

```bash
git add src/claw/adapters/ustc.py \
        tests/test_parsers/test_ustc.py \
        tests/fixtures/ustc/
# 如改了 schools.yaml:
git add schools.yaml

git commit -m "feat(ustc): 中国科学技术大学 adapter v1

- depts: cs, ai-info
- fixtures: N list + M profile
- handles: <该校特殊结构，比如 ai-info 方向关键词过滤>"
```

完成报告 → `supervisor-claw/docs/reports/ustc_report.md`：

```markdown
# 中国科学技术大学 adapter — completion report

- **branch**: `feat/adapter-ustc`
- **commit**: <hash>
- **worktree**: `~/Projects/adapter-ustc`

## test results
- compileall: PASS / FAIL
- pytest: <未跑 / N passed>

## fixture coverage
| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| cs | ... | ... | ... | ... |
| ai-info | ... | ... | ... | ... |

## 特殊结构观察
1. ...
2. ...

## schools.yaml 改动
<diff 或 "无">

## known limitations
<例如 "ai-info 的 AI 方向过滤是 keyword-based，会漏一些跨方向老师">
```

最后 commit 报告：

```bash
git add docs/reports/ustc_report.md
git commit -m "docs(ustc): completion report"
```

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py` / `core/` / `models/` / 其他学校的 adapter
- ❌ `uv sync` / 装新依赖
- ❌ 真打学校的网（fixture 测试就够）
- ❌ commit `data/` / `.env` / API key
- ❌ `git push` 到 origin

## 完成的标志

- 至少 2 个 commit（adapter + report）
- `docs/reports/ustc_report.md` 存在且填好
- 报告里说出 1-3 个特殊结构观察

开始吧。质量 > 速度。
