#!/usr/bin/env python3
"""The cleanup crew's scorecard and its nightly tasks (D265).

    fe_crew.py score [--json] [--repo PATH]    the night's scorecard: code health, detours, cost
    fe_crew.py bodies [--repo PATH]            the two task bodies the crew would be given tonight
    fe_crew.py review [REV] [--model M]        a second agent's read of one crew commit (D280)
    fe_crew.py report T<n>                     what a landed crew task did, as the board shows it (D319)

Two maintainers run after the nightly bench (manager_crew.py):

- **the code crew** (`.agents/skills/tidy-code/SKILL.md`) works from deltas
  and reasons, not targets (D280): its task names what got worse yesterday and
  the commits that did it, and why each piece of debt costs. Each commit
  carries a directive (the problem, the evidence, the change, what should
  move) and must pass `fe_health.py`'s gate (bit-identical, no fewer tests, no
  new clippy warnings, no new module cycle); complexity and duplication are
  advisory, justified or undone. `review` then has a second agent, shown the
  directive and the diff but no metrics, judge whether the conceptual load
  fell; its verdict is the commit's `Crew-Review:` trailer;
- **the process crew** (`.agents/skills/tidy-process/SKILL.md`) is judged by
  `fe_usage.py`: the share of agent tokens spent on detours (working around a
  place, a tool, a silent failure, the environment, or writing a script the
  project lacks), the recurrence of what it targeted and the adoption of what
  it built; cost per accepted task is the lagging check, read over weeks.

A crew commit carries the trailer `Crew: T<n>`, so its commits are found
after the rebase that lands them. A process crew's report names its target as
`Target: <cause> | <tool key>` and what it built as `Tool: <text agents type>`.
Everything here but `review` is code reading git, the transcripts and the board: no model runs.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fe_health  # noqa: E402
import fe_loc  # noqa: E402
import fe_usage  # noqa: E402
import studio_config  # noqa: E402  (how tools are named, and the game's words: [crew] in studio.toml)

TAG = "[fe-crew]"
CODE, PROCESS = "Code crew", "Process crew"   # the nightly tasks' title prefixes
CODEX_LIMIT = 32 * 1024                         # Codex reads AGENTS.md files up to this (project_doc_max_bytes)
# A report line's key ends where its prose starts, at " (", " - " or " -- ": "Target: environment | sh: inline
# python (the ...)" names `sh: inline python`; "Tool: python x.py gpu -- CMD (and ...)" names `python x.py gpu`;
# a backticked tool is the text in its backticks (D319).
_KEY_END = r"(?=\s+\(|\s+[—–-]{1,2}\s|`|\s*$)"
TARGET = re.compile(rf"^\s*Target:\s*(\w+)\s*\|\s*`?(.+?){_KEY_END}", re.M)
BUILT = re.compile(rf"^\s*Tool:\s*(?:`([^`\n]+)`|(.+?){_KEY_END})", re.M)
NO_TOOL = re.compile(r"^(no|none)\b", re.I)   # "Tool: no new tool. ..." built nothing to count
VERDICT = re.compile(r"^Crew-Review:\s*(keep|revise|revert)\b", re.M)
REVIEW_MODEL = "sonnet"
# The project as the blind reviewer is told it, and what proves a refactor changed nothing ([crew]).
PROJECT = studio_config.get("crew.project") or studio_config.name()
IDENTICAL = studio_config.get("crew.identical", "its behaviour is shown unchanged")
DIFF_MAX = 80_000                               # past this a commit is more than one abstraction


def point_at(repo: Path) -> None:
    """Measure this repository (the manager runs from a release folder, not a checkout)."""
    for mod in (fe_health, fe_loc, fe_usage):
        mod.ROOT = repo


def git(*args: str) -> str:
    return fe_health.git(*args).decode("utf-8", errors="replace").strip()


def rev_before(ts: float, ref: str = "origin/main") -> str:
    return git("rev-list", "-1", f"--before={int(ts)}", ref)


def board():
    import fe_board
    con = sqlite3.connect(f"file:{fe_board.db_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def crew_tasks(con, prefix: str, since: float) -> list[dict]:
    rows = con.execute(
        "SELECT t.id, t.title, t.status, t.created, m.base, m.landed_head, m.evidence FROM tasks t "
        "LEFT JOIN pm_tasks m ON m.task_id = t.id WHERE t.title LIKE ? AND t.created >= ? ORDER BY t.id",
        (prefix + "%", since)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            ev = json.loads(d.pop("evidence") or "{}")
        except ValueError:
            ev = {}
        d["summary"] = ev.get("summary") or ""
        d["crew"] = ev.get("crew") or {}
        d["landed_at"] = None
        out.append(d)
    for d in out:
        e = con.execute("SELECT MAX(at) FROM events WHERE ref=? AND summary LIKE '%status=review%'", (f"T{d['id']}",)).fetchone()
        d["landed_at"] = e[0] if e else None
    return out


def own_commits(tid: int, ref: str = "origin/main", days: int = 30) -> list[str]:
    """The crew task's commits (its `Crew: T<n>` trailer), oldest first."""
    raw = git("log", ref, f"--since={days}.days", "--reverse", "--format=%H", f"--grep=^Crew: T{tid}$")
    return [x for x in raw.splitlines() if x]


