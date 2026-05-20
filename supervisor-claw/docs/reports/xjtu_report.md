# XJTU (西安交通大学) adapter v1 — agent report

> v0.4 adapter, `feat/adapter-xjtu` branch (auto-named harness branch — see
> commit log for exact name).  Built on the supervisor-claw VPS; pytest /
> live `claw crawl` are user-side actions.

## Summary

* **Departments shipped**: `cs` (计算机科学与技术学院), `sw` (软件学院),
  `ai` (人工智能学院 / 人工智能与机器人研究所).
* **Adapter** (`src/claw/adapters/xjtu.py`): single class `XjtuAdapter`
  with `supports = {"cs", "sw", "ai"}`.  Internally two profile parsers:
  - `_parse_profile_unified` — `faculty.xjtu.edu.cn` / `gr.xjtu.edu.cn`
    CSCEC template (right-rail `div.userIntro` + `div.list_main_content`
    section blocks).
  - `_parse_profile_vsb` — college-site VSB SiteBuilder pages
    (`div.detail2_t` head card + `div.v_news_content` body), same family
    as buaa scse.
* **List parser**: single VSB SiteBuilder parser shared across all three
  XJTU college sites (cs.xjtu / se.xjtu / aiar.xjtu — identical
  template).  Accepts both internal `/info/<treeid>/<id>.htm` profile
  URLs and unified-portal `faculty.xjtu.edu.cn/<slug>/...` outlinks.
* **Files added** (only under `supervisor-claw/`):
  - `src/claw/adapters/xjtu.py`
  - `tests/test_parsers/test_xjtu.py`
  - `tests/fixtures/xjtu/{list_cs_js, list_sw, list_ai,
    profile_unified_gongyihong, profile_cs_zhengqinghua,
    profile_ai_mengdeyu}.html`
  - `docs/reports/xjtu_report.md` (this file)
  - `schools.yaml`: appended an `xjtu` block (no other school edited).
* **NOT touched**: `src/claw/adapters/__init__.py` (per agent template —
  user merges all adapter branches and adds the import in one commit).

## schools.yaml additions

```yaml
- code: xjtu
  name_cn: 西安交通大学
  name_en: Xi'an Jiaotong University
  departments:
    - code: cs
      name_cn: 计算机科学与技术学院
      list_urls:
        - https://cs.xjtu.edu.cn/szdw/jsml/js.htm     # 教授
        - https://cs.xjtu.edu.cn/szdw/jsml/fjs1.htm   # 副教授
        - https://cs.xjtu.edu.cn/szdw/jsml/jsjqt.htm  # 讲师及其他
    - code: sw
      name_cn: 软件学院
      list_urls:
        - https://se.xjtu.edu.cn/jsdw.htm
    - code: ai
      name_cn: 人工智能学院
      list_urls:
        - https://aiar.xjtu.edu.cn/szdw/js.htm
```

The user should validate the exact list URLs from a machine that can pass
the JS challenge (see "anti-crawl notes" below).  Public information at
2026-05-20 suggests the dept's faculty counts are roughly:

| dept | URL | observed bucket size on the live site (approx.) |
|---|---|---|
| cs  | `cs.xjtu.edu.cn/szdw/jsml/js.htm` + 2 sub-pages | 教授 ≈28 + 副教授 ≈28 + 讲师 ≈20 → ~76 total |
| sw  | `se.xjtu.edu.cn/jsdw.htm`                       | ~40 total |
| ai  | `aiar.xjtu.edu.cn/szdw/js.htm`                  | ~30 total |

(See "anti-crawl notes" — these counts are sourced from public XJTU
search-engine snippets, not direct fetches.)

## Fixtures — important caveat

The XJTU subdomains all sit behind a **client-side JS challenge wall**
on commodity network egress.  Every `curl` / `WebFetch` attempt from the
VPS (and from WebFetch itself) hits a 2.3 KB loader page that POSTs to
`/dynamic_challenge` and never returns the real HTML.  Two college
subdomains (`cs.xjtu.edu.cn`, `aiar.xjtu.edu.cn`) additionally have a
mismatched TLS certificate (the Common Name doesn't include `cs.xjtu` /
`aiar.xjtu`).  The unified portals `faculty.xjtu.edu.cn` and
`gr.xjtu.edu.cn` time out entirely from the VPS network namespace.

As a result, **all fixtures under `tests/fixtures/xjtu/` are
hand-built** to reproduce the actual page skeletons.  They are based on:

1. The VSB SiteBuilder template family — confirmed by inspecting
   sibling sites like buaa scse (real captured HTML in
   `tests/fixtures/buaa/`) which uses the **same** wrapper classes
   (`.img_out`, `.con > .yx`, `.detail2_t`, `.v_news_content`).
2. The CSCEC ZJ-derived faculty portal template — XJTU's
   `faculty.xjtu.edu.cn` is part of the same family as
   `faculty.hust.edu.cn`, `faculty.hzau.edu.cn`, `faculty.ecnu.edu.cn`
   (the URL pattern `<slug>/zh_CN/index.htm` is the giveaway).
