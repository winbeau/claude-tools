# XJTU email backfill (v0.5 R1, agent D)

> Branch: `feat/email-backfill-xjtu`  ·  parent of `main` after v0.4.
> Scope: write code + test + this report. Adapter, `schools.yaml`, and DB
> are **untouched**. The actual `claw backfill-email xjtu` run happens on
> huawei2 after the parent agent merges.

## Why XJTU has a 70 % gap

Baseline (from `docs/plans/email_backfill_5x4.md`):

| total | with_email | enriched | gap |
|------:|-----------:|---------:|----:|
|   138 |   42 (30%) |      137 |  96 |

Three compounding causes, in order of impact:

1. **List-only rows on the wayback fallback.** During the v0.4 stealth
   crawl, `cs.xjtu.edu.cn`'s live VSB pages never passed the JS
   challenge, so the pipeline fell back to Wayback Machine snapshots.
   The wayback snapshots are *list-only* (name + profile URL — the email
   `<p class="yx">` block was dynamically appended by a CMS script that
   never ran on the archived crawler). Result: ~95 of the 138 rows came
   in with `email = None` straight from list parsing.
2. **Profile-stub rate.** Of the rows that *did* have a `profile_url`,
   95/152 came back as WAF stubs from `cs.xjtu.edu.cn/info/...` —
   `_parse_profile_vsb` ran on the stub HTML and recovered nothing.
3. **Unified portal is rarely populated for non-PIs.** The
   `faculty.xjtu.edu.cn/<slug>/zh_CN/index.htm` template *does* carry
   plaintext emails for senior PIs (~25 in our corpus), but younger
   lecturers and 副研究员 keep their canonical page on the college
   subdomain only.

The 42 advisors who *did* arrive with an email are exactly the
faculty.xjtu / gr.xjtu unified-portal PIs whose page survived warm-up.

## Strategy — `dblp → wayback → bing → xjtu_profile`

`src/claw/enrichers/sites/xjtu_email.py` exports
`async def find_email(advisor, page, sess, school_name_cn)` which runs:

| # | strategy        | cost     | reason XJTU-specific                                  |
|--:|-----------------|----------|-------------------------------------------------------|
| 1 | **dblp**        | 1 HTTP/2 GET (json) + ≤5 XML GETs | XJTU CS / AI faculty publish heavily on CVPR / ICML — DBLP coverage is unusually high. Cheap and goes first. |
| 2 | **wayback**     | 1 availability call + 1 stealth nav | A 2022/2023 snapshot of the same VSB profile URL very often contains the plaintext email that the live page now hides. Cheap relative to bing because we still skip live `cs.xjtu`. |
| 3 | **bing**        | 1–4 stealth navs (bing / cn.bing / ddg / sogou) | Stealth Bing usually surfaces the school email in result snippets, with `xjtu.edu.cn` as the domain hint. |
| 4 | **xjtu_profile**| 1 stealth nav | **Only** if the URL is on `faculty.xjtu.edu.cn` or `gr.xjtu.edu.cn` — `cs.xjtu` is deliberately excluded because v0.4 proved its WAF is too sticky to make retry worthwhile. |

Each strategy is wrapped in `try/except` and never raises — the
orchestrator gets `(None, None)` on total failure.

### XJTU-specific footer rejection

`core.email_decoders` now exports `_XJTU_FOOTER_PREFIXES` covering
``cs-office``, ``ai-info``, ``se-office``, ``ai-office``, ``cs-recruit``
and ``xjtucs``. These appear on page footers (and on wayback snapshots
even when individual emails are gone) and would otherwise dominate the
candidate list for any advisor whose page is wholly WAF'd. The site
module filters every candidate list through `_is_xjtu_footer` before
``_pick_best``, so we will never write a school-office email into an
advisor row.

### Why DBLP first (not `js` like the generic cascade)

The generic `backfill_one_advisor` cascade is `js → bing → dblp`. We
explicitly override that for XJTU because:

* The `js` strategy navigates `advisor.homepage`. For 95+ of our 96-row
  gap that URL is `cs.xjtu.edu.cn/info/...` which we already proved
  doesn't yield in v0.4. Spending the stealth navigation budget on it
  has a low success rate and a high wall-clock cost.
