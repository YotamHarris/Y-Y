#!/usr/bin/env python3
"""Where the agents' tokens and time went, and what a process could do instead (D253).

    fe_usage.py night                       last night: yesterday 18:00 to today 10:00
    fe_usage.py night --since 2026-10-01T18:00 --until 2026-10-02T10:00
    fe_usage.py night --json                the same, for the manager's morning report
    fe_usage.py night --top 30              longer tables
    fe_usage.py smells [--days 7] [--json]  repeated steps and failures, throwaway scripts, files
                                            born and buried, rereads, the docs' fixed cost (the
                                            process crew's input)
    fe_usage.py trend [--days 30] [--json]  tokens per changed code line, day by day, kept in
                                            <board>/manager/metrics/DAY.json (its measure)

The trend judges cache-read plus output tokens (every Claude transcript, and
the meter's Codex runs) per code line changed on origin/main (fe_loc.py's
rule: added plus deleted, comments and blanks not counted); the 7-day column
is the one to read, since a day can land little.

Two sources, joined through `pm_runs.session` (= a transcript's file name):

- the manager's meter, `pm_run_stats` in the board store: per run its model,
  tokens, cost and time (working, tools, GPU wait, frozen), by the run's start;
- the Claude Code transcripts, `~/.claude/projects/*BodySimulation*/*.jsonl`
  (fe_tokens.sessions): every API request's usage counted once per
  `requestId` (one response is split over several records), every tool call's
  result size and how long it was open, by the record's own UTC timestamp.

The cost of a run is its turns times its context: each turn re-reads the
whole conversation from the cache. So a tool result is charged not only its
size but its size times the turns after it ("carried": what it cost to keep
it), and the first turn's context is the fixed price every turn pays.
Codex sessions outside the manager are not read (another transcript format);
managed Codex runs are in the meter.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fe_tokens  # noqa: E402  (the transcripts' finder)
import studio_config  # noqa: E402  ([usage] in studio.toml: the game's tools, ports and paths)

TAG = "[fe-usage]"
ROOT = studio_config.repo_root()  # the repository; the manager's release points it at the checkout
EDITS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
SHELLS = ("Bash", "PowerShell")
# A tool's script name: the studio's prefixes and the game's ([usage] tool_prefixes).
PREFIXES = "|".join(["fe", "manager", *studio_config.get("usage.tool_prefixes", [])])
TOOL_SCRIPT = re.compile(rf"\b((?:{PREFIXES})_[a-z_]+|test-[a-z0-9-]+|test_[a-z_]+)\.py\b[\"']?\s*([a-z][a-z-]*)?")


def _extra(key: str) -> str:
    """`[usage] key`'s regexes, each as one more alternative ('' when the game has none)."""
    return "".join("|" + x for x in studio_config.get(f"usage.{key}", []))


def _dirs(paths) -> str:
    """Repo-relative folders as one alternation matching either slash."""
    return "|".join(dict.fromkeys(re.escape(p.strip("/")).replace("/", r"[\\/]") for p in paths))
_VAL = r"(?:\"[^\"]*\"|'[^']*'|\S*)"
LEADING = re.compile(  # what a command does before its step: cd, VAR=..., $env:X = ...
    rf"^\s*(?:(?:cd|Set-Location|pushd)\s+{_VAL}\s*(?:;|&&)\s*|[A-Za-z_]\w*={_VAL}\s*(?:;|&&)?\s*"
    rf"|\$env:\w+\s*=\s*{_VAL}\s*;\s*|export\s+\w+={_VAL}\s*(?:;|&&)\s*)+")
POLL = re.compile(r"\b(until|while)\b.*\b(sleep|Start-Sleep)\b|\bfor\b.*\b(sleep|Start-Sleep)\b"
                  r"|^\s*(sleep|Start-Sleep)\b" + _extra("poll"), re.S)  # the game's own waits ([usage] poll)
SOURCE = re.compile(r"(?:sed -n\s+\S+|\bcat|\bhead\b[^|;]*?|\btail\b[^|;]*?|Get-Content)\s+[\"']?([\w./\\:-]+\.(?:"
                    + "|".join([*fe_tokens.CODE_EXT, "py", "md", "toml", "js", "json", "ps1"]) + "))")


def command_key(cmd: str) -> str:
    """A shell command as the step it is: `fe_build build`, `git diff`, `poll`, `sed`."""
    if POLL.search(cmd):
        return "poll/wait loop"
    body = LEADING.sub("", cmd.strip())
    m = TOOL_SCRIPT.search(body)
    if m:
        return f"{m.group(1)} {m.group(2) or ''}".strip()
    m = re.match(r"git\s+(\S+)", body)
    if m:
        return f"git {m.group(1)}"
    m = re.match(r"(?:python|py)\s+-\s*<<|python\s+-c\b|^@'", body)
    if m:
        return "inline python"
    word = re.split(r"[\s|;]+", body, 1)[0] if body else ""
    return word[:24] or "?"


def tool_key(name: str, inp: dict) -> str:
    if name in SHELLS:
        return "sh: " + command_key(inp.get("command") or "")
    if name == "Read":
        return "Read " + ("ranged" if inp.get("offset") or inp.get("limit") else "whole file")
    if name == "Skill":
        return f"Skill {inp.get('skill')}"
    return name


def result_chars(item: dict) -> int:
    c = item.get("content")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len(x.get("text") or "") for x in c if isinstance(x, dict))
    return 0


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


EXIT_LINE = re.compile(r"^(?:<[a-z_]+>)*\s*(?:Exit code -?\d+|Error:?)?\s*$", re.I)


def first_error(item: dict) -> str:
    """A failed tool result's text (600 characters), its telling line first, without `Exit code N` and markup."""
    c = item.get("content")
    text = c if isinstance(c, str) else "\n".join(x.get("text") or "" for x in c or [] if isinstance(x, dict))
    lines = [ln for ln in (re.sub(r"</?[a-z_]+>", "", x).strip() for x in text.splitlines()) if ln and not EXIT_LINE.match(ln)]
    return "\n".join(lines)[:600] or "(no message)"


def headline(err: str) -> str:
    return err.split("\n", 1)[0][:160]


def read_session(paths: list[Path], lo: str, hi: str) -> dict | None:
    """One session's requests and tool calls inside [lo, hi] (UTC ISO strings)."""
    reqs: dict[str, tuple[str, dict, str]] = {}
    pend: dict[str, tuple[str, str, dict]] = {}
    calls: list[dict] = []
    prompt, edited_at, worker = "", None, False
    for path in paths:
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = r.get("timestamp") or ""
                if not (lo <= t <= hi):
                    continue
                m = r.get("message") if isinstance(r.get("message"), dict) else {}
                if r.get("type") == "assistant":
                    if m.get("model") == "<synthetic>":
                        continue
                    rid = r.get("requestId") or r.get("uuid")
                    if isinstance(m.get("usage"), dict):
                        reqs[rid] = (t, m["usage"], m.get("model") or "")
                    for it in m.get("content") or []:
                        if isinstance(it, dict) and it.get("type") == "tool_use":
                            pend[it.get("id")] = (it.get("name") or "?", t, it.get("input") or {})
                            if it.get("name") in EDITS and edited_at is None:
                                edited_at = t
                elif r.get("type") == "user":
                    c = m.get("content")
                    if isinstance(c, str):
                        if not prompt:
                            prompt = c
                        worker = worker or "background manager (D181)" in c
                    elif isinstance(c, list):
                        for it in c:
                            if isinstance(it, dict) and it.get("type") == "tool_result" and it.get("tool_use_id") in pend:
                                name, t0, inp = pend.pop(it["tool_use_id"])
                                calls.append({"name": name, "input": inp, "at": t0, "chars": result_chars(it),
                                              "secs": max(0.0, (ts(t) - ts(t0)).total_seconds()),
                                              "error": first_error(it) if it.get("is_error") else None})
    if not reqs:
        return None
    order = sorted(reqs.values(), key=lambda v: v[0])
    ctx = [sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
           for _, u, _ in order]
    stamps = [v[0] for v in order]
    for c in calls:
        c["later"] = len(stamps) - bisect.bisect_right(stamps, c["at"])
    tot = lambda k: sum(int(u.get(k) or 0) for _, u, _ in order)  # noqa: E731
    return {"session": paths[0].stem, "project": paths[0].parent.name, "worker": worker,
            "prompt": prompt[:160], "requests": len(order), "first_ctx": ctx[0], "last_ctx": ctx[-1],
            "cache_read": tot("cache_read_input_tokens"), "cache_write": tot("cache_creation_input_tokens"),
            "input": tot("input_tokens"), "output": tot("output_tokens"),
            "models": sorted({v[2] for v in order if v[2]}), "start": stamps[0], "end": stamps[-1],
            "calls": calls, "first_edit": edited_at, "turn_at": stamps, "turn_ctx": ctx,
            "total": sum(ctx) + tot("output_tokens")}


