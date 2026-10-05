#!/usr/bin/env python3
"""The code's health, weighted by where it changes: the code crew's measure (D265).

    fe_health.py snapshot [REV] [--clippy]   the analysers' view of a commit (cached by SHA)
    fe_health.py baseline                    before a refactor: HEAD's snapshot, with clippy
    fe_health.py check                       after: the gate, the ratchet, the score; exit 1 on a failure
    fe_health.py delta A [B]                 the same between two commits (no clippy unless cached)
    fe_health.py register [REV] [--top N]    where the debt is: functions, files, clones, coupled pairs
    fe_health.py coupling [--days 90]        files that change together, and the hotspots

Why not lines of code: size is a symptom, and an agent paid for fewer lines
deletes tests, inlines helpers and squeezes code (see D265 and the research
behind it). What predicts defects is complexity and duplication where the
code changes (Nagappan & Ball; Tornhill & Borg), so:

- **penalty** of a function: max(0, cognitive - 15) + max(0, statements - 40) / 6
  + max(0, args - 5); a file adds its duplicated tokens / 50. Cognitive
  complexity (SonarSource) already charges each level of nesting; statements
  are rust-code-analysis's logical lines, which joining lines does not change.
- **weight** of a file: its commits in the 90 days before the commit, plus 1.
- **score** = sum of weight x (penalty before - penalty after). A function
  that moved keeps the weight of the file it came from, and a new one takes
  the heaviest weight of the change, so moving debt to a quiet file scores 0.
- **gate** (any failure scores 0, D280): no fewer tests (`#[test]`,
  `def test_`), no new clippy warnings when both sides have them, no new
  dependency cycle between modules that existed before (cargo-modules' `use`
  edges in the app crate, less a module's uses of its own parent or children:
  `use super::*` is how a module's files share it, not a dependency).
  `fe_regress.py check` (bit-identical, D150) is the other half, run by the
  crew and fe_land.
- **ratchet**, advisory (D280): a changed function that crosses a threshold
  or gets worse past one, a new function that starts past one, duplicated
  tokens that rise. Each must be justified in the report or undone, never
  failed blind: a hard length cap is met by splitting functions arbitrarily,
  which raises coupling.
- **guards**, reported: net code lines (fe_loc.py) do not rise, test lines do
  not fall, vulture's dead Python and cargo-machete's unused dependencies do
  not rise.

The analysers (installed by hand, see docs/engine-reference.md#code health):
rust-code-analysis-cli (Rust and Python functions), jscpd (clones in Rust,
Python and Slang), vulture (dead Python), cargo-machete, cargo-modules, clippy. A missing
one is reported and its part left out, never guessed. A snapshot reads the
commit from git into a scratch folder, so any commit can be measured
without a checkout; clippy needs a build, so only HEAD of a clean tree has it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fe_loc  # noqa: E402
import studio_config  # noqa: E402  (the checkout, and what the game's code is: [health] in studio.toml)

ROOT = studio_config.repo_root()  # fe_crew points it at the checkout it measures
_cfg = lambda key, default: studio_config.get(f"health.{key}", default)  # noqa: E731

TAG = "[fe-health]"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VERSION = 4                 # a cached snapshot from another version is measured again
# Thresholds: a starting point, tuned in the shadow week (D265). LINES is logical lines
# (statements): 40 of them pick out about the functions 60 physical lines do here.
COGNITIVE, LINES, ARGS = 15, 40, 5
LINES_PER_POINT = 6
DUP_TOKENS = 50             # jscpd's smallest clone, and the tokens one penalty point is worth
HOT_DAYS = 90
# What is measured ([health]): the code trees, the files the analysers need beside them (a Cargo
# workspace), the code's extensions; by default the studio's own Python.
TREES = tuple(_cfg("trees", [studio_config.studio_rel()]))
WORKSPACE = tuple(_cfg("workspace", []))
CODE_EXT = tuple(_cfg("code_ext", [".py"]))
ANALYSE = _cfg("analyse", list(TREES))               # rust-code-analysis's paths and file globs
ANALYSE_INCLUDE = _cfg("analyse_include", ["*.py"])
JSCPD = _cfg("jscpd", {"format": "python"})          # jscpd's --format, --formats-exts and --ignore
VULTURE = _cfg("vulture", [studio_config.studio_rel()])  # where dead Python is looked for
MACHETE = _cfg("machete", None)                      # cargo-machete's path; None: not run
CLIPPY = _cfg("clippy", None)                        # the Cargo workspace clippy runs in; None: not run
MODULES = _cfg("modules", None)                      # cargo-modules: {dir, package, bin, src}; None: not run


def artifacts() -> Path:
    """The scratch folder of the checkout measured (ROOT, which fe_crew may point elsewhere)."""
    return ROOT / studio_config.rel(studio_config.artifacts_dir())


def git(*args: str, cwd: Path | None = None, data: bytes | None = None) -> bytes:
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, input=data, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise SystemExit(f"{TAG} git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def sha(rev: str) -> str:
    return git("rev-parse", rev).decode().strip()


def store() -> Path:
    try:
        import fe_board
        p = fe_board.board_dir() / "manager" / "health"
    except Exception:  # noqa: BLE001 - a checkout without the board still measures
        p = artifacts() / "health"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if name == "vulture":
        try:
            import fe_land
            py = fe_land.manager_python()
            if py and Path(py).exists():
                return py  # vulture lives in the manager's venv: run it as `python -m vulture`
        except Exception:  # noqa: BLE001
            return None
    return None


def run(cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    exe = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)


# ---- one commit's files -------------------------------------------------------------------

def extract(rev: str, dest: Path) -> list[str]:
    """The commit's code trees under `dest`; the counted code files' repository paths."""
    parents = sorted({Path(t).parent.as_posix() for t in TREES + WORKSPACE})
    top = {f for d in parents for f in git("ls-tree", "--name-only", rev, *([f"{d}/"] if d != "." else [])).decode().split()}
    raw = git("archive", "--format=tar", rev, *(t for t in TREES + WORKSPACE if t in top))
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        tar.extractall(dest, members=members, filter="data") if sys.version_info >= (3, 12) else tar.extractall(dest, members=members)
    return sorted(m.name for m in members if m.name.endswith(CODE_EXT) and fe_loc.area(m.name))


# ---- the analysers ------------------------------------------------------------------------

def functions_of(space: dict, path: str, scope: str = "") -> list[dict]:
    """rust-code-analysis's spaces as functions, each with only its own complexity and size."""
    out = []
    for child in space.get("spaces") or []:
        name = child.get("name") or ""
        kind = child.get("kind")
        qual = f"{scope}::{name}" if scope and name else name
        if kind == "function" and name and name != "<anonymous>":
            m = child["metrics"]
            inner = [s for s in child.get("spaces") or [] if s.get("kind") == "function" and s.get("name") not in ("", "<anonymous>")]
            own = lambda key, sub: m[key][sub] - sum(s["metrics"][key][sub] for s in inner)  # noqa: E731
            out.append({"file": path, "name": qual, "start": child["start_line"], "end": child["end_line"],
                        "cognitive": int(own("cognitive", "sum")), "cyclomatic": int(own("cyclomatic", "sum")),
                        # logical lines (statements): joining lines does not change them
                        "lines": int(own("loc", "lloc")), "ploc": int(own("loc", "ploc")),
                        "args": int(m["nargs"]["total_functions"]
                                                                      - sum(s["metrics"]["nargs"]["total_functions"] for s in inner))})
            out += functions_of(child, path, qual)
        else:
            out += functions_of(child, path, qual if kind in ("impl", "trait", "class", "namespace") else scope)
    return out


