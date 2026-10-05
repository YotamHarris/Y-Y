#!/usr/bin/env python3
"""The board: Yotam's thoughts, the agents' tasks and their messages (D116).

One SQLite store outside git, shared by every checkout on this machine
(%LOCALAPPDATA%\\BodySimulation\\board\\board.db, or FE_BOARD_DIR), a browser
UI for Yotam (`open`) and this CLI for the agents.  The hooks push each agent
its unread messages and tasks (fe_sync prompt digest, `hook-post` mid-turn).

What is waiting for me:
    fe_board.py inbox                      unread messages, my tasks, ready work
    fe_board.py next [--claim]             the top ready task I may take
    fe_board.py who                        every agent's state and sessions (D175)

Tasks (T12):
    fe_board.py task new "title" [--body TEXT | --body-file F|-] [--prio 0-3]
                     [--to any|owner|A1..] [--status idea|ready] [--from-note N3]
                     [--depends T3,T4]
    fe_board.py task list [--status S] [--mine] [--all]
    fe_board.py task show T12
    fe_board.py task claim|start|release T12
    fe_board.py task review T12 [--commit H] [--version V98] [--decision D116]
                     [--note TEXT]          done by an agent: Yotam closes it
    fe_board.py task block T12 "question for Yotam"
    fe_board.py task link T12 V98 D116 abc1234
    fe_board.py task set T12 status=ready prio=1 to=A2 title="..." depends=T3
    fe_board.py task done|drop T12          owner only

Messages (M41), to owner, A1, A2, A3 or all (every agent):
    fe_board.py msg TO "subject" [BODY] [--kind question|blocker|handoff|
                     review|fyi] [--task T12] [--body-file F|-]
    fe_board.py reply M41 [BODY] [--body-file F|-]
    fe_board.py thread M41                 the whole thread; marks it read
    fe_board.py read M41 M42 | all
    fe_board.py messages [--all]           threads I am in (--all: every one)

Thoughts (N3):
    fe_board.py note "text" [--tag a,b]
    fe_board.py notes [--all]

Images (a screenshot of a rendering issue, a shot as evidence):
    --image PATH (repeatable) on note, task new|review|block, msg, reply
    fe_board.py task attach T12 PATH [PATH..]  add images to the task's body
    fe_board.py attach PATH [PATH..]       store them; print the markdown
    Bodies show every image as a local file path: Read it to see it.

Presence:
    fe_board.py doing "text" [--task T12]  what I am on (shown to everyone)

Running it:
    fe_board.py open [--no-browser]        start the server if needed, open UI
    fe_board.py serve [--port N]           run the server in the foreground
    fe_board.py export DIR | backup | selftest

Identity is --as NAME, else the checkout path in fe_agents.json, else
FE_AGENT (D165: a session moved to another checkout keeps its old FE_AGENT
until it restarts); the browser UI is always `owner`.  digest, hook-post,
hook-stop and hook-end are hook entry points (.claude/settings.json); they
also keep the `sessions` table, which Claude session is in which checkout
(fe_sync's routing, D165).
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import studio_config  # noqa: E402  (the game this studio serves: studio.toml)
import fe_sync  # noqa: E402  (registry, identity, tree and GPU state)
import fe_codex  # noqa: E402  (Codex threads from Codex's own records, D173)

TAG = "[fe-board]"
UI_DIR = HERE / "board_ui"
SCHEMA = 3           # PRAGMA user_version; a newer store refuses an older tool
SERVER_VERSION = 18  # YYEngine: mobile build controls and delivery status; open replaces an older server
DEFAULT_PORT = studio_config.get("board.port", 45300)
BACKUPS_KEPT = 14
IDLE_AFTER = 600     # seconds without a heartbeat before "working" reads stale

STATUSES = ["idea", "ready", "claimed", "in_progress", "review", "blocked", "done", "dropped"]
ACTIVE = ("claimed", "in_progress", "blocked", "review")
HOLDS = ("claimed", "in_progress", "blocked")   # D191: a managed task holds its checkout only so
STOPS = ("idea", "ready", "done", "dropped")    # D191: Yotam moving a task here ends its worker
KINDS = ["question", "blocker", "handoff", "review", "fyi", "reply"]
NOTE_STATUSES = ["open", "converted", "archived"]
MAX_IMAGE = 25 << 20
IMAGE_MAGIC = [(b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF87a", "gif"), (b"GIF89a", "gif")]
IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
FILE_NAME = r"[0-9a-f]{20}\.(?:png|jpg|gif|webp)"
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(/files/(" + FILE_NAME + r")\)")


class BoardError(Exception):
    pass


# ----------------------------------------------------------------- places

def board_dir():
    env = os.environ.get("FE_BOARD_DIR")
    if env:
        return Path(env)
    return shared_board_dir()


def shared_board_dir():
    """The one store every checkout shares, whatever FE_BOARD_DIR says."""
    return studio_config.data_dir() / "board"


def files_dir():
    """Images live beside the store, named by their content; never deleted."""
    return board_dir() / "files"


def db_path():
    return board_dir() / "board.db"


def port():
    try:
        return int(os.environ.get("FE_BOARD_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def url():
    return f"http://127.0.0.1:{port()}"


def registry():
    return fe_sync.load_registry()


def on_trunk(head, agent=None):
    """Is HEAD on origin/main, as this checkout or the task's agent checkout last fetched it?"""
    roots = [studio_config.repo_root()]
    path = registry()["agents"].get(agent, {}).get("path") if agent else None
    if path:
        roots.append(Path(path))
    return any(Path(r).is_dir() and fe_sync.git(r, "merge-base", "--is-ancestor", head,
                                                "origin/main", timeout=20).returncode == 0
               for r in roots)


def checkout_holds(con):
    """D191: {agent: hold} for each checkout the board holds for a managed task. A task
    holds its checkout while the board has it claimed, in progress or blocked (waiting
    on Yotam, its work kept there for his answer), or while a worker of it still runs;
    nothing else does. So dropping a task, closing it or putting it back to ready frees
    its checkout at once. Takes any connection, a read-only one included."""
    try:
        rows = con.execute(
            "SELECT m.task_id, m.agent, m.phase, t.status, t.title, (SELECT r.state FROM pm_runs r "
            "WHERE r.task_id=m.task_id AND r.state IN ('queued','running') LIMIT 1) AS run "
            "FROM pm_tasks m JOIN tasks t ON t.id=m.task_id WHERE m.agent IS NOT NULL "
            "ORDER BY run IS NULL, m.task_id").fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return {}  # a board without the manager's tables holds nothing
        raise
    out = {}
    for tid, agent, phase, status, title, run in rows:
        if run or (status in HOLDS and phase != "landed"):
            out.setdefault(agent, {"task": tid, "status": status, "phase": phase, "run": run, "title": title})
    return out


def hold_text(h):
    """One line for a hold: the router's reason, `who` and the Agents view say the same."""
    why = (f"its worker is {h['run']}" if h["run"]
           else f"blocked on the board, waiting on {studio_config.owner()}" if h["status"] == "blocked"
           else f"{h['status'].replace('_', ' ')} on the board")
    return f"T{h['task']} holds it ({why}; manager phase {h['phase']})"


def agents():
    return list(registry()["agents"])


def people():
    return ["owner", "manager"] + agents()


def whoami(explicit=None, cwd=None):
    """owner, an agent name, or None when nothing says who this is."""
    names = people()
    if explicit:
        if explicit not in names:
            raise BoardError(f"unknown identity {explicit!r}: one of {', '.join(names)}")
        return explicit
    # The checkout before FE_AGENT (D165): a session routed to another
    # checkout keeps the FE_AGENT it started with until it restarts.
    here = checkout_of(cwd or os.getcwd())
    if here:
        return here
    env = os.environ.get("FE_AGENT")
    return env if env in names else None


def checkout_of(path):
    """The agent whose registered checkout holds `path`, or None."""
    here = fe_sync.norm(path)
    for name, agent in registry()["agents"].items():
        root = fe_sync.norm(agent["path"])
        if here == root or here.startswith(root + os.sep):
            return name
    return None


# ----------------------------------------------------------------- refs and time

def ref(prefix, n):
    return f"{prefix}{n}" if n is not None else ""


def parse_ref(text, prefix):
    s = str(text).strip().upper()
    if s.startswith(prefix):
        s = s[1:]
    if not s.isdigit():
        raise BoardError(f"not a {prefix}-id: {text!r} (e.g. {prefix}12)")
    return int(s)


def parse_refs(text, prefix):
    return [parse_ref(p, prefix) for p in str(text or "").replace(" ", ",").split(",") if p.strip()]


def ago(ts):
    if not ts:
        return "never"
    s = max(0, time.time() - ts)
    if s < 60:
        return f"{int(s)}s ago"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"


def image_ext(data):
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def store_image(data, name=""):
    """Save an image in files/ (the type from its bytes, the name from its
    hash, so the same screenshot twice is one file); return its markdown."""
    ext = image_ext(data)
    if not ext:
        raise BoardError("not a PNG, JPEG, GIF or WebP image")
    if len(data) > MAX_IMAGE:
        raise BoardError(f"image over {MAX_IMAGE >> 20} MB")
    fname = hashlib.sha256(data).hexdigest()[:20] + "." + ext
    d = files_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / fname
    if not dest.exists():
        part = d / f"{fname}.{os.getpid()}.{threading.get_ident()}.part"
        part.write_bytes(data)
        os.replace(part, dest)
    alt = re.sub(r"[\[\]()\r\n]+", " ", Path(name).stem if name else "").strip() or "image"
    return f"![{alt}](/files/{fname})"


# extension -> magic (one or a tuple); what store_file will keep besides images: a plan's PDF,
# and the models and animation clips Yotam sends a planning thread (Mixamo FBX, Meshy GLB)
FILE_KINDS = {"pdf": b"%PDF-", "fbx": (b"Kaydara FBX Binary", b"; FBX"), "glb": b"glTF"}
MAX_FILE = 25 << 20


def store_file(data, name):
    """Save a non-image file (an allow-listed type) under files/docs/<hash>/<name>, the
    hash from its bytes and the name kept so Discord shows a readable one; return its path.
    store_image stays the only way an image is kept."""
    ext = Path(name).suffix.lstrip(".").lower()
    magic = FILE_KINDS.get(ext)
    if not magic:
        raise BoardError("not an allowed file type (" + ", ".join(sorted(FILE_KINDS)) + ")")
    if not data.startswith(magic):
        raise BoardError(f"not a real .{ext} file")
    if len(data) > MAX_FILE:
        raise BoardError(f"file over {MAX_FILE >> 20} MB")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.") or "file"
    d = files_dir() / "docs" / hashlib.sha256(data).hexdigest()[:20]
    dest = d / f"{stem}.{ext}"
    if not dest.exists():
        d.mkdir(parents=True, exist_ok=True)
        part = d / f"{stem}.{os.getpid()}.{threading.get_ident()}.part"
        part.write_bytes(data)
        os.replace(part, dest)
    return dest


def with_images(body, paths):
    """Append the images at PATHS to a markdown body."""
    if not paths:
        return body
    marks = []
    for path in paths:
        f = Path(path)
        if not f.is_file():
            raise BoardError(f"no such image: {path}")
        marks.append(store_image(f.read_bytes(), f.name))
    return ((body or "").rstrip() + "\n\n" if (body or "").strip() else "") + "\n\n".join(marks)


def image_lines(text, indent="    "):
    """One line per image in a body: its local path, which an agent can Read."""
    return [f"{indent}[image {m.group(1)}] {files_dir() / m.group(2)}" for m in IMAGE_RE.finditer(text or "")]


def clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ----------------------------------------------------------------- store

