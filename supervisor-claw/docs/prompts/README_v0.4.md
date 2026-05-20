# v0.4 adapter rollout — 13 所新学校的 launch prompt

承接 v0.2 (清华/北大/南大/中科大/浙大/复旦/上交) 之后再扩 13 所；总 7 + 13 = **20 所**。

```
~/Projects/
├── claude-tools/                  ← main 仓库
└── adapter-<school>/              ← 每个 agent 自己的 worktree
```

## 13 所新学校的 prompt 文件

按难度从低到高排（建议先跑简单的练手）：

| 学校 | code | prompt 文件 | 预期 PI 数 | 难度 |
|---|---|---|---|---|
| 上海科技大学 | shtech | `launch_shtech.md` | ~100 (SIST 大院) | 🟢 易（单院、中英双轨） |
| 中山大学 | sysu | `launch_sysu.md` | ~70 (3 院跨校区) | 🟢 易 |
| 北京航空航天大学 | buaa | `launch_buaa.md` | ~80 (3 院) | 🟢 易 |
| 华中科技大学 | hust | `launch_hust.md` | ~120 (cs 大院 + ai) | 🟡 中（cs 量大） |
| 西安交通大学 | xjtu | `launch_xjtu.md` | ~80 (gr.xjtu 统一框架) | 🟡 中 |
| 南开大学 | nankai | `launch_nankai.md` | ~50 (4 院) | 🟡 中 |
| 电子科技大学 | uestc | `launch_uestc.md` | ~80 (3 院) | 🟡 中（改版频繁） |
| 天津大学 | tju | `launch_tju.md` | ~60 (单学部多子系) | 🟡 中（合并去重） |
| 北京理工大学 | bit | `launch_bit.md` | ~60 (3 院) | 🟡 中（多分类去重） |
| 东南大学 | seu | `launch_seu.md` | ~80 (3 院多校区) | 🟠 偏难（SPA 风险） |
| 西安电子科技大学 | xidian | `launch_xidian.md` | ~80 (cs+cse+ai) | 🟠 偏难（GBK 编码） |
| 西北工业大学 | nwpu | `launch_nwpu.md` | ~50 (3 院) | 🔴 难（国防访问限制） |
| 国防科技大学 | nudt | `launch_nudt.md` | 10-20 (公开有限) | 🔴 难（军校，多数字段空） |

## 建议的批次分组

13 个一次起容易管不过来，分成 **3 批 × 4-5 个** 起：

### 批次 A — 简单暖场（4 个，并行）
shtech / sysu / buaa / hust

### 批次 B — 中等（5 个，并行）
xjtu / nankai / uestc / tju / bit

### 批次 C — 难（4 个，并行）
seu / xidian / nwpu / nudt

每批跑完一起 merge，避免 schools.yaml 大冲突。

## 启动流程（同 v0.2）

对每个学校，开一个新的 Claude Code 会话，把对应 prompt 粘进去。agent 会：

1. 创建分支 `feat/adapter-<code>`
2. 创建自己的 worktree `~/Projects/adapter-<code>`
3. 读 `docs/ADAPTER_AGENT_TEMPLATE.md`（**共用知识**）
4. 调研 → 写 adapter → 写测试 → commit + 报告

## 与 v0.2 的差异

v0.4 这一波 vs v0.2 的关键区别：

1. **新学校在 `schools.yaml` 里没有段** —— agent 需要**新增** `<school>` 段，不是改已有段
2. **POST/AJAX 列表支持已就位** —— `ListUrlSpec` + `Fetcher.post`（参照 sjtu adapter）；遇到纯 AJAX 列表别再写自定义 hack
3. **v0.3 agent enrichment 已上线** —— adapter 阶段只需拿 name/email/title/bio 等基础字段，**招生 / 评价**这些交给 enricher 联网搜
4. **质量目标可以更松** —— 难度高的学校（nudt / nwpu）接受 known limitations 而不是强行拉满

## 13 个全跑完后你做的事

```bash
cd ~/Projects/claude-tools

# 1. 看每个 agent 的报告
for s in shtech sysu buaa hust xjtu nankai uestc tju bit seu xidian nwpu nudt; do
  echo "=== $s ==="
  cat ~/Projects/adapter-$s/supervisor-claw/docs/reports/${s}_report.md 2>/dev/null \
    | head -30
done

# 2. fetch 每个 worktree 的分支到 main repo
for s in shtech sysu buaa hust xjtu nankai uestc tju bit seu xidian nwpu nudt; do
  git fetch ~/Projects/adapter-$s feat/adapter-$s:feat/adapter-$s
done

# 3. 按批次 merge（按上面的 A/B/C 顺序，每批后跑一次 pytest）
# schools.yaml 几乎肯定要手工合并冲突（每个 agent 都加了自己的段）
# 一种省心的合 yaml 法：每批 merge 后人工把 schools.yaml 整理成单文件，
# 然后 git add schools.yaml && git commit -m "chore: merge schools.yaml after batch X"

# 4. 一次性加 13 行 import 到 src/claw/adapters/__init__.py
#    (从现有 7 行扩到 20 行)

# 5. 跑全部测试
cd supervisor-claw && uv run pytest tests/ -v

# 6. 清掉 worktree
cd ~/Projects/claude-tools
for s in shtech sysu buaa hust xjtu nankai uestc tju bit seu xidian nwpu nudt; do
  git worktree remove ~/Projects/adapter-$s 2>/dev/null || true
done

# 7. push
git push origin main
```

## 完成后的全景

7 + 13 = 20 所学校覆盖：

```
v0.1: tsinghua
v0.2: pku  nju  ustc  zju  fudan  sjtu
v0.4: shtech sysu buaa hust xjtu nankai uestc tju bit seu xidian nwpu nudt
```

20 校 × 平均 50 PI ≈ **1000+ advisors** 落库，是个能真正用于 PhD 投递筛选的数据集。
