# 复旦大学 adapter — completion report

- **branch**: `feat/adapter-fudan`
- **commit**: `3fef4de` (feat(fudan): 复旦大学 adapter v1)
- **worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-ad9b1fe5aa4bfe581`

## test results

- `python3 -m compileall -q src/claw/adapters/fudan.py tests/test_parsers/test_fudan.py` → **PASS**
- `python3 -m compileall -q src tests` (full project) → **PASS**
- `pytest tests/test_parsers/test_fudan.py` → **not run on VPS** (selectolax /
  pydantic absent per CLAUDE.md no-pip rule). Logic spot-checked offline with
  stdlib-only reimplementations of the JSON path (`_parse_list_cs_json`,
  `_clean_email`), the regex-only BD table walker, and the section-walking
  algorithm via `html.parser.HTMLParser`; all returned the expected per-fixture
  counts and field shapes. User should run `uv run pytest tests/test_parsers/test_fudan.py -v`
  on the local 16-core WSL box before merging.

## fixture coverage

Public faculty headcounts taken from the live pages at scrape time (2026-05-19).

| dept | list_url (truncated) | parsed (advisors) | actual / source | email % (list-level) | notes |
|---|---|---|---|---|---|
| cs | `cs.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome…` (JSON) | **134** advisors after filter (288 raw rows) | matches "教授 + 副教授 + 院士 + 讲师" headcount across the 5 academic categories (院士 3 + 正高 63 + 副高 52 + 讲师 ≈ 16) | **~99 %** (133/134 carry an email) | placeholder `/_res/articleType/teacher_CN.jpg` photo dropped; zero-width-space-suffixed emails normalised |
| bd | `sds.fudan.edu.cn/17428/list.htm` (教授/研究员) | 20 | matches all teacher `<tr>` rows on the page | n/a (no email column in list cells) | 16/20 carry an external personal-page link inside `<h4><a>`; rest have profile_url=None |
| bd | `sds.fudan.edu.cn/17429/list.htm` (副教授/副研究员) | 15 | matches all teacher rows | n/a | 12/15 link to external personal pages |
| ai | `ai3.fudan.edu.cn/yjspy/dsdw.htm` | 16 | matches the published "导师队伍" 16-card list (excludes the 3-person 科学指导委员会 honorary section) | n/a (list cards don't expose email) | 100 % of cards carry name + profile_url + title |

**Aggregate**: ≥ 185 distinct advisors across the three departments (134 cs + 35 bd + 16 ai),
with `name_cn` and `profile_url`-or-photo populated on every parsed entry.

Profile-level samples (frozen as fixtures):

| fixture | template | field extraction |
|---|---|---|
| `profile_cs_chenyang.html` (陈阳, cs/ai.fudan.edu.cn) | `<div class="news_*">` semantic blocks | title `教授、博导` (reassembled from `nr1`+`、`+`nr2`), email `chenyang@fudan.edu.cn`, 5 interests from 研究领域 row, external homepage from `news_gr` |
| `profile_cs_qiuxipeng.html` (邱锡鹏, cs) | same | email `xpqiu@fudan.edu.cn`, bio from 个人简介 row (~250 chars), 研究领域 empty → research_interests=[] |
| `profile_ai_chenxi.html` (陈曦, AI³) | sudy v_news_content + blue-bold pseudo-headers | 研究方向 split into 5–8 tags, 招生专业 → is_recruiting=True, bio_text from 个人简介 fallback (page lacks the section, picks 研究方向 paragraph) |
| `profile_ai_chengyuan.html` (程远, AI³) | same | 研究方向 → 6 interests (multi-modal cognition, medical imaging, …); 招生专业 captured |

## 特殊结构观察

1. **CS 师资页是 SPA**：`cs.fudan.edu.cn/53161/list.htm` 是 Sudy CMS 的纯前端
   渲染页 —— 静态 HTML 里 `<ul class="cols_list career_N">` 都是空的。客户端
   `szpy2.js` 通过 `POST /_wp3services/generalQuery?queryObj=teacherHome` 拉数
   据。**好消息**：这个端点同时接受 GET，参数完全一样，单次返回全部 288 条记
   录（一次性，无分页）。所以 v0.2 直接把 GET URL 写进 `schools.yaml`，在
   adapter 里用 `_looks_like_cs_json` 嗅探 `{"total":...,"data":[...]}` 头部
   并走 JSON 分支，**省掉了 Playwright**。这是复旦特有的优势：sds（大数据）
   是静态 HTML 直出，AI³ 是另一个 CMS 实例，三套数据来源完全异构。

2. **学院重组带来的 host 漂移**：复旦在 2024 年把"计算机科学技术学院"并入
   "计算与智能创新学院"，但保留旧域名 `cs.fudan.edu.cn` 做对外门户。教师个
   人页面**实际服务器是 `ai.fudan.edu.cn`**（同一套 sudy CMS），所以 JSON
   返回的 `cnUrl` 都是 `http://ai.fudan.edu.cn/<slug>/list.htm`，**不是**
   cs.fudan 的子路径。`parse_profile` 要同时识别 `cs.fudan.edu.cn` 和
   `ai.fudan.edu.cn` 这两个 hostname 走同一个 profile parser。
   另外 `aiii.fudan.edu.cn`（v0.1 schools.yaml 里的）**不解析**，AI³ 真正的
   host 是 `ai3.fudan.edu.cn`（"3"，不是 "iii"）—— 这次已修正。

