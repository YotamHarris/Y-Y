#!/usr/bin/env python3
"""Codex threads, read from Codex's own records (D173).

Codex runs no hooks here (Codex Desktop logs no hook event, and the
checkouts' untracked `.codex/hooks.json` never ran), so until D173 the board
saw a Codex thread only when it happened to run a board or sync command, and
never saw its turn end. Codex keeps its own records, and this reads them
read-only:

- `CODEX_HOME/state_N.sqlite` (`~/.codex` by default), table `threads`: one
  row per thread with its cwd, name, model, `updated_at` and `rollout_path`.
  The threads Codex imported from other agents' transcripts (no
  `originator`) and its sub-agents (the parent's turn covers them) are
  skipped.
- the rollout log `rollout_path`: each turn starts with an `event_msg`
  `task_started` and ends with a `task_complete`, or a `turn_aborted` when it
  is interrupted. The last of these says whether a turn is running. The
  timestamp of the log's last line is the heartbeat. The log's modified time
  is not: Windows does not move it while Codex holds the file open.

- the same log's `token_count` events carry `rate_limits` (D243): the account's
  `primary` and `secondary` usage windows as `used_percent`, `window_minutes` and
  `resets_at`, either null, plus `plan_type` and `credits`. `rate_limits()` takes the
  freshest of the newest threads' logs, for the manager's status.

Every read fails open: no Codex, a locked store or a changed format gives
fewer threads, never an error.

    python engine/tools/fe_codex.py        # the recent threads and their state
"""
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

RECENT = 86400  # threads updated in the last day
# A turn counts as running this long after its last logged line. A tool call
# is logged when it is issued and again when it returns, so a single long
# command (a bench, a release build) is the longest silence; a turn that
# died with the app never logs its end and ages out here.
QUIET = 1800
TURN = {"task_started": "working", "task_complete": "idle", "turn_aborted": "idle"}
CHUNK = 256 << 10
SCAN_LIMIT = 16 << 20  # never read more than this much of one rollout back from its end


def home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def state_db(root=None):
    """The newest `state_N.sqlite` (the N is Codex's own store version)."""
    root = root or home()
    dbs = [(int(m.group(1)), p) for p in root.glob("state_*.sqlite")
           if (m := re.fullmatch(r"state_(\d+)", p.stem))]
    return max(dbs)[1] if dbs else None


def plain_path(path):
    """Codex stores Windows paths in their extended form (\\\\?\\D:\\...)."""
    path = str(path or "")
    return path[4:] if path.startswith("\\\\?\\") else path


