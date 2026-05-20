# supervisor-claw v0.4 — 西安电子科技大学 adapter agent

## 你的标识

- **学校 code**: `xidian`
- **学校名**: 西安电子科技大学
- **要支持的学院**: `cs` (计算机科学与技术学院), `cse` (网络与信息安全学院 / 网安学院), `ai` (人工智能学院)
- **分支**: `feat/adapter-xidian`
- **worktree 路径**: `~/Projects/adapter-xidian`

> 西电是国内信息安全 / 网安方向最强的高校之一，`cse` 学院师资远超普通学校；`ai` 学院 2017 建院，方向偏遥感 / 计算视觉。

## Step 0 — worktree

```bash
cd ~/Projects/claude-tools && git fetch origin
git worktree add ~/Projects/adapter-xidian -b feat/adapter-xidian origin/main
cd ~/Projects/adapter-xidian
```

## Step 1 — 必读

1. `supervisor-claw/docs/ADAPTER_AGENT_TEMPLATE.md`
2. `supervisor-claw/src/claw/adapters/tsinghua.py` / `pku.py`（多 dept 路由参考）
3. `supervisor-claw/src/claw/adapters/sjtu.py`（POST AJAX 参考）
4. `supervisor-claw/src/claw/adapters/base.py` + `models/pydantic_models.py`
5. `supervisor-claw/schools.yaml`（新增 xidian 段）

## Step 2 — 调研

候选入口：

- 计算机学院: `cs.xidian.edu.cn` / `web.xidian.edu.cn/szdw/`
- 网安学院: `cyber.xidian.edu.cn`
- 人工智能学院: `iaiac.xidian.edu.cn` 或 `sai.xidian.edu.cn`

**注意**：西电二级网站建站时间长，**编码可能是 GB2312 或 GBK**。读 fixture 时 `curl` 加 `--compressed` 后看响应头，必要时在 adapter 里对原始 bytes 调用 `.decode('gbk')` 而不是默认 utf-8。或者依靠 httpx 自动检测，但写 parse 时要兼容混合编码。

新增 `schools.yaml`：

```yaml
- code: xidian
  name_cn: 西安电子科技大学
  departments:
    - { code: cs, name_cn: 计算机科学与技术学院, list_urls: [<URL>] }
    - { code: cse, name_cn: 网络与信息安全学院, list_urls: [<URL>] }
    - { code: ai, name_cn: 人工智能学院, list_urls: [<URL>] }
```

抓 fixture：

```bash
mkdir -p tests/fixtures/xidian
curl -sSL "<URL>" -o tests/fixtures/xidian/list_<dept>.html
file tests/fixtures/xidian/list_<dept>.html  # 看编码
```

## Step 3 — 实现

`src/claw/adapters/xidian.py`，按 url 路由 dept；如果某个 dept 编码不同，**在 parse_list 入口处**做一次 `if 'charset=gbk' in html_head: html = raw_bytes.decode('gbk')` 兜底（更标准做法是在 `core/http.py` 自动检测，但本期别动 core）。

## Step 4 — 测试

`tests/test_parsers/test_xidian.py`：

1. `test_parse_list_per_dept`：3 个 dept 各 ≥ 10 人，姓名 100%
2. `test_email_obfuscation_handling`：cse 学院可能用图片邮箱，断言 `email_obfuscated=True` 的比例 ≥ 10%
3. `test_encoding_safety`：bio_text 不含 `�`（解码失败的替换字符）

## Step 5/6 — 自查 + commit + 报告

```bash
python3 -m compileall -q src/claw/adapters/xidian.py tests/test_parsers/test_xidian.py
git add src/claw/adapters/xidian.py tests/ schools.yaml
git commit -m "feat(xidian): 西安电子科技大学 adapter v1

- depts: cs, cse, ai
- fixtures: ...
- handles: GBK 编码兜底 / 多三级子域"
```

报告：`docs/reports/xidian_report.md`。

## 该校特殊点

- **GBK 编码**：西电老站可能不是 UTF-8，注意解码
- **网安学院**：cse 师资规模可能比 cs 还大
- **图片邮箱**：cse / ai 学院老师邮箱可能是图片渲染，解不出来标 `email_obfuscated=True`，等 v0.3 enrichment agent 网络补
- **分页**：西电列表常用 `?currPage=N` 或 `_N.htm`

## 严禁

同 v0.2 模板。

## 完成的标志

报告写满；schools.yaml 有 xidian 段；fixtures 至少每 dept 1 个 list + 1 个 profile。