DDL = """
CREATE TABLE notes(
  id INTEGER PRIMARY KEY, author TEXT NOT NULL, body TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
  task_id INTEGER, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE tasks(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'idea', priority INTEGER NOT NULL DEFAULT 2,
  rank REAL NOT NULL DEFAULT 0, assignee TEXT NOT NULL DEFAULT 'any',
  claimed_by TEXT, created_by TEXT NOT NULL, note_id INTEGER,
  depends TEXT NOT NULL DEFAULT '', links TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE messages(
  id INTEGER PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fyi', subject TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  reply_to INTEGER, thread INTEGER, topic TEXT, created REAL NOT NULL);
CREATE INDEX messages_thread ON messages(thread);
CREATE INDEX messages_topic ON messages(topic);
CREATE TABLE receipts(
  message_id INTEGER NOT NULL, reader TEXT NOT NULL, read_at REAL NOT NULL,
  PRIMARY KEY(message_id, reader));
CREATE TABLE presence(
  agent TEXT PRIMARY KEY, heartbeat REAL, state TEXT, task_id INTEGER,
  activity TEXT, activity_at REAL, notified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE events(
  id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
  kind TEXT NOT NULL, ref TEXT, summary TEXT);
"""

# Added beside a schema without bumping it: a tool that predates a table
# never reads it, so nothing refuses the older tool.  sessions (D165): one
# row per Claude session, in which checkout, and whether a turn is running.
ADDED_DDL = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, agent TEXT NOT NULL, state TEXT NOT NULL,
  heartbeat REAL NOT NULL, started REAL NOT NULL)
