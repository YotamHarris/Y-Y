"""The manager runs pushed code only, and hands itself over to the next version (D189).

The service used to run from the manager's own checkout and start every worker
from that checkout's files, so an edit in progress there reached new workers
while the service itself still ran the code it had loaded at start: a worker
returned D187's new result fields to a service that did not know them, and every
result failed. Now:

- A **release** is a detached worktree of the manager's repo at an origin/main
  commit, under the board's `manager/releases/`. The service and every worker it
  starts run from one release, so nothing edited in a checkout reaches them.
- A **boot** (`fe_manager.py serve` from a checkout, which the sign-in task
  repeats every minute) only makes sure one service runs: when nobody holds the
  service lock it starts the last settled release (`manager/release.json`).
- The service watches origin/main. When a pushed commit changes a file it runs
  (a module it has loaded, or the manager's requirements), it checks the commit
  out as a release and starts it as a **candidate** beside itself. The candidate
  loads everything and checks itself, says it is ready and waits for the lock;
  the service lets go between two steps; the candidate takes over, and once its
  first step goes through it becomes the release a boot starts. A candidate that
  fails, or dies before settling, is rejected: the old service keeps serving (or
  a boot brings it back) and Yotam is told once. Its workers keep running from
  their own release, which is kept while any process runs from it.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import fe_board
import fe_sync
import manager_store as store
import studio_config

HANDOFF = 'release.handoff'      # the candidate's state: starting, ready, took, failed
REJECTED = 'release.rejected'    # code keys that failed as candidates, never retried unasked
UPGRADE = 'release.upgrade'      # set by `fe_manager.py upgrade`: check now, retry rejected
SERVICE = 'release.service'      # who serves: pid, commit, path, since
READY_S = 180                    # a candidate that is not ready by then is rejected
KEEP = 3                         # releases kept besides the ones in use
REQUIREMENTS = 'manager-requirements.txt'
log = logging.getLogger('manager.release')


def releases_dir():
    return fe_board.board_dir() / 'manager' / 'releases'


def pointer_path():
    return fe_board.board_dir() / 'manager' / 'release.json'


def tools():
    """The studio directory this code runs from: a release's, or a checkout's."""
    return Path(__file__).resolve().parent


def here():
    """The release (or checkout) this code runs in: the game's root around the studio."""
    return studio_config.repo_root()


def in_release():
    return here().parent == releases_dir().resolve()


def tool(name):
    """A tool of this very release: the workers and the GPU wrapper run the service's own code."""
    return tools() / name


def commit_of(path):
    return fe_sync.git_out(path, 'rev-parse', 'HEAD')


def manager_file(rel):
    """A file under the studio the service or a worker can import or run: every module
    (the nights' own processes import theirs only inside them: the crew's, D265, D280, and
    the usage report's), the board's UI, its TOML and the venv's requirements. A hand list
    missed fe_gpu, fe_docs and fe_land."""
    return rel.endswith(('.py', '.toml')) or rel.startswith('board_ui/') or rel.rsplit('/', 1)[-1] == REQUIREMENTS


def game_files():
    """The game's files the manager runs besides the studio: studio.toml, the adapters
    and the nightly jobs' modules (from the game's tools), and [manager] release_files."""
    tools = studio_config.rel(studio_config.game_tools_dir())
    mods = [*(studio_config.get('adapters') or {}).values(),
            *(j.get('module') for j in studio_config.get('nightly') or [])]
    return sorted({studio_config.CONFIG, *(f'{tools}/{m}.py' for m in mods if m),
                   *studio_config.get('manager.release_files', [])})


def gitlink(repo, commit, path):
    """The commit a submodule at `path` is pinned to, or None when `path` is not one."""
    meta = fe_sync.git_out(repo, 'ls-tree', commit, path).partition('\t')[0].split()
    return meta[2] if len(meta) == 3 and meta[0] == '160000' else None


def files(repo, commit):
    """{path: blob} of the files the manager runs, at a commit. A studio that is a submodule
    is one entry, the commit it is pinned to."""
    studio = studio_config.studio_rel()
    pinned = gitlink(repo, commit, studio)
    out = {studio: pinned} if pinned else {}
    if not pinned:
        for row in fe_sync.git(repo, 'ls-tree', '-r', commit, studio + '/').stdout.splitlines():
            meta, _, path = row.partition('\t')
            if manager_file(path[len(studio) + 1:]):
                out[path] = meta.split()[-1]
    for row in fe_sync.git(repo, 'ls-tree', commit, '--', *game_files()).stdout.splitlines():
        meta, _, path = row.partition('\t')
        out[path] = meta.split()[-1]
    return out


def key(repo, commit):
    """Two commits with one key run the same manager."""
    got = files(repo, commit)
    return hashlib.sha256('\n'.join(f'{n} {b}' for n, b in sorted(got.items())).encode()).hexdigest()[:16]


