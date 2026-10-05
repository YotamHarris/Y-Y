"""The board's Nightly view: the bench and the cleanup crew, live and night by night (D262, D265).

    view(b) -> {"bench": {...}, "crew": {...}}   what /api/nightly serves

The bench: whether a night's bench holds the work now, its GPU claim and what that waits
for, the tail of bench-night.log, and every night that started, failed or kept a report
(performance/nightly/DAY.json) with the cases' verdicts and the commits it pinned.
The crew: the crew-night log, each night's scorecard (manager/crew/DAY.json) with the
numbers worth a trend, and the crew's tasks with the commits they made and the blind
reviewer's verdicts.

Read-only and stdlib only: the board server runs it on the system Python (no psutil).
The bench's part is shown only when the game has a bench job (studio.toml [[nightly]]).

It also names the game's nightly jobs for the service (jobs(), held(), due()): those load
the jobs' modules, so the view never calls them.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path

import fe_board
import studio_config

REPO = studio_config.repo_root()
DAYS = 30          # nights shown
LOG_LINES = 40     # of each log's tail
CREW_TITLE = re.compile(r"^(Code|Process) crew: (\d{4}-\d{2}-\d{2})$")
_commits = {"at": 0.0, "rows": []}


def performance_dir() -> Path:
    return Path(os.environ.get("FE_PERFORMANCE_DIR", studio_config.data_dir() / "performance"))


def manager_dir() -> Path:
    return fe_board.board_dir() / "manager"


def tail(path: Path, n: int = LOG_LINES) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n * 2:]:
        if out and line == out[-1].removeprefix("[fe-manager] "):
            continue  # the night's errors print twice, with and without the tag
        out.append(line)
    return out[-n:]


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def setting(b, key: str) -> str:
    row = b.q1("SELECT value FROM pm_settings WHERE key=?", key)
    return row["value"] if row else ""


def events(b, kind: str, limit: int = 60) -> list[dict]:
    return [dict(r) for r in b.q("SELECT at, summary FROM events WHERE kind=? ORDER BY id DESC LIMIT ?", kind, limit)]


def alive(pid) -> bool:
    gpu = studio_config.adapter("gpu")
    if gpu is not None and hasattr(gpu, "pid_alive"):
        return gpu.pid_alive(pid)
    try:
        import psutil
        return bool(pid) and psutil.pid_exists(int(pid))
    except (ImportError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- the game's nightly jobs
#
# A job is a studio.toml [[nightly]] row: name, module (in the game's tools), hour, latest,
# holds_work. The service starts `fe_manager.py _<name>_night` in its own hidden process once
# a night from `hour` until `latest` (D262: the bench, at 04:00), unless the manager's config
# sets nightly_<name> false; its settings are <name>_night (the day it last started),
# <name>_hold and <name>_request (a run asked of the service now). Its module gives:
#   arguments(parser, internal)  its flags on `<name>-night` (by hand) and `_<name>_night`;
#                                HELP the former's help
#   command(args, cfg, internal) runs either; the exit code
#   held(b)                      with holds_work: its hold while its process lives, else None
#   request_args(value)          optional: `_<name>_night`'s flags for a <name>_request value
#   started_note(value)          optional: what the 'nightly <name> started' event adds for one

def jobs() -> list[dict]:
    return [j for j in studio_config.get("nightly") or [] if j.get("name") and j.get("module")]


def job(name: str) -> dict | None:
    return next((j for j in jobs() if j["name"] == name), None)


def module(j: dict):
    return studio_config.game_module(j["module"])


def key(j: dict, what: str) -> str:
    """night, hold or request: the job's pm_settings row."""
    return f"{j['name']}_{what}"


def due(b, config: dict, j: dict, now) -> bool:
    return (bool(config.get(f"nightly_{j['name']}", True)) and j.get("hour", 0) <= now.hour < j.get("latest", 24)
            and setting(b, key(j, "night")) != now.date().isoformat())


def held(b):
    """A holding job's hold while its process lives (no run starts), else None."""
    for j in jobs():
        if j.get("holds_work"):
            h = module(j).held(b)
            if h:
                return h
    return None


def ran(b, day: str) -> bool:
    """A holding job started on `day`: what follows it (the crew, D265) need not wait for its hour."""
    return any(j.get("holds_work") and setting(b, key(j, "night")) == day for j in jobs())


