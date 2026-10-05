"""fe_health: a real refactor scores, deleting a test fails the gate, joining lines scores nothing."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fe_health
import fe_loc

HEAVY = """fn {name}(v: &[i32]) -> i32 {{
    let mut s = 0;
    for x in v {{
        if *x > 0 {{
            if *x % 2 == 0 {{
                for y in 0..*x {{
                    if y > 3 {{
                        s += y;
                    }} else {{
                        s -= 1;
                    }}
                }}
            }}
        }}
    }}
    s
}}
"""
TESTS = """
#[cfg(test)]
mod tests {
    #[test]
    fn one() {
        assert_eq!(super::heavy_a(&[2, 4]), 1);
    }
    #[test]
    fn two() {
        assert_eq!(super::heavy_b(&[]), 0);
    }
}
"""


@unittest.skipUnless(shutil.which("rust-code-analysis-cli") and shutil.which("jscpd"), "the analysers are not installed")
class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for target, name in ((fe_health, "ROOT"), (fe_loc, "ROOT")):
            p = patch.object(target, name, self.root)
            p.start()
            self.addCleanup(p.stop)
        cache = Path(self.temp.name) / "health"
        cache.mkdir()
        p = patch.object(fe_health, "store", lambda: cache)
        p.start()
        self.addCleanup(p.stop)
        for tool in ("vulture", "machete"):
            p = patch.object(fe_health, tool, lambda root: None)
            p.start()
            self.addCleanup(p.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, text, msg):
        p = self.root / "engine/crates/x/src/lib.rs"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, newline="\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def test_dedupe_scores_and_passes(self):
        a = self.commit(HEAVY.format(name="heavy_a") + HEAVY.format(name="heavy_b") + TESTS, "two copies")
        b = self.commit(HEAVY.format(name="heavy_a") + "fn heavy_b(v: &[i32]) -> i32 {\n    heavy_a(v)\n}\n" + TESTS,
                        "one copy")
        d = fe_health.delta(a, b)
        self.assertGreater(d["score"], 0, d)
        self.assertTrue(d["passed"], d)
        self.assertLess(d["after"]["dup_tokens"], d["before"]["dup_tokens"])

    def test_deleting_a_test_fails_the_gate(self):
        a = self.commit(HEAVY.format(name="heavy_a") + HEAVY.format(name="heavy_b") + TESTS, "two copies")
        fewer = TESTS.replace("    #[test]\n    fn two() {\n        assert_eq!(super::heavy_b(&[]), 0);\n    }\n", "")
        b = self.commit(HEAVY.format(name="heavy_a") + HEAVY.format(name="heavy_b") + fewer, "drop a test")
        d = fe_health.delta(a, b)
        self.assertFalse(d["passed"])
        self.assertEqual(d["score"], 0.0)
        self.assertTrue(any("fewer tests" in g for g in d["gate"]), d["gate"])

    def test_joining_lines_scores_nothing(self):
        a = self.commit(HEAVY.format(name="heavy_a") + HEAVY.format(name="heavy_b") + TESTS, "two copies")
        squeezed = HEAVY.format(name="heavy_a").replace("let mut s = 0;\n    for", "let mut s = 0; for") \
                                               .replace("s += y;\n                    }", "s += y; }")
        b = self.commit(squeezed + HEAVY.format(name="heavy_b") + TESTS, "squeeze")
        d = fe_health.delta(a, b)
        self.assertEqual(d["score"], 0.0, d)

    def test_new_function_past_a_limit_trips_the_ratchet(self):
        a = self.commit("fn small() -> i32 {\n    1\n}\n" + TESTS.replace("heavy_a(&[2, 4]), 1", "small(), 1")
                        .replace("heavy_b(&[]), 0", "small(), 1"), "small")
        b = self.commit("fn small() -> i32 {\n    1\n}\n" + HEAVY.format(name="heavy_a") +
                        TESTS.replace("heavy_a(&[2, 4]), 1", "small(), 1").replace("heavy_b(&[]), 0", "small(), 1"), "heavy")
        d = fe_health.delta(a, b)
        # D280: the ratchet is advisory, to be justified or undone; the score still charges it.
        self.assertTrue(d["passed"], d["gate"])
        self.assertLess(d["score"], 0)
        self.assertTrue(any("starts past the cognitive limit" in r for r in d["ratchet"]), d["ratchet"])
        self.assertEqual([(w["name"], w["measure"], w["before"]) for w in d["worse"]], [("heavy_a", "cognitive", None)])

    @unittest.skipUnless(shutil.which("cargo-modules"), "cargo-modules is not installed")
    def test_a_new_cycle_between_modules_fails_the_gate(self):
        (self.root / "engine/crates/app/src").mkdir(parents=True)
        (self.root / "engine/Cargo.toml").write_text('[workspace]\nmembers = ["crates/app"]\nresolver = "2"\n')
        (self.root / "engine/crates/app/Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n')
        main = "mod a;\nmod b;\nfn main() {\n    a::f();\n}\n"
        files = {"main.rs": main, "a.rs": "use crate::b::g;\npub fn f() {\n    g();\n}\n",
                 "b.rs": "pub fn g() {}\n"}
        for name, text in files.items():
            (self.root / "engine/crates/app/src" / name).write_text(text, newline="\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a uses b")
        a = self.git("rev-parse", "HEAD")
        (self.root / "engine/crates/app/src/b.rs").write_text("use crate::a::f;\npub fn g() {}\npub fn h() {\n    f();\n}\n",
                                                              newline="\n")
        self.git("commit", "-qam", "b uses a")
        d = fe_health.delta(a, self.git("rev-parse", "HEAD"))
        self.assertFalse(d["passed"])
        self.assertTrue(any("new dependency cycle" in g for g in d["gate"]), d["gate"])
        self.assertEqual((d["before"]["cycle_edges"], d["after"]["cycle_edges"]), (0, 2))


class CouplingTests(unittest.TestCase):
    def test_penalty_thresholds(self):
        fn = {"cognitive": 20, "lines": 52, "args": 7}
        self.assertAlmostEqual(fe_health.penalty(fn), 5 + 2 + 2)
        self.assertEqual(fe_health.penalty({"cognitive": 15, "lines": 40, "args": 5}), 0)

    def test_only_the_edges_of_a_loop_are_cyclic(self):
        edges = [["a", "b"], ["b", "c"], ["c", "a"], ["c", "d"], ["d", "e"]]
        self.assertEqual(fe_health.cyclic(edges), {("a", "b"), ("b", "c"), ("c", "a")})
        self.assertEqual(fe_health.cyclic([["a", "b"]]), set())


if __name__ == "__main__":
    unittest.main()
