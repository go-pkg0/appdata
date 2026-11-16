# 吃得明白（EatClear）数据

App 代码在私有仓库 `go-pkg0/eat-clear`。这里只放公开的法规数据和"懂法"文章。

## 文件

| 文件 | 内容 |
|---|---|
| `schema.sql` | 数据库结构 |
| `source/substances.csv` | 添加剂和营养强化剂。主键优先用 INS 编号（如 `INS202`），没有 INS 时用 CAS（如 `CAS67-97-0`） |
| `source/aliases.csv` | 配料表上的各种写法。物质的中文名会自动加入，不用重复写 |
| `source/food_categories.csv` | 食品分类（GB 2760 附录 E） |
| `source/limits.csv` | 添加剂限量。**目前是空的，需要按 GB 2760-2024 原文录入** |
| `source/claim_rules.csv` | 营养声称的数值标准（GB 28050） |
| `source/claim_patterns.csv` | 包装上的说法和声称的对应关系 |
| `source/nutrient_names.csv` | 营养成分表上的营养素名称 |
| `source/nrv.csv` | 营养素参考值 |
| `source/claim_notes.csv` | 识别得出、但无法用营养成分表对照的说法及原因（见下方"新增数据表的格式"） |
| `source/settings.csv` | 可调的数值设置，如一块方糖按多少克换算 |
| `source/label_phrases.csv` | 标签上的固定用语：致敏物质提示、适用人群、提示语、标准号、商品名 |
| `source/allergens.csv` | 8 类致敏物质 |
| `source/allergen_terms.csv` | 致敏物质的各种写法 |
| `source/notes.csv` | 大白话说明和小知识 |
| `source/note_sources.csv` | 说明的依据（三级，见下方"说明和依据"） |
| `source/law/categories.csv` | "懂法"分类 |
| `source/law/*.md` | "懂法"文章（写法见下方"懂法文章"） |
| `build.py` | 构建脚本（只用 Python 标准库） |
| `check_links.py` | 联网检查"懂法"文章和说明依据的原文链接能否打开 |
| `tests/` | 构建脚本的测试 |

每条规定、说明和依据都有 `verified` 字段：`1` 表示已经对照官方原文核实，`0` 表示待核实。App 会在待核实的数据旁边显示"待核实"标记。**目前全部是 0。**

## 常用命令（在 appdata 仓库根目录运行）

```bash
python3 -m unittest discover -s eatclear/tests     # 运行测试
python3 eatclear/build.py                          # 构建到 eatclear/dist/

# 更新 App 内置的种子数据（两个仓库放在同一个上级目录时）
python3 eatclear/build.py --seed ../eat-clear/EatClear/Resources
```

构建脚本会检查：重复的别名、指向不存在物质的限量、没有规则或说明的说法、日期格式、文章分类、原文链接、说明的依据级别和域名等。有错误时会停止，并指出是哪个文件的哪一行。

## 新增数据表的格式

数据结构版本 3 新增的 CSV。所有"写法"类字段（`pattern`、`phrase`、`term`、`alias`）在构建时按 `normalize()` 归一化，
App 按字面查找，长的写法优先，所以不用为了包含关系专门处理。

