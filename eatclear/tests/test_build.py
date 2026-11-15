"""数据构建脚本的测试。运行: python3 -m unittest discover -s eatclear/tests"""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DATA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DATA_DIR))

import build  # noqa: E402


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "source"
        shutil.copytree(DATA_DIR / "source", self.source)
        self._orig_source = build.SOURCE
        build.SOURCE = self.source
        self.out = self.tmp / "dist"

    def tearDown(self):
        build.SOURCE = self._orig_source
        shutil.rmtree(self.tmp)

    def run_build(self):
        return build.main(["--version", "test", "--out", str(self.out)])

    def append(self, name, line):
        with (self.source / name).open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class TestNormalize(unittest.TestCase):
    def test_fullwidth_and_case(self):
        self.assertEqual(build.normalize("Ｅ２０２"), "e202")

    def test_whitespace_removed(self):
        self.assertEqual(build.normalize("山梨 酸　钾"), "山梨酸钾")

    def test_zero_width_removed(self):
        self.assertEqual(build.normalize("无​糖"), "无糖")


class TestBuild(BuildTestCase):
    def test_success_and_manifest(self):
        self.assertEqual(self.run_build(), 0)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], build.SCHEMA_VERSION)
        self.assertEqual(manifest["data_version"], "test")
        self.assertEqual(len(manifest["sha256"]), 64)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        self.assertEqual(meta["schema_version"], str(build.SCHEMA_VERSION))

    def test_url_prefixes_in_manifest(self):
        code = build.main(["--version", "test", "--out", str(self.out),
                           "--url-prefix", "https://a.example/x/", "--url-prefix", "https://b.example/y"])
        self.assertEqual(code, 0)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["urls"], [
            "https://a.example/x/eatclear-data.sqlite",
            "https://b.example/y/eatclear-data.sqlite",
        ])

    def test_no_url_prefix_gives_empty_list(self):
        self.assertEqual(self.run_build(), 0)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["urls"], [])

    def test_seed_copies_files(self):
        seed_dir = self.tmp / "Resources"
        seed_dir.mkdir()
        self.assertEqual(build.main(["--version", "test", "--out", str(self.out), "--seed", str(seed_dir)]), 0)
        self.assertTrue((seed_dir / "eatclear-seed.sqlite").exists())
        self.assertTrue((seed_dir / "eatclear-seed-manifest.json").exists())

    def test_seed_missing_dir_fails(self):
        self.assertEqual(build.main(["--version", "test", "--out", str(self.out), "--seed", str(self.tmp / "nope")]), 1)

    def test_substance_name_becomes_alias(self):
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        row = conn.execute("SELECT substance_id FROM aliases WHERE alias_norm = '山梨酸钾'").fetchone()
        self.assertEqual(row[0], "INS202")

    def test_conflicting_alias_fails(self):
        self.append("aliases.csv", "山梨酸钾,INS211,zh")
        self.assertEqual(self.run_build(), 1)

    def test_unknown_substance_in_limits_fails(self):
        self.append("limits.csv", "INS999,CN,GB 2760,2024,14.0,0.5,g/kg,,GB 2760-2024 表A.1,,0")
        self.assertEqual(self.run_build(), 1)

    def test_unknown_category_in_limits_fails(self):
        self.append("limits.csv", "INS202,CN,GB 2760,2024,99.0,0.5,g/kg,,GB 2760-2024 表A.1,,0")
        self.assertEqual(self.run_build(), 1)

    def test_valid_limit_row(self):
        self.append("limits.csv", "INS202,CN,GB 2760,2024,14.0,0.5,g/kg,以山梨酸计,示例行,2025-02-08,0")
        self.assertEqual(self.run_build(), 0)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["unverified"]["limits"], 1)

    def test_bad_verified_flag_fails(self):
        self.append("limits.csv", "INS202,CN,GB 2760,2024,14.0,0.5,g/kg,,示例行,,yes")
        self.assertEqual(self.run_build(), 1)

    def test_pattern_without_rule_fails(self):
        self.append("claim_patterns.csv", "CN,超级无敌,not_a_rule")
        self.assertEqual(self.run_build(), 1)

    def test_bad_comparator_fails(self):
        self.append("claim_rules.csv", "CN,x,测试,sugar,<,1,g,per100g,GB 28050,2011,ref,,0")
        self.assertEqual(self.run_build(), 1)

    def test_law_missing_field_fails(self):
        (self.source / "law" / "bad.md").write_text("---\nid: bad\n---\n正文", encoding="utf-8")
        self.assertEqual(self.run_build(), 1)

    def write_article(self, *links, category="label"):
        meta = [
            "id: test-x", "jurisdiction: CN", f"category: {category}", "sort_order: 99",
            "title: 测试", "summary: 测试", "updated_at: 2026-11-14",
        ] + [f"link: {link}" for link in links]
        (self.source / "law" / "zz-test.md").write_text(
            "---\n" + "\n".join(meta) + "\n---\n正文", encoding="utf-8")

    def test_law_links_built(self):
        self.write_article("国家法律法规数据库 | https://flk.npc.gov.cn/",
                           "标准检索 | https://sppt.cfsa.net.cn:8086/db")
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        rows = conn.execute(
            "SELECT sort_order, title, url FROM law_links WHERE article_id = 'test-x' ORDER BY sort_order"
        ).fetchall()
        self.assertEqual(rows, [(1, "国家法律法规数据库", "https://flk.npc.gov.cn/"),
                                (2, "标准检索", "https://sppt.cfsa.net.cn:8086/db")])
        # 每篇文章都至少有一个链接，分类至少 4 个
        orphan = conn.execute(
            "SELECT COUNT(*) FROM law_articles a WHERE NOT EXISTS "
            "(SELECT 1 FROM law_links l WHERE l.article_id = a.id)").fetchone()[0]
        self.assertEqual(orphan, 0)
        self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM law_categories").fetchone()[0], 4)

    def test_law_without_link_fails(self):
        self.write_article()
        self.assertEqual(self.run_build(), 1)

    def test_law_http_link_fails(self):
        self.write_article("人大网 | http://www.npc.gov.cn/")
        self.assertEqual(self.run_build(), 1)

    def test_law_link_domain_not_allowed_fails(self):
        for url in ("https://law.pkulaw.com/x", "https://gov.cn.example.com/", "https://evilgov.cn/"):
            with self.subTest(url=url):
                self.write_article(f"转载 | {url}")
                self.assertEqual(self.run_build(), 1)

    def test_law_link_format_error_fails(self):
        self.write_article("没有竖线 https://flk.npc.gov.cn/")
        self.assertEqual(self.run_build(), 1)

    def test_law_unknown_category_fails(self):
        self.write_article("数据库 | https://flk.npc.gov.cn/", category="nope")
        self.assertEqual(self.run_build(), 1)


