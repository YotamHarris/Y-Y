#!/usr/bin/env python3
"""Land the commits: every closing gate in one blocking call, one line per gate (D253).

    fe_land.py                          the gates the change needs, then push
    fe_land.py --refactor               also fe_regress: baseline of the base, then check (a change that claims to change nothing)
    fe_land.py --bench                  also fe_bench compare HEAD, the standard suite (D262: by choice, not on every land)
    fe_land.py --scenario NAME ...      --bench with these feature cases beside the standard suite
    fe_land.py --task T12 --version V98 then fe_board task review T12 (not under the manager)
    fe_land.py --no-push                every gate but the push
    fe_land.py --plan                   say which gates would run, run none

Last night's workers ran these by hand in a different order each time (fe_build
build 65 times, test 41, fe_regress 36, fe_sync push 21, fe_bench 46) and waited
on them in sleep loops. This runs what the change needs and stops at the first
failure:

    version  a runtime change with BUILD_VERSION unchanged (a warning: say why in the report)
    build    fe_build.py build                       when engine runtime files changed
    test     fe_build.py test                        when engine runtime files changed
    undo     fe_manager.py gpu -- test-edit-undo.py --audit  when the panel, the verb table or what Ctrl+Z
                                                     captures changed (UNDO_AUDITED): `undo audit` in both scenes
                                                     and a real drag and Ctrl+Z (T80, D282)
    tools    the Python tests of the tools changed   (test_manager in the manager's venv)
    baseline fe_regress.py baseline BASE             with --refactor: BASE is where the commits leave origin/main,
                                                     built in a scratch worktree, skipped when already recorded
    regress  fe_regress.py check --base BASE         with --refactor
    bench    fe_bench.py compare HEAD [NAME...]      with --bench or --scenario (D262: main is benched nightly)
    docs     fe_docs.py check                        always
    index    fe_index.py check                       always
    push     fe_sync.py push                         unless --no-push
    review   fe_board.py task review                 with --task, never under FE_MANAGER_RUN

Every gate's whole output goes to engine/artifacts/land-<agent>.log, and the
result to land-<agent>.json. A bench takes longer than a foreground tool call
may: Claude Code runs this with run_in_background and is told when it ends.
Never poll it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import studio_config  # noqa: E402  (the checkout, and the game's gates: [land] in studio.toml)

ROOT = studio_config.repo_root()
ARTIFACTS = studio_config.artifacts_dir()
TAG = "[fe-land]"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # run from the manager, which has no console
# The game's runtime files ([land] runtime): a change there is checked for a version bump and runs the
# gates marked `runtime`. None when the game names none.
RUNTIME = re.compile(studio_config.get("land.runtime")) if studio_config.get("land.runtime") else None
RUNTIME_LABEL = studio_config.get("land.runtime_label", "runtime")
# The version a runtime change bumps ([land.version]): its file and the number in it.
VERSION = studio_config.get("land.version", {})
# The game's own gates ([[land.gate]]), in order; stage "build" runs before the tools' tests, "measure" after.
GATES = studio_config.get("land.gate", [])
_STUDIO, _GAME = studio_config.studio_rel(), studio_config.rel(studio_config.game_tools_dir())
_TOOL_DIRS = "|".join(dict.fromkeys(re.escape(d) for d in (_STUDIO, _GAME)))
# A change to the manager or what it runs on reruns its tests, in its venv; plus the game's ([land] manager_tested).
MANAGER_TESTED = re.compile(rf"^{re.escape(_STUDIO)}/(manager_\w+|fe_manager|fe_board|fe_sync|test_manager)\.py$"
                            + "".join("|" + x for x in studio_config.get("land.manager_tested", [])))
MANAGER_TEST = f"{_STUDIO}/test_manager.py"
TOOL_TEST = re.compile(rf"^(?:{_TOOL_DIRS})/test_\w+\.py$")


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW).stdout.strip()


def studio_is_submodule() -> bool:
    return (ROOT / ".gitmodules").is_file() and (ROOT / _STUDIO / ".git").exists() and _STUDIO != "."


def studio_committed() -> bool:
    """The studio submodule differs from the game's pin only by commits, not by edits."""
    return studio_is_submodule() and not git("status", "--porcelain", cwd=ROOT / _STUDIO)


def studio_changes() -> list[str]:
    """D329: the studio files changed since origin/main pinned the submodule, as game paths:
    committed in the studio, whether or not the game's gitlink points at them yet."""
    if not studio_is_submodule():
        return []
    old = (git("ls-tree", "origin/main", "--", _STUDIO).split() + ["", "", ""])[2]
    if not old:
        return []
    return [f"{_STUDIO}/{f}" for f in git("diff", "--name-only", old, "HEAD", cwd=ROOT / _STUDIO).splitlines() if f]


