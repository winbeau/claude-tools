# supervisor-claw v0.2 — 浙江大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.2 并行 rollout 的一份；同时还有另外 5 个 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**。

## 你的标识

- **学校 code**: `zju`
- **学校名**: 浙江大学
- **要支持的学院**: `cs` (计算机科学与技术学院), `cadcg` (CAD&CG 国家重点实验室), `ai-inst` (人工智能研究所), `sw` (软件学院)
- **分支**: `feat/adapter-zju`
- **worktree 路径**: `~/Projects/adapter-zju`

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools
git fetch origin
git worktree add ~/Projects/adapter-zju -b feat/adapter-zju origin/main
cd ~/Projects/adapter-zju
git status
```

## Step 1 — 必读（按顺序）

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务说明 + **11 个清华踩过的坑**（必看）
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现
3. **`supervisor-claw/src/claw/adapters/base.py`** ← SchoolAdapter ABC
4. **`supervisor-claw/src/claw/models/pydantic_models.py`** ← AdvisorPartial 字段
5. **`supervisor-claw/schools.yaml`** ← 你的 `zju` 段 list_urls

## Step 2 — 调研页面结构

浙大特有的几个点要注意：

- **浙大有个统一 `person.zju.edu.cn` 系统**（v0.1 调研时本机 ECONNREFUSED；用户在 WSL 上应该能访问） —— `person.zju.edu.cn/cs` 和 `/ai` 是这套系统的 deep link。验证它是否服务端渲染（如果是 SPA，要标 known limitation）
- CAD&CG 国重 (`cad.zju.edu.cn`) 是独立的实验室站，结构可能跟主站差很多 —— 单独写 helper
- 软件学院 (`cst.zju.edu.cn`) 在宁波 / 杭州两地，师资可能分开列 —— 看清楚是不是一个 list 涵盖全部
- 浙大老师**重叠聘任**很常见：一个老师同时挂 CS + AI 所 + CAD&CG —— 你 parse_list 只管列出，主流程的 upsert 会按 `(school, name, email)` 去重，所以**重复列出没关系**

对每个 dept 用 WebFetch 验证 → 不可达就 WebSearch 找正确入口 → 修回 `schools.yaml` 的 `zju` 段。

```bash
cd ~/Projects/adapter-zju/supervisor-claw
mkdir -p tests/fixtures/zju
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/zju/list_<dept>.html
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/zju.py`

```python
@register
class ZjuAdapter(SchoolAdapter):
    school_code = "zju"
    supports = {"cs", "cadcg", "ai-inst", "sw"}

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

4 个学院页面结构差异大就拆 helper 函数（`_parse_person_list / _parse_cadcg_list / _parse_sw_list`），用 list_url 判断走哪一条。

## Step 4 — 写测试

文件：`supervisor-claw/tests/test_parsers/test_zju.py`

至少 3 个测试。如果 person.zju.edu.cn 是 SPA，fixture 抓回来的 HTML 会几乎是空壳 —— 这种情况 test 标 skip 并在报告里写 known limitations。

## Step 5 — 自查

```bash
python3 -m compileall -q src/claw/adapters/zju.py tests/test_parsers/test_zju.py
```

## Step 6 — 提交 + 写完成报告

```bash
git add src/claw/adapters/zju.py \
        tests/test_parsers/test_zju.py \
        tests/fixtures/zju/
# 如改了 schools.yaml:
git add schools.yaml

git commit -m "feat(zju): 浙江大学 adapter v1

- depts: cs, cadcg, ai-inst, sw
- fixtures: N list + M profile
- handles: <该校特殊结构，例如 person.zju.edu.cn 统一系统 / 多学院聘任重复>"
```

完成报告 → `supervisor-claw/docs/reports/zju_report.md`：

```markdown
# 浙江大学 adapter — completion report

- **branch**: `feat/adapter-zju`
- **commit**: <hash>
- **worktree**: `~/Projects/adapter-zju`

## test results
- compileall: PASS / FAIL
- pytest: <未跑 / N passed>

## fixture coverage
| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| cs | ... | ... | ... | ... |
| cadcg | ... | ... | ... | ... |
| ai-inst | ... | ... | ... | ... |
| sw | ... | ... | ... | ... |

## 特殊结构观察
1. ...
2. ...

## schools.yaml 改动
<diff 或 "无">

## known limitations
<例如 "person.zju.edu.cn 是 SPA，httpx 拉不到，v0.4 上 Playwright">
```

最后 commit 报告：

```bash
git add docs/reports/zju_report.md
git commit -m "docs(zju): completion report"
```

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py` / `core/` / `models/` / 其他学校的 adapter
- ❌ `uv sync` / 装新依赖
- ❌ 真打学校的网（fixture 测试就够）
- ❌ commit `data/` / `.env` / API key
- ❌ `git push` 到 origin

## 完成的标志

- 至少 2 个 commit（adapter + report）
- `docs/reports/zju_report.md` 存在且填好
- 报告里说出 1-3 个特殊结构观察

开始吧。质量 > 速度。
