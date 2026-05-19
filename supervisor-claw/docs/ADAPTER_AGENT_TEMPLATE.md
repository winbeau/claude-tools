# supervisor-claw adapter agent — task template

> **用法**：把整段当 prompt 投给 subagent 前，把所有 `<SCHOOL_CODE>` 和 `<SCHOOL_NAME>` 替换成对应学校（见文末附录），其他都不用动。一个 agent 一所学校，6 个并行没问题（每个 agent 用自己的 git worktree / 分支）。

---

## 项目背景（你来到这个仓库需要知道的）

`supervisor-claw` 是一个本地工具，爬 7 所中国顶尖高校 CS/AI 方向导师的公开信息（姓名/职称/邮箱/研究方向/招生/评价）到 SQLite，提供 CLI + Web UI。

- 仓库根: `~/Projects/claude-tools`（本地）/ `~/wenbiao_zhao/claude-tools`（VPS）
- 主包: `supervisor-claw/`
- Python 用 **uv** 管理，前端用 **pnpm**
- LLM 用 **DeepSeek**（不是 Anthropic API），调用走 openai SDK + `base_url`
- v0.1 清华 CS 已跑通 136 人，全部步骤经历过。你接的是 v0.2 —— 复刻这个流程到 `<SCHOOL_CODE>`。

## 你的任务（一句话）

为学校 `<SCHOOL_CODE>` 写 `src/claw/adapters/<SCHOOL_CODE>.py`，使 `uv run claw crawl <SCHOOL_CODE>` 能成功爬到该校所有 CS/AI 相关学院的导师信息。质量目标见 §4。

## 1. 工作环境约束

- **你在 VPS 上，2 核 1.88GB**。**不要**在本机跑 `uv sync` / `uv run claw crawl` / `pnpm install`。
- 改完代码 commit + push 到你自己的分支；用户在他自己的本地（WSL，16 核 15GB）跑实际爬虫和测试。
- 允许的本机动作：`curl` 抓单个页面到 fixture、`grep` 看代码、`python3 -c "..."` 小片段验证、`python3 -m compileall` 做语法检查、`git`。
- 不要 `uv run pytest` —— 让用户跑（VPS 没装项目依赖）。语法检查用 `python3 -m compileall -q src tests`。

## 2. 分支约定（避免 6 个 agent 互踩）

```bash
git checkout -b feat/adapter-<SCHOOL_CODE>
# 你的所有提交都在这个分支
git push -u origin feat/adapter-<SCHOOL_CODE>
```

**绝对不要碰**这些文件（合并冲突源，用户最后统一改）：

- `src/claw/adapters/__init__.py` —— 用户合并完所有分支后统一加 import
- 其他学校的 adapter 文件
- `data/` 整个目录（gitignored，不应该被 commit）

你**可以**改的文件：

- 新建: `src/claw/adapters/<SCHOOL_CODE>.py` ← 你的主交付
- 新建: `tests/fixtures/<SCHOOL_CODE>/*.html`
- 新建: `tests/test_parsers/test_<SCHOOL_CODE>.py`
- 改: `schools.yaml` —— 如果 `<SCHOOL_CODE>` 下的 `list_urls` 已经过期/错了，**只改你自己学校那一段**

## 3. 必须实现的接口

参考 `src/claw/adapters/base.py`：

```python
from .base import ListItem, SchoolAdapter, register

@register
class <SchoolName>Adapter(SchoolAdapter):
    school_code = "<SCHOOL_CODE>"
    supports = {"dept_code_1", "dept_code_2", ...}  # schools.yaml 里 <SCHOOL_CODE>.departments[].code

    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        """从师资列表页解析出 [(name, profile_url, title?, email?, phone?, photo_url?), ...]"""
        ...

    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        """从个人页拿 bio / research_interests / 招生信号，回填到 list_item 数据"""
        ...
```

数据模型 `AdvisorPartial` 在 `src/claw/models/pydantic_models.py`。字段：

| 字段 | 类型 | 备注 |
|---|---|---|
| name_cn | str | 必填 |
| title | str? | 职称（教授/副教授/...）|
| email | str? | 优先小写 |
| email_obfuscated | bool | 图片邮箱 / 解不出来时 True |
| phone | str? | |
| photo_url | str? | 头像绝对 URL |
| homepage | str? | 通常 = profile_url，除非个人页有"个人主页"外链 |
| bio_text | str? | 研究概况段，**不要含 JS / 导航文本** |
| research_interests | list[str] | tag 列表，每个 ≤ 25 字、无括号、无句末标点 |
| raw_quota_text | str? | 招生段原文 |
| is_recruiting | bool? | 找到招生关键词且**不是导航栏**时 True |
| source_url | str | 一般就是 profile_url |

