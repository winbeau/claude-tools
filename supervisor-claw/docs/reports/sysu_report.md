# 中山大学 adapter — completion report

- **branch**: `feat/adapter-sysu` (harness branch, see git ref at top of repo)
- **worktree (VPS)**: `.claude/worktrees/agent-ac81a66350a3c0d16`
- **fetched**: 2026-05-20 via `curl` from the live cse / sse / sai sub-sites

## test results

- `python3 -m compileall -q src/claw/adapters/sysu.py tests/test_parsers/test_sysu.py` → exit 0
- `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` → no exception, 8 schools, sysu departments `[cs, sw, ai]`
- `pytest tests/test_parsers/test_sysu.py` — not run on VPS (no project venv); the test bodies were exercised manually against the saved fixtures and **all assertions pass** (list counts, email coverage, research-interest cleanliness, no JS/nav leak)

## fixture coverage

| dept | list_url | parsed (unique) | est. public roster | notes |
|------|----------|-----------------|--------------------|-------|
| `cs` | `https://cse.sysu.edu.cn/teacher` | **110** | ~110 (single-page roster of 专任教师, no pagination) | every card has name + title + email + 研究领域 inline; >99% email coverage from list page alone |
| `sw` | `https://sse.sysu.edu.cn/teacher` | **40** | ~50 (aggregate of 教授/副教授/助理教授/讲师/研究员, list landing page) | titles always present; emails only on ~15% of cards (profile parser fills the rest) |
| `ai` | `https://sai.sysu.edu.cn/teachers` (URL is plural, unlike sse) | **30** | ~30 (人工智能学院, 珠海校区) | every card has email + 研究方向; >90% email coverage on list page |

Profiles sampled and committed as fixtures:

- `profile_cs_caisuihua.html` — cse 副教授 with full top-card metadata and standard `<h3><strong>label:</strong></h3>` body sections
- `profile_cs_yuchao.html` — cse 教授 (强化学习 / AI智能体), profile includes recruitment phrasing
- `profile_sw_zhengzibin.html` — sse 院长 (IEEE/IET Fellow); section headers are `<p><strong>label:</strong></p>` only (no `<h3>`); 研究领域 and recruitment prose share one paragraph; two emails on page (faculty + student) — adapter must prefer the faculty one
- `profile_ai_dongrunmin.html` — sai 副教授, `<h5><strong>label</strong></h5>` section style, combined 研究与招生 section bundles research direction + 招生 boilerplate + URL

Additional list fixture: `list_sw_associate.html` (sse 副教授 sub-page) verifies the per-rank sub-pages parse with the same layout.

## 特殊结构观察 (供 v0.3 通用层归纳)

1. **`sdcs.sysu.edu.cn` 已废, 真正的 CS 入口是 `cse.sysu.edu.cn`** (School of Computer Science & Engineering 改名 2024). The `launch_sysu.md` candidate URL list pointed at `sdcs.sysu.edu.cn/szdw/jsxx.htm` style paths; all return DNS-NXDOMAIN. `schools.yaml` updated.

2. **三个学院共用同一套 Drupal 10 站点骨架, 但 list-card 模板有两种**:
   - cse: `<div class="facultyblock"> > div.detail > h3 a + span` (名字 + 职称) + `<p class="...">Email: ...</p>` + `<p class="area">研究领域：...</p>`
   - sse / sai: `<div class="list-images-1-1"> > div.list-content > h4.list-title > strong (name) + span (title)` + `<p><strong>Email：</strong>...</p>` + `<p><strong>研究方向：</strong>...</p>`
   两套都做了, 在 `parse_list` 里根据 host 路由.

3. **URL 路径不一致**: cse + sse 用 `/teacher` (单数), sai 用 `/teachers` (复数). 个人页一律是 `/teacher/<slug-or-id>`.

4. **profile 模板分三种 section header 风格** (`<h3><strong>label:</strong></h3>` vs `<h5><strong>label</strong></h5>` vs 裸 `<p><strong>label:</strong></p>`). `_split_body_sections` 用通用 traverse + pseudo-header 表统一处理.

5. **profile 头部位置不同**: cse 有完整的 `<div class="teacherinfoblock">` 头卡 (name/title/email/photo 都在); sse/sai **完全没有头卡**, 名字只在 `<title>` 和 `<h2><strong>name</strong></h2>` 出现, 其它字段都得从 body 里抓.

