#!/usr/bin/env python3
"""Jev in the agents' process (D161): TypeSafe AI's typed decision model.

Jev answers typed questions about a block of text or JSON -- a yes/no
probability (`noul`), a choice among up to 255 options, a rubric score -- in
70-500 ms, for $0.042 per million input tokens. It never writes prose. That
makes it cheap and fast enough for the places a full LLM is not: inside the
hooks, which run on every prompt, tool call and turn end, and sweeps over the
whole history.

Jev is a development tool only. The game never calls it (Yotam, 2026-09-28:
any AI in the game runs on the device); nothing in engine/crates imports this.

The rule for every use: code gathers the facts, Jev reads the prose. Its
answers warn, rank or suggest, or hand a command to Yotam to confirm; they
never deny, block or authorise anything. Every call fails open: no key, a
timeout, an HTTP error or a malformed reply gives None, and the caller goes on
as if Jev did not exist.

The uses, each behind its own switch (`all` is the master):
    dod        the turn-end Definition-of-Done check (Stop hook, task review)
    reversals  suspected unrecorded reversals in docs/decisions/ (fe_docs queue)
    board      unread messages ordered by relevance; task routing suggestions
    gate       a risk check on shell commands before they run (PreToolUse)

A switch resolves, first match wins, from
    FE_JEV=0|1, FE_JEV_<USE>=0|1                   one session or one test
    %LOCALAPPDATA%\\BodySimulation\\jev.toml         this machine, every checkout
    engine/tools/jev_questions.toml `enabled`       the checked-in default
and a use runs only when both its switch and `all` are on. Off, a use makes no
call and prints nothing: the process is exactly what it was without Jev.

    fe_jev.py status                   each switch and where its value came from
    fe_jev.py on|off USE|all           set the machine-wide switch
    fe_jev.py ask (--state T | --state-file F) [--noul Q]... [--choice Q A,B,C]...
    fe_jev.py dod [--transcript P] [--note TEXT]   the DoD check, by hand
    fe_jev.py suspect [--limit N]      rebuild the reversal leads (engine/artifacts/jev)
    fe_jev.py route (N3 | --text T)    a suggested assignee and priority
    fe_jev.py gate --cmd "..."         the command check, by hand
    fe_jev.py selftest                 a mocked API; no network, no key
    (the evals are fe_jev_eval.py)

The key is TYPESAFE_API_KEY from the environment or, for a session that started
before it was set, the user's environment in the registry. Never a file.
Calls are cached by content and logged to engine/artifacts/jev/calls.jsonl.
Stdlib only; any Python 3.11.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path

# urllib and the thread pool are imported where a call is made: a hook whose
# use is off only reads the switches.

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio_config  # noqa: E402  (the checkout, its data folder, [jev] in studio.toml)

ROOT = studio_config.repo_root()
TOOLS = studio_config.studio_dir()
# The questions are the game's words ([jev] questions); the studio's copy when it has none.
QUESTIONS = ROOT / studio_config.get("jev.questions", studio_config.rel(TOOLS / "jev_questions.toml"))
API = "https://api.typesafe.ai/v1/systemone"
USES = ("dod", "reversals", "board", "gate", "input")  # input: fe_playtest.py's Jev policy (D166)
TAG = "[fe-jev]"


def art_dir() -> Path:
    return Path(os.environ.get("FE_JEV_DIR") or studio_config.artifacts_dir() / "jev")


def switch_file() -> Path:
    if os.environ.get("FE_JEV_SWITCHES"):
        return Path(os.environ["FE_JEV_SWITCHES"])
    return studio_config.data_dir() / "jev.toml"


_config: dict | None = None


def config() -> dict:
    global _config
    if _config is None:
        with QUESTIONS.open("rb") as fh:
            _config = tomllib.load(fh)
    return _config


def use_cfg(use: str) -> dict:
    return config().get(use, {})


def threshold(use: str) -> float:
    return float(use_cfg(use).get("threshold", 0.5))


# ------------------------------------------------------------------ switches

def _flag(text: str) -> bool | None:
    t = text.strip().lower()
    if t in ("1", "on", "true", "yes"):
        return True
    if t in ("0", "off", "false", "no"):
        return False
    return None


def read_switches() -> dict:
    try:
        with switch_file().open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, bool)}


def resolve(use: str) -> tuple[bool, str]:
    """(on, where the value came from) for one switch, `all` included."""
    env = "FE_JEV" if use == "all" else f"FE_JEV_{use.upper()}"
    v = _flag(os.environ.get(env, ""))
    if v is not None:
        return v, env
    file = read_switches()
    if use in file:
        return file[use], str(switch_file())
    if use == "all":
        return True, "default"
    return bool(use_cfg(use).get("enabled", False)), "default"


def enabled(use: str) -> bool:
    """Whether a use runs: its own switch and the master. Never raises."""
    try:
        return resolve("all")[0] and resolve(use)[0]
    except Exception:  # noqa: BLE001 -- a broken switch means off
        return False


def write_switches(values: dict) -> Path:
    path = switch_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Jev's switches on this machine, for every checkout and agent (D161).",
             "# Written by `fe_jev.py on|off USE|all`; FE_JEV and FE_JEV_<USE> override them.", ""]
    for k in ("all",) + USES:
        if k in values:
            lines.append(f"{k} = {'true' if values[k] else 'false'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def status_lines() -> list[str]:
    lines = []
    master, msrc = resolve("all")
    lines.append(f"all        {'on ' if master else 'off'}  ({msrc})")
    for use in USES:
        on, src = resolve(use)
        eff = "" if master or not on else "  -- but `all` is off"
        lines.append(f"{use:<10} {'on ' if on else 'off'}  ({src}){eff}")
    key = api_key()
    lines.append(f"key        {'set' if key else 'missing: set TYPESAFE_API_KEY (user environment)'}")
    return lines


def summary_line() -> str:
    """One line for the session-start report: what is running."""
    try:
        on = [u for u in USES if enabled(u)]
        off = [u for u in USES if u not in on]
        key = "" if api_key() else " | no TYPESAFE_API_KEY"
        return f"{TAG} on: {', '.join(on) or 'none'} | off: {', '.join(off) or 'none'}{key}"
    except Exception as e:  # noqa: BLE001
        return f"{TAG} unavailable: {e}"


# ------------------------------------------------------------------- the API

def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key or sys.platform != "win32":
        return key
    try:  # a session started before the key was set does not inherit it
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, "TYPESAFE_API_KEY")[0]).strip()
    except OSError:
        return ""


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def choice(instructions: str, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, levels: list) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


def p(answers: dict | None, key: str) -> float:
    """A noul's probability, a choice's confidence; 0 when absent."""
    a = (answers or {}).get(key) or {}
    if "noul" in a:
        return float(a["noul"])
    return float(a.get("confidence", 0.0))


# selftest swaps in a fake: fn(body, key, timeout) -> (status, payload)
_transport = None


# Kept-alive HTTPS connections, shared by every thread of one process: a new
# connection costs a TCP and a TLS handshake, about 130 ms from here (the
# `latency` probe). `_connection` lets the selftest put in a fake.
_pool: list = []
_pool_lock = threading.Lock()
_connection = None
POOL = 8


def _take(timeout: float):
    import http.client
    from urllib.parse import urlsplit
    with _pool_lock:
        c = _pool.pop() if _pool else None
    if c is None:
        u = urlsplit(API)
        c = (_connection or http.client.HTTPSConnection)(u.hostname, u.port or 443, timeout=timeout)
        return c, False
    c.timeout = timeout
    if getattr(c, "sock", None) is not None:
        c.sock.settimeout(timeout)
    return c, True


def _give(c) -> None:
    with _pool_lock:
        if len(_pool) < POOL:
            _pool.append(c)
            return
    c.close()


def _http(body: dict, key: str, timeout: float) -> tuple[int, dict]:
    """One POST on a kept-alive connection. One the server closed while it sat
    idle is reopened once. The backend's own time, when the reply names it
    (x-envoy-upstream-service-time), comes back as `_backend_ms`."""
    import http.client
    from urllib.parse import urlsplit
    data = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for _ in range(2):
        c, reused = _take(max(timeout, 0.05))
        try:
            c.request("POST", urlsplit(API).path, body=data, headers=headers)
            r = c.getresponse()
            raw = r.read()
        except (http.client.HTTPException, OSError) as e:
            c.close()
            if reused and not isinstance(e, TimeoutError):
                continue  # a stale kept-alive connection: once more on a new one
            return 0, {"error": f"{type(e).__name__}: {e}"}
        if r.will_close:
            c.close()
        else:
            _give(c)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {"error": raw[:400].decode(errors="replace")}
        if isinstance(payload, dict):
            up = r.getheader("x-envoy-upstream-service-time") or ""
            if up.isdigit():
                payload["_backend_ms"] = int(up)
        return r.status, payload
    return 0, {"error": "the connection closed twice"}


def _bounded(fn, timeout: float):
    """fn() on a daemon thread, or None once `timeout` passes: a hook never
    waits on the network longer than its budget."""
    box: list = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else None


def _cache_path(h: str) -> Path:
    return art_dir() / "cache" / h[:2] / f"{h}.json"


def _cache_get(h: str) -> dict | None:
    try:
        return json.loads(_cache_path(h).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cache_put(h: str, entry: dict) -> None:
    try:
        path = _cache_path(h)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry), encoding="utf-8")
    except OSError:
        pass


_log_lock = threading.Lock()


def _compact(answers: dict | None) -> dict:
    out = {}
    for k, a in (answers or {}).items():
        if not isinstance(a, dict):
            continue
        if "noul" in a:
            out[k] = round(float(a["noul"]), 3)
        elif "choice" in a:
            out[k] = [a["choice"], round(float(a.get("confidence", 0)), 3)]
        elif "score" in a:
            out[k] = round(float(a["score"]), 3)
    return out


def _log(entry: dict) -> None:
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    try:
        path = art_dir() / "calls.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def ask(use: str, state, questions: dict, timeout: float | None = None, cache: bool = True,
        meta: dict | None = None) -> dict | None:
    """Jev's answers, keyed like `questions`, or None: no key, a timeout, an
    error or a malformed reply. Never raises; the caller carries on without.
    `meta` gets the call's facts (ms, cached, tokens) for the evals."""
    try:
        return _ask(use, state, questions, timeout, cache, meta if meta is not None else {})
    except Exception as e:  # noqa: BLE001 -- fail open, always
        _log({"use": use, "ok": False, "err": f"{type(e).__name__}: {e}"})
        return None


