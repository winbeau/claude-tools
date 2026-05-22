# supervisor-claw v0.5 — NWPU email backfill report

## 1. Baseline

| metric | value |
|---|---:|
| total advisors (nwpu) | 231 |
| with_email | **0** |
| coverage | **0%** |
| gap | 231 |

NWPU has the second-largest absolute email gap among the 20 schools, behind only
xidian.

## 2. Why email is missing — root cause

NWPU centralises **all** teacher contact information into the
`teacher.nwpu.edu.cn` portal. The per-department sub-domains we crawl
(`jsj.nwpu.edu.cn`, `ruanjian.nwpu.edu.cn`, `wlkjaqxy.nwpu.edu.cn`)
list teachers by name + title + photo only — **no email is exposed on any
list page**.

`teacher.nwpu.edu.cn` itself is behind a **TS-WAF JS challenge** that
the v0.4 fetcher and even the v0.5 `pipeline.stealth_crawler` cannot
crack. Every static GET (and every Playwright + playwright-stealth
navigation we have tried) lands on the 2-3 KB WAF stub:

```html
<meta id="hK5iNqnNcwxO" content="..." r='m'>
<script src="/jcGbaIA7dRsZ/eU4pnslpsr1i.<hash>.js" r='m'></script>
```

`adapters/nwpu.py::_is_ts_waf_stub` detects this and short-circuits to
the list-item data verbatim, so the `email` field has been `NULL` for
all 231 NWPU advisors since v0.1.

**Consequence for backfill**: the generic `js` strategy in
`enrichers/email_backfill.py` — which navigates `advisor.homepage` in
the stealth browser and waits for an in-page decoder JS to populate the
DOM — has nothing useful to do for NWPU. There is no profile DOM to
read; there is no email cipher to decode. We do **not** ship a NWPU
JS-decoder.

## 3. Strategy — wayback → bing → dblp

`src/claw/enrichers/sites/nwpu_email.py` exports
`async def find_email(advisor, page, sess, school_name_cn)` and runs the
following cascade. Each tier is wrapped in `contextlib.suppress` so a
network error in one tier never blocks the next.

### Tier 1 — Wayback Machine snapshot

For each advisor with a `homepage` (almost always
`https://teacher.nwpu.edu.cn/<id>`), we query the
`archive.org/wayback/available` API. If the closest snapshot is
available, we fetch the raw page using the `…/web/<TS>id_/…` modifier
(so we get the archived HTML, not the wayback toolbar wrapper) and
regex-match any plain-text email in the body. The `_pick_best` helper
in `email_backfill.py` prefers `nwpu.edu.cn` addresses and rejects
footer-like local parts (`webmaster@`, `info@`, etc.).

Why wayback first: a non-trivial fraction of teacher.nwpu.edu.cn pages
were archived **before** TS-WAF was deployed (~ pre-2022) and those
snapshots are plain-text academic profiles with email in the head card.
Wayback is also free + un-WAFed, so this tier is the cheapest by far.

### Tier 2 — Stealth Bing / cn.bing / DuckDuckGo / Sogou

Calls the shared `search_email_via_stealth_bing(page, name,
school_name_cn=西北工业大学, domain_hint=nwpu.edu.cn)` helper.

The query template is `<name> 西北工业大学 email nwpu.edu.cn`. Each
engine's SERP HTML is scanned with the email regex and `_pick_best`
prefers `nwpu.edu.cn`. The cascade falls through bing.com →
cn.bing.com → duckduckgo.com → sogou.com so a captcha on one engine
doesn't kill recall.

### Tier 3 — DBLP

Calls `dblp_email_lookup(sess, name, affiliation_hint="Northwestern
Polytechnical University")`. DBLP rarely surfaces an email in the
author XML directly but is cheap and we already have the helper.

## 4. Dispatch hook — minimal-change rationale

The hook added to `enrichers/email_backfill.py` (commit `aa39c6f`) is
deliberately conservative:

* a new `_SITE_EMAIL_OVERRIDES` tuple lists school codes with a
  registered site module (currently just `nwpu`)
