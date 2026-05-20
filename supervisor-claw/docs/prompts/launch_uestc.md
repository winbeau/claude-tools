# supervisor-claw v0.4 — 电子科技大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.4 第二批 rollout（共 13 所新学校）；同时还有其他 agent 在为别的学校做同样的事，**通过 git worktree 互相隔离**，你不会跟他们冲突。

## 你的标识

- **学校 code**: `uestc`
- **学校名**: 电子科技大学
- **要支持的学院**: `cs` (计算机科学与工程学院), `sw` (信息与软件工程学院), `ai` (自动化工程 / 智能学院；如有独立 AI 学院)
- **分支**: `feat/adapter-uestc`
- **worktree 路径**: `~/Projects/adapter-uestc`

## Step 0 — 准备 worktree

```bash
cd ~/Projects/claude-tools
git fetch origin
git worktree add ~/Projects/adapter-uestc -b feat/adapter-uestc origin/main
cd ~/Projects/adapter-uestc
```

确认就位（`pwd` / `git status` / `ls supervisor-claw/`）。

## Step 1 — 必读

1. **`supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`** ← 完整任务规范 + 11 个清华踩过的坑
2. **`supervisor-claw/src/claw/adapters/tsinghua.py`** ← v0.1 已工作的参考实现
3. **`supervisor-claw/src/claw/adapters/pku.py`** ← v0.2 多学院（4 dept）参考
4. **`supervisor-claw/src/claw/adapters/sjtu.py`** ← POST + ListUrlSpec 参考（如果你也撞到 AJAX 列表）
5. **`supervisor-claw/src/claw/adapters/base.py`** + **`src/claw/models/pydantic_models.py`** ← 接口
6. **`supervisor-claw/schools.yaml`** ← **这所学校目前不在 yaml 里，你需要新增一段 `uestc` school**（参照 pku 段写法）

## Step 2 — 调研 + 新增 schools.yaml 段

电子科大主站 `www.uestc.edu.cn`；CS/AI 相关学院候选入口：

- 计算机科学与工程学院: `www.scse.uestc.edu.cn` → 找 `/szdw/` 或 `/jsdw/` 师资页
- 信息与软件工程学院: `www.sse.uestc.edu.cn`
- 人工智能 / 自动化（看是否有独立 AI 学院）

用 WebFetch + WebSearch 确认每个学院**当前可达**的师资列表 URL，然后**新增** `schools.yaml` 一段（结构参照已有的 pku/nju 段）：

```yaml
- code: uestc
  name_cn: 电子科技大学
  departments:
    - code: cs
      name_cn: 计算机科学与工程学院
      list_urls:
        - <URL>
    ...
```

若发现某 dept 是 AJAX-only（前端跑 JS 拉 JSON），用 `ListUrlSpec` 的 POST 形式（参照 `schools.yaml` 里的 `sjtu/cs` 段 + `core/http.py:Fetcher.post`）。

抓 fixture：

```bash
cd ~/Projects/adapter-uestc/supervisor-claw
mkdir -p tests/fixtures/uestc
curl -sSL -A "supervisor-claw/0.1 (research)" "<list_url>" \
  -o tests/fixtures/uestc/list_<dept>.html
# 再抓 1-2 个 profile 页
```

## Step 3 — 实现 adapter

文件：`supervisor-claw/src/claw/adapters/uestc.py`

```python
@register
class UestcAdapter(SchoolAdapter):
    school_code = "uestc"
    supports = {"cs", "sw", "ai"}  # 按你实际确认的 dept 数

    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

若各 dept 页面差异大，按 `pku.py` 风格用 `_dept_from_url(list_url)` 路由到子函数。

## Step 4 — 测试

`supervisor-claw/tests/test_parsers/test_uestc.py`，至少：

1. `test_parse_list_basic`：每个 dept fixture 解析数 ≥ 10，姓名 100%，邮箱率 ≥ 50%
2. `test_parse_profile_no_nav`：bio 不含 `function` / 校园导航词
3. `test_parse_profile_has_research_or_bio`：bio 或 research_interests 至少一项非空

## Step 5 — 自查

```bash
python3 -m compileall -q src/claw/adapters/uestc.py tests/test_parsers/test_uestc.py
```

## Step 6 — commit + 报告

```bash
git add src/claw/adapters/uestc.py \
        tests/test_parsers/test_uestc.py \
        tests/fixtures/uestc/ \
        schools.yaml
git commit -m "feat(uestc): 电子科技大学 adapter v1

- depts: cs, sw, ai
- fixtures: ...
- handles: <列出特殊结构>"
```

落盘报告：`supervisor-claw/docs/reports/uestc_report.md`，结构同 `pku_report.md`（分支 / commit / 测试结果 / fixture 覆盖率 / 1-3 条特殊结构观察 / schools.yaml diff / known limitations）。

## 该校特殊点（先验，agent 自行验证）

- 电子科大网站近年改版较多，注意 URL 失效；学院多用三级子域名 `www.<dept>.uestc.edu.cn`
- 部分学院可能用 ASP.NET / JSP 老站，注意编码（UTF-8 vs GBK）
- 老师页常有"团队成员"二级结构，**只算 PI（教授/副教授/研究员）级别**，不要把博士后 / 学生当老师入库

## 严禁

- ❌ 改 `src/claw/adapters/__init__.py`、其他学校 adapter、`core/` / `models/` / `pipeline/` / `cli/`
- ❌ `uv sync` / 装新依赖
- ❌ `uv run claw crawl uestc` 大规模真打学校的网（fixture 测试就够）
- ❌ commit `data/` / `.env`
- ❌ `git push origin`（用户手动 merge）

## 完成的标志

worktree 里 `git log --oneline` ≥ 2 个 commit；`docs/reports/uestc_report.md` 写满；schools.yaml 里有 `uestc` 段；fixtures 至少 1 个 list + 1 个 profile 真页面。

开始吧。质量 > 速度。
