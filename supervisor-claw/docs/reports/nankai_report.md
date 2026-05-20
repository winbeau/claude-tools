# nankai adapter v1 — completion report

- **Branch**: `worktree-agent-af4e5b1a256862c45`
- **Worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-af4e5b1a256862c45`
- **Date**: 2026-05-20

## Deliverables

| Path | Note |
|---|---|
| `src/claw/adapters/nankai.py` | `NankaiAdapter` registered; depts `{cs, cse, ai, sw}` |
| `tests/test_parsers/test_nankai.py` | 12 parametrized list tests + routing test + 7 profile-nav tests + research/bio test + email-carry test |
| `tests/fixtures/nankai/` | 12 list HTMLs (3 cs + 2 cse + 3 ai + 4 sw) + 7 profile HTMLs (2 cs + 1 cse + 2 ai + 2 sw) |
| `schools.yaml` | appended `- code: nankai` block with 4 departments |

## Coverage observed against fixtures

| Dept | Host | Fixture(s) | Parsed items | List-page email coverage |
|---|---|---|---|---|
| cs | cc.nankai.edu.cn | jswyjy + fjswfyjy + js | 43 + 42 + 13 = **98** | 40/43, 38/42, 13/13 ≈ **93 %** |
| cse | cyber.nankai.edu.cn | 13336/list + jswyjy | 31 (umbrella) / 31 (教授) | 29/31 ≈ **94 %** (overlap with cs is expected) |
| ai | ai.nankai.edu.cn | js_yjy_ + fjs_fyjy_ + j_s | 37 + 24 + 9 = **70** | 0 — table list has no email column; profile fills it (100 % on the 2 profile fixtures) |
| sw | cs.nankai.edu.cn | js + js/1 + fjs + js2 | 8 + 1 + 8 + 7 = **24** | 0 on list; profile fills it (2/2 on profile fixtures) |

Profile parsing verified on **7 fixtures** (2 cs, 1 cse, 2 ai, 2 sw):

- bio_text non-empty on **7/7** (avg ≈ 400-1500 chars, capped at 1500)
- research_interests non-empty on **7/7** (3-4 tags each, all ≤ 25 chars, no parenthetical noise)
- email_recovered on **5/7** (the 2 misses are cc.nankai profiles where the email lives only on the list-page card — the pipeline carries it across automatically via `ListItem.email`)
- is_recruiting=True on **1/7** (陈飞 — has a dedicated 招生说明 section)

## Department layout summary

1. **cs (cc.nankai)** — Bootstrap custom theme. Cards `<div class="img-card">` with `<div class="img-content">` holding an `<a>` (text = "姓名      职称") plus 3 `<p>` lines (phone? / email / one-line research). Three ranks served at `/jswyjy/list.htm` (教授), `/fjswfyjy/list.htm` (副教授), `/js/list.htm` (讲师). No pagination — single page per rank.
2. **cse (cyber.nankai)** — **Identical template to cs**. Just a different hostname and a `c13838a<NNN>` profile slug instead of `c13619a<NNN>`. The same 教授/副教授/讲师 sub-pages exist (`/jswyjy/list.htm` etc.). Many faculty appear on both cc.nankai and cyber.nankai (e.g. 陈森) because the cyber college was carved out of cs in 2021; pipeline `(school, name, email)` upsert handles dedup.
3. **ai (ai.nankai)** — Vsb-cms `wbnewsfile` template. List pages are plain `<table>` with 4 columns (`姓名|职称|所属部门|研究方向`). No email/photo on the list page. Profile body is `div.v_news_content` containing a nested table with labelled rows ("姓 名：" / "电子邮件：" / "研究方向：" / ...) plus prose sections under pseudo-headers (基本信息 / 个人简介 / 招生说明 / 科研项目、成果、获奖、专利).
4. **sw (cs.nankai — **the misleading subdomain**)** — Custom Bootstrap theme distinct from both cc.nankai and ai.nankai. List teachers are rendered as `<a class="item">` anchors containing `<div class="name">` + `<div class="des"><div>所属部门: ...</div>...`. Paginated (e.g. `/szdw/js.htm` + `/szdw/js/1.htm`). Profile body is also `div.v_news_content` but with `<p><strong>label</strong><span>: value</span></p>` lines.

## Known oddities (school-specific layer)

1. **`cs.nankai.edu.cn` is the software college**, not the CS college. The actual CS college lives at `cc.nankai.edu.cn`. The adapter's `_dept_from_url` ratchet routes on hostname accordingly. Don't conflate when adding new list URLs.
2. **cs / cse overlap** — 陈森 (and likely others) is listed on both subdomains. Both records have the same `name_cn` + `email`, so the pipeline's `(school, name, email)` upsert merges them. No adapter-level dedup needed.
3. **cc.nankai list-page email/research are unstructured** — they're just bare `<p>...</p>` siblings of the anchor. The adapter classifies them heuristically (`@` → email, all-digit ≥ 6 chars → phone). One `<p>` per card holds a one-line research summary that we currently leave on the list-page only (it's also in the bio text).
4. **cc.nankai profile is mostly free-form prose** with no field labels (`div.wp_articlecontent > div.text`). Research interests are extracted by a defensive regex (`研究(?:方向|兴趣|领域)[为是:：]?\s*([^。\n，,；;]{2,80})`) that stops at the first clause-end punctuation — without that bound, citation counts and h-index numbers leak into the tag list.
5. **ai.nankai profile labels use full-width spaces** ("姓  名：" with 2 spaces, "职  称：" similarly). We normalise via `\s+` collapse before lookup.
6. **ai.nankai bio text contains lots of section headers** (基本信息 / 个人简介 / 招生说明 / 科研项目、成果、获奖、专利) — recognised via the `_PSEUDO_HEADERS` whitelist (mirrors the tsinghua / pku adapters). 招生说明 surfaces as `raw_quota_text` only when present.
7. **sw (cs.nankai) profile uses `<p><strong>label</strong><span>: value</span></p>`** — when flattened to text via `.text(separator="\n")`, the result is "label\n: value" so the field-line regex must tolerate the space before the colon. Already handled by `_NAME_TITLE_SPLIT_RE` and `field_re`.
8. **sw pagination** — `js.htm` (page 1) plus `js/1.htm` (page 2). Adapter is pagination-agnostic; `schools.yaml` enumerates every page explicitly.

## Validation

- `python3 -m compileall -q src/claw/adapters/nankai.py tests/test_parsers/test_nankai.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception; school code `nankai` present at index 10
- All 22 test cases (12 parametrized list + 7 parametrized profile + 3 others) pass when invoked manually (pytest not installed on the VPS, ran via direct function calls with a mocked `pytest` module)

