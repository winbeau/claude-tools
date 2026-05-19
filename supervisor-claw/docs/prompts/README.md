# v0.2 adapter rollout — 6 个 prompt 怎么用

## 总思路

6 所学校 → 6 份独立 prompt → 6 个 Claude Code 会话**各自启动一个 agent**。每个 agent 通过 **git worktree** 隔离工作：

```
~/Projects/
├── claude-tools/                  ← main 仓库（你不要在这里干活）
└── adapter-<school>/              ← 每个 agent 在自己的 worktree 里
    └── supervisor-claw/...        ← agent 实际改的代码
```

worktree 之间互不干扰，6 agent 并行没有 race。完成后所有 agent 把 commit 留在自己分支上，由你统一 merge。

## 启动顺序

对每所学校，开一个新的 Claude Code 会话，把对应 prompt 粘进去：

| 学校 | prompt 文件 |
|---|---|
| 北京大学 | `launch_pku.md` |
| 南京大学 | `launch_nju.md` |
| 中国科学技术大学 | `launch_ustc.md` |
| 浙江大学 | `launch_zju.md` |
| 复旦大学 | `launch_fudan.md` |
| 上海交通大学 | `launch_sjtu.md` |

6 个会话同时跑，预计每 agent 30-60 分钟（视该校页面复杂度）。

## 完成的标志（每个 agent 自己满足）

每个 agent 跑完会：
1. 创建分支 `feat/adapter-<SCHOOL_CODE>`，至少 2 个 commit（adapter 代码 + 完成报告）
2. 落盘报告到 `supervisor-claw/docs/reports/<SCHOOL_CODE>_report.md`（包含分支名、commit hash、fixture 覆盖率、特殊结构观察、schools.yaml diff）
3. **不**推到 origin —— 你手动合并

## 6 个 agent 全跑完后你做的事

```bash
cd ~/Projects/claude-tools

# 1. 看每个 agent 的报告（worktree 里的）
for s in pku nju ustc zju fudan sjtu; do
  echo "=== $s ==="
  cat ~/Projects/adapter-$s/supervisor-claw/docs/reports/${s}_report.md
done

# 2. 把每个 worktree 的分支拉进 main repo
for s in pku nju ustc zju fudan sjtu; do
  git fetch ~/Projects/adapter-$s feat/adapter-$s:feat/adapter-$s
done

# 3. 逐个合并（schools.yaml 可能有冲突，手工解）
for s in pku nju ustc zju fudan sjtu; do
  git merge feat/adapter-$s -m "merge: $s adapter"
done

# 4. 统一加 6 行 import 到 src/claw/adapters/__init__.py
#    (从 1 行 tsinghua 扩到 7 行)

# 5. 跑全部测试
cd supervisor-claw && uv run pytest tests/ -v

# 6. 清掉 worktree
cd ~/Projects/claude-tools
for s in pku nju ustc zju fudan sjtu; do
  git worktree remove ~/Projects/adapter-$s
done

# 7. push 一次
git push origin main
```

## 为什么不用 Claude Code Agent 工具一次起 6 个？

理论上 Agent 工具 + `isolation: "worktree"` 也能一条消息并发起 6 个 subagent —— 但你的环境限制了无法这么用。落盘 6 份 prompt 是后备方案：你可以在不同地方（不同终端、不同账号、不同设备）开多个 Claude 会话粘 prompt 跑，节奏完全你说了算。

## 共用知识

每份 prompt 都会让 agent 先读 `docs/ADAPTER_AGENT_TEMPLATE.md`，那里有：
- 完整接口规范
- 质量目标
- **11 个清华踩过的坑**（必看）
- 测试 / commit 约定

prompt 本体只写**该校特有的东西**和 worktree setup —— 通用部分都在 ADAPTER_AGENT_TEMPLATE.md。
