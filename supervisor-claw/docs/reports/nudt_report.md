# supervisor-claw v0.4 — 国防科技大学 (NUDT) adapter report

## 1. TL;DR

NUDT 是 PLA 直属军校 (国防七子 之首), 公开 web 几乎不暴露师资。我们因此
**放弃**按学院 (计算机学院 / 智能科学学院 / 系统工程学院) 拆 dept 的
常规策略, 改用 **honour-roster aggregation**: 把学校层面 `www.nudt.edu.cn/szdw/`
下 6 个 talent-roster 页面合并到单个 synthetic ``cs`` department,
共采集 **133 条 ListItem**, 经 pipeline (school, name_cn, email) 去重后
≈ **83 unique PI** —— 对一所军校的公开数据已是上限。

## 2. Coverage summary

| 入口 | 标题 | List parse 数 | 备注 |
|------|------|---------------|------|
| `/szdw/lyys/index.htm`            | 两院院士 (CAS + CAE) | **21** | h3 分 CAS/CAE 两 section |
| `/szdw/gjjcqnkxjjhdz/index.htm`   | 国家杰青             | **17** | title 带当选年, eg `国家杰青(2020)` |
| `/szdw/gjyxqnkxjj/index.htm`      | 国家优青             | **16** | title 带当选年 |
| `/szdw/bqwrcgcgjjrx/index.htm`    | 百千万人才工程       | **34** | 单 honour 标签, 年份分散不收入 title |
| `/szdw/gjkxqsjcqnsygcjhdz/index.htm` | 求是杰出青年实用工程奖 | **23** | 单 honour 标签 |
| `/szdw/qgyxkjgzz/index.htm`       | 全国优秀科技工作者   | **22** | 单 honour 标签 |
| **总计 (页面级)** |  | **133** |  |
| **去重后 PI** | unique name_cn | **~83** | pipeline (school, name_cn, email) 去重 |

## 3. 关键设计决策

### 3.1 为何只挂 1 个 dept (`cs`)

三个候选学院主页 (`yssz/jsjxy/` 计算机学院 / `yssz/znkxxy/` 智能科学
学院 / `yssz/xtgcxy/` 系统工程学院) 都**没有院内师资入口** —— 师资
导航栏全部反向链接到学校层面的 6 个 talent-roster 页 (`../../szdw/...`)。
honour-roster 页本身也**不写学院归属**, 所以即使按部门拆 dept 也无法
做正确分组。统一挂在 `cs` 下, 后续由 v0.3 DeepSeek enricher 通过
Baidu Scholar / 公开新闻交叉验证补 affiliation。

### 3.2 为何 `profile_url=None`

honour-roster 页的 `<a>胡德文</a>` **没有 href** (literally bare anchor),
NUDT 全网都不提供 per-PI profile 页 (军校通信纪律)。这意味着:

* `parse_list` 永远返回 `profile_url=None`;
* pipeline 的 `if item.profile_url:` 短路, **跳过 profile fetch**;
* `parse_profile` 仅做 safety-net (futures-enricher 合成 URL 时不崩);
* 全表 0% email / 0% research_interests / 0% photo_url —— 这是
  acceptable, 在 known limitations 里明示。

### 3.3 Title 编码策略

| 来源 | title 样例 | 备注 |
|------|-----------|------|
| `lyys` CAS 段 | `中国科学院院士` | h3 section 标签 |
| `lyys` CAE 段 | `中国工程院院士` | h3 section 标签 |
| `gjjcqn`      | `国家杰青(2020)` | 加当选年 |
| `gjyxqn`      | `国家优青(2017)` | 加当选年 |
| `bqwrc`       | `百千万人才工程国家级人选` | 不加年 (太分散) |
| `kxqs`        | `求是杰出青年实用工程奖` | 不加年 |
| `qgyx`        | `全国优秀科技工作者` | 不加年 |

同一人在多页出现时 (eg 胡德文 ∈ lyys ∩ qgyx), adapter **不做跨页去重**
—— pipeline 的 `(school, name_cn, email)` upsert 会决定最终保留哪个
title (最后处理的 list_url 胜出)。我们按惯例把 `lyys` 放在
schools.yaml 第一位, 让最高荣誉 (院士) 优先固化。

### 3.4 HTML 结构 (供 v0.5 reference)

```
<div class="faculty"|"academician">
  <h3 class="gp-f24">中国科学院院士</h3>     <!-- 只在 lyys/ 出现 -->
  <ul class="facultyList1">
    <li class="gp-f24">
      <div class="year">2025年</div>
      <div class="teachers">
        <a>胡德文</a>
        <a>李四</a>                          <!-- 同年可多名 -->
      </div>
    </li>
    ...
  </ul>
  <h3 class="gp-f24">中国工程院院士</h3>     <!-- 第二 section -->
  <ul class="facultyList1">...</ul>
</div>
```

