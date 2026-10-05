#!/usr/bin/env python3
"""How big the project is, by area, and what each commit did to it (the cleanup crew's scale).

    fe_loc.py at [REV] [--json]          code lines per area at a revision (default HEAD)
    fe_loc.py diff A [B] [--json]        each area's change from A to B (default HEAD)
    fe_loc.py commits A..B [--json]      per commit: code lines added, deleted and net, by area

A code line is one that is neither blank nor only a comment, so deleting
comments or blank lines never scores, and joining lines is the only way to
cheat (the crew's skill forbids it). Comments: `//` and `/* */` in Rust,
Slang, C and JavaScript; `#` and docstrings in Python and PowerShell. A
Markdown line counts when it is not blank.

The areas (`area`): crates (Rust outside tests), rust-tests (`tests.rs`,
`*_tests.rs`, and the `#[cfg(test)] mod` blocks inside other files),
shaders, tools (engine/tools outside `test_*`), tool-tests, skills
(`.agents/skills`), docs (the hand-written Markdown). Not counted: the
append-only histories (`docs/decisions/`, `docs/roadmap.md`), the generated
files (`*-summary.md`, `.claude/skills`, `Cargo.lock`), the world docs
(exported from Claude), vendored code, replays, the closed Unity project.

Revisions are read straight from git (`ls-tree`, `cat-file --batch`), so
any commit counts without a checkout. `commits` reads `git log -p -U0` and
counts each added or removed line by the same rule, line by line: a line
inside a block comment that does not start like one counts as code there.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio_config  # noqa: E402  (the checkout, and the game's areas: [loc] in studio.toml)

ROOT = studio_config.repo_root()
TAG = "[fe-loc]"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# The code areas ([[loc.area]]), first match wins: a path under `prefix` with one of `ext` counts in
# `name`, or in `tests_area` when it matches `tests`; `inline_tests` moves a Rust file's #[cfg(test)]
# mod lines there too. With none configured, the studio's own tools are the code.
RULES = studio_config.get("loc.area") or [
    {"name": "tools", "prefix": studio_config.studio_rel() + "/", "ext": [".py", ".ps1", ".js", ".css", ".html"],
     "tests_area": "tool-tests", "tests": r"(^|/)test[_-][^/]*$"}]
TESTS = tuple(dict.fromkeys(r["tests_area"] for r in RULES if r.get("tests_area")))
CODE = tuple(dict.fromkeys(a for r in RULES for a in (r["name"], r.get("tests_area")) if a))
AREAS = (*CODE, "skills", "docs")
SKILLS = studio_config.get("docs.skills_src", ".agents/skills").rstrip("/") + "/"
# Never counted: the append-only histories, generated files, scratch, plus the game's ([loc] skip).
SKIP = re.compile("|".join([
    "^" + re.escape(studio_config.rel(studio_config.artifacts_dir())) + "/", r"^\.claude/",
    "^" + re.escape(studio_config.get("docs.decisions_dir", "docs/decisions")) + "/",
    "^" + re.escape(studio_config.get("docs.roadmap", "docs/roadmap.md")) + "$", r"-summary\.md$",
    *studio_config.get("loc.skip", [])]))
SLASH = {".rs", ".slang", ".c", ".cpp", ".hpp", ".h", ".js", ".css"}
HASH = {".py", ".ps1"}


def area(path: str) -> str | None:
    """The area a tracked path counts in, or None when it is not counted."""
    if SKIP.search(path):
        return None
    ext = Path(path).suffix
    for r in RULES:
        if path.startswith(r["prefix"]) and ext in r["ext"]:
            return r["tests_area"] if r.get("tests") and re.search(r["tests"], path) else r["name"]
    if ext == ".md":
        return "skills" if path.startswith(SKILLS) else "docs"
    return None


INLINE_TESTS = {r["name"]: r["tests_area"] for r in RULES if r.get("inline_tests")}


def kind(path: str) -> str:
    ext = Path(path).suffix
    return "md" if ext == ".md" else "hash" if ext in HASH else "html" if ext == ".html" else "slash"


def comment_only(line: str, k: str) -> bool:
    """Whether one line, seen alone, is blank or only a comment (the rule `commits` uses)."""
    s = line.strip()
    if not s:
        return True
    if k == "md":
        return False
    if k == "hash":
        return s.startswith("#") or s in ('"""', "'''") or (s[:3] in ('"""', "'''") and s.endswith(s[:3]) and len(s) >= 6)
    if k == "html":
        return s.startswith("<!--")
    return s.startswith(("//", "/*", "*", "*/"))


