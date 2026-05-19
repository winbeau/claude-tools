# 上海交通大学 adapter — completion report

- **branch**: `feat/adapter-sjtu`
- **adapter commit**: `45269cb` (this report commit follows)
- **worktree**: `~/Projects/adapter-sjtu` (VPS path: `.claude/worktrees/agent-a06433c04edff7d4c`)
- **fetched**: 2026-05-19 via curl from the live sites

## test results

- compileall: **PASS** (`python3 -m compileall -q src tests` → exit 0)
- pytest: **not run on VPS** (no project venv; pytest must run on user's local box via `uv run pytest tests/test_parsers/test_sjtu.py`)

## fixture coverage

| dept | list_url | parsed (unique) | est. public roster | notes |
|------|----------|-----------------|---------------------|-------|
| `cs` | `https://www.cs.sjtu.edu.cn/jiaoshiml.html` (data via POST AJAX `/active/ajax_teacher_list.html` cat_id=20) | **291** | ~290 全职专任 (computer science + cyber sec) | list shell HTML returns 0; JSON fixture parses cleanly |
| `ai` | `https://soai.sjtu.edu.cn/cn/faculty/zzjs` | **41** | ~41 专职教师 (small AI school) | list page has name+title+email+homepage inline (>80% email coverage on list page itself) |
| `see-ai` | `https://sais.sjtu.edu.cn/faculty.html` (POST AJAX cat_id=18) | **204** total → filtered by 研究方向 AI keyword on profile | ~200 sais 全编 (auto/sense/AI/robotics/control) | non-AI faculty (e.g. POCT 诊断技术 / 电泳) get empty `research_interests` as the "exclude me" signal |
| `qingyuan` | `http://www.qingyuan.sjtu.edu.cn/c/quanzhijiaoshi.html` | **10** | 10 长聘教授+轨副+轨助 (research institute, small by design) | each teacher rendered twice on the page; URL-dedup collapses to 10 |

Profiles sampled and committed as fixtures:

- `profile_cs_chenhaibo.html` — senior CS 特聘教授 with **no email on page** (only 个人主页)
- `profile_cs_dongmingkai.html` — junior CS 副研究员 with email + 个人主页
- `profile_ai_caoqinxiang.html` — AI school 副教授, bio includes recruitment phrase ("招收硕士研究生与博士研究生")
- `profile_see-ai_chenweidong.html` — sais robotics 长聘教授, AI-relevant
- `profile_see-ai_baiyang.html` — sais 助理研究员, edge AI/网络方向, AI-relevant
- `profile_see-ai_caochengxi_nonai.html` — sais 仪器/电泳方向, **non-AI** (filter test)
- `profile_qingyuan_xuningyi.html` — qingyuan 长聘教授, bio carries "研究方向为..." prose

## 特殊结构观察

1. **CS + see-ai 师资名单是 POST-only AJAX**. `www.cs.sjtu.edu.cn/jiaoshiml.html` and `sais.sjtu.edu.cn/faculty.html` are JS shells; faculty data is fetched via `POST /active/ajax_teacher_list.html` with `cat_id=20` (cs) / `cat_id=18` (sais) and returned as a JSON envelope `{"tab_html": ..., "content": "..."}`. The current `core/http.py` Fetcher is GET-only, so v0.2 will pull 0 faculty until a POST mode is added. **Workaround** is wired into the adapter: `parse_list` detects a body starting with `{`, `json.loads` it and parses the `content` HTML — meaning a tiny fetcher upgrade (one POST helper) is enough to unlock these two departments without touching the adapter again.

2. **`cs.sjtu.edu.cn` 没有 PeopleList.aspx**. The v0.1 `schools.yaml` URL `https://www.cs.sjtu.edu.cn/PeopleList.aspx` simply returns the new-CMS homepage; it has been replaced by the modern `/jiaoshiml.html` + AJAX setup described above. `schools.yaml` updated.

3. **`qingyuan.sjtu.edu.cn` 不存在；canonical is `www.qingyuan.sjtu.edu.cn`** (HTTP, no HTTPS). DNS for the bare apex fails. `schools.yaml` updated. The list page also duplicates every teacher: once in a `<div class="tech_position">` block grouped by tenure rank (`长聘教授` / `长聘教轨副教授` / `长聘教轨助理教授`) and once in a commented-out alphabetical grid below.

4. **AI school (soai/sai) profiles have no dedicated 研究方向 section** — research direction is embedded in 个人简介 prose. Adapter falls back to regex grep ("研究方向 / 研究兴趣 / 研究领域 + colon + clause") inside the bio when the section dict is empty.

5. **see-ai AI filter** is keyword-based (`人工智能 / 机器学习 / 机器人 / 智能 / 感知 / 视觉 / 大模型 / 边缘计算 / ...`) applied to the 研究方向 text. Mis-classifications at the margins are expected — e.g. "智能控制 / 智能仪器" hybrid faculty (鲍其莲 group) are KEPT because the filter has no way to distinguish them from real AI work from a single signal. Non-AI faculty (biology / electrophoresis / power electronics) are filtered out. v0.3 should reconcile with the DeepSeek enrichment layer.

## schools.yaml 改动

Only the `sjtu` block was edited. Diff (semantic):

- `cs.list_urls`: `…/PeopleList.aspx` → `…/jiaoshiml.html` (with comment noting the POST AJAX dependency)
- `see-ai.list_urls`: `https://english.seiee.sjtu.edu.cn/` → `https://sais.sjtu.edu.cn/faculty.html` + `name_cn` updated to `自动化与感知学院（电院 AI 方向）`
- `ai.list_urls`: `https://soai.sjtu.edu.cn/szdw` (404) → `https://soai.sjtu.edu.cn/cn/faculty/zzjs`
- `qingyuan.list_urls`: `https://qingyuan.sjtu.edu.cn/` (DNS fail) → `http://www.qingyuan.sjtu.edu.cn/c/quanzhijiaoshi.html`

## known limitations

- `cs` and `see-ai` need a POST-capable fetcher to actually populate. The adapter is **ready** to consume the JSON payload; the bottleneck is `core/http.py`. Until then, real crawls for those two departments will return 0 items. (Adapter does not throw — gracefully returns empty list when fed the static shell.)
- CS profile pages frequently omit `email` for senior professors; we deliberately leave `email=None` rather than scraping the school-level footer `scs@sjtu.edu.cn`. Pipeline / DeepSeek can recover via the linked 个人主页 (e.g. `ipads.sjtu.edu.cn/haibo_chen`).
- see-ai AI filter uses keyword-grep; faculty whose 研究方向 is purely 控制/感知/仪器 without the substring 智能 / AI / robot will be dropped even if their actual work involves ML. **Recommend** v0.3 DeepSeek re-run on `bio_text` to recover edge cases.
- AI-school profiles' `research_interests` are extracted by regex from prose, not from a structured section; expect lower fidelity (more noisy phrases) than for CS / sais.
- No Playwright dependency was added (per instructions); CS shell + see-ai shell remain JS-dependent for non-POST clients.
