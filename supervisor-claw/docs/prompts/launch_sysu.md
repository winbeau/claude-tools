# supervisor-claw v0.4 — 中山大学 adapter agent

## 你的标识

- **学校 code**: `sysu`
- **学校名**: 中山大学
- **要支持的学院**: `cs` (计算机学院 — 广州校区), `sw` (软件工程学院 — 珠海校区), `ai` (人工智能学院 — 珠海校区)
- **分支**: `feat/adapter-sysu`
- **worktree 路径**: `~/Projects/adapter-sysu`

> 中山大学 CS / SW / AI 分布在不同校区，子域可能不同：`sdcs.sysu.edu.cn`（计算机）、`sse.sysu.edu.cn`（软件）、`sai.sysu.edu.cn`（AI）。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-sysu -b feat/adapter-sysu origin/main
cd ~/Projects/adapter-sysu
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `sysu` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `sdcs.sysu.edu.cn` → `/szdw/jsxx.htm` 或类似
- 软件工程学院: `sse.sysu.edu.cn`（珠海）
- 人工智能学院: `sai.sysu.edu.cn`（珠海）

新增 schools.yaml：

```yaml
- code: sysu
  name_cn: 中山大学
  departments:
    - { code: cs, name_cn: 计算机学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件工程学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能学院, list_urls: [<URL>] }
```

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/sysu.py`，按 URL 路由 dept。

## Step 4 — 测试

`tests/test_parsers/test_sysu.py`：

1. `test_parse_list_per_dept`：每 dept ≥ 15
2. `test_parse_profile_no_nav`
3. `test_research_or_bio_present`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/sysu.py tests/test_parsers/test_sysu.py
git add src/claw/adapters/sysu.py tests/ schools.yaml
git commit -m "feat(sysu): 中山大学 adapter v1

- depts: cs, sw, ai (跨广州/珠海校区)
- fixtures: ...
- handles: <特殊点>"
```

报告：`docs/reports/sysu_report.md`。

## 该校特殊点

- **多校区多子域**：sdcs / sse / sai 完全独立，注意每个都要单独验证
- **三个学院规模都不小**：每个 ≥ 30 人
- **博士后 / 副研究员 混入**：list 可能包含非 PI 的研究人员，按 title 过滤

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 sysu 段；fixtures 每 dept 至少 1 个。
