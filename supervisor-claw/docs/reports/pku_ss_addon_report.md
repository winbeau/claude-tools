# PKU v0.4.1 addon — 软件与微电子学院 (ss.pku.edu.cn)

## 来由

v0.4 主推 7 校 adapter 时，PKU 只覆盖了 `eecs / ai / wangxuan / cfcs` 四个学院。
软件与微电子学院 (Software & Microelectronics, `ss.pku.edu.cn`) 是 PKU 与 CS/AI
强相关、但 v0.4 漏掉的一个学院 —— 部分 CS 老师 (黄罡 / 谢冰 / 张路 / 张世琨 等)
有合署岗位放在这里，电子信息博士桶里还挂着 梅宏 / 谢涛 / 黄铁军 / 林宙辰 /
田永鸿 等知名 PI（多数是双聘）。本次 v0.4.1 就是补回这个 dept。

## 改动文件

- `src/claw/adapters/pku.py`
  - 在 `_dept_from_url` 加 `ss.pku.edu.cn` 分支（位置在 `cs.pku` 之前，
    避免 `s.pku` 后缀子串冲突）。
  - 新增 `_parse_list_ss` / `_profile_ss`。
  - `supports` 集合扩到 `{eecs, ai, wangxuan, cfcs, ss}`；`parse_list` /
    `parse_profile` 加 ss 分支。
- `schools.yaml` 的 `- code: pku` 段尾追加 `- code: ss` dept，2 个 list_urls：
  - `https://ss.pku.edu.cn/sztd/xssbsz/index.htm` (工学博士桶)
  - `https://ss.pku.edu.cn/sztd/qygcbssz/index.htm` (电子信息博士桶)
- `tests/fixtures/pku/`：新增 4 个 fixture（list × 2 + profile × 2）。
- `tests/test_parsers/test_pku.py`：新增 4 个测试用例。

整个 PKU 仍然一个 adapter 文件 (`pku.py`)，没有新建 `ss.py`。

## 页面结构观察

### List page (`sztd/<rank>/index.htm`)

每个 PI 是一个 `<li>`，里面是：

```html
<li>
  <a [href="<32hex>.htm"]>
    <div class="name">
      <span class="name-text fs19">姓名</span>
    </div>
  </a>
</li>
```

**坑点**：很多 `<li>` 的 `<a>` 是没有 `href` 的，因为合署/双聘老师在 ss.pku
上没有独立 profile 页 —— 但人是真实存在的。adapter 把这些以 name-only
`ListItem(profile_url=None)` 收下，不让它们被丢掉。pipeline 在
`(school, name, email)` 层面去重时会合并到本部那条记录。

### Profile page

所有 ss.pku profile 都用同一个 `div.left-info` 容器，结构是：

```html
<div class="left-info">
  <div class="zw fs16">博士 教授 博士生导师 ...</div>       <!-- 学位/职称 line -->
  <div class="jianjie fs18 gp-article">...bio...</div>       <!-- bio 段 -->
  <div class="item">
    <div class="title"><span class="text">研究方向</span></div>
    <div class="jianjie1 ...">tag tag tag</div>
  </div>
  <div class="item">… 讲授课程 …</div>
  <div class="item">… 学术论文 / 科研项目 / 社会服务 …</div>
</div>
```

`_profile_ss` 按 `<div class="item">` 拆 section，把 label 与 `jianjie1` 体
对齐。bio 来自 `div.jianjie`，过滤掉若干 nav 字符串以防漏入。

## 已知限制

1. **多数 PI 是合署/双聘，且无独立 profile**：
   - 工学博士 (xssbsz)：15 人，**只有 5 人** 有独立 profile_url。
   - 电子信息博士 (qygcbssz)：67 人，**只有 6 人** 有独立 profile_url。
   - 剩下的进 enrichment pipeline 后大概率会与 `eecs` / `ai` 段重合，靠
     `(school, name_cn, email)` 去重。
2. **公开页面没有邮箱**：ss.pku profile 全部 `email=None`，但
   adapter 主动标 `email_obfuscated=True`，让 v0.3 DeepSeek enricher
   按照"姓名 + 学院 + 学校"去检索 / 拼。
3. **photo / phone 字段在 list 与 profile 两端都拿不到**：list 卡片只有名字，
   profile 也没有结构化字段。已确认不存在可解析的源 → 都留 None。
4. 没有覆盖 `sztd/` 下其它桶（硕士导师 / 行政管理岗等）—— 与 CS/AI 相关性弱，
   留给 v0.5。

## 验收

```bash
$ python3 -m compileall -q src/claw/adapters/pku.py tests/test_parsers/test_pku.py
$ python3 -c "import yaml; yaml.safe_load(open('schools.yaml'))"
```

本地 smoke test（PYTHONPATH=src）：

| 测试 | 实际值 |
|---|---|
| xssbsz list parse | 15 items (5 with profile_url, 10 name-only) |
| qygcbssz list parse | 67 items (6 with profile_url) |
| xssbsz profile (李伟平) | title=教授, bio≈讲授6门研究生课程..., interests=[服务计算, 情境感知, 智慧养老与健康管理] |
| qygcbssz profile (张兴) | title=教授, bio≈现为北京大学教授..., interests=[新结构器件, 纳米级MOS器件, CMOS集成电路设计及加工工艺, 嵌入式系统设计] |

回归：原 4 个 dept (eecs/ai/wangxuan/cfcs) 的 list parse 数量与 v0.4 一致
(12 / 8 / 20 / 32)。