def rework(shas: list[str]) -> dict:
    """Of the code lines these commits added, how many HEAD no longer has from them (GitClear's churn)."""
    if not shas:
        return {"added": 0, "kept": 0, "rate": None}
    added = sum(r["code_added"] for s in shas for r in fe_loc.commits(f"{s}~1..{s}"))
    files = set()
    for s in shas:
        files.update(f for f in git("show", "--name-only", "--format=", s).splitlines() if fe_loc.area(f) in fe_loc.CODE)
    kept = 0
    short = {s[:8] for s in shas}
    for f in files:
        try:
            blame = git("blame", "--line-porcelain", "origin/main", "--", f)
        except SystemExit:
            continue  # the file is gone
        lines = blame.split("\n")
        cur = None
        for ln in lines:
            m = re.match(r"^([0-9a-f]{40}) \d+ \d+", ln)
            if m:
                cur = m.group(1)[:8]
            elif ln.startswith("\t") and cur in short and not fe_loc.comment_only(ln[1:], fe_loc.kind(f)):
                kept += 1
    return {"added": added, "kept": kept, "rate": round(1 - kept / added, 2) if added else None}


# ---- the two cards ------------------------------------------------------------------------

def code_card(con, now: float) -> dict:
    head = git("rev-parse", "origin/main")
    prev = rev_before(now - 86400)
    project = fe_health.delta(prev, head) if prev and prev != head else None
    reg = fe_health.register(head, top=10)
    crews = []
    for t in crew_tasks(con, CODE, now - 14 * 86400):
        shas = own_commits(t["id"])
        card = {"task": t["id"], "status": t["status"], "commits": len(shas)}
        if shas:
            d = fe_health.delta(f"{shas[0]}~1", shas[-1])
            card["reviews"] = [m.group(1) if (m := VERDICT.search(git("log", "-1", "--format=%B", x))) else None
                               for x in shas]
            card.update(score=d["score"], passed=d["passed"], gate=d["gate"], ratchet=d["ratchet"][:5],
                        code_lines=d["guards"]["code_lines"], dup=[d["before"]["dup_tokens"], d["after"]["dup_tokens"]],
                        rework=rework(shas) if t["landed_at"] and now - t["landed_at"] > 86400 else None)
        crews.append(card)
    return {"head": head[:9], "health": reg["summary"],
            "register": {k: reg[k] for k in ("functions", "clones", "coupled", "tangles")},
            # the day's own work on main, for context: its plain penalty change (the weighted score
            # charges new code the heaviest weight, which is right for the crew and noise for features)
            "last_day": None if not project else {
                "penalty": [project["before"]["penalty"], project["after"]["penalty"]],
                "dup_tokens": [project["before"]["dup_tokens"], project["after"]["dup_tokens"]],
                "ratchet": project["ratchet"], "guards": project["guards"],
                "worse": attributed(project["worse"], prev, head), "cycles": project["cycles"]},
            "crew": crews}


def attributed(worse: list[dict], a: str, b: str, top: int = 10) -> list[dict]:
    """The day's worst regressions, each with the commits that touched the function (D280: a delta
    with its cause is actionable, a dashboard of numbers is not)."""
    grew = lambda w: (w.get("file_commits", w["weight"] - 1) + 1) * (w["after"] - (w["before"] if w["before"] is not None else w["limit"]))  # noqa: E731
    out = []
    for w in sorted(worse, key=lambda w: -grew(w))[:top]:
        commits = []
        if w.get("start") and w.get("end"):
            try:
                log = git("log", "--format=%h %s", "-s", "-L", f"{w['start']},{w['end']}:{w['file']}", f"{a}..{b}")
                commits = [ln for ln in log.splitlines() if ln.strip()][:3]
            except SystemExit:  # fe_health.git's failure: the span moved past the file's end
                pass
        out.append({**w, "commits": commits})
    return out


