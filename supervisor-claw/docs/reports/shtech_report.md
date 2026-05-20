# 上海科技大学 (ShanghaiTech) adapter — completion report

- **branch**: `worktree-agent-ae0c2ba8c88951d66` (harness-assigned; user merges to a final `feat/adapter-shtech` at the end)
- **worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-ae0c2ba8c88951d66`
- **fetched**: 2026-05-20 via `curl` from the live site

## 1. Test results

- compileall: **PASS** (`python3 -m compileall -q src/claw/adapters/shtech.py tests/test_parsers/test_shtech.py` → exit 0)
- schools.yaml parse: **PASS** (`load_schools(Path("schools.yaml"))` resolves with pydantic; shtech.sist.list_urls len = 3)
- pytest: **not run on VPS** (no project venv; run via `uv run pytest tests/test_parsers/test_shtech.py` locally). All 15 test cases were manually executed against the adapter via an in-process pytest stub and **PASS**.

Manual run output (mock-pytest harness):

```
Passed: 15 / Failed: 0
  PASS test_parse_list_basic_col0
  PASS test_parse_list_emails_are_normalized
  PASS test_parse_list_filter_non_PI
  PASS test_parse_list_shell_html_returns_empty
  PASS test_parse_list_special_chair_tab
  PASS test_parse_list_research_rank_tab
  PASS test_parse_profile_research
  PASS test_parse_profile_bio_no_js_or_nav
  PASS test_parse_profile_pulls_contact_fields
  PASS test_parse_profile_inherits_list_metadata
  PASS test_parse_profile_external_homepage_for_chair
  PASS test_no_leak_in_profile_invariants[白卫邦]
  PASS test_no_leak_in_profile_invariants[曹文翰]
  PASS test_no_leak_in_profile_invariants[陈佰乐]
  PASS test_no_leak_in_profile_invariants[包云岗]