def agent() -> str:
    try:
        sys.path.insert(0, str(HERE))
        import fe_identity
        return fe_identity.agent_name() or os.environ.get("FE_AGENT", "agent")
    except Exception:  # noqa: BLE001
        return os.environ.get("FE_AGENT", "agent")


def manager_python() -> str:
    venv = studio_config.data_dir() / "board" / "manager" / "venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def version_changed() -> bool:
    pat, path = re.compile(VERSION["regex"]), VERSION["file"]
    old = pat.search(git("show", f"origin/main:{path}") or "")
    new = pat.search((ROOT / path).read_text(encoding="utf-8", errors="replace") if (ROOT / path).exists() else "")
    return bool(old and new and old.group(1) != new.group(1))


def runtime(changed: list[str]) -> bool:
    return bool(RUNTIME) and any(RUNTIME.search(f) for f in changed)


def game_gates(stage: str, changed: list[str], args) -> list[tuple[str, list[str], str]]:
    """The [[land.gate]] rows of `stage` this change needs: `runtime` ones when runtime files changed,
    `when` ones when a changed file matches, `flag` ones only with --refactor or --bench (or --scenario).
    In a command, {python} is this Python, {studio} and {game_tools} those folders, {base} where the
    commits leave origin/main, and an argument "{scenarios}" the --scenario names."""
    out, base = [], None
    for g in GATES:
        if g.get("stage", "build") != stage:
            continue
        if g.get("runtime") and not runtime(changed):
            continue
        if g.get("when") and not any(re.search(g["when"], f) for f in changed):
            continue
        if g.get("flag") == "refactor" and not args.refactor:
            continue
        if g.get("flag") == "bench" and not (args.bench or args.scenario):
            continue
        cmd = []
        for a in g["cmd"]:
            if a == "{scenarios}":
                cmd += args.scenario
                continue
            if "{base}" in a and base is None:
                # The commits' base, not HEAD: a baseline taken by hand after the edit was a stash, a build
                # and a pop (T58), and one taken after the commit compares the change with itself.
                base = git("merge-base", "HEAD", "origin/main")
            path = a.startswith(("{studio}", "{game_tools}"))
            a = (a.replace("{python}", sys.executable).replace("{studio}", str(HERE))
                 .replace("{game_tools}", str(studio_config.game_tools_dir())).replace("{base}", base or ""))
            cmd.append(str(Path(a)) if path else a)
        out.append((g["name"], cmd, g.get("cwd", "")))
    return out


def plan(changed: list[str], args) -> list[tuple[str, list[str] | None, str]]:
    """(gate, command or None for a check done here, cwd-relative note)."""
    py = sys.executable
    gates: list[tuple[str, list[str] | None, str]] = []
    if runtime(changed) and VERSION:
        gates.append(("version", None, ""))
    gates += game_gates("build", changed, args)
    # A test the change deletes (or moves elsewhere) is not run where it was.
    tests = sorted({f for f in changed if TOOL_TEST.match(f) and (ROOT / f).is_file()} |
                   ({MANAGER_TEST} if any(MANAGER_TESTED.match(f) for f in changed) else set()))
    for t in tests:
        exe = manager_python() if t.endswith("test_manager.py") else py
        gates.append((f"tools {Path(t).stem}", [exe, "-m", "unittest", Path(t).stem], Path(t).parent.as_posix()))
    gates += game_gates("measure", changed, args)
    gates.append(("docs", [py, str(HERE / "fe_docs.py"), "check"], ""))
    gates.append(("index", [py, str(HERE / "fe_index.py"), "check"], ""))
    if not args.no_push:
        gates.append(("push", [py, str(HERE / "fe_sync.py"), "push"], ""))
    if args.task:
        # "@HEAD" is filled in when the gate runs: the push rebases, so the hash is known only then.
        gates.append(("review", [py, str(HERE / "fe_board.py"), "task", "review", args.task, "--commit", "@HEAD",
                                 *(["--version", args.version] if args.version else [])], ""))
    return gates