def ensure(repo, commit):
    """The release for a commit, checked out once and never changed after."""
    path = releases_dir() / commit[:12]
    if path.is_dir() and (path / '.git').exists() and commit_of(path) == commit and entry(path).is_file():
        return path
    releases_dir().mkdir(parents=True, exist_ok=True)
    if path.exists():
        fe_sync.git(repo, 'worktree', 'remove', '--force', str(path))
        fe_sync.git(repo, 'worktree', 'prune')
    p = fe_sync.git(repo, 'worktree', 'add', '--detach', '--force', str(path), commit, timeout=600)
    if p.returncode:
        raise RuntimeError(f'release {commit[:12]}: git worktree add failed: {fe_sync.tail(p.stderr, 2)}')
    if (path / '.gitmodules').is_file():
        # The studio as a submodule: the release runs the commit the game pins.
        p = fe_sync.git(path, 'submodule', 'update', '--init', timeout=600)
        if p.returncode:
            raise RuntimeError(f'release {commit[:12]}: git submodule update failed: {fe_sync.tail(p.stderr, 2)}')
    return path


def read_pointer():
    try:
        data = json.loads(pointer_path().read_text(encoding='utf-8'))
        return data if Path(data['path']).is_dir() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_pointer(path, commit):
    p = pointer_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'path': str(path), 'commit': commit, 'since': time.time()}, indent=2),
                   encoding='utf-8')
    tmp.replace(p)


def trunk(repo):
    reg = fe_sync.load_registry()
    note = fe_sync.maybe_fetch(repo, reg['remote'], force=True)
    commit = fe_sync.git_out(repo, 'rev-parse', f'{reg["remote"]}/{reg["branch"]}')
    return commit, note


def reading(repo):
    """Where a run that only reads works (D216): the trunk's worktree beside the releases,
    so a planner never waits for a checkout nor reads one an agent is changing. The
    service's watch fetches every minute; this fetches only when that has not. None when
    the repo has no trunk to check out."""
    if not (Path(repo) / '.git').exists():
        return None
    reg = fe_sync.load_registry()
    fe_sync.maybe_fetch(repo, reg['remote'])
    commit = fe_sync.git_out(repo, 'rev-parse', '--verify', '--quiet', f'{reg["remote"]}/{reg["branch"]}')
    return ensure(repo, commit) if commit else None


def entry(path):
    """A release's fe_manager.py: in the studio's configured place, or the place it had then."""
    return studio_config.studio_in(path) / 'fe_manager.py'


def spawn(path, *args):
    """Start a release's fe_manager.py with this interpreter, hidden, logging to service.log."""
    log = fe_board.board_dir() / 'manager' / 'service.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as out:
        return subprocess.Popen([sys.executable, str(entry(path)), *args],
                                cwd=str(path), stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                creationflags=fe_sync.NO_WINDOW,
                                env={**os.environ, 'FE_BOARD_DIR': str(fe_board.board_dir())})


def alive(pid):
    import psutil
    try:
        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != 'zombie'
    except (psutil.Error, ValueError):
        return False


def get(b, name):
    try:
        return json.loads(store.setting(b, name) or 'null')
    except ValueError:
        return None


def put(b, name, value):
    store.set_setting(b, name, json.dumps(value))


def rejected(b):
    return get(b, REJECTED) or []


def reject(b, h, reason, after='the previous version keeps running'):
    """A version that failed: never started again unasked, and Yotam hears once."""
    import manager_brief as brief
    log.warning('rejected %s: %s', h.get('commit', '')[:12], reason)
    with b.tx():
        put(b, REJECTED, sorted(set(rejected(b)) | {h['key']}))
        put(b, HANDOFF, dict(h, state='rejected', reason=reason))
        brief.post(b, f'release-rejected:{h["key"]}', None,
                   f'The manager at {h["commit"][:12]} did not start ({reason}); {after}.',
                   ['Push a fix to main and it is tried by itself; `fe_manager.py upgrade` retries this one.'],
                   report=h.get('log', ''), ping=True, problem_text='')


def boot(config):
    """Make sure one service runs. With none serving there is nothing to hand over from,
    so it starts the newest pushed manager, unless that one was rejected; then the
    settled release."""
    import manager_host as host
    if host.serving():
        return None
    repo = config['repo']
    with fe_board.Board() as b:
        s = get(b, SERVICE) or {}
        if s.get('commit') and not s.get('settled') and not alive(s.get('pid')):
            # The last start died before its first step: its code is not started again unasked.
            reject(b, dict(s, key=s.get('key') or key(repo, s['commit'])), 'it stopped before its first step',
                   'a boot starts the settled release instead')
            with b.tx():
                put(b, SERVICE, dict(s, settled=True, died=True))
        bad = rejected(b)
    try:
        commit, _ = trunk(repo)
    except (OSError, subprocess.SubprocessError):
        commit = ''
    ptr = read_pointer()
    if commit and key(repo, commit) not in bad:
        return spawn(ensure(repo, commit), 'serve')
    if ptr:
        return spawn(ptr['path'], 'serve')
    return None