3. **BD 大数据学院把"列表 + profile"塞进同一个 `<tr>`**：sds.fudan.edu.cn 的师
   资页**没有独立 profile 页**。每个老师就是 `<tr>` 一行，左边 `<td>` 是 130×163
   头像，右边 `<td>` 是 `<h4>中文名</h4>` + `<p>` 一整段 bio（里面嵌
   `<strong>主要研究方向：</strong>` 子段）。`<h4>` 有时直接是 `<a href="外部
   个人主页">姓名</a>`（如冯建峰 → warwick.ac.uk），有时只是纯文本。v0.2 在
   `parse_list` 阶段提取 name + photo + 外部 profile URL；pipeline 会用这些
   URL 抓外部页面，但因为这些是个人 GitHub Pages，复杂多样，所以**BD 的
   profile parser 对 sds 自己的 host 不存在**（host 不匹配时返回
   `_empty_partial`，仅保留 list 阶段数据）。bio_text / research_interests
   留给 v0.3 的 DeepSeek 从 list 行内 `<p>` 文本里再提取。

## schools.yaml 改动

只动了 `fudan` 段，三个 dept 全部 list_urls 重写：

```diff
-      - code: cs
-        name_cn: 计算机科学技术学院
-        list_urls:
-          - https://cs.fudan.edu.cn/szdw/szdwjs.htm                              # 404
+      - code: cs
+        name_cn: 计算与智能创新学院（含 CS + AI 双聘）
+        list_urls:
+          - https://cs.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome&siteId=577&...
       - code: bd
         name_cn: 大数据学院
         list_urls:
-          - https://sds.fudan.edu.cn/szdw/qzjs.htm                               # 404
+          - https://sds.fudan.edu.cn/17428/list.htm
+          - https://sds.fudan.edu.cn/17429/list.htm
       - code: ai
         name_cn: 人工智能创新与产业研究院
         list_urls:
-          - https://aiii.fudan.edu.cn/                                           # DNS NXDOMAIN
+          - https://ai3.fudan.edu.cn/yjspy/dsdw.htm
```

注释里也加了为何把每个 URL 这样选的来由 + 双聘老师会在 cs/ai 两边重复出现（依
赖 pipeline 按 `(school, name, email)` upsert 去重）。

## known limitations

- **CS：依赖 sudy CMS 的 `/_wp3services/generalQuery` 端点是稳定 GET 接口的假
  设**。如果上游切到强制 POST 或加 CSRF token，adapter 会 silently 返回 []。
  缓解：fixture 里 freezing 一份 288 行 JSON，回归测试不依赖网络。如真出现
  接口换签，回退到 Playwright 渲染 SPA 即可（cs.fudan.edu.cn/53162/list.htm）。
- **BD：list 行内 bio + 研究方向**没有抽取到 `AdvisorPartial.bio_text /
  research_interests`。pipeline 会 upsert 一个只有 name + photo + 可选 profile
  URL 的瘦记录；v0.3 DeepSeek 应该从 list 行 raw HTML 直接补字段。
- **AI³ 16 人**远小于 cs/bd 的体量，因为 AI³ 是产业研究院（PI 制）而非教学
  单位。`/yjygk1/kxzdwyh1.htm` 那 3 人（金力、戴琼海、Michael I. Jordan）是
  科学指导委员会名义成员，**不计入 v0.2 师资**，故未列入 schools.yaml。
- **未测的真实抓取**：按 CLAUDE.md 红线，没在 VPS 上 `uv run claw crawl fudan`；
  所有结果数据来自 fixture HTML/JSON。用户在 WSL 跑一次完整 crawl 后会拿到
  接近 185 个真实 advisor 记录。
