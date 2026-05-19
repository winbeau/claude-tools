# supervisor-claw v0.2 — 上海交通大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.2 并行 rollout 的一份；同时还有另外 5 个 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**。

## 你的标识

- **学校 code**: `sjtu`
- **学校名**: 上海交通大学
- **要支持的学院**: `cs` (计算机科学与工程系), `see-ai` (电院 AI 方向), `ai` (人工智能学院), `qingyuan` (清源研究院)
- **分支**: `feat/adapter-sjtu`
- **worktree 路径**: `~/Projects/adapter-sjtu`

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools
git fetch origin
git worktree add ~/Projects/adapter-sjtu -b feat/adapter-sjtu origin/main
cd ~/Projects/adapter-sjtu
git status
```

## Step 1 — 必读（按顺序）

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务说明 + **11 个清华踩过的坑**（必看）
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现
3. **`supervisor-claw/src/claw/adapters/base.py`** ← SchoolAdapter ABC
4. **`supervisor-claw/src/claw/models/pydantic_models.py`** ← AdvisorPartial 字段
5. **`supervisor-claw/schools.yaml`** ← 你的 `sjtu` 段 list_urls

## Step 2 — 调研页面结构

上交特有的几个点要注意：

- 上交 CS（`cs.sjtu.edu.cn/PeopleList.aspx`）是 **ASP.NET WebForms**（`.aspx`），有 `__VIEWSTATE` POST 参数 —— 但**师资名册一般是 GET 直接渲染**，httpx 取 HTML 应该够；如果要分页可能需要带 cookie / POST，遇到再说
- 电院 `english.seiee.sjtu.edu.cn` 是英文站，可能要找中文版（一般域名 `seiee.sjtu.edu.cn`）。**只爬 AI / 计算机方向的老师**（按研究方向关键词过滤）
- AI 学院 (`soai.sjtu.edu.cn/szdw`) 是 2020 新建院，师资页可能用现代框架（Vue/React）—— 看是否 SPA
- 清源研究院 (`qingyuan.sjtu.edu.cn`) 可能没有独立的 `/people/`，研究员名单可能在首页或某个 "成员" 子页
- 上交有部分老师**同时挂 CS + 电院 / AI 院 + 清源** —— upsert 去重保护，重复列出没关系

对每个 dept 用 WebFetch 验证 → 不可达就 WebSearch 找正确入口 → 修回 `schools.yaml` 的 `sjtu` 段。

```bash
cd ~/Projects/adapter-sjtu/supervisor-claw
mkdir -p tests/fixtures/sjtu
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/sjtu/list_<dept>.html
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/sjtu.py`

```python
@register
class SjtuAdapter(SchoolAdapter):
    school_code = "sjtu"
    supports = {"cs", "see-ai", "ai", "qingyuan"}

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

4 个学院页面结构差异大就拆 helper 函数（`_parse_cs_aspx_list / _parse_seiee_list / _parse_soai_list / _parse_qingyuan_list`），按 list_url 路由。

## Step 4 — 写测试

文件：`supervisor-claw/tests/test_parsers/test_sjtu.py`

至少 3 个测试：list 解析数 + 邮箱率 + 无 JS/nav 泄漏。SPA 学院抓不到内容就 test 标 skip + 报告里写 known limitations。电院 AI 过滤要有专门 test 确认非 AI 老师被剔除。

## Step 5 — 自查

```bash
python3 -m compileall -q src/claw/adapters/sjtu.py tests/test_parsers/test_sjtu.py
```

## Step 6 — 提交 + 写完成报告

```bash
git add src/claw/adapters/sjtu.py \
        tests/test_parsers/test_sjtu.py \
        tests/fixtures/sjtu/
# 如改了 schools.yaml:
git add schools.yaml

git commit -m "feat(sjtu): 上海交通大学 adapter v1

- depts: cs, see-ai, ai, qingyuan
- fixtures: N list + M profile
- handles: <该校特殊结构，比如 ASP.NET / 电院 AI 过滤 / SPA 学院>"
```

完成报告 → `supervisor-claw/docs/reports/sjtu_report.md`：

```markdown
# 上海交通大学 adapter — completion report

- **branch**: `feat/adapter-sjtu`
- **commit**: <hash>
- **worktree**: `~/Projects/adapter-sjtu`

## test results
- compileall: PASS / FAIL
- pytest: <未跑 / N passed>

## fixture coverage
| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| cs | ... | ... | ... | ... |
| see-ai | ... | ... | ... | ... |
| ai | ... | ... | ... | ... |
| qingyuan | ... | ... | ... | ... |

## 特殊结构观察
1. ...
2. ...

## schools.yaml 改动
<diff 或 "无">

## known limitations
<例如 "soai SPA，v0.4 Playwright; see-ai 方向过滤是 keyword-based, 会漏掉跨方向老师">
```

最后 commit 报告：

```bash
git add docs/reports/sjtu_report.md
git commit -m "docs(sjtu): completion report"
```

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py` / `core/` / `models/` / 其他学校的 adapter
- ❌ `uv sync` / 装新依赖
- ❌ 真打学校的网（fixture 测试就够）
- ❌ commit `data/` / `.env` / API key
- ❌ `git push` 到 origin

## 完成的标志

- 至少 2 个 commit（adapter + report）
- `docs/reports/sjtu_report.md` 存在且填好
- 报告里说出 1-3 个特殊结构观察

开始吧。质量 > 速度。