# ---------------------------------------------------------------- the bench

def bench_now(b) -> dict:
    """The bench's hold while its process lives, and its GPU claims."""
    raw = setting(b, "bench_hold")
    hold = None
    if raw:
        try:
            h = json.loads(raw)
            if alive(h.get("pid")):
                hold = dict(pid=h["pid"], since=h.get("since"), suspended=len(h.get("suspended") or []), measured=0)
        except (ValueError, TypeError):
            pass
    claims = []
    if hold and b.q1("SELECT 1 FROM sqlite_master WHERE name='gpu_claims'"):
        rows = b.q("SELECT * FROM gpu_claims WHERE pid=? ORDER BY id DESC LIMIT 1", hold["pid"])
        for r in rows:
            claims.append(dict(id=r["id"], note=r["note"], state=r["state"], created=r["created"],
                               started=r["started"], ended=r["ended"],
                               asked=sorted(json.loads(r["asked"] or "{}"))))
        hold["measured"] = b.q1("SELECT COUNT(*) FROM gpu_claims WHERE pid=? AND state='done'", hold["pid"])[0]
    return dict(running=bool(hold), hold=hold, claim=claims[0] if claims else None,
                request=setting(b, "bench_request"), log=tail(manager_dir() / "bench-night.log"))


def bench_report(day: str, r: dict) -> dict:
    verdicts = r.get("verdicts") or {}
    worst = max((v.get("delta_ms", {}).get("combined_ms", 0) for v in verdicts.values() if v), default=None)
    return dict(day=day, at=r.get("at"), head=r.get("head"), reference=r.get("reference"), note=r.get("note"),
                cases=r.get("cases") or [], commits=r.get("commits") or [], offenders=r.get("offenders") or [],
                verdicts=verdicts, measured=r.get("measured"), task=r.get("task"), worst_ms=worst,
                truncated=r.get("truncated") or [], unrunnable=r.get("unrunnable") or [],
                attributed=r.get("attributed") or [])


def bench_nights(b, days: int = DAYS) -> list[dict]:
    """Every night the bench started, newest first: its report when it kept one, else why not."""
    reports = {}
    for p in (performance_dir() / "nightly").glob("????-??-??.json"):
        r = read_json(p)
        if isinstance(r, dict):
            reports[p.stem] = bench_report(p.stem, r)
    starts: dict[str, dict] = {}
    current = None  # a failure is the night that started last
    for e in reversed(events(b, "manager.bench", 200)):
        if "rehearsal" in e["summary"]:
            current = {}  # a rehearsal is not a night: rehearsal() reads its events
        elif "started" in e["summary"]:
            m = re.search(r"for the night of (\d{4}-\d{2}-\d{2})", e["summary"])
            day = m.group(1) if m else time.strftime("%Y-%m-%d", time.localtime(e["at"]))
            current = starts.setdefault(day, dict(day=day, started=None, failed=None))
            current["started"] = e["at"]  # the latest run of the night
            current["failed"] = None  # a rerun of the night starts it afresh
        elif "failed" in e["summary"] and current is not None and "day" in current:
            current["failed"] = e["summary"].split("failed: ", 1)[-1]
    out = []
    for day in sorted(set(reports) | set(starts), reverse=True)[:days]:
        n = dict(starts.get(day) or dict(day=day, started=None, failed=None))
        n["report"] = reports.get(day)
        out.append(n)
    return out


# ---------------------------------------------------------------- over time

def measured(sha: str, cache: dict) -> dict:
    """{case: {"ms": combined median, "passes": {pass: ms}}} for a commit, from its records."""
    if sha not in cache:
        out = {}
        for p in (performance_dir() / "records" / sha).glob("*/*.json"):
            r = read_json(p) or {}
            try:
                obs = [o["summary"] for o in r["observations"][-3:]]
                passes = {k: statistics.median(o["passes"].get(k, 0) for o in obs) for k in obs[-1]["passes"]}
                out[r["scenario"]["name"]] = dict(ms=statistics.median(o["combined_ms"] for o in obs), passes=passes)
            except (KeyError, TypeError, ValueError, statistics.StatisticsError):
                continue
        cache[sha] = out
    return cache[sha]


