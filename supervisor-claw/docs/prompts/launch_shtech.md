# supervisor-claw v0.4 — 上海科技大学 adapter agent

你是 supervisor-claw 项目的 adapter agent。这是 v0.4 第二批 rollout（共 13 所新学校）。

## 你的标识

- **学校 code**: `shtech`
- **学校名**: 上海科技大学
- **要支持的学院**: `sist` (信息科学与技术学院 — School of Information Science and Technology)
- **分支**: `feat/adapter-shtech`
- **worktree 路径**: `~/Projects/adapter-shtech`

> 上科大是 single-college 模型：CS / AI / 软件 / 通信全部归 SIST 一个学院管，所以只有 1 个 dept code。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-shtech -b feat/adapter-shtech origin/main
cd ~/Projects/adapter-shtech
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/tsinghua.py` / `pku.py` / `sjtu.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. **`supervisor-claw/schools.yaml`** — `shtech` 段不存在，你需要新增

## Step 2 — 调研

入口候选：

- `sist.shanghaitech.edu.cn/Faculty.htm`（中文）
- `sist.shanghaitech.edu.cn/sist_en/Faculty.htm`（英文，**通常更新更勤**）

**核对一下**：

- 是否有 PI / Adjunct / Visiting 等多个层级 → 只入 PI（"Professor" / "Associate Professor" / "Assistant Professor"）
- 英文版若更全，可同时挂在 `list_urls` 下（多 URL，runner 会逐个抓）

新增 `schools.yaml` 段：

```yaml
- code: shtech
  name_cn: 上海科技大学
  departments:
    - code: sist
      name_cn: 信息科学与技术学院
      list_urls:
        - <URL_zh>
        - <URL_en>   # 可选，提高召回
```

抓 fixture（列表 + 1-2 个 profile）到 `tests/fixtures/shtech/`。

## Step 3 — 实现

`src/claw/adapters/shtech.py`：

```python
@register
class ShtechAdapter(SchoolAdapter):
    school_code = "shtech"
    supports = {"sist"}
    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

中文页可能与英文页结构不同，按 list_url 后缀判断走不同分支（参照 sjtu adapter 的多入口处理）。

## Step 4 — 测试

`tests/test_parsers/test_shtech.py`：

1. `test_parse_list_basic`：解析数 ≥ 50（SIST 是大院），姓名 100%，邮箱率 ≥ 70%（上科大邮箱多明文）
2. `test_filter_non_PI`：list 不含 "Postdoc" / "Visiting Scholar" / "PhD student"
3. `test_parse_profile_research`：bio 或 research_interests 至少一项非空

## Step 5 — 自查 + commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/shtech.py tests/test_parsers/test_shtech.py
git add src/claw/adapters/shtech.py tests/ schools.yaml
git commit -m "feat(shtech): 上海科技大学 adapter v1

- depts: sist (single faculty for CS/AI/SW/Comm)
- fixtures: ...
- handles: <列出特殊点>"
```

报告：`docs/reports/shtech_report.md`。

## 该校特殊点

- **单学院**：只 sist，但 PI 数 100+，需要分页处理（看 URL 是否有 `?page=N` 或类似）
- **中英双轨**：英文页通常比中文页字段更全（research interests 是英文短语），可以把中英两个 URL 都挂上
- **PI 等级筛选**：要过滤掉学生、博士后、访问学者
- **邮箱**：上科大邮箱基本明文 `xxx@shanghaitech.edu.cn`，反爬轻

## 严禁

同 v0.2 模板（不改 `adapters/__init__.py`、不动其他学校文件、不 push 到 origin、不 commit data/.env）。

## 完成的标志

`docs/reports/shtech_report.md` 写满；`schools.yaml` 有 `shtech` 段；fixtures 至少 1 个 list + 1 个 profile。
