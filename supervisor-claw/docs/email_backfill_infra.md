# Email Backfill Infrastructure (v0.5 Phase 0)

Phase 0 of `docs/plans/email_backfill_5x4.md`. Adds the shared modules and
CLI command R1-R4 sub-agents will build on. No crawl or DB change here —
just plumbing.

## Files

| Path | Role |
|---|---|
| `src/claw/core/email_decoders.py` | Site-specific JS-encrypted email decoders. |
| `src/claw/enrichers/email_backfill.py` | Helpers + per-advisor orchestrator. |
| `src/claw/cli/__init__.py` (`backfill-email`) | CLI entry; owns Playwright + DB lifecycles. |
| `tests/test_email_backfill/` | Fixtures + unit tests. |

## `core/email_decoders.py`

| Function | Input | Output |
|---|---|---|
| `decode_xidian_email(page)` | Playwright `Page` already navigated to `faculty.xidian.edu.cn/<id>/...` | `str | None` (lowercased email) |
| `decode_hust_email(page)` | Page already on `faculty.hust.edu.cn/<slug>/zh_CN/index.htm` | `str | None` |
| `extract_email_from_rendered_dom(page, domain_hint=None, settle_ms=1500)` | Any post-render page | `str | None` (prefers `domain_hint`-matching, then academic TLDs) |

Strategy: instead of porting the site's encryption JS to Python (the
`tsitesencrypt.js` xor/base64 + per-page key on xidian, `ImageScale.addimg`
on hust), we let the page's own JS run in a real Chromium and read the
post-decode DOM. The decoder uses `page.wait_for_function(...)` to wait
for the encrypted `<span _tsites_encrypt_field>` text to contain an `@`,
then `evaluate(...)` to read it.

Footer-style addresses (`webmaster@`, `info@`, `sse@hust`, etc.) are
filtered out by the candidate picker.

## `enrichers/email_backfill.py`

| Function | Notes |
|---|---|
| `decode_js_email_on_page(page, school_code)` | Dispatches to the right decoder; falls back to generic DOM extract for schools without one. |
| `search_email_via_stealth_bing(page, name, school_name_cn, domain_hint=None)` | Cascade: `bing.com` → `cn.bing.com` → `duckduckgo.com` → `sogou.com`. **No paid APIs.** Navigates via the supplied `page`. |
| `dblp_email_lookup(sess, name, affiliation_hint)` | `httpx.AsyncClient` query against `https://dblp.org/search/author/api`; regex on each author's `.xml`. |
| `update_email_only(session, advisor_id, new_email, source, audit_path)` | Surgical write — refuses when `advisor.email IS NOT NULL`. Appends one JSON line per write to `data/email_backfill_audit.jsonl`. |
| `backfill_one_advisor(advisor, page, sess, school_code, school_name_cn, strategies)` | Cascade orchestrator (default `['js','bing','dblp']`). Returns `(email, source)`. |

Browser + httpx clients are **never opened** inside this module; the CLI
holds the lifecycle and passes them in. This lets R1-R4 site sub-modules
reuse the same `Page` and avoid re-warming the stealth context.

## `cli/__init__.py` — `claw backfill-email`

```
claw backfill-email <school> [--limit N] [--dry-run] \
    [--strategy js,bing,dblp] [--headed] [--concurrency 1]
```

* Selects `advisor` rows in `<school>` with `email IS NULL`.
* Opens **one** stealth Playwright context (reuses
  `pipeline.stealth_crawler.open_stealth_session`) and **one** shared
  `httpx.AsyncClient`.
* For each candidate runs `backfill_one_advisor(...)`; on hit calls
  `update_email_only(...)` unless `--dry-run`.
* Sleeps ~1.5s between advisors to avoid ban.
* Idempotent — rerunning skips advisors that already have an email.
* Prints final `X/Y advisors got email (Z%); audit at …`.

## Audit format

`data/email_backfill_audit.jsonl`, one JSON object per successful write:

```json
{"ts":"2026-05-22T03:14:15+00:00","advisor_id":4216,"school":"xidian",
 "name":"张测试","old_email":null,"new_email":"prof@xidian.edu.cn",
 "source":"js_decode","confidence":0.9}
```

`source` values: `js_decode`, `bing`, `dblp`. Rollback safe: any audit
row can be reverted with `UPDATE advisor SET email=NULL WHERE id=...`.

## Known limitations (R1 sub-agents to refine)

* The xidian `_tsites_encrypt_field` hex blob's exact XOR key is not
  reverse-engineered — we rely on letting the page's own JS decrypt the
  span. If `tsitesencrypt.js` ever fails to load (WAF stripped, CSP), the
  decoder falls through to the generic DOM regex which usually misses.
* HUST `Ot-ctact` decryption assumes the page is loaded over the actual
  `faculty.hust.edu.cn` origin (the JS likely checks `document.domain`).
  Wayback snapshots and offline copies will *not* decode.
* Bing / DuckDuckGo / Sogou impose soft per-IP rate limits and may CAPTCHA
  after ~30 queries in quick succession. The CLI's 1.5s inter-advisor sleep
  helps but R1 sub-agents should expect ~30% search-engine failures and
  build their reports accordingly.
* DBLP rarely lists emails directly — it's a long-tail fallback, expect
  <10% hit rate.

## Acceptance for Phase 0

* `python3 -m compileall -q src/claw` exits 0.
* Unit tests in `tests/test_email_backfill/` cover the decode path (via
  `FakePage`), picker priority, null-only-write semantics, audit shape.
* No dependency changes — reuses `playwright` / `httpx` / `sqlmodel` from
  `pyproject.toml`.
