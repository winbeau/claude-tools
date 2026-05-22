# Email Backfill — xidian (R1 / 西安电子科技大学)

Part of v0.5 email backfill (`docs/plans/email_backfill_5x4.md`). R1 sub-agent
report for **xidian** (276 advisors, **0 (0%)** currently have `email IS NOT NULL`,
gap = **-276**).

## 1. Why email is missing

faculty.xidian.edu.cn (the central Sudy-CMS / tsites profile portal that
holds all three departments' profiles) **always** ships email as:

```html
<span class="encrypt-field"
      _tsites_encrypt_field="_tsites_encrypt_field"
      id="_tsites_encryp_tsothercontact_tsccontent"
      style="display:none;">
  0520d838ac5b3db0544c91bd08fc4088474b2015fd63d3f5a6ed75006b6aa482...
</span>
```

`/system/resource/tsites/tsitesencrypt.js` runs on page load and replaces
the span's text with the plain-text email. Static-HTML crawlers (the
default v0.4 pipeline) cannot see this — the adapter correctly marks
`email_obfuscated=True` and leaves `email=NULL`.

The hex blobs are **128–160 bytes** long for a typical email (which would
be < 32 chars plaintext), strongly suggesting a block cipher (AES/SM4)
with per-page session keys baked into the obfuscated tsitesencrypt.js. We
could **not** reverse-engineer the key from public information available
in this repo. See limitations below.

A small number of advisors (mostly in `ai` dept) link to legacy
`web.xidian.edu.cn/<userid>/` personal homepages — those use a different
template with no tsites cipher (sometimes a plain email in prose).

## 2. Strategy

We add a site-specific entry point
`src/claw/enrichers/sites/xidian_email.py::find_email` that decides per
advisor which strategies to run:

| Advisor type | Selected strategies | Why |
|---|---|---|
| `homepage` matches `faculty.xidian.edu.cn/<id>/zh_CN/index.htm` | `js → bing → dblp` | full cascade; JS path is the only one that can decode the tsites blob |
| `homepage` matches `web.xidian.edu.cn/<userid>/` | `bing → dblp` | template differs; JS decoder doesn't apply |
| `homepage` IS NULL (list-only PIs) | `bing → dblp` | no profile page available |

The `js` step calls `decode_xidian_email(page)`, which now uses a
three-step cascade itself:

1. **Pure-Python `decrypt_tsites_hex`** — reads every
   `<span _tsites_encrypt_field>` raw text and tries
   * plain-ASCII hex decode (rare but cheap),
   * full single-byte XOR sweep (0x00..0xff).
   If any candidate decodes to a valid email regex, return immediately.
   This **almost always returns `None`** on real faculty.xidian blobs but
   costs ~0.1 ms and saves a 15 s browser wait on lucky pages.
2. **Browser-render wait** — `page.wait_for_function(...)` for up to 15 s
   until any encrypt-field span contains an `@`; then read it.
3. **Generic DOM fallback** — regex over the full rendered DOM (bio prose
   sometimes carries a plain email even when the tsites JS fails).

`bing` searches via the existing stealth Bing/DuckDuckGo/Sogou cascade
with `domain_hint="xidian.edu.cn"`; `dblp` looks up DBLP author XML with
affiliation hint "西安电子科技大学".

## 3. Expected recovery rate

Conservative estimate: **40–50 %** (110–140 emails out of 276).

Breakdown of likely outcomes:

| Bucket | Rough count | Expected hit |
|---|---:|---|
| faculty.xidian profile + browser JS decodes | ~200 | 60–80 % once stealth Chromium + tsitesencrypt.js succeed (rate-limited / occasional WAF 403) |
| web.xidian / list-only | ~75 | 15–30 % via bing + dblp (lots of common names → ambiguous SERPs) |

Top reasons we won't reach 100 %:

* tsitesencrypt.js relies on browser features (`document`,
  `window.crypto`?) and sometimes fails when run through stealth; the
  15 s timeout will trigger frequently → falls through to weaker paths.
* Many SAI / CSE junior PIs only publish their personal email in PDF CVs
  or via WeChat QR, never on the homepage.
* Bing has IP rate-limits (~30 queries / 5 min from one VPS); the CLI's
  1.5 s sleep helps but ~30 % of bing searches will see CAPTCHA / 0 results.

## 4. Known limitations

* **tsites XOR key not reversed.** The pure-Python `decrypt_tsites_hex`
  only handles trivial XOR / plain-ASCII cases; the real cipher is
  (almost certainly) AES/SM4 with a per-page session key embedded in
  obfuscated JS. Reversing it would require capturing the live JS and
  symbolically tracing the key derivation — out of scope for this
  sub-agent. Browser-render is therefore the source of truth.
* **JS decode timeouts.** When tsitesencrypt.js takes longer than 15 s
  (slow CDN, WAF challenge), the wait times out and we fall through to
  the generic DOM regex (which almost always misses, since plain emails
  rarely appear in xidian bio prose).
* **WAF / 403.** Xidian is one of the "国防七子" / Seven Sons; sub-domains
  occasionally return 403 to non-Chinese ASNs. The stealth crawler's
  3-tier cascade (Wayback / archive fetch) helps but the wayback
  snapshot is also pre-decode hex, so it doesn't recover the email.
* **List-only advisors.** ~75 advisors have `homepage IS NULL` (mostly
  SAI lecturers that the dept page doesn't link out for). For these the
  cascade can only use bing + dblp; expect <30 % hit.
* **Common-name ambiguity.** Bing SERPs on a common Chinese name like
  "张伟" will return many different "@xidian.edu.cn" addresses — the
  helper picks the first regex match with a matching domain hint, which
  may be wrong. There's no easy way to verify without a DeepSeek
  cross-check pass; that's deferred to the post-backfill audit step.
* **email_obfuscated flag.** `update_email_only` sets
  `email_obfuscated=False` on every successful write so downstream
  enrichers stop guessing.

## 5. How to run

After R1 → main merge, on the execution host (huawei2):

```bash
# default: js → bing → dblp cascade, all advisors with email IS NULL
uv run claw backfill-email xidian --strategy js,bing,dblp

# dry-run first to inspect candidates without writing the DB
uv run claw backfill-email xidian --strategy js,bing,dblp --dry-run --limit 20

# headed mode for debugging tsitesencrypt.js failures
uv run claw backfill-email xidian --headed --limit 10
```

Audit log lands in `data/email_backfill_audit.jsonl`. Rollback any row
with:

```sql
UPDATE advisor SET email=NULL
WHERE id IN (SELECT advisor_id FROM ...);
```

## 6. Files touched

* `src/claw/core/email_decoders.py` — add `decrypt_tsites_hex`, route
  `decode_xidian_email` through the new pure-Python step first.
* `src/claw/enrichers/sites/__init__.py` — new package.
* `src/claw/enrichers/sites/xidian_email.py` — new `find_email` hook.
* `tests/test_email_backfill/test_xidian_decoder.py` — 3 async cases
  (browser-decode / bio-fallback / no-email) + 4 pure-Python
  `decrypt_tsites_hex` cases.

**Not touched** (per R1 rules): `src/claw/adapters/xidian.py`,
`schools.yaml`, DB / migrations.