## 4. 质量目标（最低线）

| 指标 | 要求 |
|---|---|
| `parse_list` 返回数 | ≥ 该院实际师资数 × 0.95 |
| 名字非空 | 100% |
| 邮箱非空 | ≥ 80% |
| research_interests 非空 | ≥ 60%（部分老师真的没有，别强求）|
| bio_text 不含 `function`/`<script>` | 100% |
| bio_text / raw_quota_text 不含导航文本 | 100% |
| pytest 全过 | 必须 |

## 5. 工作步骤

### 5.1 调研学校页面结构

1. 打开 `schools.yaml`，找 `<SCHOOL_CODE>` 段，记下每个 dept 的 `list_urls`。
2. 用 WebFetch 验证每个 list_url 是否可达；不可达则用 WebSearch 找正确入口，写回 `schools.yaml`。
3. WebFetch 每个学院的师资页，告诉模型："列出每个老师的 HTML 结构（包括 selectors）、字段、个人页 URL 前 5 个、邮箱明文还是 obfuscated"。
4. 用 `curl` 把代表性 list 页和 1-2 个 profile 页存到 `tests/fixtures/<SCHOOL_CODE>/`：
   ```bash
   curl -sSL -A "supervisor-claw/0.1 (research)" \
     "https://..." -o tests/fixtures/<SCHOOL_CODE>/list_<dept>.html
   ```

### 5.2 实现 adapter

参考 `src/claw/adapters/tsinghua.py` 这份**已经经过 5 轮迭代修过 bug 的**代码。

**核心套路（直接抄）**：

```python
from ..core.parser_utils import (
    absolutize, extract_email, find_recruit_paragraphs, parse, text_of,
)
from ..models.pydantic_models import AdvisorPartial
from .base import ListItem, SchoolAdapter, register


@register
class XxxAdapter(SchoolAdapter):
    school_code = "<SCHOOL_CODE>"
    supports = {"dept1", "dept2"}

    def parse_list(self, html, list_url):
        tree = parse(html)
        items = []
        for card in tree.css("...selector for each advisor row..."):
            name_node = card.css_first("...")
            href = name_node.attributes.get("href", "")
            if not href: continue
            # extract email/title/phone from card if available
            ...
            items.append(ListItem(
                name_cn=text_of(name_node),
                profile_url=absolutize(list_url, href),
                title=...,
                email=...,
                phone=...,
                photo_url=...,
            ))
        return items

    def parse_profile(self, html, profile_url, list_item):
        tree = parse(html)
        content = tree.css_first("...main content selector...") or tree.body
        scope_text = content.text(separator=" ", strip=True) if content else ""

        sections = _split_sections(content)  # h4 / pseudo-header 风格的分段

        research_tags = []
        for key in ("研究方向", "研究兴趣", "研究领域"):
            if key in sections:
                research_tags = _split_interests(sections[key])
                if research_tags: break

        bio = None
        for key in ("研究概况", "个人简介", "简介"):
            if key in sections and sections[key].strip():
                bio = sections[key][:1000]; break

        recruit_chunks = find_recruit_paragraphs(scope_text)
        raw_quota_text = "\n\n".join(recruit_chunks[:3]) if recruit_chunks else None

        return AdvisorPartial(
            name_cn=list_item.name_cn,
            title=list_item.title,
            email=list_item.email or extract_email(scope_text)[0],
            email_obfuscated=...,
            phone=list_item.phone,
            photo_url=list_item.photo_url,
            homepage=profile_url,
            bio_text=bio,
            research_interests=research_tags,
            raw_quota_text=raw_quota_text,
            is_recruiting=True if recruit_chunks else None,
            source_url=profile_url,
        )
```

### 5.3 写测试

放在 `tests/test_parsers/test_<SCHOOL_CODE>.py`：

