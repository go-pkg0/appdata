# 吃得明白（EatClear）数据

App 代码在私有仓库 `go-pkg0/eat-clear`。这里只放公开的法规数据和"懂法"文章。

## 文件

| 文件 | 内容 |
|---|---|
| `schema.sql` | 数据库结构 |
| `source/substances.csv` | 添加剂。主键用 INS 编号（如 `INS202`） |
| `source/aliases.csv` | 配料表上的各种写法。物质的中文名会自动加入，不用重复写 |
| `source/food_categories.csv` | 食品分类（GB 2760 附录 E） |
| `source/limits.csv` | 添加剂限量。**目前是空的，需要按 GB 2760-2024 原文录入** |
| `source/claim_rules.csv` | 营养声称的数值标准（GB 28050） |
| `source/claim_patterns.csv` | 包装上的说法和声称的对应关系 |
| `source/nutrient_names.csv` | 营养成分表上的营养素名称 |
| `source/nrv.csv` | 营养素参考值 |
| `source/law/categories.csv` | "懂法"分类 |
| `source/law/*.md` | "懂法"文章（写法见下方"懂法文章"） |
| `build.py` | 构建脚本（只用 Python 标准库） |
| `check_links.py` | 联网检查"懂法"文章的原文链接能否打开 |
| `tests/` | 构建脚本的测试 |

每条规定都有 `verified` 字段：`1` 表示已经对照官方原文核实，`0` 表示待核实。App 会在待核实的数据旁边显示"待核实"标记。**目前所有规定都是 0。**

## 常用命令（在 appdata 仓库根目录运行）

```bash
python3 -m unittest discover -s eatclear/tests     # 运行测试
python3 eatclear/build.py                          # 构建到 eatclear/dist/

# 更新 App 内置的种子数据（两个仓库放在同一个上级目录时）
python3 eatclear/build.py --seed ../eat-clear/EatClear/Resources
```

构建脚本会检查：重复的别名、指向不存在物质的限量、没有规则的说法、日期格式、文章分类和原文链接等。有错误时会停止，并指出是哪个文件的哪一行。

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