def board_runs(since: float, until: float) -> tuple[list[dict], dict[str, tuple]]:
    """pm_run_stats rows started in the window, and session -> (task, role)."""
    try:
        import fe_board
        path = fe_board.db_path()
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except Exception as ex:  # noqa: BLE001 - the transcripts still answer
        print(f"{TAG} board store unreadable ({ex}); transcripts only", file=sys.stderr)
        return [], {}
    titles = {str(r["id"]): r["title"] for r in con.execute("SELECT id, title FROM tasks")}
    runs = [dict(r) | {"title": titles.get(str(r["task_id"]), "")} for r in con.execute(
        "SELECT * FROM pm_run_stats WHERE COALESCE(started, queued) BETWEEN ? AND ?", (since, until))]
    sess = {r["session"]: (r["task_id"], r["role"]) for r in con.execute(
        "SELECT session, task_id, role FROM pm_runs WHERE session IS NOT NULL")}
    return runs, sess


def window(args) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone()
    if args.since:
        lo = datetime.fromisoformat(args.since).astimezone()
        hi = datetime.fromisoformat(args.until).astimezone() if args.until else now
        return lo, hi
    today10 = now.replace(hour=10, minute=0, second=0, microsecond=0)
    end = today10 if now >= today10 else now
    start = (today10 - timedelta(days=1)).replace(hour=18)
    return start, end


def utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def recent(paths: list[Path], lo: datetime) -> bool:
    """Whether any of a session's transcripts was written since `lo` (the rest are skipped unread)."""
    try:
        return max(p.stat().st_mtime for p in paths) >= lo.timestamp()
    except (OSError, ValueError):
        return False


def load(lo: datetime, hi: datetime) -> tuple[list[dict], list[dict]]:
    """The meter's runs and the transcripts' sessions inside [lo, hi]."""
    runs, sess2task = board_runs(lo.timestamp(), hi.timestamp())
    sessions = []
    for first, paths in fe_tokens.sessions(all_projects=False):
        if first > utc(hi) + "~" or not recent(paths, lo):
            continue
        s = read_session(paths, utc(lo), utc(hi) + "~")
        if s:
            s["task"], s["role"] = sess2task.get(s["session"], (None, ""))
            sessions.append(s)
    return runs, sessions