"""


def outdated_checkouts():
    """The checkouts whose fe_board.py predates this tool's schema. The shared store is
    not migrated while one of them could still open it, since a newer store refuses an
    older tool. The manager's own checkout (D181) is not in the agent registry, so the
    repo its configuration names is checked too."""
    places = {}
    for name, agent in registry()['agents'].items():
        places[str(Path(agent['path']))] = name
    try:
        repo = json.loads((board_dir() / 'manager/config.json').read_text(encoding='utf-8')).get('repo')
    except (OSError, ValueError):
        repo = None
    if repo:
        places.setdefault(str(Path(repo)), 'the manager checkout ' + repo)
    old = []
    for place, name in places.items():
        source = studio_config.studio_in(place) / 'fe_board.py'
        match = re.search(r'^SCHEMA = (\d+)', source.read_text(encoding='utf-8'), re.M) if source.exists() else None
        # Mid-rebase or mid-merge the tree holds the other side's tools only until the
        # operation aborts (A3's Stop hook, which conflicted: the store was bumped in
        # that window and A3's own tools refused it after the abort).
        busy = any((Path(place) / '.git' / f).exists() for f in ('rebase-merge', 'rebase-apply', 'MERGE_HEAD'))
        if not match or int(match[1]) < SCHEMA or busy:
            old.append(name)
    return old


def connect(path=None):
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA:
        con.close()
        raise BoardError(f"the board store is schema v{version}, this tool knows v{SCHEMA}: "
                         f"pull first ({studio_config.tool_cmd('fe_sync.py')} pull)")
    if version == 0:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("BEGIN IMMEDIATE")
        try:
            if con.execute("PRAGMA user_version").fetchone()[0] == 0:
                for stmt in DDL.split(";"):
                    if stmt.strip():
                        con.execute(stmt)
                con.execute("PRAGMA user_version=1")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    con.execute(ADDED_DDL)
    # D218: each managed run's model, tokens and time, and each task's model route: added
    # tables, which no older tool reads, so no schema bump.
    from manager_models import ensure_tables
    ensure_tables(con)
    from manager_brief import ensure_tables as ensure_asks  # D224: a worker's questions and Yotam's answers
    ensure_asks(con)
    from manager_park import ensure_tables as ensure_parked  # D297: a blocked task's parked work
    ensure_parked(con)
    # Columns added the same way, which no older tool reads (D193: a goal's name).
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_goals'").fetchone() and \
            "name" not in {c[1] for c in con.execute("PRAGMA table_info(pm_goals)")}:
        from manager_store import GOAL_NAME
        try:
            con.execute(GOAL_NAME)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):  # another process added it first
                raise
    # D181: migrate atomically. The version is not bumped while an old checkout could
    # still open the store, since a newer version refuses an older tool. Every step so
    # far only adds tables and defaulted columns, which an older tool never reads, so
    # the steps themselves are applied at once and the bump waits: this tool keeps
    # working meanwhile. Raising instead deadlocked the manager: a leased checkout may
    # not pull, and its GPU wrapper and board calls failed until it did.
    if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA:
        shared = shared_board_dir() / 'board.db'
        old = outdated_checkouts() if path.resolve() == shared.resolve() else []
        from manager_store import DDL as MANAGER_DDL, MIGRATE_V3
        con.execute("BEGIN IMMEDIATE")
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA:
                if version < 2:
                    # A store that never held the manager's tables gets them at their
                    # current shape, so no later step applies to them.
                    for stmt in MANAGER_DDL.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ').replace(
                            'INSERT INTO ', 'INSERT OR IGNORE INTO ').split(';'):
                        if stmt.strip():
                            con.execute(stmt)
                elif version < 3:
                    # Idempotent: a deferred migration may already have added them.
                    have = {c[1] for c in con.execute('PRAGMA table_info(pm_providers)')}
                    for stmt in MIGRATE_V3:
                        if stmt.split()[5] not in have:
                            con.execute(stmt)
                if not old:
                    con.execute(f"PRAGMA user_version={SCHEMA}")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            con.close()
            raise
    # D226: goals as planning conversations; after the migration, which may have just made pm_goals
    from manager_talk import ensure_tables as ensure_talk
    ensure_talk(con)
    return con


def row(r, prefix):
    if r is None:
        return None
    d = dict(r)
    d["ref"] = ref(prefix, d["id"])
    if prefix == "T":
        d["links"] = json.loads(d.get("links") or "[]")
        d["depends"] = [int(x) for x in (d.get("depends") or "").split(",") if x]
    return d


class Board:
    def __init__(self, path=None):
        self.con = connect(path)

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextlib.contextmanager
    def tx(self):
        # Compose board actions and durable manager inbox/outbox atomically.
        if self.con.in_transaction:
            yield self.con
            return
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield self.con
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise

    def land_pushed(self, tid):
        """A managed task whose commit is on origin/main has landed, however it got there
        (a failed run whose push went through, a hand push). Record it so Yotam can test
        and accept it; True when the task is landed afterwards."""
        m = self.q1('SELECT phase,head,agent FROM pm_tasks WHERE task_id=?', tid)
        if not m:
            return False
        if m['phase'] == 'landed':
            return True
        if not m['head'] or not on_trunk(m['head'], m['agent']):
            return False
        with self.tx() as c:
            c.execute("UPDATE pm_tasks SET phase='landed',landed_head=head WHERE task_id=?", (tid,))
        return True

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def q1(self, sql, *args):
        return self.con.execute(sql, args).fetchone()

    def event(self, actor, kind, ref_, summary):
        self.con.execute("INSERT INTO events(at, actor, kind, ref, summary) VALUES(?,?,?,?,?)",
                         (time.time(), actor, kind, ref_, clip(summary, 160)))

    def last_event(self):
        return self.q1("SELECT COALESCE(MAX(id), 0) FROM events")[0]

    # -- notes

    def note(self, nid):
        r = row(self.q1("SELECT * FROM notes WHERE id=?", nid), "N")
        if not r:
            raise BoardError(f"no such thought N{nid}")
        return r

    def add_note(self, actor, body, tags=""):
        body = (body or "").strip()
        if not body:
            raise BoardError("a thought needs text")
        now = time.time()
        with self.tx() as c:
            nid = c.execute("INSERT INTO notes(author, body, tags, created, updated) VALUES(?,?,?,?,?)",
                            (actor, body, norm_tags(tags), now, now)).lastrowid
            self.event(actor, "note.new", ref("N", nid), body)
        return nid

    def update_note(self, actor, nid, **fields):
        self.note(nid)
        sets = {}
        if "body" in fields:
            sets["body"] = (fields["body"] or "").strip()
        if "tags" in fields:
            sets["tags"] = norm_tags(fields["tags"])
        if "status" in fields:
            if fields["status"] not in NOTE_STATUSES:
                raise BoardError(f"thought status is one of {', '.join(NOTE_STATUSES)}")
            sets["status"] = fields["status"]
        if not sets:
            return
        sets["updated"] = time.time()
        with self.tx() as c:
            c.execute(f"UPDATE notes SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                      (*sets.values(), nid))
            self.event(actor, "note.update", ref("N", nid), ", ".join(f"{k}" for k in sets if k != "updated"))

    # -- tasks

    def task(self, tid):
        r = row(self.q1("SELECT * FROM tasks WHERE id=?", tid), "T")
        if not r:
            raise BoardError(f"no such task T{tid}")
        return r

    def tasks(self, where="1", *args):
        return [row(r, "T") for r in
                self.q(f"SELECT * FROM tasks WHERE {where} ORDER BY priority, rank, id", *args)]

    def add_task(self, actor, title, body="", priority=2, assignee="any", status="idea",
                 note_id=None, depends=(), links=()):
        title = (title or "").strip()
        if not title:
            raise BoardError("a task needs a title")
        check_priority(priority)
        check_assignee(assignee)
        if status not in ("idea", "ready"):
            raise BoardError("a new task starts as idea or ready")
        if note_id is not None:
            self.note(note_id)
        for d in depends:
            self.task(d)
        now = time.time()
        with self.tx() as c:
            rank = c.execute("SELECT COALESCE(MAX(rank), 0) + 1 FROM tasks").fetchone()[0]
            tid = c.execute(
                "INSERT INTO tasks(title, body, status, priority, rank, assignee, created_by, note_id,"
                " depends, links, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (title, body or "", status, int(priority), rank, assignee, actor, note_id,
                 ",".join(str(d) for d in depends), json.dumps(list(links)), now, now)).lastrowid
            if note_id is not None:
                c.execute("UPDATE notes SET status='converted', task_id=?, updated=? WHERE id=?",
                          (tid, now, note_id))
            self.event(actor, "task.new", ref("T", tid), title)
        return tid

    def update_task(self, actor, tid, **fields):
        t = self.task(tid)
        sets = {}
        for key, value in fields.items():
            if key == "title":
                if not str(value).strip():
                    raise BoardError("a task needs a title")
                sets["title"] = str(value).strip()
            elif key == "body":
                sets["body"] = str(value or "")
            elif key == "priority":
                check_priority(value)
                sets["priority"] = int(value)
            elif key == "assignee":
                check_assignee(value)
                sets["assignee"] = value
            elif key == "rank":
                sets["rank"] = float(value)
            elif key == "depends":
                deps = value if isinstance(value, list) else parse_refs(value, "T")
                for d in deps:
                    if d == tid:
                        raise BoardError("a task cannot depend on itself")
                    self.task(d)
                sets["depends"] = ",".join(str(d) for d in deps)
            elif key == "links":
                sets["links"] = json.dumps([str(x).strip() for x in value if str(x).strip()])
            elif key == "status":
                if value not in STATUSES:
                    raise BoardError(f"task status is one of {', '.join(STATUSES)}")
                if value == "done" and actor != "owner":
                    raise BoardError(f"only {studio_config.owner()} closes a task: set it to review "
                                     f"(fe_board.py task review T{tid})")
                if value == 'done':
                    managed = self.q1('SELECT phase,head FROM pm_tasks WHERE task_id=?', tid)
                    if managed and managed[0] != 'landed' and not self.land_pushed(tid):
                        where = (f"commit {managed[1][:9]} is not on origin/main yet" if managed[1]
                                 else "it has no commit yet")
                        raise BoardError(f"T{tid} cannot be accepted: {where} (manager phase "
                                         f"{managed[0]}). Reply on the task to send it back.")
                sets["status"] = value
                if value in ("idea", "ready"):
                    sets["claimed_by"] = None
                elif value in ("claimed", "in_progress") and not t["claimed_by"] and actor != "owner":
                    sets["claimed_by"] = actor
            else:
                raise BoardError(f"unknown task field {key!r}")
        if not sets:
            return
        sets["updated"] = time.time()
        with self.tx() as c:
            c.execute(f"UPDATE tasks SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                      (*sets.values(), tid))
            what = ", ".join(f"{k}={sets[k]}" if k in ("status", "priority", "assignee") else k
                             for k in sets if k not in ("updated", "claimed_by", "rank"))
            if what:
                self.event(actor, "task.update", ref("T", tid), f"{t['title']}: {what}")
            else:
                self.event(actor, "task.move", ref("T", tid), t["title"])
            # The manager's own status writes follow its phases; anyone else's lead them.
            managed = actor != "manager" and "status" in sets and self.follow_status(tid, sets["status"])
        if managed and sets["status"] in STOPS:
            self.end_runs(tid, sets["status"])

    def follow_status(self, tid, status):
        """D191: the status set on a managed task is what the manager does with it, at
        once: out of HOLDS its checkout is free; back to ready or idea it starts over; a
        failed or blocked task set in progress resumes, as a reply to it does. True when
        the task is managed."""
        try:
            m = self.q1("SELECT phase FROM pm_tasks WHERE task_id=?", tid)
        except sqlite3.OperationalError:
            return False  # a board without the manager's tables
        if not m:
            return False
        if status not in HOLDS:
            self.con.execute("DELETE FROM pm_leases WHERE owner=?", (f"task:{tid}",))
        if status in ("idea", "ready") and m["phase"] != "landed":
            self.con.execute("UPDATE pm_tasks SET phase='ready',attempts=0 WHERE task_id=?", (tid,))
        elif status in ("claimed", "in_progress") and m["phase"] in ("failed", "blocked"):
            self.con.execute("UPDATE pm_tasks SET phase='revise',attempts=0 WHERE task_id=?", (tid,))
        return True

    def end_runs(self, tid, status):
        """End a task's live workers once it has left the work (D191). Without psutil
        here (the server's Python) the runs stay live and the manager's reconcile ends
        them on its next tick."""
        runs = self.q("SELECT * FROM pm_runs WHERE task_id=? AND state IN ('queued','running')", tid)
        if not runs:
            return
        try:
            import manager_workers
            import psutil  # noqa: F401  (owned_process needs it)
        except ImportError:
            return
        for r in runs:
            manager_workers.stop_owned(dict(r), self)
            with self.tx() as c:
                c.execute("UPDATE pm_runs SET state='handled',error=? WHERE id=?",
                          (f"Stopped: T{tid} was set to {status} on the board", r["id"]))
            self.event("manager", "manager.stopped", ref("T", tid), f"worker ended: the task is {status}")

    def add_links(self, actor, tid, links):
        t = self.task(tid)
        merged = t["links"] + [x for x in links if x and x not in t["links"]]
        self.update_task(actor, tid, links=merged)

    def deps_done(self, t):
        for d in t["depends"]:
            r = self.q1("SELECT status FROM tasks WHERE id=?", d)
            if r and r[0] not in ("done", "dropped"):
                return False
        return True

    def claim(self, actor, tid):
        """True when this call took the task (or actor already holds it)."""
        now = time.time()
        with self.tx() as c:
            t = self.task(tid)
            if t["claimed_by"] == actor and t["status"] in ACTIVE:
                return True
            if not self.deps_done(t):
                raise BoardError(f"T{tid} waits on {', '.join(ref('T', d) for d in t['depends'])}")
            n = c.execute("UPDATE tasks SET status='claimed', claimed_by=?, updated=? "
                          "WHERE id=? AND status='ready' AND (assignee='any' OR assignee=?)",
                          (actor, now, tid, actor)).rowcount
            if n:
                self.event(actor, "task.claim", ref("T", tid), t["title"])
                c.execute("INSERT INTO presence(agent, task_id) VALUES(?, ?) "
                          "ON CONFLICT(agent) DO UPDATE SET task_id=excluded.task_id", (actor, tid))
        return bool(n)

    def next_task(self, actor):
        for t in self.tasks("status='ready' AND (assignee='any' OR assignee=?)", actor):
            if self.deps_done(t):
                return t
        return None

    # -- messages

    def message(self, mid):
        r = row(self.q1("SELECT * FROM messages WHERE id=?", mid), "M")
        if not r:
            raise BoardError(f"no such message M{mid}")
        return r

    def add_message(self, actor, to, subject, body="", kind="fyi", reply_to=None, topic=None):
        parent = self.message(reply_to) if reply_to else None
        if parent:
            if not to:
                to = parent["recipient"] if parent["sender"] == actor else parent["sender"]
                if to == "all" and parent["sender"] != actor:
                    to = parent["sender"]
            subject = subject or parent["subject"]
            topic = topic or parent["topic"]
            kind = kind or "reply"
        kind = kind or "fyi"
        if to not in people() + ["all"]:
            raise BoardError(f"send to one of {', '.join(people() + ['all'])}")
        if to == actor:
            raise BoardError("that is you")
        if kind not in KINDS:
            raise BoardError(f"message kind is one of {', '.join(KINDS)}")
        subject = (subject or "").strip()
        if not subject:
            raise BoardError("a message needs a subject")
        if topic:
            topic = topic.strip().upper()
            if topic[:1] == "T":
                self.task(parse_ref(topic, "T"))
            elif topic[:1] == "N":
                self.note(parse_ref(topic, "N"))
            else:
                raise BoardError("a message's topic is a task (T12) or a thought (N3)")
        now = time.time()
        with self.tx() as c:
            mid = c.execute(
                "INSERT INTO messages(sender, recipient, kind, subject, body, reply_to, thread, topic,"
                " created) VALUES(?,?,?,?,?,?,?,?,?)",
                (actor, to, kind, subject, body or "", reply_to,
                 parent["thread"] if parent else None, topic, now)).lastrowid
            if not parent:
                c.execute("UPDATE messages SET thread=? WHERE id=?", (mid, mid))
            if parent:  # answering a thread reads it
                c.execute("INSERT OR IGNORE INTO receipts(message_id, reader, read_at) "
                          "SELECT id, ?, ? FROM messages WHERE thread=? AND id<?",
                          (actor, now, parent["thread"], mid))
            self.event(actor, "msg.new", ref("M", mid), f"{actor} -> {to} ({kind}): {subject}")
        return mid

    def thread(self, mid):
        m = self.message(mid)
        return [row(r, "M") for r in self.q("SELECT * FROM messages WHERE thread=? ORDER BY id", m["thread"])]

    def for_reader(self, reader):
        """SQL condition: messages addressed to reader."""
        return ("(recipient=? OR (recipient='all' AND sender<>? AND ?<>'owner'))", (reader, reader, reader))

    def unread(self, reader, above=0):
        cond, args = self.for_reader(reader)
        return [row(r, "M") for r in self.q(
            f"SELECT * FROM messages WHERE {cond} AND id>? AND NOT EXISTS("
            "SELECT 1 FROM receipts WHERE message_id=messages.id AND reader=?) ORDER BY id",
            *args, above, reader)]

    def mark_read(self, reader, ids):
        now = time.time()
        with self.tx() as c:
            c.executemany("INSERT OR IGNORE INTO receipts(message_id, reader, read_at) VALUES(?,?,?)",
                          [(i, reader, now) for i in ids])

    def open_questions(self):
        """Questions and blockers to the owner with no owner answer after them."""
        out = []
        for m in self.q("SELECT * FROM messages WHERE recipient='owner' AND kind IN ('question','blocker')"):
            answered = self.q1("SELECT 1 FROM messages WHERE thread=? AND id>? AND sender='owner'",
                               m["thread"], m["id"])
            if not answered:
                out.append(row(m, "M"))
        return out

    # -- presence

    def touch(self, agent, state=None, activity=None, task_id=None, throttle=0):
        now = time.time()
        cur = self.q1("SELECT * FROM presence WHERE agent=?", agent)
        if throttle and cur and cur["heartbeat"] and now - cur["heartbeat"] < throttle \
                and state in (None, cur["state"]) and activity is None:
            return
        with self.tx() as c:
            c.execute("INSERT INTO presence(agent, heartbeat, state) VALUES(?,?,?) "
                      "ON CONFLICT(agent) DO UPDATE SET heartbeat=excluded.heartbeat, "
                      "state=COALESCE(excluded.state, presence.state)", (agent, now, state))
            if activity is not None:
                c.execute("UPDATE presence SET activity=?, activity_at=?, task_id=? WHERE agent=?",
                          (activity, now, task_id, agent))
                self.event(agent, "agent.doing", ref("T", task_id) if task_id else agent, activity)
            elif state and (not cur or cur["state"] != state):
                self.event(agent, "agent.state", agent, state)

    def presence(self):
        return {r["agent"]: dict(r) for r in self.q("SELECT * FROM presence")}

    def set_notified(self, agent, mid):
        with self.tx() as c:
            c.execute("INSERT INTO presence(agent, notified) VALUES(?, ?) "
                      "ON CONFLICT(agent) DO UPDATE SET notified=MAX(presence.notified, excluded.notified)",
                      (agent, mid))

    # -- sessions (D165): which Claude session is in which checkout

    def session_touch(self, sid, agent, state, throttle=0):
        """A hook saw session `sid` in `agent`'s checkout: working (a turn is
        running) or idle (its turn ended).  Rows a day old are dropped."""
        if not sid or not agent:
            return
        now = time.time()
        if throttle:
            cur = self.q1("SELECT * FROM sessions WHERE id=?", sid)
            if cur and cur["agent"] == agent and cur["state"] == state and now - cur["heartbeat"] < throttle:
                return
        with self.tx() as c:
            c.execute("INSERT INTO sessions(id, agent, state, heartbeat, started) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET agent=excluded.agent, state=excluded.state, "
                      "heartbeat=excluded.heartbeat", (sid, agent, state, now, now))
            c.execute("DELETE FROM sessions WHERE heartbeat < ?", (now - 86400,))

    def session_end(self, sid):
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))

    def sessions(self):
        return [dict(r) for r in self.q("SELECT * FROM sessions ORDER BY heartbeat DESC")]

    # -- everything, for the UI and export

    def snapshot(self, reader="owner"):
        read = {r[0] for r in self.q("SELECT message_id FROM receipts WHERE reader=?", reader)}
        msgs = [row(r, "M") for r in self.q("SELECT * FROM messages ORDER BY id")]
        cond, args = self.for_reader(reader)
        mine = {r[0] for r in self.q(f"SELECT id FROM messages WHERE {cond}", *args)}
        for m in msgs:
            m["unread"] = m["id"] in mine and m["id"] not in read
        return {
            "me": reader,
            "people": people(),
            "statuses": STATUSES,
            "kinds": KINDS,
            "notes": [row(r, "N") for r in self.q("SELECT * FROM notes ORDER BY id DESC")],
            "tasks": self.tasks(),
            "messages": msgs,
            "open_questions": [m["id"] for m in self.open_questions()],
            "presence": self.presence(),
            "events": [dict(r) for r in self.q("SELECT * FROM events ORDER BY id DESC LIMIT 60")],
            "last_event": self.last_event(),
            "now": time.time(),
        }


def norm_tags(tags):
    if isinstance(tags, (list, tuple)):
        tags = ",".join(tags)
    return ",".join(t.strip().lower().lstrip("#") for t in str(tags or "").split(",") if t.strip())


def check_priority(p):
    if str(p) not in ("0", "1", "2", "3"):
        raise BoardError("priority is 0 (now) .. 3 (someday)")


def check_assignee(a):
    if a not in ["any"] + people():
        raise BoardError(f"assign to one of any, {', '.join(people())}")


# ----------------------------------------------------------------- CLI output

def task_line(t, board=None):
    who = t["claimed_by"] or t["assignee"]
    extra = []
    if t["depends"]:
        extra.append("after " + ",".join(ref("T", d) for d in t["depends"]))
    if t["links"]:
        extra.append(" ".join(t["links"][:4]))
    tail = f"  ({'; '.join(extra)})" if extra else ""
    return f"{t['ref']:<5} P{t['priority']} {t['status']:<11} {who:<5} {clip(t['title'], 70)}{tail}"


def msg_line(m, unread=False):
    topic = f" [{m['topic']}]" if m.get("topic") else ""
    flag = "  *unread*" if unread else ""
    return (f"{m['ref']:<5} {m['sender']} -> {m['recipient']:<5} {m['kind']:<8} "
            f"{clip(m['subject'], 60)}{topic}  {ago(m['created'])}{flag}")


def print_message(m):
    print(msg_line(m))
    for line in (m["body"] or "").strip().splitlines():
        print("    " + line)
    for line in image_lines(m["body"]):
        print(line)


def read_body(args):
    f = getattr(args, "body_file", None)
    if f:
        if f == "-":
            return sys.stdin.read()
        return Path(f).read_text(encoding="utf-8")
    return getattr(args, "body", None) or ""


# ----------------------------------------------------------------- commands

def need_me(args):
    me = whoami(getattr(args, "who", None))
    if not me:
        raise BoardError("who are you? set FE_AGENT or pass --as NAME "
                         f"({', '.join(people())})")
    return me


def cmd_inbox(b, me, args):
    if me == "owner":
        qs = b.open_questions()
        print(f"{TAG} open questions for {studio_config.owner()}: {len(qs)}")
        for m in qs:
            print("  " + msg_line(m))
            if m["body"]:
                print("        " + clip(m["body"], 200))
        rv = b.tasks("status='review'")
        print(f"{TAG} in review: {len(rv)}")
        for t in rv:
            print("  " + task_line(t))
    un = b.unread(me)
    print(f"{TAG} {me}: {len(un)} unread message(s)")
    for m in un:
        print("  " + msg_line(m))
        body = clip(m["body"], 200)
        if body:
            print("        " + body)
    mine = b.tasks(f"(claimed_by=? AND status IN {ACTIVE}) OR (assignee=? AND status IN ('ready','idea'))",
                   me, me)
    print(f"{TAG} tasks for {me}: {len(mine)}")
    for t in mine:
        print("  " + task_line(t))
    ready = [t for t in b.tasks("status='ready' AND assignee='any'") if b.deps_done(t)]
    print(f"{TAG} ready for anyone: {len(ready)}")
    for t in ready[:5]:
        print("  " + task_line(t))
    if me != "owner":
        waiting = [m for m in b.open_questions() if m["sender"] == me]
        if waiting:
            print(f"{TAG} your questions waiting on {studio_config.owner()}: " + ", ".join(m["ref"] for m in waiting))
    if un:
        print(f"{TAG} read one: fe_board.py thread {un[0]['ref']} | reply: fe_board.py reply {un[0]['ref']} \"...\"")
    return 0


def cmd_next(b, me, args):
    t = b.next_task(me)
    if not t:
        print(f"{TAG} nothing ready for {me}")
        return 1
    if args.claim:
        if not b.claim(me, t["id"]):
            print(f"{TAG} {t['ref']} was taken first; run next again")
            return 1
        t = b.task(t["id"])
    print(task_line(t))
    if t["body"]:
        print(t["body"].rstrip())
    return 0


def cmd_who(b, me, args):
    pres = b.presence()
    now = time.time()
    rows = [s for s in merged_sessions(b.sessions(), fe_codex.threads()) if now - s["heartbeat"] < SESSIONS_SHOWN]
    holds = checkout_holds(b.con)
    runs = manager_outlook(b)["running"]
    for name in agents():
        p = pres.get(name) or {}
        mine = [s for s in rows if s["agent"] == name]
        theirs = [r for r in runs if r["agent"] == name]
        # D210: `doing` said in an earlier session is not what the agent does now
        # (a managed worker never says it), so it is left out rather than shown stale.
        act = f" -- {p['activity']} (said {ago(p.get('activity_at'))})" \
            if p.get("activity") and activity_current(p, mine) else ""
        task = f" [{ref('T', p['task_id'])}]" if p.get("task_id") else ""
        beat = max([p.get("heartbeat") or 0] + [s["heartbeat"] for s in mine]) or None
        print(f"{name:<4} {agent_state(p, mine, now):<8} heartbeat {ago(beat)}{task}{act}")
        for r in theirs:
            print(f"       manager: {r['name']} ({r['provider']}) {clip(r['title'], 70)} -- {r['state']} "
                  f"since {ago(r['started'])}, last step {ago(r['beat'])}")
            for step in r["steps"][-2:]:
                print(f"         {clip(step, 110)}")
        if name in holds:
            print(f"       checkout: {hold_text(holds[name])}")
        for s in mine[:4]:
            run = next((r for r in theirs if r["session"] and s["id"] in (r["session"], "codex:" + r["session"])), None)
            print(f"       {session_state(s, now):<8} {session_label(s)}"
                  f"{' = ' + run['name'] if run else ''} (last {ago(s['heartbeat'])})")
        if len(mine) > 4:
            print(f"       ... {len(mine) - 4} older session(s)")
    return 0


def presence_state(p):
    if not p or not p.get("heartbeat"):
        return "unseen"
    if p.get("state") == "idle":
        return "idle"
    if time.time() - p["heartbeat"] > IDLE_AFTER:
        return "stale"
    return p.get("state") or "working"


# D173: an agent's state is its sessions' (Claude's from its hooks, Codex's
# from Codex's own records); the presence heartbeat is the fallback.
SESSIONS_SHOWN = 12 * 3600  # sessions older than this are left off `who` and the Agents view


def merged_sessions(rows, codex):
    """The sessions table's rows (Claude's hooks, and a Codex thread's own
    board and sync commands) and the Codex threads from Codex's records
    (fe_codex.threads), which replace the table's row for the same thread.
    Each row gets its kind and `limit`: how long a working state holds after
    its last beat.  A thread is placed by its cwd, else by the checkout its
    commands ran in (a thread opened elsewhere can work in a checkout);
    threads outside every checkout are left out."""
    out = {}
    for s in rows:
        kind = "codex" if s["id"].startswith("codex:") else "claude"
        out[s["id"]] = dict(s, kind=kind, limit=fe_sync.WORKING_FOR)
    for t in codex:
        old = out.get(t["id"]) or {}
        agent = (checkout_of(t["cwd"]) if t.get("cwd") else None) or old.get("agent")
        if agent:
            out[t["id"]] = dict(t, agent=agent, limit=fe_codex.QUIET)
    return sorted(out.values(), key=lambda s: -s["heartbeat"])


def session_state(s, now=None):
    """working, idle or stale (a turn that went quiet without ending)."""
    if s["state"] != "working":
        return "idle"
    return "working" if (now or time.time()) - s["heartbeat"] < s.get("limit", fe_sync.WORKING_FOR) else "stale"


def session_label(s):
    if s.get("kind") == "codex":
        title = " ".join(re.sub(r"<[^>]+>", " ", s.get("title") or "").split())
        parts = ["Codex", s.get("model"), f'"{clip(title, 60)}"' if title else None]
        if s.get("turn") == "working" and s.get("turn_at"):
            parts.append(f"(turn running {ago(s['turn_at']).replace(' ago', '')})")
        return " ".join(p for p in parts if p)
    return f"Claude Code {s['id'][:8]}"


def agent_state(p, rows, now=None):
    """working when any of the agent's sessions has a turn running; else its
    newest session's state; else (no session rows) its presence."""
    now = now or time.time()
    states = [session_state(s, now) for s in rows]
    if "working" in states:
        return "working"
    return states[0] if states else presence_state(p)