def process_card(con, now: float) -> dict:
    hi = datetime.fromtimestamp(now).astimezone()
    day = fe_usage.detours(hi - timedelta(days=1), hi, 10)
    week = fe_usage.detours(hi - timedelta(days=8), hi - timedelta(days=1), 10)
    cost = fe_usage.cost(14)
    trend = [r for r in fe_usage.trend(9) if not r.get("partial")]  # today so far is not a day
    agents = (fe_usage.ROOT / "AGENTS.md").stat().st_size if (fe_usage.ROOT / "AGENTS.md").exists() else 0
    targets = []
    for t in crew_tasks(con, PROCESS, now - 14 * 86400):
        if not t["landed_at"]:
            continue
        for cause, key in reported_targets(t):
            targets.append(dict(task=t["id"], cause=cause, key=key, **recurrence(cause, key, t["landed_at"], now)))
        for text in reported_tools(t):
            targets.append(dict(task=t["id"], tool=text, **adoption(text, t["landed_at"], now)))
    return {"detours": {"day": slim(day), "week": slim(week)}, "cost": {"all": cost["all"], "classes": cost["classes"]},
            "trend": [{k: r[k] for k in ("day", "per_line", "per_line_7d", "changed", "tokens")} for r in trend],
            "instructions": {"agents_md_bytes": agents, "codex_limit": CODEX_LIMIT, "over": agents > CODEX_LIMIT},
            "targets": targets, "crew": [{"task": t["id"], "status": t["status"]}
                                         for t in crew_tasks(con, PROCESS, now - 14 * 86400)]}


def slim(d: dict) -> dict:
    return {"all": d["all"], "managed": d["managed"], "by_cause": d["by_cause"],
            "patterns": [{k: p[k] for k in ("cause", "key", "count", "sessions", "minutes", "tokens", "errors", "example")}
                         for p in d["patterns"][:8]], "reported": len(d["reported"])}


def recurrence(cause: str, key: str, landed: float, now: float) -> dict:
    """The target's detours per 100 sessions in the 7 days before it landed and since."""
    def rate(lo: float, hi: float):
        a, b = datetime.fromtimestamp(lo).astimezone(), datetime.fromtimestamp(hi).astimezone()
        loaded = fe_usage.load(a, b)
        d = fe_usage.detours(a, b, 50, loaded)
        n = sum(1 for x in d["found"] if x["cause"] == cause and key in x["key"])
        return round(100 * n / max(1, len(loaded[1])), 1)
    return {"before": rate(landed - 7 * 86400, landed), "since": rate(landed, now)}


def adoption(text: str, landed: float, now: float) -> dict:
    a, b = datetime.fromtimestamp(landed).astimezone(), datetime.fromtimestamp(now).astimezone()
    _, sessions = fe_usage.load(a, b)
    uses = sum(1 for s in sessions for c in s["calls"] if text in json.dumps(c["input"]))
    return {"uses": uses, "sessions": len(sessions)}


def reported_targets(t: dict) -> list[tuple[str, str]]:
    """What a process crew task targeted: its report's `crew.target`, else its summary's Target: lines."""
    tg = (t.get("crew") or {}).get("target") or {}
    if tg.get("cause") and tg.get("key"):
        return [(tg["cause"], tg["key"])]
    return TARGET.findall(t["summary"])


def reported_tools(t: dict) -> list[str]:
    tool = ((t.get("crew") or {}).get("tool") or "").strip().strip("`")
    tools = [tool] if tool else [(a or b).strip("` ") for a, b in BUILT.findall(t["summary"])]
    return [x for x in tools if x and not NO_TOOL.match(x)]


def score(now: float | None = None) -> dict:
    now = now or time.time()
    con = board()
    return {"day": datetime.fromtimestamp(now).date().isoformat(), "at": now,
            "code": code_card(con, now), "process": process_card(con, now)}


def code_after(t: dict) -> str:
    """A code crew task's numbers on main: its score, lines, duplication, rework and verdicts (D280, D319)."""
    reviews = [v or "none" for v in t.get("reviews") or []]
    dup = t.get("dup") or []
    return (f"score {t['score']:+.0f} {'passed' if t['passed'] else 'FAILED'}, code lines {t['code_lines']:+d}"
            + (f", duplicated tokens {dup[0]} -> {dup[1]}" if len(dup) == 2 else "")
            + (f", rework {t['rework']['rate']}" if t.get("rework") and t["rework"].get("rate") is not None else "")
            + (f", reviews {', '.join(reviews)}" if reviews else ""))


def process_after(t: dict) -> str:
    if "cause" in t:
        return f"target {t['cause']} {t['key'][:40]}: {t['before']} -> {t['since']} per 100 sessions"
    return f"tool `{t['tool'][:40]}`: used {t['uses']} times in {t['sessions']} sessions"


def followups(s: dict) -> list[tuple[int, str]]:
    """The morning-after numbers for each crew task the scorecard measured, for that task's own thread (D319)."""
    out = {}
    for t in s["code"]["crew"]:
        if "score" in t:
            out[t["task"]] = [f"On main the morning after ({s['day']}): " + code_after(t) + "."
                              + ("" if t.get("rework") else " Rework is measured from a day after it lands.")]
    for t in s["process"]["targets"]:
        out.setdefault(t["task"], [f"Measured the morning after ({s['day']}):"]).append(process_after(t) + ".")
    return [(tid, "\n".join(lines)) for tid, lines in out.items()]


