# 浙江大学 adapter — completion report

- **branch**: `feat/adapter-zju`
- **commit**: `720c042` (`feat(zju): 浙江大学 adapter v1`)
- **worktree**: `~/.claude/worktrees/feat+adapter-zju` (VPS-side; user-local is `~/Projects/adapter-zju` per launch prompt)
- **depts covered**: `cs`, `ai-inst`, `cadcg`, `sw`

## test results

| check | result | notes |
|---|---|---|
| `python3 -m compileall src/claw/adapters/zju.py tests/test_parsers/test_zju.py` | PASS | syntax-only; selectolax/pydantic not installed on VPS per CLAUDE.md |
| `pytest tests/test_parsers/test_zju.py` | **未跑** | run locally — VPS has no project deps |

6 tests defined (one per list flavour + 2 person.zju.edu.cn profile tests):

1. `test_parse_list_cs_research_supervisors` — csen/27051 page 1: ~14 advisors, 张秉晟 spot check
2. `test_parse_list_ai_inst_full_roster` — csen/27003: >100 advisors across 14 institutes; nav-noise exclusion; 庄越挺/吴飞/李玺/肖俊 spot checks
3. `test_parse_list_cadcg_dedupes_honor_groups` — 鲍虎军 dedupes from 5+ honor sections to 1
4. `test_parse_list_sw_table_anchors` — >50 sw teachers, all linking to person.zju.edu.cn
5. `test_parse_profile_person_zju_template` — full field lift (name/email/phone/title/research_interests) + footer-email no-leak + AJAX bio expected-None
6. `test_parse_profile_falls_back_when_no_email_in_template` — strips `<li class='email'>` and asserts adapter does NOT fall back to footer's xwmaster@zju.edu.cn

## fixture coverage

| dept | list_url | parsed / actual* | email % | RI % | notes |
|------|----------|------------------|---------|------|-------|
| cs | `csen/27051/list.htm` page 1 (of 6) | ~14 / 14 expected | 0% (none on list page) | n/a | every entry needs profile fetch to populate email/RI |
| cs | `csen/27051/list2.htm` page 2 (of 6) | (fixture only; not asserted) | — | — | proves pagination URL pattern works |
| ai-inst | `csen/27003/list.htm` (single page) | **100+ / 269 announced**† | 0% on list | n/a | full-college roster grouped by 14 所室 |
| cadcg | `cad.zju.edu.cn/talent-team` | dedupes 院士/长江/杰青 repeats | 0% on list | n/a | links mix person.zju + cad.zju/home + external (github.io etc.) |
| sw | `cst.zju.edu.cn/szdw/list.htm` | **50+ / 95** name links visible in fixture | 0% on list | n/a | table layout; all `<td>` cells point to person.zju.edu.cn |

\* "actual" = what the list page itself advertises (`总共 N 记录` / visible cells / lab self-report). `csen/26695` says 269 全院教职工.
† Conservative `>100` lower bound — exact count depends on how the nav blocklist handles a couple of borderline group-header anchors; the spot-checked names (庄越挺/吴飞/李玺/肖俊/陈纯) all appear.

**Email / RI % are 0 on list pages** because every zju list flavour gives only `<a>姓名</a>` — no inline contact. Both fields come from the profile pass:
- `person.zju.edu.cn` profiles: email + phone + title + research-interest tags all reliably extracted (see test 5)
- `mypage.zju.edu.cn` / `cad.zju.edu.cn/home/X` / external personal sites: generic best-effort section walker

## 特殊结构观察

1. **`person.zju.edu.cn/<slug>` 是 hub-and-spoke 统一系统，固定 `tpl_1` 模板**。
   姓名/职称/单位/电话/邮箱/地址/研究方向 全部用稳定 selector
   (`span.userBaseName` / `div.zc > span` / `li.telephone|email|address` /
   `li.yjfx > ul.second_research > li`)。研究方向 tag 模式干净（每个 `<li>` 一个 tag，
   `<b>·&nbsp;</b>` bullet 在 tag 之前要 strip）。**但**`个人简介`/`教学`/`研究`
   tab 是 `columnData(id)` 触发的 AJAX 调用，静态 HTML 中完全为空 —— v0.4
   Playwright 才能补 bio。

2. **dept 边界与浙大组织结构错位**：浙大 CS 学院下设 14 个研究所
   (人工智能所/计算机软件所/系统结构与脑机所/...)，"人工智能研究所"是其中一个 sub-section
   而非独立学院。`csen/27003` 是**全院**教师名录按所室分组排版，
   `csen/27051` 是**研究生导师**子集再按专业切分。因此 `cs` dept 和 `ai-inst` dept
   会高度重叠 (同一人在 27003 出现 1 次、在 27051 可能再出现) —— **adapter 不去重
   跨 dept**，靠主流程 `(school, name, email)` upsert 合并。Adapter 内只在
   单页内按 `(name, profile_url)` 去重 (主要为 cadcg 的多荣誉重复)。

