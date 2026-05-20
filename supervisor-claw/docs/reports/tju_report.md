# tju adapter v1 — completion report

- **Branch**: `worktree-agent-a17d82f673c5a2300`
- **Worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-a17d82f673c5a2300`
- **Date**: 2026-05-20

## Deliverables

| Path | Note |
|---|---|
| `src/claw/adapters/tju.py` | `TjuAdapter` registered; depts `{ic}` |
| `tests/test_parsers/test_tju.py` | 13 test cases (2 parametrized list + 5 parametrized profile + 6 others) |
| `tests/fixtures/tju/` | 2 list HTMLs (by-title + by-name) + 5 profile HTMLs |
| `schools.yaml` | appended `- code: tju` block with single `ic` department + both indexes |

## Coverage observed against fixtures

| Index | URL | Fixture | Parsed items | Title coverage |
|---|---|---|---|---|
| by-title | `cic.tju.edu.cn/szdw/szmd/azcjs.htm` | `list_by_title.html` | **150** | 149/150 (99 %) tagged via `<h3>` bucket |
| by-name | `cic.tju.edu.cn/szdw/szmd/azmjs.htm` | `list_by_name.html` | **170** | 0/170 — by design (h3 letters are A-Z, not titles) |

The by-name index is a **strict superset** of by-title (extra 20 entries include 助理工程师 / 研究员 / 工程师 buckets and a handful of new arrivals; by-title's 助教 bucket gets joined too). Union after pipeline `(school, name, email)` dedupe → **~170 unique faculty**.

Profile parsing verified on **5 fixtures** (all 教授 / 讲席教授, sampled across info buckets 1067/1071/1075/1079):

- **email** non-empty on **5/5** (100 %, all plaintext, no obfuscation)
- **research_interests** non-empty on **5/5** (3 tags each, all ≤ 25 chars, no parenthetical noise, no JS leakage)
- **homepage** populated from `个人主页` field on **5/5** (varies between `cic.tju.edu.cn/faculty/...`, `seea.tju.edu.cn/...`, external)
- **title** upgraded to a more specific bucket on **1/5** (刘安安: list-page `教授` → profile-page `讲席教授`)
- **bio_text** non-empty on **5/5** (synthesised from labelled fields since profiles have no free-form `个人简介` block in the v0.4 template)
- **is_recruiting**: None on all 5 — no 招生 keywords in any fixture's main content (no false positives from the nav menu's `招生招聘`)

## Department / page layout summary

1. **single learning-college umbrella** — 智能与计算学部 (College of Intelligence and Computing, CIC, founded 2018) sits over 计算机学院 / 软件学院 / 人工智能学院 / 网络空间安全学院 / 数学（应用方向）. The schema-level `dept_code` is therefore a single `ic`; sub-college affiliation lives inside each profile's `所在系别` field (e.g. "人工智能学院", "软件学院软件工程系", "计算机学院") for v0.4+ enricher use.
2. **two equivalent faculty indexes** —
   * by-title `/szdw/szmd/azcjs.htm` — groups by 教授 / 副教授 / 讲师 / 助教 / 研究员 / 专任助理研究员 / 工程师 / 助理工程师 under `<h3 class="h3_jiaoshou">`.
   * by-name `/szdw/szmd/azmjs.htm` — groups by initial-letter A-Z under the same `<h3>` class.

   Both render every advisor as `<li><a href="../../info/<bucket>/<id>.htm" title="姓名">姓名</a></li>` inside `<ul class="ul_jiaoshi">`. **Critical**: the two indexes use **different `<bucket>` numbers for the SAME person**, so URL-based dedupe across indexes cannot collapse them — name-based (pipeline level) dedupe is mandatory.
3. **uniform profile template** — `div.v_news_content` containing ten labelled `"label：value"` lines (姓名 / 性别 / 职称 / 所在系别 / 主讲课程 / 导师类型 / 电子邮件 / 研究领域 / 研究方向 / 个人主页). Tags are short, comma- or 顿号-separated.

## Known oddities

1. **JS click-counter inside every `<ul>`** — each `ul.ul_jiaoshi` ends with `<script>_showDynClickBatch([...])</script>`. selectolax fold the script's text into `ul.text()`, so the adapter selects `ul > li > a` only and never reads raw `ul` text.
2. **Label/value line split** — roughly a third of profiles render `电子邮件：\n yahong@tju.edu.cn` (label and value on **separate DOM lines**), versus the rest using `电子邮件：yahong@tju.edu.cn` on one line. `_pair_split_labels` walks the flattened line stream and glues the pair back together. Without it, the kv-extractor would record `电子邮件：` with an empty value and email coverage would crater on those profiles.
3. **Title precedence** — list-page `<h3>` says e.g. `教授`, but profile-page `职称：讲席教授` carries the real bucket. The adapter prefers the profile-page value (no information loss).
4. **`研究领域` vs `研究方向`** — `研究领域` is the broad theme (often just "人工智能" or "Artificial Intelligence"); `研究方向` is the specific 3-5 tag list. The adapter prefers `研究方向` and falls back to `研究领域` only when 方向 is empty.
5. **No bio prose in v0.4 templates** — every profile is kv-only. To give downstream LLM enrichers context, the adapter synthesises a `bio_text` from `所在系别` + `导师类型` + `主讲课程` + `研究领域` + `研究方向`. v0.5 enricher can fetch external `个人主页` URLs for richer prose.
6. **Different `info/<bucket>/` numbers across indexes** — see §2 above. The adapter's `parse_list` dedupes by URL **within a single page**, but the two list URLs return different URLs for the same person. Acceptable — pipeline collapses via `(school, name, email)`.
7. **`招生招聘` in the global nav** doesn't bleed into bios because we only scope `find_recruit_paragraphs` to the `div.v_news_content` container, not `tree.body`. Validated: no fixture produced a false `is_recruiting=True`.

## Validation

- `python3 -m compileall -q src/claw/adapters/tju.py tests/test_parsers/test_tju.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception; `tju` present at index 13
- All **13 test cases** (2 parametrized list + 5 parametrized profile + 6 others) pass when invoked manually (pytest not installed on VPS — ran via direct function calls with a mocked `pytest` module)

