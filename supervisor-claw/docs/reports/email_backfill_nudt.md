# v0.5 email backfill — 国防科技大学 (NUDT)

> R1 agent E — military-restricted public web; predicted recovery
> **< 10 %**.

## 1. Baseline (锁数 2026-05-22)

| metric | value |
|---|---:|
| total advisors | **83** |
| with_email     | **0 (0%)** |
| enriched (v0.4 DeepSeek) | 79 |
| gap            | **-83** |

NUDT 是 5 校缺口里 **最绝望** 的一个: 不是 JS 加密 (xidian / hust)
也不是 TS-WAF (nwpu), 而是**公开数据里根本没邮箱**。

## 2. 缺失原因

### 2.1 上游 (v0.4 crawl) 状况

复述 `docs/reports/nudt_report.md` 的关键事实:

* 所有 83 个 advisor 来自 6 个 honour-roster 页 (院士 / 杰青 / 优青 /
  百千万 / 求是 / 全国优秀科技工作者), 路径 `www.nudt.edu.cn/szdw/...`。
* honour 页**只列姓名** —— `<a>胡德文</a>` 是 *裸 anchor* (无 href),
  `profile_url` 全部为 `None`。
* 三个 CS / AI 学院主页 (`yssz/jsjxy/`, `yssz/znkxxy/`, `yssz/xtgcxy/`)
  **不挂师资目录** —— 师资导航反链回学校层面 talent 页, 形成闭环。
* 子域 (`jsjkx.nudt.edu.cn` 等) 404 或仅内网可达。

### 2.2 v0.5 含义

* `js` 策略**不适用** —— 没有 profile 渲染。
* `wayback` 策略**不适用** —— 公开站从未存过 profile, 历史快照也是空的。
* 唯一可能的两条路:
  1. **DBLP** — 资深 NUDT CS 学者 (尤其计算机学院 / 智能科学) 在 DBLP
     有 author page, affiliation 字段偶尔带 email。
  2. **Stealth Bing** — 公开新闻 / 会议名单 / 第三方学术目录可能泄露
     `@nudt.edu.cn` 邮箱。

## 3. 选定策略

实现位置: `src/claw/enrichers/sites/nudt_email.py`

```
async def find_email(advisor, page, sess, school_name_cn) -> (email, source)
```

### 3.1 级联顺序

| 顺序 | 策略 | 调用 | 命中 source 标签 |
|---:|---|---|---|
| 1 | DBLP × 2 aff hints | `dblp_email_lookup(name, "National University of Defense Technology")` 然后 `"nudt"` | `dblp` |
| 2 | stealth Bing 级联 | `search_email_via_stealth_bing(name, "国防科技大学", domain_hint="nudt.edu.cn")` (bing → cn.bing → ddg → sogou) | `bing` |

### 3.2 不做的事

| 不做 | 原因 |
|---|---|
| JS decoder | 没有 profile 页可解 |
| wayback fallback | 公开页面从未有 email, 历史快照同样是空 |
| 内网穿透 (`yjszs.nudt.edu.cn`) | 需要校园网账号; 超出 v0.5 范围 |
| email guess (`<拼音>@nudt.edu.cn`) | 公开规则未知, SMTP 探测违反隐私 + 军校规约 |

### 3.3 picker 策略 (复用 helper)

* DBLP 命中时, `_pick_best` 优先 `nudt.edu.cn` 域; 非 nudt 的也接受
  (e.g. 早年 gmail/163 学术联系地址) 但加 INFO 级 log 留痕。
* Bing 命中时, `domain_hint="nudt.edu.cn"` 让 SERP 里的 `@nudt.edu.cn`
  优于 webmail。

## 4. 预期回收率

**< 10 %** —— 即 83 advisor 里乐观估计能拿到 **5-8 条**。

理由:

| 子集 | 估算人数 | 公开 email 概率 |
|---|---:|---|
| 院士 (lyys CAS + CAE) | 21 | 几乎 0% — 都是退役 / 不联系 |
| 国家杰青 / 优青 (gjjcqn + gjyxqn) | 33 | 中年活跃 PI, DBLP 概率最高, 5-15% |
| 百千万 / 求是 / 全国优秀 | 29 | 偏老 / 偏工程, DBLP 命中率 < 5% |

DBLP 命中率上限被 NUDT 自家保密政策卡住: 即使有 author page,
affiliation 字段也常被改成 "PLA University" / 不带 email。Bing 主要
能捞到的是国际会议 PC member 列表里的 `firstname.lastname@nudt.edu.cn`
模式 —— 但 NUDT 老师为隐私通常用 `xxx@gmail.com` 投稿, 不暴露校内邮箱。

