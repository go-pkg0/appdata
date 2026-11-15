"""M1 的数据内容测试：对真实的 source/ 构建一次，检查 M1 功能需要的数据都在。

运行: python3 -m unittest discover -s eatclear/tests
"""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DATA_DIR))

import build  # noqa: E402

CURRENT_28050 = "2011"


class TestM1Content(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        out = cls.tmp / "dist"
        orig_source = build.SOURCE
        build.SOURCE = DATA_DIR / "source"
        try:
            code = build.main(["--version", "test", "--out", str(out)])
        finally:
            build.SOURCE = orig_source
        if code != 0:
            raise AssertionError("真实 source/ 构建失败")
        cls.manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        cls.conn = sqlite3.connect(out / "eatclear-data.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp)

    def column(self, sql, *args):
        return [row[0] for row in self.conn.execute(sql, args).fetchall()]

    def test_nutrient_names(self):
        expected = {"饱和脂肪": "saturated_fat", "饱和脂肪酸": "saturated_fat",
                    "反式脂肪": "trans_fat", "反式脂肪酸": "trans_fat", "胆固醇": "cholesterol",
                    "维生素a": "vitamin_a", "维生素d": "vitamin_d", "维生素e": "vitamin_e",
                    "维生素b1": "vitamin_b1", "维生素b2": "vitamin_b2", "维生素c": "vitamin_c",
                    "烟酸": "niacin", "叶酸": "folate", "磷": "phosphorus", "铁": "iron", "锌": "zinc",
                    "钾": "potassium", "镁": "magnesium", "二十二碳六烯酸": "dha", "dha": "dha"}
        actual = dict(self.conn.execute("SELECT name_norm, nutrient FROM nutrient_names").fetchall())
        for name_norm, nutrient in expected.items():
            with self.subTest(name=name_norm):
                self.assertIn(name_norm, actual)
                self.assertEqual(actual[name_norm], nutrient)

    def test_fiber_and_comparative_claims(self):
        patterns = dict(self.conn.execute(
            "SELECT pattern_norm, claim_key FROM claim_patterns WHERE jurisdiction = 'CN'").fetchall())
        for pattern, key in (("高膳食纤维", "high_fiber"), ("富含膳食纤维", "high_fiber"),
                             ("含膳食纤维", "source_of_fiber"), ("膳食纤维来源", "source_of_fiber")):
            with self.subTest(pattern=pattern):
                self.assertEqual(patterns.get(build.normalize(pattern)), key)
        for key in ("high_fiber", "source_of_fiber"):
            with self.subTest(rule=key):
                bases = self.column(
                    "SELECT basis FROM claim_rules WHERE jurisdiction = 'CN' AND claim_key = ? "
                    "AND standard = 'GB 28050' AND standard_version = ?", key, CURRENT_28050)
                self.assertCountEqual(bases, ["per100g", "per100ml"])
        notes = {key: (kind, explain) for key, kind, explain in self.conn.execute(
            "SELECT claim_key, kind, explain FROM claim_notes WHERE jurisdiction = 'CN'").fetchall()}
        for pattern in ("减糖", "减少糖", "少糖", "减盐", "减钠", "减脂", "减少脂肪"):
            with self.subTest(pattern=pattern):
                key = patterns.get(build.normalize(pattern))
                self.assertIsNotNone(key, f"claim_patterns 里没有 {pattern}")
                self.assertIn(key, notes)
                kind, explain = notes[key]
                self.assertEqual(kind, "comparative")
                self.assertTrue(explain.strip())

    def test_every_gb28050_content_claim_has_patterns(self):
        missing = self.column(
            "SELECT DISTINCT r.claim_key FROM claim_rules r WHERE NOT EXISTS ("
            "SELECT 1 FROM claim_patterns p WHERE p.jurisdiction = r.jurisdiction AND p.claim_key = r.claim_key)")
        self.assertEqual(missing, [])

    def test_fortifiers(self):
        count = self.conn.execute(
            "SELECT COUNT(*) FROM substances WHERE roles IN ('fortifier', 'additive|fortifier')").fetchone()[0]
        self.assertGreaterEqual(count, 40)
        alias_norms = set(self.column("SELECT alias_norm FROM aliases"))
        for name in ("磷酸氢钙", "碳酸钙", "葡萄糖酸锌", "富马酸亚铁", "棕榈酸维生素A",
                     "胆钙化醇", "烟酸", "核黄素", "盐酸硫胺素", "二十二碳六烯酸油脂"):
            with self.subTest(alias=name):
                self.assertIn(build.normalize(name), alias_norms)
        roles = self.column(
            "SELECT s.roles FROM substances s JOIN aliases a ON a.substance_id = s.id WHERE a.alias_norm = ?",
            build.normalize("抗坏血酸"))
        self.assertEqual(roles, ["additive|fortifier"])

    def test_allergens(self):
        rows = self.conn.execute("SELECT key, name_zh FROM allergens ORDER BY sort_order").fetchall()
        self.assertEqual(rows, [
            ("gluten", "含麸质的谷物"), ("crustacean", "甲壳类"), ("fish", "鱼"), ("egg", "蛋"),
            ("peanut", "花生"), ("soy", "大豆"), ("milk", "乳"), ("nut", "坚果")])
        terms = {}
        for norm, key, kind in self.conn.execute(
                "SELECT term_norm, allergen_key, kind FROM allergen_terms").fetchall():
            terms[(norm, kind)] = key
        for term, key in (("麸质", "gluten"), ("大豆", "soy"), ("乳制品", "milk"), ("鱼类", "fish"), ("蛋类", "egg")):
            with self.subTest(statement=term):
                self.assertEqual(terms.get((build.normalize(term), "statement")), key)
        for term, key in (("小麦粉", "gluten"), ("乳粉", "milk"), ("大豆", "soy"), ("花生", "peanut")):
            with self.subTest(ingredient=term):
                self.assertEqual(terms.get((build.normalize(term), "ingredient")), key)

    def test_label_phrases(self):
        expected = {
            "allergen_section": ("致敏物质", "致敏原信息", "过敏原"),
            "allergen_may_contain": ("同一生产线", "可能含有", "生产设备"),
            "audience": ("适宜人群", "适用于", "不适宜人群"),
            "notice": ("儿童青少年应避免过量摄入盐油糖",),
            "standard_code": ("产品标准代号", "执行标准", "产品标准号"),
            "product_name": ("产品名称", "品名"),
        }
        for kind, phrases in expected.items():
            present = set(self.column("SELECT phrase_norm FROM label_phrases WHERE kind = ?", kind))
            for phrase in phrases:
                with self.subTest(kind=kind, phrase=phrase):
                    self.assertIn(build.normalize(phrase), present)

    def notes_with_tier(self, subject_type, tier):
        return set(self.column(
            "SELECT DISTINCT n.subject_key FROM notes n JOIN note_sources s ON s.note_id = n.id "
            "WHERE n.subject_type = ? AND s.tier = ?", subject_type, tier))

    def test_notes_coverage(self):
        def keys(subject_type):
            return set(self.column("SELECT subject_key FROM notes WHERE subject_type = ?", subject_type))

        self.assertLessEqual({"sugar", "sodium", "fat", "saturated_fat", "trans_fat"}, keys("nutrient"))
        self.assertEqual(set(self.column("SELECT key FROM allergens")) - keys("allergen"), set())
        self.assertEqual(len(keys("allergen")), 8)
        classes = set(self.column("SELECT DISTINCT function_class FROM substances"))
        self.assertIn("营养强化剂", classes)
        self.assertEqual(classes - keys("function_class"), set())

        without_tier1 = self.column(
            "SELECT id FROM notes n WHERE subject_type != 'tip' AND NOT EXISTS ("
            "SELECT 1 FROM note_sources s WHERE s.note_id = n.id AND s.tier = 1)")
        self.assertEqual(without_tier1, [])

        self.assertLessEqual({"sugar", "sodium", "trans_fat"}, self.notes_with_tier("nutrient", 2))

        sweeteners_with_tier2 = self.column(
            "SELECT DISTINCT n.subject_key FROM notes n JOIN substances sub ON sub.id = n.subject_key "
            "JOIN note_sources s ON s.note_id = n.id "
            "WHERE n.subject_type = 'substance' AND sub.function_class = '甜味剂' AND s.tier = 2")
        self.assertGreaterEqual(len(sweeteners_with_tier2), 3)

        tier3 = self.conn.execute("SELECT COUNT(*) FROM note_sources WHERE tier = 3").fetchone()[0]
        self.assertGreaterEqual(tier3, 3)
        tier3_subjects = self.conn.execute(
            "SELECT n.subject_type, n.subject_key, sub.function_class FROM notes n "
            "JOIN note_sources s ON s.note_id = n.id "
            "LEFT JOIN substances sub ON n.subject_type = 'substance' AND sub.id = n.subject_key "
            "WHERE s.tier = 3").fetchall()
        for subject_type, subject_key, function_class in tier3_subjects:
            with self.subTest(tier3=f"{subject_type}:{subject_key}"):
                self.assertTrue(
                    (subject_type == "nutrient" and subject_key in ("sugar", "sodium", "trans_fat"))
                    or (subject_type == "substance" and function_class == "甜味剂")
                    or (subject_type == "function_class" and subject_key == "甜味剂"))

        self.assertGreaterEqual(len(keys("tip")), 5)

    def test_all_new_rows_unverified(self):
        for table in ("notes", "note_sources"):
            with self.subTest(table=table):
                self.assertEqual(self.column(f"SELECT COUNT(*) FROM {table} WHERE verified != 0"), [0])

    def test_no_banned_word_warnings(self):
        self.assertEqual(self.manifest["warnings"], [])


if __name__ == "__main__":
    unittest.main()