class TestSchema3(BuildTestCase):
    def test_schema_version_is_3(self):
        self.assertEqual(build.SCHEMA_VERSION, 3)

    def test_roles_default_and_validation(self):
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        self.assertEqual(conn.execute("SELECT roles FROM substances WHERE id='INS202'").fetchone()[0], "additive")
        self.append("substances.csv", "CASX1,,,,测试物质,,营养强化剂,,nutrient")   # 非法用途
        self.assertNotEqual(self.run_build(), 0)

    def test_sucrose_free_moved_to_claim_notes(self):
        self.assertFalse(hasattr(build, "NON_REGULATED_CLAIMS"))
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        row = conn.execute("SELECT kind FROM claim_notes WHERE claim_key='sucrose_free'").fetchone()
        self.assertEqual(row[0], "non_regulated")

    def test_pattern_needs_rule_or_note(self):
        self.append("claim_patterns.csv", "CN,减盐,reduced_salt_unknown")
        self.assertNotEqual(self.run_build(), 0)

    def test_claim_key_cannot_be_both_rule_and_note(self):
        self.append("claim_notes.csv", "CN,low_sugar,低糖,comparative,测试")
        self.assertNotEqual(self.run_build(), 0)

    def test_allergen_term_needs_known_allergen(self):
        self.append("allergen_terms.csv", "外星豆,unknown_key,statement")
        self.assertNotEqual(self.run_build(), 0)

    def test_label_phrase_kind_checked(self):
        self.append("label_phrases.csv", "bad_kind,测试")
        self.assertNotEqual(self.run_build(), 0)

    def test_setting_sugar_cube(self):
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='sugar_cube_grams'").fetchone()[0], "4.5")

    def test_claim_note_kind_checked(self):
        self.append("claim_notes.csv", "CN,some_claim,某说法,bad_kind,测试")
        self.assertNotEqual(self.run_build(), 0)

    def test_duplicate_keys_in_new_tables_fail(self):
        for name, line in (
            ("settings.csv", "sugar_cube_grams,4.5,重复"),
            ("allergens.csv", "milk,牛奶,1\nmilk,牛奶,2"),
            ("label_phrases.csv", "notice,请注意\nnotice,请 注意"),
            ("claim_notes.csv", "CN,sucrose_free,0蔗糖,non_regulated,重复"),
        ):
            with self.subTest(file=name):
                path = self.source / name
                original = path.read_text(encoding="utf-8")
                self.append(name, line)
                self.assertNotEqual(self.run_build(), 0)
                path.write_text(original, encoding="utf-8")

    def test_claim_rule_condition_round_trips_and_may_be_empty(self):
        self.append("claim_rules.csv",
                    "CN,test_cond,测试条件,sugar,<=,1,g,per100g,GB 28050,2011,ref,,0,仅指乳品类。")
        self.append("claim_rules.csv", "CN,test_none,测试无条件,sugar,<=,1,g,per100g,GB 28050,2011,ref,,0,")
        self.append("claim_patterns.csv", "CN,测试条件声称,test_cond")
        self.append("claim_patterns.csv", "CN,测试无条件声称,test_none")
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        rows = dict(conn.execute(
            "SELECT claim_key, condition FROM claim_rules WHERE claim_key IN ('test_cond', 'test_none')").fetchall())
        self.assertEqual(rows, {"test_cond": "仅指乳品类。", "test_none": None})

    def test_claim_rules_header_has_condition(self):
        header = (DATA_DIR / "source" / "claim_rules.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(header.split(",")[-1], "condition")

    def test_allergen_term_exempt_kind(self):
        self.append("allergens.csv", "testkey,测试类,99")
        self.append("allergen_terms.csv", "测试精炼油,testkey,exempt")
        self.assertEqual(self.run_build(), 0)
        conn = sqlite3.connect(self.out / "eatclear-data.sqlite")
        row = conn.execute("SELECT allergen_key, kind FROM allergen_terms WHERE term = '测试精炼油'").fetchone()
        self.assertEqual(row, ("testkey", "exempt"))
        self.append("allergen_terms.csv", "测试精炼油2,testkey,exempted")   # 非法 kind
        self.assertNotEqual(self.run_build(), 0)

    def test_manifest_counts_new_tables(self):
        self.assertEqual(self.run_build(), 0)
        counts = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))["counts"]
        for table in ("claim_notes", "settings", "label_phrases", "allergens", "allergen_terms"):
            self.assertIn(table, counts)


