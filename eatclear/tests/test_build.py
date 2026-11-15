"""数据构建脚本的测试。运行: python3 -m unittest discover -s eatclear/tests"""
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


if __name__ == "__main__":
    unittest.main()