# D212: the histories and their summaries are not a task's deliverable, nor the rules
DOC_SKIP = {"CLAUDE.md", "AGENTS.md", *studio_config.get("board.doc_skip", [])}
DOC_SKIP_DIRS = tuple(studio_config.get("board.doc_skip_dirs", []))
DOCS_SHOWN = 8
_docs_cache = {}


def docs_of(repo, head, reported=()):
    """D212: the Markdown documents a task wrote, as [{path, commit, text}] read from git
    at its landed commit, so a task whose answer is a document (a plan, a spec, a
    rundown) is read from the task: those the landing commit added or changed, and the
    .md files its worker reported among its artifacts. Not every commit since the task's
    base: that range holds every other agent's work landed meanwhile. A commit this
    clone has not fetched yet gives none until it has (the hooks and the release watch
    fetch; this never does)."""
    reported = tuple(reported)
    key = (str(repo), head, reported)
    if key in _docs_cache:
        return _docs_cache[key]
    try:  # never in the way of a landing: no checkout or no git is no documents
        if not Path(repo).is_dir() or fe_sync.git(repo, "cat-file", "-e", head + "^{commit}").returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []
    names = fe_sync.git_out(repo, "diff", "--name-only", "--diff-filter=AM", f"{head}^!", "--", "*.md").splitlines()
    for a in reported:  # "docs/x.md", "docs/x.md (what it holds)", "docs/a.md, docs/b.md"
        names += [w.strip(",;") for w in str(a).split() if w.strip(",;").endswith(".md")]
    out = []
    for p in dict.fromkeys(names):
        if p in DOC_SKIP or (DOC_SKIP_DIRS and p.startswith(DOC_SKIP_DIRS)) or len(out) >= DOCS_SHOWN:
            continue
        r = fe_sync.git(repo, "show", f"{head}:{p}")
        if r.returncode == 0:
            out.append({"path": p, "commit": head, "text": r.stdout})
    _docs_cache[key] = out
    return out


def task_docs(b, tid, repo=None):
    """The documents a managed task's landed work wrote (docs_between); none for a task
    the manager does not hold or that has not made a commit."""
    try:
        m = b.q1("SELECT head, landed_head, evidence FROM pm_tasks WHERE task_id=?", tid)
    except sqlite3.OperationalError:
        return []
    head = m and (m["landed_head"] or m["head"])
    if not head:
        return []
    try:
        reported = json.loads(m["evidence"] or "{}").get("artifacts") or []
    except ValueError:
        reported = []
    return docs_of(repo or studio_config.repo_root(), head, reported)


def manager_outlook(b, steps=True):
    """D210: what the manager runs, what waits and why (manager_outlook.py), read with
    the service's own config when this machine has one."""
    import manager_host
    import manager_outlook as outlook
    cfg = manager_host.load_config() if manager_host.config_path().exists() else None
    return outlook.outlook(b, cfg, steps=steps)


def activity_current(p, rows):
    """Whether `doing` was said in the agent's newest session, not an earlier one."""
    return not rows or (p.get("activity_at") or 0) >= (rows[0].get("started") or rows[0]["heartbeat"])


def cmd_doing(b, me, args):
    tid = parse_ref(args.task, "T") if args.task else None
    b.touch(me, state="working", activity=" ".join(args.text), task_id=tid)
    print(f"{TAG} {me} is on: {' '.join(args.text)}")
    return 0


def cmd_note(b, me, args):
    nid = b.add_note(me, with_images(" ".join(args.text), args.image), args.tag or "")
    print(f"{TAG} N{nid} saved")
    return 0