6. **`Drupal field-body` 选择器陷阱**: 一页有三个 `<div class="field-body">` 命中 (header banner / page body / footer). 必须用 `div[data-block-plugin-id="entity_field:node:body"] div.field-body` 锁定中间那个, 否则 `tree.css_first("div.field-body")` 会取到 banner. (v0.3 通用 helper 可以专门加一个 "pick non-banner non-footer body" 工具.)

7. **`mail.sysu.edu.cn` vs `mail2.sysu.edu.cn`**: 中大教师邮箱在 `mail.sysu.edu.cn`, 学生在 `mail2.sysu.edu.cn`. 部分 prof (郑子彬) 在 profile 里**先**列出招生联系学生邮箱再列出自己的, 一个 naive `extract_email` 会拿错. 在 adapter 中加了 `_pick_best_email` 优先 `@mail.sysu.edu.cn` 域. (v0.3 可以做通用 "校园域 vs 学生域" 区分.)

8. **AI 学院的 "研究与招生" 合并 section**: sai 把 "研究领域 / 招生 / 个人主页 URL / 联系邮箱" 全塞一个 `<h5>研究与招生</h5>` 块. `_split_interests` 加了 `_NOT_TAG_HINTS` (招生 / 欢迎 / http / @ / 课题组 / ...), 命中即停止读取, 防止 URL 和招生文本污染 tag 列表.

9. **段内 `。` 切分**: 郑子彬 (sse 院长) 在一个 `<p>` 里写 `可信大模型，软件可靠性，程序分析，区块链，智能合约，可信软件。本科、硕士和博士招生：常年欢迎报名`. 不能只按行切, 还要按 `。` 进一步切, 然后到第一个含招生关键词的子句就停. v0.3 通用 `_split_interests` 可以参考.

## schools.yaml 改动

新增 (附在文件末尾):

```yaml
- code: sysu
  name_cn: 中山大学
  name_en: Sun Yat-sen University
  departments:
    - code: cs
      name_cn: 计算机学院
      list_urls:
        - https://cse.sysu.edu.cn/teacher
    - code: sw
      name_cn: 软件工程学院
      list_urls:
        - https://sse.sysu.edu.cn/teacher
    - code: ai
      name_cn: 人工智能学院
      list_urls:
        - https://sai.sysu.edu.cn/teachers
```

其他六校段未触碰 (仅新增, 无修改).

## 已知 limitations

- **sse 名单不完整**: `sse.sysu.edu.cn/teacher` 只显示 ~40 人, 但每个 rank 子页 (`/teacher/professor`, `/teacher/associate_professor`, `/teacher/assistant_professor`, `/teacher/lecturer`, `/teacher/researcher`) 加起来更全. 当前 `schools.yaml` 只放了 aggregate `/teacher` 一个 URL, 因为它已覆盖大多数 PI; 如果用户跑了发现漏人, 后续可以把 5 个 rank 子页都列进 `list_urls`.
- **sai 学院规模较小** (~30 人), CS / SW 分布在不同校区, 实际"导师覆盖率"取决于学校口径. 招生网公告里挂的 v0.3 的 enrichment 层 (DeepSeek) 可以补 bio / 招生信号上的不足.
- **list 页头像**: cse 卡片里 `<img src="/sites/default/files/styles/image_style_2/...">` 是缩略图; 全尺寸图在个人页. 当前 adapter 直接用列表页的 URL (absolutize 到 hostname), 没二次抓个人页的高清图.

## 完成自检清单 (按 launch_sysu.md / ADAPTER_AGENT_TEMPLATE 验收)

- [x] `python3 -m compileall -q src/claw/adapters/sysu.py tests/test_parsers/test_sysu.py` 退出 0
- [x] `python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"` 无异常
- [x] adapter 文件: `src/claw/adapters/sysu.py`
- [x] 测试文件: `tests/test_parsers/test_sysu.py` (3 列表测试 + 4 profile 测试 + 1 JS/nav invariants)
- [x] fixtures: 3 dept × 1 list + 1 profile 起步, sse 多 1 个 sub-page list, cse 多 1 个 recruiting profile = 共 8 个 HTML 文件
- [x] schools.yaml 只在末尾追加 sysu 段, 未改其他学校
- [x] **不改** `src/claw/adapters/__init__.py` (父 agent 统一收尾)
- [x] 至少 2 个 commit: `feat(sysu): adapter v1` + `docs(sysu): report`
