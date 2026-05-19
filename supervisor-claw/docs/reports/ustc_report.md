# 中国科学技术大学 adapter — completion report

- **branch**: `feat/adapter-ustc`
- **commit**: `169d801` — feat(ustc): 中国科学技术大学 adapter v1
- **worktree**: `~/Projects/claude-tools/.claude/worktrees/feat+adapter-ustc` (harness-managed)
- **fixture capture date**: 2026-05-19

## test results

| check | status |
|---|---|
| `python3 -m compileall -q src/claw/adapters/ustc.py tests/test_parsers/test_ustc.py` | **PASS** |
| `pytest tests/test_parsers/test_ustc.py` | **not run on VPS** — selectolax/pydantic not installed per `~/.claude/CLAUDE.md` (run locally with `uv run pytest tests/test_parsers/test_ustc.py`) |

Logic-traced manually against each fixture:

| test | what it asserts | manual trace |
|---|---|---|
| `test_parse_list_cs_prof_basic` | ≥15 rows, all have name+url, ≥80% email, 安虹@han@ustc.edu.cn | 18 rows on p1; class swap (`.news_tel` = email) handled |
| `test_parse_list_cs_assoc_title` | title="副教授" inferred from `/fjs_23239/`, no double-space in name | URL-prefix lookup table covers it |
| `test_parse_list_eeis_prof_basic` | 8 cards, all name+url, no email, title="教授" | eeis list pages omit email — verified |
| `test_parse_list_eeis_assoc_title` | title="副教授" from `/2615/`, ≥6 cards | URL lookup table |
| `test_parse_list_eeis_ignores_sidebar_cards` | `h5.card-title` + `p.card-text` filter rejects sidebar `<h6>` cards | sidebar uses `h6`, not `h5` |
| `test_parse_profile_cs_basic` | 安虹: email, ≥3 research_interests, bio>50, is_recruiting=True, no nav leak | inline pseudo-header split + first-line-wins for interests |
| `test_parse_profile_eeis_structured` | 常晓军: email=xjchang@..., ≥2 interests, no mwave@, no sidebar leak | h2-based section build inside `article.blog-details` |
| `test_parse_profile_eeis_freeform` | 陈力: external homepage picked up (staff.ustc.edu.cn) | `_find_external_homepage` regex |
| `test_parse_profile_eeis_empty` | 陈畅: empty body → no interests, no mwave@, no crash | h2 collector yields {} ; fall-through to profile_url |
| `test_cs_list_name_no_double_space` | "安  虹" collapsed to "安虹" | `re.sub(r"\s+", "", name)` |

## fixture coverage

| dept | list_url | parsed / actual | email % | RI % |
|------|----------|----------------|---------|------|
| cs (教授) | `https://cs.ustc.edu.cn/js_23235/list.htm` (+`list2.htm`) | 18 on p1 (32 total / 2 pages) | ~95% (inline `.news_tel`) | profile-side, ~80% |
| cs (副教授) | `https://cs.ustc.edu.cn/fjs_23239/list.htm` (+`list2.htm`) | 18 on p1 (36 total / 2 pages) | ~95% | profile-side, ~80% |
| ai-info (教授) | `https://eeis.ustc.edu.cn/2648/list.htm` (+ p2-8) | 8 on p1 (59 total / 8 pages) | **0%** on list; profile pages carry it | profile-side, ~50% (empty bodies bring it down) |
| ai-info (副教授) | `https://eeis.ustc.edu.cn/2615/list.htm` (+ p2-7) | 8 on p1 (49 total / 7 pages) | **0%** on list | profile-side, ~50% |

> Coverage % is an estimate based on fixture sampling — the pipeline will produce the real numbers when the user runs `uv run claw crawl ustc` locally.

## 特殊结构观察 (for v0.3 / v0.4 generic layer)

1. **CS 学院 class-name swap** — In `ul.news_list > li.news`, `.news_tel` actually contains the email and `.news_email` actually contains research-direction lines. Class names are misleading; rely on content shape, not class semantics, when generalising. Worth recording in a "class-name-vs-content registry" for v0.3.

2. **eeis 信息学院 inline-script photo URLs** — Photo `<img>` tags are *not* in the DOM; each card embeds a `<script>` that does `document.write('<img src="..."/>')` with the URL held in a `defpic` JS variable. Adapter extracts the URL via `defpic\s*=\s*['"]([^'"]+)['"]` regex on the script body. Worth promoting to a generic utility in `parser_utils` since other 大学jweb-templated sites use the same pattern.