def _ask(use, state, questions, timeout, cache, meta):
    body = {"model": config().get("model", "jev-latest"), "state": state, "questions": questions}
    h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
    if cache:
        hit = _cache_get(h)
        if hit is not None and isinstance(hit.get("answers"), dict):
            meta.update(ms=hit.get("ms"), cached=True, tokens=(hit.get("usage") or {}).get("input_tokens"))
            _log({"use": use, "hash": h, "ok": True, "cached": True, "ms": hit.get("ms")})
            return hit["answers"]
    key = "selftest" if _transport else api_key()
    if not key:
        meta.update(err="no key")
        return None
    budget = timeout if timeout is not None else float(use_cfg(use).get("timeout_s", 2.0))
    t0 = time.perf_counter()  # monotonic ticks at 15.6 ms on Windows
    deadline = t0 + budget
    send = _transport or _http
    status, payload, tries = 0, {"error": "no time"}, 0
    while True:
        left = deadline - time.perf_counter()
        if left <= 0.02:
            break
        tries += 1
        got = _bounded(lambda: send(body, key, left), left)
        if got is None:
            status, payload = 0, {"error": "timeout"}
            break
        status, payload = got
        if status not in (429, 500, 502, 503, 529):
            break
        wait = min(0.25 * 2 ** (tries - 1), deadline - time.perf_counter() - 0.05)
        if wait <= 0:
            break
        time.sleep(wait)
    ms = round((time.perf_counter() - t0) * 1000)
    answers = payload.get("answers") if status == 200 and isinstance(payload, dict) else None
    ok = isinstance(answers, dict) and all(isinstance(answers.get(k), dict) for k in questions)
    usage = (payload.get("usage") or {}) if ok else {}
    backend = payload.get("_backend_ms") if isinstance(payload, dict) else None
    meta.update(ms=ms, cached=False, tokens=usage.get("input_tokens"), status=status, tries=tries,
                backend_ms=backend)
    # backend_ms: the service's own time; ms - backend_ms is the network, the edge and our client
    entry = {"use": use, "hash": h, "ok": ok, "cached": False, "ms": ms, "backend_ms": backend,
             "status": status, "tries": tries, "tokens": usage.get("input_tokens")}
    if ok:
        entry["answers"] = _compact(answers)
    else:
        entry["err"] = str(payload.get("error") or payload.get("detail") or payload)[:300]
        meta.update(err=entry["err"])
    _log(entry)
    if not ok:
        return None
    if cache:
        _cache_put(h, {"answers": answers, "ms": ms, "usage": usage, "model": payload.get("model")})
    return answers


