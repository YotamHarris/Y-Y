#!/usr/bin/env python3
"""Keep the agents' checkouts of BodySimulation in step on one trunk.

    python engine/tools/fe_sync.py status | pull | push [--no-gate]
    python engine/tools/fe_sync.py init --agent NAME [--port N] [--repo PATH]
    python engine/tools/fe_sync.py free     which checkout can take a new session

Each agent works in its own clone (own engine/target build, own control
port); everything lands on origin/main.  `pull` fast-forwards a clean tree or
rebases onto origin/main.  `push` fetches, rebases, runs the compile gate
(`cargo check --release` when engine/crates or Cargo files changed) and
pushes; a rejected push is retried after another rebase.

Identity is the checkout path matched against fe_agents.json, else FE_AGENT.
`init` writes .claude/settings.local.json (FE_AGENT, FE_CONTROL_PORT, never
committed) and sets pull.rebase for the clone.

session-start, prompt, pre-bash and stop are Claude Code hook entry points
(.claude/settings.json).  They read the hook JSON on stdin and report sync
state, fast-forward a clean tree, refuse commands that break the two-agent
rules (raw `git push`, amending a pushed commit, a bench while another
app.exe runs, launching while a bench runs, shortcuts from a non-primary
checkout) and push unpushed commits when a turn ends.  Codex has no hooks
and runs pull/push by hand (AGENTS.md).

Routing (D165): a new session that starts in a busy checkout (another
session's turn running there, uncommitted or unpushed work, a rebase, a
branch, an app.exe from it) is told at session start which checkout is free
and moves itself there before it does anything.  The board store's
`sessions` table says which session is in which checkout.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import studio_config

HERE = Path(__file__).resolve().parent
REGISTRY = studio_config.agents_path()
TAG = "[fe-sync]"
FETCH_INTERVAL = 60      # seconds between automatic fetches
FETCH_TIMEOUT = 45
GATE_TIMEOUT = 900       # a push gate's default time limit
DEFAULT_PORT = studio_config.get("game.control_port", 0)


# ----------------------------------------------------------------- helpers

# A console child of a windowless parent (the board server runs under
# pythonw) gets a console window of its own, which flashes up and takes the
# focus; every git and PowerShell call here stays hidden.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args, cwd=None, timeout=120):
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def git(repo, *args, timeout=120):
    return run(["git", *args], repo, timeout)


def git_out(repo, *args, timeout=120):
    return git(repo, *args, timeout=timeout).stdout.strip()


def norm(path):
    return os.path.normcase(os.path.normpath(os.path.realpath(str(path))))


def load_registry():
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    reg.setdefault("remote", "origin")
    reg.setdefault("branch", "main")
    reg.setdefault("agents", {})
    return reg


def repo_root(start):
    p = run(["git", "rev-parse", "--show-toplevel"], start)
    if p.returncode != 0:
        raise SystemExit(f"{TAG} not a git checkout: {start}")
    return Path(p.stdout.strip())


def git_dir(repo):
    return Path(git_out(repo, "rev-parse", "--absolute-git-dir"))


def identify(repo, reg):
    """Return (name, source, name_by_path); source is 'path', 'env' or None.
    A registered checkout is who you are; FE_AGENT names a checkout outside
    the registry (a worktree).  D165: a session routed to another checkout
    keeps the FE_AGENT it started with until it restarts."""
    env_name = os.environ.get("FE_AGENT")
    by_path = None
    for name, agent in reg["agents"].items():
        if norm(agent["path"]) == norm(repo):
            by_path = name
            break
    if by_path:
        return by_path, "path", by_path
    if env_name and env_name in reg["agents"]:
        return env_name, "env", by_path
    return None, None, None


def tail(text, n=12):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def subjects(repo, rev_range, limit=6):
    out = git_out(repo, "log", "--format=%h %s", rev_range)
    lines = out.splitlines()
    shown = [f"  {l}" for l in lines[:limit]]
    if len(lines) > limit:
        shown.append(f"  ... {len(lines) - limit} more")
    return "\n".join(shown)


# ------------------------------------------------------------------- state

def maybe_fetch(repo, remote, force=False):
    """Fetch at most once per FETCH_INTERVAL unless forced. Returns a note."""
    stamp = git_dir(repo) / "fe-sync.fetch"
    now = time.time()
    if not force and stamp.exists() and now - stamp.stat().st_mtime < FETCH_INTERVAL:
        return f"fetched {int(now - stamp.stat().st_mtime)}s ago"
    try:
        p = git(repo, "fetch", "--quiet", remote, timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "fetch timed out; state may be stale"
    if p.returncode != 0:
        return "fetch failed (" + tail(p.stderr, 1) + "); state may be stale"
    stamp.touch()
    return "fetched now"


def in_progress(repo):
    gd = git_dir(repo)
    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        return "rebase"
    if (gd / "MERGE_HEAD").exists():
        return "merge"
    return None


def tree_state(repo):
    porcelain = git(repo, "status", "--porcelain").stdout  # not stripped: "XY path"
    modified, untracked = [], []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        (untracked if line.startswith("??") else modified).append(line[3:].strip())
    return modified, untracked


def counts(repo, upstream):
    def count(rng):
        out = git_out(repo, "rev-list", "--count", rng)
        return int(out) if out.isdigit() else 0
    return count(f"{upstream}..HEAD"), count(f"HEAD..{upstream}")


def branch_of(repo):
    return git_out(repo, "rev-parse", "--abbrev-ref", "HEAD")


def trunk_checkout(repo, reg, branch=None):
    """Allow detached HEAD only in the project's explicitly configured worker paths."""
    branch = branch if branch is not None else branch_of(repo)
    if branch == reg["branch"]:
        return True
    name = identify(repo, reg)[2]  # Never authorize a path from FE_AGENT alone.
    return (branch == "HEAD" and studio_config.get("routing.detached_workers", False)
            and name in studio_config.get("routing.checkouts", [])
            and not reg["agents"][name].get("primary", False))