NOTES_HEADER = "id,subject_type,subject_key,title,body,verified"
SOURCES_HEADER = "note_id,tier,org,title,year,url,method,study_type,verified"
NOTE = "n-sodium,nutrient,sodium,钠,钠主要来自食盐。,0"
SRC1 = ("n-sodium,1,国家卫生健康委员会,GB 28050 预包装食品营养标签通则,2011,https://sppt.cfsa.net.cn/,"
        "用每100克的钠含量和营养素参考值相比,,0")
TIER2_URL = "https://www.who.int/publications/i/item/9789240073647"
TIER3_URL = "https://doi.org/10.1000/xyz123"


def source_row(tier=1, year=2011, url="https://sppt.cfsa.net.cn/", method="用每100克的钠含量和营养素参考值相比",
               study_type="", verified=0, note_id="n-sodium"):
    return f"{note_id},{tier},某机构,某文件,{year},{url},{method},{study_type},{verified}"


class TestNotes(BuildTestCase):
    def setUp(self):
        super().setUp()
        self.write_rows("notes.csv", NOTES_HEADER, NOTE)
        self.write_rows("note_sources.csv", SOURCES_HEADER, SRC1)

    def write_rows(self, name, header, *rows):
        (self.source / name).write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")

    def set_sources(self, *rows):
        self.write_rows("note_sources.csv", SOURCES_HEADER, *rows)

    def set_notes(self, *rows):
        self.write_rows("notes.csv", NOTES_HEADER, *rows)

    def manifest(self):
        return json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))

    def db(self):
        return sqlite3.connect(self.out / "eatclear-data.sqlite")

    def test_valid_note_builds(self):
        self.assertEqual(self.run_build(), 0)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM note_sources").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT sort_order FROM note_sources").fetchone()[0], 1)
        counts = self.manifest()["counts"]
        self.assertEqual((counts["notes"], counts["note_sources"]), (1, 1))
        self.assertEqual(self.manifest()["unverified"]["notes"], 1)
        self.assertEqual(self.manifest()["unverified"]["note_sources"], 1)

    def test_sort_order_follows_file_order_per_note(self):
        self.set_notes(NOTE, "n-calcium,nutrient,calcium,钙,钙对骨骼有用。,0")
        self.set_sources(SRC1, source_row(note_id="n-calcium"),
                         source_row(tier=2, url=TIER2_URL, year=2020))
        self.assertEqual(self.run_build(), 0)
        rows = self.db().execute(
            "SELECT note_id, sort_order, tier FROM note_sources ORDER BY note_id, sort_order").fetchall()
        self.assertEqual(rows, [("n-calcium", 1, 1), ("n-sodium", 1, 1), ("n-sodium", 2, 2)])

    def test_non_tip_note_needs_source(self):
        self.set_sources()
        self.assertNotEqual(self.run_build(), 0)

    def test_tip_needs_no_source(self):
        self.set_notes("t-1,tip,t-1,小知识,这是一条小知识。,0")
        self.set_sources()
        self.assertEqual(self.run_build(), 0)

    def test_unknown_subject_rejected(self):
        self.set_notes("n-x,nutrient,not_a_nutrient,某,说明。,0")
        self.set_sources(source_row(note_id="n-x"))
        self.assertNotEqual(self.run_build(), 0)

    def test_subject_keys_checked_per_type(self):
        # milk、防腐剂、INS202 都来自随仓库提供的 source/ 数据
        cases = {
            "allergen": ("milk", "not_an_allergen"),
            "function_class": ("防腐剂", "不存在的类别"),
            "substance": ("INS202", "INS999"),
        }
        for subject_type, (good, bad) in cases.items():
            for key, expected_ok in ((good, True), (bad, False)):
                with self.subTest(subject_type=subject_type, key=key):
                    self.set_notes(f"n-x,{subject_type},{key},某,说明。,0")
                    self.set_sources(source_row(note_id="n-x"))
                    code = self.run_build()
                    self.assertEqual(code == 0, expected_ok)

    def test_unknown_subject_type_rejected(self):
        self.set_notes("n-x,mineral,sodium,某,说明。,0")
        self.set_sources(source_row(note_id="n-x"))
        self.assertNotEqual(self.run_build(), 0)

    def test_duplicate_note_id_or_subject_rejected(self):
        for second in ("n-sodium,nutrient,calcium,钙,说明。,0",      # id 重复
                       "n-other,nutrient,sodium,钠,说明。,0"):       # 同一个主题重复
            with self.subTest(second=second):
                self.set_notes(NOTE, second)
                self.assertNotEqual(self.run_build(), 0)

    def test_source_for_unknown_note_rejected(self):
        self.set_sources(SRC1, source_row(note_id="n-missing"))
        self.assertNotEqual(self.run_build(), 0)

    def test_bad_flags_and_year_rejected(self):
        for row in (source_row(verified="yes"), source_row(year="去年"), source_row(tier=4), source_row(tier="一")):
            with self.subTest(row=row):
                self.set_sources(row)
                self.assertNotEqual(self.run_build(), 0)
        self.set_notes("n-sodium,nutrient,sodium,钠,钠主要来自食盐。,yes")
        self.set_sources(SRC1)
        self.assertNotEqual(self.run_build(), 0)

    def test_tier_domain_enforced(self):
        cases = (
            (3, TIER2_URL),                                  # 级别 3 只收 doi.org
            (2, "https://doi.org/10.1000/xyz123"),           # 级别 2 不收 doi.org
            (1, "https://www.who.int/x"),                    # 级别 1 不收 who.int
            (2, "http://www.who.int/x"),                     # 必须 https
        )
        for tier, url in cases:
            with self.subTest(tier=tier, url=url):
                study = "系统综述" if tier == 3 else ""
                self.set_sources(source_row(tier=tier, url=url, study_type=study))
                self.assertNotEqual(self.run_build(), 0)

    def test_tier_domains_accepted(self):
        for tier, url, study in ((1, "https://www.cnsoc.org/x", ""), (1, "https://www.nhc.gov.cn/x", ""),
                                 (2, "https://www.efsa.europa.eu/x", ""), (2, "https://www.fda.gov/x", ""),
                                 (3, TIER3_URL, "系统综述")):
            with self.subTest(tier=tier, url=url):
                self.set_sources(source_row(tier=tier, url=url, study_type=study, year=2020))
                self.assertEqual(self.run_build(), 0)

    def test_tier3_needs_study_type(self):
        for study in ("", "队列研究"):
            with self.subTest(study_type=study):
                self.set_sources(source_row(tier=3, url=TIER3_URL, study_type=study, year=2020))
                self.assertNotEqual(self.run_build(), 0)
        for study in ("系统综述", "荟萃分析"):
            with self.subTest(study_type=study):
                self.set_sources(source_row(tier=3, url=TIER3_URL, study_type=study, year=2020))
                self.assertEqual(self.run_build(), 0)

    def test_tier3_dated_flag(self):
        with mock.patch.object(build, "current_year", return_value=2030):
            for year, expected in ((2019, 1), (2020, 0)):      # 构建年份 - 11 / - 10
                with self.subTest(year=year):
                    self.set_sources(source_row(tier=3, url=TIER3_URL, study_type="系统综述", year=year))
                    self.assertEqual(self.run_build(), 0)
                    self.assertEqual(self.db().execute("SELECT is_dated FROM note_sources").fetchone()[0], expected)
            # 级别 1 年份再老也不算过时
            self.set_sources(source_row(tier=1, year=1990))
            self.assertEqual(self.run_build(), 0)
            self.assertEqual(self.db().execute("SELECT is_dated FROM note_sources").fetchone()[0], 0)

    def test_current_year_is_utc_year(self):
        import datetime
        self.assertEqual(build.current_year(), datetime.datetime.now(datetime.timezone.utc).year)

    def test_method_required(self):
        self.set_sources(source_row(method=""))
        self.assertNotEqual(self.run_build(), 0)

    def test_banned_word_warns_not_fails(self):
        self.set_notes("n-sodium,nutrient,sodium,钠,这种成分有害。,0")
        self.assertEqual(self.run_build(), 0)
        warnings = self.manifest()["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("n-sodium", warnings[0])
        self.assertIn("有害", warnings[0])

    def test_banned_word_in_title_warns(self):
        self.set_notes("n-sodium,nutrient,sodium,钠的风险,钠主要来自食盐。,0")
        self.assertEqual(self.run_build(), 0)
        warnings = self.manifest()["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("风险", warnings[0])

    def test_banned_word_inside_quotes_ok(self):
        self.set_notes("n-sodium,nutrient,sodium,钠,IARC 把它列为“可能对人类致癌”，《食品安全法》另有规定。,0")
        self.assertEqual(self.run_build(), 0)
        self.assertEqual(self.manifest()["warnings"], [])

    def test_warnings_key_always_present(self):
        self.assertEqual(self.run_build(), 0)
        self.assertEqual(self.manifest()["warnings"], [])


class TestBannedWords(unittest.TestCase):
    def test_plain_hits(self):
        self.assertEqual(build.banned_words_in("这种成分有害，而且超标。"), ["有害", "超标"])

    def test_longest_word_wins_and_deduplicates(self):
        self.assertEqual(build.banned_words_in("不安全。不安全。"), ["不安全"])
        self.assertEqual(build.banned_words_in("不合格"), ["不合格"])
        self.assertEqual(build.banned_words_in("安全和合格"), ["安全", "合格"])

    def test_quoted_text_is_skipped(self):
        for text in ("“有害”", "\"有害\"", "「有害」", "《食品安全法》"):
            with self.subTest(text=text):
                self.assertEqual(build.banned_words_in(text), [])

    def test_text_outside_quotes_still_checked(self):
        self.assertEqual(build.banned_words_in("《食品安全法》说这样有害"), ["有害"])

    def test_clean_text(self):
        self.assertEqual(build.banned_words_in("钠主要来自食盐。"), [])


class TestNoteLinks(BuildTestCase):
    def test_read_note_links(self):
        (self.source / "notes.csv").write_text(f"{NOTES_HEADER}\n{NOTE}\n", encoding="utf-8")
        (self.source / "note_sources.csv").write_text(f"{SOURCES_HEADER}\n{SRC1}\n", encoding="utf-8")
        self.assertEqual(build.read_note_links(self.source), [
            ("n-sodium", "GB 28050 预包装食品营养标签通则", "https://sppt.cfsa.net.cn/")])

    def test_shipped_note_files_have_headers(self):
        links = build.read_note_links(DATA_DIR / "source")
        self.assertTrue(links)
        for _, _, url in links:
            self.assertTrue(url.startswith("https://"), url)
        for name, header in (("notes.csv", NOTES_HEADER), ("note_sources.csv", SOURCES_HEADER)):
            with self.subTest(file=name):
                first = (DATA_DIR / "source" / name).read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first, header)


class TestLinkDomain(unittest.TestCase):
    def test_allowed(self):
        for url in ("https://flk.npc.gov.cn/detail", "https://www.gov.cn/x.htm",
                    "https://sppt.cfsa.net.cn:8086/db", "https://www.12315.cn/", "https://gov.cn/"):
            with self.subTest(url=url):
                self.assertIsNone(build.link_problem(url))

    def test_rejected(self):
        for url in ("http://www.gov.cn/", "https://evilgov.cn/", "https://gov.cn.example.com/",
                    "ftp://www.gov.cn/", "https:///nohost", "flk.npc.gov.cn"):
            with self.subTest(url=url):
                self.assertIsNotNone(build.link_problem(url))


class TestCheckLinks(unittest.TestCase):
    def setUp(self):
        import check_links
        self.check_links = check_links

    def test_reports_broken_links(self):
        links = [("cn-01", "好的", "https://a.gov.cn/"), ("cn-02", "坏的", "https://b.gov.cn/")]

        def fake_fetch(url):
            if "a.gov.cn" in url:
                return 200, "https://a.gov.cn/"
            raise OSError("连接被拒绝")

        failures = self.check_links.check_all(links, fetch=fake_fetch)
        self.assertEqual(len(failures), 1)
        self.assertIn("cn-02", failures[0])

    def test_http_error_status_is_failure(self):
        failures = self.check_links.check_all(
            [("cn-01", "404", "https://a.gov.cn/")], fetch=lambda url: (404, url))
        self.assertEqual(len(failures), 1)

    def test_redirect_off_allowed_domain_is_failure(self):
        failures = self.check_links.check_all(
            [("cn-01", "跳走", "https://a.gov.cn/")], fetch=lambda url: (200, "https://ads.example.com/"))
        self.assertEqual(len(failures), 1)

    def test_bot_challenge_is_warning_not_failure(self):
        # 部分政府网站对脚本返回 403/412 的验证页，浏览器可以正常打开
        warnings = []
        failures = self.check_links.check_all(
            [("cn-01", "卫健委", "https://www.nhc.gov.cn/x.shtml")],
            fetch=lambda url: (412, url), warnings=warnings)
        self.assertEqual(failures, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("浏览器", warnings[0])

    def test_note_links_may_redirect_to_other_https_domains(self):
        # DOI 链接会跳转到出版社的网站，所以说明的链接只要求跳转后仍是 https
        links = [("n-1", "论文", "https://doi.org/10.1000/xyz123")]
        failures = self.check_links.check_all(
            links, fetch=lambda url: (200, "https://publisher.example.com/paper"), strict_redirects=False)
        self.assertEqual(failures, [])
        failures = self.check_links.check_all(
            links, fetch=lambda url: (200, "http://publisher.example.com/paper"), strict_redirects=False)
        self.assertEqual(len(failures), 1)

    def test_collect_links_includes_notes(self):
        with mock.patch.object(build, "read_law_links", return_value=[("a", "法", "https://a.gov.cn/")]), \
             mock.patch.object(build, "read_note_links",
                               return_value=[("n", "论文", "https://doi.org/1"), ("n2", "同一篇", "https://doi.org/1")]):
            law, notes = self.check_links.collect_links()
        self.assertEqual(law, [("a", "法", "https://a.gov.cn/")])
        self.assertEqual(notes, [("n", "论文", "https://doi.org/1")])


if __name__ == "__main__":
    unittest.main()
