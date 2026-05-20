# supervisor-claw v0.4 — 西北工业大学 (NWPU) adapter report

## 1. Coverage summary

| Dept | Host | List URL | List parse count | Coverage |
|------|------|----------|------------------|----------|
| `cs`  | jsj.nwpu.edu.cn       | `/snew/szdw/szmd.htm`       | **174**         | full       |
| `sw`  | ruanjian.nwpu.edu.cn  | `/szdw/jsdw.htm`            | **45** (PI 仅)  | full       |
| `cse` | wlkjaqxy.nwpu.edu.cn  | `/jsfc.htm` (教师风采)      | **12**         | partial    |

Total list-level PIs: **~231**. Profile-level enrichment is currently
limited to the CSE jsfc article subset (~12) — see §3 for why.

## 2. Per-dept observations

### `cs` (计算机学院) — full coverage

* The 师资名单 page is a Word-exported table inside `div#vsb_content`.
  Faculty are grouped twice: first by 系 (6 sub-departments) and then
  by rank (`正高职称` / `副高职称` / `其他职称`).
* Group headers are bare `<strong>` tags; teacher anchors are plain
  `<a href="https://teacher.nwpu.edu.cn/...">姓名</a>` with no
  containing card markup.
* The adapter walks `div#vsb_content` in document order, tracking the
  current dept + rank context, and maps `正高 → 教授`, `副高 → 副教授`,
  leaving `其他职称` as `title=None` for the v0.3 enricher to resolve.
* Counts by sub-department (raw): 智能计算系统系 45, 计算机科学与软件系
  36, 计算机信息工程系 49, 网络与机器人系统系 19, 计算机基础教学与实验
  中心 22, 高性能中心 4 = 175 anchors; **1 orphan** anchor wrapping a
  `&nbsp;` glyph is dropped (no text), giving the final 174.

### `sw` (软件学院) — full coverage

* `ruanjian.nwpu.edu.cn` (not `xinst.nwpu.edu.cn`) is the active site.
  Cards are uniform `<a title=name><img><h3>name</h3><h3>title</h3></a>`.
* The second `<h3>` carries honour-prefixed titles (`国家级青年人才 教授`,
  `长聘副教授`, `准聘教授`, etc.) — we keep the full string verbatim.
* The list mixes ~45 faculty PIs with ~15 postdocs (`title=博士后`);
  the adapter filters postdocs at the list-page layer so the pipeline
  doesn't carry them downstream.

### `cse` (网络空间安全学院) — partial coverage, known-limited

* The expected `wlkjaqxy.nwpu.edu.cn/szdw.htm` is **404** (the site
  redirects to `/` after 5 s). `/szdw/szgk.htm` is a brochure page
  with no individual teacher pages.
* The only faculty-bearing surface is `/jsfc.htm` ("教师风采"), an
  ~17-link list of `info/1090/<id>.htm` narrative articles. We parse
  the anchor text — e.g. `"...网络空间安全学院毛伯敏教授"` — via regex
  to recover (name, title); we accept that this is a tiny subset
  (~12 PIs vs an estimated 30+ true CSE faculty).
* CSE profile pages (`info/1090/*.htm`) are narrative profile pieces;
  we surface them as `bio_text` (capped 1500 chars). No emails are
  ever exposed.

### 国防访问限制 — TS-WAF wall on teacher.nwpu.edu.cn

* The canonical per-teacher portal `teacher.nwpu.edu.cn/<slug>.html`
  is behind a TS-WAF JS-challenge wall: every static GET returns
  HTTP 412 + a ~2 KB stub of the form

  ```html
  <meta id="hK5iNqnNcwxO" content="..." r='m'>
  <script>$_ts=window['$_ts']; ...; $_ts.cd="qx2J...";</script>
  <script src="/jcGbaIA7dRsZ/eU4pnslpsr1i.<hash>.js" r='m'></script>
  ```

  The adapter detects the stub markers (`$_ts=window['$_ts']`,
  `_$ep()`, `id="hK5iNqnNcwxO"`) and returns list-item data verbatim
  rather than scraping JS challenge code.
* **Implication**: ~219/231 list-level PIs (everything outside CSE
  jsfc) carry name + title + photo only; bio / email / research
  enrichment requires either Playwright TS-WAF bypass in v0.5 or the
  v0.3 DeepSeek enricher acting on bare names.

## 3. Known limitations

| # | Limitation | Status | Resolution path |
|---|------------|--------|------------------|
| 1 | `teacher.nwpu.edu.cn` is TS-WAF-walled | All CS/SW profiles return empty bio | v0.5 Playwright fetch |
| 2 | CSE has no real faculty directory | Only ~12/30+ PIs visible via jsfc | v0.5 Playwright + CSE student council site |
| 3 | No emails on any list page | Pipeline lacks contact info | v0.3 DeepSeek enricher or v0.5 portal scrape |
| 4 | "其他职称" CS bucket has no rank info | `title=None` for ~30 PIs | v0.3 enricher infers from bio/keywords |

## 4. Special structural notes (for v0.3 generic layer)

1. **NWPU centralises teacher pages under one TS-WAF domain.** Several
   "国防七子" peers will use the same pattern — we recommend a generic
   `_is_ts_waf_stub(html)` helper in `core/parser_utils.py` so each
   adapter doesn't redefine the markers.
2. **Word-exported VSB content blobs.** The `jsj.nwpu.edu.cn` list page
   uses `<strong>` instead of `<h3>/<h4>` for both dept and rank
   headers, with no per-card container — only a flat sequence of
   anchors. A traverse-in-document-order parse with sticky context
   pointers is the right primitive (also used by clean buaa/sjtu
   layouts; worth promoting to a util in v0.3).
3. **Anchor text noun-marker regex.** For sites where individual
   teacher pages don't exist (CSE jsfc), we extract `(name, title)`
   from anchor text by anchoring on a preceding noun marker
   (`学院` / `个人` / `学者` / `记`) plus a longest-match title
   alternation. Worth lifting to a shared util once a second school
   needs it.

## 5. `schools.yaml` changes

Appended a new `nwpu` block (3 departments, 3 list URLs) at the end of
the file. Two URLs that are currently unreachable are listed as
commented-out placeholders so they're discoverable when v0.5 lands:

* `wlkjaqxy.nwpu.edu.cn/szdw.htm` — 404 redirect
* `teacher.nwpu.edu.cn/<id>` — TS-WAF JS challenge

## 6. Fixtures shipped

```
tests/fixtures/nwpu/
├── list_cs_szmd.html             # 336 KB, 175 teacher anchors
├── list_sw_jsdw.html             # 66 KB,  60 cards (45 PI + 15 postdoc)
├── list_cse_jsfc.html            # 19 KB,  17 jsfc articles
├── profile_cse_maobomin.html     # 20 KB,  narrative profile sample
└── profile_guobin.html           # 2.3 KB, TS-WAF challenge stub
```

## 7. Tests (6 cases — all pass under manual `python3` run)

```
PASS  test_parse_list_cs_recovers_most_faculty
PASS  test_parse_list_sw_returns_faculty_only
PASS  test_parse_list_cse_partial_coverage
PASS  test_parse_profile_ts_waf_stub_is_safe
PASS  test_parse_profile_cse_jsfc_extracts_bio
PASS  test_parse_profile_falls_back_for_unknown_host
```

Tests were run on the VPS with `pytest` mocked (project deps aren't
installed locally); the user should re-run `uv run pytest
tests/test_parsers/test_nwpu.py -q` on their box to confirm.