def last_line(out: str) -> str:
    """The line a tool ends on that says how it went (its summary), not a stray brace."""
    for line in reversed(out.splitlines()):
        s = line.strip()
        if len(s) > 2 and not s.startswith(("}", "{", "warning: LF", "The file will have")):
            return s[:160]
    return ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fe_land.py", description=__doc__.split("\n")[0])
    ap.add_argument("--refactor", action="store_true", help="also fe_regress baseline of the base, then check")
    ap.add_argument("--bench", action="store_true", help="also fe_bench compare HEAD (D262: by choice)")
    ap.add_argument("--scenario", nargs="*", default=[], help="--bench with these feature cases")
    ap.add_argument("--task", help="T12: mark it review after the push (not under the manager)")
    ap.add_argument("--version", help="the version the task shipped, for --task")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--plan", action="store_true", help="list the gates, run none")
    args = ap.parse_args(argv)
    if args.task and os.environ.get("FE_MANAGER_RUN"):
        print(f"{TAG} --task refused: under the manager the supervisor marks the task (D181)")
        return 2

    dirty = [l for l in git("status", "--porcelain").splitlines() if not l.startswith("??")
             and not (l[3:].strip() == _STUDIO and studio_committed())]
    if dirty and not args.plan:
        print(f"{TAG} the tree has uncommitted changes ({len(dirty)} file(s)): commit them first\n  "
              + "\n  ".join(dirty[:8]))
        return 1
    subprocess.run(["git", "fetch", "-q"], cwd=ROOT, creationflags=NO_WINDOW)
    ahead = git("rev-list", "--count", "origin/main..HEAD")
    changed = [f for f in git("diff", "--name-only", "origin/main...HEAD").splitlines() if f and f != _STUDIO]
    studio = studio_changes()
    changed += studio
    if studio and ahead in ("", "0"):
        ahead = "the studio's"  # D329: the push commits the game's gitlink to them
    if ahead in ("", "0"):
        print(f"{TAG} nothing to land: HEAD is not ahead of origin/main")
        return 0
    gates = plan(changed, args)
    print(f"{TAG} {ahead} commit(s), {len(changed)} file(s) changed"
          f"{f' ({RUNTIME_LABEL})' if runtime(changed) else ''}: " + ", ".join(g for g, _, _ in gates))
    if args.plan:
        return 0

    name = agent()
    log_path, json_path = ARTIFACTS / f"land-{name}.log", ARTIFACTS / f"land-{name}.json"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results, rc = [], 0
    head = git("rev-parse", "HEAD")

    def progress(**running):
        """land-<agent>.json while it runs: the gate, since when, its last line (the board shows it)."""
        json_path.write_text(json.dumps({"head": head, "gates": results, "of": len(gates), "at": time.time(),
                                         **running}, indent=1), encoding="utf-8")
    # The tools' own progress lines reach the log as they print, not when the gate ends.
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"{TAG} {time.strftime('%Y-%m-%d %H:%M:%S')} HEAD {head}\n")
        for gate, cmd, cwd in gates:
            t0 = time.time()
            if cmd is None:  # version: a check made here
                ok = version_changed()
                name = VERSION.get("name", "the version")
                line = f"{name} bumped" if ok else \
                    f"{name} unchanged: a change the player can see or do bumps it (AGENTS.md, Versioning)"
                status = "ok" if ok else "warn"
            else:
                cmd = [git("rev-parse", "--short", "HEAD") if c == "@HEAD" else c for c in cmd]
                log.write(f"\n===== {gate}: {' '.join(cmd)}\n")
                log.flush()
                progress(running=gate, since=t0, last="")
                p = subprocess.Popen(cmd, cwd=ROOT / cwd if cwd else ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     env=env, creationflags=NO_WINDOW)
                lines = []
                for raw in p.stdout:
                    log.write(raw)
                    log.flush()
                    lines.append(raw)
                    if raw.strip():
                        progress(running=gate, since=t0, last=raw.strip()[:200])
                p.wait()
                out = "".join(lines)
                line = last_line(out)
                status = "ok" if p.returncode == 0 else "FAILED"
                summary = next((ARTIFACTS / g["summary"] for g in GATES if g["name"] == gate and g.get("summary")), None)
                if summary and summary.exists():  # a gate whose wrapper swallows stdout (the gpu lease) says it here
                    line = summary.read_text(encoding="utf-8").strip().replace("\n", " | ")[:200]
            secs = time.time() - t0
            results.append({"gate": gate, "status": status, "secs": round(secs, 1), "line": line})
            print(f"  {gate:22} {status:6} {secs:6.0f}s  {line}", flush=True)
            if status == "FAILED":
                rc = 1
                print(f"{TAG} stopped at {gate}: the whole output is {log_path.relative_to(ROOT).as_posix()}")
                break
    json_path.write_text(json.dumps({"head": head, "ok": rc == 0, "gates": results,
                                     "at": time.time()}, indent=1), encoding="utf-8")
    if rc == 0:
        print(f"{TAG} landed: " + git("log", "-1", "--format=%h %s")[:120])
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
