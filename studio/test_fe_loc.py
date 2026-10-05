"""fe_loc: what counts as a code line, which area a file is in, and the per-commit counts."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fe_loc


class CountTests(unittest.TestCase):
    def test_comments_and_blanks_do_not_count(self):
        rust = "// a comment\n\nfn a() {\n    /* one */ let x = 1;\n    /*\n     * block\n     */\n    x\n}\n"
        self.assertEqual(fe_loc.count(rust, "slash"), (4, 0))
        py = '"""Doc\nstring."""\n# comment\n\ndef f():\n    """One line."""\n    return 1\n'
        self.assertEqual(fe_loc.count(py, "hash"), (2, 0))
        self.assertEqual(fe_loc.count("# Title\n\ntext\n", "md"), (2, 0))

    def test_cfg_test_mod_counts_as_tests(self):
        src = "fn a() {}\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {\n        a();\n    }\n}\nfn b() {}\n"
        code, tests = fe_loc.count(src, "slash")
        self.assertEqual(code, 9)
        self.assertEqual(tests, 6)  # the mod line to its closing brace

    def test_areas(self):
        self.assertEqual(fe_loc.area("engine/crates/app/src/main.rs"), "crates")
        self.assertEqual(fe_loc.area("engine/crates/app/src/body/tests.rs"), "rust-tests")
        self.assertEqual(fe_loc.area("engine/crates/app/src/regen_defaults_tests.rs"), "rust-tests")
        self.assertEqual(fe_loc.area("engine/shaders/march.slang"), "shaders")
        self.assertEqual(fe_loc.area("engine/tools/fe_loc.py"), "tools")
        self.assertEqual(fe_loc.area("engine/tools/test_fe_loc.py"), "tool-tests")
        self.assertEqual(fe_loc.area(".agents/skills/task/SKILL.md"), "skills")
        self.assertEqual(fe_loc.area("AGENTS.md"), "docs")
        for skipped in ("docs/roadmap.md", "docs/decisions/lighting.md", "docs/roadmap-summary.md",
                        ".claude/skills/task/SKILL.md", "docs/world/nordlys-world-design.md",
                        "engine/vendor/x/lib.rs", "engine/Cargo.lock", "engine/tests/replays/a.txt"):
            self.assertIsNone(fe_loc.area(skipped), skipped)


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        root = patch.object(fe_loc, "ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)
        self.run_git("init", "-q", "-b", "main")
        self.run_git("config", "user.email", "t@t")
        self.run_git("config", "user.name", "t")

    def run_git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def commit(self, files, msg):
        for name, text in files.items():
            p = self.root / name
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, newline="\n")
        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", msg)

    def test_at_diff_and_commits_agree(self):
        dup = "fn a() {\n    one();\n    two();\n}\nfn b() {\n    one();\n    two();\n}\n"
        self.commit({"engine/crates/x/src/lib.rs": dup, "engine/tools/t.py": "x = 1\n", "docs/n.md": "a\n"}, "first")
        merged = "// both, now one\nfn ab() {\n    one();\n    two();\n}\n"
        self.commit({"engine/crates/x/src/lib.rs": merged, "engine/tools/t.py": None}, "merge a and b")
        before, after = fe_loc.at("HEAD~1"), fe_loc.at("HEAD")
        self.assertEqual(before["areas"]["crates"], 8)
        self.assertEqual(after["areas"]["crates"], 4)
        self.assertEqual(after["areas"]["tools"], 0)
        d = fe_loc.diff("HEAD~1", "HEAD")
        self.assertEqual(d["code"], -5)
        rows = fe_loc.commits("HEAD~1..HEAD")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code_net"], -5)  # the added comment does not count
        self.assertEqual(rows[0]["net"], {"crates": -4, "tools": -1})


if __name__ == "__main__":
    unittest.main()