def cmd_notes(b, me, args):
    where = "" if args.all else "WHERE status='open'"
    for r in b.q(f"SELECT * FROM notes {where} ORDER BY id DESC LIMIT 50"):
        n = row(r, "N")
        tags = f" #{n['tags'].replace(',', ' #')}" if n["tags"] else ""
        task = f" -> {ref('T', n['task_id'])}" if n["task_id"] else ""
        print(f"{n['ref']:<5} {n['author']:<5} {n['status']:<9} {clip(n['body'], 80)}{tags}{task}  {ago(n['created'])}")
        for line in image_lines(n["body"], "      "):
            print(line)
    return 0


def cmd_task(b, me, args):
    act = args.action
    if act == "new":
        depends = parse_refs(args.depends, "T") if args.depends else []
        note_id = parse_ref(args.from_note, "N") if args.from_note else None
        body = with_images(read_body(args), args.image)
        tid = b.add_task(me, " ".join(args.ids), body, args.prio,
                         args.to, args.status, note_id, depends)
        print(f"{TAG} T{tid} created ({args.status}, P{args.prio}, {args.to})")
        if note_id:
            hint = jev_suggest(f"{' '.join(args.ids)}\n\n{body}")
            if hint:
                print(f"{hint}: fe_board.py task set T{tid} to=... prio=...")
        return 0
    if act == "list":
        if args.status:
            ts = b.tasks("status=?", args.status)
        elif args.mine:
            ts = b.tasks("claimed_by=? OR assignee=?", me, me)
        elif args.all:
            ts = b.tasks()
        else:
            ts = b.tasks("status NOT IN ('done','dropped')")
        for t in ts:
            print(task_line(t))
        if not ts:
            print(f"{TAG} no tasks")
        return 0
    if not args.ids:
        raise BoardError(f"task {act} needs a task id")
    tid = parse_ref(args.ids[0], "T")
    rest = args.ids[1:]
    if act == "show":
        t = b.task(tid)
        print(task_line(t))
        print(f"  created by {t['created_by']} {ago(t['created'])}, updated {ago(t['updated'])}"
              + (f", from {ref('N', t['note_id'])}" if t["note_id"] else ""))
        if t["depends"]:
            print("  depends: " + ", ".join(f"{ref('T', d)} [{b.task(d)['status']}]" for d in t["depends"]))
        if t["links"]:
            print("  links: " + " ".join(t["links"]))
        if t["body"].strip():
            print("---")
            print(t["body"].rstrip())
            for line in image_lines(t["body"], "  "):
                print(line)
        msgs = [row(r, "M") for r in b.q("SELECT * FROM messages WHERE topic=? ORDER BY id", t["ref"])]
        if msgs:
            print(f"--- discussion ({len(msgs)})")
            for m in msgs:
                print_message(m)
            b.mark_read(me, [m["id"] for m in msgs])
        return 0
    if act == "claim":
        if b.claim(me, tid):
            print(f"{TAG} T{tid} is yours")
            return 0
        t = b.task(tid)
        print(f"{TAG} T{tid} not claimable: {t['status']}, assignee {t['assignee']}"
              + (f", held by {t['claimed_by']}" if t["claimed_by"] else ""))
        return 1
    if act == "start":
        b.update_task(me, tid, status="in_progress")
        b.touch(me, state="working", activity=f"T{tid}: {b.task(tid)['title']}", task_id=tid)
        print(f"{TAG} T{tid} in progress")
        return 0
    if act == "release":
        b.update_task(me, tid, status="ready")
        print(f"{TAG} T{tid} back to ready")
        return 0
    if act == "review":
        links = [x for x in (args.commit, args.version, args.decision) if x]
        if links:
            b.add_links(me, tid, links)
        flags = jev_dod(args.note)
        for f in flags:
            print(f"[fe-jev] DoD: {f}")
        b.update_task(me, tid, status="review")
        t = b.task(tid)
        body = (args.note or "") + ("\n\nLinks: " + " ".join(t["links"]) if t["links"] else "")
        if flags:
            body += "\n\nJev (D161) reads this note as missing: " + "; ".join(flags)
        b.add_message(me, "owner", f"Review T{tid}: {t['title']}", with_images(body.strip(), args.image),
                      "review", topic=f"T{tid}")
        print(f"{TAG} T{tid} in review; {studio_config.owner()} was told")
        return 0
    if act == "block":
        question = " ".join(rest).strip()
        if not question:
            raise BoardError(f"task block T12 \"the question {studio_config.owner()} must answer\"")
        b.update_task(me, tid, status="blocked")
        mid = b.add_message(me, "owner", f"T{tid} blocked: {clip(question, 70)}",
                            with_images(question, args.image), "question", topic=f"T{tid}")
        print(f"{TAG} T{tid} blocked; asked {studio_config.owner()} in M{mid}")
        return 0
    if act == "attach":
        paths = rest + (args.image or [])
        if not paths:
            raise BoardError("task attach T12 PATH [PATH..]")
        b.update_task(me, tid, body=with_images(b.task(tid)["body"], paths))
        if me not in ("owner", "manager"):  # D304: said in the task's thread too, which the manager carries to Discord
            b.add_message(me, "owner", f"T{tid}: images attached", with_images("", paths), kind="fyi",
                          topic=f"T{tid}")
        print(f"{TAG} T{tid}: {len(paths)} image(s) added to the description")
        return 0
    if act == "link":
        b.add_links(me, tid, rest)
        print(f"{TAG} T{tid} links: {' '.join(b.task(tid)['links'])}")
        return 0
    if act in ("done", "drop"):
        b.update_task(me, tid, status="done" if act == "done" else "dropped")
        print(f"{TAG} T{tid} {'done' if act == 'done' else 'dropped'}")
        return 0
    if act == "set":
        alias = {"prio": "priority", "to": "assignee"}
        fields = {}
        for kv in rest:
            if "=" not in kv:
                raise BoardError(f"task set takes key=value, not {kv!r}")
            k, v = kv.split("=", 1)
            k = alias.get(k, k)
            fields[k] = parse_refs(v, "T") if k == "depends" else v
        b.update_task(me, tid, **fields)
        print(task_line(b.task(tid)))
        return 0
    raise BoardError(f"unknown task action {act!r}")


def cmd_msg(b, me, args):
    mid = b.add_message(me, args.to, args.subject, with_images(read_body(args), args.image), args.kind,
                        topic=args.task)
    print(f"{TAG} M{mid} sent to {args.to}")
    return 0


def cmd_reply(b, me, args):
    parent = parse_ref(args.id, "M")
    mid = b.add_message(me, None, None, with_images(read_body(args), args.image), args.kind, reply_to=parent)
    m = b.message(mid)
    print(f"{TAG} M{mid} sent to {m['recipient']} (thread M{m['thread']})")
    return 0


def cmd_thread(b, me, args):
    msgs = b.thread(parse_ref(args.id, "M"))
    for m in msgs:
        print_message(m)
    b.mark_read(me, [m["id"] for m in msgs])
    return 0


def cmd_read(b, me, args):
    if args.ids == ["all"]:
        ids = [m["id"] for m in b.unread(me)]
    else:
        ids = [parse_ref(i, "M") for i in args.ids]
    b.mark_read(me, ids)
    print(f"{TAG} {len(ids)} marked read")
    return 0


def cmd_attach(b, me, args):
    for path in args.paths:
        f = Path(path)
        if not f.is_file():
            raise BoardError(f"no such image: {path}")
        mark = store_image(f.read_bytes(), f.name)
        print(mark)
        print(f"    {files_dir() / mark.rsplit('/', 1)[1].rstrip(')')}")
    return 0


def cmd_messages(b, me, args):
    if args.all:
        roots = b.q("SELECT thread, MAX(id) AS last FROM messages GROUP BY thread ORDER BY last DESC LIMIT 40")
    else:
        roots = b.q("SELECT thread, MAX(id) AS last FROM messages WHERE sender=? OR recipient=? OR "
                    "(recipient='all' AND ?<>'owner') GROUP BY thread ORDER BY last DESC LIMIT 40", me, me, me)
    unread = {m["id"] for m in b.unread(me)}
    for r in roots:
        msgs = b.thread(r["thread"])
        root, last = msgs[0], msgs[-1]
        n = sum(1 for m in msgs if m["id"] in unread)
        flag = f"  {n} unread" if n else ""
        print(f"{root['ref']:<5} {clip(root['subject'], 60):<60} {len(msgs)} msg, last {last['sender']} "
              f"{ago(last['created'])}{flag}")
    return 0


# ----------------------------------------------------------------- hooks

def jev_order(name, messages, tasks):
    """D161: the unread messages that matter to `name` first, the routine GPU
    notices folded out. The list unchanged when the use is off or Jev does
    not answer in time; never raises."""
    if not messages:
        return messages, []
    try:
        import fe_jev
        if not fe_jev.enabled("board"):
            return messages, []
        task = "; ".join(f"{t['ref']} {t['title']}: {clip(t.get('body') or '', 300)}" for t in tasks[:2])
        rest, folded, _ = fe_jev.board_order(name, messages, task)
        return rest, folded
    except Exception:  # noqa: BLE001
        return messages, []


def jev_suggest(text):
    """D161: Jev's suggested assignee and priority, as a line, or None."""
    try:
        import fe_jev
        if not fe_jev.enabled("board"):
            return None
        got = fe_jev.route_suggest(text)
    except Exception:  # noqa: BLE001
        return None
    if not got:
        return None
    return f"[fe-jev] suggests: to {got['to']} ({got['p']:.2f}), P{got['prio']} -- a suggestion only"


def jev_dod(note):
    """D161: what Jev reads a review note as missing, against this session's
    evidence; [] when the use is off or it does not answer."""
    if not note:
        return []
    try:
        import fe_jev
        if not fe_jev.enabled("dod"):
            return []
        path = fe_jev.current_transcript()
        return fe_jev.dod_check(path, final=note, timeout=10.0)["flags"] if path else []
    except Exception:  # noqa: BLE001
        return []


def digest_lines(name, touch=False, session=None):
    """The prompt-time digest for one agent (fe_sync report); never raises."""
    if not name:
        return []
    try:
        with Board() as b:
            if touch:
                b.touch(name, state="working")
                b.session_touch(session, name, "working")
            un = b.unread(name)
            mine = b.tasks(f"(claimed_by=? AND status IN {ACTIVE}) OR (assignee=? AND status='ready')",
                           name, name)
            ready = [t for t in b.tasks("status='ready' AND assignee='any'") if b.deps_done(t)]
            waiting = [m for m in b.open_questions() if m["sender"] == name]
            if un:
                b.set_notified(name, un[-1]["id"])
    except Exception as e:  # a broken board never breaks the sync hooks
        return [f"{TAG} unavailable: {e}"]
    total = len(un)
    un, folded = jev_order(name, un, mine)
    lines = []
    head = f"{TAG} {name}: {total} unread"
    if un:
        head += " -- " + "; ".join(f'{m["ref"]} {m["sender"]} {m["kind"]} "{clip(m["subject"], 50)}"'
                                  for m in un[:3])
        if len(un) > 3:
            head += f"; +{len(un) - 3} more"
        head += f" | read: fe_board.py thread {un[0]['ref']}"
    if folded:
        head += f" | {len(folded)} routine GPU notice(s) folded: {' '.join(m['ref'] for m in folded)}"
    lines.append(head)
    if mine or ready or waiting:
        parts = []
        if mine:
            parts.append("yours: " + "; ".join(f"{t['ref']} [{t['status']}] {clip(t['title'], 40)}" for t in mine[:3]))
        if ready:
            parts.append(f"ready for anyone: {len(ready)} (top {ready[0]['ref']} P{ready[0]['priority']} "
                         f"\"{clip(ready[0]['title'], 40)}\")")
        if waiting:
            parts.append(f"your questions waiting on {studio_config.owner()}: " + ", ".join(m["ref"] for m in waiting))
        lines.append(f"{TAG} " + " | ".join(parts))
    up = server_up()
    lines.append(f"{TAG} {url()} ({'up' if up else 'server down: fe_board.py open'}) | "
                 "fe_board.py inbox | skill /board")
    return lines


