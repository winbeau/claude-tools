# supervisor-claw v0.4 — 东南大学 adapter agent

## 你的标识

- **学校 code**: `seu`
- **学校名**: 东南大学
- **要支持的学院**: `cs` (计算机科学与工程学院), `sw` (软件学院), `ai` (人工智能学院 / 苏州校区)
- **分支**: `feat/adapter-seu`
- **worktree 路径**: `~/Projects/adapter-seu`

> 东南大学 CS/AI 强校，AI 学院位于苏州校区，可能有独立子站。计算机学院网站近年用 Vue/React 重做过部分页面，注意 SPA 兜底。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-seu -b feat/adapter-seu origin/main
cd ~/Projects/adapter-seu
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `seu` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `cse.seu.edu.cn`
- 软件学院: `cose.seu.edu.cn`
- AI 学院（苏州校区）: `ai.seu.edu.cn` 或 `aii.seu.edu.cn`

**重点排查 SPA**：用 `curl` 抓页面后看 body 是否近乎空（只有 `<div id="app">`），若是则该 dept 写到 known limitations 等 v0.4 上 Playwright。

新增 schools.yaml：

```yaml
- code: seu
  name_cn: 东南大学
  departments:
    - { code: cs, name_cn: 计算机科学与工程学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能学院, list_urls: [<URL>] }
```

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/seu.py`，按 URL 路由 dept。SPA dept 在 parse_list 里返回空列表 + warning log，**不要 crash**。

## Step 4 — 测试

`tests/test_parsers/test_seu.py`：

1. `test_parse_list_per_dept`：每非 SPA dept ≥ 10
2. `test_spa_dept_graceful_empty`：SPA dept 返回 [] 不 crash
3. `test_parse_profile_research_or_bio`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/seu.py tests/test_parsers/test_seu.py
git add src/claw/adapters/seu.py tests/ schools.yaml
git commit -m "feat(seu): 东南大学 adapter v1

- depts: cs, sw, ai
- fixtures: ...
- handles: SPA dept 优雅降级"
```

报告：`docs/reports/seu_report.md`。

## 该校特殊点

- **SPA dept**：可能某个学院前端是 Vue/React，httpx 拿不到数据
- **多校区**：AI 学院在苏州，与南京本部独立
- **CS 名字叫"计算机科学与工程学院"**：注意完整名

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 seu 段；fixtures 每非-SPA dept 至少 1 个。