def ask_many(use: str, jobs: list[tuple], timeout: float | None = None, workers: int = 8,
             cache: bool = True) -> list[tuple[dict | None, dict]]:
    """[(answers | None, meta)] for [(state, questions)], in order, `workers` at a time."""
    def one(job):
        meta: dict = {}
        return ask(use, job[0], job[1], timeout=timeout, cache=cache, meta=meta), meta
    if not jobs:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as ex:
        return list(ex.map(one, jobs))


def questions(use: str) -> dict:
    """A use's questions from jev_questions.toml, as the API takes them."""
    out = {}
    for k, q in (use_cfg(use).get("questions") or {}).items():
        item = {"type": q["type"], "instructions": q["instructions"]}
        if "criteria" in q:
            item["criteria"] = q["criteria"]
        out[k] = item
    return out


def git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


# ----------------------------------------------------------- dod: turn end

# The game's Definition of done ([jev.dod]): per fact, the regex a session's shell command shows it by and,
# for the ones whose absence is flagged, the flag. A fact the game does not name is not checked (None).
DOD = studio_config.get("jev.dod", {})
COMMIT = re.compile(r"\[main ([0-9a-f]{7,40})\]")        # git commit without -q
PUSHED = re.compile(r"pushed \d+ commit\(s\)[^\n]*\n((?:[ \t]+[0-9a-f]{7,40} [^\n]*\n?)+)")  # fe_sync.py push
# A change under these paths may change what the player sees: Jev reads its diff ([jev] runtime_paths).
ENGINE = tuple(studio_config.get("jev.runtime_paths", []))
# The version every player-visible change bumps ([land.version]: its file, and the name an added line carries).
MAIN_RS = studio_config.get("land.version.file")
VERSION_NAME = studio_config.get("land.version.name", "the version")


def _fact(name: str, cmds: list[str]) -> bool | None:
    rx = DOD.get(name, {}).get("rx")
    return None if not rx else any(re.search(rx, c) for c in cmds)


def _bumped(diff: str) -> bool:
    return bool(re.search(rf"^\+.*\b{re.escape(VERSION_NAME)}\b", diff, re.M))


def shell(call: dict) -> str:
    return str(call["input"].get("command", "")) if call["name"] in ("Bash", "PowerShell") else ""


def session_facts(turns: list[dict]) -> dict:
    """What the session did, from its tool calls: code, not Jev."""
    calls = [c for t in turns for c in t["calls"]]
    commits: list[str] = []
    for c in calls:
        cmd, out = shell(c), c.get("result", "").replace("\r\n", "\n")
        found = COMMIT.findall(out) if "commit" in cmd else []
        if "fe_sync.py" in cmd:
            for block in PUSHED.findall(out):
                found += re.findall(r"^[ \t]+([0-9a-f]{7,40}) ", block, re.M)
        for h in found:
            if h not in commits:
                commits.append(h)
    return {
        "png_read": any(c["name"] == "Read" and str(c["input"].get("file_path", "")).lower().endswith(".png")
                        for c in calls),
        **{k: _fact(k, [shell(c) for c in calls]) for k in ("shot", "bench", "baseline", "regress", "debug")},
        "commits": commits,
    }


def diff_facts(commits: list[str], uncommitted: bool = True) -> dict:
    """The session's diff: its commits (the last five) and the uncommitted tree."""
    stats, patches, files, bumped = [], [], set(), (False if MAIN_RS else None)
    for h in commits[-5:]:
        r = git("show", "--stat=120", "--format=%h %s", h)
        if r.returncode != 0:
            continue
        stats.append(r.stdout.strip())
        names = git("show", "--name-only", "--format=", h).stdout.split()
        files.update(names)
        if MAIN_RS and MAIN_RS in names:
            bumped |= _bumped(git("show", "--unified=0", "--format=", h, "--", MAIN_RS).stdout)
        patches.append(git("show", "--format=", "--unified=1", h).stdout[:3000])
    if uncommitted:
        names = git("diff", "--name-only", "HEAD").stdout.split()
        if names:
            files.update(names)
            stats.append("uncommitted:\n" + git("diff", "--stat=120", "HEAD").stdout.strip())
            patches.append(git("diff", "--unified=1", "HEAD").stdout[:3000])
            if MAIN_RS and MAIN_RS in names:
                bumped |= _bumped(git("diff", "--unified=0", "HEAD", "--", MAIN_RS).stdout)
    return {"files": sorted(files), "engine": bool(ENGINE) and any(f.startswith(ENGINE) for f in files),
            "version_bumped": bumped, "stat": "\n\n".join(stats), "patch": "\n".join(patches)[:6000]}


def dod_questions() -> tuple[dict, dict]:
    qs = questions("dod")
    diff_q = {"change_kind": qs.pop("change_kind")}
    return qs, diff_q


def dod_flags(msg: dict | None, diff: dict | None, facts: dict, dfacts: dict, final: str) -> list[str]:
    """The mismatches between what the report claims (Jev) and what the
    session did (code). Empty when the report claims nothing is finished."""
    t = threshold("dod")
    if msg is None or p(msg, "claims_done") < t:
        return []
    flags = []
    visual = p(msg, "claims_visual")
    if visual >= t and not facts["png_read"]:
        flags.append(f"claims a visual or physics effect ({visual:.2f}) but no screenshot was Read this session (DoD 3)")
    if visual >= t and p(msg, "claims_player_path") < t:
        flags.append("claims a visual or physics effect but no validation through the player's path (DoD 1)")
    if facts["debug"] and p(msg, "separates_debug") < t:
        flags.append("debug scenarios ran, but the report does not say which evidence came from them (DoD 1)")
    kind = ((diff or {}).get("change_kind") or {}).get("choice") if dfacts["engine"] else None
    kp = p(diff, "change_kind")
    if kind == "player_visible" and kp >= t:
        if dfacts["version_bumped"] is False:
            flags.append(f"the diff changes what the player sees ({kp:.2f}) but {VERSION_NAME} was not bumped")
        if facts["bench"] is False:
            flags.append(DOD["bench"]["missing"])
        elif facts["bench"] and facts["baseline"] is False:
            flags.append(DOD["baseline"]["missing"])
        elif facts["bench"] and p(msg, "quotes_bench") < t:
            flags.append("a bench ran, but the report does not quote its stage, goal and memory lines (DoD 5)")
        if facts["shot"] and p(msg, "quotes_audits") < t:
            flags.append("shots were taken, but the report quotes no audits (DoD 4)")
        named = DOD.get("version", {}).get("rx")
        if named and not re.search(named, final, re.I):
            flags.append("the report names no build version")
    if kind == "pure_refactor" and kp >= t and facts["regress"] is False:
        flags.append(DOD["regress"]["missing"].format(p=kp))
    return flags


