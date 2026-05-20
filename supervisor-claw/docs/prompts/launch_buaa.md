# supervisor-claw v0.4 — 北京航空航天大学 adapter agent

## 你的标识

- **学校 code**: `buaa`
- **学校名**: 北京航空航天大学
- **要支持的学院**: `cs` (计算机学院), `sw` (软件学院), `ai` (人工智能研究院 / 自动化学院 AI 方向)
- **分支**: `feat/adapter-buaa`
- **worktree 路径**: `~/Projects/adapter-buaa`

> 北航是国防七子之一，CS/AI 实力强。3 个相关学院都有独立官网。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-buaa -b feat/adapter-buaa origin/main
cd ~/Projects/adapter-buaa
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `buaa` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `scse.buaa.edu.cn` 或 `cs.buaa.edu.cn`
- 软件学院: `soft.buaa.edu.cn` 或 `sse.buaa.edu.cn`
- 人工智能研究院 / AI 学院: `iai.buaa.edu.cn`

新增 schools.yaml：

```yaml
- code: buaa
  name_cn: 北京航空航天大学
  departments:
    - { code: cs, name_cn: 计算机学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能研究院, list_urls: [<URL>] }
```

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/buaa.py`，按 URL 路由 dept。

## Step 4 — 测试

`tests/test_parsers/test_buaa.py`：

1. `test_parse_list_per_dept`：每 dept ≥ 15
2. `test_parse_profile_no_nav`
3. `test_research_or_bio_present`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/buaa.py tests/test_parsers/test_buaa.py
git add src/claw/adapters/buaa.py tests/ schools.yaml
git commit -m "feat(buaa): 北京航空航天大学 adapter v1

- depts: cs, sw, ai
- fixtures: ...
- handles: <特殊点>"
```

报告：`docs/reports/buaa_report.md`。

## 该校特殊点

- **改版频繁**：近 2 年师资页 URL 多次变动，要重新验证
- **AI 研究院 vs 自动化学院**：AI 方向同时挂在两个单位，注意选规模更大、入口更稳定的那个
- **国防访问限制**：部分页面可能 403，记录 known limitations

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 buaa 段；fixtures 每 dept 至少 1 个。
