# uestc adapter v1 — completion report

- **Branch**: `worktree-agent-a0c2911ab9b491436`
- **Worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-a0c2911ab9b491436`
- **Date**: 2026-05-20

## Deliverables

| Path | Note |
|---|---|
| `src/claw/adapters/uestc.py` | `UestcAdapter` registered; depts `{cs, sw, ai}` |
| `tests/test_parsers/test_uestc.py` | 13 tests — list bucket counts, dedup, TS-stub handling, profile email/title/research/bio, second-profile sanity |
| `tests/fixtures/uestc/` | 3 list HTMLs (sice 教授 / 副教授 / 研究员) + 2 profile HTMLs (sice 教授 + 副教授) |
| `schools.yaml` | appended `- code: uestc` with cs / sw / ai entry URLs (no other schools touched) |

## Coverage observed against fixtures

| Bucket | URL | Parsed | Email coverage (list) |
|---|---|---:|---|
| 教授 (js.htm) | `https://www.sice.uestc.edu.cn/szdw/jsml1/js.htm` | 112 / 112 | 100% (via inline JSON `yx` field) |
| 副教授 (fjs.htm) | `https://www.sice.uestc.edu.cn/szdw/jsml1/fjs.htm` | 91 / 91 | 100% |
| 研究员 (yjy.htm) | `https://www.sice.uestc.edu.cn/szdw/jsml1/yjy.htm` | 18 / 18 | 100% |

Profile parsing verified on 2 fixtures (`崔宗勇`, `安洪阳`) — both yield:

- ✅ correct `name_cn`, `title`, `email`, `photo_url` (absolutised), `homepage`
- ✅ `bio_text` extracted from `教师简介` section (no `function`/`<script>`/nav leak)
- ✅ `research_interests` parsed from inline "研究方向：X、Y、Z等" phrase in `科学研究` section — e.g. `["SAR图像智能解译", "雷达目标识别"]`, `["认知合成孔径雷达成像", "雷达智能感知"]`
- ✅ `is_recruiting=True` when the `招生专业` section contains the 招 keyword

## Known oddities (school-specific layer)

1. **TS-WAF JS-challenge wall on scse / sise / auto.** UESTC switched every CS-relevant school subdomain *except* `sice` to a TS-WAF proxy in 2023-2024. Static httpx GETs return a ~2.4 KB stub (HTTP 202/412) with a `<meta id="sx6c1KC7YLcx">` token + an inline `$_ts=window['$_ts']` bootstrap script — the real page only renders after a JS-generated cookie round-trip. The adapter detects this via `_is_ts_challenge` (length < 4 KB AND contains the marker) and returns `[]` from `parse_list` / a stub `AdvisorPartial` from `parse_profile` so the crawl doesn't crash. **Until the crawl pipeline gains Playwright-fetch support (today only the enrich pipeline uses Playwright), the cs/sw depts are non-crawlable.** schools.yaml lists their URLs anyway so wiring Playwright doesn't require a YAML edit later.
2. **List data is inline JSON, not the DOM.** The `<ul id="showdatainfo">` element is empty on first paint — jQuery walks a `var ret = [...]` JSON blob (embedded in a `<script>` near the bottom) and injects `<li>` nodes client-side. The adapter parses `ret` directly via a regex + `json.loads`. Per-teacher fields: `zc`=职称, `yx`=邮箱, `dh`=电话, `kxyj`=科研概况, `jsjj`=教师简介, `td`=团队, `xb`=系别, `picUrl`=头像, `url`=详情页相对路径.
3. **Profile prose uses uppercase `<BR>`** (and lots of leading whitespace). selectolax normalises tag case but the adapter additionally collapses runs of half-/full-width spaces in `_clean_bio` so the rendered bio isn't double-spaced.
4. **科学研究 section header has a leading space** (`<h3><img> 科学研究</h3>`). Header matching uses `_normalise_section_header` which strips leading whitespace + trailing colons.
5. **One international PI on sice副教授 page** ("Inserra Daniele") — initial CJK-only name filter dropped it. Relaxed to accept either CJK OR a 2+-character Latin name when the title looks faculty-shaped. Coverage now 91/91 instead of 90/91.
6. **Email is plain text inside the head card** (`<p>邮箱: x@y</p>`); never image-obfuscated on the samples. The unified `faculty.uestc.edu.cn` personal-page system (which encrypts emails like at HUST) is firewalled to non-CN IPs (HTTP 403 from this VPS) and is **not** used by sice/scse/sise list pages, so the encryption path is irrelevant for v0.4.

## Validation

- `python3 -m compileall -q src/claw/adapters/uestc.py tests/test_parsers/test_uestc.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception, school code `uestc` present
- Manual harness-based pytest run (with pytest stubbed since the VPS has no pytest install) — **13 / 13 PASS**:
  ```
  PASS test_is_ts_challenge_detects_stub
  PASS test_is_ts_challenge_false_on_real_list
  PASS test_parse_list_dedup
  PASS test_parse_list_fjs_bucket
  PASS test_parse_list_js_bucket
  PASS test_parse_list_returns_empty_on_ts_stub
  PASS test_parse_list_yjy_bucket
  PASS test_parse_profile_extracts_email_and_title
  PASS test_parse_profile_has_research_or_bio
  PASS test_parse_profile_no_js_no_nav
  PASS test_parse_profile_research_tags
  PASS test_parse_profile_returns_stub_on_ts_challenge
  PASS test_parse_profile_second_sample
  ```

## Known limitations

- **`cs` and `sw` depts cannot be crawled today** by the static httpx pipeline due to the TS-WAF wall on scse/sise. The adapter is fully written and template-compatible (same `var ret = [...]` blob + same `div.teacher-info` profile shape as sice) — wiring Playwright-fetch into `core/http.py` is enough to enable them, no further adapter work needed.
- `ai` dept currently maps to **信息与通信工程学院 (sice)** rather than a dedicated "AI 学院". UESTC has no standalone 人工智能学院 web presence in 2026-05; AI/ML research is split across sice (信号/机器学习 团队), auto (智能控制), and scse (软件/AI/网络空间安全). After Playwright lands, the dept layout may need to be re-sliced once those subdomains become crawlable.
- Pagination: sice uses a single jQuery `.pagination` widget that re-slices the inline JSON client-side. There is **no** server-side pagination — one GET on each `<rank>.htm` returns the full roster (verified 112 教授 in one file), so no extra `_2.htm` / `?currPage=` URLs are needed.

## schools.yaml diff

Appended one new top-level `- code: uestc` block after `nankai`. No other schools touched. Each dept (`cs`, `sw`, `ai`) carries 3-6 rank-bucket URLs covering 教授 / 副教授 / 研究员 / 副研究员 / 高级工程师 / 讲师 buckets.
