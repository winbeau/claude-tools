# supervisor-claw v0.4 — 北京理工大学 adapter agent

## 你的标识

- **学校 code**: `bit`
- **学校名**: 北京理工大学
- **要支持的学院**: `cs` (计算机学院), `sw` (软件学院), `cse` (网络空间安全学院)
- **分支**: `feat/adapter-bit`
- **worktree 路径**: `~/Projects/adapter-bit`

> 北理工是国防七子之一，部分二级页面可能有访问限制。北理工的师资页常常按 "系所 / 团队 / 研究方向" 分类，**一个老师可能在多个分类下出现**，注意去重（按 profile_url 或 name+title）。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-bit -b feat/adapter-bit origin/main
cd ~/Projects/adapter-bit
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `bit` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `cs.bit.edu.cn` → `/szdw/` 或 `/jsdw/`
- 软件学院: `sse.bit.edu.cn`
- 网安学院: `cse.bit.edu.cn` 或 `nis.bit.edu.cn`

确认入口可达；新增 schools.yaml：

```yaml
- code: bit
  name_cn: 北京理工大学
  departments:
    - { code: cs, name_cn: 计算机学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
    - { code: cse, name_cn: 网络空间安全学院, list_urls: [<URL>] }
```

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/bit.py`，按 URL 路由 dept。注意 list 可能因为 "按方向分类" 出现重复条目，在 parse_list 末尾加一次 `dict.fromkeys(items, key=lambda i: i.profile_url or i.name_cn)` 风格的去重。

## Step 4 — 测试

`tests/test_parsers/test_bit.py`：

1. `test_parse_list_per_dept`：每 dept ≥ 15
2. `test_list_dedup`：相同 profile_url 不重复
3. `test_parse_profile_no_nav`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/bit.py tests/test_parsers/test_bit.py
git add src/claw/adapters/bit.py tests/ schools.yaml
git commit -m "feat(bit): 北京理工大学 adapter v1

- depts: cs, sw, cse
- fixtures: ...
- handles: 多分类去重 / <其他特殊点>"
```

报告：`docs/reports/bit_report.md`。

## 该校特殊点

- **多分类导致重复**：同一老师在 "教授" 和 "AI 团队" 两个分类下都列，要去重
- **国防访问限制**：部分页面可能 403
- **首页 → 师资 → 二级团队** 的层级，要找到 PI 的统一列表，不是按团队穷举

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 bit 段；fixtures 每 dept 至少 1 个。