def stamp(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _lines_back(path, limit):
    """The non-empty lines of a file from its end backwards, reading at most `limit`
    bytes (a line cut by the limit is dropped). A file that cannot be read gives none."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = pos = f.tell()
            tail = b""
            while pos > 0 and end - pos < limit:
                step = min(CHUNK, pos)
                pos -= step
                f.seek(pos)
                lines = (f.read(step) + tail).split(b"\n")
                # The first line may start in the chunk before; keep it for then.
                tail = lines.pop(0) if pos > 0 else b""
                for line in reversed(lines):
                    if line.strip():
                        yield line
    except OSError:
        return


def turn_state(path, limit=SCAN_LIMIT):
    """(state, since, last) from a rollout: 'working' or 'idle' by its last
    turn event (None when it has none in the last `limit` bytes), when that
    event was logged, and when its last line was."""
    last = None
    for line in _lines_back(path, limit):
        head = line[:400]
        turn = b'"event_msg"' in head and any(k.encode() in head for k in TURN)
        if last is not None and not turn:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue  # a line Codex is still writing
        if last is None:
            last = stamp(d.get("timestamp"))
        kind = (d.get("payload") or {}).get("type") if d.get("type") == "event_msg" else None
        if kind in TURN:
            return TURN[kind], stamp(d.get("timestamp")), last
    return None, None, last


def threads(recent=RECENT, now=None, root=None):
    """The Codex threads updated in the last `recent` seconds, newest first:
    dicts of id ('codex:THREAD'), thread, cwd, title, model, started,
    heartbeat, state ('working' while a turn is running and its log moved in
    the last QUIET seconds, else 'idle'; a log with no turn event in its scanned
    tail counts as working too), turn ('working', 'idle' or None by the log alone)
    and turn_at (when that turn started or ended)."""
    now = now or time.time()
    try:
        db = state_db(root)
        if not db:
            return []
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM threads WHERE updated_at > ? ORDER BY updated_at DESC", (int(now - recent),))]
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []
    out = []
    for r in rows:
        if r.get("archived") or not r.get("originator") or r.get("thread_source") == "subagent":
            continue
        rollout = plain_path(r.get("rollout_path"))
        turn, turn_at, last = turn_state(rollout) if rollout else (None, None, None)
        # The log's last line, not updated_at: renaming or reopening a thread moves that.
        beat = last or max((r.get("updated_at_ms") or 0) / 1000, r.get("updated_at") or 0)
        # No turn event in the scanned tail means the turn began before it and has not
        # ended (its `task_complete` would be the log's last events): a long turn, whose
        # tool output pushed the start out of SCAN_LIMIT, is running while the log moves.
        running = turn in ("working", None) and now - beat < QUIET
        out.append({
            "id": f"codex:{r['id']}", "thread": r["id"], "kind": "codex",
            "cwd": plain_path(r.get("cwd")),
            "title": r.get("name") or r.get("title") or "",
            "model": r.get("model") or "",
            "started": (r.get("created_at_ms") or 0) / 1000 or r.get("created_at") or beat,
            "heartbeat": beat,
            "state": "working" if running else "idle",
            "turn": turn, "turn_at": turn_at,
        })
    return out


RATE_LIMIT_SCAN = 2 << 20  # of one rollout's end; a token_count with the limits comes every turn
RATE_LIMIT_FILES = 16      # the newest rollouts looked into
_seen = {}                 # path -> ((size, mtime), reading) so a quiet rollout is read once


def last_rate_limits(path, limit=RATE_LIMIT_SCAN):
    """{at, rate_limits} from the last `token_count` event of a rollout that carries
    the account's limits (`rate_limits` is null in logs Codex imported), else None."""
    for line in _lines_back(path, limit):
        if b'"rate_limits":{' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("payload") or {}
        if d.get("type") == "event_msg" and p.get("type") == "token_count" and isinstance(p.get("rate_limits"), dict):
            at = stamp(d.get("timestamp"))
            if at:
                return {"at": at, "rate_limits": p["rate_limits"]}
    return None


def recent_rollouts(now, root, days, files):
    """The rollout logs of the threads Codex ran itself (not imported ones) updated in
    the last `days` days, newest first, from its thread store; failing that, the newest
    logs under `sessions/` by modified time."""
    try:
        db = state_db(root)
        if db:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
            try:
                rows = con.execute(
                    "SELECT rollout_path FROM threads WHERE updated_at > ? AND originator IS NOT NULL "
                    "AND originator <> '' AND rollout_path IS NOT NULL ORDER BY updated_at DESC LIMIT ?",
                    (int(now - days * 86400), files)).fetchall()
            finally:
                con.close()
            return [Path(plain_path(r[0])) for r in rows]
    except (sqlite3.Error, OSError):
        pass
    found = []
    try:
        for d in range(days):
            day = (root or home()) / "sessions" / time.strftime("%Y/%m/%d", time.localtime(now - d * 86400))
            found += [(p.stat().st_mtime, p) for p in day.glob("rollout-*.jsonl")]
    except OSError:
        return []
    return [p for _, p in sorted(found, reverse=True)[:files]]


def rate_limits(now=None, root=None, days=3, files=RATE_LIMIT_FILES):
    """The freshest account usage Codex has logged ({at, rate_limits}: `primary` and
    `secondary` as {used_percent, window_minutes, resets_at}, either null, plus
    plan_type and credits). Each of the newest threads' logs is read from its end
    and the latest reading wins; a log is read again only when it has grown."""
    now = now or time.time()
    best = None
    for path in recent_rollouts(now, root, days, files):
        try:
            st = path.stat()
            key = (st.st_size, st.st_mtime)
        except OSError:
            continue
        if path not in _seen or _seen[path][0] != key:
            _seen[path] = (key, last_rate_limits(path))
        seen = _seen[path][1]
        if seen and (best is None or seen["at"] > best["at"]):
            best = seen
    return best


def ago(ts):
    s = max(0, time.time() - ts)
    return f"{s:.0f}s ago" if s < 90 else f"{s / 60:.0f} min ago" if s < 5400 else f"{s / 3600:.0f} h ago"


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    db = state_db()
    if not db:
        print(f"[fe-codex] no Codex store under {home()}")
        return 0
    rows = threads()
    print(f"[fe-codex] {db} | {len(rows)} thread(s) updated in the last {RECENT // 3600} h")
    for t in rows:
        turn = f"turn {t['turn']} {ago(t['turn_at'])}" if t["turn_at"] else "no turn logged"
        print(f"  {t['state']:<7} {t['thread']}  {t['model'] or '?':<12} last line {ago(t['heartbeat']):<11} "
              f"{turn:<24} {t['cwd']}  \"{t['title'][:50]}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
