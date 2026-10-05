"""Durable manager state in the board database. No network or model calls here."""
import json
import os
import time
import uuid

import studio_config

DDL = """
CREATE TABLE pm_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO pm_settings VALUES('mode', 'paused');
CREATE TABLE pm_goals(
 id INTEGER PRIMARY KEY, body TEXT NOT NULL, source TEXT UNIQUE NOT NULL,
 status TEXT NOT NULL DEFAULT 'planning', created REAL NOT NULL, name TEXT NOT NULL DEFAULT '');
CREATE TABLE pm_tasks(
 task_id INTEGER PRIMARY KEY REFERENCES tasks(id), goal_id INTEGER NOT NULL,
 agent TEXT, phase TEXT NOT NULL DEFAULT 'ready', head TEXT, base TEXT,
 evidence TEXT, reviewed_head TEXT, landed_head TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 feedback TEXT NOT NULL DEFAULT '', worker_provider TEXT, worker_session TEXT);
CREATE TABLE pm_runs(
 id TEXT PRIMARY KEY, task_id INTEGER, goal_id INTEGER, role TEXT NOT NULL,
 provider TEXT NOT NULL, cwd TEXT NOT NULL, agent TEXT, session TEXT,
 state TEXT NOT NULL DEFAULT 'queued', pid INTEGER, pid_started REAL,
 created REAL NOT NULL, heartbeat REAL NOT NULL, prompt TEXT NOT NULL,
 result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE pm_leases(resource TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE pm_inbox(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, task_id INTEGER, body TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
CREATE TABLE pm_outbox(
 id INTEGER PRIMARY KEY, dedup TEXT UNIQUE NOT NULL, task_id INTEGER,
 body TEXT NOT NULL, ping INTEGER NOT NULL DEFAULT 0, files TEXT NOT NULL DEFAULT '[]',
 sent TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0);
CREATE TABLE pm_discord_threads(task_id INTEGER PRIMARY KEY, channel_id TEXT UNIQUE NOT NULL);
CREATE TABLE pm_providers(
 name TEXT PRIMARY KEY, reason TEXT NOT NULL, retry_at REAL NOT NULL,
 blocks INTEGER NOT NULL DEFAULT 0, window_s REAL NOT NULL DEFAULT 0);
"""

# Schema 3: how long this block lasts and how many in a row it is, so a wait that has
# to be guessed can grow instead of repeating half an hour for ever. A store made from
# DDL above is already v3; one made at v2 is altered.
MIGRATE_V3 = ('ALTER TABLE pm_providers ADD COLUMN blocks INTEGER NOT NULL DEFAULT 0',
              'ALTER TABLE pm_providers ADD COLUMN window_s REAL NOT NULL DEFAULT 0')


# D193: a goal's short name, added in place without a version bump: an older tool
# never reads it (its INSERTs name their columns), so no checkout has to pull first.
GOAL_NAME = "ALTER TABLE pm_goals ADD COLUMN name TEXT NOT NULL DEFAULT ''"


def goal_label(b, gid):
    """'G3 · V165 improvements', or 'G3' before it has a name."""
    r = b.q1('SELECT name FROM pm_goals WHERE id=?', gid)
    return f'G{gid} · {r[0]}' if r and r[0] else f'G{gid}'


def name_goal(b, gid, name, keep=False):
    """Name goal G`gid` (the planner's name keeps one Yotam already gave it)."""
    name = ' '.join(str(name).split())[:60]
    if not b.q1('SELECT 1 FROM pm_goals WHERE id=?', gid):
        raise ValueError(f'no goal G{gid}')
    if name:
        b.con.execute('UPDATE pm_goals SET name=? WHERE id=?' + (" AND name=''" if keep else ''), (name, gid))


# D195: a planned task opens with why it is done, which Yotam edits before he approves
# it and its worker plans its tests from. It is the head of the task's body, so an edit
# on the board changes it too.
WHY = '## Why'


def reasoning(body):
    """The task's Why section, or '' when its body has none."""
    lines = (body or '').splitlines()
    if not lines or lines[0].strip() != WHY:
        return ''
    end = next((i for i, l in enumerate(lines[1:], 1) if l.startswith('## ')), len(lines))
    return '\n'.join(lines[1:end]).strip()