def app_processes():
    """[(pid, command line)] of the game's running processes, any checkout
    (studio.toml `[game] processes`, image names; none configured, none)."""
    names = studio_config.get("game.processes", [])
    if not names:
        return []
    where = " OR ".join(f"Name='{n}'" for n in names)
    ps = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
          f"Get-CimInstance Win32_Process -Filter \"{where}\" | "
          "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"]
    try:
        out = run(ps, timeout=25).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return []
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [(d.get("ProcessId"), (d.get("CommandLine") or "").strip()) for d in data]


def other_agents(repo, reg):
    """Working-tree summary of the other registered checkouts."""
    lines = []
    for name, agent in reg["agents"].items():
        path = Path(agent["path"])
        if norm(path) == norm(repo) or not (path / ".git").exists():
            continue
        modified, untracked = tree_state(path)
        upstream = f'{reg["remote"]}/{reg["branch"]}'
        ahead, _ = counts(path, upstream)
        branch = branch_of(path)
        files = modified + untracked
        shown = ", ".join(files[:8]) + (f", ... {len(files) - 8} more" if len(files) > 8 else "")
        state = f"{len(modified)} modified, {len(untracked)} untracked" if files else "clean"
        note = f"{TAG} {name} tree ({branch}): {state}"
        if ahead:
            note += f"; {ahead} commit(s) not pushed"
        if files:
            note += f" -- {shown}. Expect conflicts if you edit these."
        lines.append(note)
    return lines


# ----------------------------------------------------------------- reports

def identity_lines(repo, reg, full):
    me, source, by_path = identify(repo, reg)
    lines, warnings = [], []
    if me is None:
        lines.append(f"{TAG} checkout {repo} is not a registered agent ({studio_config.rel(REGISTRY)}). "
                     f"Run: {studio_config.tool_cmd('fe_sync.py')} init --agent NAME")
        return lines, warnings
    agent = reg["agents"][me]
    port = agent.get("port", DEFAULT_PORT)
    others = ", ".join(
        f'{n} at {a["path"]}, port {a.get("port", DEFAULT_PORT)}' + (" (primary)" if a.get("primary") else "")
        for n, a in reg["agents"].items() if n != me) or "none"
    role = "primary: owns the desktop shortcut and the player's instance" if agent.get("primary") else "second agent"
    lines.append(f"{TAG} you are agent {me} at {repo} | control port {port} | {role} | other: {others}")
    env_port = os.environ.get("FE_CONTROL_PORT")
    if (env_port or str(DEFAULT_PORT)) != str(port):
        env_name = os.environ.get("FE_AGENT")
        if env_name and env_name != me:  # D165: a session moved here from another checkout
            warnings.append(
                f"{TAG} WARNING: this session still has {env_name}'s env (FE_AGENT={env_name}, FE_CONTROL_PORT="
                f"{env_port or 'unset'}): it started in {env_name}'s checkout. The tools follow the checkout "
                f"(D194): {studio_config.get('sync.follows', 'fe_sync and fe_board')} use {me}, and "
                f"{game_label()} launched from here "
                f"listens on {port}; a script that reads FE_AGENT itself still sees {env_name}.")
        else:
            warnings.append(
                f"{TAG} WARNING: FE_CONTROL_PORT is {env_port or 'unset'} but {me} owns {port}; "
                f"{studio_config.get('game.port_users', 'the game')} "
                f"would use {env_port or DEFAULT_PORT}. Run: {studio_config.tool_cmd('fe_sync.py')} init --agent {me} "
                f"and restart the session. Until then pass --control {port} / --port {port} explicitly.")
    if full:
        run = studio_config.get("sync.run_text")  # how to run and drive the game, if it says
        lines.append(f"{TAG} " + (f"{run} | " if run else "")
                     + f"sync: {studio_config.tool_cmd('fe_sync.py')} pull|push|status")
    return lines, warnings


def sync_lines(repo, reg, auto_ff=True):
    upstream = f'{reg["remote"]}/{reg["branch"]}'
    lines = []
    prog = in_progress(repo)
    if prog:
        lines.append(f"{TAG} a {prog} is in progress; finish or abort it before anything else.")
        return lines
    old_upstream = git_out(repo, "rev-parse", upstream)
    note = maybe_fetch(repo, reg["remote"])
    branch = branch_of(repo)
    ahead, behind = counts(repo, upstream)
    modified, untracked = tree_state(repo)
    pulled = ""
    if (behind and auto_ff and branch == reg["branch"] and not modified and ahead == 0
            and not checkout_lease(repo, reg)):
        p = git(repo, "merge", "--ff-only", upstream)
        if p.returncode == 0:
            pulled = (f"{TAG} pulled {behind} commit(s) from {upstream}; rebuild before running:\n"
                      + subjects(repo, f"{old_upstream}..{upstream}"))
            behind = 0
    if not modified or pulled:
        lines += follow_submodules(repo)
    tree = "clean" if not modified else f"{len(modified)} modified file(s)"
    if untracked:
        tree += f", {len(untracked)} untracked"
    lines.append(f"{TAG} {branch}: {ahead} ahead / {behind} behind {upstream} ({note}) | tree {tree}")
    if pulled:
        lines.append(pulled)
    if behind:
        pull = f"`{studio_config.tool_cmd('fe_sync.py')} pull`"
        how = (f"commit, then {pull} (rebases)" if modified else
               f"{pull} rebases your commits onto it")
        lines.append(f"{TAG} {upstream} has {behind} new commit(s); {how}:\n" + subjects(repo, f"HEAD..{upstream}"))
    if ahead:
        where = ("pushed automatically when this turn ends" if branch == reg["branch"] else
                 f"branch {branch} is not auto-pushed")
        lines.append(f"{TAG} {ahead} local commit(s) not on {upstream} ({where}):\n" + subjects(repo, f"{upstream}..HEAD"))
    return lines