def summary(s: dict) -> str:
    """The scorecard in one Discord message."""
    c, p = s["code"], s["process"]
    h = c["health"]
    lines = [f"**Cleanup crew scorecard, {s['day']}**",
             f"Code ({c['head']}): {h['functions']} functions; past a limit cognitive {h['over']['cognitive']}, "
             f"statements {h['over']['lines']}, args {h['over']['args']}; duplicated {h['dup_percent']}%; penalty {h['penalty']}."]
    if c["last_day"]:
        d = c["last_day"]
        lines.append(f"Last 24 h on main: penalty {d['penalty'][0]} -> {d['penalty'][1]}, duplicated tokens "
                     f"{d['dup_tokens'][0]} -> {d['dup_tokens'][1]}, code lines {d['guards']['code_lines']:+d}"
                     + (f", {len(d['ratchet'])} functions past or further past a limit" if d["ratchet"] else ""))
    for t in c["crew"]:
        if "score" in t:
            lines.append(f"Code crew T{t['task']}: " + code_after(t))
    top = c["register"]["functions"][:3]
    if top:
        lines.append("Worst debt: " + "; ".join(f"{f['name']} ({f['cognitive']} cog, {f['lines']} stmts)" for f in top))
    dd, dw = p["detours"]["day"]["all"], p["detours"]["week"]["all"]
    lines.append(f"Detours: {dd['token_share'] * 100:.1f}% of tokens in the last day (week before {dw['token_share'] * 100:.1f}%), "
                 f"{dd['detour_minutes']:.0f} min")
    pats = p["detours"]["day"]["patterns"][:3]
    if pats:
        lines.append("Top: " + "; ".join(f"{x['cause']} {x['key'][:40]} x{x['count']}" for x in pats))
    a = p["cost"]["all"]
    lines.append(f"Cost per accepted task (14 d): ${a['cost_of_pass']} cost-of-pass, ${a['median_accepted']} median, "
                 f"{a['accepted']}/{a['tasks']} accepted")
    for t in p["targets"]:
        lines.append(f"Process crew T{t['task']} " + process_after(t))
    i = p["instructions"]
    if i["over"]:
        lines.append(f"AGENTS.md is {i['agents_md_bytes']} bytes, past Codex's {i['codex_limit']}: Codex workers miss its end.")
    lines.append(f"`{studio_config.tool_cmd('fe_crew.py')} score` has the rest.")
    return "\n".join(lines)[:1990]


# ---- the nightly task bodies --------------------------------------------------------------

def code_body(s: dict) -> str:
    """The code crew's task: deltas with their causes and the reason each piece of debt costs, not a
    number to raise (D280). The numbers point at a problem; the crew names the problem."""
    c = s["code"]
    reg = c["register"]
    why = lambda n: f"changed in {n} commits in 90 days: every change there has to read it"  # noqa: E731
    day = c["last_day"] or {}
    worse = "\n".join(
        f"- `{w['name']}` ({w['file']}): {w['measure']} "
        + (f"{w['before']} -> {w['after']}" if w["before"] is not None else f"new at {w['after']}")
        + f" (limit {w['limit']}); its file {why(w.get('file_commits', w['weight'] - 1))}."
        + ("\n  By: " + "; ".join(w["commits"]) if w.get("commits") else "")
        for w in day.get("worse") or [])
    short = fe_health.short_module
    cycles = "\n".join(f"- module {short(x['from'])} -> {short(x['to'])} now lies on a loop" for x in day.get("cycles") or [])
    tangles = "\n".join(
        f"- {len(t['modules'])} modules use each other in a loop: {', '.join(t['modules'][:12])}"
        f"{' ...' if len(t['modules']) > 12 else ''}. A change to any of them can reach any other, and none can "
        f"be understood or moved on its own. "
        f"{len(t['edges'])} edges hold it shut, among them: {'; '.join(t['edges'][:6])}"
        for t in (reg.get("tangles") or [])[:3])
    fns = "\n".join(f"- `{f['name']}` ({f['file']}): cognitive {f['cognitive']}, {f['lines']} statements, "
                    f"{f['args']} args; its file {why(f['weight'] - 1)}." for f in reg["functions"][:8])
    clones = "\n".join(f"- {x['a']} = {x['b']} ({x['lines']} lines): a fix to one has to be found and made in the other."
                       for x in reg["clones"][:6])
    coupled = "\n".join(f"- {x['a']} + {x['b']}: changed together in {x['shared']} commits ({x['degree']:.0%} of the "
                        f"rarer one's): a change to one nearly always needs the other." for x in reg["coupled"][:6])
    last = "\n".join(f"- T{t['task']}: passed {t.get('passed')}, reviews {t.get('reviews')}, score {t.get('score')}, "
                     f"lines {t.get('code_lines')}, rework {t.get('rework')}" for t in c["crew"]) or "- (no crew night yet)"
    return f"""## Why
Fast iteration leaves debt where the code changes most, and the next agent pays for it on every change there. Tonight,
by the tidy-code skill (`.agents/skills/tidy-code/SKILL.md`), make up to three behaviour-identical changes that make
this code easier to change. There is no score to raise: the numbers below point at problems. Each commit names the
problem it fixes and why it costs (its directive), and a second agent reads the diff without the numbers and judges
whether the code got easier to understand.

## What got worse in the last day, and what did it (origin/main {c['head']})
{worse or '- nothing past a limit'}
{cycles}

## Module loops (structure: a new edge on one fails the gate)
{tangles or '- none'}

## Where debt costs most (complexity x how often its file changes)
{fns}

## Duplicated
{clones or '- none'}

## Files that change together (a shared idea with no home, or one idea split in two)
{coupled or '- none'}

## Last crew nights
{last}

## Done when
Up to three commits, one abstraction each. Each commit message carries its directive block and the trailers
`Crew: T<this task>` and `Crew-Review: keep` (from `{studio_config.tool_cmd('fe_crew.py')} review`); `fe_health.py check`
passes its gate, every advisory line it prints is justified in the commit or undone, and {IDENTICAL}.
"""