Output of the manual test run:

```
PASS  test_dedup_across_indexes
PASS  test_email_carries_from_list_item
PASS  test_parse_list_basic [by_title]
PASS  test_parse_list_basic [by_name]
PASS  test_parse_list_by_name_leaves_titles_blank
PASS  test_parse_list_by_title_groups_titles
PASS  test_parse_profile_has_research_or_bio
PASS  test_parse_profile_no_js_no_nav  [liuanan]
PASS  test_parse_profile_no_js_no_nav  [chenshizhan]
PASS  test_parse_profile_no_js_no_nav  [hanyahong]
PASS  test_parse_profile_no_js_no_nav  [fengzhiyong]
PASS  test_parse_profile_no_js_no_nav  [huqinghua]
PASS  test_split_label_value_glued

13 passed, 0 failed
```

## Known limitations

- **No free-form bio** in v0.4 templates — synthesised from labelled fields. Real bios live on external `个人主页` URLs (varies: cic.tju.edu.cn/faculty/<slug>/, seea.tju.edu.cn/...); v0.5 enrich step can resolve them.
- **`所在系别` not surfaced as a structured field** in v0.4 — embedded inside `bio_text` only. When the schema grows `appointment.role` (v0.5+) the adapter can promote 所在系别 directly.
- **Pre-2018 satellite sites** (e.g. `cs.tju.edu.cn`, `soft.tju.edu.cn`) still exist for news/admissions but no longer publish faculty rosters — we don't bother with them.
- **Title `工程师 / 助理工程师 / 助教`** buckets are kept (broad whitelist) — downstream filters can drop pure admin/teaching-staff if needed.

## Suggestions for v0.5 generic layer

- The split-label-value gluing trick (`_pair_split_labels`) is now duplicated across the tju and ai.nankai adapters; worth lifting into `core/parser_utils.py` as `glue_label_value_pairs(lines, labels)`.
- The `<h3>` letter-vs-title disambiguation logic could become a generic helper for any vsb-cms-style "by-name index" page.
- The `_FIELD_LABELS` / `_PSEUDO_HEADERS` whitelists are 80 %+ identical across tsinghua, pku, nankai, and tju — promote into a shared `core/cn_labels.py` module so new adapters can `from .cn_labels import KV_LABELS, PSEUDO_HEADERS` instead of redefining.
