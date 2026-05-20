# v0.3 enrich — pku 全量 agent enrichment 收尾报告

> 仓库：`supervisor-claw`
> 日期：2026-05-20
> 范围：pku 207 advisors，DeepSeek tool-calling + Playwright (cn.bing.com) 自主 enrichment

## 1. 数据成果

```
advisors total: 207 · enriched: 206 (99.5%)

recruiting:    招=155   未知=47   不招=5
reputation:    positive=88   neutral=30   negative=12   unknown=76
confidence ≥ 0.7:  121 / 207 (58%)
evidence:      452 evaluation rows · 511 quota_info rows
```

### 1.1 per-dept 拆分

| dept | n | enriched | 招 | 不招 | positive | negative | conf≥0.7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| eecs 信科 | 119 | 119 | 94 | 0 | 51 | 9 | 67 |
| ai 智能学院 | 36 | 36 | 31 | 1 | 16 | 2 | 25 |
| wangxuan 王选所 | 30 | 30 | 23 | 4 | 14 | 1 | 26 |
| cfcs | 32 | 31 | 16 | 0 | 12 | 0 | 11 |
| **合计** | **207** | **206** | **155** | **5** | **88** | **12** | **121** |

- 1 个未 enrich（agent crash 后未能重试到 submit_report）
- 5 个 "不招" — 多在王选所，agent 在官方招生名单中没找到该导师 → 判 false

### 1.2 negative 风评导师全名单（投递避雷参考）

> ⚠️ 这 12 位老师的"负面"标签**完全由 agent 综合公开评价网站 + 知乎 + 论坛得出**，不代表任何客观结论，仅作为初筛参考。**强烈建议在做决策前，至少和 1-2 位该导师的在校/毕业学生当面沟通**。

| # | 姓名 | 院 | 职称 | 招生 | conf | 主要风评信号（agent summary 节选） |
|---|---|---|---|---|---:|---|
| 1 | 彭宇新 | eecs | 教授 | 招 | 0.9 | 评分 1.3/5；工作强度大（早 8 晚 11）；指导偏宏观 |
| 2 | 易江芳 | eecs | 副教授 | 招 | 0.8 | 管理严格（钉钉打卡）、不允许实习、补助偏低 |
| 3 | 李胜 | eecs | 研究员 | 招 | 0.8 | 评分 1.9/5；管理严格 |
| 4 | 肖臻 | eecs | 研究员 | 招 | 0.75 | 评分 0.8/5（8 条评价）；经费有限、补助延迟 |
| 5 | 王腾蛟 | eecs | 教授 | 招 | 0.7 | 评分 1.8/5；补助偏低（~1100/月）、实习受限 |
| 6 | 田永鸿 | eecs | 教授 | 招 | 0.7 | 评分 1.4/5（13 条评价）；知乎有学生差评 |
| 7 | 汪国平 | eecs | 教授 | 招 | 0.7 | 评分 0.8/5；管理严格、以毕业施压、补助低 |
| 8 | 段凌宇 | eecs | 教授 | 招 | 0.7 | 评分 1.2/5；学术指导能力受质疑、频繁换课题 |
| 9 | 李红燕 | ai | 教授 | 招 | 0.7 | 评分 1.0/5；团队论文层次不高、经费紧 |
| 10 | 刘先华 | eecs | 教授 | 招 | 0.6 | 评分 1.0/5；论文产出低、考勤严格 |
| 11 | 吴玺宏 | ai | 教授 | 招 | 0.6 | 历史上有实验室管理严格、师生冲突的报道；近年缓和 |
| 12 | 佟冬 | eecs | 副教授 | 未知 | 0.2 | 2013 年有学生退学控诉；研桔评价偏负面 |

每位详细 evidence 见详情页（`evaluation` 表 + 各 `source_url`）。

## 2. 成本

### 2.1 三段成本分解

| 段 | jsonl | advisors | tokens (in/out) | 成本 |
|---|---|---:|---|---:|
| 首跑 (181 完成后被 tmux 超时杀) | `20260520T081244Z.jsonl` | 181 | 7.73M / 0.32M | **¥17.98** |
| ⚠️ 浪费的 2nd 跑（auto-recover 重启，全是重复） | `20260520T094258Z.jsonl` | 42 | 1.68M / 0.07M | ¥3.92 |
| 收尾（only-missing 模式，含部分 no-report 重试）| `20260520T100851Z.jsonl` | 32 | 1.29M / 0.05M | ¥3.02 |
| **总计** | | **255 records** | **10.7M / 0.44M** | **¥24.92** |