def process_body(s: dict) -> str:
    p = s["process"]
    d = p["detours"]["day"]
    pats = "\n".join(f"- **{x['cause']}** `{x['key'][:70]}`: {x['count']} times in {x['sessions']} sessions, "
                     f"{x['minutes']:.0f} min, {fe_usage.k(x['tokens'])} tokens"
                     + "".join(f"\n  - `{e[:110]}`" for e in x["errors"][:2]) for x in d["patterns"][:8])
    w = p["detours"]["week"]["patterns"][:5]
    week = "\n".join(f"- {x['cause']} `{x['key'][:70]}`: {x['count']} in {x['sessions']} sessions, {x['minutes']:.0f} min"
                     for x in w)
    i = p["instructions"]
    targets = "\n".join(f"- T{t['task']}: " + (f"target {t['cause']} {t['key']}: {t['before']} -> {t['since']} per 100 sessions"
                                                if "cause" in t else f"tool `{t['tool']}` used {t['uses']} times")
                        for t in p["targets"]) or "- (no crew night yet)"
    a = p["cost"]["all"]
    return f"""## Why
Agents spend {d['all']['token_share'] * 100:.1f}% of their tokens working around the project instead of doing their tasks:
finding where they are, a tool or hook that misfires, a call that silently did nothing, the shell, or a script written
because a tool is missing. Tonight's job is to remove the costliest of those at its cause, by the tidy-process skill
(`.agents/skills/tidy-process/SKILL.md`), measured by `{studio_config.tool_cmd('fe_usage.py')} detours`.

## The last day's detours (fe_usage.py detours)
{pats or '- none found'}

## The week before
{week or '- none'}

## Earlier crew nights
{targets}

## Context
- Cost per accepted task, 14 days: cost-of-pass ${a['cost_of_pass']}, median ${a['median_accepted']}, {a['accepted']}/{a['tasks']} accepted.
- AGENTS.md is {i['agents_md_bytes']} bytes; Codex reads {i['codex_limit']}{' — it is over: Codex workers miss the end of the rules' if i['over'] else ''}.

## Done when
One cause fixed (two if small), its expected effect stated, and the report naming `Target: <cause> | <tool key>` and,
for a tool, `Tool: <what agents will type>`, so tomorrow's scorecard measures it.
"""


REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "load", "names", "responsibilities", "call_sites", "indirection", "fixes_directive",
                 "findings", "summary"],
    "properties": {
        "verdict": {"enum": ["keep", "revise", "revert"]},
        "load": {"enum": ["lower", "same", "higher"]},
        "names": {"enum": ["better", "same", "worse"]},
        "responsibilities": {"enum": ["clearer", "same", "muddier"]},
        "call_sites": {"enum": ["easier", "same", "harder"]},
        "indirection": {"enum": ["earned", "none added", "gratuitous"]},
        "fixes_directive": {"type": "boolean"},
        "findings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["where", "problem", "fix"],
                                                "properties": {"where": {"type": "string"}, "problem": {"type": "string"},
                                                               "fix": {"type": "string"}}}},
        "summary": {"type": "string"},
    },
}

