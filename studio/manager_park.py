"""A blocked task's work is parked so its checkout can run another task (D297, D314).

D191 made a blocked task hold its checkout until Yotam answers, so with A2 and A3 both
held, a ready task waited although a run slot was free. When a ready task needs a
checkout and none is free, the task blocked longest is parked: its dirty tree (modified
and untracked files, gitignored ones left out) becomes one WIP commit on top of its
commits, and the result is pushed, forced, to `fe/parked/T<n>` on origin, a branch
Yotam can open and read on GitHub; the checkout goes back to origin/main. When it runs
again it is restored as it was, fetched from that branch wherever it runs (its own
checkout when that is free, the same session resumes, or any free one, a new session
told where its work came from); the WIP commit is undone so the work reads as modified
and untracked again. The work stays on its own base; the worker's push rebases it as
for any long task. The branch is Yotam's to read only until he accepts or drops the
task: a sweep then deletes it from origin and drops its row.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import time

import fe_board
import fe_sync
import manager_store as store
import studio_config

DDL = ('CREATE TABLE IF NOT EXISTS pm_parked(task_id INTEGER PRIMARY KEY, agent TEXT NOT NULL, '
       'path TEXT NOT NULL, branch TEXT NOT NULL, url TEXT NOT NULL, tip TEXT NOT NULL, wip TEXT, '
       "restored INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL)")
ADDED = (  # a store from before D314 has the table without these (D181: no schema bump)
    ('pm_parked', 'branch', "TEXT NOT NULL DEFAULT ''"),
    ('pm_parked', 'url', "TEXT NOT NULL DEFAULT ''"),
    ('pm_parked', 'restored', 'INTEGER NOT NULL DEFAULT 0'),
)


def ensure_tables(con):
    con.execute(DDL)
    for table, column, kind in ADDED:
        cols = {c[1] for c in con.execute(f'PRAGMA table_info({table})')}
        if cols and column not in cols:
            con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')


def branch(tid):
    return f'fe/parked/T{tid}'


def parked(b, tid):
    """The task's row while it is parked; None once it is restored (its row then stays,
    marked restored, only so the branch is still found and deleted when it is done)."""
    row = b.q1('SELECT * FROM pm_parked WHERE task_id=? AND restored=0', tid)
    return dict(row) if row else None


def _git(path, *args, env=None, data=None, binary=False):
    try:
        p = (fe_sync.git(path, *args) if env is None and data is None and not binary else subprocess.run(
            ['git', *args], cwd=path, env=env, input=data, capture_output=True,
            timeout=120, creationflags=fe_sync.NO_WINDOW))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"git {' '.join(args)}: {e}") from e
    if p.returncode != 0:
        error = p.stderr or p.stdout
        if isinstance(error, bytes):
            error = error.decode('utf-8', errors='replace')
        raise RuntimeError(f"git {' '.join(args)}: {error.strip()[-400:]}")
    if binary:
        return p.stdout
    out = p.stdout.decode('utf-8', errors='surrogateescape') if isinstance(p.stdout, bytes) else p.stdout
    return out.strip()


def changes(path, *revs, env=None):
    """Mode, blob and pathname for each changed index/tree entry, without rename folding."""
    parts = _git(path, 'diff', '--raw', '--no-abbrev', '--no-renames', '-z', *revs, env=env).split('\0')
    for header, name in zip(parts[::2], parts[1::2]):
        fields = header.split()
        yield fields[1], fields[3], name


def raw_files(path, tip, base):
    """Overlay WIP blobs without checkout filters, preserving the original working bytes."""
    for mode, blob, name in changes(path, base, tip):
        dest = path / name
        if mode != '000000' and mode != '160000' and not dest.is_symlink():
            dest.write_bytes(_git(path, 'cat-file', 'blob', blob, binary=True))


def github_url(path, reg, br):
    url = _git(path, 'remote', 'get-url', reg['remote'])
    if url.startswith('git@github.com:'):
        url = 'https://github.com/' + url[len('git@github.com:'):]
    elif url.startswith('ssh://git@github.com/'):
        url = 'https://github.com/' + url[len('ssh://git@github.com/'):]
    if url.endswith('.git'):
        url = url[:-4]
    return f'{url}/tree/{br}'


def publish_legacy(b, reg, p):
    """Publish a pre-D314 row once; keep its old refs until acceptance as a safety copy."""
    path, br = Path(p['path']), branch(p['task_id'])
    tip = p['tip']
    if p['wip']:
        with tempfile.TemporaryDirectory(prefix='fe-park-') as tmp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'))
            _git(path, 'read-tree', p['wip'], env=env)  # stash's working tree
            _git(path, 'read-tree', '--prefix=', p['wip'] + '^3', env=env)  # untracked files
            tree = _git(path, 'write-tree', env=env)
            tip = _git(path, 'commit-tree', tree, '-p', p['tip'], '-m', f'T{p["task_id"]} parked work (WIP)')
    url = github_url(path, reg, br)
    _git(path, 'push', '-f', reg['remote'], f'{tip}:refs/heads/{br}')
    b.con.execute('UPDATE pm_parked SET branch=?,url=?,tip=?,wip=? WHERE task_id=?',
                  (br, url, tip, p['tip'] if p['wip'] else None, p['task_id']))
    return parked(b, p['task_id'])


def candidate(b, reg, sessions, procs, only=None):
    """(agent, task) for the held checkout whose task is blocked on the board with no
    worker, blocked longest (or `only` that checkout); None when every hold is a running
    or in-progress task, or the checkout is in use by anything but the task's own work
    (a session's turn, an app.exe, a rebase, another branch)."""
    holds = fe_board.checkout_holds(b.con)
    rows = []
    for agent, h in holds.items():
        if h['run'] or h['status'] != 'blocked' or agent not in reg['agents'] or only not in (None, agent):
            continue
        path = Path(reg['agents'][agent]['path'])
        if not (path / '.git').exists() or fe_sync.in_progress(path) \
                or fe_sync.branch_of(path) != reg['branch'] \
                or fe_sync.working_elsewhere(agent, sessions) \
                or fe_sync.owned_processes(agent, path, procs):
            continue
        rows.append((b.task(h['task'])['updated'], agent, h['task']))
    return min(rows)[1:] if rows else None


def park(b, reg, agent, tid, why=''):
    """Commit the dirty tree as one WIP commit, push HEAD to `fe/parked/T<n>` on origin
    (forced, so a second park of the same task replaces it), and give the checkout back
    clean at origin/main. No local ref is kept as a fallback: the branch on origin is the
    only copy, so it is Yotam's to open, and restoring needs only a fetch of it, from
    whichever checkout runs next, even one that never held this work.
    True when parked; on any git failure the checkout is left as it was found."""
    path = Path(reg['agents'][agent]['path'])
    upstream = f"{reg['remote']}/{reg['branch']}"
    br = branch(tid)
    base, dirty, reset_started = None, False, False
    try:
        base = _git(path, 'rev-parse', 'HEAD')
        index = Path(_git(path, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
        index_bytes = index.read_bytes() if index.exists() else None
        dirty = bool(_git(path, 'status', '--porcelain'))
        tip = base
        if dirty:
            # A private index captures the working tree without changing staging or
            # HEAD, even if add/commit/push fails. commit-tree also avoids user hooks.
            with tempfile.TemporaryDirectory(prefix='fe-park-') as tmp:
                snapshot = Path(tmp) / 'index'
                if index_bytes is not None:
                    snapshot.write_bytes(index_bytes)
                env = dict(os.environ, GIT_INDEX_FILE=str(snapshot))
                _git(path, 'add', '-A', env=env)
                # add normally cleans line endings/filters. WIP is the actual working
                # bytes; its blobs must survive restores in differently configured clones.
                for mode, _, name in list(changes(path, '--cached', base, env=env)):
                    if mode in ('000000', '160000'):
                        continue
                    file = path / name
                    data = os.fsencode(os.readlink(file)) if file.is_symlink() else file.read_bytes()
                    blob = _git(path, 'hash-object', '-w', '--no-filters', '--stdin', data=data)
                    _git(path, 'update-index', '--cacheinfo', mode, blob, name, env=env)
                tree = _git(path, 'write-tree', env=env)
                tip = _git(path, 'commit-tree', tree, '-p', base, '-m', f'T{tid} parked work (WIP)')
        url = github_url(path, reg, br)
        ahead = int(_git(path, 'rev-list', '--count', f'{upstream}..{base}') or 0)
        _git(path, 'push', '-f', reg['remote'], f'{tip}:refs/heads/{br}')
        reset_started = True
        if dirty:
            _git(path, 'reset', '-q', '--hard', tip)  # track WIP additions so the next reset removes them
        _git(path, 'reset', '-q', '--hard', upstream)
    except (RuntimeError, OSError) as e:
        if reset_started:
            # A hard reset can fail after writing some files. Recover their snapshot
            # before putting HEAD and the exact original index back.
            _git(path, 'reset', '-q', '--hard', tip)
            _git(path, 'reset', '-q', '--mixed', base)
            if dirty:
                raw_files(path, tip, base)
            if index_bytes is None:
                index.unlink(missing_ok=True)
            else:
                index.write_bytes(index_bytes)
        b.event('manager', 'manager.park', fe_board.ref('T', tid), f'could not park in {agent}: {e}'[:300])
        return False
    wip = base if dirty else None
    with b.tx():
        b.con.execute('INSERT OR REPLACE INTO pm_parked '
                      '(task_id,agent,path,branch,url,tip,wip,restored,created) VALUES(?,?,?,?,?,?,?,?,?)',
                      (tid, agent, str(path), br, url, tip, wip, 0, time.time()))
        b.con.execute('UPDATE pm_tasks SET agent=NULL WHERE task_id=?', (tid,))
        store.release(b, 'task:' + str(tid), 'checkout:' + agent)
        kept = ' and '.join(x for x in (f'{ahead} commit(s)' if ahead else '',
                                        'its uncommitted changes' if wip else '') if x) or 'no changes'
        store.notify(b, f'park:{tid}:{tip[:12]}:{wip or ""}',
                     f'T{tid} waits on your answer, so its work ({kept}) is parked at {url} '
                     f'and {agent} runs {why or "other work"} meanwhile. When you answer, it is restored as it '
                     'was, in the first checkout that is free.', tid)
        b.event('manager', 'manager.park', fe_board.ref('T', tid), f'parked in {agent}: {kept} at {br}')
    return True


def restore(b, reg, agent, tid):
    """Put T<tid>'s parked work into `agent`'s clean checkout, fetched from its branch on
    origin (never from the clone it was parked in, which may no longer exist or hold it).
    Returns the note its worker is given, or raises RuntimeError and leaves the checkout
    at origin/main. The `pm_parked` row stays, marked restored, so a later sweep still
    finds the branch and deletes it once the task is done or dropped."""
    p = parked(b, tid)
    path = Path(reg['agents'][agent]['path'])
    upstream = f"{reg['remote']}/{reg['branch']}"
    try:
        if not p['branch']:
            p = publish_legacy(b, reg, p)
        br = p['branch']
        _git(path, 'fetch', '-q', reg['remote'], br)
        _git(path, 'reset', '-q', '--hard', 'FETCH_HEAD')
        if p['wip']:
            _git(path, 'reset', '-q', '--mixed', p['wip'])  # undoes the WIP commit (D297's open point)
            raw_files(path, p['tip'], p['wip'])
    except (RuntimeError, OSError) as e:
        fe_sync.git(path, 'reset', '-q', '--hard', upstream)
        fe_sync.git(path, 'clean', '-fdq')
        raise RuntimeError(str(e)) from e
    b.con.execute('UPDATE pm_parked SET restored=1 WHERE task_id=?', (tid,))
    b.event('manager', 'manager.unpark', fe_board.ref('T', tid), f'restored in {agent} from {p["branch"]}')
    who, scratch = studio_config.owner(), studio_config.rel(studio_config.artifacts_dir())
    if agent == p['agent']:
        return (f'While this task waited on {who} its work was parked and another task used this checkout. '
                'Your commits and uncommitted changes are restored exactly as you left them, on your '
                f'original base; gitignored files (builds, {scratch}) are as the other task left '
                'them, so rebuild before you run anything.')
    return (f"While this task waited on {who} its work was parked, and it is restored in {agent} ({path}); "
            f"it was in {p['agent']} ({p['path']}). A session does not move between checkouts, so none of the "
            'previous conversation comes with it. Your commits and uncommitted changes are restored exactly, '
            'on your original base: read the task, its board thread and `git status`, `git log` and '
            f'`git diff` before you continue. Gitignored files ({scratch}/keep, builds) stayed in '
            f'{p["path"]}; read evidence from there, and write new evidence here.')


def sweep(b, reg):
    """A parked branch is Yotam's to read only until he accepts or drops its task (D314):
    once `tasks.status` says done or dropped, delete it on origin and drop its row. Run
    from whichever registered checkout still exists; a delete that fails (origin
    unreachable, the branch already gone) is left for the next tick and never stops the
    manager."""
    for row in b.q("SELECT p.* FROM pm_parked p JOIN tasks t ON t.id=p.task_id "
                   "WHERE p.branch='' AND t.status NOT IN ('done','dropped')"):
        try:
            publish_legacy(b, reg, dict(row))
        except (RuntimeError, OSError) as e:
            b.event('manager', 'manager.park', fe_board.ref('T', row['task_id']),
                    f'could not publish legacy parked work: {e}'[:300])
    for row in b.q("SELECT p.* FROM pm_parked p JOIN tasks t ON t.id=p.task_id "
                   "WHERE t.status IN ('done','dropped')"):
        p = dict(row)
        repos = list(dict.fromkeys(c for c in (Path(p['path']), *(Path(a['path']) for a in reg['agents'].values()))
                                  if (c / '.git').exists()))
        if not repos:
            continue
        try:
            if p['branch']:
                try:
                    _git(repos[0], 'push', reg['remote'], '--delete', p['branch'])
                except RuntimeError as e:
                    if 'remote ref does not exist' not in str(e):
                        raise
            for repo in repos:
                refs = [f'refs/fe/parked/T{p["task_id"]}', f'refs/fe/parked/T{p["task_id"]}-wip']
                if p['branch']:
                    refs += [f'refs/heads/{p["branch"]}', f'refs/remotes/{reg["remote"]}/{p["branch"]}']
                for ref in refs:
                    _git(repo, 'update-ref', '-d', ref)
        except RuntimeError as e:
            b.event('manager', 'manager.park', fe_board.ref('T', p['task_id']),
                     f'could not delete branch {p["branch"]}: {e}'[:300])
            continue
        b.con.execute('DELETE FROM pm_parked WHERE task_id=?', (p['task_id'],))