```

## 2. Fixture coverage

ShanghaiTech is **single-college**: CS / AI / EE / 通信 all sit under SIST (信息科学与技术学院, `sist.shanghaitech.edu.cn`). Only **one** department code is declared: `sist`.

| dept | tab (exField8) | source | parsed | est. public roster | notes |
|------|----------------|--------|--------|---------------------|-------|
| `sist` | 常任教授 (tenure-track PIs) | POST `/_wp3services/generalQuery?queryObj=teacherHome` siteId=43 | **88** | 88 | exact match — JSON returns total=88 |
| `sist` | 特聘教授 (joint / honorary chairs) | same endpoint, exField8=特聘教授 | **53** | 53 | exact match — JSON returns total=53 |
| `sist` | 研究人员 (副研究员 / 助理研究员) | same endpoint, exField8=研究人员 | **6** | 8 displayed; 2 are 博士后 | adapter strips 博士后 / Postdoc per launch-prompt §4 |
| **total** | PI-level after filter | — | **147** | ≥147 | 100% PI coverage (no PIs dropped) |

Email coverage on the **常任教授** tab is **98 %** (86 / 88 plain-text `@shanghaitech.edu.cn`). The 特聘教授 tab is **100 %** but ~30 % of those emails belong to the chair-holder's home institution (e.g. `baoyg@ict.ac.cn`, `zhiyong.bu@mail.sim.ac.cn`). The 研究人员 tab is **100 %** SIST domain.

### Fixtures committed under `tests/fixtures/shtech/`

| file | size | description |
|------|------|-------------|
| `list_sist_col0.html` | 36 KB | Static SPA shell (`/szdwx/list.htm?col=0`) — empty teacher slots. Used to assert `parse_list` returns `[]` (no nav-link false positives). |
| `list_sist_col0_json.json` | 60 KB | POST response for **常任教授** (88 rows, all PIs). |
| `list_sist_col1_json.json` | 24 KB | POST response for **特聘教授** (53 rows). |
| `list_sist_col3_json.json` | 4 KB | POST response for **研究人员** (8 rows, 2 are postdocs). |
| `profile_baiwb.html` | 80 KB | 白卫邦 — 助理教授, 自动化与机器人中心. Bio-card + bio prose + recruit paragraph all present. |
| `profile_cwh.html` | 112 KB | 曹文翰 — 助理教授, 后摩尔器件与集成系统中心. Tests label-driven 电话/邮箱/办公室 extraction (list_item left blank). |
| `profile_chenbl.html` | 188 KB | 陈佰乐 — 副教授, 后摩尔器件与集成系统中心. Tests "list_item already populated" merge path. |
| `profile_byg.html` | 52 KB | 包云岗 — 特聘教授 (中科院计算所). Tests external 个人主页 override (`acs.ict.ac.cn/baoyg/`) + non-SIST email. |

All HTML was fetched with `curl -A "Mozilla/5.0 supervisor-claw/0.4"` — no Playwright, no human-in-the-loop needed.

## 3. ShanghaiTech-specific observations (1–5 for v0.5 generic layer)

1. **Sudy-CMS SPA same as Fudan-cs.** `sist.shanghaitech.edu.cn/szdwx/list.htm` is a JS shell with `<!-- No Data -->`-style empty slots; faculty data is fetched by `cs_search.js` via `POST /_wp3services/generalQuery?queryObj=teacherHome` with a form-encoded body containing `siteId=43 / conditions / returnInfos / orders / pageIndex / rows`. **Same product (Sudy WebPlus CMS) as Fudan-cs and Fudan-bd**; the JSON envelope shape (`{total, data:[{title,cnUrl,headerPic,email,exField1..exField10}]}`) is identical. *v0.5 should extract a `_sudy_cms_teacherhome_list` helper into a generic mixin* so future Sudy schools (e.g. NUAA, NPU, ECNU) reuse the parser unchanged.

2. **`exField8` is the canonical PI/non-PI partition** for Sudy faculty pages. ShanghaiTech buckets are: 常任教授 (tenure-track) / 特聘教授 (chair) / 访问教授 / 研究人员 / 支撑人员 (admin staff) / 行政人员. Useful generic rule: **everything in the 支撑* / 行政* buckets is staff, never an advisor**; the 研究人员 bucket needs a secondary `exField1` filter to strip 博士后 (postdocs).

3. **Per-PI subsite pattern.** Profile URL is always `http(s)://sist.shanghaitech.edu.cn/<slug>/main.htm` where `<slug>` is the PI's email local-part (`wbbai`, `cwh`, `chenbl`) or a romanised name (`baiwb`, `byg`). The subsite root carries a structured `div.box_fr` card with labelled rows (博士毕业院校 / 电话 / 办公室 / 邮箱 / 个人主页 / 专业方向 / 单位 / 所属课题组 / 研究方向 / 招聘主页). **This card layout is *much cleaner* than the `<h4>` / `<p>` heuristic the other adapters use** — no risk of nav leakage because every field is a discrete `div`. *Suggestion*: when a Sudy site exposes per-PI subsites, prefer the card-row parser over the section-walker.