REVIEW_PROMPT = """You review one refactoring commit of {project} for one thing: did it lower the conceptual load of the code, what a reader must hold in mind to change
it safely? Behaviour is already proven bit-identical: do not review correctness or performance.

Metrics can improve while the code gets harder to understand, so you are not shown them. Look for: a function split
into pieces that are not steps a reader would name; a helper named for where it came from rather than what it does; a
parameter or flag per caller; an indirection that hides a plain sequence; a trait, macro or table with one user; a
name that now means two things; and, the other way, duplication that really did become one idea with one name.

The commit message says what it set out to fix (its directive):

{message}

The diff:

{diff}

You may Read, Grep and Glob the repository to see the call sites and the code around the change.
Verdict: keep (the load fell, or a real duplication became one named idea, and nothing got worse); revise (the idea
is right: list each fix as a finding); revert (it moves the load around or adds to it). Findings are problems only,
each naming file:line and its fix; with none, leave the list empty and say what is good in the summary."""


def claude_exe() -> str | None:
    found = shutil.which("claude")
    home = Path.home() / ".local" / "bin" / "claude.exe"
    return found or (str(home) if home.exists() else None)


def review(rev: str = "HEAD", model: str = REVIEW_MODEL, directive: str | None = None) -> dict:
    """A second agent's read of one commit (D280): shown its directive and its diff, never the metrics."""
    sha = git("rev-parse", rev)
    message = git("log", "-1", "--format=%B", sha)
    if directive:  # a commit from before directives, reviewed against one stated now
        message += "\n\nDirective: " + directive
    diff = git("diff", f"{sha}~1", sha, "--", *fe_health.TREES)
    base = {"commit": sha[:9], "model": model}
    if "Directive:" not in message:
        return {**base, "verdict": "revise", "summary": "The commit message has no directive block: say what problem "
                "this fixes and why it costs before it can be reviewed (the tidy-code skill)."}
    if len(diff) > DIFF_MAX:
        return {**base, "verdict": "revise", "summary": f"The diff is {len(diff)} characters: more than one abstraction. "
                "Split it, one idea a commit."}
    exe = claude_exe()
    if not exe:
        return {**base, "verdict": None, "summary": "claude CLI not found: no review"}
    # Project settings off: its hooks would treat the reviewer as one more session in the checkout.
    cmd = [exe, "-p", "--model", model, "--output-format", "json", "--json-schema", json.dumps(REVIEW_SCHEMA),
           "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
           "--setting-sources", "user", "--no-session-persistence"]
    r = subprocess.run(cmd, input=REVIEW_PROMPT.format(project=PROJECT, message=message, diff=diff), cwd=fe_health.ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200,
                       creationflags=fe_health.NO_WINDOW)
    try:
        out = json.loads(r.stdout)
        found = out.get("structured_output") or json.loads(out.get("result") or "{}")
    except ValueError:
        return {**base, "verdict": None, "summary": f"the reviewer gave no verdict (exit {r.returncode}): "
                f"{(r.stderr or r.stdout)[-300:]}"}
    return keep_review({**base, **found, "cost_usd": out.get("total_cost_usd")}, sha, message)


# ---- the run's report (D319) --------------------------------------------------------------

def reviews_dir() -> Path:
    import fe_board
    return fe_board.board_dir() / "manager" / "crew" / "reviews"