| 文件 | 列 | 说明 |
|---|---|---|
| `substances.csv` | `id,ins,cas,e_number,name_zh,name_en,function_class,note,roles` | `roles`：`additive`（GB 2760 添加剂，空着时默认）、`fortifier`（GB 14880 营养强化剂）或 `additive\|fortifier`（两者都是）。只是营养强化剂的，`function_class` 写"营养强化剂"；两者都是的，`function_class` 写它在 GB 2760 里列出的第一个功能 |
| `claim_notes.csv` | `jurisdiction,claim_key,claim_name_zh,kind,explain` | `kind`：`comparative`（比较声称，如"减糖"）、`non_regulated`（标准里没有数值定义，如"0蔗糖"）或 `ambiguous`（同一个写法在现行标准里有两种意思：GB 28050-2011 表C.2 把"少"列为"低"的同义语，表C.4 又把"少"列为"减少"的同义语，所以"少糖""少盐"包装上看不出是哪一种）。"少油"例外：表C.2 注a 写明"少油"仅用于低脂肪的声称，所以它直接对应 `low_fat`。同一个 `claim_key` 只能出现在 `claim_rules.csv` 或这里其中之一 |
| `settings.csv` | `key,value,note` | |
| `label_phrases.csv` | `kind,phrase` | `kind`：`allergen_section`、`allergen_may_contain`、`audience`、`notice`、`standard_code`、`product_name` |
| `allergens.csv` | `key,name_zh,sort_order` | 8 类，按 GB 7718 的顺序 |
| `allergen_terms.csv` | `term,allergen_key,kind` | `kind`：`statement`（致敏物质提示里的写法）、`ingredient`（配料表里的原料）、`exempt` 或 `not_allergen`，后两种的 `allergen_key` 写它本来会被匹配到的类别。`exempt` 只收 GB 7718-2025 附录D.4 写明可以免于致敏物质标示的配料，按原文列出的配料录，可用常见写法（如原文"大豆来源的……磷脂"录成"大豆磷脂""大豆卵磷脂"）；不带"精炼"的"大豆油""花生油""鱼油"不算，因为原文只豁免精炼过的。`not_allergen` 收字面上含有某类致敏物质、但并不是这类物质的写法（如"鱿鱼"不是鱼类，"花生四烯酸"不是花生，"植物奶油"不是乳）。App 按最长写法匹配，被这两种写法占住的文字不再算作致敏物质 |
| `notes.csv` | `id,subject_type,subject_key,title,body,verified` | `subject_type`：`nutrient`（`subject_key` 是营养素 key）、`allergen`（致敏物质 key）、`function_class`（功能类别名）、`substance`（物质 id）、`tip`（小知识，`subject_key` 写自己的 id）。正文 2～4 句 |
| `note_sources.csv` | `note_id,tier,org,title,year,url,method,study_type,verified` | 见下一节。同一条说明的依据按文件里的先后排序 |

`claim_rules.csv` 的数值部分只能表达"某个营养素 每100克/每100毫升 ≤ 或 ≥ 某个数"。倒数第二列 `condition` 写标准里的限制性条件（如"仅指乳品类"、"指饱和脂肪和反式脂肪的总和"、"脂肪提供的能量不超过总能量的50%"），用一两句大白话写清标准还要求什么；没有条件时留空。有条件的规则，App 在数值不满足时显示"存在差异"，满足时显示"无法对照"并附上这段文字，不会显示"一致"。

表C.1 对蛋白质、膳食纤维、维生素、矿物质的"来源"和"高"声称，还给了"每420千焦"的替代要求。这个数写在 `per420kj_threshold` 列，单位和 `threshold` 相同（如膳食纤维写克，维生素写 %NRV），每100克和每100毫升两行都填（国家卫健委《GB 28050-2025 问答》第四十二条把三种要求并列为"或"）；没有替代要求的留空。按 `basis` 不满足时，App 用营养成分表的能量一行换算成每420千焦再比一次。

膳食纤维的限制性条件（2011 版）大意是：膳食纤维总量符合含量要求，或者可溶性膳食纤维、不溶性膳食纤维、单体成分其中一项符合含量要求即可；2025 版原文写的是"膳食纤维总量符合含量要求；或者可溶性膳食纤维、不溶性膳食纤维、膳食纤维单体成分符合含量要求"。这几项都包含在总量里，其中一项达标时总量也达标，所以只按总量录入，不写进 `condition`。

## 说明和依据

每条说明（小知识除外）至少要有一条依据。依据分三级，链接必须是 https，域名由 `build.py` 的 `TIER_DOMAINS` 检查：

| 级别 | 来源 | 允许的域名 |
|---|---|---|
| 1 | 中国国家标准、《中国居民膳食指南》，用作"和标准对照"的依据 | `gov.cn`、`cfsa.net.cn`、`12315.cn`、`cnsoc.org` |
| 2 | 国际机构的正式结论：WHO（含 IARC）、JECFA、EFSA、美国 FDA 等，补充说明 | `who.int`、`fao.org`、`europa.eu`、`fda.gov` |
| 3 | 期刊上的系统综述、荟萃分析，补充说明 | 只收 `doi.org` 链接 |