def gpu_lines(procs=None):
    """The GPU's state at session start: the game's adapter says it (D200); a game
    with no GPU adapter has nothing to say."""
    gpu = studio_config.adapter("gpu")
    if not gpu:
        return []
    return gpu.session_lines(app_processes() if procs is None else procs, TAG)


def game_label():
    """How the hooks' lines name a running game (`[game] label`)."""
    return studio_config.get("game.label") or "the game"


def board_lines(name, touch, session=None):
    """The board digest (D116): unread messages and tasks for this agent.
    Imported lazily; a broken board never breaks the sync hooks."""
    try:
        import fe_board
        return fe_board.digest_lines(name, touch=touch, session=session)
    except Exception as e:  # noqa: BLE001
        return [f"[fe-board] unavailable: {e}"]


# ----------------------------------------------------------------- routing (D165)

ROUTE = "[fe-route]"
# A session's turn counts as running this long after its last hook: the
# tool-call hook beats at most once a minute and a tool call runs up to ten.
# An interrupted turn never reaches the Stop hook, so it also ages out here.
WORKING_FOR = 900


def sessions(recorded=False):
    """The board's session rows with the Codex threads read from Codex's own
    records (fe_board.merged_sessions, D173); [] when the board is
    unreachable, so routing fails open.  A checkout with no rows (its hooks
    predate D165) stands in by its agent's presence heartbeat.  recorded=True
    is the board's own rows alone: what a Codex thread's commands wrote."""
    try:
        import contextlib
        import sqlite3
        import fe_board
        import fe_codex
        # Session inspection must never migrate or write the shared board.
        with contextlib.closing(sqlite3.connect(fe_board.db_path().as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute('SELECT * FROM sessions ORDER BY heartbeat DESC')]
            presence = {r['agent']: dict(r) for r in con.execute('SELECT * FROM presence')}
        if recorded:
            return rows
        rows = fe_board.merged_sessions(rows, fe_codex.threads())
    except Exception:  # noqa: BLE001
        return []
    seen = {s["agent"] for s in rows}
    for agent, p in presence.items():
        if agent not in seen and p.get("heartbeat") and p.get("state"):
            rows.append({"id": f"presence:{agent}", "agent": agent, "state": p["state"],
                         "heartbeat": p["heartbeat"]})
    return rows


def ago(ts):
    s = max(0, time.time() - ts)
    return f"{s:.0f}s ago" if s < 90 else f"{s / 60:.0f} min ago" if s < 5400 else f"{s / 3600:.0f} h ago"


def working_elsewhere(name, rows, me=None, stand_in=True):
    """The rows of other sessions whose turn is running in `name`'s checkout.
    A presence stand-in cannot tell this session from another, so a session
    asking about its own checkout mid-life passes stand_in=False."""
    now = time.time()
    return [s for s in rows if s["agent"] == name and s["id"] != me
            and (stand_in or not s["id"].startswith("presence:"))
            and s["state"] == "working" and now - s["heartbeat"] < s.get("limit", WORKING_FOR)]


def scratch_marker(name):
    """What marks agent `name`'s scratch build in a game's command line
    (`[game] scratch`, `{agent}` its lower-case name), or None."""
    fmt = studio_config.get("game.scratch")
    return fmt.format(agent=name.lower()) if fmt else None


def owned_processes(name, path, procs):
    """The game processes run from this checkout or its scratch target; the
    player's build (`[game] play_dir`, D77) belongs to no agent."""
    root = norm(path) + os.sep
    play = studio_config.get("game.play_dir")
    play = os.sep + os.path.normcase(os.path.normpath(play)) + os.sep if play else None
    scratch = scratch_marker(name)
    out = []
    for pid, cmd in procs:
        c = os.path.normcase(cmd.replace("/", "\\"))
        if play and play in c:
            continue
        if root in c or (scratch and scratch in c):
            out.append(pid)
    return out


def checkout_hold(name):
    """What holds `name`'s checkout for the manager, as one line, or None. The board is
    the truth (D191): a managed task holds it while the board has it claimed, in
    progress or blocked, or while its worker runs, whatever lease the manager last
    wrote, so Yotam's edit to the task moves it and a slow supervisor tick lapses
    nothing. A board that cannot be read counts as holding it: the callers route
    sessions away or leave commits local on this answer."""
    import contextlib
    import sqlite3
    import fe_board
    try:
        with contextlib.closing(sqlite3.connect(fe_board.db_path().as_uri() + '?mode=ro', uri=True, timeout=5)) as con:
            h = fe_board.checkout_holds(con).get(name)
    except sqlite3.OperationalError as e:
        if 'unable to open' in str(e):
            return None  # no board yet: nothing can hold a checkout
        return f'the board could not be read ({e})'
    return fe_board.hold_text(h) if h else None


def checkout_lease(repo, reg):
    """The manager's hold on this checkout, if it has one. An interactive session
    there must not pull or push its commits: they are a managed worker's, which lands
    them itself when its task is finished (D188)."""
    me = identify(repo, reg)[0]
    return checkout_hold(me) if me else None


def checkout_state(name, reg, rows, procs, me=None):
    """What stops a new session working in `name`'s checkout: (busy reasons,
    soft notes, the last time any session was seen there)."""
    path = Path(reg["agents"][name]["path"])
    if not (path / ".git").exists():
        return ["no checkout at " + str(path)], [], 0
    busy, soft = [], []
    hold = checkout_hold(name)
    if hold:
        busy.append(hold)
    running = working_elsewhere(name, rows, me)
    if running:
        who = (f"{len(running)} sessions' turns are" if len(running) > 1 else
               "another session's turn is" if me else "a session's turn is")
        busy.append(f"{who} running here (seen {ago(max(s['heartbeat'] for s in running))})")
    prog = in_progress(path)
    if prog:
        busy.append(f"a {prog} is in progress")
    modified, untracked = tree_state(path)
    if modified:
        busy.append(f"{len(modified)} modified file(s)")
    ahead, _ = counts(path, f'{reg["remote"]}/{reg["branch"]}')
    if ahead:
        busy.append(f"{ahead} unpushed commit(s)")
    branch = branch_of(path)
    if not trunk_checkout(path, reg, branch):
        busy.append(f"on branch {branch}")
    pids = owned_processes(name, path, procs)
    if pids:
        busy.append(f"{game_label()} running from it (PID " + ", ".join(map(str, pids)) + ")")
    if untracked:
        soft.append(f"{len(untracked)} untracked")
    last = max((s["heartbeat"] for s in rows if s["agent"] == name and s["id"] != me), default=0)
    return busy, soft, last


def free_checkouts(reg, rows, procs, skip=None):
    """[(name, soft, last)] of the free checkouts, best first: clean before
    untracked-only, then the one whose last session is oldest."""
    free = []
    for name in reg["agents"]:
        if name == skip:
            continue
        busy, soft, last = checkout_state(name, reg, rows, procs)
        if not busy:
            free.append((name, soft, last))
    return sorted(free, key=lambda f: (bool(f[1]), f[2]))


def describe_free(name, soft, last):
    return (f"{name} is free (" + ("; ".join(soft) if soft else "clean") + "; last session "
            + (ago(last) if last else "not seen") + ")")


def route_lines(repo, reg, me, session, arriving, procs, rows):
    """At a new session's start, route it out of a busy checkout; on every
    prompt, warn when another session's turn is running in this checkout."""
    if not me or not session:
        return []
    if arriving:
        busy, _, _ = checkout_state(me, reg, rows, procs, me=session)
    else:
        busy = [f"another session's turn is running here too (seen {ago(s['heartbeat'])})"
                for s in working_elsewhere(me, rows, session, stand_in=False)]
    if not busy:
        return []
    free = free_checkouts(reg, rows, procs, skip=me)
    owner = studio_config.owner()
    head = f"{ROUTE} {me} is busy: " + "; ".join(busy) + "."
    if not arriving:
        also = f" {describe_free(*free[0])}." if free else ""
        return [head + f" Two sessions in one checkout share its tree, build and control port: tell {owner} "
                       f"before you edit anything more.{also}"]
    if not free:
        others = "; ".join(f"{n}: " + "; ".join(checkout_state(n, reg, rows, procs)[0])
                           for n in reg["agents"] if n != me)
        return [head + f" No checkout is free ({others}). Tell {owner} before you edit anything: two "
                       f"sessions in one checkout share its tree, build and control port."]
    name = free[0][0]
    path = reg["agents"][name]["path"]
    return [head + f" {describe_free(*free[0])}.",
            f"{ROUTE} Move there first: call mcp__ccd_directory__change_directory with path \"{path}\" "
            f"(load it with ToolSearch \"select:mcp__ccd_directory__change_directory\"), then end this turn "
            f"at once, telling {owner} in one line that you moved to {name} and why; the request itself waits "
            f"for your next turn. Do nothing in {me} before you go: until this turn ends your hooks, "
            f"FE_AGENT and control port are still {me}'s. Your next turn starts with "
            f"\"{TAG} you are agent {name}\". If a Stop hook (a /goal) will not let the turn end, the move "
            f"has still taken effect: carry on in {path}, the tools follow the checkout (D194). Stay only if {owner} named {me} or the request only reads; "
            f"with no such tool (a terminal session), tell {owner} to open the session in {path}."]


def report(repo, reg, full, hook=False, session=None, source=None):
    lines, warnings = identity_lines(repo, reg, full)
    me = identify(repo, reg)[0]
    procs = app_processes()
    rows = sessions() if hook else []
    arriving = full and source == "startup"
    # Another session's turn is running here: never move its tree under it.
    shared = bool(me and working_elsewhere(me, rows, session, stand_in=arriving))
    lines += warnings
    lines += sync_lines(repo, reg, auto_ff=not shared)
    lines += other_agents(repo, reg)
    lines += gpu_lines(procs)
    # Before board_lines records this session as working here.
    lines += route_lines(repo, reg, me, session, arriving, procs, rows)
    lines += board_lines(me, touch=hook, session=session)
    if full:
        lines += jev_lines()
    return "\n".join(lines)


def cmd_free(repo, reg):
    """Every checkout's state for a new session, and the one to use."""
    rows, procs = sessions(), app_processes()
    for name in reg["agents"]:
        busy, soft, last = checkout_state(name, reg, rows, procs)
        seen = "last session " + (ago(last) if last else "not seen")
        state = "busy: " + "; ".join(busy) if busy else "free (" + ("; ".join(soft) or "clean") + ")"
        print(f"{name:<4} {state} | {seen}")
    free = free_checkouts(reg, rows, procs)
    if free:
        print(f"{ROUTE} next session: {free[0][0]} ({reg['agents'][free[0][0]]['path']})")
    else:
        print(f"{ROUTE} no checkout is free")
    return 0


def jev_lines():
    """D161: which of Jev's uses run, at session start. Never raises."""
    try:
        import fe_jev
        return [fe_jev.summary_line()]
    except Exception as e:  # noqa: BLE001
        return [f"[fe-jev] unavailable: {e}"]


# -------------------------------------------------------------------- sync

class SyncResult:
    def __init__(self, ok, message):
        self.ok, self.message = ok, message


def submodules(repo):
    """The game's submodule paths (the studio, D329), from .gitmodules."""
    if not (Path(repo) / ".gitmodules").is_file():
        return []
    out = git_out(repo, "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    return [line.split(None, 1)[1] for line in out.splitlines() if " " in line]


def pinned(repo, path, rev="HEAD"):
    """The commit the game pins `path` at in `rev` (its gitlink), or ''."""
    out = git_out(repo, "ls-tree", rev, "--", path).split()
    return out[2] if len(out) >= 3 and out[1] == "commit" else ""


def follow_submodules(repo):
    """D329: check each submodule out at the commit the game pins, on its branch, so an
    agent can commit there. A submodule holding work of its own (edits, or commits the
    game does not pin yet) is left as it is: push lands that work. Returns notes."""
    notes = []
    for path in submodules(repo):
        sub, pin = Path(repo) / path, pinned(repo, path)
        if not pin:
            continue
        fresh = not (sub / ".git").exists()
        if fresh:
            p = git(repo, "submodule", "update", "--init", "--", path, timeout=300)
            if p.returncode != 0:
                notes.append(f"{TAG} {path}: submodule init failed: {tail(p.stderr, 2)}")
                continue
        head = git_out(sub, "rev-parse", "HEAD")
        modified, _ = tree_state(sub)
        if head != pin:
            if modified or (git(sub, "merge-base", "--is-ancestor", pin, head).returncode == 0):
                continue  # its own work, ahead of the pin: push lands it
            if git(sub, "cat-file", "-e", pin + "^{commit}").returncode != 0:
                git(sub, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT)
            if git(sub, "merge-base", "--is-ancestor", head, pin).returncode != 0:
                notes.append(f"{TAG} {path}: at {head[:9]}, which the game's pin {pin[:9]} does not "
                             f"contain; rebase it onto origin/{submodule_branch(repo, path)}")
                continue
        branch = submodule_branch(repo, path)
        if fresh or head != pin or branch_of(sub) != branch:
            git(sub, "checkout", "-q", "-B", branch, pin)
            git(sub, "branch", "-q", f"--set-upstream-to=origin/{branch}", branch)
            if not fresh and head != pin:
                notes.append(f"{TAG} {path}: moved to the game's pin {pin[:9]}")
    return notes


def submodule_branch(repo, path):
    name = git_out(repo, "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    for line in name.splitlines():
        key, _, value = line.partition(" ")
        if value == path:
            return git_out(repo, "config", "-f", ".gitmodules", key[:-len(".path")] + ".branch") or "main"
    return "main"


def push_submodules(repo, gate_files=None):
    """D329: before the game is pushed, push each submodule's own commits (rebased onto
    its origin) and commit the game's gitlink to them, so the game never pins a commit
    its submodule's origin lacks. Returns (error or None, notes)."""
    notes = []
    for path in submodules(repo):
        sub = Path(repo) / path
        if not (sub / ".git").exists():
            continue
        branch = submodule_branch(repo, path)
        upstream = f"origin/{branch}"
        note = maybe_fetch(sub, "origin", force=True)
        if "failed" in note or "timed out" in note:
            return f"{path}: {note}", notes
        if branch_of(sub) not in (branch, "HEAD"):
            return f"{path} is on branch {branch_of(sub)}; only {branch} is pushed", notes
        if git(sub, "rev-parse", "--verify", "--quiet", upstream).returncode != 0:
            # A studio repo with no branch yet (its first push): everything is ahead.
            ahead, behind = int(git_out(sub, "rev-list", "--count", "HEAD") or 0), 0
        else:
            ahead, behind = counts(sub, upstream)
        if ahead:
            if behind:
                err = rebase_onto(sub, upstream)
                if err:
                    return f"{path}: {err}", notes
            p = git(sub, "push", "origin", f"HEAD:{branch}", timeout=180)
            if p.returncode != 0:
                return f"{path}: push failed:\n" + tail(p.stderr, 8), notes
            notes.append(f"pushed {ahead} {path} commit(s) to its {upstream}")
        head = git_out(sub, "rev-parse", "HEAD")
        pin = pinned(repo, path)
        if head != pin and (not pin or git(sub, "merge-base", "--is-ancestor", pin, head).returncode == 0):
            subject = git_out(sub, "log", "-1", "--format=%s", "HEAD")
            git(repo, "add", "--", path)
            p = git(repo, "commit", "-q", "-m", f"{path}: {subject}", "--", path)
            if p.returncode != 0:
                return f"could not commit the {path} gitlink:\n" + tail(p.stdout + p.stderr, 6), notes
            notes.append(f"the game now pins {path} at {head[:9]} ({subject})")
        pin = pinned(repo, path)
        if pin and git(sub, "merge-base", "--is-ancestor", pin, upstream).returncode != 0:
            return (f"the game pins {path} at {pin[:9]}, which its {upstream} lacks; "
                    f"push {path} first"), notes
    return None, notes


def submodules_ahead(repo):
    """The submodules with commits their origin lacks."""
    out = []
    for path in submodules(repo):
        sub = Path(repo) / path
        if (sub / ".git").exists() and counts(sub, f"origin/{submodule_branch(repo, path)}")[0]:
            out.append(path)
    return out


def rebase_onto(repo, upstream):
    """Rebase with autostash; on conflict abort and return the failure text."""
    p = git(repo, "rebase", "--autostash", upstream, timeout=300)
    if p.returncode == 0:
        return None
    files = git_out(repo, "diff", "--name-only", "--diff-filter=U")
    git(repo, "rebase", "--abort")
    return (f"rebase onto {upstream} conflicts in:\n  " + "\n  ".join(files.splitlines() or ["(see git)"])
            + f"\nRun `git rebase {upstream}`, resolve ("
            + (studio_config.get("sync.rebase_hint") or "renumber a duplicate decision entry")
            + "), `git rebase --continue`, then "
            f"`{studio_config.tool_cmd('fe_sync.py')} push`.\n" + tail(p.stdout + p.stderr, 6))


def push_gates():
    """The checks a push runs first (`[[sync.push_gate]]`: name, paths, cmd, ...)."""
    return studio_config.get("sync.push_gate", [])


def needs_gate(repo, upstream):
    """The changed files some push gate watches (a prefix in its `paths`)."""
    prefixes = tuple(p for g in push_gates() for p in g.get("paths", []))
    if not prefixes:
        return []
    changed = git_out(repo, "diff", "--name-only", f"{upstream}...HEAD").splitlines()
    return [f for f in changed if f.startswith(prefixes)]


def compile_gate(repo, files=None):
    """Run each push gate a changed file in `files` falls under (every gate when
    `files` is None): the first failure's text, or None when they all pass."""
    for g in push_gates():
        paths = tuple(g.get("paths", []))
        if files is not None and not any(f.startswith(paths) for f in files):
            continue
        name, argv = g.get("name", "push"), list(g["cmd"])
        env = dict(os.environ)
        for k, v in (g.get("env") or {}).items():
            env.setdefault(k, str(v))
        try:
            p = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=g.get("timeout", GATE_TIMEOUT), env=env)
        except subprocess.TimeoutExpired:
            return f"{name} gate timed out"
        except OSError as e:
            return f"{name} gate could not run {argv[0]}: {e}"
        if p.returncode != 0:
            return f"{name} gate failed ({g.get('label') or ' '.join(argv)}):\n" + tail(p.stderr, 25)
    return None


def sync_push(repo, reg, gate=True):
    # A managed worker lands its own task with this too (D188): it has the context to
    # resolve a conflict, so no reviewer or integrator stands between it and trunk.
    remote, branch = reg["remote"], reg["branch"]
    upstream = f"{remote}/{branch}"
    prog = in_progress(repo)
    if prog:
        return SyncResult(False, f"a {prog} is in progress; finish or abort it first")
    # A detached scratch worktree of the trunk lands the same way (D200): every checkout
    # may hold another session's uncommitted edits, and the worktree touches none of them.
    if branch_of(repo) not in (branch, "HEAD"):
        return SyncResult(True, f"on branch {branch_of(repo)}; only {branch} is pushed by the sync tool")
    pulled = ""
    for _attempt in range(3):
        note = maybe_fetch(repo, remote, force=True)
        if "failed" in note or "timed out" in note:
            return SyncResult(False, note)
        err, subs = push_submodules(repo)
        if err:
            return SyncResult(False, err + "\nCommits stay local.")
        ahead, behind = counts(repo, upstream)
        if ahead == 0:
            return SyncResult(True, "; ".join(subs + ["nothing to push"]) + (f"; {pulled}" if pulled else ""))
        if behind:
            old = git_out(repo, "rev-parse", "HEAD")
            err = rebase_onto(repo, upstream)
            if err:
                return SyncResult(False, err)
            pulled = f"rebased onto {behind} new commit(s):\n" + subjects(repo, f"{old}..{upstream}")
        if gate:
            files = needs_gate(repo, upstream)
            if files:
                err = compile_gate(repo, files)
                if err:
                    return SyncResult(False, err + "\nCommits stay local until it compiles.")
        # No bench here (D262, which ends D235's every-commit records): the manager's nightly
        # bench compares main with the night before and bisects a slowdown to its commits.
        p = git(repo, "push", remote, f"HEAD:{branch}", timeout=180)
        if p.returncode == 0:
            msg = "".join(f"{n}\n" for n in subs) + f"pushed {ahead} commit(s) to {upstream}:\n" + subjects(repo, f"{upstream}~{ahead}..{upstream}")
            if pulled:
                msg += "\n" + pulled + "\nRebuild: the pushed tree includes the other agent's changes."
            return SyncResult(True, msg)
        if "rejected" not in (p.stderr + p.stdout) and "fetch first" not in p.stderr:
            return SyncResult(False, "push failed:\n" + tail(p.stderr, 8))
    return SyncResult(False, "push kept being rejected; the other agent is pushing. Retry in a moment.")


def sync_pull(repo, reg):
    remote, branch = reg["remote"], reg["branch"]
    upstream = f"{remote}/{branch}"
    prog = in_progress(repo)
    if prog:
        return SyncResult(False, f"a {prog} is in progress; finish or abort it first")
    old = git_out(repo, "rev-parse", upstream)
    note = maybe_fetch(repo, remote, force=True)
    if "failed" in note or "timed out" in note:
        return SyncResult(False, note)
    ahead, behind = counts(repo, upstream)
    if behind == 0:
        extra = f" ({ahead} local commit(s) to push)" if ahead else ""
        notes = follow_submodules(repo)
        return SyncResult(True, f"up to date with {upstream}{extra}" + "".join("\n" + n for n in notes))
    if not trunk_checkout(repo, reg):
        return SyncResult(False, f"on branch {branch_of(repo)}; switch to {branch} to pull the trunk")
    modified, _ = tree_state(repo)
    if ahead == 0 and not modified:
        p = git(repo, "merge", "--ff-only", upstream)
        if p.returncode != 0:
            return SyncResult(False, "fast-forward failed:\n" + tail(p.stderr, 6))
    else:
        err = rebase_onto(repo, upstream)
        if err:
            return SyncResult(False, err)
    notes = follow_submodules(repo)
    return SyncResult(True, f"pulled {behind} commit(s) from {upstream}; rebuild before running:\n"
                      + subjects(repo, f"{old}..{upstream}") + "".join("\n" + n for n in notes))


# ------------------------------------------------------------------- hooks

def read_payload():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def hook_repo(payload):
    return repo_root(payload.get("cwd") or os.getcwd())


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"{TAG} {reason}"}}))
    return 0