def count(text: str, k: str) -> tuple[int, int]:
    """(code lines, of them inside a Rust `#[cfg(test)] mod`) in one file."""
    code = tests = 0
    block = None          # the closing of an open block comment or docstring
    test_depth = None     # brace depth at which a #[cfg(test)] mod closes
    pending_test = False
    depth = 0
    for line in text.splitlines():
        s = line.strip()
        if block:
            if block in s:
                rest = s.split(block, 1)[1].strip()
                block = None
                if rest and not comment_only(rest, k):
                    code += 1
            continue
        if not s:
            continue
        if k == "md":
            code += 1
            continue
        if k == "hash":
            if s.startswith("#"):
                continue
            q = s[:3]
            if q in ('"""', "'''"):
                if not (len(s) >= 6 and s.endswith(q)):
                    block = q
                continue
            code += 1
            continue
        if k == "html":
            code += 1
            continue
        if s.startswith("//"):
            continue
        if s.startswith("/*"):
            if "*/" not in s[2:]:
                block = "*/"
            elif s.split("*/", 1)[1].strip():
                code += 1
            continue
        code += 1
        if s.startswith("#[cfg(test)]"):
            pending_test = True
        if test_depth is not None:
            tests += 1
        opened = depth
        depth += s.count("{") - s.count("}")
        if pending_test and re.match(r"(pub(\([^)]*\))?\s+)?mod\s+\w+\s*\{", s):
            pending_test = False
            if test_depth is None:
                test_depth = opened
                tests += 1
        elif pending_test and not s.startswith("#["):
            pending_test = False
        if test_depth is not None and depth <= test_depth:
            test_depth = None
    return code, tests