def without_reasoning(body):
    """The task's body after its Why section: what to do and how it is accepted."""
    lines = (body or '').splitlines()
    if lines and lines[0].strip() == WHY:
        lines = lines[next((i for i, l in enumerate(lines[1:], 1) if l.startswith('## ')), len(lines)):]
    return '\n'.join(lines).strip()


def with_reasoning(body, why):
    """body with its Why section replaced by why (added when it has none)."""
    return f'{WHY}\n{why.strip()}\n\n' + without_reasoning(body)


def setting(b, key, default=None):
    r = b.q1('SELECT value FROM pm_settings WHERE key=?', key)
    return r[0] if r else default


def set_setting(b, key, value):
    b.con.execute('INSERT INTO pm_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (key, str(value)))


def control_key(tid, head):
    """The setting that records an Accept control was sent for this commit of this task (D284)."""
    return f'control:{tid}:{head[:12]}'


def control(b, action):
    modes = {'start': 'running', 'resume': 'running', 'pause': 'paused', 'stop': 'stopped'}
    if action not in modes:
        raise ValueError('unknown manager action')
    with b.tx():
        set_setting(b, 'mode', modes[action])
        b.event('owner', 'manager.control', None, action)
        if action == 'resume':
            b.con.execute('DELETE FROM pm_providers')
    if action == 'stop':
        stop_runs(b)
    return snapshot(b)


def stop_runs(b):
    # Also works when the supervisor is offline but a surviving wrapper is live.
    import manager_workers
    for r in b.q("SELECT * FROM pm_runs WHERE state IN ('running','queued')"):
        manager_workers.stop_owned(dict(r), b)
        with b.tx():
            b.con.execute("UPDATE pm_runs SET state='handled',error='Stopped by owner' WHERE id=?", (r['id'],))
            if r['task_id']:
                b.con.execute("UPDATE pm_tasks SET phase='revise',feedback='Owner stopped this run; resume from preserved work.' "
                              "WHERE task_id=?", (r['task_id'],))


def snapshot(b):
    return {'mode': setting(b, 'mode', 'paused'),
            'heartbeat': float(setting(b, 'heartbeat', '0')),
            'runs': [dict(r) for r in b.q("SELECT r.id,r.task_id,r.goal_id,r.role,r.provider,r.state,r.agent,r.heartbeat,"
                                        "r.error,s.model FROM pm_runs r LEFT JOIN pm_run_stats s ON s.run_id=r.id "
                                        "ORDER BY r.created DESC LIMIT 20")],
            # a goal's planner runs and the model the last one ran on, for its transcript link (D222)
            'goals': [dict(r) for r in b.q(
                "SELECT g.*, (SELECT count(*) FROM pm_runs r WHERE r.goal_id=g.id AND r.task_id IS NULL) AS planner_runs, "
                "(SELECT s.model FROM pm_runs r JOIN pm_run_stats s ON s.run_id=r.id WHERE r.goal_id=g.id "
                "AND r.task_id IS NULL ORDER BY r.created DESC LIMIT 1) AS planner_model "
                "FROM pm_goals g ORDER BY g.id DESC LIMIT 20")],
            'providers': [dict(r) for r in b.q('SELECT name,reason,retry_at,blocks,window_s FROM pm_providers ORDER BY name')]}


def block_provider(b, name, reason, retry_at, blocks, window_s):
    b.con.execute('INSERT INTO pm_providers VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                  'reason=excluded.reason,retry_at=excluded.retry_at,blocks=excluded.blocks,'
                  'window_s=excluded.window_s', (name, reason, retry_at, blocks, window_s))


def clear_provider(b, name):
    """A run this provider carried through ends its block and its escalation: the next
    one starts again at the shortest wait."""
    b.con.execute('DELETE FROM pm_providers WHERE name=?', (name,))


PROVIDERS = ('claude', 'codex')
SLOTS = 'slots'  # the service's {slots, providers} at its last tick (D210)


def available(b, config, now=None):
    """Every provider configured and not waiting out a block, claude first. A CLI that
    updated itself into a new folder since the config was read (Codex: bin/<hash>/) is
    found again here and the config follows it, so an update mid-service does not
    quietly take a provider, and with it a run slot, away (D210)."""
    from manager_host import moved
    now = time.time() if now is None else now
    out = []
    for p in PROVIDERS:
        if config.get(p):
            config[p] = moved(config[p])
        blocked = b.q1('SELECT retry_at FROM pm_providers WHERE name=?', p)
        if config.get(p) and os.path.isfile(config[p]) and (not blocked or blocked[0] < now):
            out.append(p)
    return out


RUNS = 2  # D212: two runs at a time, A2 and A3, on Claude (Opus) when it is the one left
# D216: planners only read and hold no checkout, so they take none of the RUNS: a goal is
# planned while both workers run. Two at once, so a burst of messages cannot start ten.
PLANNERS = 2


def slots(providers):
    """How many runs at once. D197 held one provider to one run, so as not to spend its
    allowance twice as fast; Yotam chose two (D212): with Codex spent, both run on Opus.
    Which provider a run should take is a later routing rule."""
    return RUNS


def provider_line(p, now=None):
    """The one line the Manager tab and fe_manager.py status both show."""
    now = time.time() if now is None else now
    fmt = '%H:%M' if p['retry_at'] - now < 24 * 3600 else '%Y-%m-%d %H:%M'
    return (f"{p['name']} waiting: {p['reason']}, retrying at "
            + time.strftime(fmt, time.localtime(p['retry_at'])))


def notify(b, key, body, task=None, ping=False, files=(), goal=None):
    """Queue a Discord message: to a task's thread, a goal's planning thread (D226), or the channel.
    Every board image the text shows goes with it as an attachment (D304)."""
    files = with_images_of(body, files)
    if goal:
        b.con.execute('INSERT OR IGNORE INTO pm_outbox(dedup,task_id,body,ping,files,goal_id) VALUES(?,?,?,?,?,?)',
                      (key, task, body, int(ping), json.dumps(files), goal))
        return
    b.con.execute('INSERT OR IGNORE INTO pm_outbox(dedup,task_id,body,ping,files) VALUES(?,?,?,?,?)',
                  (key, task, body, int(ping), json.dumps(files)))


def with_images_of(text, files=()):
    """FILES plus each stored board image TEXT marks as ![..](/files/NAME), once each, in
    order (D304): a message that shows an image on the board carries it to Discord too."""
    import fe_board
    out = [str(f) for f in files]
    for m in fe_board.IMAGE_RE.finditer(text or ''):
        p = fe_board.files_dir() / m[2]
        if p.is_file() and str(p) not in out:
            out.append(str(p))
    return out


def receive(b, event_id, kind, body='', task=None, goal=None):
    """Called only after transport authentication. Atomically deduplicate input. goal: said
    in that goal's planning thread (D226)."""
    with b.tx():
        n = b.con.execute('INSERT OR IGNORE INTO pm_inbox VALUES(?,?,?,?,?,?)',
                         (str(event_id), kind, task, body, 'pending', time.time())).rowcount
        if n and goal:
            b.con.execute('INSERT OR IGNORE INTO pm_inbox_goal VALUES(?,?)', (str(event_id), goal))
    return bool(n)


def lease(b, resource, owner, seconds=180, now=None):
    now = time.time() if now is None else now
    with b.tx():
        r = b.q1('SELECT * FROM pm_leases WHERE resource=?', resource)
        if r and r['owner'] != owner and r['expires'] > now:
            return False
        b.con.execute('INSERT INTO pm_leases VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE '
                      'SET owner=excluded.owner,expires=excluded.expires', (resource, owner, now + seconds))
    return True


def release(b, owner, resource=None):
    b.con.execute('DELETE FROM pm_leases WHERE owner=?' + (' AND resource=?' if resource else ''),
                  (owner, resource) if resource else (owner,))


TASK_HOURS = 3  # D201: a task's worker time before it stops and reports where it got to
OVERTIME_GRACE_S = 20 * 60  # told at TASK_HOURS; its run is ended this much later if it has not reported


def task_clock(b, tid, now=None):
    """(seconds, runs): the worker time a task has taken since Yotam last approved it
    or answered on it (D201). His answer starts a fresh TASK_HOURS."""
    now = time.time() if now is None else now
    since = b.q1('SELECT MAX(created) FROM pm_inbox WHERE task_id=?', tid)[0] or 0
    total, runs = 0.0, 0
    for r in b.q("SELECT state, created, pid_started, heartbeat FROM pm_runs WHERE task_id=? AND role='worker' "
                 "AND state<>'queued' AND created>=?", tid, since):
        start = r['pid_started'] or r['created']
        total += max(0.0, (now if r['state'] == 'running' else r['heartbeat']) - start)
        runs += 1
    return total, runs


def hours(seconds):
    h, m = divmod(int(seconds // 60), 60)
    return f'{h} h {m:02d} min'


def overtime_note(b, rid, now=None):
    """What a managed run is told, once, when its task passes TASK_HOURS (D201); ''
    before then, after it was told, or for a run that is not a worker's."""
    r = b.q1("SELECT task_id, role FROM pm_runs WHERE id=?", rid)
    if not r or r['role'] != 'worker' or setting(b, 'overtime:' + rid):
        return ''
    spent, _ = task_clock(b, r['task_id'], now)
    if spent < TASK_HOURS * 3600:
        return ''
    set_setting(b, 'overtime:' + rid, str(time.time() if now is None else now))
    return (f'[fe-manager] T{r["task_id"]} has taken {hours(spent)} of its {TASK_HOURS} hours (D201). Stop now: '
            'start nothing new, end the processes you started, leave the work in the checkout (do not push '
            'unfinished work), and return status "blocked" with one line in summary. Write no report: the '
            f'manager reads your log and tells {studio_config.owner()} what blocked you and what the time went to (D321). The '
            f'manager ends this run in {OVERTIME_GRACE_S // 60} minutes if you have not returned.')


def checkouts(reg=None):
    """The checkouts the manager runs tasks in (D191): [routing] checkouts, else every registered
    one but the primary (the owner's own)."""
    names = studio_config.get('routing.checkouts')
    if names is None:
        import fe_board
        names = sorted(n for n, a in (reg or fe_board.registry())['agents'].items() if not a.get('primary'))
    return tuple(names)


def queue_run(b, role, provider, cwd, prompt, task=None, goal=None, agent=None, session=None, tier='', model=''):
    import manager_models
    rid = uuid.uuid4().hex
    now = time.time()
    b.con.execute('INSERT INTO pm_runs(id,task_id,goal_id,role,provider,cwd,agent,session,created,heartbeat,prompt) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (rid, task, goal, role, provider, str(cwd), agent, session, now, now, prompt))
    # D218: the model it runs on, and from here on what it spends.
    manager_models.queued(b, rid, task, goal, role, provider, tier, model or (provider if provider != 'claude' else ''), now)
    return rid


def accept(b, tid):
    # Whatever is on origin/main can be tested and accepted, even when the run that
    # pushed it was recorded as failed.
    if not b.land_pushed(tid) or b.task(tid)['status'] in ('done', 'dropped'):
        raise ValueError(f'T{tid} must be on origin/main (landed) and still open before acceptance')
    b.update_task('owner', tid, status='done')
    import manager_plan_docs
    manager_plan_docs.start(b, tid)


def adopt(b, tid, proposed=False):
    """D224: a task Yotam wrote by the task skill becomes the manager's, as if it had
    planned it: its own goal, a Discord thread, the model routing, the meter and the
    transcript. A proposal (idea) waits for Approve; a ready task starts when a slot
    and a checkout are free. Its body must open with its Why: the worker tests against it."""
    t = b.task(tid)
    if b.q1('SELECT 1 FROM pm_tasks WHERE task_id=?', tid):
        raise ValueError(f'T{tid} is already the manager\'s')
    if t['status'] in ('done', 'dropped', 'review'):
        raise ValueError(f'T{tid} is {t["status"]}: only open work can be handed over')
    if t['status'] in ('claimed', 'in_progress', 'blocked'):
        raise ValueError(f'T{tid} is {t["status"].replace("_", " ")} by {t["claimed_by"]}: release it first '
                         f'(fe_board.py task release T{tid})')
    if not reasoning(t['body']):
        raise ValueError(f'T{tid} has no "{WHY}" at the head of its body: write it by the task skill '
                         f'({studio_config.get("prompts.task_skill", ".agents/skills/task/SKILL.md")}) first')
    with b.tx():
        b.con.execute("INSERT INTO pm_goals(body,source,status,created,name) VALUES(?,?,'active',?,?)",
                      (f'T{tid}: {t["title"]}\n(written by {studio_config.owner()} and handed to the manager, D224)',
                       f'adopt:{tid}:{time.time()}', time.time(), ' '.join(t['title'].split())[:60]))
        gid = b.q1('SELECT max(id) FROM pm_goals')[0]
        if proposed:
            b.con.execute('UPDATE pm_goals SET body=? WHERE id=?',
                          (f'T{tid}: {t["title"]}\nProposed automatically from performance evidence; awaits owner approval.', gid))
        b.con.execute('INSERT INTO pm_tasks(task_id,goal_id) VALUES(?,?)', (tid, gid))
        b.add_links('manager', tid, [f'Goal G{gid}'])
        import manager_brief
        if t['status'] == 'idea':
            manager_brief.propose(b, f'task:{tid}', tid)
        else:
            notify(b, f'adopt:{tid}', f'T{tid} handed to the manager (G{gid}). It starts when a run slot and a '
                   'checkout are free; its transcript is on the board.', tid)
    return gid


def check_plan(tasks):
    """Plan-local dependency indices (zero based) must refer to earlier tasks."""
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
        raise ValueError('a goal needs 1..20 bounded tasks')
    for i, t in enumerate(tasks):
        deps = t.get('depends', [])
        if not isinstance(deps, list) or any(type(x) is not int or x < 0 or x >= i for x in deps):
            raise ValueError('dependencies must refer to earlier tasks')
        if not t.get('body', '').strip():
            raise ValueError('task needs scope and acceptance criteria')
        if not str(t.get('reasoning') or '').strip():
            raise ValueError('task needs its reasoning: why it is done')


def plan_tasks(b, goal_id, tasks, approved=False, key=None):
    """approved: an approved plan (D226), whose tasks are ready and start; else each is a
    proposal waiting for its own Approve (D195). key: the plan's, so a replay never
    creates it twice (a goal may have several plans)."""
    check_plan(tasks)
    ids = []
    with b.tx():
        done = f'plan-done:{goal_id}:{key}'
        if (key and setting(b, done)) or (not key and b.q1('SELECT task_id FROM pm_tasks WHERE goal_id=?', goal_id)):
            return  # replay after a crash: never duplicate a plan
        if key:
            set_setting(b, done, '1')
        for i, t in enumerate(tasks):
            deps = t.get('depends', [])
            body = with_reasoning('## What to do\n' + t['body'].strip(), t['reasoning'])
            tid = b.add_task('manager', t['title'], body, status='ready' if approved else 'idea',
                             depends=[ids[x] for x in deps], links=[f'Goal G{goal_id}'])
            ids.append(tid)
            b.con.execute('INSERT INTO pm_tasks(task_id,goal_id) VALUES(?,?)', (tid, goal_id))
            # Its own thread opens on what it fixes and why, with the buttons to decide.
            import manager_brief
            manager_brief.remember_problem(b, tid, t.get('problem'))
            import manager_models  # D218: the planner's suggested model, checked against the record at dispatch
            manager_models.suggest(b, tid, t.get('model') or '', t.get('model_reason') or '')
            if approved:
                manager_brief.post(b, f'task:{tid}', tid, f'From the approved plan for {goal_label(b, goal_id)}. It '
                                   'starts when a run slot and a checkout are free; this thread has its reports.',
                                   report=without_reasoning(body), why=reasoning(body))
            else:
                manager_brief.propose(b, f'task:{tid}', tid)
        b.con.execute("UPDATE pm_goals SET status='active' WHERE id=?", (goal_id,))
    return ids