def cmd_pre_bash(payload):
    # Both shells: until D161 the PowerShell tool's commands skipped every refusal below.
    if payload.get("tool_name", "Bash") not in ("Bash", "PowerShell"):
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return 0
    seg = r"[^|;&\n]*"  # stay within one shell command
    if re.search(rf"\bgit\b{seg}\bpush\b", cmd) and "fe_sync.py" not in cmd:
        return deny("raw `git push` skips the rebase/compile gate shared by both agents. "
                    f"Run: {studio_config.tool_cmd('fe_sync.py')} push")
    repo = None
    if re.search(rf"\bgit\b{seg}\bcommit\b{seg}--amend", cmd):
        repo = hook_repo(payload)
        reg = load_registry()
        upstream = f'{reg["remote"]}/{reg["branch"]}'
        if git(repo, "merge-base", "--is-ancestor", "HEAD", upstream).returncode == 0:
            return deny(f"HEAD is already on {upstream}; amending rewrites history the other agent has. "
                        "Make a new commit instead.")
    for rule in studio_config.get("sync.primary_only", []):  # commands only the primary checkout runs
        if rule["match"] not in cmd:
            continue
        repo = repo or hook_repo(payload)
        reg = load_registry()
        me, _, _ = identify(repo, reg)
        if not (me and reg["agents"][me].get("primary")):
            return deny(rule.get("reason") or f"only the primary agent runs {rule['match']}.")
    gpu = studio_config.adapter("gpu")
    verdict = gpu.pre_bash(cmd, payload) if gpu else None  # D200: the game's GPU lease
    if verdict == "":
        return 0  # a command that waits for the GPU itself: through, unread
    if verdict:
        return deny(verdict)
    rc = jev_gate(cmd)
    if not _jev_gate_on():
        advise(cmd, payload)
    return rc