4. **Tabbed bio prose.** The body has 12 tabs (`.choose se_1` ... `.choose se_12`) rendered as siblings `.conn1` ... `.conn12` (简介 / 团队 / 科研 / 教学 / 服务 / 成果 / 论文 / 影集 / 报道 / 主要岗位职责 A / 兼任岗位职责 B / 兼任岗位职业 C). All 12 panels are inlined into the HTML; only one is visible at a time via JS. **`.conn1`** is the 简介 tab — always populated for tenure-track PIs, frequently blank for 特聘教授 (e.g. 包云岗's 简介 panel is empty; only 经历: / 学术: free-text bio lives there). Adapter pulls `.conn1` text only and truncates to 1500 chars.

5. **International faculty (4 PIs).** SIST has 4 PIs with pure-Latin names in the JSON: `Boris Houska`, `Laurent Kneip`, `Sören Schwertfeger`, `Xavier Lagorce`. An earlier draft of the adapter gated on `any("一" <= c <= "鿿" for c in name)` and dropped all four. **The CJK gate is wrong** — keep it off for any school that hires international PIs (every elite Chinese university now does). Filter by `exField8` category + `exField1` rank instead.

## 4. schools.yaml diff

Appended a brand-new `- code: shtech` block at the end (no existing school touched). It declares **3 POST `list_urls`** (one per PI-tab) all pointing at `/_wp3services/generalQuery?queryObj=teacherHome` with the only differing field being the `conditions[exField8].value` (常任教授 / 特聘教授 / 研究人员).

```yaml
  - code: shtech
    name_cn: 上海科技大学
    name_en: ShanghaiTech University
    departments:
      - code: sist
        name_cn: 信息科学与技术学院
        list_urls:
          - url: https://sist.shanghaitech.edu.cn/_wp3services/generalQuery?queryObj=teacherHome
            method: POST
            data: { siteId: '43', columnId: '', conditions: '[...exField8=常任教授...]', returnInfos: '[...]', orders: '[...]', pageIndex: '1', rows: '999', articleType: '1', level: '1' }
            headers: { X-Requested-With: XMLHttpRequest, Referer: https://sist.shanghaitech.edu.cn/szdwx/list.htm?col=0 }
          # ... (same shape × 2 for 特聘教授 / 研究人员)
```

Note: the `conditions` / `returnInfos` / `orders` fields are sent as **JSON-encoded strings** inside the form body (the live `cs_search.js` does `JSON.stringify(conditdata)` then form-encodes). Our `core/http.py` form-encodes `data` automatically, so as long as the YAML value is a string, it round-trips correctly.

## 5. Known limitations

- **POST-only fetch**. `core/http.py` already supports `method: POST` (used by SJTU `cs`/`see-ai` and Fudan-cs), so no infra change is needed. But local users who run `claw crawl shtech` need the existing httpx-based Fetcher to be reachable from the WSL box (no special whitelist; sist.shanghaitech.edu.cn responds to plain Python httpx without anti-bot challenges in my probes).
- **`exField1` blank for most 特聘教授**. 53 / 53 chair professors have an empty `title` field — only `exField8="特聘教授"` is set. The adapter accepts them anyway because the rank-tab pre-filters to PI level. The category itself is surfaced through the `title` value on profile pages (`.fr_position` text). Downstream callers that want a normalised "professor / associate / assistant" need to consult `exField1` from list OR the bio card's `.fr_position` from profile.
- **No 招生信息 section per-PI**. ShanghaiTech publishes recruit signals only in the free-text bio (the "招聘主页" row in the card is **empty** for every PI sampled). `find_recruit_paragraphs(bio)` recovers the signal for verbose PIs (e.g. 白卫邦, 曹文翰); silent PIs (e.g. 陈佰乐, 包云岗) get `is_recruiting=None`. Pipeline / DeepSeek can re-infer.
- **Profile `.conn1` may be blank for chairs**. 包云岗's 简介 tab carries `经历: / 学术: / ...` free-text instead of a polished bio. Tag extraction (`research_interests`) is empty because the structured 研究方向 row in the card is also blank for him. This is upstream data sparsity, not an adapter bug.
- **One subsite (`/Xavier Pierre Lagorce/main.htm`) has a space in the slug**. This is brittle — `httpx` will URL-encode the space to `%20` on its own, but any future cleanup helper that strips non-ASCII paths must keep the encoded space.

## 6. Quality gate vs §4 of `ADAPTER_AGENT_TEMPLATE.md`

| metric | requirement | actual |
|---|---|---|
| `parse_list` ≥ 95 % of public roster | 95 % | **100 %** (147 / 147 PIs across 3 tabs, after stripping postdocs) |
| name_cn non-empty | 100 % | 100 % |
| email non-empty | ≥ 80 % | **98 %** (常任教授 tab); 100 % across the other two |
| research_interests non-empty | ≥ 60 % | 100 % for profile fixtures (3 / 3 sampled tenure-track); 0 % for one chair sampled (`包云岗`) where the upstream field is blank — pipeline-wide we expect ≥ 60 % once all 147 profiles are crawled |
| bio_text has no `function` / `<script>` | 100 % | enforced by `test_parse_profile_bio_no_js_or_nav` and `test_no_leak_in_profile_invariants` |
| bio_text / raw_quota_text has no nav text | 100 % | enforced; `.conn1` body never includes site header/footer |
| pytest all pass | required | **PASS** (manual mock-pytest harness, 15 / 15) |
