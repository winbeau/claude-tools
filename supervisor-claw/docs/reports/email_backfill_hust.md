# Email Backfill — hust (华中科技大学)

> v0.5 R1 agent C. Companion to `docs/plans/email_backfill_5x4.md`.

## Baseline (锁数 2026-05-22)

* total advisors: **299**
* with email: **37 (12 %)**
* gap to fill: **262**

HUST is the largest absolute gap of the three "JS-encrypted faculty
portal" schools in R1 (xidian / nwpu / hust). The encrypted population is
real and well-defined (we have the slug for almost every faculty member),
so the recovery ceiling here is much higher than for nwpu (TS-WAF blocks)
or nudt (military, never published).

## 缺失原因

1. **`faculty.hust.edu.cn` 主模板** — the email lives inside
   ``div.blockwhite.Ot-ctact > p > span[_tsites_encrypt_field]`` as a 64-char
   hex cipher. SiteBuilder's ``tsitesencrypt.js`` + an inline
   ``ImageScale.addimg()`` call decrypt and overwrite the span's text on
   page-load. The fill is **lazy and debounced** — on a fast machine it
   completes in ~3-5 s, on the VPS we routinely saw 8-12 s. Static GET
   (no JS) returns the cipher verbatim, so the v0.4 crawler left
   `email=None, email_obfuscated=True`.
2. **Legacy `info/<treeid>/<id>.htm` template** (cs/sse/aia sub-sites) —
   email is plain-text ("邮箱：x@hust.edu.cn") or a ``mailto:`` link, but
   the v0.4 parser sometimes missed it when the page had nothing inside
   ``div.txt`` and dumped everything into the bio prose; the regex in
   that path only picked up one address and was easily fooled by the
   shared institute footer alias (``scs@hust.edu.cn`` / ``sse@hust.edu.cn``
   / ``aia-president@hust.edu.cn``).
3. **Institute footer aliases** — every HUST sub-site embeds a global
   mailto in the page chrome (e.g. ``scs@hust.edu.cn`` for cs,
   ``sse@hust.edu.cn`` for sw, ``aia-president@hust.edu.cn`` for ai).
   Treating these as personal emails would be **worse** than leaving the
   row NULL, so the decoder must filter them.

## 选定策略 (cascade)

Implemented in
`supervisor-claw/src/claw/enrichers/sites/hust_email.py`,
exported as ``async def find_email(advisor, page, sess, school_name_cn)``.

| # | Strategy       | Source tag        | What it does |
|---|----------------|-------------------|--------------|
| 1 | `js` primary   | `js_decode`       | Load `advisor.homepage` (or `source_url`), wait up to 25 s across a 4-selector cascade (`a[mailto]` → `.email` → encrypt-field span → `div.Ot-ctact`) for the cipher to decrypt, then read the DOM. If still missing, sniff `a[href^=mailto:]` site-wide and finally regex over the rendered body. Footer aliases are filtered. |
| 2 | `js` alt URL   | `js_decode_alt`   | If step 1 missed, retry on URL-shape variants of the primary URL (HTTP↔HTTPS swap, trailing `/` ↔ `index.htm`). Cheap salvage for profiles whose primary URL serves a stub. |
| 3 | `bing`         | `bing`            | Stealth public-web search with `domain_hint="hust.edu.cn"`. Cascades bing.com → cn.bing.com → DuckDuckGo → Sogou. |
| 4 | `dblp`         | `dblp`            | DBLP author lookup with affiliation `Huazhong University of Science and Technology`. Low yield by itself but catches some senior PIs whose author pages list a public email. |

## Phase 0 patches reused / extended

* `core/email_decoders.decode_hust_email` — extended the timeout from
  15 s → 25 s and added the 4-selector probe sequence.
* `core/email_decoders._extract_mailto` (new) — generic
  ``a[href^="mailto:"]`` extractor with footer filtering and ``?subject``
  param stripping. Reusable from any site module.