# D253: what the night's audit found agents doing by hand that costs turns. A warning
# goes to the model beside the call; it never refuses and never decides a permission.
SLEEP_LOOP = re.compile(r"\b(until|while)\b.*\b(sleep|Start-Sleep)\b|\bfor\b.*\b(sleep|Start-Sleep)\b", re.S)
SOURCE_EXTS = studio_config.get("sync.source_exts", [])  # the source files fe_index.py shows
SOURCE_WINDOW = re.compile(
    (r"(?:\bsed\s+-n\b|\bcat\b|\bhead\b|\btail\b|Get-Content)[^|;&\n]*?"
     r"[\w./\\-]+\.(?:" + "|".join(map(re.escape, SOURCE_EXTS)) + r")\b|" if SOURCE_EXTS else "")
    + r"(?:\bsed\s+-n\b|\bcat\b|Get-Content)[^|;&\n]*?docs[/\\](?:decisions|roadmap)\.md")
ADVICE = {
    "sleep": "D253: a sleep loop spends turns and minutes waiting. Run what you wait for itself, it "
             "blocks (fe_land.py, fe_gpu.py wait, fe_regress.py check, a test script); in Claude Code "
             "run a long one with run_in_background and you are told when it ends.",
    "window": "D97: read code with fe_index.py show NAME (or path:L1-L2) and a history with "
              "fe_docs.py show Dnn: a window of a file stays in the context and is re-read every turn.",
}