def read_payload():
    try:
        raw = sys.stdin.read() if sys.stdin and not sys.stdin.isatty() else ""
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def cmd_hook_post():
    """PostToolUse: tell the agent about messages that arrived mid-turn, once."""
    payload = read_payload()
    try:
        name = whoami(cwd=payload.get("cwd"))
        if not name or name == "owner":
            return 0
        with Board() as b:
            p = b.q1("SELECT notified FROM presence WHERE agent=?", name)
            new = b.unread(name, above=p["notified"] if p else 0)
            b.touch(name, state="working", throttle=60)
            b.session_touch(payload.get("session_id"), name, "working", throttle=60)
            overtime = ""
            if os.environ.get("FE_MANAGER_RUN"):
                import manager_store
                overtime = manager_store.overtime_note(b, os.environ["FE_MANAGER_RUN"])
            if not new and not overtime:
                return 0
            if new:
                b.set_notified(name, new[-1]["id"])
    except Exception:
        return 0
    if not new:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": overtime}}))
        return 0
    everything = new
    new, folded = jev_order(name, new, [])
    parts = ([overtime] if overtime else []) + [f"{TAG} new message(s) for {name} on the board:"]
    for m in new[:5]:
        topic = f", on {m['topic']}" if m["topic"] else ""
        parts.append(f"{m['ref']} from {m['sender']} ({m['kind']}{topic}): {m['subject']}")
        body = (m["body"] or "").strip()
        if body:
            parts.append("    " + clip(body, 600))
        parts += image_lines(body)
    if folded:
        parts.append("routine GPU notices (folded by Jev, D161): "
                     + "; ".join(f"{m['ref']} {m['sender']}: {clip(m['subject'], 50)}" for m in folded))
    parts.append(f"Reply: {studio_config.tool_cmd('fe_board.py')} reply {everything[0]['ref']} \"...\" | "
                 f"mark read: fe_board.py read {' '.join(m['ref'] for m in everything)}")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": "\n".join(parts)}}))
    return 0


def cmd_hook_stop():
    payload = read_payload()
    try:
        name = whoami(cwd=payload.get("cwd"))
        if name and name != "owner":
            with Board() as b:
                b.touch(name, state="idle")
                b.session_touch(payload.get("session_id"), name, "idle")
    except Exception:
        pass
    return 0


def codex_touch(b, name):
    """Codex runs no hooks (D165): each board or sync command it runs marks
    its thread working in its checkout.  Where Codex's own records can be
    read (D173, fe_codex) they replace this row, turn end included; the row
    is what routing's arrival check reads, and the fallback without them."""
    thread = os.environ.get("CODEX_THREAD_ID")
    if thread and name and name != "owner":
        b.session_touch(f"codex:{thread}", name, "working", throttle=60)


def cmd_hook_end():
    """SessionEnd: the session no longer holds its checkout (D165)."""
    payload = read_payload()
    try:
        if payload.get("session_id"):
            with Board() as b:
                b.session_end(payload["session_id"])
    except Exception:
        pass
    return 0


# ----------------------------------------------------------------- live agent state (server)

def project_info():
    """What the UI names: the project, who directs it, and what a running game is
    called (studio.toml: name, owner, [game] label)."""
    return {"name": studio_config.name(), "owner": studio_config.owner(),
            "process_label": studio_config.get("game.label") or "games"}


class Live:
    """Each agent's tree and the GPU, refreshed in the background (git and
    PowerShell are too slow to run inside a request)."""

    def __init__(self):
        self.data = {}
        self.at = 0
        self.busy = False
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            stale = time.time() - self.at > 15 and not self.busy
            if stale:
                self.busy = True
        if stale:
            threading.Thread(target=self.refresh, daemon=True).start()
        return self.data

    def refresh(self):
        try:
            self.data = live_agents()
            self.at = time.time()
        except (OSError, sqlite3.Error, BoardError):
            # Preserve the last reading during a restart or board backup/migration.
            self.at = time.time()
        finally:
            self.busy = False


def live_agents():
    reg = registry()
    upstream = f'{reg["remote"]}/{reg["branch"]}'
    procs = fe_sync.app_processes()
    rows = fe_sync.sessions()
    gpu = studio_config.adapter("gpu")
    is_bench = gpu.is_bench if gpu else (lambda cmd: False)
    with Board() as b:
        holds = checkout_holds(b.con)
    claimed = set()
    out = {}
    for name, agent in reg["agents"].items():
        path = Path(agent["path"])
        info = {"path": str(path), "port": agent.get("port"), "primary": bool(agent.get("primary"))}
        if (path / ".git").exists():
            modified, untracked = fe_sync.tree_state(path)
            ahead, behind = fe_sync.counts(path, upstream)
            info.update(branch=fe_sync.branch_of(path), modified=modified, untracked=untracked,
                        ahead=ahead, behind=behind,
                        head=fe_sync.git_out(path, "log", "-1", "--format=%h %s (%cr)"))
        root = fe_sync.norm(path) + os.sep
        scratch = fe_sync.scratch_marker(name)
        games = []
        for pid, cmd in procs:
            c = os.path.normcase(cmd.replace("/", "\\"))
            if root in c or (scratch and scratch in c):
                games.append({"pid": pid, "cmd": cmd[:200], "bench": is_bench(cmd)})
                claimed.add(pid)
        info["gpu"] = games
        # What the router tells a new session about this checkout (D165, D191), word for word.
        busy, soft, _ = fe_sync.checkout_state(name, reg, rows, procs)
        h = holds.get(name)
        info.update(route={"busy": busy, "soft": soft},
                    hold=dict(h, text=hold_text(h)) if h else None)
        out[name] = info
    other = [{"pid": pid, "cmd": cmd[:200], "bench": is_bench(cmd)} for pid, cmd in procs if pid not in claimed]
    return {"agents": out, "other_gpu": other, "at": time.time()}