def analyse(lo: datetime, hi: datetime, top: int, loaded=None) -> dict:
    runs, sessions = loaded or load(lo, hi)

    # Spend per task, from the meter.
    tasks: dict = defaultdict(lambda: defaultdict(float))
    for r in runs:
        t = tasks[(r["task_id"], r["role"])]
        t["runs"] += 1
        for k in ("output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "turns",
                  "model_s", "tool_s", "gpu_wait_s", "gpu_s", "frozen_s"):
            t[k] += r[k] or 0
        t["wall_s"] += max(0.0, (r["ended"] or r["started"] or 0) - (r["started"] or 0))
        t.setdefault("models", set()).add(r["model"] or "?")
        t["title"] = r["title"]
    spend = sorted(({"task": k[0], "role": k[1], **{a: (sorted(b) if isinstance(b, set) else b) for a, b in v.items()}}
                    for k, v in tasks.items()), key=lambda d: -d["cost_usd"])

    # Tool calls: count, size, time, and what keeping their output cost.
    steps: dict = defaultdict(lambda: {"calls": 0, "chars": 0, "secs": 0.0, "carried": 0, "sessions": set()})
    files: dict = defaultdict(lambda: {"reads": 0, "chars": 0, "sessions": set()})
    openings, before_edit = defaultdict(int), []
    for s in sessions:
        seen_open = set()
        n_open = 0
        for c in s["calls"]:
            key = tool_key(c["name"], c["input"])
            st = steps[key]
            st["calls"] += 1; st["chars"] += c["chars"]; st["secs"] += c["secs"]
            st["carried"] += c["chars"] * c["later"]; st["sessions"].add(s["session"])
            src = None
            if c["name"] == "Read":
                src = c["input"].get("file_path")
            elif c["name"] in SHELLS:
                m = SOURCE.search(c["input"].get("command") or "")
                src = m.group(1) if m else None
            if src:
                p = Path(src.replace("\\", "/"))
                f = files[f"{p.parent.name}/{p.name}" if p.name == "SKILL.md" else p.name]
                f["reads"] += 1; f["chars"] += c["chars"]; f["sessions"].add(s["session"])
            if s["worker"] and (s["first_edit"] is None or c["at"] < s["first_edit"]):
                n_open += 1
                if key not in seen_open:
                    seen_open.add(key); openings[key] += 1
        if s["worker"]:
            before_edit.append(n_open)
    workers = sum(1 for s in sessions if s["worker"])
    fin = lambda d: {k: (len(v) if isinstance(v, set) else v) for k, v in d.items()}  # noqa: E731
    step_rows = sorted(({"step": k, **fin(v)} for k, v in steps.items()), key=lambda d: -d["carried"])
    return {
        "window": [lo.isoformat(timespec="minutes"), hi.isoformat(timespec="minutes")],
        "meter": {"runs": len(runs), "tasks": len({r["task_id"] for r in runs}),
                  **{k: sum(r[k] or 0 for r in runs) for k in (
                      "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "turns", "gpu_wait_s")}},
        "spend": spend[:top],
        "sessions": sorted(({k: v for k, v in s.items() if k not in ("calls", "turn_at", "turn_ctx")} for s in sessions),
                           key=lambda s: -s["cache_read"])[:top],
        "base_ctx_median": statistics.median([s["first_ctx"] for s in sessions if s["worker"]] or [0]),
        "resumed_big": [{"session": s["session"][:8], "task": s["task"], "first_ctx": s["first_ctx"]}
                        for s in sessions if s["first_ctx"] > 200_000],
        "steps_by_carried": step_rows[:top],
        "steps_by_time": sorted(step_rows, key=lambda d: -d["secs"])[:top],
        "opening": {"workers": workers, "calls_before_first_edit_median": statistics.median(before_edit or [0]),
                    "common": sorted(({"step": k, "workers": n} for k, n in openings.items() if n * 2 >= workers),
                                     key=lambda d: -d["workers"])},
        "files": sorted(({"file": k, **fin(v)} for k, v in files.items()), key=lambda d: (-d["sessions"], -d["reads"]))[:top],
        "polling_min": sum(v["secs"] for k, v in steps.items() if "poll" in k or k == "TaskOutput") / 60,
    }


def k(n) -> str:
    n = float(n or 0)
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else f"{n:.0f}"


def report(a: dict) -> str:
    m = a["meter"]
    out = [f"{TAG} {a['window'][0]} .. {a['window'][1]}",
           f"meter: {m['runs']} runs over {m['tasks']} tasks | ${m['cost_usd']:.0f} | out {k(m['output_tokens'])} | "
           f"cache read {k(m['cache_read_tokens'])} | cache write {k(m['cache_write_tokens'])} | "
           f"{m['turns']:.0f} turns | GPU wait {m['gpu_wait_s'] / 60:.0f} min",
           f"base context (a worker's first turn): median {k(a['base_ctx_median'])} tokens, paid again on every turn",
           "", "spend by task (meter)          $   runs  out    cache-read  wall  tools  gpu-wait (min)  model"]
    for t in a["spend"]:
        out.append(f"  T{t['task'] or '-':<4} {t['role']:<8} {t['cost_usd']:6.0f} {t['runs']:4.0f}  {k(t['output_tokens']):>6} "
                   f"{k(t['cache_read_tokens']):>8}  {t['wall_s'] / 60:5.0f} {t['tool_s'] / 60:5.0f} {t['gpu_wait_s'] / 60:5.0f}"
                   f"   {','.join(t['models'])[:24]}  {t.get('title', '')[:50]}")
    out += ["", "sessions (transcripts)  task  turns  first-ctx  last-ctx  cache-read  out"]
    for s in a["sessions"]:
        out.append(f"  {s['session'][:8]}  {('T' + str(s['task'])) if s['task'] else '-':>5} {s['requests']:6}  "
                   f"{k(s['first_ctx']):>8}  {k(s['last_ctx']):>8}  {k(s['cache_read']):>9}  {k(s['output']):>6}  {s['project'][-28:]}")
    if a["resumed_big"]:
        out.append("  resumed past 200k: " + ", ".join(f"{r['session']} (T{r['task']}, {k(r['first_ctx'])})" for r in a["resumed_big"]))
    out += ["", "steps by what keeping their output cost (carried = result tokens x later turns)",
            "  step                                calls  result  carried  minutes  sessions"]
    for r in a["steps_by_carried"]:
        out.append(f"  {r['step'][:36]:36} {r['calls']:5} {k(r['chars'] / 4):>7} {k(r['carried'] / 4):>8} {r['secs'] / 60:8.1f} {r['sessions']:5}")
    out += ["", "steps by time open", "  step                                calls  minutes  avg s"]
    for r in a["steps_by_time"]:
        out.append(f"  {r['step'][:36]:36} {r['calls']:5} {r['secs'] / 60:8.1f} {r['secs'] / max(r['calls'], 1):6.0f}")
    o = a["opening"]
    out += ["", f"opening: {o['workers']} workers, median {o['calls_before_first_edit_median']:.0f} calls before the first edit; "
                f"in at least half of them: " + ", ".join(f"{c['step']} ({c['workers']})" for c in o["common"])]
    out += [f"polling and waiting: {a['polling_min']:.0f} min", "", "files read again and again   reads  result  sessions"]
    for f in a["files"]:
        out.append(f"  {f['file'][:28]:28} {f['reads']:5} {k(f['chars'] / 4):>7} {f['sessions']:5}")
    return "\n".join(out)


def summary(a: dict, n: int = 5) -> str:
    """The night in a Discord message (under 2000 characters): the manager's morning report."""
    m, o = a["meter"], a["opening"]
    lines = [f"**Last night's agents** ({a['window'][0][11:16]}–{a['window'][1][11:16]}): ${m['cost_usd']:.0f} over "
             f"{m['runs']} runs and {m['tasks']} tasks; cache read {k(m['cache_read_tokens'])}, output {k(m['output_tokens'])}.",
             f"Every worker turn starts at ~{k(a['base_ctx_median'])} tokens; median {o['calls_before_first_edit_median']:.0f} "
             f"calls before the first edit; {a['polling_min']:.0f} min polling or waiting.",
             "Most spent: " + "; ".join(f"T{t['task']} ${t['cost_usd']:.0f}" for t in a["spend"][:n] if t["task"]),
             "Kept in context longest: " + "; ".join(f"{r['step']} {k(r['carried'] / 4)}" for r in a["steps_by_carried"][:n]),
             "Longest open: " + "; ".join(f"{r['step']} {r['secs'] / 60:.0f} min" for r in a["steps_by_time"][:n])]
    if a["resumed_big"]:
        lines.append("Resumed past 200k: " + ", ".join(f"T{r['task']} ({k(r['first_ctx'])})" for r in a["resumed_big"]))
    lines.append(f"`{studio_config.tool_cmd('fe_usage.py')} night` has the rest.")
    return "\n".join(lines)[:1990]


# ---- smells: what the process crew works from -------------------------------------------

NOISE = [(re.compile(r"[A-Za-z]:[\\/][^\s\"'`;|]+|(?<![\w.])/(?:c|d|tmp|home|Users|mnt)/[^\s\"'`;|]+", re.I), "<path>"),
         (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"), "<sha>"),
         (re.compile(r"\b\d+(?:\.\d+)?\b"), "N")]
SCRIPT_EXT = (".py", ".ps1", ".sh", ".js", ".mjs", ".bat", ".cmd")
# A script written under these is kept work, not throwaway: the studio's folders and the game's ([usage] kept).
KEPT = re.compile(r"[\\/](?:" + _dirs([studio_config.studio_rel(), studio_config.rel(studio_config.game_tools_dir()),
                                        ".agents", ".claude/hooks", ".claude/skills", "docs",
                                        *studio_config.get("usage.kept", [])]) + r")[\\/]", re.I)


def _ports() -> str:
    """The agents' control ports (the registry) and the game's others ([usage] extra_ports), as one group."""
    try:
        reg = json.loads(studio_config.agents_path().read_text(encoding="utf-8")).get("agents", {})
    except (OSError, ValueError):
        reg = {}
    ports = [str(a["port"]) for a in reg.values() if a.get("port")] + [str(p) for p in studio_config.get("usage.extra_ports", [])]
    return "(?:" + "|".join(dict.fromkeys(ports)) + ")" if ports else "(?!)"


USES = {  # what a throwaway script does, by what it touches: the tool it is missing
    "shot or image": r"\bPIL\b|Image\.open|\.png\b",
    "board store": r"sqlite3|board\.db|fe_board",
    "transcripts": r"\.jsonl\b|requestId|cache_read",
    "git history": r"\bgit\b.*\b(?:log|show|diff|blame)\b",
    "edit a file": r"\.write_text\(|\.replace\(.*\)\s*$|re\.sub\(",
}
for _k, _v in studio_config.get("usage.uses", {}).items():  # the game's: a new purpose, or more of one; {ports}
    _v = _v.replace("{ports}", _ports())
    USES[_k] = f"{USES[_k]}|{_v}" if _k in USES else _v
USES_RX = {k: re.compile(v, re.I | re.M) for k, v in USES.items()}


def shape(cmd: str) -> str:
    """A command with its paths, hashes and numbers taken out: the step it repeats."""
    body = LEADING.sub("", cmd.strip())
    lines = body.splitlines() or [""]
    body = lines[0] + (" …" if len(lines) > 1 else "")
    for rx, rep in NOISE:
        body = rx.sub(rep, body)
    return re.sub(r"\s+", " ", body).strip()[:140]


def uses(text: str) -> list[str]:
    tools = set(re.findall(r"\b((?:fe|manager)_[a-z_]+)\.py\b", text))
    return sorted({k for k, rx in USES_RX.items() if rx.search(text)} | tools)


def fingerprint(text: str) -> str:
    import hashlib
    body = "\n".join(s for s in (ln.split("#", 1)[0].strip() for ln in text.splitlines()) if s)
    for rx, rep in NOISE:
        body = rx.sub(rep, body)
    return hashlib.sha1(body.encode()).hexdigest()[:10]


def scripts_of(s: dict) -> list[dict]:
    """The throwaway scripts one session wrote (a file outside the kept tree) or piped in."""
    out = []
    shells = [c for c in s["calls"] if c["name"] in SHELLS]
    for c in s["calls"]:
        inp = c["input"]
        if c["name"] == "Write":
            path = str(inp.get("file_path") or "")
            if not path.lower().endswith(SCRIPT_EXT) or KEPT.search(path):
                continue
            name = Path(path.replace("\\", "/")).name
            text = str(inp.get("content") or "")
            ran = sum(1 for x in shells if x["at"] >= c["at"] and name in (x["input"].get("command") or ""))
            out.append({"name": name, "path": path, "at": c["at"], "lines": text.count("\n") + 1,
                        "fp": fingerprint(text), "uses": uses(text), "ran": ran})
        elif c["name"] in SHELLS and command_key(inp.get("command") or "") == "inline python":
            text = inp.get("command") or ""
            if text.count("\n") < 4:
                continue  # a one-liner is a command, not a script
            out.append({"name": "<inline python>", "path": "", "at": c["at"], "lines": text.count("\n") + 1,
                        "fp": fingerprint(text), "uses": uses(text), "ran": 1})
    return out


def born_and_buried(days: int = 60, within: int = 14) -> list[dict]:
    """Tracked files added and deleted again within `within` days: a script kept for one job."""
    import subprocess
    root = ROOT
    try:
        raw = subprocess.run(["git", "log", f"--since={days}.days", "--reverse", "-M",
                              "--diff-filter=ADR", "--name-status", "--format=\x01%H\t%cI\t%s"],
                             cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    except OSError:
        return []
    added: dict = {}
    out = []
    sha = when = subject = ""
    for line in raw.splitlines():
        if line.startswith("\x01"):
            sha, when, subject = (line[1:].split("\t", 2) + ["", ""])[:3]
            continue
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):  # a move keeps the file alive
            if parts[1] in added:
                added[parts[2]] = added.pop(parts[1])
            continue
        if len(parts) != 2:
            continue
        st, path = parts
        if st == "A":
            added[path] = (sha, when, subject)
        elif st == "D" and path in added:
            a_sha, a_when, a_subj = added.pop(path)
            life = (datetime.fromisoformat(when) - datetime.fromisoformat(a_when)).total_seconds() / 86400
            if life <= within:
                out.append({"path": path, "days": round(life, 1), "added": a_sha[:9], "added_in": a_subj[:70],
                            "deleted": sha[:9], "deleted_in": subject[:70]})
    return out


def smells(lo: datetime, hi: datetime, top: int, min_sessions: int = 3) -> dict:
    """Repeated steps, repeated failures, throwaway scripts, short-lived files, rereads, doc cost."""
    runs, sessions = loaded = load(lo, hi)
    a = analyse(lo, hi, top, loaded)

    steps: dict = defaultdict(lambda: {"calls": 0, "secs": [], "carried": 0, "sessions": set(), "tasks": set()})
    fails: dict = defaultdict(lambda: {"count": 0, "sessions": set(), "tasks": set(), "lost_s": 0.0, "example": ""})
    writes: list[dict] = []
    for s in sessions:
        calls = s["calls"]
        for i, c in enumerate(calls):
            if c["name"] in SHELLS:
                sh = shape(c["input"].get("command") or "")
                st = steps[sh]
                st["calls"] += 1; st["secs"].append(c["secs"]); st["carried"] += c["chars"] * c["later"] // 4
                st["sessions"].add(s["session"]); st["tasks"].add(s["task"])
            if c["error"]:
                key = tool_key(c["name"], c["input"])
                err = headline(c["error"])
                for rx, rep in NOISE:
                    err = rx.sub(rep, err)
                f = fails[(key, err[:120])]
                f["count"] += 1; f["sessions"].add(s["session"]); f["tasks"].add(s["task"])
                f["example"] = f["example"] or (shape(c["input"].get("command") or "") if c["name"] in SHELLS
                                                else str(c["input"].get("file_path") or c["input"].get("pattern") or ""))[:140]
                ok = next((x for x in calls[i + 1:] if tool_key(x["name"], x["input"]) == key and not x["error"]), None)
                f["lost_s"] += (ts(ok["at"]) - ts(c["at"])).total_seconds() if ok else c["secs"]
        for w in scripts_of(s):
            writes.append(dict(w, session=s["session"][:8], task=s["task"]))

    def tasks(v):
        return sorted(f"T{t}" for t in v if t)
    repeated = sorted(({"step": k, "calls": v["calls"], "sessions": len(v["sessions"]), "tasks": tasks(v["tasks"]),
                        "minutes": round(sum(v["secs"]) / 60, 1), "median_s": round(statistics.median(v["secs"]), 1),
                        "carried_tokens": v["carried"]}
                       for k, v in steps.items() if len(v["sessions"]) >= min_sessions),
                      key=lambda d: -(d["minutes"] * 60 + d["carried_tokens"] / 1000))
    failures = sorted(({"tool": k[0], "error": k[1], "count": v["count"], "sessions": len(v["sessions"]),
                        "tasks": tasks(v["tasks"]), "lost_minutes": round(v["lost_s"] / 60, 1), "example": v["example"]}
                       for k, v in fails.items() if v["count"] >= 2),
                      key=lambda d: (-d["sessions"], -d["lost_minutes"]))

    def group(key):
        g: dict = defaultdict(list)
        for w in writes:
            g[key(w)].append(w)
        rows = []
        for k_, ws in g.items():
            sess = {w["session"] for w in ws}
            if len(sess) < 2:
                continue
            rows.append({"key": k_, "sessions": len(sess), "writes": len(ws), "ran": sum(w["ran"] for w in ws),
                         "lines": sum(w["lines"] for w in ws), "tasks": tasks({w["task"] for w in ws}),
                         "names": sorted({w["name"] for w in ws})[:6],
                         "uses": sorted({u for w in ws for u in w["uses"]})})
        return sorted(rows, key=lambda d: (-d["sessions"], -d["writes"]))
    scripts = {"written": len(writes), "sessions": len({w["session"] for w in writes}),
               "by_name": group(lambda w: w["name"].lower())[:top],
               "by_purpose": group(lambda w: ", ".join(w["uses"]) or "(nothing recognised)")[:top],
               "same_content": group(lambda w: w["fp"])[:top]}

    root = ROOT
    fixed = sum((root / f).stat().st_size for f in ("AGENTS.md", "CLAUDE.md") if (root / f).exists()) // 4
    requests = sum(s["requests"] for s in sessions)
    docs = [f for f in a["files"] if f["file"].endswith(".md")]
    return {"window": a["window"], "sessions": len(sessions), "workers": a["opening"]["workers"],
            "repeated_steps": repeated[:top], "failures": failures[:top], "scripts": scripts,
            "born_and_buried": born_and_buried(), "rereads": a["files"], "polling_min": a["polling_min"],
            "doc_cost": {"agents_md_tokens": fixed, "requests": requests, "carried_tokens": fixed * requests,
                         "md_rereads": docs},
            "opening": a["opening"]}


def smells_report(m: dict, top: int) -> str:
    out = [f"{TAG} smells {m['window'][0]} .. {m['window'][1]}: {m['sessions']} sessions ({m['workers']} managed)", "",
           "repeated steps (same command shape in 3+ sessions)   calls sess  minutes  median-s  carried"]
    for r in m["repeated_steps"][:top]:
        out.append(f"  {r['step'][:50]:50} {r['calls']:5} {r['sessions']:4} {r['minutes']:8.1f} {r['median_s']:8.1f} {k(r['carried_tokens']):>8}")
    out += ["", "repeated failures                                     count sess  lost-min"]
    for r in m["failures"][:top]:
        out.append(f"  {r['tool'][:24]:24} {r['error'][:60]:60} {r['count']:4} {r['sessions']:4} {r['lost_minutes']:7.1f}")
        if r["example"]:
            out.append(f"      e.g. {r['example'][:100]}")
    sc = m["scripts"]
    out += ["", f"throwaway scripts: {sc['written']} written in {sc['sessions']} sessions; written again in 2+ sessions:"]
    for title, rows in (("by name", sc["by_name"]), ("by what they touch", sc["by_purpose"]), ("same content", sc["same_content"])):
        if rows:
            out.append(f"  {title}:")
        for r in rows[:top]:
            out.append(f"    {r['key'][:40]:40} {r['sessions']:3} sessions {r['writes']:3} writes {r['lines']:5} lines  "
                       f"{' '.join(r['tasks'][:5])}  [{', '.join(r['uses'])[:60]}]")
    if m["born_and_buried"]:
        out += ["", "added and deleted within 14 days (git)"]
        for r in m["born_and_buried"][:top]:
            out.append(f"  {r['path'][:60]:60} {r['days']:5.1f} d  added in {r['added_in'][:50]}")
    d = m["doc_cost"]
    out += ["", f"AGENTS.md + CLAUDE.md: {k(d['agents_md_tokens'])} tokens in every request; "
                f"{d['requests']} requests carried {k(d['carried_tokens'])}",
            "  md read again: " + ", ".join(f"{f['file']} {f['reads']}x/{f['sessions']}s" for f in d["md_rereads"][:8]),
            f"polling and waiting: {m['polling_min']:.0f} min; median {m['opening']['calls_before_first_edit_median']:.0f} "
            "calls before a worker's first edit"]
    return "\n".join(out)


# ---- detours: the times an agent worked around the project instead of doing its task ---------

CAUSES = ("place", "tool", "silent", "environment", "workaround")
PLACE_TOOLS = {"mcp__ccd_directory__change_directory", "mcp__ccd_directory__request_directory", "EnterWorktree"}
PLACE_CMD = re.compile(r"^\s*(?:pwd|Get-Location|gl|popd|(?:cd|Set-Location|pushd|sl)(?:\s+\S+)?)\s*$", re.I)
PLACE_ERR = re.compile(r"hook error.*fe_sync\.py|change_directory|working directory|cwd\b", re.I | re.S)
ENV_ERR = re.compile(r"Permission to use \w+ has been denied|permission denied|Access is denied|requires approval"
                     r"|sandbox|here-?doc|unexpected EOF|syntax error near|ParserError|unterminated|"
                     r"is not recognized as|command not found|address already in use|Only one usage of each socket"
                     r"|err frozen|bench is running|InputValidationError|timed out|Blocked: (?:sleep|Start-Sleep)"
                     r"|NativeCommandError|RemoteException", re.I)  # PowerShell 5.1 wraps a native stderr line as an error
TOOL_ERR = re.compile(r"hook error|Blocked:|refused|no such symbol|unknown (?:command|verb|option|subcommand)"
                      r"|unrecognized arguments|invalid choice|(?:"
                      + _dirs([studio_config.studio_rel(), studio_config.rel(studio_config.game_tools_dir())])
                      + r")[\\/]\w+\.py\", line|^\[fe-[a-z]+\]", re.I | re.M)
SLIP_ERR = re.compile(r"String to replace not found|File does not exist|has not been read yet|modified since read"
                      r"|Found \d+ matches|old_string", re.I)
PROJECT_TOOL = re.compile(r"\b(?:fe|manager)_[a-z_]+\.py\b" + _extra("project_tool"))
# A tool the task itself runs (its build, tests, landing): its errors are the work, not a detour.
TASK_TOOL = re.compile(r"\bfe_land\.py\b|\bunittest\b|\bpytest\b" + _extra("task_tool"))
SPAN_CAP = 12
# A call answered after this long was waiting on a person (a folder grant, a permission prompt
# left overnight), not working: a detour counts no more of it than this.
WAIT_CAP = 600


def ctx_at(s: dict, at: str) -> int:
    """The context the turn that made a call read: what one more call costs."""
    i = bisect.bisect_right(s["turn_at"], at) - 1
    return s["turn_ctx"][max(i, 0)]


def trigger(calls: list[dict], i: int, usual: dict) -> str | None:
    """The cause of a detour starting at calls[i], 'slip' for its own mistake, or None."""
    c = calls[i]
    name, inp, err = c["name"], c["input"], c["error"]
    cmd = LEADING.sub("", (inp.get("command") or "").strip()) if name in SHELLS else ""
    if name in PLACE_TOOLS or (cmd and PLACE_CMD.match(cmd)):
        return "place"
    if err:
        if SLIP_ERR.search(err):
            return "slip"
        if PLACE_ERR.search(err):
            return "place"
        if ENV_ERR.search(err):
            return "environment"
        if TOOL_ERR.search(err) and not TASK_TOOL.search(cmd):
            return "tool"
        return None  # the task's own work failing: a compile error, a failing test, a gate
    key = tool_key(name, inp)
    if name in SHELLS and PROJECT_TOOL.search(cmd) and c["chars"] <= 40 and usual.get(key, 0) >= 400:
        nxt = calls[i + 1:i + 4]
        if any(tool_key(x["name"], x["input"]) == key or x["name"] in SHELLS and
               shape(x["input"].get("command") or "") == shape(inp.get("command") or "") for x in nxt):
            return "silent"  # a call that usually answers came back empty, and was run again
    return None


def span_of(calls: list[dict], i: int) -> list[int]:
    """The calls a detour took: up to the next success of the same intent, or the failures right after."""
    key = tool_key(calls[i]["name"], calls[i]["input"])
    for j in range(i + 1, min(len(calls), i + 1 + SPAN_CAP)):
        if tool_key(calls[j]["name"], calls[j]["input"]) == key and not calls[j]["error"]:
            return list(range(i, j))
    out = [i]
    for j in range(i + 1, min(len(calls), i + 1 + SPAN_CAP)):
        if not calls[j]["error"]:
            break
        out.append(j)
    return out


def session_detours(s: dict, usual: dict) -> list[dict]:
    calls = s["calls"]
    out, taken = [], set()
    for w in scripts_of(s):
        if w["name"] == "<inline python>":
            continue
        # What the script cost to make: its writes and edits, and the runs that failed while it was
        # debugged. A run that worked did the task's work, which the missing tool would have done too.
        idx = [n for n, c in enumerate(calls) if n not in taken and c["at"] >= w["at"] and (
            (c["name"] in EDITS and str(c["input"].get("file_path") or "") == w["path"]) or
            (c["name"] in SHELLS and c["error"] and w["name"] in (c["input"].get("command") or "")))]
        if not idx:
            continue
        out.append({"cause": "workaround", "calls": idx, "key": w["name"], "uses": w["uses"],
                    "example": ", ".join(w["uses"])[:120]})
        taken.update(idx)
    for i in range(len(calls)):
        if i in taken:
            continue
        cause = trigger(calls, i, usual)
        if not cause:
            continue
        idx = [n for n in span_of(calls, i) if n not in taken]
        taken.update(idx)
        c = calls[i]
        err = headline(c["error"] or "")
        for rx, rep in NOISE:
            err = rx.sub(rep, err)
        what = shape(c["input"].get("command") or "") if c["name"] in SHELLS else c["name"]
        out.append({"cause": cause, "calls": idx, "key": tool_key(c["name"], c["input"]), "err": err[:120],
                    "example": what[:120]})
    for d in out:
        cs = [calls[n] for n in d["calls"]]
        d["at"] = cs[0]["at"] if cs else s["start"]
        d["minutes"] = sum(min(c["secs"], WAIT_CAP) for c in cs) / 60
        d["tokens"] = sum(ctx_at(s, c["at"]) + c["chars"] * c["later"] // 4 for c in cs)
        d["n"] = len(cs)
        d["session"], d["task"], d["worker"] = s["session"], s["task"], s["worker"]
    return out


def reported_detours(lo: datetime, hi: datetime) -> list[dict]:
    """What the managed runs said they worked around (the result's `detours`)."""
    try:
        import fe_board
        con = sqlite3.connect(f"file:{fe_board.db_path()}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for tid, session, result in con.execute(
            "SELECT task_id, session, result FROM pm_runs WHERE result IS NOT NULL AND heartbeat BETWEEN ? AND ?",
            (lo.timestamp(), hi.timestamp())):
        try:
            for d in json.loads(result).get("detours") or []:
                out.append({"task": tid, "session": session or "", **d})
        except (ValueError, AttributeError):
            continue
    return out


def detours(lo: datetime, hi: datetime, top: int, loaded=None) -> dict:
    """Every detour in the window, its cost by cause, and the share of all spend it took."""
    runs, sessions = loaded or load(lo, hi)
    sizes: dict = defaultdict(list)
    for s in sessions:
        for c in s["calls"]:
            if not c["error"]:
                sizes[tool_key(c["name"], c["input"])].append(c["chars"])
    usual = {k_: statistics.median(v) for k_, v in sizes.items() if len(v) >= 5}
    found = [d for s in sessions for d in session_detours(s, usual)]

    def share(sel):
        ss = [s for s in sessions if sel(s)]
        tot = sum(s["total"] for s in ss)
        mins = sum((ts(s["end"]) - ts(s["start"])).total_seconds() for s in ss) / 60
        ids = {s["session"] for s in ss}
        mine = [d for d in found if d["session"] in ids and d["cause"] != "slip"]
        return {"sessions": len(ss), "tokens": tot, "detour_tokens": sum(d["tokens"] for d in mine),
                "token_share": round(sum(d["tokens"] for d in mine) / tot, 4) if tot else 0.0,
                "minutes": round(mins), "detour_minutes": round(sum(d["minutes"] for d in mine), 1),
                "per_session": round(len(mine) / len(ss), 2) if ss else 0.0}

    by_cause = {}
    for cause in (*CAUSES, "slip"):
        ds = [d for d in found if d["cause"] == cause]
        by_cause[cause] = {"count": len(ds), "sessions": len({d["session"] for d in ds}),
                           "minutes": round(sum(d["minutes"] for d in ds), 1), "tokens": sum(d["tokens"] for d in ds)}
    groups: dict = defaultdict(list)
    for d in found:
        groups[(d["cause"], d["key"])].append(d)
    def errors(ds):
        n: dict = defaultdict(int)
        for d in ds:
            if d.get("err"):
                n[d["err"]] += 1
        return [e for e, _ in sorted(n.items(), key=lambda x: -x[1])[:3]]
    patterns = sorted(({"cause": c_, "key": k_, "count": len(ds), "sessions": len({d["session"] for d in ds}),
                        "tasks": sorted({f"T{d['task']}" for d in ds if d["task"]}),
                        "minutes": round(sum(d["minutes"] for d in ds), 1), "tokens": sum(d["tokens"] for d in ds),
                        "errors": errors(ds), "example": ds[0].get("example", "")} for (c_, k_), ds in groups.items()),
                      key=lambda p: -(p["tokens"] + p["minutes"] * 20_000))
    tasks: dict = defaultdict(lambda: {"detours": 0, "tokens": 0, "total": 0})
    for s in sessions:
        if s["task"]:
            tasks[s["task"]]["total"] += s["total"]
    for d in found:
        if d["task"] and d["cause"] != "slip":
            tasks[d["task"]]["detours"] += 1; tasks[d["task"]]["tokens"] += d["tokens"]
    return {"window": [lo.isoformat(timespec="minutes"), hi.isoformat(timespec="minutes")],
            "all": share(lambda s: True), "managed": share(lambda s: s["worker"]),
            "by_cause": by_cause, "patterns": patterns[:top],
            "tasks": sorted(({"task": t, **v, "share": round(v["tokens"] / v["total"], 4) if v["total"] else 0.0}
                             for t, v in tasks.items()), key=lambda r: -r["share"])[:top],
            "reported": reported_detours(lo, hi), "found": found}


def detours_report(d: dict, top: int) -> str:
    out = [f"{TAG} detours {d['window'][0]} .. {d['window'][1]}"]
    for name in ("all", "managed"):
        x = d[name]
        out.append(f"  {name:8} {x['sessions']:4} sessions: {x['token_share'] * 100:5.1f}% of tokens "
                   f"({k(x['detour_tokens'])} of {k(x['tokens'])}), {x['detour_minutes']:.0f} of {x['minutes']} min, "
                   f"{x['per_session']} detours a session")
    out += ["", "  cause        count  sess  minutes  tokens"]
    for c_, v in d["by_cause"].items():
        out.append(f"  {c_:12} {v['count']:5} {v['sessions']:5} {v['minutes']:8.1f} {k(v['tokens']):>7}"
                   + ("   (its own mistake: not counted as a detour)" if c_ == "slip" else ""))
    out += ["", "  the detours that cost most                                      count sess  min  tokens"]
    for p in d["patterns"][:top]:
        out.append(f"  {p['cause']:11} {p['key'][:52]:52} {p['count']:4} {p['sessions']:4} {p['minutes']:5.0f} {k(p['tokens']):>7}")
        for e in p.get("errors") or []:
            out.append(f"      ! {e[:100]}")
        if p.get("example"):
            out.append(f"      e.g. {p['example'][:100]}")
    if d["tasks"]:
        out += ["", "  managed tasks by detour share: " + ", ".join(
            f"T{t['task']} {t['share'] * 100:.0f}%" for t in d["tasks"][:10])]
    rep = d["reported"]
    out += ["", f"  reported by the runs themselves: {len(rep)}"
            + ("".join(f"\n    T{r['task']} {r.get('cause')}: {str(r.get('what'))[:90]} ({r.get('minutes')} min)" for r in rep[:top]))]
    return "\n".join(out)


def label_sample(d: dict, sessions: list[dict], n: int, path: Path, seed: int = 1) -> int:
    """Write found detours and as many ordinary calls to label by hand (precision and recall)."""
    import random
    rng = random.Random(seed)
    by_sess = {s["session"]: s for s in sessions}
    picks = rng.sample(d["found"], min(n, len(d["found"])))
    in_detour = {(x["session"], i) for x in d["found"] for i in x["calls"]}
    plain = [(s["session"], i) for s in sessions for i in range(len(s["calls"])) if (s["session"], i) not in in_detour]
    rows = []
    for x in picks:
        cs = by_sess[x["session"]]["calls"]
        first = x["calls"][0] if x["calls"] else 0
        rows.append({"rule": x["cause"], "session": x["session"], "call": first,
                     "before": [brief(c) for c in cs[max(0, first - 2):first]],
                     "calls": [brief(cs[i]) for i in x["calls"][:6]], "label": ""})
    for sess, i in rng.sample(plain, min(n, len(plain))):
        cs = by_sess[sess]["calls"]
        rows.append({"rule": "none", "session": sess, "call": i, "before": [brief(c) for c in cs[max(0, i - 2):i]],
                     "calls": [brief(c) for c in cs[i:i + 3]], "label": ""})
    rng.shuffle(rows)
    # The labeller sees no verdict: the rules' answers go to a key file beside it.
    key = {str(n_): r.pop("rule") for n_, r in enumerate(rows)}
    for n_, r in enumerate(rows):
        r["id"] = str(n_)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    path.with_suffix(path.suffix + ".key").write_text(json.dumps(key), encoding="utf-8")
    return len(rows)


def brief(c: dict) -> str:
    what = c["input"].get("command") or c["input"].get("file_path") or c["input"].get("pattern") or ""
    return f"{c['name']}: {str(what)[:200]}" + (f"  -> ERROR {c['error'][:300]}" if c["error"] else f"  -> {c['chars']} chars")


def validate(path: Path) -> dict:
    """Precision and recall of the rules against a hand-labelled sample (label: a cause, or none)."""
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    key = json.loads(path.with_suffix(path.suffix + ".key").read_text(encoding="utf-8"))
    rows = [dict(r, rule=key[r["id"]]) for r in rows if r.get("label") and r.get("id") in key]
    out = {}
    for c_ in CAUSES:
        tp = sum(1 for r in rows if r["rule"] == c_ and r["label"] == c_)
        fp = sum(1 for r in rows if r["rule"] == c_ and r["label"] != c_)
        fn = sum(1 for r in rows if r["rule"] != c_ and r["label"] == c_)
        out[c_] = {"precision": round(tp / (tp + fp), 2) if tp + fp else None,
                   "recall": round(tp / (tp + fn), 2) if tp + fn else None, "labelled": tp + fn}
    detour = {"tp": sum(1 for r in rows if r["rule"] in CAUSES and r["label"] in CAUSES),
              "fp": sum(1 for r in rows if r["rule"] in CAUSES and r["label"] not in CAUSES),
              "fn": sum(1 for r in rows if r["rule"] not in CAUSES and r["label"] in CAUSES)}
    out["any detour"] = {"precision": round(detour["tp"] / max(1, detour["tp"] + detour["fp"]), 2),
                         "recall": round(detour["tp"] / max(1, detour["tp"] + detour["fn"]), 2), "labelled": len(rows)}
    return out


# ---- cost: what an accepted task costs, by class (cost-of-pass) ----------------------------

def task_costs(days: int) -> list[dict]:
    """Each managed task with spend in the window: its class, its cost, whether it was accepted."""
    import subprocess
    import fe_board
    con = sqlite3.connect(f"file:{fe_board.db_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    since = (datetime.now() - timedelta(days=days)).timestamp()
    spend = {r["task_id"]: dict(r) for r in con.execute(
        "SELECT task_id, SUM(cost_usd) cost, COUNT(*) runs, SUM(output_tokens) output, SUM(cache_read_tokens) cache_read,"
        " MIN(COALESCE(started, queued)) first, MAX(COALESCE(ended, started, queued)) last"
        " FROM pm_run_stats WHERE task_id IS NOT NULL GROUP BY task_id HAVING last >= ?", (since,))}
    cat = {r["task_id"]: r["category"] or "" for r in con.execute("SELECT task_id, category FROM pm_task_models")}
    task = {r["id"]: dict(r) for r in con.execute("SELECT id, title, status FROM tasks")}
    landed = {r["task_id"]: r["landed_head"] or "" for r in con.execute("SELECT task_id, landed_head FROM pm_tasks")}
    done_at: dict = {}
    reopened: set = set()
    for r in con.execute("SELECT at, ref, summary FROM events WHERE kind='task.update' AND summary LIKE '%status=%' ORDER BY at"):
        try:
            tid = int(str(r["ref"]).lstrip("T"))
        except ValueError:
            continue
        status = r["summary"].rsplit("status=", 1)[1].strip()
        if status == "done":
            done_at[tid] = r["at"]
        elif tid in done_at and r["at"] - done_at[tid] <= 7 * 86400:
            reopened.add(tid)
    root = ROOT
    log = subprocess.run(["git", "log", "origin/main", f"--since={days + 14}.days", "--format=%B"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    reverted = set(re.findall(r"This reverts commit ([0-9a-f]{7,40})", log))
    rows = []
    for tid, s in spend.items():
        t = task.get(tid) or {"title": "", "status": "?"}
        head = landed.get(tid, "")
        gone = any(head.startswith(x) or x.startswith(head[:12]) for x in reverted) if head else False
        accepted = t["status"] == "done" and tid not in reopened and not gone
        rows.append({"task": tid, "title": t["title"], "status": t["status"], "class": cat.get(tid) or "unclassified",
                     "cost": round(s["cost"] or 0, 2), "runs": s["runs"], "output": s["output"], "cache_read": s["cache_read"],
                     "accepted": accepted, "reopened": tid in reopened, "reverted": gone,
                     "finished": t["status"] in ("done", "dropped"), "last": s["last"]})
    return rows


def cost(days: int) -> dict:
    rows = task_costs(days)
    classes: dict = defaultdict(list)
    for r in rows:
        classes[r["class"]].append(r)
    table = []
    for c_, rs in sorted(classes.items()):
        fin = [r for r in rs if r["finished"] or r["accepted"]]
        acc = [r for r in rs if r["accepted"]]
        table.append({"class": c_, "tasks": len(rs), "finished": len(fin), "accepted": len(acc),
                      "acceptance": round(len(acc) / len(fin), 2) if fin else None,
                      "median_accepted": round(statistics.median([r["cost"] for r in acc]), 2) if acc else None,
                      "cost_of_pass": round(sum(r["cost"] for r in fin) / len(acc), 2) if acc else None,
                      "open_spend": round(sum(r["cost"] for r in rs if r not in fin), 2)})
    fin = [r for r in rows if r["finished"] or r["accepted"]]
    acc = [r for r in rows if r["accepted"]]
    return {"days": days, "classes": table, "tasks": sorted(rows, key=lambda r: -r["cost"]),
            "all": {"tasks": len(rows), "accepted": len(acc),
                    "cost_of_pass": round(sum(r["cost"] for r in fin) / len(acc), 2) if acc else None,
                    "median_accepted": round(statistics.median([r["cost"] for r in acc]), 2) if acc else None}}


def cost_report(c: dict, top: int) -> str:
    a = c["all"]
    out = [f"{TAG} cost per accepted task, last {c['days']} days (meter; accepted = done, not reopened or reverted in 7 days)",
           f"  all: {a['tasks']} tasks, {a['accepted']} accepted | cost-of-pass ${a['cost_of_pass']} | "
           f"median accepted ${a['median_accepted']}",
           "  class          tasks  finished  accepted  rate   median$  cost-of-pass$  still-open$"]
    dash = lambda v, fmt="{}": "-" if v is None else fmt.format(v)  # noqa: E731
    for r in c["classes"]:
        out.append(f"  {r['class']:14} {r['tasks']:5} {r['finished']:9} {r['accepted']:9}  "
                   f"{dash(r['acceptance'], '{:.0%}'):>5} {dash(r['median_accepted']):>8} "
                   f"{dash(r['cost_of_pass']):>13} {r['open_spend']:>11}")
    out += ["", "  most spent: " + "; ".join(f"T{r['task']} ${r['cost']:.0f} {r['status']}{' reopened' if r['reopened'] else ''}"
                                          f"{' reverted' if r['reverted'] else ''}" for r in c["tasks"][:top])]
    return "\n".join(out)


# ---- trend: tokens per changed code line, kept day by day ----------------------------------

def metrics_dir() -> Path:
    import fe_board
    return fe_board.board_dir() / "manager" / "metrics"


def day_usage(first: date, last: date) -> dict:
    """Per local day: the transcripts' tokens (each request once) and how many sessions spent them."""
    lo = datetime.combine(first, datetime.min.time()).astimezone()
    hi = datetime.combine(last + timedelta(days=1), datetime.min.time()).astimezone()
    reqs: dict = {}
    for start, paths in fe_tokens.sessions(all_projects=False):
        if start > utc(hi) or not recent(paths, lo):
            continue
        for path in paths:
            try:
                fh = path.open(encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    m = r.get("message") if isinstance(r.get("message"), dict) else {}
                    if r.get("type") != "assistant" or m.get("model") == "<synthetic>" or not isinstance(m.get("usage"), dict):
                        continue
                    t = r.get("timestamp") or ""
                    if not t:
                        continue
                    day = ts(t).astimezone().date()
                    if first <= day <= last:
                        reqs[r.get("requestId") or r.get("uuid")] = (day.isoformat(), m["usage"], paths[0].stem)
    out: dict = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "requests": 0,
                                     "sessions": set()})
    for day, u, sess in reqs.values():
        d = out[day]
        d["input"] += int(u.get("input_tokens") or 0); d["output"] += int(u.get("output_tokens") or 0)
        d["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        d["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
        d["requests"] += 1; d["sessions"].add(sess)
    return out


def day_meter(first: date, last: date) -> dict:
    """Per local day: the meter's cost (every provider) and the Codex runs' tokens (no transcript reads them)."""
    lo = datetime.combine(first, datetime.min.time()).astimezone().timestamp()
    hi = datetime.combine(last + timedelta(days=1), datetime.min.time()).astimezone().timestamp()
    runs, _ = board_runs(lo, hi)
    out: dict = defaultdict(lambda: {"cost_usd": 0.0, "runs": 0, "codex_output": 0, "codex_cache_read": 0})
    for r in runs:
        d = out[datetime.fromtimestamp(r["started"] or r["queued"]).date().isoformat()]
        d["cost_usd"] += r["cost_usd"] or 0; d["runs"] += 1
        if (r.get("provider") or "") not in ("", "claude"):
            d["codex_output"] += r["output_tokens"] or 0; d["codex_cache_read"] += r["cache_read_tokens"] or 0
    return out


def day_code(first: date, last: date, ref: str = "origin/main") -> dict:
    """Per local day: the code lines the commits on `ref` changed (fe_loc's rule)."""
    import fe_loc
    out: dict = defaultdict(lambda: {"commits": 0, "changed": 0, "code_net": 0})
    for r in fe_loc.commits(ref, since=first.isoformat(), until=(last + timedelta(days=1)).isoformat()):
        day = datetime.fromisoformat(r["when"]).astimezone().date()
        if first <= day <= last:
            d = out[day.isoformat()]
            d["commits"] += 1; d["changed"] += r["changed"]; d["code_net"] += r["code_net"]
    return out


def judged(d: dict) -> int:
    """The tokens the trend judges: what was read from the cache and written, Claude and Codex."""
    return d["cache_read"] + d["output"] + d.get("codex_cache_read", 0) + d.get("codex_output", 0)


def trend(days: int, since: str | None = None, refresh: bool = False) -> list[dict]:
    """One row per day, oldest first; a finished day is kept in `<board>/manager/metrics/DAY.json`."""
    today = date.today()
    first = date.fromisoformat(since) if since else today - timedelta(days=days - 1)
    store = metrics_dir()
    store.mkdir(parents=True, exist_ok=True)
    rows: dict = {}
    for i in range((today - first).days + 1):
        day = first + timedelta(days=i)
        f = store / f"{day.isoformat()}.json"
        if day < today and f.exists() and not refresh:
            try:
                rows[day.isoformat()] = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                pass
    todo = [first + timedelta(days=i) for i in range((today - first).days + 1)
            if (first + timedelta(days=i)).isoformat() not in rows]
    if todo:
        lo, hi = min(todo), max(todo)
        usage, meter, code = day_usage(lo, hi), day_meter(lo, hi), day_code(lo, hi)
        for day in todo:
            key = day.isoformat()
            u = usage.get(key) or {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "requests": 0, "sessions": set()}
            row = {"day": key, "partial": day == today, **{x: y for x, y in u.items() if x != "sessions"},
                   "sessions": len(u["sessions"]), **(meter.get(key) or {"cost_usd": 0.0, "runs": 0, "codex_output": 0,
                                                                          "codex_cache_read": 0}),
                   **(code.get(key) or {"commits": 0, "changed": 0, "code_net": 0})}
            row["tokens"] = judged(row)
            row["per_line"] = round(row["tokens"] / row["changed"]) if row["changed"] else None
            rows[key] = row
            if day < today:
                (store / f"{key}.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
    ordered = [rows[k_] for k_ in sorted(rows)]
    for i, r in enumerate(ordered):
        week = ordered[max(0, i - 6):i + 1]
        changed = sum(w["changed"] for w in week)
        r["per_line_7d"] = round(sum(w["tokens"] for w in week) / changed) if changed else None
    return ordered


def trend_report(rows: list[dict]) -> str:
    out = [f"{TAG} tokens per changed code line (cache read + output, Claude transcripts + Codex meter; "
           "lines by fe_loc on origin/main)",
           "  day         sessions  tokens   meter$  commits  changed  net     per-line  7-day"]
    for r in rows:
        pl = f"{k(r['per_line'])}" if r["per_line"] is not None else "-"
        p7 = f"{k(r['per_line_7d'])}" if r["per_line_7d"] is not None else "-"
        out.append(f"  {r['day']}{'*' if r['partial'] else ' '} {r['sessions']:7}  {k(r['tokens']):>7} {r['cost_usd']:7.0f} "
                   f"{r['commits']:7} {r['changed']:8} {r['code_net']:+6}  {pl:>8} {p7:>6}")
    out.append("  * today, still counting")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fe_usage.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (("night", "where the night's tokens and time went"),
                       ("smells", "repeated steps and failures, throwaway scripts, rereads: what the process crew fixes"),
                       ("detours", "the times agents worked around the project instead of doing the task, by cause")):
        n = sub.add_parser(name, help=text)
        n.add_argument("--since", help="local ISO time, e.g. 2026-10-01T18:00")
        n.add_argument("--until", help="local ISO time (default: now)")
        n.add_argument("--days", type=float, help="the last N days instead of the night")
        n.add_argument("--top", type=int, default=15)
        n.add_argument("--json", action="store_true")
        if name == "detours":
            n.add_argument("--label", metavar="FILE", help="write a sample of detours and ordinary calls to label by hand")
            n.add_argument("--sample", type=int, default=30, help="how many of each --label writes")
            n.add_argument("--validate", metavar="FILE", help="precision and recall against a labelled sample")
    c = sub.add_parser("cost", help="cost per accepted task, by class (the process crew's outcome)")
    c.add_argument("--days", type=int, default=14)
    c.add_argument("--top", type=int, default=10)
    c.add_argument("--json", action="store_true")
    t = sub.add_parser("trend", help="tokens per changed code line, day by day (the process crew's measure)")
    t.add_argument("--days", type=int, default=14)
    t.add_argument("--since", help="first day, YYYY-MM-DD")
    t.add_argument("--refresh", action="store_true", help="recount the kept days too")
    t.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "trend":
        rows = trend(args.days, args.since, args.refresh)
        print(json.dumps(rows, indent=1) if args.json else trend_report(rows))
        return 0
    if args.cmd == "cost":
        c = cost(args.days)
        print(json.dumps(c, indent=1) if args.json else cost_report(c, args.top))
        return 0
    if args.cmd == "detours" and args.validate:
        print(json.dumps(validate(Path(args.validate)), indent=1))
        return 0
    if args.days and not args.since:
        hi = datetime.now().astimezone()
        lo, hi = hi - timedelta(days=args.days), hi
    else:
        lo, hi = window(args)
    if args.cmd == "smells":
        m = smells(lo, hi, args.top)
        print(json.dumps(m, default=str, indent=1) if args.json else smells_report(m, args.top))
        return 0
    if args.cmd == "detours":
        loaded = load(lo, hi)
        d = detours(lo, hi, args.top, loaded)
        if args.label:
            n = label_sample(d, loaded[1], args.sample, Path(args.label))
            print(f"{TAG} {n} rows to label in {args.label}: set each \"label\" to one of "
                  f"{', '.join(CAUSES)}, slip or none, then run detours --validate {args.label}")
            return 0
        if args.json:
            print(json.dumps({k_: v for k_, v in d.items() if k_ != "found"}, default=str, indent=1))
        else:
            print(detours_report(d, args.top))
        return 0
    a = analyse(lo, hi, args.top)
    if args.json:
        print(json.dumps(a, default=str, indent=1))
    else:
        print(report(a))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