- **数值必须来自读过的原文。** 录入前打开原文（标准文本、机构网页、DOI 页面）核对，`title` 写到条款或表号。找不到可读的原文就不录。
- `method` 一句话写清"拿什么和什么比"，例如"用每100克的钠含量，和营养素参考值2000毫克相比，得出 NRV%"；只作说明时写"只作说明，不做数值对照"。两个量不是同一个概念时写"无法直接对照"并说明原因（如标签上的糖是总糖，建议量针对的是添加糖）。
- 级别 3 只收系统综述或荟萃分析，`study_type` 必须写"系统综述"或"荟萃分析"，`year` 写实际发表年份。构建年份减发表年份超过 10 年的，构建时自动标为"较早的研究"（`is_dated = 1`）。
- 不同机构结论不一致时（如阿斯巴甜：IARC 的分类和 JECFA 的每日允许摄入量），两条依据都录，正文并列写，用引号原样引用机构用语，不替用户下结论。
- 新录入的说明和依据一律 `verified = 0`，经人对照原文核对后才改成 1。

## 禁用词警告

App 自己的文案不用"安全、不安全、风险、有害、合格、不合格、超标、检测、认证、官方、监管、食安"这些词（`build.py` 的 `BANNED_WORDS`）。构建时检查 `notes.csv` 的标题和正文、`note_sources.csv` 的 `org`/`title`/`method`、`claim_rules.csv` 的 `claim_name_zh` 和 `condition`、`claim_notes.csv` 的 `explain`、`label_phrases.csv` 的 `phrase`：这些词出现在引号（“” "" 「」）和书名号（《》）外面时，构建不失败，但会在输出和 `manifest.json` 的 `warnings` 里列出。

处理方法：

1. 能改写的就改写，换成平实的说法（如"风险"改成具体说了什么）。
2. 转述机构的正式结论、确实需要原词时，用引号原样引用并写明出处，如 IARC 的分类名称。
3. 机构名、文件名里本来就有这些词时（如 WHO 的文件《阿斯巴甜危害及风险评估结果发布》），把文件名放进书名号，不要放宽检查。
4. 发布前 `warnings` 必须为空，`tests/test_content.py` 会检查。

## 懂法文章

```markdown
---
id: cn-05
jurisdiction: CN
category: food-safety-law        # law/categories.csv 里的 id
sort_order: 5
title: 标题
summary: 一句话摘要
updated_at: 2026-11-14           # 只写日期
link: 中华人民共和国食品安全法（2025年修正） | https://...
link: 第二个链接 | https://...    # 可以写多行，按顺序显示
---
正文，支持 **加粗**
```

- 每篇文章至少一个 `link`。链接必须是 https，域名只能是 `gov.cn`、`cfsa.net.cn`、`12315.cn` 或它们的子域名（`build.py` 里的 `LINK_DOMAINS`）。不要链接到转载网站或商业法律库。
- 引用条文时写明所依据的版本（如"2025 年修正"），并在法律修正、标准换版后及时更新。已发布但还没实施的新标准，写明实施日期。
- 文章里不要出现"安全/不安全/风险/有害"之类的结论。

## 发布

**发布前在本地运行 `python3 eatclear/check_links.py`，确认所有原文链接都能打开。** CI 也会运行，但部分政府网站可能不接受境外访问，所以 CI 里只提示、不阻断。

```bash
git tag eatclear-v2026.11.15
git push origin eatclear-v2026.11.15
```

GitHub Actions 会：

1. 运行测试并构建
2. 创建 `eatclear-v2026.11.15` Release，上传 `eatclear-data.sqlite` 和 `manifest.json`
3. 把 `manifest.json` 覆盖到固定的 `eatclear-latest` Release

App 从下面这个固定地址检查更新：

```
https://github.com/go-pkg0/appdata/releases/download/eatclear-latest/manifest.json
```

`manifest.json` 里的 `urls` 是数据文件的完整下载地址，App 按顺序尝试，并用 `sha256` 校验。

## 修改数据库结构时

1. 修改 `schema.sql`
2. 把 `build.py` 里的 `SCHEMA_VERSION` 加 1
3. 把 App 仓库里 `DataRepository.swift` 的 `supportedSchemaVersion` 改成同样的值
4. 先发布新版 App，再发布新结构的数据。旧版 App 会拒绝下载自己读不了的数据，继续使用原来的数据
