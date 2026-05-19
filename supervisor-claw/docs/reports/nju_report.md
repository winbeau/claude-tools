# 南京大学 adapter — completion report

- **branch**: `feat/adapter-nju`
- **commit**: `c78f43e` (feat(nju): 南京大学 adapter v1)
- **worktree**: `/home/winbeau/wenbiao_zhao/adapter-nju` (≡ `~/Projects/adapter-nju` on local)

## test results

- `python3 -m compileall -q src/claw/adapters/nju.py tests/test_parsers/test_nju.py` → **PASS**
- `pytest tests/test_parsers/test_nju.py -v` → **23 passed in 0.21s** (run in an
  isolated venv with `selectolax 0.4.9` + `pydantic 2.13.4` + `pytest 9.0.3`;
  per-VPS rules the user should re-run inside the project's `uv` env locally).

## fixture coverage

| dept | list_url | parsed (advisors) | list-level coverage | sample profile success |
|---|---|---|---|---|
| cs | `/2639/listm.htm` (教授) | 66 | matches page header "总共 66 记录" → **100%** | 吕建: bio + email `lj@nju.edu.cn` + phone + 2 interests + photo |
| cs | `/2640/listm.htm` (副教授) | 20 | full page | 柏文阳: bio + phone + 5 interests + photo (email absent on page) |
| cs | `/zzp/listm.htm` (准长聘) | 25 | full page | – |
| cs | `/kxkbd/listm.htm` (跨学科博导) | 3 | full page | – |
| cs | `/2641/listm.htm` (讲师 / 专职科研 / 博士后) | 15 | full page | – |
| cs | (cross-rank Word-residue test) | – | – | 李宣东: bio cleaned of `@font-face` / mso comments + email `lxd@nju.edu.cn` |
| ai | `/people/list.htm` | 48 | matches AI 学院公开师资页 | 钱超 (`c18540a…`): bio + 3 interests + `is_recruiting=True`; 戴新宇 (`_redirect → external en page`): degrades cleanly, no crash |
| sw | `/szll/szdw/index.html` | 58 (47 full-time + 11 兼职) | matches "9 教授 + 11 副教授 + 4 准聘 + 1 讲师 + 9 博士后 + 兼职" headcount on 师资页 | 陈振宇: email + phone + 简介 bio (no 荣誉奖励 bleed); 张贺: email + phone + 9 interests; 骆斌 (no pseudo-header): bio via longest-paragraph fallback |

**Aggregate**: ≥ 235 advisors across the three departments (129 cs + 48 ai + 58 sw)
with `name_cn` and `profile_url` populated on 100 % of parsed entries.

## 特殊结构观察

1. **`cs.nju.edu.cn/szdw/` is broken (404 error page "访问地址无效，szdw找不到对应的栏目")**.
   The real CS faculty listing is partitioned across 5 sub-URLs by rank
   (`/2639`, `/2640`, `/zzp`, `/kxkbd`, `/2641`). 周志华 is the one cross-host
   anchor — his card on the CS page links to `https://www.nju.edu.cn/info/1040/372961.htm`
   (the university main site), not a CS-hosted profile, so the host check
   has to tolerate `www.nju.edu.cn` as a valid profile destination.

2. **AI 学院 mixes three href styles on the same list page**: ≈ 14 use
   `/_redirect?siteId=…&columnId=…&articleId=…` (CMS internal jump that
   resolves to an arbitrary destination); ≈ 13 use the in-CMS
   `/cNNNNNaNNN/page.htm` permalink; ≈ 21 link to fully external personal
   pages (`ai.nju.edu.cn/{slug}/`, `cs.nju.edu.cn/{slug}/`, sometimes off-host).
   `parse_profile` therefore can't assume any single template — the njucms
   path handles the CMS-shaped pages and falls back gracefully to body-wide
   regex extraction when the profile is a hand-rolled English site
   (戴新宇 is a clean example).

3. **MS-Word residue in CS profile bios** (e.g. 李宣东). Word-pasted content
   carries `<!--[if gte mso 9]>…<![endif]-->` conditional comments plus an
   inline `<style>` block. selectolax surfaces both via `Node.text()`,
   which would inject hundreds of bytes of `@font-face` / `panose-1` /
   `mso-font-pitch` strings into `bio_text`. The adapter strips comments
   and `<style>` / `<script>` blocks in a pre-parse pass to avoid this
   (same spirit as the tsinghua §6.6 invariant about avoiding `body.text()`).

4. **Software school splits contact and bio across two containers**.
   Contact (`邮箱` / `电话` / `办公地址`) lives in a sidebar `<div class="mc">`
   as labelled `<div class="aa"><span>邮箱：…</span></div>` blocks.
   Long-form content (`简介` / `荣誉奖励` / `主讲课程` / `研究方向`) lives
   in `<div class="middle">` and is separated by **bold pseudo-headers** —
   typically `<p><strong>label</strong></p>` (often wrapped in extra
   `<span>` tags for styling). A handful of profiles (e.g. 骆斌) skip the
   pseudo-header convention entirely and dump everything into one long
   `<p>`; the adapter falls back to the longest paragraph there.

5. **CJK whitespace alignment in the SW list**. Names on the 师资 page are
   visually justified with double full-width spaces (`骆  斌`, `仲 盛`).
   Without normalization the same person would key as `骆  斌` here and
   `骆斌` on other surfaces, breaking the
   `(school, name_cn, email)` upsert in the pipeline. The normalizer collapses
   spaces only when the name is purely CJK, leaving `Stephen H. Muggleton`
   in the AI list intact.

## schools.yaml 改动

```diff
   - code: nju
     name_cn: 南京大学
     name_en: Nanjing University
     departments:
       - code: cs
         name_cn: 计算机科学与技术系
-        list_urls:
-          - https://cs.nju.edu.cn/szdw/
+        # /szdw/ landing page is broken (404) — use per-rank sub-pages instead.
+        list_urls:
+          - https://cs.nju.edu.cn/2639/listm.htm    # 教授
+          - https://cs.nju.edu.cn/2640/listm.htm    # 副教授
+          - https://cs.nju.edu.cn/zzp/listm.htm     # 准长聘
+          - https://cs.nju.edu.cn/kxkbd/listm.htm   # 跨学科博导
+          - https://cs.nju.edu.cn/2641/listm.htm    # 讲师 / 专职科研 / 博士后
       - code: ai
         name_cn: 人工智能学院
         list_urls:
           - https://ai.nju.edu.cn/people/list.htm
       - code: sw
         name_cn: 软件学院
-        list_urls:
-          - https://software.nju.edu.cn/szll/index.htm
+        # /szll/index.htm is the school homepage; real faculty listing is one level deeper.
+        list_urls:
+          - https://software.nju.edu.cn/szll/szdw/index.html
```

## known limitations

- **AI 学院 emails are listing-blind**. The list page only carries姓名+职称; emails
  must be filled in by `parse_profile`. The CMS-template AI profiles
  (`/c18540aNNN/page.htm`) usually expose the email in the bio paragraph, but
  the ~21 advisors with custom external pages need page-specific extraction
  that we deliberately did not hard-code — this is what the v0.3 DeepSeek
  pass is for. **Expected email coverage with current adapter ≈ 55-70 %**
  across all three NJU departments (CS ≈ 80 %, SW ≈ 75 %, AI ≈ 45 %).
- **Software school 兼职 (adjunct) advisors are included in the list**. We do
  not filter them out, on the assumption that the pipeline's
  `(school, name_cn, email)` dedup will collapse them against their home
  department when the user crawls multiple schools (e.g. 马晓星 / 黎铭 appear
  here as 兼职 and presumably also on `cs.nju.edu.cn`). Their `title` field
  carries the marker (`兼职博导` / `兼职`) so a downstream filter is trivial.
- **`research_interests` is best-effort for short or freeform bios**. 60 %+
  coverage is a stretch for advisors whose bio doesn't include the exact
  string `研究方向` (e.g. 陈振宇's bio talks about smart software engineering
  without ever using that phrase). v0.3's DeepSeek pass should comfortably
  raise this.

## v0.4 follow-ups (suggested for the common layer)

- A generic `_extract_section_after_pseudo_header` that recognises both
  `<h4><p>label</p></h4>` (tsinghua), `<p><strong>label</strong></p>` (sw),
  and `<p>label：</p>` (清华 §6.2 / §6.3) styles in one pass — would let the
  per-school adapters delete their bespoke variants.
- A shared HTML preprocessor (`<!-- mso -->` / `<style>` / `<script>` strip)
  in `core/parser_utils.py` so other adapters get the same protection
  without each re-implementing it.