def _jev_gate_on():
    try:
        import fe_jev
        return fe_jev.enabled("gate")
    except Exception:  # noqa: BLE001
        return False


def advise(cmd, payload=None):
    """Print the D253 warnings a command earns, as context for the model; returns them."""
    found = []
    if re.search(r"\|\s*python3?\s+-(\s|$)|python3?\s+-\s*<<|python3?\s+-c\b", cmd):
        return found  # an inline script: a loop or a path in it is data, not a step
    if SLEEP_LOOP.search(cmd):
        found.append(ADVICE["sleep"])
    if SOURCE_WINDOW.search(cmd) and "fe_index.py" not in cmd:
        found.append(ADVICE["window"])
    if found:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": f"{TAG} " + " ".join(found)}}))
    return found


def jev_gate(cmd):
    """D161: Jev's reading of a command the refusals above let through. It can
    only ask Yotam to confirm, never deny; off (the default), it prints nothing."""
    try:
        import fe_jev
        if not fe_jev.enabled("gate"):
            return 0
        gpu = studio_config.adapter("gpu")
        return fe_jev.hook_gate(cmd, lambda: bool(gpu and gpu.bench_running()))
    except Exception:  # noqa: BLE001 -- Jev never breaks the sync hooks
        return 0


def cmd_stop(payload):
    if os.environ.get('FE_MANAGER_RUN'):
        return 0  # D188: the worker lands its task itself, when it is finished, not per turn
    repo = hook_repo(payload)
    reg = load_registry()
    upstream = f'{reg["remote"]}/{reg["branch"]}'
    ahead, _ = counts(repo, upstream)
    subs = submodules_ahead(repo)  # D329: the studio's own commits wait to be pushed too
    if (ahead == 0 and not subs) or branch_of(repo) != reg["branch"]:
        return 0
    ahead = ahead or len(subs)
    lease = checkout_lease(repo, reg)
    if lease:
        print(json.dumps({"systemMessage": f"{TAG} {ahead} commit(s) stay local: {lease}, and its "
                                           f"worker lands them (drop or re-ready the task on the board "
                                           f"to free the checkout)"}))
        return 0
    result = sync_push(repo, reg)
    if result.ok:
        print(json.dumps({"systemMessage": f"{TAG} {result.message}"}))
        return 0
    if payload.get("stop_hook_active"):
        print(json.dumps({"systemMessage": f"{TAG} still not pushed: {result.message}"}))
        return 0
    print(json.dumps({"decision": "block",
                      "reason": f"{TAG} {ahead} commit(s) are not on {upstream}: {result.message}\n"
                                f"Resolve this, then run: {studio_config.tool_cmd('fe_sync.py')} push"}))
    return 0