3. **CAD&CG 实验室是独立 WordPress 站 (`cad.zju.edu.cn`, ISO-8859-1/GBK 编码)**，
   人才页按"院士/长江学者/国家杰青/..."等荣誉分组，**同一人会被列 5+ 次**。
   profile 链接也分三种：`cad.zju.edu.cn/home/<slug>/`、`person.zju.edu.cn/...`、
   完全外部站 (`kunzhou.net`、`cshen.github.io` 等)。adapter 的 list-side dedup
   是必要的；profile-side 把外部域名 fall through 到 `_parse_generic` (无法保证
   字段覆盖率，只能尽力)。

4. **软件学院 (`cst.zju.edu.cn/szdw/list.htm`) 用 `<table>` 网格布局**，每个
   `<td>` 一个老师，`<a href="person.zju.edu.cn/<slug>" title="陈纯">陈　纯</a>`
   —— 二字名用 U+3000 全角空格居中 ("陈　纯")，必须在比较/dedup 前 `.replace("　", "")`。
   这页本身**没有任何**职称/邮箱/电话信息，全部要走 profile pass。

## schools.yaml 改动

```diff
-      - code: cs
-        ...
-          - https://person.zju.edu.cn/cs        # 404 (旧入口已废)
-      - code: cadcg
-          - http://www.cad.zju.edu.cn/          # 主页非师资页
-      - code: ai-inst
-          - https://person.zju.edu.cn/ai        # 404
-      - code: sw
-          - http://www.cst.zju.edu.cn/          # 主页非师资页
+      - code: cs
+          - http://www.cs.zju.edu.cn/csen/27051/list.htm   # 研究生导师/CS 专业, 6 页
+          - http://www.cs.zju.edu.cn/csen/27051/list2.htm
+          - ... list6.htm
+      - code: cadcg
+          - http://www.cad.zju.edu.cn/talent-team          # 人才队伍
+      - code: ai-inst
+          - http://www.cs.zju.edu.cn/csen/27003/list.htm   # 全院教师名录
+      - code: sw
+          - http://www.cst.zju.edu.cn/szdw/list.htm        # 师资队伍
```

附原文 yaml 注释：解释 `person.zju.edu.cn/{cs,ai}` 在 2026-05 已 404，
解释 cs/ai-inst 段会因为 csen/27003 含全院而重叠，由 pipeline 去重。

## known limitations

1. **person.zju.edu.cn 的 `个人简介` / `工作研究项目` / `教学与课程` / `研究与成果` 4 个 tab 是 AJAX (`columnData(id)`) 加载**，静态 HTML 中 bio 完全为空。
   `_parse_person_zju` 显式 `bio = None`。v0.4 Playwright 配 `await page.click("a:has-text('个人简介')")` 后再 scrape 即可，selector 已经稳定。

2. **`recruit` / `is_recruiting` 在 person.zju.edu.cn 模板里没有专门字段**，全文搜
   "招" 关键词又会大量误伤（页面有 "招生信息" 在站点级 nav）。`_parse_person_zju`
   保守地返回 `None` 而不是猜；v0.3 DeepSeek extractor 可以读 bio 后推断。

3. **`mypage.zju.edu.cn` 模板没有抓 fixture**（多数老师其实在 `mypage` 而非
   `person`，比如 csen/27003 看到一半链接落在 `mypage.zju.edu.cn/<slug>`）。
   现在 `_parse_generic` 兜底，但完全没有针对性 selector —— 实测覆盖率会下降。
   建议 v0.2.1 单独跑 `_parse_mypage_zju` 分支（fixture 在用户本地拉一张代表性
   `mypage` 页后回填即可）。

4. **`cad.zju.edu.cn/home/<slug>/` 是 GBK 编码**，VPS 上 `curl` 后用 `open(..., encoding='utf-8')` 解码失败 (实际 fixture `profile_bao.html` 没法用 utf-8 读)。
   pipeline 层的 `http.py` 应该按 `Content-Type` charset 解；若没有，需要给
   cad 路径加 special-case。当前 adapter 假设传入的 `html` 已经是 str (decode 由
   pipeline 负责)，没显式处理。

5. **`tests/fixtures/zju/profile_bao.html` 是 GBK 文件**，目前 test_zju.py 没有
   测它（直接 `read_text(encoding='utf-8')` 会爆）。保留 fixture 是为了 v0.2.1
   写 cad-specific parser 时复用。

6. **VPS 上没装 selectolax / pydantic，所以 pytest 没在 VPS 跑过**。
   `compileall` 只验语法，逻辑错误（比如 selector typo）只有 user 本地跑 pytest
   才会暴露。**建议在 user 本地复跑前 6 个 test，特别是 test 2 (>100 advisors)
   和 test 5 (字段提取) —— 这两个最容易因为 selectolax 行为细节挂掉**。