def git(*args: str, data: bytes | None = None) -> bytes:
    r = subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise SystemExit(f"{TAG} git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def at(rev: str = "HEAD") -> dict:
    """Code lines and files per area at a revision, and the largest files."""
    paths = [p for p in git("ls-tree", "-r", "--name-only", rev).decode().splitlines() if area(p)]
    out = git("cat-file", "--batch", data="".join(f"{rev}:{p}\n" for p in paths).encode())
    lines: dict = defaultdict(int)
    files: dict = defaultdict(int)
    sizes = []
    pos = 0
    for p in paths:
        nl = out.index(b"\n", pos)
        size = int(out[pos:nl].split()[2])
        body = out[nl + 1:nl + 1 + size].decode("utf-8", errors="replace")
        pos = nl + 1 + size + 1
        a = area(p)
        code, tests = count(body, kind(p))
        if a in INLINE_TESTS and tests:
            lines[INLINE_TESTS[a]] += tests
            code -= tests
        lines[a] += code
        files[a] += 1
        sizes.append((code + tests, p))
    sizes.sort(reverse=True)
    code_total = sum(lines[a] for a in CODE)
    return {"rev": rev, "commit": git("rev-parse", rev).decode().strip(),
            "areas": {a: lines[a] for a in AREAS}, "files": {a: files[a] for a in AREAS},
            "code": code_total, "all": sum(lines.values()), "largest": [{"file": p, "lines": n} for n, p in sizes[:12]]}


def diff(a: str, b: str = "HEAD") -> dict:
    x, y = at(a), at(b)
    return {"from": x["commit"][:9], "to": y["commit"][:9],
            "areas": {k: y["areas"][k] - x["areas"][k] for k in AREAS},
            "code": y["code"] - x["code"], "all": y["all"] - x["all"], "before": x, "after": y}


HUNK_FILE = re.compile(r"^\+\+\+ b/(.*)$")
GONE_FILE = re.compile(r"^--- a/(.*)$")


def commits(rng: str, paths: list[str] | None = None, since: str | None = None, until: str | None = None) -> list[dict]:
    """Per commit in a range (oldest first): code lines added and deleted per area."""
    dates = [*([f"--since={since}"] if since else []), *([f"--until={until}"] if until else [])]
    raw = git("log", "--reverse", "--no-merges", "-p", "-U0", "--no-color", "--no-renames", *dates,
              "--format=\x01%H\t%an\t%cI\t%s", rng, *(["--", *paths] if paths else [])).decode("utf-8", errors="replace")
    rows: list[dict] = []
    cur = None
    path, header = None, False
    for line in raw.splitlines():
        if line.startswith("\x01"):
            sha, author, when, subject = (line[1:].split("\t", 3) + ["", "", ""])[:4]
            cur = {"commit": sha, "author": author, "when": when, "subject": subject,
                   "added": defaultdict(int), "deleted": defaultdict(int)}
            rows.append(cur)
            path, header = None, False
            continue
        if cur is None:
            continue
        if line.startswith("diff --git "):
            path, header = None, True
            continue
        if header:
            m = GONE_FILE.match(line) or HUNK_FILE.match(line)
            if m:
                path = m.group(1)
            header = not line.startswith("@@")
            continue
        if not path or line.startswith(("@@", "\\")):
            continue
        a = area(path)
        if not a or line[:1] not in "+-":
            continue
        if comment_only(line[1:], kind(path)):
            continue
        (cur["added"] if line[0] == "+" else cur["deleted"])[a] += 1
    for r in rows:
        add, dele = dict(r["added"]), dict(r["deleted"])
        r["added"], r["deleted"] = add, dele
        r["net"] = {a: add.get(a, 0) - dele.get(a, 0) for a in AREAS if add.get(a) or dele.get(a)}
        r["code_added"] = sum(add.get(a, 0) for a in CODE)
        r["code_deleted"] = sum(dele.get(a, 0) for a in CODE)
        r["code_net"] = r["code_added"] - r["code_deleted"]
        r["changed"] = r["code_added"] + r["code_deleted"]
    return rows


def totals(rows: list[dict]) -> dict:
    t = {"commits": len(rows), "code_added": 0, "code_deleted": 0, "code_net": 0, "changed": 0,
         "net": defaultdict(int)}
    for r in rows:
        for k in ("code_added", "code_deleted", "code_net", "changed"):
            t[k] += r[k]
        for a, n in r["net"].items():
            t["net"][a] += n
    t["net"] = dict(t["net"])
    return t


def signed(n: int) -> str:
    return f"{n:+d}" if n else "0"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fe_loc.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("at", help="code lines per area at a revision")
    p.add_argument("rev", nargs="?", default="HEAD")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("diff", help="each area's change between two revisions")
    p.add_argument("a")
    p.add_argument("b", nargs="?", default="HEAD")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("commits", help="per commit: code lines added, deleted, net")
    p.add_argument("range")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "at":
        r = at(args.rev)
        if args.json:
            print(json.dumps(r, indent=1))
            return 0
        print(f"{TAG} {r['commit'][:9]}: {r['code']} code lines ({r['all']} with skills and docs)")
        for a in AREAS:
            print(f"  {a:<11} {r['areas'][a]:7}  in {r['files'][a]} files")
        print("  largest: " + ", ".join(f"{Path(f['file']).name} {f['lines']}" for f in r["largest"][:6]))
    elif args.cmd == "diff":
        r = diff(args.a, args.b)
        if args.json:
            print(json.dumps({k: v for k, v in r.items() if k not in ("before", "after")}, indent=1))
            return 0
        print(f"{TAG} {r['from']}..{r['to']}: code {signed(r['code'])} lines (all {signed(r['all'])})")
        print("  " + "  ".join(f"{a} {signed(r['areas'][a])}" for a in AREAS))
    else:
        rows = commits(args.range)
        if args.json:
            print(json.dumps({"commits": rows, "total": totals(rows)}, indent=1))
            return 0
        for r in rows:
            parts = " ".join(f"{a} {signed(n)}" for a, n in r["net"].items())
            print(f"  {r['commit'][:9]} +{r['code_added']:<5} -{r['code_deleted']:<5} net {signed(r['code_net']):>6}  "
                  f"{r['subject'][:60]}  [{parts}]")
        t = totals(rows)
        print(f"{TAG} {t['commits']} commits: code +{t['code_added']} -{t['code_deleted']} net {signed(t['code_net'])} | "
              f"changed {t['changed']} | " + " ".join(f"{a} {signed(n)}" for a, n in t["net"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
