# supervisor-claw v0.4 — 西安交通大学 adapter agent

## 你的标识

- **学校 code**: `xjtu`
- **学校名**: 西安交通大学
- **要支持的学院**: `cs` (电子与信息学部 / 计算机科学与技术学院), `sw` (软件学院), `ai` (人工智能学院)
- **分支**: `feat/adapter-xjtu`
- **worktree 路径**: `~/Projects/adapter-xjtu`

> 西交在 2018-2020 年间把电信、电气、自动化、计算机等整合为「电子与信息学部」（学部制），但师资页可能仍各自挂在原学院的子域。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-xjtu -b feat/adapter-xjtu origin/main
cd ~/Projects/adapter-xjtu
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku,sjtu}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `xjtu` 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `cs.xjtu.edu.cn` 或 `gr.xjtu.edu.cn`（"学者主页"系统统一入口）
- 软件学院: `se.xjtu.edu.cn`
- AI 学院: `aii.xjtu.edu.cn`（人工智能与机器人研究所）/ `ai.xjtu.edu.cn`

**重点**：西交的"学者主页"系统 `gr.xjtu.edu.cn/web/<advisor-id>` 是全校统一的个人页框架，可能比学院的师资页更全。优先用学院师资页拿名单 → 拿到 gr.xjtu profile URL → parse_profile 走统一格式。

新增 schools.yaml：

```yaml
- code: xjtu
  name_cn: 西安交通大学
  departments:
    - { code: cs, name_cn: 计算机科学与技术学院, list_urls: [<URL>] }
    - { code: sw, name_cn: 软件学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能学院, list_urls: [<URL>] }
```

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/xjtu.py`：

```python
@register
class XjtuAdapter(SchoolAdapter):
    school_code = "xjtu"
    supports = {"cs", "sw", "ai"}
    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item):
        # gr.xjtu.edu.cn 统一框架 vs 学院自建页 — 用 url 判断
        ...
```

## Step 4 — 测试

`tests/test_parsers/test_xjtu.py`：

1. `test_parse_list_per_dept`：每 dept ≥ 15，姓名 100%
2. `test_parse_profile_gr_xjtu`：若有 gr.xjtu 链接，parse_profile 能拿到 bio + research
3. `test_research_or_bio_present`

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/xjtu.py tests/test_parsers/test_xjtu.py
git add src/claw/adapters/xjtu.py tests/ schools.yaml
git commit -m "feat(xjtu): 西安交通大学 adapter v1

- depts: cs, sw, ai
- fixtures: list + gr.xjtu 统一 profile
- handles: <特殊点>"
```

报告：`docs/reports/xjtu_report.md`。

## 该校特殊点

- **gr.xjtu.edu.cn 统一学者主页**：全校 PI 共用框架，parse_profile 可以共用一段代码
- **学部制下的混乱**：电信学部内多个二级单位都自称"信息相关"，注意只入 CS/AI/SW 这 3 个
- **学位制度特殊**：研究生分"学硕 / 专硕 / 工程硕士"，注意 list 里别把研究生当老师

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 xjtu 段；fixtures 每 dept 至少 1 个 + 1 个 gr.xjtu profile。