## 5. Known limitations

1. **大多数 advisor 永远不会公开邮箱。** 这是军校通信纪律的直接结果,
   不是技术问题, **无解**。
2. **DBLP affiliation 字段不可信。** NUDT 老师有时在 DBLP 上写
   "University of Macau visiting" / "PLA" / 不写 —— 我们的 aff hint
   会漏掉这些。可考虑加 alias 列表, 但收益有限。
3. **Bing 命中存在假阳性风险。** 公开新闻里 `@nudt.edu.cn` 可能是
   学校宣传部 / 新闻办 / IT 运维 (`webmaster` 等已被 `_FOOTER_LOCAL_PARTS`
   过滤), 但导师/学生混淆仍可能发生。**人工抽检** 命中行后才能信。
4. **零 list-level email 兜底。** 列表页确认无 email, 任何
   regex-on-html 都不会救。
5. **DeepSeek enricher 也无法补救。** v0.4 已让 DeepSeek 跑过 79/83
   advisor, 仍是 0% email —— LLM 找不到不存在于 web 的信息。
6. **音译歧义。** 同名 PI 在 DBLP 上很多 (eg "李伟"), 我们靠 aff
   hint 收窄但仍会偶尔误中其他单位的 "李伟@xxx.edu.cn"。

## 6. 设计取舍备忘

| 取舍 | 决定 |
|---|---|
| 是否实现 `js` 策略? | **不**, 没有目标页面 |
| 是否实现 `wayback`? | **不**, 历史快照也是空的 |
| DBLP 顺序 vs Bing | DBLP 第一 —— 单 HTTP 调用, 高精度; Bing 兜底 |
| domain_hint 是否硬性过滤? | **不**, 软偏好 (允许非 nudt 域命中并打 INFO log) |
| 是否对 honour 子集分别取样? | **不**, 跑全量, 每个 advisor 都给两个策略一次机会 |

## 7. 运行指令

跑 NUDT email backfill 只需要 dblp + bing 两个 strategy:

```bash
uv run claw backfill-email nudt --strategy dblp,bing
```

`--dry-run` 检查候选:

```bash
uv run claw backfill-email nudt --strategy dblp,bing --dry-run
```

预期产出:

* `data/email_backfill_audit.jsonl` 新增 ~5-8 行 NUDT 记录;
* `data/email_backfill_nudt_<ts>.log` 总耗时估计 8-15 min
  (83 advisor × 2-4 search 引擎页面 × 2-5 s + DBLP API ≤ 0.5 s/aff)。

## 8. 测试

`tests/test_email_backfill/test_nudt_email.py` (≥ 4 cases):

| 测试 | 验证 |
|---|---|
| `test_find_email_dblp_hit_short_circuits_bing` | DBLP 命中 → 返回 `(email,'dblp')`, Bing **未调用** |
| `test_find_email_falls_through_to_bing_when_dblp_misses` | DBLP miss + Bing 命中 → `(email,'bing')` |
| `test_find_email_all_miss_returns_none` | 全 miss → `(None,None)`, 不抛 |
| `test_find_email_safe_when_advisor_has_no_name` | name=None → 立即返回, 不调用 helper |

测试用 `monkeypatch` 替换 `dblp_email_lookup` / `search_email_via_stealth_bing`
两个 helper, 不依赖真实 Playwright / DBLP / 网络。

## 9. 上下文 / 复用

* 实际 dblp / bing 实现复用 `src/claw/enrichers/email_backfill.py` 里
  Phase 0 的两个 helper (`dblp_email_lookup`, `search_email_via_stealth_bing`),
  本模块不重复实现。
* `_pick_best` (在 `email_backfill.py` 内部) 处理候选排序: domain_hint →
  academic TLD → 第一个非 footer。本模块不需要自己写。
* 一旦 R1 父 agent 把所有 5 校 merge 到 main, 跑总指令
  (`for s in ... ; do uv run claw backfill-email $s ...`) NUDT 这条
  应该是 5 校里**最快出来**的 (advisor 少 + 多数没命中)。

## 10. 验收 checklist

- [x] 不动 `src/claw/adapters/nudt.py`
- [x] 不动 `schools.yaml`
- [x] 不动 DB
- [x] 新增 `src/claw/enrichers/sites/nudt_email.py` 导出 `find_email`
- [x] 新增 `tests/test_email_backfill/test_nudt_email.py` (4 cases)
- [x] `python3 -m compileall -q supervisor-claw/src/claw` 退出 0
- [x] 不 `uv sync` / 不 `pip install`
- [x] 不 push