def project_dir(root: Path = ROOT) -> Path:
    import fe_tokens
    slug = re.sub(r"[:\\/ ]", "-", str(root))
    return fe_tokens.PROJECTS / slug


def current_transcript(root: Path = ROOT) -> Path | None:
    files = sorted(project_dir(root).glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    return files[-1] if files else None


def dod_check(transcript: Path, final: str | None = None, timeout: float | None = None,
              uncommitted: bool = True) -> dict:
    """{flags, facts, answers} for a transcript's last turn; `final` replaces
    the report (a task review's note)."""
    import fe_tokens
    turns = fe_tokens.turns(transcript)
    if not turns:
        return {"flags": [], "why": "no turns"}
    report = final if final is not None else turns[-1]["final"]
    if not report.strip():
        return {"flags": [], "why": "no report"}
    facts = session_facts(turns)
    if uncommitted:  # live: this turn's commits are not pushed yet
        for h in reversed(git("rev-list", "origin/main..HEAD").stdout.split()):
            if not any(h.startswith(c) for c in facts["commits"]):
                facts["commits"].append(h[:12])
    dfacts = diff_facts(facts["commits"], uncommitted=uncommitted)
    msg_q, diff_q = dod_questions()
    jobs = [({"final_message": report[:8000]}, msg_q)]
    if dfacts["engine"]:
        jobs.append(({"diff_stat": dfacts["stat"][:3000], "diff": dfacts["patch"]}, diff_q))
    got = ask_many("dod", jobs, timeout=timeout)
    msg = got[0][0]
    diff = got[1][0] if len(got) > 1 else None
    return {"flags": dod_flags(msg, diff, facts, dfacts, report), "facts": facts,
            "diff": {k: v for k, v in dfacts.items() if k not in ("stat", "patch")},
            "answers": {"message": _compact(msg), "diff": _compact(diff)}}


def hook_stop(payload: dict) -> int:
    """Stop: a note to Yotam when the report claims done without the evidence.
    Never blocks the stop; silent when the use is off or nothing is missing."""
    if not enabled("dod"):
        return 0
    try:
        path = payload.get("transcript_path")
        if not path:
            return 0
        res = dod_check(Path(path), final=payload.get("last_assistant_message") or None)
        if res["flags"]:
            print(json.dumps({"systemMessage": f"{TAG} DoD: " + "; ".join(res["flags"])}))
    except Exception:  # noqa: BLE001 -- fail open
        pass
    return 0


# ---------------------------------------------------- reversals: the docs

def masked(text: str) -> str:
    """Decision and version ids hidden: a lead must come from what an entry
    says, since an entry that names what it changes is found by a regex."""
    return re.sub(r"\b[DV]\d+\b", "D?", text)


def entry_text(e, limit: int, mask: bool) -> str:
    body = [ln for ln in e.body if not re.match(r"^\*{0,2}Status:", ln.strip(), re.I)]
    text = f"{e.title}\n" + "\n".join(body).strip()
    return (masked(text) if mask else text)[:limit]


def reversal_job(new, earlier: list, mask: bool = True) -> tuple:
    cfg = use_cfg("reversals")
    options = {e.ident: e.title[:200] for e in earlier[-254:]}
    options["none"] = cfg["none"]
    state = {"new_decision": entry_text(new, int(cfg.get("max_state_chars", 6000)), mask)}
    return state, {"changes": choice(cfg["instructions"], options)}


def top(answers: dict | None, key: str, n: int) -> list[tuple[str, float]]:
    probs = ((answers or {}).get(key) or {}).get("probabilities") or {}
    return sorted(((k, float(v)) for k, v in probs.items()), key=lambda kv: -kv[1])[:n]


def suspects_path() -> Path:
    return art_dir() / "suspects.json"


def decisions_stamp() -> str:
    import fe_docs
    h = hashlib.sha256()
    for p in fe_docs.decision_files():  # D258: one file per category
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def recorded_changes(entries: list) -> set[tuple[str, str]]:
    """Every (new, old) pair a status line records. A status can name several
    ("amended by D68, D125, D147"); `Entry.ref` is only the first."""
    pairs = set()
    for e in entries:
        if e.group in ("superseded", "amended"):
            for new in re.findall(r"\bD\d+\b", e.status):
                pairs.add((new, e.ident))
    return pairs


def suspect(limit: int | None = None) -> dict:
    """Ask, for every entry that still applies, which earlier still-applying
    entry it changes, and keep the pairs no status line records."""
    import fe_docs
    d, _ = fe_docs.load()
    d = sorted(d, key=lambda e: e.num)
    applies = [e for e in d if e.group in ("active", "amended")]
    recorded = recorded_changes(d)  # (new, old)
    news = applies[-limit:] if limit else applies
    jobs, pairs = [], []
    for new in news:
        earlier = [e for e in applies if e.num < new.num]
        if earlier:
            jobs.append(reversal_job(new, earlier))
            pairs.append(new)
    got = ask_many("reversals", jobs)
    t = threshold("reversals")
    by = {e.ident: e for e in d}
    leads, asked = [], 0
    for new, (ans, _meta) in zip(pairs, got):
        if ans is None:
            continue
        asked += 1
        for old, prob in top(ans, "changes", 3):
            if old == "none" or prob < t or (new.ident, old) in recorded:
                continue
            leads.append({"new": new.ident, "old": old, "p": round(prob, 3),
                          "new_title": new.title[:90], "old_title": by[old].title[:90] if old in by else ""})
    out = {"built": time.strftime("%Y-%m-%d %H:%M"), "stamp": decisions_stamp(), "asked": asked,
           "of": len(jobs), "threshold": t, "leads": sorted(leads, key=lambda x: -x["p"])}
    path = suspects_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def queue_lines() -> list[str]:
    """fe_docs.py queue's Jev section: the leads, never verdicts. [] when off."""
    if not enabled("reversals"):
        return []
    try:
        data = json.loads(suspects_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"  (no leads yet: {studio_config.tool_cmd('fe_jev.py')} suspect)"]
    lines = []
    if data.get("stamp") != decisions_stamp():
        lines.append(f"  (built {data.get('built')}; the decisions changed since: fe_jev.py suspect)")
    for lead in data.get("leads", []):
        lines.append(f"{lead['new']} may change {lead['old']} (p {lead['p']:.2f}): "
                     f"{lead['new_title'][:50]} / {lead['old_title'][:50]}")
    return lines


# --------------------------------------------------------- board: routing

def agents_roles() -> dict:
    try:
        reg = json.loads(studio_config.agents_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: "the primary agent, who owns the player's build" if v.get("primary") else "an agent"
            for k, v in reg.get("agents", {}).items()}


def modified_files(root: Path = ROOT) -> list[str]:
    r = git("status", "--porcelain", cwd=root)
    return [ln[3:].strip() for ln in r.stdout.splitlines() if ln.strip()][:40]


def board_scores(reader: str, messages: list[dict], task: str = "") -> dict | None:
    """{message id: {relevant, routine}} for the reader, or None (Jev did not
    answer in time). Each message is its own call, cached by content."""
    roles = agents_roles()
    files = modified_files()
    qs = {k: v for k, v in questions("board").items() if k in ("relevant", "routine")}
    jobs = []
    for m in messages:
        state = {"reader": f"{reader}, {roles.get(reader, 'an agent')}",
                 "reader_task": task or "none claimed", "reader_files": files,
                 "message": {"from": m["sender"], "to": m["recipient"], "kind": m["kind"],
                             "subject": m["subject"], "body": (m["body"] or "")[:1500]}}
        jobs.append((state, qs))
    budget = float(use_cfg("board").get("timeout_s", 1.0))
    got = _bounded(lambda: ask_many("board", jobs, timeout=budget), budget + 0.2)
    if not got:
        return None
    out = {}
    for m, (ans, _meta) in zip(messages, got):
        if ans is not None:
            out[m["id"]] = {"relevant": p(ans, "relevant"), "routine": p(ans, "routine")}
    return out or None


def board_order(reader: str, messages: list[dict], task: str = "") -> tuple[list[dict], list[dict], dict]:
    """(the messages that matter, relevant first; the routine ones folded; the
    scores). The messages unchanged when Jev does not answer."""
    scores = board_scores(reader, messages, task) if messages else None
    if not scores:
        return messages, [], {}
    t = threshold("board")
    routine = [m for m in messages if scores.get(m["id"], {}).get("routine", 0) >= t
               and scores[m["id"]].get("relevant", 0) < t]
    rest = [m for m in messages if m not in routine]
    rest.sort(key=lambda m: -scores.get(m["id"], {}).get("relevant", 0))
    return rest, routine, scores


def route_suggest(text: str) -> dict | None:
    """A suggested assignee and priority for a task or a thought, or None."""
    cfg = use_cfg("board").get("route", {})
    roles = agents_roles()
    options = {k: f"{k}, {v}" for k, v in roles.items()}
    options["any"] = cfg.get("any", "Any agent.")
    busy = {}
    try:
        import fe_sync
        reg = fe_sync.load_registry()
        for name, a in reg["agents"].items():
            path = Path(a.get("path", ""))
            if path.is_dir() and path.resolve() != ROOT.resolve():
                busy[name] = modified_files(path)[:15]
        busy[fe_sync.identify(ROOT, reg)[0] or "me"] = modified_files()[:15]
    except Exception:  # noqa: BLE001
        pass
    state = {"task": text[:4000], "agents_editing": busy}
    qs = {"assignee": choice(cfg.get("assignee", "Which agent should take this task?"), options),
          "priority": score(cfg.get("priority", "How urgent is this task?"), cfg.get("levels", ["low", "high"]))}
    ans = ask("board", state, qs)
    if ans is None:
        return None
    levels = len(cfg.get("levels", [])) or 4
    s = float(ans["priority"].get("score", 0))
    return {"to": ans["assignee"].get("choice"), "p": p(ans, "assignee"),
            "prio": max(0, min(3, round(levels - 1 - s))), "score": round(s, 2)}


# ------------------------------------------------------ gate: shell commands

OTHER_SEND = re.compile(r"\bgh\s+(pr|issue|release|api|repo|gist)\b|\bcurl\b|Invoke-(RestMethod|WebRequest)|"
                        r"\bfe_meshy\.py\s+(concept|model|retry)\b|\bscp\b|\brsync\b", re.I)


def sanctioned_send(cmd: str) -> bool:
    """`fe_sync.py push` is how every commit lands (D50): a chain whose only
    way off the machine is that push is not a risk worth asking about."""
    return bool(re.search(r"\bfe_sync\.py\s+push\b", cmd)) and not OTHER_SEND.search(cmd)


def safe_command(cmd: str) -> bool:
    for pat in use_cfg("gate").get("safe", []):
        try:
            if re.search(pat, cmd):
                return True
        except re.error:
            continue
    return False


def gate_check(cmd: str, bench_running=False, timeout: float | None = None) -> str | None:
    """The reason to ask Yotam before `cmd` runs, or None. Jev reads the
    command; code decides what a risk is worth asking about."""
    if not cmd.strip() or safe_command(cmd):
        return None
    ans = ask("gate", gate_state(cmd), questions("gate"), timeout=timeout)
    return gate_reasons(ans, cmd, bench_running) if ans is not None else None


def gate_state(cmd: str) -> dict:
    return {"command": cmd[:4000], "shell": "Windows (Git Bash or PowerShell)"}


def gate_reasons(ans: dict, cmd: str, bench_running=False) -> str | None:
    """Code's verdict on Jev's reading; `bench_running` a bool or a callable
    (only called when the command reads as launching the GPU)."""
    t = threshold("gate")
    why = []
    if p(ans, "rewrites_history") >= t:
        why.append(f"rewrites or throws away git history or uncommitted work ({p(ans, 'rewrites_history'):.2f})")
    if p(ans, "sends_outside") >= t and not sanctioned_send(cmd):
        why.append(f"sends something off this machine ({p(ans, 'sends_outside'):.2f})")
    # Where a delete lands is Jev's reading too: commands name their paths
    # through variables ($wt, $P) that a regex cannot follow (s4, round 1).
    if p(ans, "deletes") >= t and p(ans, "deletes_outside") >= t:
        why.append(f"deletes files outside the scratch folders ({p(ans, 'deletes_outside'):.2f})")
    if p(ans, "launches_gpu") >= t and (bench_running() if callable(bench_running) else bench_running):
        why.append(f"starts a GPU program while a bench runs ({p(ans, 'launches_gpu'):.2f})")
    return "; ".join(why) or None


def hook_gate(cmd: str, bench_running=False) -> int:
    """PreToolUse, after fe_sync's own refusals passed: ask Yotam to confirm a
    risky command. Prints nothing when the use is off or the command is fine."""
    if not enabled("gate"):
        return 0
    try:
        why = gate_check(cmd, bench_running)
    except Exception:  # noqa: BLE001
        return 0
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": f"{TAG} Jev reads this command as risky: {why}. Confirm to run it."}}))
    return 0


# ------------------------------------------------------------------- latency

def latency(n: int = 10, cold: int = 3) -> dict:
    """Where a call's time goes: one call split into DNS, TCP connect, TLS
    handshake, request to first byte and body; then `n` calls on that same
    connection kept alive, and `cold` calls through the client's own `_http`.
    Uses the real API (a one-line state); never the cache."""
    import http.client
    import socket
    import ssl
    from urllib.parse import urlsplit
    key = api_key()
    if not key:
        return {"error": "no TYPESAFE_API_KEY"}
    u = urlsplit(API)
    host, port, path = u.hostname, u.port or 443, u.path
    body = {"model": config().get("model", "jev-latest"), "state": "The build passed and the change was pushed.",
            "questions": {"q": noul("The text says the change was pushed.")}}
    data = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    pc = time.perf_counter
    ms = lambda a, b: round((b - a) * 1000, 1)  # noqa: E731
    wait = 30  # seconds: long enough to see the tail, not just that there is one
    t0 = pc()
    addr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4]
    t1 = pc()
    raw = socket.create_connection(addr[:2], timeout=wait)
    t2 = pc()
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(raw, server_hostname=host)
    t3 = pc()
    conn = http.client.HTTPSConnection(host, port, timeout=wait, context=ctx)
    conn.sock = tls
    try:
        conn.request("POST", path, body=data, headers=headers)
        resp = conn.getresponse()
    except (http.client.HTTPException, OSError) as e:
        return {"error": f"the first call failed: {type(e).__name__} after {ms(t3, pc())} ms "
                         f"(dns {ms(t0, t1)}, tcp {ms(t1, t2)}, tls {ms(t2, t3)} ms)"}
    t4 = pc()
    resp.read()
    t5 = pc()
    timing = {k: v for k, v in resp.getheaders()
              if re.search(r"time|timing|duration|latency|region|cf-ray|via|server", k, re.I)}
    out = {"ip": addr[0], "status": resp.status, "dns": ms(t0, t1), "tcp": ms(t1, t2), "tls": ms(t2, t3),
           "first_byte": ms(t3, t4), "body": ms(t4, t5), "total": ms(t0, t5), "headers": timing,
           "keep_alive": not resp.will_close}
    out["upstream"] = resp.getheader("x-envoy-upstream-service-time")
    warm, split, failed, statuses = [], [], [], {}
    for _ in range(n):
        a = pc()
        try:
            conn.request("POST", path, body=data, headers=headers)
            resp = conn.getresponse()
            resp.read()
        except (http.client.HTTPException, OSError) as e:  # a timeout or a dropped connection
            failed.append(f"{type(e).__name__} after {ms(a, pc())} ms")
            conn.close()
            conn = http.client.HTTPSConnection(host, port, timeout=wait, context=ctx)
            continue
        total = ms(a, pc())
        warm.append(total)
        statuses[resp.status] = statuses.get(resp.status, 0) + 1
        up = resp.getheader("x-envoy-upstream-service-time")
        if up and up.isdigit():
            split.append((total, int(up)))
        if resp.will_close:
            conn.close()
            conn = http.client.HTTPSConnection(host, port, timeout=wait, context=ctx)
    conn.close()
    out["warm"] = sorted(warm)
    out["split"] = split  # (total ms at the client, the backend's own ms)
    out["failed"] = failed
    out["statuses"] = statuses
    coldms = []
    for _ in range(cold):
        a = pc()
        _http(body, key, 10.0)
        coldms.append(ms(a, pc()))
    out["client"] = sorted(coldms)
    return out


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    global _transport, _config
    fails: list[str] = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            fails.append(name)

    saved_env = {k: os.environ.get(k) for k in
                 ("FE_JEV", "FE_JEV_DIR", "FE_JEV_SWITCHES", "TYPESAFE_API_KEY", *[f"FE_JEV_{u.upper()}" for u in USES])}
    tmp = Path(tempfile.mkdtemp(prefix="fe-jev-selftest-"))
    try:
        for k in saved_env:
            os.environ.pop(k, None)
        os.environ["FE_JEV_DIR"] = str(tmp / "art")
        os.environ["FE_JEV_SWITCHES"] = str(tmp / "jev.toml")

        # switches: the environment beats the file, which beats the default
        check("default: every use off", not any(enabled(u) for u in USES))
        write_switches({"dod": True})
        check("file turns dod on", enabled("dod") and resolve("dod")[1].endswith("jev.toml"))
        os.environ["FE_JEV_DOD"] = "0"
        check("FE_JEV_DOD=0 beats the file", not enabled("dod") and resolve("dod")[1] == "FE_JEV_DOD")
        del os.environ["FE_JEV_DOD"]
        os.environ["FE_JEV"] = "0"
        check("FE_JEV=0 silences a use the file turned on", not enabled("dod"))
        del os.environ["FE_JEV"]
        write_switches({"all": False, "dod": True})
        check("`all` off in the file silences dod", not enabled("dod"))
        write_switches({})

        # a missing key fails open without touching the network
        _transport = None
        os.environ["TYPESAFE_API_KEY"] = ""
        if sys.platform == "win32":
            check("no key: ask returns None", api_key() != "" or ask("dod", "x", {"q": noul("q")}) is None)
        else:
            check("no key: ask returns None", ask("dod", "x", {"q": noul("q")}) is None)

        calls = []

        def fake(body, key, timeout):
            calls.append(body)
            ans = {}
            for k, q in body["questions"].items():
                if q["type"] == "noul":
                    ans[k] = {"type": "noul", "noul": 0.9 if "finished" in q["instructions"] or "visual" in q["instructions"] else 0.1}
                elif q["type"] == "choice":
                    first = next(iter(q["criteria"]))
                    ans[k] = {"type": "choice", "choice": first, "confidence": 0.8,
                              "probabilities": {c: (0.8 if c == first else 0.2 / max(1, len(q["criteria"]) - 1))
                                                for c in q["criteria"]}}
                else:
                    ans[k] = {"type": "score", "score": 1.0, "confidence": 0.7, "probabilities": {}}
            return 200, {"model": "jev-fake", "answers": ans, "usage": {"input_tokens": 100, "output_tokens": 5}}

        _transport = fake
        a = ask("dod", {"s": 1}, {"q": noul("is it finished")})
        check("ask returns typed answers", a is not None and abs(p(a, "q") - 0.9) < 1e-9)
        n = len(calls)
        ask("dod", {"s": 1}, {"q": noul("is it finished")})
        check("the same call again is a cache hit", len(calls) == n)
        log = (tmp / "art" / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        check("every call is logged", len(log) >= 2 and json.loads(log[-1])["cached"] is True)

        def slow(body, key, timeout):
            time.sleep(2.0)
            return fake(body, key, timeout)
        _transport = slow
        t0 = time.monotonic()
        a = ask("dod", {"s": 2}, {"q": noul("q")}, timeout=0.2)
        check("a slow reply times out to None within the budget", a is None and time.monotonic() - t0 < 0.6)

        seq = [429, 200]

        def busy(body, key, timeout):
            code = seq.pop(0) if seq else 200
            return (429, {"error": "rate"}) if code == 429 else fake(body, key, timeout)
        _transport = busy
        check("a 429 is retried inside the budget", ask("dod", {"s": 3}, {"q": noul("q")}, timeout=2.0) is not None)

        def broken(body, key, timeout):
            return 200, {"answers": {"other": {}}}
        _transport = broken
        check("a reply missing an answer is None", ask("dod", {"s": 4}, {"q": noul("q")}) is None)

        # the kept-alive pool: reuse, a dropped connection reopened, the backend's time
        global _connection
        import http.client

        class FakeResp:
            status, will_close = 200, False

            def read(self):
                return json.dumps({"answers": {"q": {"type": "noul", "noul": 0.5}}}).encode()

            def getheader(self, k, d=None):
                return "42" if k == "x-envoy-upstream-service-time" else d

        class FakeConn:
            made = 0

            def __init__(self, host, port, timeout):
                FakeConn.made += 1
                self.sock, self.timeout, self.dead = None, timeout, False

            def request(self, *a, **kw):
                if self.dead:
                    raise http.client.RemoteDisconnected("closed while idle")

            def getresponse(self):
                return FakeResp()

            def close(self):
                pass

        _connection, saved_pool = FakeConn, _pool[:]
        _pool.clear()
        _http({}, "k", 1.0)
        status, payload = _http({}, "k", 1.0)
        check("a second call reuses the kept-alive connection", FakeConn.made == 1 and status == 200)
        check("the backend's own time comes back", payload.get("_backend_ms") == 42)
        _pool[0].dead = True
        status, _ = _http({}, "k", 1.0)
        check("a connection dropped while idle is reopened once", status == 200 and FakeConn.made == 2)
        _connection = None
        _pool[:] = saved_pool

        # dod flags: code facts against Jev's claims
        _transport = fake
        msg = {"claims_done": {"noul": 0.95}, "claims_visual": {"noul": 0.9}, "claims_player_path": {"noul": 0.2},
               "quotes_bench": {"noul": 0.1}, "quotes_audits": {"noul": 0.1}, "separates_debug": {"noul": 0.1}}
        diff = {"change_kind": {"choice": "player_visible", "confidence": 0.9}}
        facts = {"png_read": False, "shot": True, "bench": False, "baseline": False, "regress": False,
                 "debug": False, "commits": []}
        dfacts = {"engine": True, "version_bumped": False}
        saved_dod = DOD, VERSION_NAME   # a Definition of done of its own, whatever the game's (studio.toml [jev.dod])
        globals().update(VERSION_NAME="THE_VERSION", DOD={"bench": {"rx": "--bench", "missing": "no --bench ran"}})
        flags = dod_flags(msg, diff, facts, dfacts, "done, pushed")
        check("dod: no screenshot Read is flagged", any("no screenshot" in f for f in flags))
        check("dod: no version bump is flagged", any("THE_VERSION" in f for f in flags))
        check("dod: no bench is flagged", any("--bench" in f for f in flags))
        check("dod: nothing flagged when nothing is claimed done",
              dod_flags({**msg, "claims_done": {"noul": 0.1}}, diff, facts, dfacts, "") == [])
        check("dod: a docs change skips the engine flags",
              not any("THE_VERSION" in f for f in dod_flags(msg, diff, facts, {**dfacts, "engine": False}, "")))
        check("dod: a fact the game does not name is not flagged",
              not any("--bench" in f for f in dod_flags(msg, diff, {**facts, "bench": None}, dfacts, "")))
        globals().update(DOD=saved_dod[0], VERSION_NAME=saved_dod[1])

        # hooks print nothing with their use off
        import contextlib
        import io
        buf = io.StringIO()
        n = len(calls)
        with contextlib.redirect_stdout(buf):
            hook_stop({"transcript_path": str(tmp / "none.jsonl")})
            hook_gate("rm -rf C:/Users")
        check("hooks are silent with their use off", buf.getvalue() == "")
        check("... and make no call", len(calls) == n)

        # gate: the safe list skips Jev; a risky reading asks
        write_switches({"gate": True})

        def risky(body, key, timeout):
            calls.append(body)
            outside = "engine/artifacts" not in body["state"]["command"]
            hi = {"deletes"} | ({"deletes_outside"} if outside else set())
            return 200, {"answers": {k: {"type": "noul", "noul": 0.95 if k in hi else 0.05}
                                     for k in body["questions"]}, "usage": {}}
        _transport = risky
        n = len(calls)
        check("a safe command never reaches Jev", gate_check("git status") is None and len(calls) == n)
        check("a delete outside scratch asks", "deletes" in (gate_check("Remove-Item -Recurse C:/Users/x/src") or ""))
        check("a delete inside engine/artifacts does not",
              gate_check("Remove-Item -Recurse engine/artifacts/old") is None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hook_gate("Remove-Item -Recurse D:/Source/other")
        out = json.loads(buf.getvalue() or "{}")
        check("the gate hook asks, never denies",
              out.get("hookSpecificOutput", {}).get("permissionDecision") == "ask")
    finally:
        _transport = None
        _config = None
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print(f"{TAG} selftest: {'all passed' if not fails else f'{len(fails)} failed'}")
    return 1 if fails else 0


# ----------------------------------------------------------------------- cli

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fe_jev.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("on", "off"):
        s = sub.add_parser(name)
        s.add_argument("use", choices=("all",) + USES)
    s = sub.add_parser("ask")
    s.add_argument("--state")
    s.add_argument("--state-file")
    s.add_argument("--noul", action="append", default=[])
    s.add_argument("--choice", nargs=2, action="append", default=[], metavar=("QUESTION", "A,B,C"))
    s.add_argument("--no-cache", action="store_true")
    s = sub.add_parser("dod")
    s.add_argument("--transcript")
    s.add_argument("--note")
    s = sub.add_parser("suspect")
    s.add_argument("--limit", type=int)
    s = sub.add_parser("route")
    s.add_argument("note", nargs="?")
    s.add_argument("--text")
    s = sub.add_parser("gate")
    s.add_argument("--cmd", dest="command", required=True)
    sub.add_parser("hook-stop")
    sub.add_parser("selftest")
    s = sub.add_parser("latency")
    s.add_argument("n", nargs="?", type=int, default=10)
    a = ap.parse_args(argv)

    if a.cmd == "status":
        print("\n".join(status_lines()))
        return 0
    if a.cmd in ("on", "off"):
        values = read_switches()
        if a.use == "all" and a.cmd == "on":
            values.update({"all": True, **{u: True for u in USES}})
        else:
            values[a.use] = a.cmd == "on"
        print(f"{TAG} {a.use} {a.cmd} in {write_switches(values)}")
        print("\n".join(status_lines()))
        return 0
    if a.cmd == "ask":
        state = a.state if a.state is not None else Path(a.state_file).read_text(encoding="utf-8") \
            if a.state_file else None
        if state is None:
            ap.error("ask needs --state or --state-file")
        qs = {f"n{i + 1}": noul(q) for i, q in enumerate(a.noul)}
        for i, (q, opts) in enumerate(a.choice):
            qs[f"c{i + 1}"] = choice(q, {o.strip(): o.strip() for o in opts.split(",") if o.strip()})
        if not qs:
            ap.error("ask needs a --noul or a --choice")
        meta: dict = {}
        ans = ask("ask", state, qs, timeout=30.0, cache=not a.no_cache, meta=meta)
        if ans is None:
            print(f"{TAG} no answer: {meta.get('err', 'unknown')}")
            return 1
        for k, q in qs.items():
            got = ans[k]
            if q["type"] == "noul":
                print(f"{k}  {got['noul']:.3f}  {q['instructions']}")
            else:
                probs = ", ".join(f"{c} {v:.2f}" for c, v in top(ans, k, 5))
                print(f"{k}  {got.get('choice')} ({got.get('confidence', 0):.2f})  [{probs}]  {q['instructions']}")
        print(f"{TAG} {meta.get('ms')} ms{' (cached)' if meta.get('cached') else ''}, "
              f"{meta.get('tokens')} input tokens")
        return 0
    if a.cmd == "dod":
        path = Path(a.transcript) if a.transcript else current_transcript()
        if not path or not path.exists():
            print(f"{TAG} no transcript for {ROOT}")
            return 1
        res = dod_check(path, final=a.note, timeout=10.0)
        print(json.dumps({k: v for k, v in res.items() if k != "flags"}, indent=1))
        for f in res["flags"]:
            print(f"{TAG} DoD: {f}")
        if not res["flags"]:
            print(f"{TAG} DoD: nothing missing{' (' + res['why'] + ')' if res.get('why') else ''}")
        return 0
    if a.cmd == "suspect":
        out = suspect(a.limit)
        print(f"{TAG} asked {out['asked']} of {out['of']} entries; {len(out['leads'])} lead(s) "
              f"at p >= {out['threshold']} -> {suspects_path()}")
        for lead in out["leads"][:40]:
            print(f"  {lead['new']} may change {lead['old']} (p {lead['p']:.2f}): {lead['new_title'][:60]}")
        return 0
    if a.cmd == "route":
        text = a.text
        if a.note:
            import fe_board
            with fe_board.Board() as b:
                row = b.q1("SELECT body FROM notes WHERE id=?", int(a.note.lstrip("Nn")))
            text = row["body"] if row else None
        if not text:
            ap.error("route needs a note (N3) or --text")
        got = route_suggest(text)
        print(f"{TAG} no answer" if got is None else
              f"{TAG} suggests: to {got['to']} ({got['p']:.2f}), P{got['prio']} (score {got['score']})")
        return 0 if got else 1
    if a.cmd == "gate":
        why = gate_check(a.command, timeout=10.0)
        print(f"{TAG} {'ask: ' + why if why else 'fine'}")
        return 0
    if a.cmd == "hook-stop":
        import fe_sync
        return hook_stop(fe_sync.read_payload())
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "latency":
        out = latency(a.n)
        if "error" in out:
            print(f"{TAG} {out['error']}")
            return 1
        print(f"{TAG} {API} -> {out['ip']} (HTTP {out['status']}, keep-alive {'yes' if out['keep_alive'] else 'no'})")
        print(f"  one call, cold:  dns {out['dns']} + tcp {out['tcp']} + tls {out['tls']} + "
              f"request to first byte {out['first_byte']} + body {out['body']} = {out['total']} ms")
        print(f"  the backend's own time on that call (x-envoy-upstream-service-time): {out['upstream']} ms")
        w = out["warm"]
        if w:
            codes = ", ".join(f"HTTP {k} x{v}" for k, v in sorted(out.get("statuses", {}).items()))
            print(f"  same connection, {len(w)} calls ({codes}): min {w[0]}  p50 {w[len(w) // 2]}  max {w[-1]} ms")
        sp = sorted(out.get("split", []), key=lambda x: x[1])
        if sp:
            back = [b for _, b in sp]
            travel = sorted(t - b for t, b in sp)
            print(f"    of which the backend: min {back[0]}  p50 {back[len(back) // 2]}  max {back[-1]} ms; "
                  f"the rest (network, edge, queue): min {round(travel[0])}  p50 {round(travel[len(travel) // 2])}"
                  f"  max {round(travel[-1])} ms")
        for f in out.get("failed", []):
            print(f"    failed: {f}")
        c = out["client"]
        print(f"  fe_jev's own client, {len(c)} calls: {' '.join(str(x) for x in c)} ms")
        for k, v in out["headers"].items():
            print(f"  header {k}: {v}")
        return 0
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(TOOLS))
    sys.exit(main(sys.argv[1:]))