# ----------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "fe-board"
    live = Live()

    def log_message(self, fmt, *args):
        pass

    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

    def send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"error": "bad host"})
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/app.css": ("app.css", "text/css; charset=utf-8")}
        if re.fullmatch("/files/" + FILE_NAME, path):
            f = files_dir() / path[len("/files/"):]
            if not f.is_file():
                return self.send(404, {"error": "no such image"})
            return self.send(200, f.read_bytes(), IMAGE_MIME[f.suffix[1:]], cache="max-age=31536000, immutable")
        try:
            if path in static:
                name, ctype = static[path]
                body = (UI_DIR / name).read_bytes()
                if name == "index.html":  # the project's name before the script runs
                    import html
                    body = body.replace(b"{{project}}", html.escape(studio_config.name()).encode())
                return self.send(200, body, ctype)
            if path == "/api/project":
                return self.send(200, project_info())
            if path == "/api/health":
                return self.send(200, health())
            if path == "/api/state":
                with Board() as b:
                    snap = b.snapshot("owner")
                    from manager_store import snapshot
                    snap["manager"] = snapshot(b)
                    snap["outlook"] = manager_outlook(b)
                    mobile = studio_config.adapter("mobile")
                    if mobile is not None:
                        snap["mobile"] = mobile.view(b)
                    import manager_models  # D218: each managed task's model and what it spent
                    snap["task_models"] = manager_models.board_view(b)
                    snap["sessions"] = [s for s in merged_sessions(b.sessions(), fe_codex.threads())
                                        if snap["now"] - s["heartbeat"] < SESSIONS_SHOWN]
                snap["live"] = self.live.get()
                snap["registry"] = registry()["agents"]
                snap["project"] = project_info()
                return self.send(200, snap)
            if path == "/api/transcript":
                # a managed run's whole log, as a Claude Code session reads (manager_transcript.py)
                import manager_transcript
                if params.get("run"):
                    return self.send(200, manager_transcript.read(params["run"], params.get("from", "0") or 0))
                with Board() as b:
                    return self.send(200, manager_transcript.runs(b, urllib.parse.unquote(params.get("ref", ""))))
            if path == "/api/nightly":
                import manager_nightly  # D262, D265: the bench and the cleanup crew, live and by night
                with Board() as b:
                    return self.send(200, manager_nightly.view(b))
            if path == "/api/task-docs":
                with Board() as b:
                    return self.send(200, {"docs": task_docs(b, int(params.get("task", "0") or 0))})
            if path == "/api/events":
                since = int(params.get("since", "0") or 0)
                with Board() as b:
                    evs = [dict(r) for r in b.q("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200", since)]
                    last = b.last_event()
                return self.send(200, {"last": last, "events": evs})
        except ValueError as e:  # a malformed run id or offset
            return self.send(400, {"error": str(e)})
        except BoardError as e:
            return self.send(409, {"error": str(e)})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        # a custom header and JSON body: a page on another origin cannot send
        # this without a CORS preflight, which is never answered
        if not self.host_ok() or self.headers.get("X-Board") != "1":
            return self.send(403, {"error": "forbidden"})
        if self.path.partition("?")[0] == "/api/upload":
            return self.upload()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1 << 20:
                return self.send(413, {"error": "too big"})
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        path = self.path.partition("?")[0]
        if path == "/api/shutdown":
            self.send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        try:
            with Board() as b:
                result = post(b, path, data)
            return self.send(200, {"ok": True, **(result or {})})
        except BoardError as e:
            return self.send(400, {"error": str(e)})
        except (KeyError, TypeError, ValueError) as e:
            return self.send(400, {"error": f"bad request: {e}"})


    def upload(self):
        """Raw image bytes (a pasted or dropped screenshot) -> its markdown."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_IMAGE:
            return self.send(413, {"error": f"an image must be 1 byte to {MAX_IMAGE >> 20} MB"})
        data = self.rfile.read(n)
        name = urllib.parse.unquote(self.headers.get("X-Filename") or "")
        try:
            mark = store_image(data, name)
        except BoardError as e:
            return self.send(400, {"error": str(e)})
        return self.send(200, {"ok": True, "markdown": mark, "url": mark[mark.index("](") + 2:-1]})


def post(b, path, d):
    me = "owner"
    if path == "/api/mobile-build":
        mobile = studio_config.adapter("mobile")
        if mobile is None:
            raise BoardError("This project has no mobile delivery adapter")
        mobile.queue_build(b, "board:" + str(time.time_ns()))
        return {"ok": True}
    if path == "/api/manager":
        from manager_store import control
        if d['action'] == 'model':  # D218: Yotam picks a task's model, or hands it back to the policy
            import manager_models
            manager_models.set_owner_tier(b, int(d['task']), d['tier'])
            return {}
        return control(b, d['action'])
    if path == "/api/note":
        return {"id": b.add_note(me, d["body"], d.get("tags", ""))}
    if path == "/api/note/update":
        fields = {k: d[k] for k in ("body", "tags", "status") if k in d}
        b.update_note(me, int(d["id"]), **fields)
        return {}
    if path == "/api/task":
        deps = parse_refs(d.get("depends", ""), "T")
        return {"id": b.add_task(me, d["title"], d.get("body", ""), int(d.get("priority", 2)),
                                 d.get("assignee", "any"), d.get("status", "idea"),
                                 d.get("note_id"), deps, d.get("links", []))}
    if path == "/api/task/update":
        fields = {k: d[k] for k in ("title", "body", "status", "priority", "assignee", "rank",
                                    "depends", "links") if k in d}
        b.update_task(me, int(d["id"]), **fields)
        return {}
    if path == "/api/msg":
        return {"id": b.add_message(me, d.get("to"), d.get("subject"), d.get("body", ""),
                                    d.get("kind"), d.get("reply_to"), d.get("topic"))}
    if path == "/api/read":
        b.mark_read(me, [int(i) for i in d["ids"]])
        return {}
    raise BoardError(f"no route {path}")


def health():
    return {"ok": True, "version": SERVER_VERSION, "schema": SCHEMA, "dir": str(board_dir()),
            "pid": os.getpid(), "tool": str(Path(__file__).resolve())}


def backup(keep=BACKUPS_KEPT):
    src = db_path()
    if not src.exists():
        return None
    d = board_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"board-{datetime.date.today():%Y%m%d}.db"
    with contextlib.closing(sqlite3.connect(str(src))) as s, contextlib.closing(sqlite3.connect(str(dst))) as t:
        s.backup(t)
    for old in sorted(d.glob("board-*.db"))[:-keep]:
        old.unlink()
    return dst


def serve(port_=None):
    p = port_ or port()
    with Board():
        pass  # create or check the store before listening
    backup()
    srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
    srv.daemon_threads = True

    def daily():
        while True:
            time.sleep(3600)
            try:
                backup()
            except Exception:
                pass
    threading.Thread(target=daily, daemon=True).start()
    print(f"{TAG} serving {board_dir()} on http://127.0.0.1:{p} (v{SERVER_VERSION})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def server_up(timeout=0.25):
    try:
        with socket.create_connection(("127.0.0.1", port()), timeout=timeout):
            return True
    except OSError:
        return False


def server_health():
    try:
        with urllib.request.urlopen(url() + "/api/health", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ensure_server(quiet=False):
    """Start the server unless one of this version or newer answers. A server whose
    code predates this board schema is older too, whatever its version: it refuses
    the store once it is migrated (409 on /api/state), so the board page goes blank."""
    h = server_health()
    if h and h.get("version", 0) >= SERVER_VERSION and h.get("schema", 0) >= SCHEMA:
        return True
    if h:  # an older server: replace it
        try:
            req = urllib.request.Request(url() + "/api/shutdown", data=b"{}", method="POST",
                                         headers={"X-Board": "1", "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:
            pass
        for _ in range(30):
            if not server_up():
                break
            time.sleep(0.1)
    elif server_up():
        if not quiet:
            print(f"{TAG} port {port()} is taken by something else; set FE_BOARD_PORT")
        return False
    board_dir().mkdir(parents=True, exist_ok=True)
    log = open(board_dir() / "server.log", "ab")
    exe = sys.executable
    if os.name == "nt":
        w = Path(exe).with_name("pythonw.exe")
        exe = str(w) if w.exists() else exe
    base = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    for flags in ((base | 0x01000000, base) if os.name == "nt" else (0,)):  # try BREAKAWAY_FROM_JOB
        try:
            subprocess.Popen([exe, str(Path(__file__).resolve()), "serve"], cwd=str(HERE),
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                             creationflags=flags)
            break
        except OSError:
            continue
    for _ in range(50):
        if server_health():
            return True
        time.sleep(0.1)
    if not quiet:
        print(f"{TAG} the server did not start; see {board_dir() / 'server.log'}")
    return False


# ----------------------------------------------------------------- export and selftest

def export(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with Board() as b:
        snap = b.snapshot("owner")
    (dest / "board.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [f"# Board export ({stamp})", "", "## Tasks", ""]
    for s in STATUSES:
        ts = [t for t in snap["tasks"] if t["status"] == s]
        if not ts:
            continue
        out += [f"### {s}", ""]
        for t in ts:
            who = t["claimed_by"] or t["assignee"]
            out.append(f"- **{t['ref']}** P{t['priority']} ({who}) {t['title']}"
                       + (f" — {' '.join(t['links'])}" if t["links"] else ""))
            for line in t["body"].strip().splitlines():
                out.append(f"  {line}")
        out.append("")
    out += ["## Thoughts", ""]
    for n in snap["notes"]:
        out.append(f"- **{n['ref']}** ({n['author']}, {n['status']}"
                   + (f", #{n['tags'].replace(',', ' #')}" if n["tags"] else "") + ")")
        for line in n["body"].strip().splitlines():
            out.append(f"  {line}")
    out += ["", "## Messages", ""]
    for m in snap["messages"]:
        when = datetime.datetime.fromtimestamp(m["created"]).strftime("%Y-%m-%d %H:%M")
        out.append(f"- **{m['ref']}** {when} {m['sender']} → {m['recipient']} ({m['kind']}"
                   + (f", {m['topic']}" if m["topic"] else "") + f", thread M{m['thread']}): {m['subject']}")
        for line in (m["body"] or "").strip().splitlines():
            out.append(f"  {line}")
    text = "\n".join(out) + "\n"
    used = {fname for _, fname in IMAGE_RE.findall(json.dumps(snap, ensure_ascii=False))}
    if used:
        (dest / "files").mkdir(exist_ok=True)
        for fname in used:
            if (files_dir() / fname).is_file():
                shutil.copy2(files_dir() / fname, dest / "files" / fname)
    (dest / "board.md").write_text(text.replace("](/files/", "](files/"), encoding="utf-8")
    return dest


def selftest():
    tmp = tempfile.mkdtemp(prefix="fe-board-test-")
    old_dir, old_port = os.environ.get("FE_BOARD_DIR"), os.environ.get("FE_BOARD_PORT")
    old_agent, old_codex = os.environ.get("FE_AGENT"), os.environ.get("CODEX_HOME")
    os.environ["FE_BOARD_DIR"] = tmp
    failures = []

    def check(cond, what):
        print(f"  {'ok  ' if cond else 'FAIL'} {what}")
        if not cond:
            failures.append(what)

    try:
        a, c = agents()[0], agents()[1]
        with Board() as b:
            nid = b.add_note("owner", "cuts should bleed more #feel", "feel")
            tid = b.add_task("owner", "More blood on deep cuts", "acceptance: ...", 1, "any", "idea",
                             note_id=nid)
            check(b.note(nid)["status"] == "converted" and b.note(nid)["task_id"] == tid, "thought -> task")
            check(not b.claim(a, tid), "an idea cannot be claimed")
            b.update_task("owner", tid, status="ready")
            dep = b.add_task("owner", "Needs T1", status="ready", depends=[tid])
            check(b.next_task(a)["id"] == tid, "next skips a task whose dependency is open")
            try:
                b.claim(a, dep)
                check(False, "claim refuses an unmet dependency")
            except BoardError:
                check(True, "claim refuses an unmet dependency")

        wins = []

        def racer(name):
            with Board() as bb:
                wins.append((name, bb.claim(name, tid)))
        ts = [threading.Thread(target=racer, args=(n,)) for n in (a, c, a, c)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        winners = {n for n, w in wins if w}
        check(len(winners) == 1, f"claim race: exactly one winner ({winners})")

        with Board() as b:
            holder = b.task(tid)["claimed_by"]
            try:
                b.update_task(holder, tid, status="done")
                check(False, "an agent cannot close a task")
            except BoardError:
                check(True, "an agent cannot close a task")
            b.update_task(holder, tid, status="review", links=["V98", "D116"])
            b.update_task("owner", tid, status="done")
            check(b.task(tid)["status"] == "done", "owner closes a task")

            m1 = b.add_message("owner", "all", "Heads up", "main.rs is mine today")
            check(any(m["id"] == m1 for m in b.unread(a)) and any(m["id"] == m1 for m in b.unread(c)),
                  "a message to all is unread for every agent")
            check(not any(m["id"] == m1 for m in b.unread("owner")), "... and not for its sender")
            b.mark_read(a, [m1])
            check(not b.unread(a) and b.unread(c), "unread is tracked per reader")
            q = b.add_message(a, "owner", "Which wound size?", "40 or 60 mm?", "question", topic=f"T{tid}")
            check(q in [m["id"] for m in b.open_questions()], "a question to the owner is open")
            r = b.add_message("owner", None, None, "60 mm", None, reply_to=q)
            check(b.message(r)["recipient"] == a and b.message(r)["thread"] == q, "a reply goes back to the asker")
            check(q not in [m["id"] for m in b.open_questions()], "... and answers the question")

        os.environ["FE_AGENT"] = a
        lines = digest_lines(a, touch=True, session="s-prompt")
        check(any("1 unread" in l for l in lines), f"digest shows the unread reply ({lines[0]})")

        def hook(fn, sid="s-post"):
            import io
            old_in, old_out = sys.stdin, sys.stdout
            payload = json.dumps({"cwd": registry()["agents"][a]["path"], "session_id": sid})
            sys.stdin, sys.stdout = io.StringIO(payload), io.StringIO()
            try:
                fn()
                return sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out

        def post_hook():
            return hook(cmd_hook_post)
        with Board() as b:
            b.add_message(c, a, "Rebased onto your change", "", "fyi")
        first, second = post_hook(), post_hook()
        check("Rebased onto your change" in first and not second.strip(), "hook-post tells once, then is silent")

        # D165: the hooks keep one row per Claude session, in its checkout
        def session(sid):
            with Board() as b:
                return next((s for s in b.sessions() if s["id"] == sid), None)
        s = session("s-prompt")
        check(s and s["agent"] == a and s["state"] == "working", "a prompt records its session as working")
        check(session("s-post") and session("s-post")["agent"] == a, "hook-post records its session")
        hook(cmd_hook_stop, "s-prompt")
        check(session("s-prompt")["state"] == "idle", "hook-stop marks the session idle")
        hook(cmd_hook_end, "s-prompt")
        check(session("s-prompt") is None and session("s-post"), "hook-end removes that session alone")

        # D173: Codex threads from Codex's own records (a fake CODEX_HOME)
        chome = Path(tmp) / "codex"
        chome.mkdir()
        os.environ["CODEX_HOME"] = str(chome)
        now = time.time()

        def iso(t):
            return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        def rollout(tid, *events):
            f = chome / f"rollout-{tid}.jsonl"
            with open(f, "a", encoding="utf-8") as out:
                for t, typ, kind in events:
                    payload = {"type": kind} if kind else {"type": "reasoning"}
                    if typ == "response_item":  # a tool's output quoting a turn end must not end the turn
                        payload["output"] = json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
                    out.write(json.dumps({"timestamp": iso(t), "type": typ, "payload": payload}) + "\n")
            return str(f)

        paths = {n: registry()["agents"][n]["path"] for n in (a, c)}
        threads_ = [  # id, cwd, originator, source, events
            ("t-run", "\\\\?\\" + paths[a].replace("/", "\\"), "Codex Desktop", "user",
             [(now - 60, "event_msg", "task_started"), (now - 5, "response_item", None)]),
            ("t-done", paths[c], "Codex Desktop", "user",
             [(now - 90, "event_msg", "task_started"), (now - 30, "event_msg", "task_complete")]),
            ("t-quiet", paths[a], "Codex Desktop", "user",
             [(now - fe_codex.QUIET - 90, "event_msg", "task_started")]),
            ("t-long", paths[a], "Codex Desktop", "user", [(now - 3600, "event_msg", "task_started")]),
            ("t-import", paths[a], None, None, [(now - 20, "event_msg", "task_started")]),
            ("t-sub", paths[a], "Codex Desktop", "subagent", [(now - 20, "event_msg", "task_started")]),
            ("t-away", str(Path(tmp) / "elsewhere"), "Codex Desktop", "user",
             [(now - 40, "event_msg", "task_started"), (now - 10, "event_msg", "turn_aborted")]),
        ]
        with contextlib.closing(sqlite3.connect(str(chome / "state_5.sqlite"))) as con:
            con.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER, "
                        "updated_at INTEGER, updated_at_ms INTEGER, cwd TEXT, name TEXT, model TEXT, "
                        "thread_source TEXT, originator TEXT, archived INTEGER DEFAULT 0)")
            for tid, cwd, orig, source, events in threads_:
                t0 = events[0][0]
                con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                            (tid, rollout(tid, *events), int(t0), int(now), int(now * 1000), cwd,
                             f"thread {tid}", "gpt-test", source, orig))
            con.commit()
        # a turn whose start tool output pushed past the scanned tail, still being written
        pad = json.dumps({"timestamp": iso(now - 5), "type": "response_item",
                          "payload": {"type": "reasoning", "text": "x" * (64 << 10)}}) + "\n"
        with open(chome / "rollout-t-long.jsonl", "a", encoding="utf-8") as out:
            out.write(pad * (fe_codex.SCAN_LIMIT // len(pad) + 2))
        with Board() as b:
            b.session_touch("codex:t-away", c, "working")  # its commands ran in c's checkout
        got = {t["thread"]: t for t in fe_codex.threads()}
        check(set(got) == {"t-run", "t-done", "t-quiet", "t-long", "t-away"},
              f"Codex threads: imports and sub-agents are skipped ({sorted(got)})")
        check(got["t-run"]["state"] == "working" and got["t-done"]["state"] == "idle",
              "a Codex turn is working until its task_complete")
        check(got["t-quiet"]["turn"] == "working" and got["t-quiet"]["state"] == "idle",
              "a Codex turn quiet past QUIET no longer counts as working")
        check(got["t-away"]["state"] == "idle", "turn_aborted ends a Codex turn")
        check(got["t-long"]["turn"] is None and got["t-long"]["state"] == "working",
              "a turn longer than the scanned tail is working while its log moves")
        with Board() as b:
            merged = {s["id"]: s for s in merged_sessions(b.sessions(), fe_codex.threads())}
        check(merged["codex:t-run"]["agent"] == a and merged["codex:t-done"]["agent"] == c,
              "a Codex thread is placed by its cwd (extended path form too)")
        check(merged["codex:t-away"]["agent"] == c and merged["codex:t-away"]["state"] == "idle",
              "a thread opened elsewhere is placed where its commands ran; Codex's state replaces the row's")
        check(agent_state({}, [merged["codex:t-run"]]) == "working"
              and agent_state({}, [merged["codex:t-done"]]) == "idle", "an agent's state is its sessions'")
        busy = {s["id"] for s in fe_sync.working_elsewhere(a, fe_sync.sessions())}
        check("codex:t-run" in busy and "codex:t-quiet" not in busy, f"routing counts a running Codex turn ({sorted(busy)})")
        check(not any(s["id"] == "codex:t-run" for s in fe_sync.sessions(recorded=True)),
              "a Codex arrival is judged by its own commands' rows")
        rollout("t-run", (time.time(), "event_msg", "task_complete"))
        busy = {s["id"] for s in fe_sync.working_elsewhere(a, fe_sync.sessions())}
        check("codex:t-run" not in busy, "routing frees the checkout when the Codex turn ends")
        p = {"activity": "old work", "activity_at": now - 7200}
        check(not activity_current(p, [merged["codex:t-run"]])
              and activity_current(dict(p, activity_at=now), [merged["codex:t-run"]]),
              "`doing` from before the newest session reads as an earlier session's")

        png = b"\x89PNG\r\n\x1a\n" + os.urandom(64)
        shot = Path(tmp) / "shot.png"
        shot.write_bytes(png)
        with Board() as b:
            mid = b.add_message(c, a, "The seam", with_images("see", [shot]), "fyi")
            paths = [Path(line.split("] ", 1)[1]) for line in image_lines(b.message(mid)["body"])]
        check(len(paths) == 1 and paths[0].read_bytes() == png, "an image is stored and its local path shown")
        told = post_hook()
        check(bool(told) and str(paths[0]) in json.loads(told)["hookSpecificOutput"]["additionalContext"],
              "hook-post names the image's path")
        try:
            store_image(b"not an image")
            check(False, "a non-image is refused")
        except BoardError:
            check(True, "a non-image is refused")

        # HTTP: CSRF guard and a round trip
        os.environ["FE_BOARD_PORT"] = str(free_port())
        srv = ThreadingHTTPServer(("127.0.0.1", port()), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            h = server_health()
            check(bool(h and h["version"] == SERVER_VERSION), "server answers /api/health")
            body = json.dumps({"body": "from the browser"}).encode()
            req = urllib.request.Request(url() + "/api/note", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=2)
                check(False, "a POST without X-Board is refused")
            except urllib.error.HTTPError as e:
                check(e.code == 403, "a POST without X-Board is refused")
            req.add_header("X-Board", "1")
            res = json.loads(urllib.request.urlopen(req, timeout=2).read())
            with Board() as b:
                check(b.note(res["id"])["author"] == "owner", "a POST from the UI writes as owner")
            with urllib.request.urlopen(url() + "/api/state", timeout=5) as r:
                snap = json.loads(r.read())
            check(len(snap["tasks"]) == 2 and snap["open_questions"] == [], "/api/state returns the board")
            page = urllib.request.urlopen(url() + "/", timeout=2).read()
            check(f"<title>{studio_config.name()} Board</title>".encode() in page, "the UI page is served, named for the project")
            with urllib.request.urlopen(url() + "/api/project", timeout=5) as r:
                check(json.loads(r.read()) == snap["project"] == project_info(), "/api/project names the project")
            up = urllib.request.Request(url() + "/api/upload", data=b"GIF, honest", method="POST",
                                        headers={"X-Board": "1", "X-Filename": "bad.gif"})
            try:
                urllib.request.urlopen(up, timeout=2)
                check(False, "the upload route refuses a non-image")
            except urllib.error.HTTPError as e:
                check(e.code == 400, "the upload route refuses a non-image")
            png2 = b"\x89PNG\r\n\x1a\n" + os.urandom(64)
            up = urllib.request.Request(url() + "/api/upload", data=png2, method="POST",
                                        headers={"X-Board": "1", "X-Filename": "pasted%20shot.png"})
            res = json.loads(urllib.request.urlopen(up, timeout=2).read())
            got = urllib.request.urlopen(url() + res["url"], timeout=2).read()
            check(got == png2 and res["markdown"].startswith("![pasted shot]"), "an uploaded image is served back")
        finally:
            srv.shutdown()
            srv.server_close()

        check(backup() is not None and export(Path(tmp) / "export").joinpath("board.md").exists(),
              "backup and export")
        with contextlib.closing(sqlite3.connect(str(db_path()))) as con:
            con.execute(f"PRAGMA user_version={SCHEMA + 1}")
        try:
            Board().close()
            check(False, "a newer store refuses this tool")
        except BoardError:
            check(True, "a newer store refuses this tool")
    finally:
        for k, v in (("FE_BOARD_DIR", old_dir), ("FE_BOARD_PORT", old_port), ("FE_AGENT", old_agent),
                     ("CODEX_HOME", old_codex)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{TAG} selftest: {'all passed' if not failures else f'{len(failures)} failed'}")
    return 1 if failures else 0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ----------------------------------------------------------------- main

def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--as", dest="who", default=argparse.SUPPRESS, help="act as owner, A1, A2, ...")
    body = argparse.ArgumentParser(add_help=False)
    body.add_argument("--body-file", help="read the body from a file, or - for stdin")
    body.add_argument("--image", action="append", metavar="PATH", help="attach an image (repeatable)")

    p = argparse.ArgumentParser(prog="fe_board.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("inbox", parents=[common])
    s = sub.add_parser("next", parents=[common])
    s.add_argument("--claim", action="store_true")
    sub.add_parser("who", parents=[common])
    s = sub.add_parser("doing", parents=[common])
    s.add_argument("text", nargs="+")
    s.add_argument("--task")
    s = sub.add_parser("note", parents=[common, body])
    s.add_argument("text", nargs="+")
    s.add_argument("--tag")
    s = sub.add_parser("notes", parents=[common])
    s.add_argument("--all", action="store_true")

    s = sub.add_parser("task", parents=[common, body])
    s.add_argument("action", choices=["new", "list", "show", "claim", "start", "release", "review",
                                      "block", "attach", "link", "set", "done", "drop"])
    s.add_argument("ids", nargs="*", help="task id, then action arguments")
    s.add_argument("--body")
    s.add_argument("--prio", type=int, default=2)
    s.add_argument("--to", default="any")
    s.add_argument("--status", default=None)
    s.add_argument("--from-note")
    s.add_argument("--depends")
    s.add_argument("--mine", action="store_true")
    s.add_argument("--all", action="store_true")
    s.add_argument("--commit")
    s.add_argument("--version")
    s.add_argument("--decision")
    s.add_argument("--note")

    s = sub.add_parser("msg", parents=[common, body])
    s.add_argument("to")
    s.add_argument("subject")
    s.add_argument("body", nargs="?")
    s.add_argument("--kind", default="fyi")
    s.add_argument("--task")
    s = sub.add_parser("reply", parents=[common, body])
    s.add_argument("id")
    s.add_argument("body", nargs="?")
    s.add_argument("--kind", default="reply")
    s = sub.add_parser("thread", parents=[common])
    s.add_argument("id")
    s = sub.add_parser("read", parents=[common])
    s.add_argument("ids", nargs="+")
    s = sub.add_parser("messages", parents=[common])
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("attach", parents=[common])
    s.add_argument("paths", nargs="+")

    s = sub.add_parser("serve")
    s.add_argument("--port", type=int)
    s = sub.add_parser("open")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s = sub.add_parser("export")
    s.add_argument("dir")
    sub.add_parser("backup")
    sub.add_parser("selftest")
    s = sub.add_parser("digest", parents=[common])
    s.add_argument("--touch", action="store_true")
    sub.add_parser("hook-post")
    sub.add_parser("hook-stop")
    return p


COMMANDS = {"inbox": cmd_inbox, "next": cmd_next, "who": cmd_who, "doing": cmd_doing,
            "note": cmd_note, "notes": cmd_notes, "task": cmd_task, "msg": cmd_msg,
            "reply": cmd_reply, "thread": cmd_thread, "read": cmd_read, "messages": cmd_messages,
            "attach": cmd_attach}


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if argv and argv[0] == "hook-post":
        return cmd_hook_post()
    if argv and argv[0] == "hook-stop":
        return cmd_hook_stop()
    if argv and argv[0] == "hook-end":
        return cmd_hook_end()
    args = parser().parse_args(argv)
    try:
        if args.cmd is None:
            print(__doc__)
            return 2
        if args.cmd == "serve":
            return serve(args.port)
        if args.cmd == "open":
            ok = ensure_server(quiet=args.quiet)
            if ok and not args.no_browser:
                webbrowser.open(url())
            if not args.quiet:
                print(f"{TAG} {url()} ({'up' if ok else 'down'})")
            return 0 if ok or args.quiet else 1
        if args.cmd == "export":
            print(f"{TAG} exported to {export(args.dir)}")
            return 0
        if args.cmd == "backup":
            print(f"{TAG} {backup() or 'nothing to back up'}")
            return 0
        if args.cmd == "selftest":
            return selftest()
        if args.cmd == "digest":
            print("\n".join(digest_lines(whoami(getattr(args, "who", None)), touch=args.touch)))
            return 0
        if args.cmd == "task" and args.status is None:
            args.status = "idea" if args.action == "new" else None
        me = need_me(args)
        with Board() as b:
            codex_touch(b, me)
            return COMMANDS[args.cmd](b, me, args)
    except BoardError as e:
        print(f"{TAG} {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
