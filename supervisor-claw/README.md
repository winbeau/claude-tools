# supervisor-claw

本地工具：抓取 7 所中国顶尖高校（清华、北大、南大、中科大、浙大、复旦、上交）**计算机/AI 方向**导师的公开信息，落地到 SQLite，提供 CLI + 本地 Web UI 查询。

## 数据范围
- 来源：学院官网 + 第三方源（评价网/知乎/Bing，需 Playwright + 人工辅助通过登录或验证）
- 字段：姓名、职称、研究方向、工作邮箱、办公电话、个人主页、招生信号 (is_recruiting + raw_quota_text)、评价（用户手动导入或第三方源）

## 使用条款（必读）
- 仅作**个人学术参考**用途
- **禁止**公开发布、聚合分发、二次售卖本工具采集的数据
- **禁止**将 Web UI 公开部署到外网（默认仅监听 127.0.0.1）
- 所有抓取遵守目标站 `robots.txt`，限速 ≤ 0.5 req/s/域名
- 评价类内容来自第三方公开渠道，不代表事实，保留原始来源链接以备溯源
- 仅采集**公开发布的职务联系信息**（工作邮箱、办公电话），不采集私人手机/家庭地址等
- 涉及个人信息处理，遵守《中华人民共和国个人信息保护法》

继续使用本工具即视为同意以上条款。

## 安装（用户本地执行，VPS 上不要跑）
```bash
cd supervisor-claw
uv sync
uv run playwright install chromium     # v0.4 起需要
cp .env.example .env                   # 填 DEEPSEEK_API_KEY 等
```

## 常用命令
```bash
uv run claw doctor                                   # 自检
uv run claw crawl tsinghua --dept cs --limit 5       # 抓清华 CS 前 5 人
uv run claw crawl all                                # 抓全部
uv run claw enrich --source quota                    # DeepSeek 抽招生信号
uv run claw enrich --source web --interactive        # Playwright 抓全网（需人工辅助）
uv run claw login zhihu                              # 交互登录知乎
uv run claw import evaluations.csv                   # 手动导入评价
uv run claw export --format csv                      # 导出
uv run claw stats                                    # 覆盖率
uv run claw serve                                    # 启 Web UI (127.0.0.1:8787)
```

## 开发
```bash
uv run pytest tests/                                  # 单元测试（用 fixtures）
```

## 项目状态
v0.1：骨架 + 清华 CS 系跑通。后续 v0.2~v0.5 见 `/home/winbeau/.claude/plans/precious-mixing-panda.md`。