```python
from pathlib import Path
import pytest
from claw.adapters.<SCHOOL_CODE> import XxxAdapter
from claw.adapters.base import ListItem

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "<SCHOOL_CODE>"

@pytest.fixture
def adapter(): return XxxAdapter()

def test_parse_list_basic(adapter):
    html = (FIX / "list_<dept>.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, "https://...")
    assert len(items) > 10
    assert all(it.name_cn and it.profile_url for it in items)
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) > 0.8

def test_parse_profile_no_js_no_nav(adapter):
    html = (FIX / "profile_sample.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="...", profile_url="...")
    p = adapter.parse_profile(html, "...", item)
    assert p.bio_text and "function" not in p.bio_text
    assert p.bio_text and "招生招聘" not in (p.raw_quota_text or "")
    if p.research_interests:
        assert all(len(t) <= 25 and "(" not in t for t in p.research_interests)

def test_parse_profile_has_research_or_bio(adapter):
    html = (FIX / "profile_sample.html").read_text(encoding="utf-8")
    item = ListItem(name_cn="...", profile_url="...")
    p = adapter.parse_profile(html, "...", item)
    assert p.bio_text or p.research_interests
```

### 5.4 自查 + 提交

```bash
# 语法检查（VPS 上）
python3 -m compileall -q src/claw/adapters/<SCHOOL_CODE>.py tests/test_parsers/test_<SCHOOL_CODE>.py

# 提交
git add src/claw/adapters/<SCHOOL_CODE>.py tests/test_parsers/test_<SCHOOL_CODE>.py tests/fixtures/<SCHOOL_CODE>/
# 如果改了 schools.yaml:
git add schools.yaml

git commit -m "$(cat <<'EOF'
feat(<SCHOOL_CODE>): <SCHOOL_NAME> adapter v1

- Departments covered: <list of dept codes from supports={}>
- Fixtures: <N> list pages + <M> profile samples (real fetched HTML)
- Parser handles <special structure observations>
- Tests: list 解析数 + email 覆盖率 + 反 JS/nav 泄漏

Known limitations: <if any, e.g. dept Y is SPA, needs Playwright in v0.4>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push
```

## 6. 清华 adapter 踩过的坑（**必读**，避开它们）

### 6.1 `node.css("h4, p")` 不保留 document order

selectolax 的多选 selector 是"按 selector 分组"返回，先所有 h4 再所有 p。
**永远用 `node.traverse(include_text=False)` 然后按 `n.tag` 过滤**，否则 section 分组算法挂掉。

### 6.2 三种 section header 风格都要支持

- `<h4>研究领域</h4>` 后跟 `<p>tag list</p>` —— 标准
- `<h4><p>研究领域</p></h4>` —— h4 里嵌 p；traverse 会把内层 p 跟在 h4 后吐出来，要跳过这个"重复"
- `<p>研究领域</p>` 没有 h4 —— pseudo-header，看文本是否匹配已知关键词（"研究领域"、"研究方向"、"研究兴趣"、"研究概况"、"招生信息"、"教育背景"等）

### 6.3 strip trailing colons

`<p>研究领域：</p>` 后跟 tag —— pseudo-header 检测时要 `.rstrip("：:")` 再查表。

### 6.4 tag 分隔风格不统一

- 冯建华：`数据库管理系统，数据安全与隐私保护，信息检索`（一段，全角逗号）
- 李国良：`大数据挖掘与分析` / `人机协作群体计算` / `时空大数据处理` 各自一个 `<p>`
- 李涓子：`知识图谱、大模型和数据挖掘`（一段，顿号）

**正确做法**：永远先按 `\n` 分行，再每行 `re.split(r"[、，,；;/]+", line)`，每个 part 单独判断长度 / 标点。**不要**搞"如果整行短就当一个 tag"的捷径 —— 那样会漏切。

### 6.5 噪声 tag 过滤

拒绝任何含 `()（）。！？` 的 part：

```python
def _is_tag(p):
    return 2 <= len(p) <= 25 and not any(c in p for c in "。！？()（）")
```

这能挡掉 `(40240751, 本科)` 这种课程编号。

### 6.6 主内容容器一定要找

绝对不要 `tree.body.text()` —— 会拉到 `<script>` 内联 JS。
每所学校都有个类似 `div.v_news_content` / `div.col-2` / `div.detail` / `div#vsb_content_NNNN` 的主容器。**先 css_first 选它**，所有提取都在它内部做。

### 6.7 招生关键词在 nav / footer 里也会出现

"招生招聘 招生信息 ..." 经常出现在网站全局导航。**只在主内容容器内调用 `find_recruit_paragraphs(scope_text)`**，不要在 body 全局调用，否则 100% 都被误判 is_recruiting。

### 6.8 去重

主程序里有按 `(school, name_cn, email)` 的 upsert 去重。你**不需要**在 adapter 里去重，但要保证你解析出的 (name, email) 对是一致的 —— 比如同一人在列表页和 profile 页拿到的 email 应该相同。

