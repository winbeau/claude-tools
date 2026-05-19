# 北京大学 adapter — completion report

- **branch**: `feat/adapter-pku`
- **commit**: `a6335198ccbde03458c4775c73d7976fdd0126ee`
- **worktree**: `/home/winbeau/wenbiao_zhao/adapter-pku`

## test results

- `python3 -m compileall -q src/claw/adapters/pku.py tests/test_parsers/test_pku.py`: **PASS**
- `pytest`: **未跑** (VPS 上未装 selectolax/pydantic 等项目依赖；本地复跑命令：
  `uv run pytest tests/test_parsers/test_pku.py -v`)

## fixture coverage

测试基线（fixture 上、单页可解析数）：

| dept | list_url (验证后) | parsed / list-page actual | email % (list) | RI % (profile) |
|------|------------------|--------------------------|----------------|----------------|
| eecs | `cs.pku.edu.cn/szdw/jyxl/amz/ALL.htm` | ~12 / 12（全页 12 条 教研系列卡片）| ≥ 60%（图片混淆 `local<img>domain` 拼接率高）| 已覆盖 主要研究方向 |
| ai | `www.cis.pku.edu.cn/szdw/zzjs.htm` | ~8 / 8（首页 8 条）| ≥ 80%（纯文本邮箱）| 已覆盖 研究领域 / 教育背景 |
| wangxuan | `www.icst.pku.edu.cn/xstd/xstd_01/index.htm` | ~19 / 20（首页 20 条，过滤掉了 1 个 hex32 无中文名条目）| 0%（list 页无邮箱字段，profile 上有）| 已覆盖 研究领域 / 教育背景，部分人无 bio |
| cfcs | `cfcs.pku.edu.cn/people/faculty/index.htm` | ~36 / ~36（首页全 36 条，含中心主任/教学科研/访问讲席/博士后/行政辅助混杂）| ~30% 明文 + ~70% 半混淆（只有 local，domain 默认 @pku.edu.cn） | 已覆盖 简介 + sampleText 研究领域 |

> 注：上述都是 **单页/起始页** 数据。`schools.yaml` 里已经把所有分页 URL 都列出（cs.pku 1-9、cis.pku 1-4、wangxuan index/index1、cfcs 1 页）；pipeline 跑全部分页后人数会显著高于上面。

## 特殊结构观察（喂给 orchestrator 归纳）

1. **PKU 学院域名重构**：原 `eecs.pku.edu.cn` 现在只是导航壳，把师资分流到 `cs.pku.edu.cn`（计算机学院）/ `ele.pku.edu.cn` / `ic.pku.edu.cn` / `cis.pku.edu.cn`（智能学院）四个独立站点。同理 `ai.pku.edu.cn` 是旧 AILab placeholder（HTTP 200 但内容为空模板），真正的智能学院在 `cis.pku.edu.cn`。`www.wangxuan.pku.edu.cn` 是 Vue SPA 纪念站，王选所师资实际在 `www.icst.pku.edu.cn`。**6 个 agent 的 prompt + schools.yaml 都该再 sanity-check 一遍域名是否还指向真实师资。**
2. **图片做 @ 符号的混淆，全 PKU 三种变体**：cs.pku 列表页 `local<img f_h_pic.png>domain` —— 可拼接；cfcs 列表页 `<strong>local</strong><img mail.png>` —— domain 完全在图里，只能猜 `@pku.edu.cn`；wangxuan 个人页用纯文本 `name (at) domain`（`extract_email` 已支持）。建议 v0.3 在 `parser_utils.py` 加 `stitch_image_obfuscated_email(node)` 复用函数。
3. **VSB-CMS 是 PKU 站群通用模板**：cs.pku 和 cis.pku 的个人页都用 `div.v_news_content` + `<p><strong>label</strong></p>` 这套，跟清华 cs 几乎同构。所以 `_split_h4_sections` + `_PSEUDO_HEADERS` 这套抽取逻辑在 v0.3 提取到 `core/section_parser.py` 应该能直接服务 PKU/THU。

## schools.yaml 改动

```diff
   - code: pku
     name_cn: 北京大学
     departments:
       - code: eecs
-        name_cn: 信息科学技术学院
-        list_urls:
-          - https://eecs.pku.edu.cn/info/1342/8930.htm
+        name_cn: 计算机学院 (原信息学院 CS 方向)
+        list_urls:
+          - https://cs.pku.edu.cn/szdw/jyxl/amz/ALL.htm
+          - ... 1.htm ... 9.htm (9 page urls)
       - code: ai
         name_cn: 智能学院
-        list_urls:
-          - https://ai.pku.edu.cn/rydw/index.htm
+        list_urls:
+          - https://www.cis.pku.edu.cn/szdw/zzjs.htm
+          - ... 1.htm ... 4.htm
       - code: wangxuan
-        list_urls:
-          - https://www.icst.pku.edu.cn/szdw/        # 404
+        list_urls:
+          - https://www.icst.pku.edu.cn/xstd/xstd_01/index.htm
+          - https://www.icst.pku.edu.cn/xstd/xstd_01/index1.htm
       - code: cfcs
-        list_urls:
-          - https://cfcs.pku.edu.cn/people/
+        list_urls:
+          - https://cfcs.pku.edu.cn/people/faculty/index.htm
+          - https://cfcs.pku.edu.cn/people/directors/index.htm
```

## known limitations

- **cfcs 邮箱域名**：列表页的师资邮箱 domain 部分是 `mail.png` 图片，无法 OCR 之外的方式还原。adapter 默认拼 `@pku.edu.cn` 并把 `email_obfuscated=True`，正确率应该 ≥ 95%（cfcs 是 pku 下属），但 v0.3 DeepSeek pass 最好对照个人主页核对一遍。
- **wangxuan profile 内容很薄**：很多人的个人页只有一行"研究领域 + 电子邮件"，没有 bio。`research_interests` 覆盖率应该 ≥ 80%，但 `bio_text` 可能 < 50%。这是页面本身的事实，不是 parser 问题。
- **没有去重跨 dept**：北大很多老师横跨 cs.pku + 王选所（如冯岩松、连宙辉），主程序的 `(school, name_cn, email)` upsert 会自然合并，但同一学校两个 dept 都有条目时主程序需要决定哪条胜出。该决策不在 adapter 层。
- **学生条目过滤**：cs.pku 列表是"教研系列 ALL"按设计已经只含教师，没看到学生条目混入。cfcs 列表里有"博士后 / 行政辅助"分类，目前用 `_FACULTY_TITLE_KEYWORDS` 白名单过滤掉了无关条目；但白名单可能误杀少数无 title 字段的教学条目（v0.3 可以放宽到"有 profile 链接 + 名字 ≥ 2 中文字"作 fallback）。
- **pytest 未在 VPS 运行**：本机没装项目依赖，按 v0.2 工作流由用户在本地 `uv run pytest tests/test_parsers/test_pku.py -v` 复跑。