3. **cs.ustc URL rot** — `schools.yaml`'s seed URL (`/2013/1224/c2496a30007/page.htm`) 301-redirects to `.psp` and that endpoint returns "访问地址无效，错误的模板参数". Replaced with the live `/js_23235/` and `/fjs_23239/` columns. **General lesson**: old jweb deep-URLs of the form `cXXXXa<id>/page.htm` are unstable; column-root URLs like `/<columnId>_<colName>/list.htm` are more durable.

4. **eeis profile-page trichotomy** — Older entries (pre-2020) have an *empty* `<div class="content">`; mid-era entries are a single freeform paragraph with no section headers; only the newest (2024+) wave uses proper `<h2>` sections (个人简介 / 研究方向 / 联系方式 / 所获荣誉 / 代表性论文). Adapter handles all three and degrades gracefully (profile body empty → fall back to list-item fields only). The 50% "RI on profile" stat above is dominated by empty-body olds.

5. **cs.ustc inline pseudo-headers without `<h4>`** — The body of `#wp_articlecontent` is a flat sequence of `<p>` blocks; section markers are *inline text* like `主要研究方向 ：<list>` or `招生信息 ：<text>`, often wrapped in `<strong>` or styled `<span>`. We slice on a known-label regex (sorted longest-first so `主要研究方向` wins over `研究方向`) and use a **first-line-wins** rule for `_split_interests` so cs.ustc's "tag list \n bio paragraph \n bio paragraph" pattern doesn't bleed bio fragments into `research_interests`.

6. **Department-wide email collision** — `mwave@ustc.edu.cn` is the EEIS department's contact address and appears in the page footer of *every* eeis profile. Adapter explicitly drops it so it never wins per-advisor email. Same pattern likely exists at other USTC sub-units (e.g., `cs@ustc.edu.cn`); not generalised yet.

## schools.yaml 改动

```diff
   - code: ustc
     departments:
       - code: cs
         name_cn: 计算机科学与技术学院
-        list_urls:
-          - https://cs.ustc.edu.cn/2013/1224/c2496a30007/page.htm
+        # 教授 (js_23235, 32 人 / 2 页) + 副教授 (fjs_23239, 36 人 / 2 页)
+        list_urls:
+          - https://cs.ustc.edu.cn/js_23235/list.htm
+          - https://cs.ustc.edu.cn/js_23235/list2.htm
+          - https://cs.ustc.edu.cn/fjs_23239/list.htm
+          - https://cs.ustc.edu.cn/fjs_23239/list2.htm
       - code: ai-info
-        name_cn: 信息学院（AI 方向）
-        list_urls:
-          - https://eeis.ustc.edu.cn/main.htm
+        name_cn: 信息学院（电子工程与信息科学系，含 AI 方向）
+        # 教授 (2648, 59 人 / 8 页) + 副教授 (2615, 49 人 / 7 页)
+        list_urls:
+          - https://eeis.ustc.edu.cn/2648/list.htm   # ... list8.htm
+          - https://eeis.ustc.edu.cn/2615/list.htm   # ... list7.htm
```
Full list is in `schools.yaml` (15 paginated URLs for ai-info, 4 for cs).

## known limitations

- **AI-direction filtering deliberately deferred.** The eeis department (信息学院 电子工程与信息科学系) covers far more than AI — RF/微波, 信号处理, 通信, 雷达, etc. The prompt suggested either keyword-filtering at parse time or punting to v0.3's DeepSeek extractor; this adapter does the latter ("先全收") because keyword lists are brittle and the LLM tagger has full bio + research text to judge from.
- **Special-appointment ranks not crawled.** USTC has rich 特任教授 / 特任副研究员 / 讲师 / 院士 lists that *are* PhD advisors but use template URLs we haven't added to `schools.yaml` (e.g. `trjs/list.htm`, `trfyjy/list.htm`, `ys_23622/list.htm` in CS; `2593/list.htm` for 讲师 in eeis). Adapter's `_TITLE_FROM_URL` table already maps those prefixes to titles, so adding them later is one yaml edit.
- **eeis empty-body profiles produce skeletal records.** ~30-40% of older eeis entries have an empty `<div class="content">`. For those advisors we deliver name, profile_url, title, research-direction (one phrase, from the list card) — no email, no bio, no quota signal. The v0.4 Playwright enricher (or v0.3 DeepSeek if it can scrape) could fall through to staff.ustc.edu.cn personal pages when present.
- **Photo URLs from eeis cards are normalised but not validated.** The `defpic` script string is taken verbatim; if `defpic` is empty the script falls through to a placeholder `person.png` which the adapter doesn't recognise (so `photo_url=None`, which is correct).
- **pytest not executed on VPS.** Per project policy, syntax was verified via `python3 -m compileall`; user should run `uv run pytest tests/test_parsers/test_ustc.py -v` locally to confirm assertions.
