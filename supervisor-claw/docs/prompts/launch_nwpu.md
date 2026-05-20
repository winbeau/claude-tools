# supervisor-claw v0.4 — 西北工业大学 adapter agent

## 你的标识

- **学校 code**: `nwpu`
- **学校名**: 西北工业大学
- **要支持的学院**: `cs` (计算机学院), `sw` (软件学院), `cse` (网络空间安全学院)
- **分支**: `feat/adapter-nwpu`
- **worktree 路径**: `~/Projects/adapter-nwpu`

> 西工大是国防特色高校（工信部直属"国防七子"之一）。**注意**：部分页面可能对境外 / 非校园网 IP 有访问限制；遇到 403 / 跳转登录，记录到 known limitations，不要试图绕过。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-nwpu -b feat/adapter-nwpu origin/main
cd ~/Projects/adapter-nwpu
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `nwpu` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `jsj.nwpu.edu.cn` / `computer.nwpu.edu.cn` → `/szdw/`
- 软件学院: `xinst.nwpu.edu.cn` 或并入计算机学院
- 网络空间安全学院: `cse.nwpu.edu.cn` 或 `bbs.nwpu.edu.cn` 改版后入口

先 WebFetch 确认可达。若某 dept 实际归并到计算机学院，**减少 supports 集合**，不要硬撑。

新增 schools.yaml：

```yaml
- code: nwpu
  name_cn: 西北工业大学
  departments:
    - { code: cs, name_cn: 计算机学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
    - { code: cse, name_cn: 网络空间安全学院, list_urls: [<URL>] }
```

抓 fixture 到 `tests/fixtures/nwpu/`。

## Step 3 — 实现

`src/claw/adapters/nwpu.py`，按 URL 路由 dept。注意西工大网站近年改版，部分二级页面用 vue/react SPA，遇到空 body 直接降级（在 known limitations 里写明该 dept 需要 v0.4 上 Playwright 兜底）。

## Step 4 — 测试

`tests/test_parsers/test_nwpu.py`：

1. `test_parse_list_per_dept`：每 dept 解析数 ≥ 8（西工大学院规模相对小），姓名 100%
2. `test_parse_profile_no_nav`
3. `test_research_or_bio_present`

## Step 5/6 — 自查 + commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/nwpu.py tests/test_parsers/test_nwpu.py
git add src/claw/adapters/nwpu.py tests/ schools.yaml
git commit -m "feat(nwpu): 西北工业大学 adapter v1

- depts: cs, sw, cse
- fixtures: ...
- handles: <特殊点>"
```

报告：`docs/reports/nwpu_report.md`。

## 该校特殊点

- **国防类访问限制**：部分页面可能 403，记录到 known limitations
- **SPA 兜底**：若某 dept 是 SPA，httpx 拿不到数据，标注后等 v0.4 Playwright
- **多个二级域名**：jsj/computer/xinst/cse 各不相同

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 nwpu 段；fixtures 每 dept 至少 1 个。
