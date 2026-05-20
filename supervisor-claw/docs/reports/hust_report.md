# 华中科技大学 (HUST) adapter — v0.4 report

**Branch**: (agent-managed) — see worktree HEAD
**Date**: 2026-05-20
**School code**: `hust`
**Departments**: `cs`, `sw`, `ai`

## 1. Domain map

| dept | 学院 | 入口域名 | 单页/多页 | 静态 GET 可达？ |
|------|------|---------|----------|----------------|
| `cs` | 计算机科学与技术学院 | `cs.hust.edu.cn` | 单页 `szdw/jsml/ayjslb.htm` (按研究所) | yes |
| `sw` | 软件学院 | `sse.hust.edu.cn` (注: `software.hust.edu.cn` DNS 不通) | 三页：`szdw1/js_yjy.htm`(教授) + `fjs.htm`(副) + `js1.htm`(讲师) | yes |
| `ai` | 人工智能与自动化学院 (AIA) | `aia.hust.edu.cn` | 单页 `szdw/xysz/axlb.htm` (按职称组) | yes |

`cs` 的 sibling 入口 `axmpyszmlb.htm` (按姓名拼音首字母) 在静态 GET 下也能拿到所有 PI 链接 (≈149)，但它的分组标签是 A/B/C/… 字母，不如 `ayjslb.htm` 的研究所标签信息量大；schools.yaml 选 `ayjslb.htm`。

## 2. 解析覆盖（基于本期 fixtures）

| dept | fixture 来源 | parse_list 返回数 | 备注 |
|------|-------------|-------------------|------|
| cs   | `list_cs_ayjslb.html`     | **151** | 涵盖 11 个研究所；与官网公开人数 (~150) 完全对齐 |
| sw   | `list_sw_js_yjy.html`     | 14      | 教授 / 研究员 |
| sw   | `list_sw_fjs.html`        | 16      | 副教授 |
| sw   | `list_sw_js1.html`        | 3       | 讲师 |
| **sw 合计** | —                   | **33**  | ≥ 启动文档目标 (≥20) |
| ai   | `list_ai_axlb.html`       | **117** | 正高 68 / 副高 40 / 中级 9；与 `axmpyszmlb.htm` 的 120 高度重合 |

## 3. 两种 profile 模板

HUST 的 PI 个人页根据是否启用了统一的 `faculty.hust.edu.cn` 个人页系统而分两类。adapter 用 hostname 路由：

### 3.1 `faculty.hust.edu.cn/<slug>/zh_CN/index.htm` — “blockwhite” 模板

整页是一串 `<div class="blockwhite XXX">` 卡片：

| class 后缀 | h2 内文 | 用途 |
|------|------|------|
| `JS-display`  | 姓名 | 大头像 + 姓名（`<img>` 静态无 `src`，src 在 inline script 的 `ImageScale.addimg("/path...")` 调用里）|
| `Psl-info`    | `个人信息` | 职称 / 性别 / 单位 / 学位 / 学科 |
| `Ot-ctact`    | `其他联系方式` | **邮箱 / 邮编 / 办公地址都是一长串 hex 加密 blob**，前端 JS 解密 |
| `Psl-info` (二)| `个人简介` | 长 prose bio |
| `Edu-exp` x N | `教育经历` / `工作经历` / `社会兼职` | 经历列表 |
| `Rsh-focus`   | `研究方向` | tag 列表（每个 `<li>` 一个 tag）|

**结构关键点**：每个 block 都有：
- `div.title > div.info > h2` —— 干净的中文标签（不是 `<div class="title">` 直接 text，那是 “研究方向Research focus” 双语连写）
- `div.cont` —— 干净的 body（**直接取 `.cont` 文本即可，不需要 prefix-strip**）

所以正确的 section 切分是：`block.css_first('h2')` 取标签 + `block.css_first('div.cont')` 取 body。

**邮箱**：留 `email=None, email_obfuscated=True`，让 v0.3 enricher 通过搜索补全。**不要硬猜域名**（HUST 内部不同部门用不同邮箱域：`@hust.edu.cn` / `@mail.hust.edu.cn` / `@cs.hust.edu.cn` 都见过）。

**照片**：从 inline `<script>` 的 `addimg(\"/_resources/...\")` 提取。

### 3.2 `<school>.hust.edu.cn/info/<treeid>/<id>.htm` — VSB legacy 模板

`v_news_content` 容器，结构紧凑：

```
<div class="v_news_content">
  <div class="pic fl"><img></div>
  <div class="txt fr">
    <h1>姓名</h1>
    <p><strong>职称：</strong>教授</p>
    <p><strong>电话：</strong>027-XXXXXXXX</p>
    <p><strong>邮箱：</strong>x@hust.edu.cn</p>
    <p><strong>研究方向：</strong>方向1，方向2，方向3</p>
  </div>
  <h2>个人简介</h2>
  ... 长 prose bio ...
</div>
```

