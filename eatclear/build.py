#!/usr/bin/env python3
"""
把 data/source 下的源文件构建成 App 使用的 SQLite 数据包。

用法（在 appdata 仓库根目录运行）:
    python3 eatclear/build.py                       # 输出到 eatclear/dist/
    python3 eatclear/build.py --version 2026.11.15  # 指定数据版本
    python3 eatclear/build.py --seed ../eat-clear/EatClear/Resources
                                                    # 同时更新 App 内置的种子数据
    python3 eatclear/build.py --url-prefix https://example.com/files/
                                                    # 在 manifest 里写下载地址，可写多个

只用 Python 标准库，GitHub Actions 里不需要安装依赖。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

# 数据库结构版本。修改 schema.sql 时必须加 1，
# 并同时修改 App 里 DataRepository.swift 的 supportedSchemaVersion。
# App 会拒绝加载自己不认识的版本。
SCHEMA_VERSION = 3

# "懂法"文章的原文链接只能指向这些域名（含子域名），并且必须是 https。
# gov.cn 覆盖国家法律法规数据库 flk.npc.gov.cn、中国人大网、中国政府网、市场监管总局、国家卫健委；
# cfsa.net.cn 是食品安全国家标准数据检索平台；12315.cn 是全国 12315 平台。
LINK_DOMAINS = ("gov.cn", "cfsa.net.cn", "12315.cn")

# substances.roles 允许的取值：添加剂、营养强化剂，或两者都是
SUBSTANCE_ROLES = ("additive", "fortifier", "additive|fortifier")
CLAIM_NOTE_KINDS = ("comparative", "non_regulated")
LABEL_PHRASE_KINDS = ("allergen_section", "allergen_may_contain", "audience",
                      "notice", "standard_code", "product_name")
ALLERGEN_TERM_KINDS = ("statement", "ingredient")

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source"
SCHEMA = ROOT / "schema.sql"
DIST = ROOT / "dist"

DB_NAME = "eatclear-data.sqlite"
MANIFEST_NAME = "manifest.json"


class BuildError(Exception):
    pass


def normalize(text: str) -> str:
    """归一化文字，App 端（TextNormalizer.swift）必须使用相同的规则。

    1. NFKC：全角字母数字和标点转半角
    2. 转小写
    3. 去掉所有空白字符和零宽字符
    """
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if not ch.isspace() and ch not in "​‌‍﻿")


def read_csv(name: str) -> list[dict[str, str]]:
    path = SOURCE / name
    if not path.exists():
        raise BuildError(f"缺少源文件: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        rows = []
        for i, row in enumerate(csv.DictReader(f), start=2):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k is not None}
            if not any(row.values()):
                continue
            row["__line__"] = f"{name}:{i}"
            rows.append(row)
        return rows


def require(row: dict, *fields: str) -> None:
    for field in fields:
        if not row.get(field):
            raise BuildError(f"{row['__line__']}: 字段 {field} 不能为空")


def to_float(row: dict, field: str, allow_empty: bool = False) -> float | None:
    value = row.get(field, "")
    if value == "" and allow_empty:
        return None
    try:
        return float(value)
    except ValueError:
        raise BuildError(f"{row['__line__']}: {field} 不是数字: {value!r}") from None


def to_flag(row: dict, field: str) -> int:
    value = row.get(field, "0") or "0"
    if value not in ("0", "1"):
        raise BuildError(f"{row['__line__']}: {field} 只能是 0 或 1，现在是 {value!r}")
    return int(value)


def check_date(row: dict, field: str) -> str | None:
    value = row.get(field, "")
    if not value:
        return None
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise BuildError(f"{row['__line__']}: {field} 应为 YYYY-MM-DD 格式: {value!r}") from None
    return value


def link_problem(url: str) -> str | None:
    """检查原文链接，有问题时返回原因，没问题返回 None。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "必须是 https 链接"
    host = (parsed.hostname or "").lower()
    if not host:
        return "缺少域名"
    if not any(host == d or host.endswith("." + d) for d in LINK_DOMAINS):
        return f"域名 {host} 不在允许的范围 {LINK_DOMAINS}"
    return None