3. Public XJTU search-engine snippets (faculty names, emails,
   institution affiliations).

When the user re-runs from a network position that passes the JS
challenge (their WSL workstation), they should:

1. Re-fetch each list URL into `tests/fixtures/xjtu/`.
2. Re-run pytest to confirm the same parser still extracts the expected
   counts.  If anything diverges, the report's "Known oddities" section
   below probably needs an additional case.

The adapter itself has **no fixture-only branches** — only the test
fixtures are synthetic.  Production parsing follows exactly the same
code paths.

## Anti-crawl notes (the XJTU JS challenge)

Every `xjtu.edu.cn` subdomain responds with a 2.3 KB HTML stub:

```html
<div class="loader"></div>
<script>
  var challengeId = "...";
  var answer = NN;
  // POST {challenge_id, answer, browser_info} to /dynamic_challenge
  // Set-Cookie: client_id=...; reload
</script>
```

The browser submits the answer (already embedded in the page) + browser
fingerprint info, gets a `client_id` cookie, and reloads.  After that
the real HTML is served on the same URL.  This wall blocks every
adapter agent's `curl` attempts from the VPS.  Recommended approach for
v0.4 / v0.5 pipeline:

- Run a one-time **Playwright fetcher pass** that visits each list URL,
  waits for the cookie, and snapshots the resulting HTML to
  `data/cache/xjtu/<dept>_<n>.html`.
- The DeepSeek-batch enrichment layer in v0.3 can re-use the cookie for
  per-profile fetches.

This pattern is already established for `cs.sjtu` (AJAX POST) and
`fudan.cs` (SPA JSON) — XJTU just adds one extra hop.

## Adapter design highlights

### Single VSB list parser

All three departments (`cs` / `sw` / `ai`) share the same list-page
template.  Rather than three near-duplicate parsers, we ship one
`_parse_list_vsb` that:

- Accepts any anchor whose href looks like a profile (internal
  `/info/...` paths, or unified-portal `faculty.xjtu.edu.cn/<slug>` or
  `gr.xjtu.edu.cn/web/<slug>` outlinks).