def cmd_context(payload, full):
    if os.environ.get('FE_MANAGER_RUN'):
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart' if full else 'UserPromptSubmit',
              'additionalContext': 'Managed run: work the task by the task skill '
              '(.agents/skills/task/SKILL.md, D224). Stay in this checkout; commit and return complete: the '
              'manager lands it with `fe_land.py` (D253: the gates, then the push) and resumes you if a gate '
              'stops it, so do not run the closing gates or push yourself; GPU launches go through fe_manager.py gpu; '
              f'questions for {studio_config.owner()} go in your result\'s asks, each with its options.'}}))
        return 0
    repo = hook_repo(payload)
    reg = load_registry()
    text = report(repo, reg, full, hook=True, session=payload.get("session_id"),
                  source=payload.get("source"))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart" if full else "UserPromptSubmit",
        "additionalContext": text}}))
    return 0


# --------------------------------------------------------------------- cli

def cmd_init(args):
    name = port = repo = None
    it = iter(args)
    for a in it:
        if a == "--agent":
            name = next(it, None)
        elif a == "--port":
            port = int(next(it))
        elif a == "--repo":
            repo = Path(next(it))
        else:
            raise SystemExit(f"{TAG} unknown init option {a}")
    reg = load_registry()
    if name not in reg["agents"]:
        raise SystemExit(f"{TAG} --agent must be one of {', '.join(reg['agents'])} (edit {REGISTRY.name} to add one)")
    repo = repo_root(repo or os.getcwd())
    port = port or reg["agents"][name].get("port", DEFAULT_PORT)
    local = repo / ".claude" / "settings.local.json"
    settings = {}
    if local.exists():
        try:
            settings = json.loads(local.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise SystemExit(f"{TAG} {local} is not valid JSON; fix or remove it first")
    env = settings.setdefault("env", {})
    env["FE_AGENT"] = name
    env["FE_CONTROL_PORT"] = str(port)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    git(repo, "config", "pull.rebase", "true")
    ignored = git(repo, "check-ignore", "-q", str(local)).returncode == 0
    print(f"{TAG} {name}: wrote {local} (FE_AGENT={name}, FE_CONTROL_PORT={port}); pull.rebase=true")
    if not ignored:
        print(f"{TAG} WARNING: {local.relative_to(repo)} is not gitignored; add it to .gitignore")
    print(f"{TAG} restart the Claude Code session in {repo} so the env and hooks take effect")
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "push" and os.environ.get("FE_MANAGER_RUN") and studio_config.get("adapters.mobile"):
        print(f"{TAG} managed mobile providers cannot publish; commit and return to the coordinator")
        return 2
    if cmd == "init":
        return cmd_init(args)
    if cmd in ("session-start", "prompt", "pre-bash", "stop"):
        payload = read_payload()
        if cmd == "pre-bash":
            return cmd_pre_bash(payload)
        if cmd == "stop":
            return cmd_stop(payload)
        return cmd_context(payload, full=(cmd == "session-start"))
    repo = repo_root(os.getcwd())
    reg = load_registry()
    codex = os.environ.get("CODEX_THREAD_ID")
    if cmd == "status":
        if codex:  # D165: no hooks, so its first status is its arrival
            sid = f"codex:{codex}"
            # Its own commands' rows alone: Codex's records list it from its first turn.
            first = not any(s["id"] == sid for s in sessions(recorded=True))
            print(report(repo, reg, full=True, hook=True, session=sid, source="startup" if first else None))
        else:
            print(report(repo, reg, full=True))
        return 0
    if codex and cmd in ("pull", "push"):
        try:
            import fe_board
            with fe_board.Board() as b:
                fe_board.codex_touch(b, identify(repo, reg)[0])
        except Exception:  # noqa: BLE001
            pass
    if cmd == "free":
        return cmd_free(repo, reg)
    if cmd == "pull":
        r = sync_pull(repo, reg)
        # D148: Codex has no SessionStart hook, so a pull also runs the game's after-pull
        # scripts (`[sync] after_pull`, repo-relative Python scripts and their arguments).
        for script, *rest in studio_config.get("sync.after_pull", []):
            done = run([sys.executable, str(repo / script), *rest], cwd=repo)
            if done.stdout.strip():
                print(done.stdout.strip())
    elif cmd == "push":
        r = sync_push(repo, reg, gate="--no-gate" not in args)
    else:
        print(__doc__)
        return 2
    print(f"{TAG} {r.message}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