`adapter._parse_talent_list` 用 `container.traverse(include_text=False)`
按 document order 走, 维护 `current_section`, 遇到 h3 in
`_LYYS_SECTION_TITLES` 就切换 section。这复制了清华 adapter 的
"h3 + 后续 ul" 套路。

## 4. Known limitations

1. **覆盖范围 = honours-only。** 非荣誉头衔的多数 PI (普通教授 / 副教授 / 讲师)
   一律不可达。83 个 unique PI 远低于 NUDT 实际 CS/AI 教研规模 (估计 200+),
   但这是公开数据极限。
2. **零 profile pages。** 任何 v0.3/v0.5 enrichment 都要从外部源 (Baidu Scholar /
   DBLP / 公开新闻) 反向解析, 不能依赖 NUDT 自家站点。
3. **零 email coverage。** NUDT 公开页面不暴露 `@nudt.edu.cn` 邮箱 (军校通信
   纪律)。v0.3 DeepSeek enricher 也大概率拿不到, 建议把 NUDT 行的
   `email_obfuscated` 标 True 让用户手填。
4. **无学院归属信息。** Honour 页只列名字 + 年份, 不写所属学院。pipeline
   会把所有 NUDT advisor 统一挂在 dept=`cs` 下, 实际可能横跨 计算机 /
   智能科学 / 系统工程 / 电子科学 / 前沿交叉 几个学院。v0.3 enricher
   需要做学院归属解析。
5. **无招生信号。** `raw_quota_text` / `is_recruiting` 永远 None。
6. **历史性头衔噪声。** lyys 页含 1980 当选的慈云桂、1993 周兴铭等先生
   (大概率已离世或退休); adapter 不做过滤, 让用户在数据库层用 elected_year
   切。但 elected_year 当前没存进 AdvisorPartial (没有这个字段), 仅在
   title 里作为 `(2020)` 后缀。

## 5. 三所目标学院主页 — 都没有师资目录

为完整起见, 我们抓了三个 CS/AI 学院主页 fixture
(`page_jsjxy_home.html`, `page_znkxxy_home.html`) 并人工验证:

| 学院 | URL | 师资入口 |
|------|-----|---------|
| 计算机学院 | `yssz/jsjxy/index.htm` | 无 —— 仅反链 `../../szdw/...` |
| 智能科学学院 | `yssz/znkxxy/index.htm` | 无 —— 同上 |
| 系统工程学院 | `yssz/xtgcxy/index.htm` | 无 (未抓 fixture, WebFetch 验证) |

v0.5 若要扩展, 候选方向:

* Playwright 走研究生院 `yjszs.nudt.edu.cn` (导师名单可能有但需登录);
* 反向爬 NUDT 在 DBLP / Baidu Scholar / 中国知网 上的论文 author 列表
  → 反推 PI;
* 联系学校信息公开办公室申请 FOIA-style faculty list (理论可行, v0.5 之外)。

## 6. 测试矩阵 (tests/test_parsers/test_nudt.py)

| 测试 | 验证项 |
|------|-------|
| `test_parse_list_lyys_two_sections` | h3 section split (CAS 10 + CAE 11), profile_url=None |
| `test_parse_list_gjjcqn_includes_year_in_title` | 年份后缀 `(2020)` 覆盖率 100% |
| `test_parse_list_bqwrc_flat_honour` | 单 honour 标签无年份后缀 |
| `test_parse_list_qgyx_basic` | 22 条 ≥10 (≥5 即可) |
| `test_parse_list_handles_unknown_host` | non-NUDT URL → 空列表 |
| `test_parse_list_handles_empty_html` | 空 body → 不崩 |
| `test_parse_profile_safe_with_none_profile` | safety-net, 有 title 时产 bio_text |
| `test_parse_profile_safe_with_empty_list_item` | 无 title 时 bio_text=None |
| `test_dedupe_across_pages_is_pipeline_concern` | 跨页同名不去重, 留 pipeline 处理 |

两个 `pytest.mark.skip` 占位:
* `test_profile_bio_from_real_html` — 等 v0.5 Playwright 内网穿透;
* `test_email_coverage` — NUDT 公开 web 永远拿不到。

## 7. Fixtures

`tests/fixtures/nudt/`:

* `list_lyys.html`         — 两院院士 page (36 KB)
* `list_gjjcqn.html`       — 国家杰青 page (38 KB)
* `list_gjyxqn.html`       — 国家优青 page (36 KB)
* `list_bqwrc.html`        — 百千万人才 page (40 KB)
* `list_kxqs.html`         — 求是杰青 page (40 KB)
* `list_qgyx.html`         — 全国优秀科技工作者 page (37 KB)
* `page_jsjxy_home.html`   — 计算机学院主页 (41 KB, 供 v0.5 参考)
* `page_znkxxy_home.html`  — 智能科学学院主页 (40 KB, 供 v0.5 参考)

所有 fixture 通过 VPS `curl` 实抓 (UA `supervisor-claw/0.1 (research)`),
2026-05-20 当天快照, HTTP 200, 平均 38 KB。