* `_resolve_site_override(school_code)` lazy-imports
  `claw.enrichers.sites.<code>_email` via `importlib`. 19 other schools
  pay **zero** import-time cost.
* `backfill_one_advisor` calls the site module's `find_email` first;
  if it raises, we log and fall through to the generic cascade; if it
  returns `(None, None)` we **also** fall through, so a conservative
  site module doesn't reduce recall.

Alternative considered: a `_SITE_EMAIL_DISPATCH = {"nwpu": nwpu_email.find_email}`
dict at module level. Rejected because that forces an eager import of
`enrichers.sites.nwpu_email` (and via it the shared helpers) on every
`email_backfill` import, even for schools that don't need it. The lazy
form keeps the module load graph clean.

## 5. Expected recovery rate

Conservative estimate: **20-30 %** of the 231 gap (~ 45 – 70 advisors).

Breakdown:

* **wayback**: 10-20 %. Empirically, wayback has snapshots for prominent
  PIs (老资历正高), much sparser for assistant professors and recent
  hires. The TS-WAF wall went up some time in 2022/2023, so anyone
  appointed since then has effectively no archived profile.
* **bing**: 5-15 %. Bing surfaces conference biographies, lab pages,
  ResearchGate, etc. Risk: cn.bing captcha after a few hundred queries.
  The cascade to DuckDuckGo / Sogou mitigates but doesn't eliminate.
* **dblp**: 1-3 %. DBLP email coverage for Chinese CS faculty is very
  thin; mostly only authors with international co-authorships have any
  reachable email.

We deliberately do **not** attempt name-to-pinyin email guessing for
NWPU because:

1. There is no public webmail probe endpoint we can verify against
   without sending mail.
2. NWPU has multiple email domains (`nwpu.edu.cn`,
   `mail.nwpu.edu.cn`, some legacy `vip.163.com` aliases) — guess
   space is too wide.

## 6. Known limitations

* **TS-WAF is the single biggest blocker.** Anyone hired post-2022 who
  has no DBLP-linkable publication is essentially unrecoverable through
  email backfill alone.
* **Wayback coverage is uneven.** Many teacher.nwpu.edu.cn pages have
  zero snapshots; others have only stub snapshots that were already
  captured during the TS-WAF era.
* **Chinese-engine captcha risk.** cn.bing / Sogou will trip CAPTCHAs
  if we hammer them. The shared `search_email_via_stealth_bing` helper
  already inserts polite sleeps; the human-in-the-loop policy in
  `feedback_anticrawl_strategy` may need to be invoked if recall stalls.
* **`is_recruiting` / `bio` / `research_interests` are out of scope.**
  This task only fills `advisor.email`; everything else remains as the
  v0.4 list-item stub.

## 7. Test coverage

`tests/test_email_backfill/test_nwpu_email.py`:

* `test_nwpu_wayback_hit_returns_email` — tier 1 success, bing/dblp not
  touched
* `test_nwpu_wayback_miss_then_bing_hit` — tier 1 miss + bing SERP hit,
  asserts dblp **not** called
* `test_nwpu_all_tiers_miss_returns_none` — full miss returns
  `(None, None)` cleanly

Tests drive the site module with tiny async fakes (`FakeSess`,
`FakePage`) that mirror the shape of `httpx.AsyncClient` and the
Playwright `Page` surface; no live network is required.

## 8. Run command (huawei2 / local)

```bash
uv run claw backfill-email nwpu --strategy bing,dblp
```

`js` is intentionally omitted from `--strategy` — the dispatch hook in
`backfill_one_advisor` will route through `sites.nwpu_email.find_email`
first regardless of the strategy list, and that module ignores `js`
entirely. Passing `js` would only cost wasted navigations during the
generic-cascade fall-through (which only runs if `find_email` returns
`(None, None)`).

Audit log: every successful write goes to `data/email_backfill_audit.jsonl`
with `source ∈ {wayback, bing, dblp}`. Roll-back any tier post-hoc:

```sql
UPDATE advisor SET email = NULL
 WHERE id IN (
   SELECT advisor_id FROM <jsonl> WHERE source = '<tier>'
 );
```
