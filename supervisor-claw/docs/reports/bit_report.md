# bit adapter v1 — completion report

- **Branch**: `worktree-agent-a98cede57460eb052`
- **Worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-a98cede57460eb052`
- **Date**: 2026-05-20

## Deliverables

| Path | Note |
|---|---|
| `src/claw/adapters/bit.py` | `BitAdapter` registered; depts `{cs, sw, ai, cse}` |
| `tests/test_parsers/test_bit.py` | 4 per-dept list tests + 1 dedup test + 4 profile-nav tests + 4 parametrized research-or-bio tests = 13 cases |
| `tests/fixtures/bit/` | 6 list HTMLs (2 cs + 1 sw + 2 ai + 1 cse) + 4 profile HTMLs (1 per dept) |
| `schools.yaml` | appended `- code: bit` block with cs / sw / ai / cse entry URLs (no existing schools touched) |

## Departments resolved

The launch prompt asked for `{cs, sw, cse}`; on closer inspection BIT also publishes a standalone 人工智能学院 (`ai.bit.edu.cn`) which is the obvious "AI" target — included as `ai`. So we ship **4 depts** rather than 3.

| Dept | Site | Faculty entry |
|---|---|---|
| `cs`  | `cs.bit.edu.cn`  | `/szdw/jsml/bssds/index.htm` + `/szdw/jsml/sssds/index.htm` |
| `sw`  | `cs.bit.edu.cn`  | three software-flavored institutes under `/szdw/jsml2/<inst>2/index.htm` |
| `ai`  | `ai.bit.edu.cn`  | `/szdw/index.htm` + `/szdw/index1.htm` (paginated grid) |
| `cse` | `cst.bit.edu.cn` | `/szdw/jsml/index.htm` (one page, bssds + sssds inline) |

Note that the launch prompt mentioned `cse` host as `cse.bit.edu.cn` / `nis.bit.edu.cn`; the real host is **`cst.bit.edu.cn`** (网络空间安全学院 — "网络空间技术", not "网络空间安全"). The adapter routes both `cst.*` and `cse.*` to the same parser via `_dept_from_url`.

## Coverage observed against fixtures

| Dept | Fixture | Parsed | Notes |
|---|---|---|---|
| cs | `list_cs_bssds.html` | **76 PIs** | 博士生导师 grid (5 cards × ~16 rows) |
| cs | `list_cs_sssds.html` | **72 PIs** | 硕士生导师 — disjoint from bssds (different hex hashes & names) |
| cs aggregate | bssds + sssds | **148 unique** | aggregate clears the per-dept ≥ 15 bar |
| sw | `list_sw_rjznyrjgc.html` | 12 PIs | one institute; 11/12 overlap with cs bssds — pipeline dedupes |
| ai | `list_ai.html` + `list_ai_p2.html` | 21 + 8 = **29 PIs** | name + rank + photo all populated on every card |
| cse | `list_cse.html` | **46 PIs (bssds)** + 13 (sssds inline) | one HTML page holds both rank buckets |

Profile parsing verified on 4 fixtures — all yield bio (289-764 chars) and email; 4/4 surface research_interests via the interest splitter (CS uses `<h2>科研方向</h2>`, AI uses 学科方向 from head card, CSE uses `<h1><strong>研究方向</strong></h1>`).

| Profile fixture | Title extracted | Email | bio_text len | research_interests |
|---|---|---|---|---|
| `profile_cs_chai.html` (柴成亮) | 预聘副教授（特别研究员）、博士生导师 | ccl@bit.edu.cn | 764 | 4 tags |
| `profile_cs_liyouqi.html` (黎有琦) | 助理教授、特别副研究员 | liyouqi@bit.edu.cn | 289 | 8 tags |
| `profile_ai_dengfang.html` (邓方) | 教授 | dengfang@bit.edu.cn | 476 | 2 tags |
| `profile_cse_anjianping.html` (安建平) | 特聘教授，博士生导师，学院院长 | an@bit.edu.cn | 392 | 2 tags |

## Known oddities (school-specific layer)

1. **`cs.bit.edu.cn` is a multi-school site.** 计算机学院 + 特色化示范性软件学院 + 信创学院 share one site, one nav, one PI list. Treating `sw` as a separate code in schools.yaml is a *taxonomy hint* — the actual PI set is a subset of `cs`. The pipeline upsert layer dedupes by `(school, name, email)`.
2. **BIT uses 32-char hex hashes as profile filenames** (`<hex>.htm`), with the bucket-name as the directory (`bssds/`, `sssds/`, or `<inst>2/`). The hash is **per-profile**, not per-person: the same advisor in 教师名录 (jsml2/<inst>2/) gets the *same* hash as in 导师名录 (jsml/bssds/), but the URL prefix differs — dedup by basename (hex.htm) collapses these correctly.
3. **bssds vs sssds are disjoint sets of people.** A name in 博士生导师 never appears in 硕士生导师 on the captured CS pages (verified empirically — `set(names_b) & set(names_s) == ∅`). They are not "rank buckets of the same person" but two distinct advisor pools.
4. **CS profile head uses `div.sub_034 > div.left > div.summary > <p>职称：…</p>`.** The label/value pairs are `<p>`-per-row plain text with `职称：` / `联系电话：` / `E-mail：` / `通信地址：` prefixes. Body sits in `div.sub_034 > div.right.article` with real `<h2>` section headers (`个人信息` / `科研方向` / `代表性学术成果`) — *not* `<h4>` or pseudo-headers.
5. **AI list cards are lazy-loaded.** Images carry `data-src="…"` (not `src`); the parser falls back to `data-src` first, skipping placeholder GIFs.
6. **AI profile head is a label/value `div.box > div.left + div.right` grid** with labels `姓名 / 职称 / 导师类型 / 学科方向 / 电子邮件`. The body article `div.sub_031b article` carries `<h2>个人信息</h2>` (not `研究方向`), so research_interests typically come from 学科方向 in the head card or inline regex over the bio.
7. **CST (cst.bit.edu.cn) profile uses a `<table>` head card** with the photo in one `<td>` and all metadata in another, each line wrapped in `<p><span>label：value</span></p>`. Body uses `<h1>` (yes, `h1`!) with `<strong>研究方向</strong>` inside as the section header — so `_split_h2_sections` could miss it. Adapter ships a dedicated `_split_h1_strong_sections` walker for this case.
8. **CST advisor list paginates client-side** (`<a class="pages" href="#">N</a>`). The static HTML still contains the full first-page bucket (~46 bssds + 13 sssds = 59 PIs), enough to clear the 15-per-dept bar without JS execution.
9. **国防访问限制**: `ai.bit.edu.cn` and `cst.bit.edu.cn` occasionally serve 403 / require human-in-the-loop from VPS networks. The fixtures here are canonical "no-block" responses captured on 2026-05-20; in production a 403 should trigger Playwright fallback (planned v0.4 work — out of scope for the adapter).

## Multi-bucket dedup

The launch prompt called out "一个老师可能在多个分类下出现，注意去重". The parsers all maintain a `seen: set[str]` keyed by absolute `profile_url`; the `test_list_dedup` test concatenates the bssds HTML to itself and asserts the parser returns the same count as the single-page parse (proving the seen-set is doing its job).

## Validation

- `python3 -m compileall -q src/claw/adapters/bit.py tests/test_parsers/test_bit.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception, school code `bit` present (alongside 13 others)
- Manual fixture-based parse smoke (6 list pages + 4 profiles) — all parse cleanly with non-empty bio and recognized emails on every profile
- All 13 test cases pass under a stubbed-pytest runner on the VPS (real `uv run pytest` deferred to user-local execution)

## Known limitations

- `sw` department is a curated subset of `cs`; the pipeline upsert dedupes by `(school, name, email)`, so the actual yield is determined by the `cs` advisor list. If 软件学院 ever gets its own subdomain (`sse.bit.edu.cn` / similar), the adapter's host-based router will need to be updated.
- Only the static first page of the CST advisor list is parsed; later JS-paginated batches are dropped. v0.4 Playwright fallback can recover them.
- AI school: only 2 paginated pages are listed in schools.yaml (the live site has exactly 2 today). A future expansion of AI faculty would need a schools.yaml update or a generic pagination layer.
- 国防访问限制: any 403 on `ai.bit.edu.cn` / `cst.bit.edu.cn` is a fetch-layer issue, not a parser issue — the parser handles the HTML correctly when it arrives.

## schools.yaml diff

Appended one new top-level `- code: bit` block (no existing schools touched). See the bottom of `schools.yaml` for the canonical text.