### 6.9 学生 / 兼职页面

部分学院 list 会混入研究生 / 博士后 / 校外兼职。识别方法：

- URL 路径（北大 yjs/ssb/bys）
- 职称字段（不含"教授/副教授/研究员/讲师/工程师"）
- 整行 class（`.students` / `.alumni`）

在 `parse_list` 里过滤掉，不要返回。

### 6.10 邮箱反混淆

`core/parser_utils.extract_email(text)` 已经处理 `[at]/[dot]/(at)` 变体，返回 `(email, was_obfuscated)`。

**图片邮箱**：不要 OCR，标 `email_obfuscated=True` 让用户手填。

### 6.11 列表分页

很多学院有分页（`?currPage=N` / `?page=N` / `_2.htm`）。在 adapter 里**不要自己分页**，而是把所有分页 URL 都列进 `schools.yaml` 的 `list_urls`（pipeline 会逐一调用）。

## 7. 完成后给用户的报告

提交后回报：

1. **分支名**（`feat/adapter-<SCHOOL_CODE>`）
2. **pytest 输出截图 / 文本**（让用户在本地复跑也行）
3. **每个学院 fixture 覆盖到的师资人数 vs 该院实际公开数**（如 "CS 系: 86/89 = 96%"）
4. **该校页面观察到的 1-3 个特殊点**（结构/反爬/邮箱混淆等），让我归纳进 v0.3 通用层
5. **`schools.yaml` 改动**（如果有）

## 8. 你**不要**做的事

- ❌ 改 `src/claw/adapters/__init__.py` —— 6 个 agent 改这同一文件 = 合并冲突
- ❌ 改 `src/claw/core/*`、`src/claw/models/*`、`src/claw/pipeline/*`、`src/claw/storage/*`、`src/claw/cli/*`（如需新功能用 GitHub issue 告知用户）
- ❌ 改其他学校的 adapter
- ❌ 本机 `uv sync` / `uv run claw crawl`（资源有限 + 真打学校网）
- ❌ commit `data/` 下任何内容
- ❌ 把 DeepSeek API key 写进代码或 .env.example（key 在用户私有 .env，不入 git）
- ❌ 加新的 Python 依赖（`uv add` 类操作）—— 用 stdlib + 已有 deps（httpx / selectolax / sqlmodel / pydantic / typer / rich / tenacity）

---

## 附录：6 所学校的 dept 清单（替换 `<SCHOOL_CODE>` 时对照）

| SCHOOL_CODE | SCHOOL_NAME | departments (codes) |
|---|---|---|
| **pku** | 北京大学 | `eecs`（信息科学技术学院）, `ai`（智能学院）, `wangxuan`（王选所）, `cfcs`（前沿计算研究中心）|
| **nju** | 南京大学 | `cs`（计算机科学与技术系）, `ai`（人工智能学院）, `sw`（软件学院）|
| **ustc** | 中国科学技术大学 | `cs`（计算机科学与技术学院）, `ai-info`（信息学院 AI 方向）|
| **zju** | 浙江大学 | `cs`（计算机学院）, `cadcg`（CAD&CG 国家重点实验室）, `ai-inst`（人工智能研究所）, `sw`（软件学院）|
| **fudan** | 复旦大学 | `cs`（计算机科学技术学院）, `bd`（大数据学院）, `ai`（AI 创新与产业研究院）|
| **sjtu** | 上海交通大学 | `cs`（计算机科学与工程系）, `see-ai`（电院 AI 方向）, `ai`（人工智能学院）, `qingyuan`（清源研究院）|

详细 list_urls 看 `schools.yaml`（如果 URL 已经过期，你这一所学校段里更新）。

---

## 附录：核心模块快速参考

- `src/claw/adapters/base.py` —— ABC + `@register` + `ListItem`
- `src/claw/adapters/tsinghua.py` —— **照抄结构，按本校特性改 selector / sections / 噪声过滤**
- `src/claw/core/parser_utils.py` —— `parse(html)` / `text_of(node)` / `extract_email(text)` / `find_recruit_paragraphs(text)` / `absolutize(base, href)`
- `src/claw/core/http.py` —— 不用直接调，pipeline 会处理
- `src/claw/models/pydantic_models.py` —— `AdvisorPartial`
- `schools.yaml` —— 学校 / 学院 / list_urls 配置

祝顺利。质量 > 速度，不确定的字段宁可留空，让 v0.3 的 DeepSeek agent 补 —— 别 hardcode 编出来。