**邮箱**：plaintext 明文 mailto-style，`extract_email` 直接拿到。

**子变体**：`sse.hust.edu.cn/info/.../X.htm`（如白翔）有时**没有结构化 `<p>`**，只是一坨 prose；adapter 此时退化到“尽力提取 bio prose、研究方向通过正则 `研究(方向|领域)[为是:：](.{2,200})` 拉一次”，效果有限，但满足 “bio 或 research_interests 至少一个非空” 的硬性指标。

**Footer 邮箱**：`v_news_content` 容器内部偶尔嵌入网站全局页脚 mailto（`sse@hust.edu.cn` / `ssedean@hust.edu.cn` / `aia-president@hust.edu.cn`）；adapter 用 `_FOOTER_EMAIL_PREFIXES` 黑名单显式拒绝。

## 4. 列表页结构

三个学院的列表页都建在 VSB 模板上：

```
<div class="munu_js">
    <h6>研究所名 / 系名 / 职称</h6>
    <div class="js_bt">
        <ul>
            <li><a href="http://faculty.hust.edu.cn/.../zh_CN/index.htm">姓名</a></li>
            ...
        </ul>
    </div>
</div>
```

`sse.hust.edu.cn` 的 rank 页是变种（无 `div.munu_js`），但有 `<li id="line_uXX_N"><a title="姓名"...>` 卡片。Adapter 内部 dispatch：

1. `_parse_list_munu` —— 优先（CS/AIA）
2. `_parse_list_li_pic` —— 备用（SSE rank 页）
3. `_parse_list_generic` —— 兜底（全锚扫描）

每个 `<a href>` 必须命中以下两种 URL 之一才算 profile（其他都是 nav）：

- `faculty.hust.edu.cn/<slug>/zh_CN`
- `/info/<treeid>/<id>.htm`（含 `../info/...` 相对路径）

## 5. 已知限制 / Known limitations

1. **AIA 学院 = 自动化 + AI 合院**。本期 117 人全收，不做学科过滤。从 group 标签上看：正高 68 / 副高 40 / 中级 9，其中 AI 方向占比估计 30-40% (启动文档说 AI ≥ 20 人——这肯定能覆盖)。v0.3 DeepSeek enricher 需要按研究方向关键词 (机器学习/视觉/NLP/智能/机器人) 二次过滤纯控制方向 PI。
2. **faculty.hust 邮箱全部 obfuscated**。所有走 `faculty.hust.edu.cn` 模板的 PI（绝大多数 CS/SW PI、部分 AIA PI）`email=None, email_obfuscated=True`。需 v0.3 enricher 通过 Bing/Google 补全。**adapter 不做 OCR、不猜域名**。
3. **SSE 的 jzjs / cyjs / spjs / gjgccrc 子页未收**。它们是兼职/产业/双聘/国家高层次人才标记，要么不是全职 CS faculty，要么是已有教师的 highlight 子集 (pipeline 按 (school, name, email) 去重不会增收)。schools.yaml 故意不挂这三页。
4. **`software.hust.edu.cn` 不通**。曾经的备用域名 DNS 解不出来，schools.yaml 只挂 `sse.hust.edu.cn`。
5. **`axmpyszmlb.htm` 看起来 JS-rendered 实际上 SSR**。第一次 sample 取的是 group "A" (空，JS 后补)，但 B/C/D/… 的字母组都是 SSR 满的。本期选了 `ayjslb.htm` 因为研究所标签更有用；若 ayjslb 改版可秒切到 axmpyszmlb。

## 6. fixture 列表

```
tests/fixtures/hust/
├── list_cs_axmpy.html              # 备用 (SSR 满)
├── list_cs_ayjslb.html             # CS 主入口 (151 PI)
├── list_sw_js_yjy.html             # SW 教授 (14)
├── list_sw_fjs.html                # SW 副教授 (16)
├── list_sw_js1.html                # SW 讲师 (3)
├── list_ai_axmpyszmlb.html         # AIA 拼音版备用
├── list_ai_axlb.html               # AIA 主入口 (117 PI)
├── profile_faculty_caoqiang.html   # faculty.hust 模板样本 (CS, 曹强)
├── profile_aia_internal_deng.html  # v_news_content 结构化样本 (AIA, 邓忠华)
└── profile_sw_info_baixiang.html   # v_news_content prose-only 变体 (SW, 白翔)
```

## 7. schools.yaml 改动

新增 `- code: hust` 段，挂 3 个 dept。未触碰其他学校。

## 8. 文件清单

- `src/claw/adapters/hust.py` (新建, ~480 LOC)
- `tests/test_parsers/test_hust.py` (新建, 9 个 test cases，含 3 个 parametrize)
- `tests/fixtures/hust/*.html` (10 个 fixture)
- `schools.yaml` (append `hust` 段)
- `docs/reports/hust_report.md` (本文件)