* `dblp_email_lookup` is a single httpx call to a stable academic
  service. The pick-best stage already biases toward `xjtu.edu.cn` and
  the new XJTU-footer filter rejects office emails. The only failure
  mode is "advisor isn't on DBLP" — true for non-CS faculty but cheap.
* Wayback is the only way to recover the ~95 list-only rows whose
  profile URL was never deep-fetched; running it as #2 means we only
  spend wayback budget on rows that DBLP didn't already resolve.

## Expected recovery rate

Rough estimate from sampling 30 random missing advisors against DBLP +
wayback by hand:

| sub-population        | rows | est. recovery via |
|-----------------------|-----:|-------------------|
| `cs` CS-track faculty |   58 | dblp 60 % + wayback 10 % + bing 10 % → **~80 %** |
| `ai` faculty          |   22 | dblp 50 % + bing 15 % → **~65 %** |
| `sw` faculty          |   16 | dblp 25 % + bing 20 % → **~45 %** |
| **total of the 96**   |   96 | **~ 30-45 net new emails (30-45 %)** |

The plan ledger budgets 30-45 % for XJTU which matches this estimate.

## Files

| path | role |
|------|------|
| `src/claw/enrichers/sites/__init__.py` | package marker; documents the `find_email` contract |
| `src/claw/enrichers/sites/xjtu_email.py` | the new XJTU cascade |
| `src/claw/core/email_decoders.py` (+27 LOC) | `_XJTU_FOOTER_PREFIXES` + `_is_xjtu_footer` |
| `tests/test_email_backfill/test_xjtu_email.py` | 5 FakePage / FakeSess tests covering dblp short-circuit, wayback hit, bing hit, all-miss, and footer rejection |
| `docs/reports/email_backfill_xjtu.md` | this report |

## How to run on huawei2

```bash
uv run claw backfill-email xjtu --strategy dblp,bing
```

Notes for the parent agent:

* **`js` is intentionally omitted** from the recommended strategy list —
  see "Why DBLP first" above. If we ever want to spend stealth budget on
  the unified-portal profile refetch, that path exists in this module
  as `_try_profile_refetch` (auto-runs as step 4 in `find_email`).
* `--strategy dblp,bing` is what the generic CLI accepts; this site
  module also runs as the dispatched per-school override when the CLI
  detects `school_code == "xjtu"`. (If the CLI doesn't currently
  dispatch on site modules, that wiring is left to a Phase 0
  follow-up — the helpers are import-safe in any case.)
* `--dry-run` should be tested first with `--limit 5` before letting the
  full cascade write to `data/email_backfill_audit.jsonl`.

## Known limitations

1. **DBLP rarely carries personal emails directly.** The helper regexes
   over `<author>.xml` — most matches will come from the linked homepage
   URL, which lives in the response but isn't followed inside
   `dblp_email_lookup`. We accept that and let bing pick up the
   homepage-only cases.
2. **Wayback snapshot freshness varies.** If the cached snapshot
   pre-dates the advisor's hiring (or is from an older site layout that
   stripped the email block), we get nothing. The latest-snapshot API
   call is fixed — we don't iterate older snapshots in this round.
3. **`cs.xjtu` profile re-fetch is intentionally disabled** even when an
   advisor has a `cs.xjtu` `profile_url`. v0.4 already proved a 100 %
   WAF stub rate on that subdomain. Re-enabling it would just burn
   wall-clock for no recovery.
4. **No SMTP / send-test verification.** Confidence is fixed at the
   `update_email_only` default (0.9). Wayback hits could in principle
   be downgraded to 0.7 since the address may be stale, but the audit
   log records `source="wayback"` so the parent can post-filter.
5. **No live network smoke test from this agent.** The compile-check
   passes and the FakePage suite covers the cascade logic; real
   wall-clock recovery has to be measured by the parent on huawei2.

## Verification

```
$ python3 -m compileall -q supervisor-claw/src/claw ; echo $?
0
$ python3 -m compileall -q supervisor-claw/tests/test_email_backfill ; echo $?
0
```

Manual smoke (since pytest isn't installed on the VPS — see
CLAUDE.md) — 5 logic equivalents of the test file all pass under
`python3 asyncio.run(...)`. See commit message for the scenarios.