## Known limitations

- **cc.nankai phone field**: detection is regex-only (`\d{6,}`); some entries put the URL of a personal homepage into the same `<p>` slot, which we correctly leave as None.
- **cc.nankai profile research_interests**: prose-based, often a single phrase like "计算机视觉和计算机图形学" rather than a list. The DeepSeek enrichment layer (v0.3) should refine these into proper tag arrays.
- **sw email coverage on list page = 0** — labelled cards have an "电子邮件:" line but it's empty in every observed fixture. Emails come from the profile page (covered).
- **No JS / SPA** observed on any of the four hosts — straight static HTML for all 11 list-page fixtures and 7 profile fixtures.

## Recommendations for v0.5 generic layer

1. Reuse the `_build_kv_sections` flat-line key/value + pseudo-header walker — this is now the third adapter (after pku.wangxuan and buaa.iai) that uses essentially the same trick on different label sets.
2. Generalise the "card with photo + labelled `<div>` lines" list pattern — cc.nankai, cyber.nankai, sw.nankai and several SJTU/SYSU pages all match this with minor selector variation.
3. The free-form-prose research-extraction regex (`研究(?:方向|兴趣|领域)[为是:：]?\s*(...)`) bounded by clause-end punctuation is a reusable utility — promote it to `core/parser_utils.py` if any other adapter needs it.