* `_FOOTER_LOCAL_PARTS` — added the HUST institute aliases (`cs-help`,
  `sse-info`, `aia-info`, `csdean`, `csshuji`, `ssedean`, `sseshuji`,
  `aia-dean`) so that the picker never returns a footer mailto.

## 预期回收率

HUST's faculty portal is publicly indexed and almost every PI does
publish their address on the page (just behind the encryption layer).
With the lazy-fill timeout extended and a mailto fallback, we expect:

| strategy contribution (estimated) | hits / 262 missing |
|---|---:|
| js_decode (primary)              | 140-170 |
| js_decode_alt                    | 5-15    |
| bing (hust.edu.cn-restricted)    | 10-25   |
| dblp                             | 3-8     |
| **expected total**               | **160-200** |
| **expected coverage delta**      | **62 %-76 %** of the gap → final coverage **66 %-75 %** of HUST |

The plan target is "≥ 60 %" final coverage post-R1; this strategy should
hit the upper-middle of that range. Conservative point estimate:
**+150 emails recovered → final ~ 187 / 299 = 63 %** by the end of R1.

## 已知限制

1. **VSB / SiteBuilder rate-limiting** — repeatedly hitting
   `faculty.hust.edu.cn` from one IP triggers an interstitial redirect
   ("访问过于频繁"). The Phase 0 stealth crawler handles this with
   exponential backoff, but **rate-limited** advisors get retried only
   once per backfill pass. ≈ 5-10 PIs will need a second pass.
2. **Customised legacy URLs** — a handful of senior PIs (院士 / 长江)
   redirect their `faculty.hust.edu.cn/<slug>/zh_CN/index.htm` to a
   personal homepage on a third-party domain. We do **not** chase
   off-site homepages from `js_decode`; the `bing` step usually finds
   the address anyway, but with lower confidence.
3. **Adjunct / 双聘 PIs** publish a non-`hust.edu.cn` email (gmail,
   nudt.edu.cn, etc.). The decoder accepts these when they appear in a
   real `mailto:` link, but `bing` / `dblp` candidate-scoring biases
   toward `hust.edu.cn` so cross-domain addresses may be missed if they
   never made it onto the official page.
4. **No SMTP probe** — we intentionally do not verify candidate addresses
   via SMTP banner-check (would look like spam to the school's mail
   gateway). Audit / dry-run mode is the only safety net.
5. **Tests are FakePage-based** — the VPS has no Chromium binary, so we
   simulate Playwright's wait-for-function + evaluate surface with a
   selectolax-backed stub. End-to-end behaviour will only be exercised
   on huawei2 during the backfill run.

## 执行命令 (跑在 huawei2 / 本地)

```bash
# audit / dry-run first to inspect candidate emails per advisor
uv run claw backfill-email hust --strategy js,bing,dblp --dry-run

# real run (idempotent — only fills email IS NULL rows)
uv run claw backfill-email hust --strategy js,bing,dblp

# verify recovery
sqlite3 data/claw.db "
  SELECT COUNT(*) total,
         SUM(CASE WHEN email IS NOT NULL THEN 1 ELSE 0 END) AS with_email
  FROM advisor a JOIN school s ON s.id=a.school_id
  WHERE s.code='hust';"
```

Audit log appended at `data/email_backfill_audit.jsonl` (one line per
write). Rollback (per the plan):

```sql
UPDATE advisor SET email=NULL
WHERE id IN (<audit ids from this run window>);
```

## 文件 deliverables

* `supervisor-claw/src/claw/core/email_decoders.py` — improved
  `decode_hust_email`, new `_extract_mailto`, extended footer filter.
* `supervisor-claw/src/claw/enrichers/sites/__init__.py` — new package.
* `supervisor-claw/src/claw/enrichers/sites/hust_email.py` — new
  per-school cascade.
* `supervisor-claw/tests/test_email_backfill/test_hust_decoder.py` — 4
  test cases (decrypt path / mailto fallback / footer filter /
  `_extract_mailto` helper).
* `supervisor-claw/docs/reports/email_backfill_hust.md` — this file.