def parse_markdown(path: Path) -> tuple[dict[str, str], list[str], str]:
    """返回 (元数据, link 行列表, 正文)。link 可以写多行，其他键只能写一次。"""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise BuildError(f"{path.name}: 文件开头缺少 --- 元数据块")
    try:
        header, body = text[4:].split("\n---\n", 1)
    except ValueError:
        raise BuildError(f"{path.name}: 元数据块没有用 --- 结束") from None
    meta: dict[str, str] = {}
    links: list[str] = []
    for line in header.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise BuildError(f"{path.name}: 元数据格式错误: {line!r}")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key == "link":
            links.append(value)
        elif key in meta:
            raise BuildError(f"{path.name}: 元数据 {key} 重复")
        else:
            meta[key] = value
    return meta, links, body.strip()


def parse_link(path: Path, line: str) -> tuple[str, str]:
    """把 "标题 | https://..." 拆成 (标题, 地址) 并检查地址。"""
    if line.count("|") != 1:
        raise BuildError(f"{path.name}: link 应写成 \"标题 | https://...\": {line!r}")
    title, url = (part.strip() for part in line.split("|"))
    if not title or not url:
        raise BuildError(f"{path.name}: link 的标题和地址都不能为空: {line!r}")
    problem = link_problem(url)
    if problem:
        raise BuildError(f"{path.name}: 链接 {url} {problem}")
    return title, url


def read_law_links(source: Path) -> list[tuple[str, str, str]]:
    """读出所有文章的链接 (文章 id, 标题, 地址)，供 check_links.py 使用。"""
    result = []
    for path in sorted((source / "law").glob("*.md")):
        meta, links, _ = parse_markdown(path)
        for line in links:
            title, url = parse_link(path, line)
            result.append((meta.get("id", path.name), title, url))
    return result