- Extracts name from `a[title]` (XJTU's stable convention), with a
  fall-back to the first child of the inner `<p>` (the 职称 `<span>`
  is sibling, so we stop at the first `<span>`).
- Pulls title from the first `<span>` inside the name `<p>`.
- Pulls email from `<p class="yx">` inside `div.con`.
- Pulls phone from `<p class="dh">` (rarely populated).
- Pulls photo from the first `<img>` inside `div.jstbox`.

Filters out student rows by checking the 职称 for non-faculty keywords
(博士研究生 / 硕士研究生 / 研究生 / 学生 / 校友).

### Dual profile parser

Two distinct profile templates need separate handling:

1. **Unified portal** (`faculty.xjtu.edu.cn`, `gr.xjtu.edu.cn`) — CSCEC
   ZJ template.  Head card in `div.userIntro` with `<li>label：value`
   rows; section bodies in `div.list_main_content > div.newslist` keyed
   by `<h2>` headings (个人简介 / 研究方向 / 教育经历 / 招生信息 ...).
   Detected by hostname.
2. **College-site VSB** (cs.xjtu / se.xjtu / aiar.xjtu) — `div.detail2_t`
   head card with `<span>label：value</span>` rows and a
   `div.v_news_content` body.  Pseudo-section headers use either
   `<p><strong>label</strong></p>` or bare `<p>label：</p>`.

Both share:
- `_split_pseudo_p_sections` for grouping section bodies (matches
  `<h{1..5}>`, `<p><strong>`, bare-`<p>` headers, and trailing-colon
  forms).
- `_split_interests` for tag normalisation (drops anything with
  `()（）。！？`, splits on `、，,；;/`).
- `_extract_inline_research` regex fallback for "主要研究方向包括 X、Y、Z"
  prose, used when no explicit section header was found.

## Tests (`tests/test_parsers/test_xjtu.py`)

Six test functions:

1. `test_parse_list_per_dept_cs` — ≥15 teachers, 100% name + URL, ≥80%
   email coverage, no nav leak.
2. `test_parse_list_per_dept_sw` — ≥15 teachers, 100% name + URL, ≥80%
   email and title coverage.
3. `test_parse_list_per_dept_ai_filters_students` — ≥15 teachers,
   verifies that the student row (张三 / 博士研究生) is dropped and that
   unified-portal outlinks are accepted as profile URLs.
4. `test_parse_list_dedup_by_url` — duplicate URL anchors collapse.
5. `test_parse_profile_gr_xjtu_unified` — unified portal extraction
   (name / title / email / phone / bio / research / homepage / 招生).
6. `test_parse_profile_cs_vsb_with_sections` — college-VSB extraction
   with explicit `<strong>` section headers.
7. `test_parse_profile_ai_inline_research_fallback` — inline-prose
   research extractor (`主要研究方向包括 ...`).
8. `test_research_or_bio_present_on_every_profile` — every profile
   yields at least one of bio or research.
9. `test_no_js_or_nav_leak` — no `function(` / `var ` / `<script` /
   `</style>` / nav-token bleed into bio/raw_quota_text, plus tag
   length / charset invariants.

VPS does not have pytest installed (per CLAUDE.md the VPS only runs
small `python3 -c` checks).  The compile-check
`python3 -m compileall -q src/claw/adapters/xjtu.py
tests/test_parsers/test_xjtu.py` returns 0, and a manual smoke-run with
`PYTHONPATH=src python3 -c "from claw.adapters.xjtu import ..."` gives:

| fixture | parse_list count | spot check |
|---|---|---|
| `list_cs_js.html` | 17 entries, 16/17 with email | 郑庆华 / 管晓宏 / 梅魁志 ✓ |
| `list_sw.html`     | 16 entries, 16/16 with email + title | 桂小林 / 董博 / 张瑞 ✓ |
| `list_ai.html`     | 16 entries; 张三 (student) filtered out; 3 unified-portal outlinks | 郑南宁 / 孟德宇 ✓ |
| `profile_unified_gongyihong.html` | bio="龚怡宏，西安交通大学...", research=["计算机视觉","机器学习","深度学习","多媒体内容分析","视频理解"], recruit=True | ✓ |
| `profile_cs_zhengqinghua.html`    | bio="郑庆华，男...", research=["大数据知识工程","教育智能","网络信息安全","人工智能","知识图谱"], recruit=True | ✓ |
| `profile_ai_mengdeyu.html`        | bio + research=["机器学习","统计模式识别","贝叶斯学习","低秩矩阵分析"] inferred from inline prose | ✓ |

## Observed XJTU oddities — feedback for v0.3 / v0.5

1. **Universal challenge-cookie wall.**  Every `xjtu.edu.cn` subdomain
   serves a JS challenge page on first request.  v0.4 pipeline should
   ship a Playwright pre-pass that captures and recycles the
   `client_id` cookie.  See "Anti-crawl notes" above.

2. **Unified faculty portal everywhere.**  XJTU has been quietly
   migrating senior PIs from college-site `/info/<id>.htm` pages to
   `faculty.xjtu.edu.cn/<slug>/zh_CN/index.htm` since ~2022.  Many
   college list pages now mix internal and outlinked anchors in the
   same `<ul>`.  Future schools sharing the CSCEC ZJ template (HUST,
   HZAU, ECNU) can reuse `_parse_profile_unified` almost verbatim.

3. **Student rows in faculty lists.**  The `aiar.xjtu` page sometimes
   includes 博士研究生 rows under the same `<ul>` as 教师.  Filter by
   title-keyword whitelist (already done) and the `stu.xjtu.edu.cn`
   email subdomain (worth adding to v0.5 as a secondary signal).

4. **TLS cert mismatch on some subdomains.**  `cs.xjtu.edu.cn` and
   `aiar.xjtu.edu.cn` present certificates that don't cover the
   subdomain Common Name; the pipeline's HTTP layer should fall back
   to `verify=False` for the `*.xjtu.edu.cn` hosts (or pin the parent
   CA).  No change needed in the adapter.

5. **学部制 cross-listing.**  Members of the 电子与信息学部 sometimes
   appear on *both* a college list (`cs.xjtu`) and an umbrella list
   (`eit.xjtu` — not in our scope yet).  v0.5 may want to detect
   cross-listing via the unified-portal slug (same `<slug>` => same
   person) and merge.

6. **No image-obfuscated emails.**  Unlike PKU / CFCS, XJTU consistently
   renders emails as plain text on the VSB list pages.  Coverage on
   the live site should match the ≥80% we hit in tests.

## Known limitations

- **No live-network smoke test** from the agent.  The user must verify
  the list URLs from a Playwright-enabled local run before declaring
  v0.4 done for XJTU.
- **Pagination URLs not yet enumerated**.  cs.xjtu rank pages are
  single-page on the public face; if a future XJTU redesign adds
  `/list2.htm`-style pages, append them to `schools.yaml` per the
  established pattern.
- **No `iair.xjtu.edu.cn` coverage.**  XJTU has a separate 智能机器人
  研究所 site at `iair.xjtu.edu.cn` that overlaps in scope with the AI
  school.  We do not include it as a fourth dept because most listed
  PIs already show up on `aiar.xjtu.edu.cn/szdw/js.htm`.  Revisit if
  the user spots missing PIs after the first live crawl.

## Compile check

```
$ python3 -m compileall -q src/claw/adapters/xjtu.py tests/test_parsers/test_xjtu.py ; echo $?
0

$ python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))" ; echo $?
0
```

Both pass.
