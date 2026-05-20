# supervisor-claw v0.4 — 国防科技大学 adapter agent

## 你的标识

- **学校 code**: `nudt`
- **学校名**: 国防科技大学
- **要支持的学院**: `cs` (计算机学院), `ai` (智能科学学院 / 系统工程学院 AI 方向)
- **分支**: `feat/adapter-nudt`
- **worktree 路径**: `~/Projects/adapter-nudt`

> **国防科大是军校**，公开师资页非常有限，很多个人页可能要内网访问。本任务接受**不完整覆盖**：能爬到院士 / 公开教授名单层即可，详细 bio / 邮箱 / 招生信息基本拿不到。known limitations 部分要重点写明。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-nudt -b feat/adapter-nudt origin/main
cd ~/Projects/adapter-nudt
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/{tsinghua,pku}.py`
3. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
4. `supervisor-claw/schools.yaml`（新增 `nudt` 段）

## Step 2 — 调研

主站 `www.nudt.edu.cn`；CS/AI 候选入口：

- 计算机学院: `www.nudt.edu.cn/jsj/` 或学校师资栏目
- 智能科学学院: 看是否有 `zhinengkexue` 或 `ai` 子站
- 系统工程学院 AI 方向: `xitongxueyuan` 子站

**先用 WebFetch 验证**，能拿到啥就拿啥。预期：

- 公开页面只列出院士 / 长江学者 / 国家杰青
- 邮箱大概率全部缺失（军校通信纪律）
- bio / research_interests 可能只有一句话

新增 schools.yaml（即使只爬到 1 个 dept 也行）：

```yaml
- code: nudt
  name_cn: 国防科技大学
  departments:
    - code: cs
      name_cn: 计算机学院
      list_urls:
        - <URL>
```

不可达就**只挂 1 个 dept**，known limitations 里写清楚为何无法覆盖更多。

抓 fixture。

## Step 3 — 实现

`src/claw/adapters/nudt.py`：

```python
@register
class NudtAdapter(SchoolAdapter):
    school_code = "nudt"
    supports = {"cs"}  # 实际能跑通几个就写几个
    def parse_list(self, html, list_url): ...
    def parse_profile(self, html, profile_url, list_item): ...
```

容错为先：邮箱拿不到没关系，留空；title 拿不到留空。**绝对不要伪造**。

## Step 4 — 测试

`tests/test_parsers/test_nudt.py`：

1. `test_parse_list_basic`：解析数 ≥ 5（接受小规模），姓名 100%
2. `test_parse_profile_safe`：profile 即使内容很少也不应该 crash
3. **不要**断言 email 覆盖率 / bio 覆盖率

## Step 5/6 — commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/nudt.py tests/test_parsers/test_nudt.py
git add src/claw/adapters/nudt.py tests/ schools.yaml
git commit -m "feat(nudt): 国防科技大学 adapter v1

- depts: cs (可能仅院士/公开教授层)
- fixtures: ...
- handles: 公开数据有限，多数字段为空"
```

报告：`docs/reports/nudt_report.md`，**重点写 known limitations**：

```markdown
## known limitations
- 个人页大概率内网，邮箱 0% 覆盖
- 仅能爬到学校官网公开列出的院士 / 长江 / 杰青等头衔老师
- 招生信号几乎完全为空，依赖 v0.3 web_research agent 联网搜
```

## 该校特殊点

- **军校公开度有限**：接受不完整覆盖
- **官方邮箱**：基本是 `xxx@nudt.edu.cn` 但很少明文列在公开页
- **网络可达性**：部分子站可能仅校内可达，遇 timeout 直接记录，不要重试到死

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满（known limitations 必须详尽）；schools.yaml 有 nudt 段（即使只 1 dept）；至少 1 个 list fixture。