### 2.2 单位经济

- **有效 advisor 成本**：¥21 / 207 ≈ **¥0.10 / advisor**（不算浪费）
- **平均 token 用量**：53K in / 2.1K out per advisor（max_iter=6）
- **平均 wall time**：29s / advisor at concurrency=2

### 2.3 7 校全量预估

- 7 × 207 ≈ 1450 advisors（粗估）
- 全量一轮：**¥145 / 11 小时 wall time**
- 若开 concurrency=4：5.5 小时（DeepSeek 限流上限附近）

## 3. 工程教训（直接转化为 v0.3.1 fix）

### 3.1 长跑 + tmux-inject 超时 = 悲剧

- 首跑预估 73 min，实际 91 min，撞 tmux-inject 默认 `-t 5400` (90 min) 上限
- tmux-inject 的 `--auto-recover` 看到超时后**用同一条命令重启**（`--all` 也照样重启）→ 重做 42 个 ≈ ¥4 浪费

### 3.2 `--all` 是危险开关

- 默认 `--only-missing` 是安全的，但**首跑被 kill 后再起，必须再次保证用 only-missing**
- 我们用了 `--all` 因为首次跑想做全量验证，但这导致 auto-recover 重启时也带着 `--all`，自然全部重做

### 3.3 没有任何启动校验

- 启动时只打印 "candidates=207"，没有显示"其中已 enriched=N，pending=N"
- 用户（包括 agent 自己）很难一眼看出"这次启动会做多少工作 / 重复多少"

## 4. v0.3.1 需要解决的（次轮立即做）

1. **断点回复**：lock file + 启动前对账 + 明确的 `--resume` 语义
2. **明确的 redo 语义**：把 `--all` 重命名为 `--force-redo`（保留 `--all` 作为 alias），并加大警告
3. **启动 banner**：
   ```
   pku: 207 advisors
     already enriched (last 30d): 181 → skip
     pending: 26
   proceed? (or pass --force-redo to redo enriched)
   ```
4. **lock file 互斥**：`data/enrich.lock` 写入 PID；二次启动检测到活进程立即拒绝

## 5. 数据质量观察

### 5.1 agent 的"模糊"行为

- 真正"不招"判定为 false 的只有 5 个，因为公开列表只有"招"和"没说"，没有"明确不招"
- **未知 47 个**有些其实是 unknown reputation：agent 找不到学生评价就标 unknown，confidence 通常 < 0.5

### 5.2 evidence sources 实际有效域名（top 5）

| 来源 | evaluation 行数 |
|---|---:|
| cs.pku.edu.cn | 54 |
| www.zhihu.com | 39 |
| yanjubian.com | 33 |
| daoshipingjia.net | 29 |
| zhuanlan.zhihu.com | 22 |

> 知乎主站 + 专栏合计 **61 条** = 总 evaluation 的 13%，是仅次于 pku 官网的第二大源。小红书 0 命中（system prompt 没列）。

### 5.3 typical agent run trace（一个 advisor）

```
iter 1  search  "<name> 北京大学 评价 知乎"      → 5 results
        search  "<name> 北大 招生 博士"          → 5 results
iter 2  read    cs.pku.edu.cn/<dept>/<name>.htm  → 4500 chars
        read    zhuanlan.zhihu.com/<post>        → 4800 chars
iter 3  search  "<name> 导师 评价 小木虫"        → 5 results
iter 4  read    yanjubian.com/t/topic/<id>       → 3200 chars
iter 5  submit  evaluation × 2 (1 zhihu, 1 yanjubian)
        submit  quota × 1 (PhD 2026 conf=0.7)
final   submit_report (招生·positive·conf=0.7)
        finish
```

## 6. 下一步

按优先级：

1. **v0.3.1 断点回复**（紧迫，避免后续学校重蹈覆辙）
2. **v0.4 批 A 启动** — shtech / sysu / buaa / hust 4 个简单学校（adapter 阶段）
3. **v0.4 enrich 全量铺开** — 等 adapter 完成后跑 enrichment，预计 ¥145 / 11h
4. **v0.5 Web UI** — 用户已有 Claude Design 提示词草案，等数据齐了启动
