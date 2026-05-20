# supervisor-claw v0.4 — 天津大学 adapter agent

## 你的标识

- **学校 code**: `tju`
- **学校名**: 天津大学
- **要支持的学院**: `ic` (智能与计算学部 — 统合 CS + AI + Software + CSE)
- **分支**: `feat/adapter-tju`
- **worktree 路径**: `~/Projects/adapter-tju`

> 天大 2018 年成立"智能与计算学部"（College of Intelligence and Computing, CIC），把计算机科学与技术、软件、人工智能、网络空间安全、数学（应用方向）等全部归口管理。**只有 1 个 dept code**。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-tju -b feat/adapter-tju origin/main
cd ~/Projects/adapter-tju
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `tju` 段）

## Step 2 — 调研

入口候选：

- `cic.tju.edu.cn`（智能与计算学部主页）→ "师资队伍" 或 "教师" 入口
- 内部可能再分系（计算机系 / 软件系 / 智能系 / 网安系），但都在 cic 子站下

学部下可能按"系"细分 list_urls，可以在 `tju` dept `ic` 下挂多个 URL：

```yaml
- code: tju
  name_cn: 天津大学
  departments:
    - code: ic
      name_cn: 智能与计算学部
      list_urls:
        - <cs 系师资 URL>
        - <ai 系师资 URL>
        - <sw 系师资 URL>
        - <cse 系师资 URL>
```

抓 fixture（每个子系一个 list）。

## Step 3 — 实现

`src/claw/adapters/tju.py`：

```python
@register
class TjuAdapter(SchoolAdapter):
    school_code = "tju"
    supports = {"ic"}
    def parse_list(self, html, list_url): ...   # 不同子系 URL 可能结构略不同
    def parse_profile(self, html, profile_url, list_item): ...
```

可以在 parse_list 里用 url 关键字（`/computer/` vs `/ai/` vs `/software/`）判断子系，在 `appointment.role` 字段（v0.4 之后再用）写入子系信息。本期先把 `dept_code='ic'` 固定。

## Step 4 — 测试

`tests/test_parsers/test_tju.py`：

1. `test_parse_list_basic`：合并 4 个子系列表 ≥ 50 人
2. `test_dedup_across_subsystems`：同一老师不重复
3. `test_parse_profile_research_or_bio`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/tju.py tests/test_parsers/test_tju.py
git add src/claw/adapters/tju.py tests/ schools.yaml
git commit -m "feat(tju): 天津大学 adapter v1

- depts: ic (intelligence & computing 学部统合)
- fixtures: 4 子系 list + 2 profile
- handles: 学部下多子系列表合并去重"
```

报告：`docs/reports/tju_report.md`。

## 该校特殊点

- **单学部多子系**：智能与计算学部是大单位，下面 4 个系都要爬，统一归到 `ic`
- **跨系兼聘**：合并子系列表后去重很重要
- **学部站 vs 老学院站**：可能 `cs.tju.edu.cn`（老）还在但更新慢，优先用 cic.tju.edu.cn

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 tju 段；fixtures 至少 2 个子系 list + 1 个 profile。