def patch_id(sha: str, repo: Path | None = None) -> str:
    """A commit's diff fingerprint: the same after the rebase that lands it, unlike its sha."""
    try:
        show = subprocess.run(["git", "show", sha], cwd=repo or fe_health.ROOT, capture_output=True,
                              creationflags=fe_health.NO_WINDOW, timeout=60).stdout
        out = subprocess.run(["git", "patch-id", "--stable"], input=show, cwd=repo or fe_health.ROOT,
                             capture_output=True, creationflags=fe_health.NO_WINDOW, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.decode("ascii", errors="replace").split(" ")[0].strip()


def keep_review(v: dict, sha: str, message: str) -> dict:
    """The reviewer's own words, kept for the run's report (a worker's paraphrase is not a review)."""
    task = re.search(r"^Crew:\s*T(\d+)", message, re.M)
    row = {**v, "sha": sha, "patch_id": patch_id(sha), "subject": message.split("\n")[0],
           "task": int(task.group(1)) if task else None, "at": time.time()}
    try:
        d = reviews_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{row['patch_id'] or sha}.json").write_text(json.dumps(row), encoding="utf-8")
    except OSError as e:
        print(f"{TAG} review not kept: {e}", file=sys.stderr)
    return v


def saved_review(c: dict, repo: Path | None = None) -> dict | None:
    """The kept review of a landed commit: by its diff, else the last one of the same task and subject."""
    d = reviews_dir()
    if not d.exists():
        return None
    pid = patch_id(c["sha"], repo)
    if pid and (d / f"{pid}.json").exists():
        return json.loads((d / f"{pid}.json").read_text(encoding="utf-8"))
    rows = []
    for p in d.glob("*.json"):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    same = [r for r in rows if r.get("subject") == c["subject"] and (c.get("task") is None or r.get("task") == c["task"])]
    return max(same, key=lambda r: r.get("at", 0)) if same else None


def landed_commits(repo: Path, tid: int, base: str, head: str) -> list[dict]:
    """The commits task T<tid> landed, oldest first: its `Crew: T<n>` trailer, or T<n> in the subject of a
    commit with no trailer (base..head also holds what the landing rebased onto)."""
    if not base or not head:
        return []
    mine = re.compile(rf"\bT{tid}\b")
    return [c for c in log_range(repo, base, head)
            if c["task"] == tid or (c["task"] is None and mine.search(c["subject"]))]


def log_range(repo: Path, base: str, head: str) -> list[dict]:
    try:
        out = subprocess.run(["git", "log", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", f"{base}..{head}"],
                             cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=fe_health.NO_WINDOW, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for chunk in out.split("\x1e"):
        parts = chunk.strip("\n").split("\x1f")
        if len(parts) >= 3:
            sha, subject, body = parts[:3]
            task = re.search(r"^Crew:\s*T(\d+)", body, re.M)
            rows.append(dict(sha=sha, subject=subject, body=body, task=int(task.group(1)) if task else None))
    return rows


def field(body: str, name: str) -> str:
    """One directive field of a commit message, its first paragraph only."""
    m = re.search(rf"^{name}:\s*(.+(?:\n(?!\w[\w-]*:|\n).+)*)", body, re.M)
    return " ".join(m.group(1).split()) if m else ""


def first_sentence(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def crew_kind(title: str) -> str | None:
    return next((k for k in (CODE, PROCESS) if (title or "").startswith(k + ":")), None)


def review_line(c: dict, review: dict | None) -> tuple[str, str | None]:
    """How the blind reviewer (D280) judged a commit, and its verdict."""
    trailer = VERDICT.search(c["body"])
    if review and review.get("verdict"):
        return f"{review['verdict']} ({review.get('model', 'reviewer')}): {first_sentence(review.get('summary', ''))}", review["verdict"]
    if trailer:
        return f"{trailer.group(1)} (its Crew-Review trailer; the review's own words were not kept)", trailer.group(1)
    if review:
        return f"no verdict: {first_sentence(review.get('summary', ''))} Your Accept is its only review.", None
    return "not reviewed: your Accept is its only review.", None


def report(tid: int, title: str, result: dict, commits: list[dict], reviews: dict | None = None) -> dict:
    """What a crew run did, laid out for Yotam (D319): the board message's text and Discord's short form.

    commits are landed_commits' rows; reviews maps a sha to its saved_review. The plain words are the
    worker's (`crew` in its result); with none, each commit's directive fields stand in."""
    kind = crew_kind(title) or "Crew"
    crew = result.get("crew") or {}
    said = {c.get("commit", "")[:7]: c for c in crew.get("changes") or [] if c.get("commit")}
    unmatched = [c for c in crew.get("changes") or [] if not c.get("commit") or c["commit"][:7] not in
                 {x["sha"][:7] for x in commits}]
    reviews = reviews or {}
    full = [f"{kind} T{tid}: {len(commits)} commit{'s' if len(commits) != 1 else ''} on the main branch."]
    short = []
    lead = " ".join(x for x in ((result.get("problem") or "").strip(), (result.get("bottom_line") or "").strip()) if x)
    if lead:
        full += ["", "In short", lead]
    full += ["", "What changed"] if commits or unmatched else ["", "What changed: nothing landed."]
    turned = []
    for i, c in enumerate(commits, 1):
        w = said.get(c["sha"][:7]) or {}
        what = w.get("what") or field(c["body"], "Directive") or c["subject"]
        why = w.get("why") or field(c["body"], "Problem") or first_sentence(c["body"].split("\n\n")[0], 300)
        nums = w.get("numbers") or (("expected: " + field(c["body"], "Expect")) if field(c["body"], "Expect") else "")
        rv, verdict = review_line(c, reviews.get(c["sha"]))
        full.append(f"{i}. {what}")
        if why:
            full.append(f"   Why: {why}")
        if nums:
            full.append(f"   Numbers: {nums}")
        if kind == CODE or verdict:
            full.append(f"   Reviewer: {rv}")
        full.append(f"   Commit {c['sha'][:10]} \"{c['subject']}\"")
        short.append(f"• {first_sentence(what, 160)}" + (f" ({first_sentence(nums, 120)})" if nums else "")
                     + (f" — reviewer: {verdict}" if verdict else ""))
        if verdict in ("revise", "revert"):
            turned.append(f"{c['subject']}: the reviewer said {verdict}. {first_sentence((reviews.get(c['sha']) or {}).get('summary', ''))}".strip())
    for j, w in enumerate(unmatched, len(commits) + 1):
        full.append(f"{j}. {w.get('what', '')}" + (f"\n   Why: {w['why']}" if w.get("why") else "")
                    + (f"\n   Numbers: {w['numbers']}" if w.get("numbers") else ""))
        short.append(f"• {first_sentence(w.get('what', ''), 160)}")
    turned += [f"{r.get('what', '').strip()}: {r.get('why', '').strip()}".strip(": ") for r in crew.get("rejected") or []]
    if turned:
        full += ["", "Turned down"] + [f"- {t}" for t in turned]
        short.append("Turned down: " + "; ".join(first_sentence(t, 140) for t in turned[:4]))
    elif crew.get("changes") or crew.get("rejected"):
        full += ["", "Turned down: nothing."]
    else:
        full += ["", "Turned down: not reported (the run gave no crew report; its technical record has the rest)."]
    if kind == PROCESS:
        tg, tools = reported_targets({"crew": crew, "summary": result.get("summary", "")}), \
            reported_tools({"crew": crew, "summary": result.get("summary", "")})
        if tg or tools:
            full += ["", "What it is judged by"]
            full += [f"Target: {cause} | {key}: how often agents still hit it, per 100 sessions" for cause, key in tg]
            full += [f"Tool: `{t}`: how many sessions use it" for t in tools]
            full.append("The scorecard after its first night measures both and posts them here.")
    elif kind == CODE:
        full += ["", "The scorecard after its first night measures its score, code lines, duplication and rework "
                 "on main, and posts them here."]
    checks = result.get("checks") or []
    odd = [c for c in checks if c.get("outcome") in ("failed", "passed_with_debt")]
    ran = sum(1 for c in checks if c.get("outcome") in ("passed", "passed_with_debt"))
    line = (f"Checks: {ran} passed, {len(checks) - ran} not applicable." if not odd else
            "Checks: " + "; ".join(f"{c['name']} {c['outcome']}: {c.get('detail', '')}" for c in odd))
    full += ["", line]
    return {"full": "\n".join(full), "short": "\n".join(short)}


def task_report(tid: int, repo: Path | None = None) -> str:
    """`fe_crew.py report T<n>`: a landed crew task's report, as the board shows it."""
    con = board()
    r = con.execute("SELECT t.title, m.base, m.landed_head, m.evidence FROM tasks t LEFT JOIN pm_tasks m "
                    "ON m.task_id = t.id WHERE t.id = ?", (tid,)).fetchone()
    if not r:
        return f"{TAG} no task T{tid}"
    try:
        ev = json.loads(r["evidence"] or "{}")
    except ValueError:
        ev = {}
    repo = repo or fe_health.ROOT
    commits = landed_commits(repo, tid, r["base"], r["landed_head"])
    return report(tid, r["title"], ev, commits, {c["sha"]: saved_review(c, repo) for c in commits})["full"]


def print_review(v: dict) -> None:
    print(f"{TAG} review {v['commit']} ({v['model']}): {v.get('verdict') or 'none'}"
          + (f" | load {v['load']}, names {v['names']}, responsibilities {v['responsibilities']}, call sites "
             f"{v['call_sites']}, indirection {v['indirection']}, fixes its directive {v['fixes_directive']}"
             if "load" in v else ""))
    print(f"  {v.get('summary', '')}")
    for f in v.get("findings") or []:
        print(f"  - {f['where']}: {f['problem']} -> {f['fix']}")
    if v.get("verdict"):
        print(f"  trailer: Crew-Review: {v['verdict']}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fe_crew.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("score", "bodies"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path)
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("review")
    p.add_argument("rev", nargs="?", default="HEAD")
    p.add_argument("--model", default=REVIEW_MODEL)
    p.add_argument("--directive", help="for a commit made without one: the problem it set out to fix")
    p.add_argument("--json", action="store_true")
    p.add_argument("--repo", type=Path)
    p = sub.add_parser("report")
    p.add_argument("task", help="T<n>: a landed crew task")
    p.add_argument("--repo", type=Path)
    args = ap.parse_args(argv)
    if args.repo:
        point_at(args.repo)
    if args.cmd == "report":
        print(task_report(int(args.task.lstrip("Tt")), args.repo))
        return 0
    if args.cmd == "review":
        v = review(args.rev, args.model, args.directive)
        print(json.dumps(v, indent=1)) if args.json else print_review(v)
        return 0 if v.get("verdict") == "keep" else 1
    s = score()
    if args.cmd == "score":
        print(json.dumps(s, indent=1, default=str) if args.json else summary(s))
    else:
        print(code_body(s) + "\n\n----\n\n" + process_body(s))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
