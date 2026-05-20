# xidian adapter v1 — completion report

- **Branch**: `worktree-agent-a3396ba8ab23e210f`
- **Worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-a3396ba8ab23e210f`
- **Date**: 2026-05-20

## Deliverables

| Path | Note |
|---|---|
| `src/claw/adapters/xidian.py` | `XidianAdapter` registered; depts `{cs, cse, ai}` |
| `tests/test_parsers/test_xidian.py` | 14 tests (helpers + 4 list-page + 4 profile-page + 2 quality/integration) |
| `tests/fixtures/xidian/` | 4 list HTMLs (2 cs + 1 cse + 1 ai) + 3 profile HTMLs (one per dept) |
| `schools.yaml` | appended `- code: xidian` with cs / cse / ai entry URLs |

## Coverage observed against fixtures

| Dept | Fixture(s) | List parsed | Notes |
|---|---|---|---|
| cs | `szdw/rcpy.htm` (broadest) + `szdw/dsjs.htm` (导师介绍) | **162** + **87** | Union across all 3 dept-side pages (rcpy + dsjs + xkky) ≈ **207 unique CS PIs**; pipeline dedupes |
| cse | `xyjslb.jsp?id=1596` (学院总览) | **20** | xyjslb endpoint caps each `id` at ~20; schools.yaml lists 5 sub-tree ids (1596 + 1928–1930 + 2661 + 3203) so the pipeline visits each → ≈ **80 unique PIs** |
| ai | `yjspy/dszy1.htm` | **74** | 68 link to `faculty.xidian.edu.cn`, 6 to `web.xidian.edu.cn/<userid>/` personal homepages |

Profile parsing verified on **3 fixtures (CS / AI / CSE)** — all yield bio (≥ 80 chars, no JS / nav leakage), research interests (3-5 tags each), and a proper 职称 string from `t_jbxx_nr`.

Email recovery:

* CS 慕建君: not surfaced (encrypted span absent in this fixture — left `None` + `email_obfuscated=False`).
* AI 白静: extracted from bio prose (`baijing@mail.xidian.edu.cn`, plaintext in recruiting blurb).
* CSE 曾勇: detected `<span _tsites_encrypt_field>` → marked `email_obfuscated=True`.

This matches the launch-prompt expectation: email coverage on faculty.xidian portal is low because the platform deliberately encrypts contact fields client-side, so v0.3 DeepSeek enrichment will pick them up.

## Known oddities (xidian-specific layer)

1. **Centralized faculty portal `faculty.xidian.edu.cn`**. CS / CSE / AI departments all funnel into a single Sudy-CMS template (`div.t_grjj_nr` for bio, `div.t_jbxx_nr` for 职称 / 所在单位 / 学科, `a[href*=/yjfx/]` anchors for research directions). One profile parser covers every dept.
2. **CS dept-site is just a giant table of name+link**. `cs.xidian.edu.cn/szdw/{rcpy,dsjs,xkky}.htm` are `<table>`-of-`<td>`-of-`<p>`-of-`<a href="https://faculty.xidian.edu.cn/.../zh_CN/index.htm">姓名</a>`. Names like `"张 南"` carry a half-width space, `"王 琨（男）"` carries a disambiguation suffix — adapter strips both.
3. **CSE list cap = ~20 PIs per sub-tree id**. `faculty.xidian.edu.cn/xyjslb.jsp` has no URL pagination (`pageNum` / `st` are no-ops). We enumerate 5 sub-tree ids in schools.yaml to cover ≈ 80 PIs of the ~100+ true CSE PIs. *Documented as v0.4 known limitation*; v0.5 should add a Playwright-driven alphabetical sweep over the portal.
4. **AI has one true list page**. `sai.xidian.edu.cn/yjspy/dszy1.htm` is the canonical 75-PI roster; alternative `sai.xidian.edu.cn/xygk/rcdw.htm` (人才队伍) is informational only (no faculty links).
5. **Senior AI PIs use legacy `web.xidian.edu.cn/<userid>/` homepages**. ~6 entries in the AI list link to custom personal sites, not the central portal. Adapter accepts the URL into `ListItem` but `parse_profile` returns an empty `AdvisorPartial` for non-`faculty.xidian` hosts so the pipeline doesn't crash; v0.3 enricher fills in.
6. **Emails always encrypted on faculty portal**. `<span _tsites_encrypt_field>` blobs are hex-encoded and decrypted by `/system/resource/tsites/tsitesencrypt.js` at runtime. Plaintext emails only appear when a PI writes them into the bio prose (招生 blurbs etc.) — adapter still tries `extract_email(scope_text)` first; falls back to `email_obfuscated=True` when the encrypted span is present.
7. **Research direction anchors carry numbered prefixes**. Anchor text is sometimes `"1. 计算机网络与差错控制技术"`, `"2. 网络编码技术及其应用"` (CS profile), sometimes a single anchor cramming multiple tags joined by `；`/space (CSE 曾勇: `"加密流量分析 IoT安全 物理层安全 安全芯片设计与分析"`). The adapter strips `^\d+[.)、．]\s*` and falls back to the standard `_split_interests` splitter when separators are detected.
8. **GBK fallback wired in but not currently triggered**. All sampled pages (cs / ce / sai / faculty.xidian) responded as UTF-8 with explicit `Content-Type: text/html; charset=UTF-8`. The adapter's `_decode_html_if_bytes` still sniffs for `gb2312`/`gbk`/`gb18030` meta on raw `bytes` input so older Xidian micro-sites (e.g. lab pages) don't break the pipeline.
9. **Anti-crawl is mild**. cs.xidian / ce.xidian / sai.xidian respond 200 under a stock UA without rate-limit. Only `ensai.xidian.edu.cn` (some lab) timed out from the VPS — irrelevant for v0.4, just a heads-up that Xidian is a 国防七子 school and a few sub-domains may need Playwright in v0.5.
10. **Duplicate-href bug on at least one CS cell**. `cs.xidian.edu.cn/szdw/rcpy.htm` carries the value `href="https://faculty.xidian.edu.cn/HXT/zh_CN/index.htmhttps://faculty.xidian.edu.cn/HXT/zh_CN/index.htm"` (CMS editor mistake). The adapter strips the second URL via `_DUP_HREF_RE` before passing to `absolutize`.

## Validation

- `python3 -m compileall -q src/claw/adapters/xidian.py tests/test_parsers/test_xidian.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception, school code `xidian` present with 3 depts (cs / cse / ai)
- Manual fixture-based parse smoke covering all 14 test functions — **14/14 pass** (executed via a pytest stub since the VPS doesn't have pytest installed; user should rerun `uv run pytest tests/test_parsers/test_xidian.py` locally to confirm)

## Known limitations

- **CSE coverage is portal-capped**. We surface ~80 of the ~100+ true CSE PIs because `xyjslb.jsp` doesn't paginate. Mitigation in v0.5: Playwright sweep `xyjslb.jsp?zm=A`…`zm=Z`.
- **Senior AI PIs with custom homepages are link-only**. ~6 senior PIs (e.g. 焦李成 at `web.xidian.edu.cn/lchjiao/`) are returned by `parse_list` but `parse_profile` cannot extract bio/email from their custom homepages. v0.3 DeepSeek + Playwright enrichment expected to fill these in.
- **Email coverage on the faculty portal is intrinsically low**. The portal encrypts emails JS-side; only PIs who write their email into the bio prose get a plaintext capture. The adapter correctly surfaces `email_obfuscated=True` for the rest so downstream knows to enrich.

## schools.yaml diff

Appended one new top-level `- code: xidian` block at the end of `schools.yaml` (no existing entries touched). 3 departments × 9 total list URLs:

* `cs`: 3 URLs (rcpy / dsjs / xkky)
* `cse`: 5 URLs (5 sub-tree ids on faculty portal)
* `ai`: 1 URL (dszy1.htm)
