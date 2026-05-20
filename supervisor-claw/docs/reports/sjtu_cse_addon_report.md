# SJTU CSE (网络空间安全学院) Add-on Report — v0.4.1

## Why this exists

v0.4 wrapped up the SJTU adapter with four departments (`cs` / `see-ai` /
`ai` / `qingyuan`) and pushed forward, but missed a standalone college:
**上海交通大学网络空间安全学院** at `infosec.sjtu.edu.cn`. The college is
organisationally distinct from `cs.sjtu` (计算机科学与工程系) and
`sais.sjtu` (电院 AI 方向), and it hosts senior PIs in cryptography,
software security, network security, system security, and AI security. We
add it back as the `cse` dept of the existing SJTU adapter — no new
adapter file.

## Source surface

| Item | Value |
|---|---|
| Department code | `cse` |
| Domain | `infosec.sjtu.edu.cn` |
| Roster URL | `https://infosec.sjtu.edu.cn/Directory.aspx` |
| Roster size | ~80 PI (75 local + ~5 cross-link to cs.sjtu) |
| Page size | 67 KB static HTML, no AJAX/SPA |
| Profile URL pattern | `DirectoryDetail.aspx?id=<int>` |
| Cross-link pattern | `http://www.cs.sjtu.edu.cn/PeopleDetail.aspx?id=<int>` (~5 senior PI) |

## Parser shape

### `_parse_list_cse`

- Iterates `li a` anchors whose `href` matches either
  `DirectoryDetail.aspx?id=N` (local) or
  `PeopleDetail.aspx?id=N` (cross-link).
- For each item, the list card itself carries name (`<h2>`), title
  (`<h5>职称：...</h5>`), research interests hint (`<p>研究兴趣：...</p>`),
  email (`<p>邮箱：...</p>`), and photo (`<img>`). This is unusually rich
  for the SJTU surface — most other SJTU lists need a profile fetch to get
  email.
- Cross-linked URLs are kept absolute so `_dept_from_url(profile_url)`
  routes them to the cs.sjtu profile parser instead of looping back to cse.

### `_parse_profile_cse`

Profile template is .NET WebForms `<div class="TeamDetail">`:

- Left column `div.w180`: phone/email/address `<em><i class="fa fa-*">` icons.
- Right column `div.w510`: `<h2>name</h2> <h3>title</h3>` plus an inline
  `<p>` containing biographical prose (Microsoft-Word-style span soup, but
  `selectolax` flattens it correctly via `text(separator=" ")`).
- Tabbed content area: `<li id="oneN">label</li>` × 8 paired by index to
  `<div id="con_one_N">body</div>`. Canonical labels observed in every
  fixture sampled: `研究兴趣 / 教育背景 / 工作经验 / 教授课程 / 论文发表 /
  项目资助 / 获奖信息 / 学术服务`. The parser reads labels positionally
  (handles future tab reordering) and falls back to the canonical sequence
  when the tab block is missing.
- `email_obfuscated` is set to `True` only when no email could be found
  anywhere (the school footer `scs@` is explicitly blocked from adoption).

## Cross-link routing — what happens for those ~5 senior PIs

The infosec roster cross-links 5 PIs (刘胜利 / 骆源 / 王磊 / 丁宁 / 龙宇) to
`http://www.cs.sjtu.edu.cn/PeopleDetail.aspx?id=N`. The list parser
absolutises these URLs as-is. At profile-fetch time:

1. `_dept_from_url(profile_url)` sees `cs.sjtu.edu.cn` host → returns `"cs"`.
2. `_parse_profile_cs` runs on the body and looks for `div.js-info` /
   `div.js-dt`.

**Known limitation (2026-05):** The PeopleDetail.aspx endpoint at cs.sjtu
now returns HTTP 302 to `/` — the cs.sjtu CMS migration retired this URL
pattern. `_parse_profile_cs` therefore returns `_empty_partial`. This is
fine because list-card metadata (name + email + title + photo) was already
captured at list-parse time and is preserved by `_empty_partial` via
`list_item` fall-through. So we still get a complete record for each
cross-linked PI; the only loss is the profile-only bio prose.

If/when the cs.sjtu site reinstates `PeopleDetail.aspx?id=N` (or maps it
to the new `jiaoshiml/<slug>.html` URL) we'll automatically pick up bio
text without further adapter changes.

## Coverage observed (2026-05-20 fetch)

| Metric | Value |
|---|---|
| `parse_list` items | 80 |
| Unique URLs | 80 (no dupes) |
| Names extracted | 80 / 80 = 100% |
| Title coverage | 80 / 80 = 100% |
| Email coverage on list | 76 / 80 = 95% |
| Cross-linked items | 5 / 80 = 6.25% |
| Profile bio extraction (local) | 3 / 3 sampled fixtures had non-empty bio |
| Profile research_interests (local) | 3 / 3 sampled fixtures had ≥3 tags |
| `email_obfuscated` (sampled local) | False on all 3 (plaintext emails present) |

## Limitations

1. **Cross-link cs.sjtu pages are stub responses** — see "Cross-link
   routing" above. Captured: name/title/email/photo. Lost: profile bio.
2. **Bio is span-soup** — the right-column `<p>` is full of Word-export
   `<span style>` fragments. `text(separator=" ")` produces clean prose
   but inserts extra spaces. v0.5 can normalise whitespace if needed.
3. **No 招生 section in any sampled profile** — `is_recruiting` will
   typically be `None` for CSE PIs unless the prose mentions 招收/招聘. This
   matches the other static SJTU departments.
4. **Tab order is template-assumed** — `_cse_tab_labels()` reads the
   visible labels and falls back to the canonical 8-label sequence. If a
   page omits the `.Tab` block entirely we use the fallback list.

## Files touched

- `src/claw/adapters/sjtu.py`
  - `_dept_from_url`: route `infosec.sjtu.edu.cn` → `"cse"` (placed first).
  - `_parse_list_cse`, `_cse_tab_labels`, `_parse_profile_cse` added.
  - `SjtuAdapter.supports` += `"cse"`; dispatch in both `parse_list` and
    `parse_profile`.
  - Module docstring updated.
- `schools.yaml`: `cse` dept appended under the `sjtu` block (only).
- `tests/fixtures/sjtu/`:
  - `list_cse_directory.html` (Directory.aspx, 67 KB).
  - `profile_cse_177_gudawu.html` (院长 with 8-tab bio).
  - `profile_cse_188_konglinghe.html` (smaller profile with phone).
  - `profile_cse_132_xingchaoping.html` (smaller profile, no phone).
  - `profile_cse_xlink_cs_96_liushengli.html` (cross-link redirect target,
    used to verify the routing-fallback path).
- `tests/test_parsers/test_sjtu.py`: +4 tests
  (`test_parse_list_cse`, `test_parse_profile_cse_no_nav`,
   `test_parse_profile_cse_email`, `test_parse_profile_cse_xlink_routes_to_cs`)
  plus 3 fixtures appended to the existing JS/nav leak-check sweep.

## Local validation

`uv run pytest tests/test_parsers/test_sjtu.py -q` should report 15
passing tests (10 pre-existing + 4 new + the broadened leak sweep). On
the VPS only `python3 -m compileall -q` was run (no pytest available);
all 15 tests pass when invoked via an in-memory pytest-stub harness.