def pass_steps(after: dict | None, before: dict | None, top: int = 3) -> list[dict]:
    """The passes that moved most between two measurements of one case."""
    if not after or not before:
        return []
    a, b = after["passes"], before["passes"]
    steps = sorted(((k, a.get(k, 0) - b.get(k, 0)) for k in (set(a) | set(b)) - {"total"}),
                   key=lambda x: -abs(x[1]))
    return [dict(pass_name=k, delta_ms=round(d, 4)) for k, d in steps[:top] if abs(d) >= 0.005]


def history(nights: list[dict]) -> dict:
    """Each case's measured time at each night's head, oldest first: the first night's
    reference leads, so one night already shows a before and an after."""
    reports = [n["report"] for n in reversed(nights) if n.get("report")]
    points = []
    if reports and reports[0].get("reference"):
        points.append(dict(label="before " + reports[0]["day"], commit=reports[0]["reference"]))
    points += [dict(label=r["day"], commit=r["head"]) for r in reports if r.get("head")]
    cache: dict = {}
    cases = sorted({c for r in reports for c in r["cases"]} | {u["case"] for r in reports for u in r["unrunnable"]})
    rows = []
    for c in cases:
        vals = [(measured(p["commit"], cache).get(c) or {}).get("ms") for p in points]
        failed = [dict(day=r["day"], why=u.get("why", "")) for r in reports for u in r["unrunnable"] if u["case"] == c]
        rows.append(dict(case=c, values=[round(v, 4) if v is not None else None for v in vals], failed=failed))
    return dict(points=[dict(p, commit=p["commit"][:10]) for p in points], cases=rows)


def flagged(nights: list[dict]) -> list[dict]:
    """Every commit a night flagged as costing performance, newest night first: past the
    allowance (a finding, filed), or a rise within it traced to its commit (recorded debt).
    A trace whose own step is within noise is not a flag."""
    cache: dict = {}
    out = []
    for n in nights:
        r = n.get("report")
        if not r:
            continue
        rows = [dict(o, kind="over the allowance", parent=o.get("against")) for o in r["offenders"]]
        rows += [dict(a, kind="within the allowance") for a in r["attributed"] if not a.get("within_noise")]
        for o in rows:
            parent = o.get("parent") or o.get("against")
            out.append(dict(day=r["day"], kind=o["kind"], commit=o["commit"][:10], subject=o.get("subject", ""),
                            case=o["case"], own_ms=(o.get("own_delta_ms") or {}).get("combined_ms"),
                            total_ms=(o.get("delta_ms") or {}).get("combined_ms"), task=r.get("task"),
                            hard=o.get("hard_failures") or [],
                            passes=pass_steps(measured(o["commit"], cache).get(o["case"]),
                                              measured(parent, cache).get(o["case"]) if parent else None)))
    return out


# ---------------------------------------------------------------- the crew

