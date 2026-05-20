# buaa adapter v1 — completion report

- **Branch**: `worktree-agent-a41b924367b351bd4`
- **Worktree**: `/home/winbeau/wenbiao_zhao/claude-tools/.claude/worktrees/agent-a41b924367b351bd4`
- **Date**: 2026-05-20

## Deliverables

| Path | Note |
|---|---|
| `src/claw/adapters/buaa.py` | `BuaaAdapter` registered; depts `{cs, sw, ai}` |
| `tests/test_parsers/test_buaa.py` | 3 per-dept list tests + 3 profile-nav tests + 5 parametrized research-or-bio tests |
| `tests/fixtures/buaa/` | 7 list HTMLs (3 cs + 2 sw + 2 ai) + 5 profile HTMLs |
| `schools.yaml` | appended `- code: buaa` with cs / sw / ai entry URLs |

## Coverage observed against fixtures

| Dept | Fixture(s) | Parsed | Email coverage (list) |
|---|---|---|---|
| cs | js.htm + fjs.htm + js1.htm | ≈ 12 + 12 + 4 cards across 3 ranks (single-page snapshots) | 10/12 on js.htm via `<p class="yx">` |
| sw | wbtreeid=1224 (教授) + 1262 (副教授) | 25 + ~20 | 0 — VSB list cards omit email, profile fills it |
| ai | ayjsdsjs.htm + jcrc.htm | 54 + N | 0 on list, profile body has plain-text `邮箱：` |

Profile parsing verified on 5 fixtures — all yield bio (140-536 chars) and email; 3 of 5 surface research_interests via the interest splitter.

## Known oddities (school-specific layer)

1. **scse.buaa.edu.cn pagination is reversed** — landing `js.htm` is page 1; deeper pages count *down* (`js/6.htm` → `js/1.htm`). schools.yaml currently lists landing pages only; subsequent pages can be appended by the v0.5 generic pagination layer.
2. **scse head card uses `<span>label：value</span>`** instead of `<p>` — adapter pulls `电子邮箱` / `职称` / `座机` / `个人主页` from those spans.
3. **soft.buaa profile head uses `<dl><dd><font>label</font>value</dd>`** with `<font>` instead of `<strong>`. Body is `div.ar_article` / `div#vsb_content_NNN` with `<font color>label：</font>` pseudo-headers.
4. **soft.buaa name suffix "（双聘）"** is kept as-is; pipeline upsert dedupes by `(school, name, email)`.
5. **iai.buaa head uses `<p>姓　　名：X</p>` lines inside `div.titbox`** (full-width spaces in label). Body is `div#vsb_content` with `★科研概况` / `★联系方式` pseudo-headers (★ glyph prefix).
6. **iai email is plain-text inside body after `邮箱：`** — never image-obfuscated on the sample captured.
7. **VsbCMS plumbing shared across scse / soft / iai** — same favicon / `/system/resource/js/*` / `vsb_content` containers; adapter relies only on inner `v_news_content` / `vsb_content` plus per-dept head-card selectors.

## Bug fixed during finalization

`_split_font_sections` had a KeyError path: a same-line pseudo-header with empty body never called `sections.setdefault(head, [])`, so the next paragraph's append crashed. Fix: always `setdefault` before the conditional append.

## Validation

- `python3 -m compileall -q src/claw/adapters/buaa.py tests/test_parsers/test_buaa.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception, school code `buaa` present
- Manual fixture-based parse smoke (3 list pages + 5 profiles) — all parse cleanly with non-empty bio and recognized emails

## Known limitations

- Only landing pages of scse ranks are in schools.yaml; deeper pages of scse cs ranks (`js/N.htm` for `N` in 1..6) deliberately omitted from v1 — additional `list_urls` can be appended without code change.
- No `cs.buaa.edu.cn` (legacy) fallback — scse is the live host; if the school migrates back, `_dept_from_url` still routes correctly thanks to the `or "cs.buaa.edu.cn" in host` clause.

## schools.yaml diff

Appended one new top-level `- code: buaa` block (no existing schools touched). See section in `schools.yaml` for the canonical text.