def changes(repo, old, new):
    """The commits between two releases that changed what the manager runs. A studio that is
    a submodule adds its own log between the two commits the game pinned (best effort: the
    checkout's submodule may not have them)."""
    text = fe_sync.git_out(repo, 'log', '--format=%h %s', f'{old}..{new}', '--', *files(repo, new))
    studio = studio_config.studio_rel()
    a, b = gitlink(repo, old, studio), gitlink(repo, new, studio)
    if a and b and a != b:
        try:
            inner = fe_sync.git_out(Path(repo) / studio, 'log', '--format=%h %s', f'{a}..{b}')
        except (OSError, subprocess.SubprocessError):
            inner = ''
        text = '\n'.join(t for t in (inner, text) if t)
    return text


class Service:
    """The release side of a handoff: the serving process and its candidate."""

    def __init__(self, config, candidate=None):
        # A candidate carries the token its handoff record names: the pid a service
        # spawns is the venv's launcher, not the interpreter that answers.
        self.config, self.candidate, self.token = config, bool(candidate), candidate or None
        self.repo = config['repo']
        self.path = here()
        self.commit = commit_of(self.path) if in_release() else ''
        self.checked = 0.0
        self.child = None

    # -- the serving side ---------------------------------------------------

    def started(self, b):
        """This process holds the lock: record it, and settle what an earlier handoff left."""
        h = get(b, HANDOFF)
        if h and h.get('state') in ('starting', 'ready', 'took') and not self.mine(h) \
                and not alive(h.get('pid')) and h.get('commit') != self.commit:
            # A candidate that died before its first step (a boot brought back the settled release).
            reject(b, h, 'it stopped before its first step')
        with b.tx():
            # Settled after its first step, whoever started it.
            put(b, SERVICE, {'pid': os.getpid(), 'commit': self.commit, 'key': key(self.repo, self.commit),
                             'path': str(self.path), 'since': time.time(), 'settled': False})
            if self.candidate and self.mine(h):
                put(b, HANDOFF, dict(h, state='took', at=time.time()))
        log.info('serving %s (pid %s%s)', self.commit[:12], os.getpid(), ', took over' if self.candidate else '')

    def stepped(self, b):
        """After a step went through. A candidate becomes the release a boot starts."""
        s = get(b, SERVICE) or {}
        if s.get('settled') or s.get('pid') != os.getpid():
            return
        h = get(b, HANDOFF) or {}
        was = read_pointer() or {}
        write_pointer(self.path, self.commit)
        self.candidate = False  # the release now, so it watches for the next one
        with b.tx():
            put(b, SERVICE, dict(s, settled=True))
            put(b, HANDOFF, dict(h, state='settled', at=time.time()))
            if was.get('commit') != self.commit:
                log = changes(self.repo, was['commit'], self.commit) if was.get('commit') else ''
                store.notify(b, 'release:' + self.commit, f'Manager updated to {self.commit[:12]}'
                             + (f' (was {was["commit"][:12]})' if was.get('commit') else '') + '.'
                             + (('\n' + '\n'.join(log.splitlines()[:8])) if log else ''))

    def mine(self, h):
        return bool(h and self.token and h.get('token') == self.token)

    def yields(self, b):
        """A ready candidate for other code: the serving process lets go of the lock."""
        h = get(b, HANDOFF)
        go = bool(h and h.get('state') == 'ready' and not self.mine(h)
                  and h.get('commit') != self.commit and alive(h.get('pid')))
        if go:
            log.info('handing over to %s (pid %s)', h['commit'][:12], h['pid'])
        return go

    def watch(self, b, now=None):
        """Once a minute (or when asked): a pushed change to this manager starts a candidate."""
        now = time.time() if now is None else now
        if not in_release() or self.candidate:
            return None
        # A candidate in flight is followed to the end first: it takes over, or is rejected.
        self.follow(b, now)
        h = get(b, HANDOFF)
        if h and h.get('state') in ('starting', 'ready'):
            return None
        asked = store.setting(b, UPGRADE)
        if not asked and now - self.checked < float(os.environ.get('FE_MANAGER_UPGRADE_S', 60)):
            return None
        self.checked = now
        if asked:
            with b.tx():
                b.con.execute('DELETE FROM pm_settings WHERE key IN (?,?)', (UPGRADE, REJECTED))
        commit, note = trunk(self.repo)
        if not commit or commit == self.commit:
            return None
        want = key(self.repo, commit)
        if want == key(self.repo, self.commit) or want in rejected(b):
            return None
        path = ensure(self.repo, commit)
        token = os.urandom(8).hex()
        with b.tx():
            put(b, HANDOFF, {'state': 'starting', 'commit': commit, 'key': want, 'token': token,
                             'path': str(path), 'since': now, 'from': self.commit})
        self.child = spawn(path, 'serve', '--candidate', token)
        with b.tx():
            h = get(b, HANDOFF)
            if h.get('token') == token and h.get('state') == 'starting':
                put(b, HANDOFF, dict(h, pid=self.child.pid))
        log.info('candidate %s started (pid %s)', commit[:12], self.child.pid)
        return self.child

    def follow(self, b, now):
        """A candidate that fails or never gets ready is rejected, and stopped."""
        h = get(b, HANDOFF)
        if not h or h.get('state') not in ('starting', 'ready', 'failed'):
            return None
        if h['state'] == 'failed':
            reject(b, h, h.get('reason') or 'its start check failed')
        elif h.get('pid') and not alive(h.get('pid')):
            reject(b, h, 'it exited before it was ready')
        elif now - h['since'] > READY_S:
            stop(h.get('pid'))
            reject(b, h, f'it was not ready within {READY_S} s')
        return None

    # -- the candidate side -------------------------------------------------

    def check(self):
        """Load everything the service and a worker load; the host's own checks, sign-in aside."""
        import importlib
        import manager_host as host
        problems = []
        for m in ('manager_core', 'manager_workers', 'manager_brief', 'manager_store', 'manager_host'):
            try:
                importlib.import_module(m)
            except Exception as e:  # noqa: BLE001 - any failure to load is the reason it is rejected
                problems.append(f'{m}: {type(e).__name__}: {e}')
        if not os.environ.get('FE_MANAGER_OFFLINE'):
            try:
                importlib.import_module('manager_discord')
            except Exception as e:  # noqa: BLE001
                problems.append(f'manager_discord: {type(e).__name__}: {e}')
            problems += host.doctor(self.config, auth=False)
        return problems

    def announce(self, b, problems):
        """Ready (or failed), with this interpreter's own pid for the service to watch."""
        h = get(b, HANDOFF) or {}
        if not self.mine(h) or h.get('state') != 'starting':
            log.warning('candidate %s: the handoff record is not this one; leaving', self.commit[:12])
            return False
        with b.tx():
            if problems:
                put(b, HANDOFF, dict(h, pid=os.getpid(), state='failed', reason='; '.join(problems)[:500]))
            else:
                put(b, HANDOFF, dict(h, pid=os.getpid(), state='ready', ready=time.time()))
        log.info('candidate %s %s', self.commit[:12], 'failed: ' + '; '.join(problems) if problems else 'ready')
        return not problems

    def still_wanted(self, b):
        h = get(b, HANDOFF)
        return self.mine(h) and h.get('state') == 'ready'