def build(db_path: Path, version: str, url_prefixes: list[str] | None = None) -> dict:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    cur = conn.cursor()
    unverified: dict[str, int] = {}

    # substances
    substance_ids: set[str] = set()
    for r in read_csv("substances.csv"):
        require(r, "id", "name_zh", "function_class")
        if r["id"] in substance_ids:
            raise BuildError(f"{r['__line__']}: 重复的物质 id {r['id']}")
        substance_ids.add(r["id"])
        roles = r.get("roles") or "additive"
        if roles not in SUBSTANCE_ROLES:
            raise BuildError(f"{r['__line__']}: roles 只能是 {' / '.join(SUBSTANCE_ROLES)}，现在是 {roles!r}")
        cur.execute(
            "INSERT INTO substances VALUES (?,?,?,?,?,?,?,?,?)",
            (r["id"], r.get("ins") or None, r.get("cas") or None, r.get("e_number") or None,
             r["name_zh"], r.get("name_en") or None, r["function_class"], r.get("note") or None, roles),
        )

    # aliases（每个物质自动加上中文名，避免漏写）
    alias_owner: dict[str, str] = {}

    def add_alias(alias: str, sid: str, lang: str, where: str) -> None:
        norm = normalize(alias)
        if not norm:
            raise BuildError(f"{where}: 别名归一化后为空")
        owner = alias_owner.get(norm)
        if owner and owner != sid:
            raise BuildError(f"{where}: 别名 {alias!r} 同时指向 {owner} 和 {sid}")
        if owner == sid:
            return
        alias_owner[norm] = sid
        cur.execute("INSERT INTO aliases VALUES (?,?,?,?)", (alias, norm, sid, lang))

    for sid, name in cur.execute("SELECT id, name_zh FROM substances").fetchall():
        add_alias(name, sid, "zh", f"substances.csv({sid})")
    for r in read_csv("aliases.csv"):
        require(r, "alias", "substance_id")
        if r["substance_id"] not in substance_ids:
            raise BuildError(f"{r['__line__']}: 未知的物质 id {r['substance_id']}")
        add_alias(r["alias"], r["substance_id"], r.get("lang") or "zh", r["__line__"])

    # food categories
    categories: set[tuple[str, str]] = set()
    for r in read_csv("food_categories.csv"):
        require(r, "jurisdiction", "code", "name_zh")
        categories.add((r["jurisdiction"], r["code"]))
        cur.execute(
            "INSERT INTO food_categories VALUES (?,?,?,?,?)",
            (r["jurisdiction"], r["code"], r["name_zh"], r.get("name_en") or None, r.get("parent_code") or None),
        )

    # limits
    for r in read_csv("limits.csv"):
        require(r, "substance_id", "jurisdiction", "standard", "standard_version", "food_category_code", "source_ref")
        if r["substance_id"] not in substance_ids:
            raise BuildError(f"{r['__line__']}: 未知的物质 id {r['substance_id']}")
        if (r["jurisdiction"], r["food_category_code"]) not in categories:
            raise BuildError(f"{r['__line__']}: 未知的食品分类 {r['jurisdiction']} {r['food_category_code']}")
        max_value = to_float(r, "max_value", allow_empty=True)
        if max_value is not None and not r.get("unit"):
            raise BuildError(f"{r['__line__']}: 有 max_value 时 unit 不能为空")
        verified = to_flag(r, "verified")
        if not verified:
            unverified["limits"] = unverified.get("limits", 0) + 1
        cur.execute(
            "INSERT INTO limits VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["substance_id"], r["jurisdiction"], r["standard"], r["standard_version"], r["food_category_code"],
             max_value, r.get("unit") or None, r.get("note") or None, r["source_ref"],
             check_date(r, "effective_from"), verified),
        )

    # claim rules
    rule_keys: set[tuple[str, str]] = set()
    for r in read_csv("claim_rules.csv"):
        require(r, "jurisdiction", "claim_key", "claim_name_zh", "nutrient", "comparator", "threshold",
                "unit", "basis", "standard", "standard_version", "source_ref")
        if r["comparator"] not in ("<=", ">="):
            raise BuildError(f"{r['__line__']}: comparator 只能是 <= 或 >=")
        if r["basis"] not in ("per100g", "per100ml"):
            raise BuildError(f"{r['__line__']}: basis 只能是 per100g 或 per100ml")
        verified = to_flag(r, "verified")
        if not verified:
            unverified["claim_rules"] = unverified.get("claim_rules", 0) + 1
        rule_keys.add((r["jurisdiction"], r["claim_key"]))
        cur.execute(
            "INSERT INTO claim_rules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["jurisdiction"], r["claim_key"], r["claim_name_zh"], r["nutrient"], r["comparator"],
             to_float(r, "threshold"), r["unit"], r["basis"], r["standard"], r["standard_version"],
             r["source_ref"], check_date(r, "effective_from"), verified),
        )

    # NRV
    nrv_keys: set[tuple[str, str]] = set()
    for r in read_csv("nrv.csv"):
        require(r, "jurisdiction", "nutrient", "value", "unit", "standard", "standard_version")
        verified = to_flag(r, "verified")
        if not verified:
            unverified["nrv"] = unverified.get("nrv", 0) + 1
        nrv_keys.add((r["jurisdiction"], r["nutrient"]))
        cur.execute(
            "INSERT INTO nrv VALUES (?,?,?,?,?,?,?)",
            (r["jurisdiction"], r["nutrient"], to_float(r, "value"), r["unit"], r["standard"],
             r["standard_version"], verified),
        )

    # %NRV 规则必须有对应的 NRV 数值
    for jur, nutrient in cur.execute(
        "SELECT DISTINCT jurisdiction, nutrient FROM claim_rules WHERE unit = '%NRV'"
    ).fetchall():
        if (jur, nutrient) not in nrv_keys:
            raise BuildError(f"claim_rules.csv: {jur} {nutrient} 使用 %NRV，但 nrv.csv 里没有它的参考值")

    # claim notes：识别得出但不能数值对照的说法。同一个 claim_key 不能同时有规则和说明
    note_keys: set[tuple[str, str]] = set()
    for r in read_csv("claim_notes.csv"):
        require(r, "jurisdiction", "claim_key", "claim_name_zh", "kind", "explain")
        if r["kind"] not in CLAIM_NOTE_KINDS:
            raise BuildError(f"{r['__line__']}: kind 只能是 {' / '.join(CLAIM_NOTE_KINDS)}，现在是 {r['kind']!r}")
        key = (r["jurisdiction"], r["claim_key"])
        if key in rule_keys:
            raise BuildError(f"{r['__line__']}: claim_key {r['claim_key']} 已经在 claim_rules.csv 里有规则，不能再写说明")
        if key in note_keys:
            raise BuildError(f"{r['__line__']}: claim_key {r['claim_key']} 重复")
        note_keys.add(key)
        cur.execute("INSERT INTO claim_notes VALUES (?,?,?,?,?)",
                    (r["jurisdiction"], r["claim_key"], r["claim_name_zh"], r["kind"], r["explain"]))

    # claim patterns
    pattern_owner: dict[tuple[str, str], str] = {}
    for r in read_csv("claim_patterns.csv"):
        require(r, "jurisdiction", "pattern", "claim_key")
        key = (r["jurisdiction"], r["claim_key"])
        if key not in rule_keys and key not in note_keys:
            raise BuildError(f"{r['__line__']}: claim_key {r['claim_key']} 在 claim_rules.csv 和 claim_notes.csv 里都没有")
        norm = normalize(r["pattern"])
        pkey = (r["jurisdiction"], norm)
        if pkey in pattern_owner:
            raise BuildError(f"{r['__line__']}: 说法 {r['pattern']!r} 重复")
        pattern_owner[pkey] = r["claim_key"]
        cur.execute("INSERT INTO claim_patterns VALUES (?,?,?,?)", (r["jurisdiction"], r["pattern"], norm, r["claim_key"]))

    # nutrient names
    for r in read_csv("nutrient_names.csv"):
        require(r, "name", "nutrient")
        cur.execute("INSERT INTO nutrient_names VALUES (?,?,?)", (r["name"], normalize(r["name"]), r["nutrient"]))

    # settings
    setting_keys: set[str] = set()
    for r in read_csv("settings.csv"):
        require(r, "key", "value")
        if r["key"] in setting_keys:
            raise BuildError(f"{r['__line__']}: 重复的设置 {r['key']}")
        setting_keys.add(r["key"])
        cur.execute("INSERT INTO settings VALUES (?,?,?)", (r["key"], r["value"], r.get("note") or None))

    # label phrases
    phrase_keys: set[tuple[str, str]] = set()
    for r in read_csv("label_phrases.csv"):
        require(r, "kind", "phrase")
        if r["kind"] not in LABEL_PHRASE_KINDS:
            raise BuildError(f"{r['__line__']}: kind 只能是 {' / '.join(LABEL_PHRASE_KINDS)}，现在是 {r['kind']!r}")
        norm = normalize(r["phrase"])
        if not norm:
            raise BuildError(f"{r['__line__']}: phrase 归一化后为空")
        if (r["kind"], norm) in phrase_keys:
            raise BuildError(f"{r['__line__']}: {r['kind']} 里的 {r['phrase']!r} 重复")
        phrase_keys.add((r["kind"], norm))
        cur.execute("INSERT INTO label_phrases VALUES (?,?,?)", (r["kind"], r["phrase"], norm))

    # allergens
    allergen_keys: set[str] = set()
    for r in read_csv("allergens.csv"):
        require(r, "key", "name_zh", "sort_order")
        if r["key"] in allergen_keys:
            raise BuildError(f"{r['__line__']}: 重复的致敏物质 key {r['key']}")
        allergen_keys.add(r["key"])
        cur.execute("INSERT INTO allergens VALUES (?,?,?)",
                    (r["key"], r["name_zh"], int(to_float(r, "sort_order"))))

    # allergen terms
    term_keys: set[tuple[str, str]] = set()
    for r in read_csv("allergen_terms.csv"):
        require(r, "term", "allergen_key", "kind")
        if r["allergen_key"] not in allergen_keys:
            raise BuildError(f"{r['__line__']}: 未知的致敏物质 key {r['allergen_key']}")
        if r["kind"] not in ALLERGEN_TERM_KINDS:
            raise BuildError(f"{r['__line__']}: kind 只能是 {' / '.join(ALLERGEN_TERM_KINDS)}，现在是 {r['kind']!r}")
        norm = normalize(r["term"])
        if not norm:
            raise BuildError(f"{r['__line__']}: term 归一化后为空")
        if (norm, r["kind"]) in term_keys:
            raise BuildError(f"{r['__line__']}: {r['kind']} 里的 {r['term']!r} 重复")
        term_keys.add((norm, r["kind"]))
        cur.execute("INSERT INTO allergen_terms VALUES (?,?,?,?)", (r["term"], norm, r["allergen_key"], r["kind"]))

    # law categories
    law_dir = SOURCE / "law"
    category_ids: set[str] = set()
    for r in read_csv("law/categories.csv"):
        require(r, "id", "jurisdiction", "sort_order", "title", "summary")
        if r["id"] in category_ids:
            raise BuildError(f"{r['__line__']}: 重复的分类 id {r['id']}")
        category_ids.add(r["id"])
        cur.execute("INSERT INTO law_categories VALUES (?,?,?,?,?)",
                    (r["id"], r["jurisdiction"], int(to_float(r, "sort_order")), r["title"], r["summary"]))

    # law articles + links
    article_ids: set[str] = set()
    for path in sorted(law_dir.glob("*.md")):
        meta, links, body = parse_markdown(path)
        for field in ("id", "jurisdiction", "category", "title", "summary", "updated_at"):
            if not meta.get(field):
                raise BuildError(f"{path.name}: 元数据缺少 {field}")
        if meta["id"] in article_ids:
            raise BuildError(f"{path.name}: 重复的文章 id {meta['id']}")
        article_ids.add(meta["id"])
        if meta["category"] not in category_ids:
            raise BuildError(f"{path.name}: 分类 {meta['category']} 不在 law/categories.csv 里")
        try:
            dt.date.fromisoformat(meta["updated_at"])
        except ValueError:
            raise BuildError(f"{path.name}: updated_at 应为 YYYY-MM-DD") from None
        if not links:
            raise BuildError(f"{path.name}: 至少要有一个原文链接（link: 标题 | https://...）")
        cur.execute(
            "INSERT INTO law_articles VALUES (?,?,?,?,?,?,?,?)",
            (meta["id"], meta["jurisdiction"], meta["category"], int(meta.get("sort_order", "0")),
             meta["title"], meta["summary"], body, meta["updated_at"]),
        )
        for order, line in enumerate(links, start=1):
            title, url = parse_link(path, line)
            cur.execute("INSERT INTO law_links VALUES (?,?,?,?)", (meta["id"], order, title, url))

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    cur.executemany(
        "INSERT INTO meta VALUES (?,?)",
        [("schema_version", str(SCHEMA_VERSION)), ("data_version", version), ("generated_at", generated_at)],
    )
    conn.commit()

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise BuildError(f"外键检查失败: {fk_errors}")

    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("substances", "aliases", "food_categories", "limits", "claim_rules",
                      "claim_notes", "claim_patterns", "nutrient_names", "nrv",
                      "settings", "label_phrases", "allergens", "allergen_terms",
                      "law_categories", "law_articles", "law_links")
    }
    conn.execute("VACUUM")
    conn.close()

    data = db_path.read_bytes()
    return {
        "schema_version": SCHEMA_VERSION,
        "data_version": version,
        "generated_at": generated_at,
        "file": DB_NAME,
        # 数据文件的完整下载地址，App 按顺序尝试。为空时，App 会到 manifest 所在目录下载 file。
        "urls": [prefix.rstrip("/") + "/" + DB_NAME for prefix in (url_prefixes or [])],
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "counts": counts,
        "unverified": unverified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 EatClear 数据包")
    parser.add_argument("--version", help="数据版本，默认使用当前 UTC 时间 YYYY.MM.DD.HHMM")
    parser.add_argument("--out", type=Path, default=DIST, help="输出目录")
    parser.add_argument("--seed", type=Path, metavar="DIR", help="同时复制到 App 的资源目录（如 ../eat-clear/EatClear/Resources），作为 App 内置数据")
    parser.add_argument("--url-prefix", action="append", default=[], metavar="URL",
                        help="数据文件所在的目录地址，写进 manifest。可以写多个，App 按顺序尝试")
    args = parser.parse_args(argv)

    version = args.version or dt.datetime.now(dt.timezone.utc).strftime("%Y.%m.%d.%H%M")
    args.out.mkdir(parents=True, exist_ok=True)
    db_path = args.out / DB_NAME

    try:
        manifest = build(db_path, version, args.url_prefix)
    except BuildError as e:
        print(f"构建失败: {e}", file=sys.stderr)
        return 1

    manifest_path = args.out / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.seed:
        if not args.seed.is_dir():
            print(f"构建失败: --seed 指定的目录不存在: {args.seed}", file=sys.stderr)
            return 1
        shutil.copyfile(db_path, args.seed / "eatclear-seed.sqlite")
        shutil.copyfile(manifest_path, args.seed / "eatclear-seed-manifest.json")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest["unverified"]:
        print("\n提示: 以下数据还没有对照原文核实（verified=0），App 会显示\"待核实\"标记:", file=sys.stderr)
        for table, n in manifest["unverified"].items():
            print(f"  {table}: {n} 条", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
