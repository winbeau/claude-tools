# supervisor-claw v0.4 — 南开大学 adapter agent

## 你的标识

- **学校 code**: `nankai`
- **学校名**: 南开大学
- **要支持的学院**: `cs` (计算机学院), `cse` (网络空间安全学院), `ai` (人工智能学院), `sw` (软件学院)
- **分支**: `feat/adapter-nankai`
- **worktree 路径**: `~/Projects/adapter-nankai`

> 南开 4 院并列（cs / cse / ai / sw），都在津南校区或八里台校区，独立官网。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-nankai -b feat/adapter-nankai origin/main
cd ~/Projects/adapter-nankai
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `nankai` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `cc.nankai.edu.cn` 或 `cs.nankai.edu.cn`
- 网络空间安全学院: `cyber.nankai.edu.cn`
- 人工智能学院: `ai.nankai.edu.cn`
- 软件学院: `cs.nankai.edu.cn/sw` 或独立子站

新增 schools.yaml：

```yaml
- code: nankai
  name_cn: 南开大学
  departments:
    - { code: cs, name_cn: 计算机学院, list_urls: [<URL>] }
    - { code: cse, name_cn: 网络空间安全学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
```

注意：若 sw 实际并入 cs，**减少 supports**，不要硬撑。

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/nankai.py`，按 URL 路由 dept。南开网站编码大概率 UTF-8，但部分老站可能 GBK，遇到先看响应头。

## Step 4 — 测试

`tests/test_parsers/test_nankai.py`：

1. `test_parse_list_per_dept`：每 dept ≥ 10
2. `test_parse_profile_no_nav`
3. `test_research_or_bio_present`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/nankai.py tests/test_parsers/test_nankai.py
git add src/claw/adapters/nankai.py tests/ schools.yaml
git commit -m "feat(nankai): 南开大学 adapter v1

- depts: cs, cse, ai, sw
- fixtures: ...
- handles: <特殊点>"
```

报告：`docs/reports/nankai_report.md`。

## 该校特殊点

- **4 院并列**：要分别确认 4 个域名都可达
- **是否独立 sw 学院**：若 sw 实际并入 cs，supports 砍掉 sw
- **PI 池小**：网安学院 / AI 学院都是 2020 年后才独立，PI 数可能只有 10-15 个

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 nankai 段；fixtures 每 dept 至少 1 个。
