# supervisor-claw v0.4 — 华中科技大学 adapter agent

## 你的标识

- **学校 code**: `hust`
- **学校名**: 华中科技大学
- **要支持的学院**: `cs` (计算机科学与技术学院), `sw` (软件学院), `ai` (人工智能与自动化学院 AI 方向)
- **分支**: `feat/adapter-hust`
- **worktree 路径**: `~/Projects/adapter-hust`

> 华科 CS 是国内顶尖之一，规模大，单 cs 院可能 100+ PI。`aia.hust.edu.cn` 是人工智能与自动化学院，AI 方向 PI 需要从中筛出。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-hust -b feat/adapter-hust origin/main
cd ~/Projects/adapter-hust
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `hust` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `cs.hust.edu.cn` → `/szdw/`（"师资队伍"）
- 软件学院: `sse.hust.edu.cn` 或 `software.hust.edu.cn`
- 人工智能与自动化学院: `aia.hust.edu.cn` → AI 方向 PI

新增 schools.yaml：

```yaml
- code: hust
  name_cn: 华中科技大学
  departments:
    - { code: cs, name_cn: 计算机科学与技术学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能与自动化学院, list_urls: [<URL>] }
```

**aia 学院**：注意要筛 AI 方向的 PI，**自动化 / 控制方向**的不算 CS/AI。可以在 parse_list 里按 title 或 research_interests 关键词筛（"人工智能 / 机器学习 / 计算机视觉 / NLP / 智能系统" 等）。但若太复杂，本期先全收，**known limitations 注明**让 v0.3 enricher 进一步过滤。

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/hust.py`，按 URL 路由 dept。

## Step 4 — 测试

`tests/test_parsers/test_hust.py`：

1. `test_parse_list_per_dept`：cs ≥ 80（华科大院），sw ≥ 20，ai ≥ 20
2. `test_parse_profile_no_nav`
3. `test_research_or_bio_present`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/hust.py tests/test_parsers/test_hust.py
git add src/claw/adapters/hust.py tests/ schools.yaml
git commit -m "feat(hust): 华中科技大学 adapter v1

- depts: cs, sw, ai (aia)
- fixtures: ...
- handles: aia 学院 AI 方向筛选 / <其他特殊点>"
```

报告：`docs/reports/hust_report.md`。

## 该校特殊点

- **CS 大院**：100+ PI，注意分页和去重
- **AIA 学院的 AI 子集**：自动化 vs AI 同院，本期可以全收，known limitations 注明
- **改版频繁**：cs.hust.edu.cn 近年改过几次

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 hust 段；fixtures 每 dept 至少 1 个；known limitations 写清 aia 筛选问题。