def rca(root: Path) -> list[dict] | None:
    exe = tool("rust-code-analysis-cli")
    if not exe:
        return None
    out = Path(tempfile.mkdtemp(prefix="fe-rca-"))
    try:
        r = run([exe, "-m", "-O", "json", "-o", str(out), *sum((["-p", t] for t in ANALYSE), []),
                 *sum((["-I", g] for g in ANALYSE_INCLUDE), [])], cwd=root)
        if r.returncode:
            print(f"{TAG} rust-code-analysis: {r.stderr.strip()[:300]}", file=sys.stderr)
        fns = []
        for f in out.rglob("*.json"):
            try:
                unit = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            path = str(f.relative_to(out))[:-5].replace("\\", "/")
            if fe_loc.area(path):
                fns += functions_of(unit, path)
        return fns
    finally:
        shutil.rmtree(out, ignore_errors=True)


def jscpd(root: Path) -> dict | None:
    exe = tool("jscpd")
    if not exe:
        return None
    out = Path(tempfile.mkdtemp(prefix="fe-jscpd-"))
    try:
        run([exe, *(t for t in TREES if (root / t).is_dir()), "--silent", "--absolute", "--reporters", "json", "--output", str(out),
             "--min-tokens", str(DUP_TOKENS), "--format", JSCPD["format"],
             *(["--formats-exts", JSCPD["formats_exts"]] if JSCPD.get("formats_exts") else []),
             *(["--ignore", JSCPD["ignore"]] if JSCPD.get("ignore") else [])], cwd=root)
        try:
            rep = json.loads((out / "jscpd-report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    finally:
        shutil.rmtree(out, ignore_errors=True)
    base = str(root.resolve()).replace("\\", "/").rstrip("/") + "/"

    def rel(name: str) -> str:
        n = name[4:] if name.startswith("\\\\?\\") else name  # jscpd's --absolute is a Windows long path
        n = n.replace("\\", "/")
        return n[len(base):] if n.lower().startswith(base.lower()) else n
    per_file: dict = defaultdict(int)
    clones = []
    for d in rep.get("duplicates") or []:
        a, b = d["firstFile"], d["secondFile"]
        fa, fb = rel(a["name"]), rel(b["name"])
        if not (fe_loc.area(fa) and fe_loc.area(fb)):
            continue
        per_file[fa] += d["tokens"]; per_file[fb] += d["tokens"]
        clones.append({"a": f"{fa}:{a['start']}-{a['end']}", "b": f"{fb}:{b['start']}-{b['end']}",
                       "lines": d["lines"], "tokens": d["tokens"], "files": [fa, fb]})
    total = rep.get("statistics", {}).get("total", {})
    return {"tokens": sum(c["tokens"] for c in clones), "percent": round(total.get("percentageTokens", 0.0), 3),
            "clones": clones, "per_file": dict(per_file)}


def vulture(root: Path) -> list[str] | None:
    exe = tool("vulture")
    if not exe:
        return None
    cmd = [exe, "-m", "vulture"] if exe.lower().endswith("python.exe") else [exe]
    r = run([*cmd, *VULTURE, "--min-confidence", "80", "--exclude", "test_*"], cwd=root)
    if r.returncode not in (0, 1, 3):  # 3: dead code found
        return None
    return [ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if ln.strip()]


def modules(root: Path) -> list[list[str]] | None:
    """The app crate's module `use` edges, less those between a module and its own parent or
    children (`use super::*` shares a parent's items with its files; it is not a dependency)."""
    exe = tool("cargo-modules")
    if not MODULES or not exe or not (root / MODULES["dir"] / "Cargo.toml").exists():
        return None
    crate = MODULES["bin"]
    r = run([exe, "modules", "dependencies", "-p", MODULES["package"], "--bin", crate, "--no-externs", "--no-sysroot",
             "--no-fns", "--no-traits", "--no-types", "--no-owns"], cwd=root / MODULES["dir"])
    if r.returncode:
        return None
    kin = lambda a, b: a.startswith(b + "::") or b.startswith(a + "::")  # noqa: E731
    parent = lambda m: m.rpartition("::")[0]  # noqa: E731
    src = root / MODULES["src"]

    def globs_parent(m: str) -> bool:
        # `use super::*` names every sibling module, so cargo-modules draws an edge to each:
        # the siblings of such a file are reached through the glob, not used.
        rel = Path(*m.split("::")[1:]) if "::" in m else None
        for f in ((src / rel).with_suffix(".rs"), src / rel / "mod.rs") if rel else ():
            if f.exists():
                # at the top level only: a `mod tests` globbing the file it tests is not the file's own
                return bool(re.search(r"^use super::\*;", f.read_text(encoding="utf-8", errors="replace"), re.M))
        return False
    globbed = {m for m in set(re.findall(rf'"({re.escape(crate)}(?:::\w+)+)"', r.stdout)) if globs_parent(m)}
    edges = {(a, b) for a, b in re.findall(r'"([^"]+)" -> "([^"]+)" \[label="uses"', r.stdout)
             if a != b and not kin(a, b) and not (a in globbed and parent(a) == parent(b))}
    return sorted([a, b] for a, b in edges)


def cyclic(edges: list[list[str]]) -> set[tuple[str, str]]:
    """The edges that lie on a cycle: both ends in one strongly connected component (Tarjan)."""
    out: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        out[a].add(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    comp: dict[str, int] = {}
    stack: list[str] = []
    for root in sorted(set(out) | {b for _, b in edges}):
        if root in index:
            continue
        work = [(root, iter(sorted(out[root])))]
        index[root] = low[root] = len(index)
        stack.append(root)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    work.append((w, iter(sorted(out[w]))))
                elif w not in comp:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    comp[w] = index[v]
                    if w == v:
                        break
    return {(a, b) for a, b in edges if comp[a] == comp[b]}


def short_module(m: str) -> str:
    """A module path without its crate (`app::body::cut` -> `body::cut`)."""
    return m.removeprefix(f"{MODULES['bin']}::") if MODULES else m


def tangles(snap: dict) -> list[dict]:
    """The module loops, biggest first: each one's modules and the edges that hold it shut."""
    edges = sorted(cyclic(snap.get("modules") or []))
    groups: list[set[str]] = []
    for a, b in edges:
        hit = [g for g in groups if a in g or b in g]
        merged = {a, b}.union(*hit)
        groups = [g for g in groups if g not in hit] + [merged]
    short = short_module
    return sorted(({"modules": sorted(map(short, g)), "edges": [f"{short(a)} -> {short(b)}" for a, b in edges if a in g]}
                   for g in groups), key=lambda t: -len(t["modules"]))


def machete(root: Path) -> list[str] | None:
    exe = tool("cargo-machete")
    if not MACHETE or not exe:
        return None
    r = run([exe, MACHETE], cwd=root)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.startswith(("\t", "  ")) and ln.strip()]


def clippy() -> dict | None:
    """Clippy's warnings per lint for the checkout as it is (needs a build: HEAD only)."""
    if not CLIPPY:
        return None
    target = Path(os.environ.get("TEMP", tempfile.gettempdir())) / f"fe-clippy-{os.environ.get('FE_AGENT', 'x')}"
    try:
        r = run(["cargo", "clippy", "--release", "--message-format=json", "--target-dir", str(target)],
                cwd=ROOT / CLIPPY, timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lints: dict = defaultdict(int)
    seen = set()
    for line in r.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        msg = m.get("message") or {}
        if m.get("reason") != "compiler-message" or msg.get("level") != "warning":
            continue
        code = (msg.get("code") or {}).get("code") or "warning"
        span = next((s for s in msg.get("spans") or [] if s.get("is_primary")), {})
        key = (code, span.get("file_name"), span.get("line_start"), msg.get("message"))
        if key not in seen:
            seen.add(key)
            lints[code] += 1
    if r.returncode and not lints:
        return None
    return {"total": sum(lints.values()), "lints": dict(sorted(lints.items()))}


def tests_of(root: Path, files: list[str]) -> dict:
    rust = py = 0
    for f in files:
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f.endswith(".rs"):
            rust += len(re.findall(r"#\[test\]", text))
        elif Path(f).name.startswith("test_"):
            py += len(re.findall(r"^\s*def test_\w+", text, re.M))
    return {"rust": rust, "python": py, "total": rust + py}


# ---- hotspots and coupling ----------------------------------------------------------------

def history(rev: str = "HEAD", days: int = HOT_DAYS) -> list[tuple[str, list[str]]]:
    """(commit, code files) for each commit in the days before `rev`, newest first."""
    when = int(git("log", "-1", "--format=%ct", rev).decode().strip())
    raw = git("log", rev, f"--since={when - days * 86400}", "--no-merges", "--name-only", "--format=\x01%H").decode(
        "utf-8", errors="replace")
    out = []
    for chunk in raw.split("\x01")[1:]:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        out.append((lines[0], [f for f in lines[1:] if f.endswith(CODE_EXT) and fe_loc.area(f)]))
    return out


def hotspots(rev: str = "HEAD", days: int = HOT_DAYS) -> dict:
    n: dict = defaultdict(int)
    for _, files in history(rev, days):
        for f in files:
            n[f] += 1
    return dict(n)


def coupling(rev: str = "HEAD", days: int = HOT_DAYS, min_shared: int = 5, max_files: int = 25) -> list[dict]:
    """File pairs that change together (code-maat's degree: shared / mean revisions); a big commit is skipped."""
    revs: dict = defaultdict(int)
    pairs: dict = defaultdict(int)
    for _, files in history(rev, days):
        fs = sorted(set(files))
        for f in fs:
            revs[f] += 1
        if 2 <= len(fs) <= max_files:
            for i, a in enumerate(fs):
                for b in fs[i + 1:]:
                    pairs[(a, b)] += 1
    out = []
    for (a, b), shared in pairs.items():
        if shared >= min_shared:
            degree = shared / ((revs[a] + revs[b]) / 2)
            out.append({"a": a, "b": b, "shared": shared, "degree": round(degree, 2), "revs": [revs[a], revs[b]]})
    return sorted(out, key=lambda p: (-p["degree"], -p["shared"]))


# ---- snapshot -----------------------------------------------------------------------------

def penalty(fn: dict) -> float:
    return (max(0, fn["cognitive"] - COGNITIVE) + max(0, fn["lines"] - LINES) / LINES_PER_POINT + max(0, fn["args"] - ARGS))


def over(fn: dict) -> dict:
    return {"cognitive": fn["cognitive"] > COGNITIVE, "lines": fn["lines"] > LINES, "args": fn["args"] > ARGS}


def snapshot(rev: str = "HEAD", with_clippy: bool = False, fresh: bool = False) -> dict:
    full = sha(rev)
    cache = store() / f"{full}.json"
    snap = None
    if cache.exists() and not fresh:
        try:
            snap = json.loads(cache.read_text(encoding="utf-8"))
            if snap.get("version") != VERSION:
                snap = None
        except ValueError:
            snap = None
    if snap is None:
        started = time.time()
        # On the checkout's drive, not %TEMP%: C: fills up (the night of 2026-10-03 it was at 0 bytes).
        scratch = artifacts()
        tmp = Path(tempfile.mkdtemp(prefix="fe-health-", dir=scratch if scratch.is_dir() else None))
        try:
            files = extract(full, tmp)
            fns = rca(tmp)
            dup = jscpd(tmp)
            snap = {"version": VERSION, "commit": full, "when": int(git("log", "-1", "--format=%ct", full).decode()),
                    "functions": fns, "duplication": dup, "vulture": vulture(tmp), "machete": machete(tmp),
                    "modules": (mods := modules(tmp)),
                    "tests": tests_of(tmp, files), "loc": fe_loc.at(full)["areas"], "clippy": None,
                    "missing": [n for n, v in (("rust-code-analysis-cli", fns), ("jscpd", dup),
                                               *((("cargo-modules", mods),) if MODULES else ())) if v is None]}
            snap["seconds"] = round(time.time() - started, 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if with_clippy and snap.get("clippy") is None:
        if full != sha("HEAD") or git("status", "--porcelain", "--untracked-files=no").strip():
            print(f"{TAG} clippy needs {full[:9]} checked out and a clean tree; left out", file=sys.stderr)
        else:
            snap["clippy"] = clippy()
    cache.write_text(json.dumps(snap), encoding="utf-8")
    return snap


def file_penalties(snap: dict) -> dict:
    out: dict = defaultdict(float)
    for fn in snap.get("functions") or []:
        out[fn["file"]] += penalty(fn)
    for f, tokens in ((snap.get("duplication") or {}).get("per_file") or {}).items():
        out[f] += tokens / DUP_TOKENS
    return dict(out)


def summary(snap: dict) -> dict:
    fns = snap.get("functions") or []
    pens = file_penalties(snap)
    dup = snap.get("duplication") or {}
    return {"commit": snap["commit"][:9], "functions": len(fns),
            "over": {k: sum(1 for f in fns if over(f)[k]) for k in ("cognitive", "lines", "args")},
            "penalty": round(sum(pens.values()), 1), "dup_tokens": dup.get("tokens"), "dup_percent": dup.get("percent"),
            "clones": len(dup.get("clones") or []), "tests": snap["tests"]["total"],
            "clippy": (snap.get("clippy") or {}).get("total"), "vulture": None if snap.get("vulture") is None else len(snap["vulture"]),
            "machete": None if snap.get("machete") is None else len(snap["machete"]),
            "cycle_edges": None if snap.get("modules") is None else len(cyclic(snap["modules"])),
            "code_lines": sum(snap["loc"].get(a, 0) for a in fe_loc.CODE), "missing": snap.get("missing") or []}


# ---- delta: the gate, the ratchet, the score ----------------------------------------------

def match(before: list[dict], after: list[dict]) -> tuple[dict, dict]:
    """after-index -> before function, by file and name, then by a name unique on both sides (a move)."""
    by_key = {(f["file"], f["name"]): f for f in before}
    names_b: dict = defaultdict(list)
    names_a: dict = defaultdict(list)
    for f in before:
        names_b[f["name"]].append(f)
    for f in after:
        names_a[f["name"]].append(f)
    pairs, used = {}, set()
    for i, f in enumerate(after):
        old = by_key.get((f["file"], f["name"]))
        if old is None and len(names_b[f["name"]]) == 1 and len(names_a[f["name"]]) == 1:
            old = names_b[f["name"]][0]
        if old is not None:
            pairs[i] = old
            used.add(id(old))
    gone = {id(f): f for f in before if id(f) not in used}
    return pairs, gone


def changed_files(a: str, b: str) -> list[str]:
    raw = git("diff", "--name-only", "--no-renames", a, b, "--", *TREES).decode()
    return [f for f in raw.splitlines() if f.endswith(CODE_EXT) and fe_loc.area(f)]


def delta(a: str, b: str = "HEAD", sa: dict | None = None, sb: dict | None = None) -> dict:
    sa = sa or snapshot(a)
    sb = sb or snapshot(b)
    changed = set(changed_files(sa["commit"], sb["commit"]))
    hot = hotspots(sa["commit"])
    weight = lambda f: hot.get(f, 0) + 1  # noqa: E731
    heaviest = max((weight(f) for f in changed), default=1)
    before = [f for f in sa.get("functions") or [] if f["file"] in changed]
    after = [f for f in sb.get("functions") or [] if f["file"] in changed]
    pairs, gone = match(before, after)

    score, moves, ratchet, worse = 0.0, [], [], []
    for i, f in enumerate(after):
        old = pairs.get(i)
        w = weight(old["file"]) if old else heaviest
        p_new, p_old = penalty(f), penalty(old) if old else 0.0
        score += w * (p_old - p_new)
        if p_old != p_new:
            moves.append({"fn": f"{f['file']}::{f['name']}", "before": round(p_old, 1), "after": round(p_new, 1),
                          "weight": w, "new": old is None})
        for k in ("cognitive", "lines", "args"):
            limit = {"cognitive": COGNITIVE, "lines": LINES, "args": ARGS}[k]
            if old is None and f[k] > limit:
                ratchet.append(f"new {f['file']}::{f['name']} starts past the {k} limit ({f[k]} > {limit})")
            elif old is None:
                continue
            elif old[k] <= limit < f[k]:
                ratchet.append(f"{f['file']}::{f['name']} crossed the {k} limit ({old[k]} -> {f[k]} > {limit})")
            elif old[k] > limit and f[k] > old[k]:
                ratchet.append(f"{f['file']}::{f['name']} got worse past the {k} limit ({old[k]} -> {f[k]})")
            else:
                continue
            # The same, as data: the scorecard names the commits that did it (D280).
            worse.append({"file": f["file"], "name": f["name"], "start": f.get("start"), "end": f.get("end"),
                          "measure": k, "before": old[k] if old else None, "after": f[k], "limit": limit,
                          "weight": w, "file_commits": weight(f["file"]) - 1})
    for old in gone.values():
        w = weight(old["file"])
        score += w * penalty(old)
        if penalty(old):
            moves.append({"fn": f"{old['file']}::{old['name']}", "before": round(penalty(old), 1), "after": 0.0,
                          "weight": w, "new": False, "removed": True})
    da, db = sa.get("duplication") or {}, sb.get("duplication") or {}
    for f in changed:
        ta = (da.get("per_file") or {}).get(f, 0)
        tb = (db.get("per_file") or {}).get(f, 0)
        score += (weight(f) if tb <= ta else heaviest) * (ta - tb) / DUP_TOKENS
    if da and db and db["tokens"] > da["tokens"]:
        ratchet.append(f"duplicated tokens rose {da['tokens']} -> {db['tokens']}")

    gate = []
    if sb["tests"]["total"] < sa["tests"]["total"]:
        gate.append(f"fewer tests: {sa['tests']} -> {sb['tests']}")
    ca, cb = sa.get("clippy"), sb.get("clippy")
    if ca and cb:
        new = {k: v - ca["lints"].get(k, 0) for k, v in cb["lints"].items() if v > ca["lints"].get(k, 0)}
        if new:
            gate.append("new clippy warnings: " + ", ".join(f"{k} +{v}" for k, v in new.items()))
    clippy_checked = bool(ca and cb)
    # Structure is gated, not advised (D280): a new edge on a cycle between modules that both
    # existed binds them so neither can change alone. A new module's own cycles (its siblings'
    # `use super::*` reaching it) are reported, not failed.
    ma, mb = sa.get("modules"), sb.get("modules")
    new_cycles = []
    if ma is not None and mb is not None:
        known = {m for e in ma for m in e}
        for x, y in sorted(cyclic(mb) - cyclic(ma)):
            new_cycles.append({"from": x, "to": y, "existing": x in known and y in known})
            if x in known and y in known:
                gate.append(f"new dependency cycle: {x} -> {y} now lies on a loop back to {x}")

    la, lb = sa["loc"], sb["loc"]
    code_net = sum(lb.get(x, 0) - la.get(x, 0) for x in fe_loc.CODE)
    test_net = sum(lb.get(x, 0) - la.get(x, 0) for x in fe_loc.TESTS)
    guards = {"code_lines": code_net, "test_lines": test_net,
              "vulture": None if sa.get("vulture") is None or sb.get("vulture") is None else len(sb["vulture"]) - len(sa["vulture"]),
              "machete": None if sa.get("machete") is None or sb.get("machete") is None else len(sb["machete"]) - len(sa["machete"])}
    warn = []
    if code_net > 0:
        warn.append(f"net code lines rose by {code_net}")
    if test_net < 0:
        warn.append(f"test lines fell by {-test_net}")
    for k in ("vulture", "machete"):
        if guards[k] and guards[k] > 0:
            warn.append(f"{k} found {guards[k]} more")
    for c in new_cycles:
        if not c["existing"]:
            warn.append(f"a new module sits on a cycle: {c['from']} -> {c['to']}")
    missing = sorted(set(sa.get("missing") or []) | set(sb.get("missing") or []))
    return {"from": sa["commit"][:9], "to": sb["commit"][:9], "changed": sorted(changed),
            "score": round(score if not gate else 0.0, 1), "raw_score": round(score, 1),
            "passed": not gate, "gate": gate, "ratchet": ratchet, "worse": worse, "cycles": new_cycles,
            "warnings": warn, "guards": guards, "clippy_checked": clippy_checked, "missing": missing,
            "moves": sorted(moves, key=lambda m: -abs(m["weight"] * (m["before"] - m["after"])))[:20],
            "before": summary(sa), "after": summary(sb)}


# ---- register: where the debt is ----------------------------------------------------------

def register(rev: str = "HEAD", top: int = 15) -> dict:
    snap = snapshot(rev)
    hot = hotspots(snap["commit"])
    w = lambda f: hot.get(f, 0) + 1  # noqa: E731
    fns = sorted(({**f, "penalty": round(penalty(f), 1), "weight": w(f["file"]),
                   "priority": round(w(f["file"]) * penalty(f), 1)} for f in snap.get("functions") or [] if penalty(f) > 0),
                 key=lambda f: -f["priority"])
    files = sorted(({"file": f, "penalty": round(p, 1), "weight": w(f), "priority": round(w(f) * p, 1)}
                    for f, p in file_penalties(snap).items() if p > 0), key=lambda f: -f["priority"])
    dup = snap.get("duplication") or {}
    clones = sorted(({**c, "priority": c["tokens"] * (w(c["files"][0]) + w(c["files"][1]))}
                     for c in dup.get("clones") or []), key=lambda c: -c["priority"])
    return {"commit": snap["commit"][:9], "summary": summary(snap), "functions": fns[:top], "files": files[:top],
            "clones": clones[:top], "coupled": coupling(snap["commit"])[:top], "tangles": tangles(snap),
            "dead_python": (snap.get("vulture") or [])[:top], "unused_deps": snap.get("machete") or []}


# ---- the commands -------------------------------------------------------------------------

def baseline_path() -> Path:
    return artifacts() / f"health-baseline-{os.environ.get('FE_AGENT', 'local')}.txt"


def print_delta(d: dict) -> None:
    a, b = d["before"], d["after"]
    verdict = "PASSED" if d["passed"] else "FAILED"
    print(f"{TAG} {d['from']}..{d['to']}: {verdict} | score {d['score']:+.1f} (hotspot-weighted health) | "
          f"{len(d['changed'])} code files changed")
    print(f"  penalty {a['penalty']} -> {b['penalty']} | past a limit: cognitive {a['over']['cognitive']} -> "
          f"{b['over']['cognitive']}, lines {a['over']['lines']} -> {b['over']['lines']}, args {a['over']['args']} -> "
          f"{b['over']['args']} | duplicated tokens {a['dup_tokens']} -> {b['dup_tokens']}")
    print(f"  tests {a['tests']} -> {b['tests']} | clippy {a['clippy']} -> {b['clippy']}"
          f"{'' if d['clippy_checked'] else ' (not compared: run baseline and check on a clean tree)'} | "
          f"code lines {d['guards']['code_lines']:+d} | test lines {d['guards']['test_lines']:+d} | "
          f"module edges on a cycle {a.get('cycle_edges')} -> {b.get('cycle_edges')}")
    for g in d["gate"]:
        print(f"  gate: {g}")
    for r in d["ratchet"][:15]:
        print(f"  ratchet (justify in the report, or undo): {r}")
    for w in d["warnings"]:
        print(f"  guard: {w}")
    for m in d["moves"][:10]:
        print(f"  {m['fn'][:80]:80} {m['before']:6.1f} -> {m['after']:6.1f}  x{m['weight']}")
    if d["missing"]:
        print(f"  not measured (analyser missing): {', '.join(d['missing'])}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fe_health.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("rev", nargs="?", default="HEAD")
    p.add_argument("--clippy", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--json", action="store_true")
    sub.add_parser("baseline")
    p = sub.add_parser("check")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("delta")
    p.add_argument("a")
    p.add_argument("b", nargs="?", default="HEAD")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("register")
    p.add_argument("rev", nargs="?", default="HEAD")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("coupling")
    p.add_argument("--days", type=int, default=HOT_DAYS)
    p.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)

    if args.cmd == "snapshot":
        s = snapshot(args.rev, args.clippy, args.fresh)
        print(json.dumps(summary(s), indent=1) if args.json else
              f"{TAG} {s['commit'][:9]}: " + " | ".join(f"{k} {v}" for k, v in summary(s).items() if k != "commit"))
    elif args.cmd == "baseline":
        s = snapshot("HEAD", with_clippy=True)
        baseline_path().write_text(s["commit"], encoding="utf-8")
        print(f"{TAG} baseline {s['commit'][:9]}: " + " | ".join(f"{k} {v}" for k, v in summary(s).items() if k != "commit"))
    elif args.cmd in ("check", "delta"):
        if args.cmd == "check":
            try:
                base = baseline_path().read_text(encoding="utf-8").strip()
            except OSError:
                print(f"{TAG} no baseline: run fe_health.py baseline before the change", file=sys.stderr)
                return 2
            d = delta(base, "HEAD", sb=snapshot("HEAD", with_clippy=True))
        else:
            d = delta(args.a, args.b)
        if args.json:
            print(json.dumps(d, indent=1))
        else:
            print_delta(d)
        return 0 if d["passed"] else 1
    elif args.cmd == "register":
        r = register(args.rev, args.top)
        if args.json:
            print(json.dumps(r, indent=1))
            return 0
        s = r["summary"]
        print(f"{TAG} {r['commit']}: {s['functions']} functions, past a limit: {s['over']} | penalty {s['penalty']} | "
              f"duplicated {s['dup_percent']}% ({s['clones']} clones) | dead python {s['vulture']} | unused deps {s['machete']}")
        print("  functions (weight x penalty)                                         cog  lines args  prio")
        for f in r["functions"]:
            print(f"  {(f['file'] + '::' + f['name'])[:70]:70} {f['cognitive']:4} {f['lines']:5} {f['args']:4} {f['priority']:6}")
        print("  clones")
        for c in r["clones"][:10]:
            print(f"  {c['a'][:50]:50} = {c['b'][:50]:50} {c['lines']:3} lines")
        print("  change-coupled files (shared commits, degree)")
        for p in r["coupled"][:10]:
            print(f"  {p['a'][:45]:45} + {p['b'][:45]:45} {p['shared']:3} {p['degree']}")
    else:
        for p in coupling(days=args.days)[:args.top]:
            print(f"  {p['a'][:50]:50} + {p['b'][:50]:50} {p['shared']:3} {p['degree']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
