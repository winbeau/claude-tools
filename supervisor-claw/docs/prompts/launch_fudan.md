# supervisor-claw v0.2 — 复旦大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.2 并行 rollout 的一份；同时还有另外 5 个 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**。

## 你的标识

- **学校 code**: `fudan`
- **学校名**: 复旦大学
- **要支持的学院**: `cs` (计算机科学技术学院), `bd` (大数据学院), `ai` (AI 创新与产业研究院)
- **分支**: `feat/adapter-fudan`
- **worktree 路径**: `~/Projects/adapter-fudan`

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools
git fetch origin
git worktree add ~/Projects/adapter-fudan -b feat/adapter-fudan origin/main
cd ~/Projects/adapter-fudan
git status
```

## Step 1 — 必读（按顺序）

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务说明 + **11 个清华踩过的坑**（必看）
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现
3. **`supervisor-claw/src/claw/adapters/base.py`** ← SchoolAdapter ABC
4. **`supervisor-claw/src/claw/models/pydantic_models.py`** ← AdvisorPartial 字段
5. **`supervisor-claw/schools.yaml`** ← 你的 `fudan` 段 list_urls

## Step 2 — 调研页面结构

复旦特有的几个点要注意：

- 复旦 CS（`cs.fudan.edu.cn/szdw/szdwjs.htm`）经常按职称分类多页 + 多个 `szdwjs_NNN.htm` —— 全部列进 schools.yaml `list_urls`
- 大数据学院 (`sds.fudan.edu.cn`) 是新院，师资页可能用现代化的 SPA 框架（Vue / React），httpx 拉不到 → 标 known limitations，v0.4 用 Playwright
- AI 创新与产业研究院 (`aiii.fudan.edu.cn`) 师资名册可能就在首页 → 验证一下，可能没有独立的 `/people/` 页
- 复旦有些教师是 **CS + AI 院双聘**（如邱锡鹏），主流程会按 `(school, name, email)` upsert 去重，**重复列出没关系**

对每个 dept 用 WebFetch 验证 → 不可达就 WebSearch 找正确入口 → 修回 `schools.yaml` 的 `fudan` 段。

```bash
cd ~/Projects/adapter-fudan/supervisor-claw
mkdir -p tests/fixtures/fudan
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/fudan/list_<dept>.html
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/fudan.py`

```python
@register
class FudanAdapter(SchoolAdapter):
    school_code = "fudan"
    supports = {"cs", "bd", "ai"}

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

3 个学院页面差异大就拆 helper 函数，按 list_url 路由。

## Step 4 — 写测试

文件：`supervisor-claw/tests/test_parsers/test_fudan.py`

至少 3 个测试：list 解析数 + 邮箱率 + 无 JS/nav 泄漏。SPA 学院（如 sds）抓不到内容就 test 标 skip + 报告里写 known limitations。

## Step 5 — 自查

```bash
python3 -m compileall -q src/claw/adapters/fudan.py tests/test_parsers/test_fudan.py
```

## Step 6 — 提交 + 写完成报告

```bash
git add src/claw/adapters/fudan.py \
        tests/test_parsers/test_fudan.py \
        tests/fixtures/fudan/
# 如改了 schools.yaml:
git add schools.yaml

git commit -m "feat(fudan): 复旦大学 adapter v1

- depts: cs, bd, ai
- fixtures: N list + M profile
- handles: <该校特殊结构，比如 CS 多页分类 / 双聘老师 / SPA 学院>"
```

完成报告 → `supervisor-claw/docs/reports/fudan_report.md`：

```markdown
# 复旦大学 adapter — completion report

- **branch**: `feat/adapter-fudan`
- **commit**: <hash>
- **worktree**: `~/Projects/adapter-fudan`

## test results
- compileall: PASS / FAIL
- pytest: <未跑 / N passed>

## fixture coverage
| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| cs | ... | ... | ... | ... |
| bd | ... | ... | ... | ... |
| ai | ... | ... | ... | ... |

## 特殊结构观察
1. ...
2. ...

## schools.yaml 改动
<diff 或 "无">

## known limitations
<例如 "大数据学院 SPA 渲染，需要 v0.4 Playwright">
```

最后 commit 报告：

```bash
git add docs/reports/fudan_report.md
git commit -m "docs(fudan): completion report"
```

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py` / `core/` / `models/` / 其他学校的 adapter
- ❌ `uv sync` / 装新依赖
- ❌ 真打学校的网（fixture 测试就够）
- ❌ commit `data/` / `.env` / API key
- ❌ `git push` 到 origin

## 完成的标志

- 至少 2 个 commit（adapter + report）
- `docs/reports/fudan_report.md` 存在且填好
- 报告里说出 1-3 个特殊结构观察

开始吧。质量 > 速度。