def stop(pid):
    import psutil
    try:
        psutil.Process(int(pid)).kill()
    except (psutil.Error, ValueError, TypeError):
        pass


def prune(repo):
    """Old releases go, but never the settled one, the serving one, or one a process runs from."""
    import psutil
    root = releases_dir()
    if not root.is_dir():
        return []
    keep = set()
    ptr = read_pointer()
    if ptr:
        keep.add(Path(ptr['path']).resolve())
    keep.add(here().resolve())
    busy = set()
    for p in psutil.process_iter(['cmdline', 'cwd']):
        try:
            text = ' '.join(p.info['cmdline'] or []) + ' ' + (p.info['cwd'] or '')
        except (psutil.Error, TypeError):
            continue
        busy.add(text.lower())
    dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    gone = []
    for d in dirs[KEEP:]:
        if d.resolve() in keep or any(str(d).lower() in t for t in busy):
            continue
        fe_sync.git(repo, 'worktree', 'remove', '--force', str(d))
        gone.append(d.name)
    if gone:
        fe_sync.git(repo, 'worktree', 'prune')
    return gone


def line(b):
    s, ptr, h = get(b, SERVICE) or {}, read_pointer() or {}, get(b, HANDOFF) or {}
    text = (f'release: serving {s.get("commit", "")[:12] or "a checkout"} (pid {s.get("pid")})'
            f', settled {ptr.get("commit", "")[:12] or "none"}')
    if h.get('state') and h['state'] != 'settled':
        text += f'; handoff to {h.get("commit", "")[:12]}: {h["state"]}' + (f' ({h["reason"]})' if h.get('reason') else '')
    return text


def status(b):
    ptr = read_pointer() or {}
    return {'service': get(b, SERVICE), 'settled': ptr, 'handoff': get(b, HANDOFF), 'rejected': rejected(b)}