def crew_commits(since_days: int = 60) -> list[dict]:
    """main's commits of the last days with their crew trailers, cached a minute."""
    if time.time() - _commits["at"] < 60:
        return _commits["rows"]
    try:
        out = subprocess.run(["git", "log", "--first-parent", f"--since={since_days}.days", "origin/main",
                              "--format=%H%x1f%ct%x1f%s%x1f%b%x1e"], cwd=REPO, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return _commits["rows"]
    rows = []
    for chunk in out.split("\x1e"):
        parts = chunk.strip("\n").split("\x1f")
        if len(parts) < 4:
            continue
        sha, at, subject, body = parts[:4]
        crew = re.search(r"^Crew:\s*T(\d+)", body, re.M)
        review = re.search(r"^Crew-Review:\s*(\w+)", body, re.M)
        directive = re.search(r"^Directive:\s*(.+)$", body, re.M)
        rows.append(dict(sha=sha, at=int(at), subject=subject, crew=int(crew.group(1)) if crew else None,
                         review=review.group(1) if review else None,
                         directive=directive.group(1).strip() if directive else None))
    _commits.update(at=time.time(), rows=rows)
    return rows


def crew_tasks(b, days: int = DAYS) -> list[dict]:
    rows = b.q("SELECT id, title, status, claimed_by AS owner, updated FROM tasks WHERE title LIKE '% crew: %' "
               "ORDER BY id DESC LIMIT ?", days * 2)
    commits = crew_commits()
    out = []
    for r in rows:
        m = CREW_TITLE.match(r["title"])
        if not m:
            continue
        mine = re.compile(rf"\bT{r['id']}\b")
        made = [c for c in commits if c["crew"] == r["id"] or (c["crew"] is None and mine.search(c["subject"]))]
        out.append(dict(id=r["id"], crew=m.group(1).lower(), day=m.group(2), status=r["status"],
                        owner=r["owner"], updated=r["updated"],
                        commits=[dict(sha=c["sha"][:10], subject=c["subject"], review=c["review"],
                                      directive=c["directive"], trailer=c["crew"] is not None) for c in made]))
    return out


def scorecard(s: dict) -> dict:
    """The numbers of one night's scorecard that are worth a trend, and its summary."""
    c, p = s.get("code") or {}, s.get("process") or {}
    h = c.get("health") or {}
    det = (p.get("detours") or {}).get("day", {}).get("all", {})
    cost = (p.get("cost") or {}).get("all", {})
    last = c.get("last_day") or {}
    try:
        import fe_crew
        text = fe_crew.summary(s)
    except Exception as e:  # noqa: BLE001 - an older scorecard's shape still lists
        text = f"(no summary: {type(e).__name__})"
    return dict(day=s.get("day"), at=s.get("at"), head=c.get("head"),
                penalty=h.get("penalty"), dup_tokens=h.get("dup_tokens"), cycle_edges=h.get("cycle_edges"),
                tests=h.get("tests"), code_lines=h.get("code_lines"), over=h.get("over"),
                worse=len(last.get("worse") or []), ratchet=len(last.get("ratchet") or []),
                detour_share=det.get("token_share"), detour_minutes=det.get("detour_minutes"),
                cost_of_pass=cost.get("cost_of_pass"), accepted=cost.get("accepted"), tasks=cost.get("tasks"),
                agents_md=(p.get("instructions") or {}).get("agents_md_bytes"), summary=text)


def crew_nights(days: int = DAYS) -> list[dict]:
    out = []
    for path in sorted((manager_dir() / "crew").glob("????-??-??.json"), reverse=True)[:days]:
        s = read_json(path)
        if isinstance(s, dict):
            out.append(scorecard(s))
    return out


def view(b, days: int = DAYS) -> dict:
    tasks = crew_tasks(b, days)
    if any(j["name"] == "bench" for j in jobs()):
        bench = dict(**bench_now(b), nights=(nights := bench_nights(b, days)), history=history(nights),
                     flagged=flagged(nights), rehearsal=rehearsal(b))
    else:  # a game with no nightly bench: its part is empty
        bench = dict(running=False, hold=None, claim=None, request="", log=[], nights=[], history=history([]),
                     flagged=[], rehearsal=None, off=True)
    return dict(
        now=time.time(),
        bench=bench,
        crew=dict(mode=crew_mode(), night=setting(b, "crew_night"), log=tail(manager_dir() / "crew-night.log"),
                  running=[t for t in tasks if t["status"] in ("ready", "in_progress", "blocked")],
                  tasks=tasks, nights=crew_nights(days)))


def rehearsal(b) -> dict | None:
    """The last rehearsal of the coming night (`bench-night --rehearse`): when it started,
    whether it failed, and the report it kept."""
    started = failed = None
    for e in reversed(events(b, "manager.bench", 200)):
        if "rehearsal" in e["summary"]:
            started, failed = e["at"], None
        elif "started" in e["summary"]:
            started = started and -started  # a night after it: its failures are not the rehearsal's
        elif "failed" in e["summary"] and started and started > 0:
            failed = e["summary"].split("failed: ", 1)[-1]
    paths = sorted((performance_dir() / "nightly").glob("rehearsal-*.json"))
    r = read_json(paths[-1]) if paths else None
    report = bench_report(paths[-1].stem.removeprefix("rehearsal-"), r) if isinstance(r, dict) else None
    if report and started and report["at"] and report["at"] < abs(started):
        report = None  # the kept report is an older rehearsal's
    return dict(started=abs(started), failed=failed, report=report) if started else None


def crew_mode() -> str:
    cfg = read_json(manager_dir() / "config.json") or {}
    return str(cfg.get("nightly_crew", "shadow"))
