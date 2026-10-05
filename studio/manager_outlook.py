"""What the manager is doing, what waits and on what, and what became of Yotam's
messages (D210): one reading of the manager's tables for the board's Manager and
Agents views, `fe_board.py who`, `fe_manager.py status` and Discord's receipt and
/status, so all of them say the same thing. Reads only; no network or model calls."""
import json
import sqlite3
import time

import fe_board
import manager_models as models
import manager_store as store
import manager_usage as usage
import manager_workers as workers
import studio_config

ONLINE_S = 150  # a tick runs at least every POLL_S (60 s); past this the service is not stepping
STEPS = 4       # a run's last steps shown with it


def _ago(ts, now):
    """How long since ts, '25m'."""
    if not ts:
        return '?'
    s = max(0, now - ts)
    return f'{int(s)}s' if s < 60 else f'{int(s // 60)}m' if s < 3600 else \
        f'{int(s // 3600)}h {int(s % 3600 // 60)}m' if s < 86400 else f'{int(s // 86400)}d'


def _goal_title(g):
    return g['name'] or fe_board.clip(g['body'], 80)


def _title(b, tid):
    r = b.q1('SELECT title FROM tasks WHERE id=?', tid)
    return r[0] if r else ''


def _run_name(r):
    if r['task_id']:
        return f"T{r['task_id']}'s {r['role']}"
    return f"the {r['role']} for G{r['goal_id']}" if r['goal_id'] else f"a {r['role']}"


def outlook(b, config=None, now=None, steps=True, docs_too=True, models_too=True):
    """{mode, online, providers, usage, slots, used, running, waiting, inbox}. `usage` is how much
    of each provider's allowance is spent (D243, manager_usage). The slots and the
    providers ready are what the service published at its last tick (store.SLOTS), so a
    reader never claims capacity the service does not see; `config` (the manager's,
    manager_host.load_config) says why a provider is out. With neither: one slot."""
    now = time.time() if now is None else now
    mode = store.setting(b, 'mode', 'paused')
    beat = float(store.setting(b, 'heartbeat', '0'))
    try:
        published = json.loads(store.setting(b, store.SLOTS, 'null'))
    except ValueError:
        published = None
    here = store.available(b, dict(config), now) if config is not None else []
    # Planners have their own lane (D216): the run slots are the workers'.
    used = b.q1("SELECT count(*) FROM pm_runs WHERE role NOT IN ('planner','quick') AND state IN ('queued','running')")[0]
    ready = published['providers'] if published else here
    # A service from before D210 publishes nothing: its slots are taken as the ones it fills,
    # so nothing is said to start that it may not start.
    slots = published['slots'] if published else max(1, min(used, store.slots(here)))
    providers = []
    if published or config is not None:
        blocks = {r['name']: dict(r) for r in b.q('SELECT * FROM pm_providers')}
        for p in store.PROVIDERS:
            if p in ready:
                providers.append({'name': p, 'state': 'ready', 'why': ''})
            elif p in blocks and blocks[p]['retry_at'] > now:
                providers.append({'name': p, 'state': 'blocked', 'why': store.provider_line(blocks[p], now)})
            elif config is not None and not config.get(p):
                providers.append({'name': p, 'state': 'missing', 'why': 'not configured'})
            elif p in here:
                providers.append({'name': p, 'state': 'missing', 'why': 'not found by the service at its last '
                                  'tick; it is there now, and the next tick takes it'})
            else:
                providers.append({'name': p, 'state': 'missing', 'why': 'not found at '
                                  + str((config or {}).get(p) or 'its configured path')})
    running = []
    rows = [dict(r) for r in b.q("SELECT * FROM pm_runs WHERE state IN ('queued','running') ORDER BY created")]
    spent = models.runs_stats(b, [r['id'] for r in rows], now)  # D218: its model and what it spent so far
    tasks_spent = models.tasks_stats(b, [r['task_id'] for r in rows if r['task_id']], now)
    for r in rows:
        s = spent.get(r['id']) or {}
        recent = workers.last_activity(r['id'], 12) if steps else []
        land = workers.landing(r, now) if steps and r['state'] == 'running' else ''
        if land:  # the worker is done; its land is what runs (D257)
            recent.append(land)
        ran =[x[len('ran: '):] for x in recent if x.startswith('ran: ')]
        running.append({
            'stats': tasks_spent.get(r['task_id']) or s,
            'latest': workers.last_words(r['id']) if steps else '', 'doing': ran[-1] if ran else '',
            'tier': s.get('tier', ''), 'model': s.get('model', ''),
            'model_label': models.label(s.get('model') or s.get('tier') or r['provider']),
            'why': (tasks_spent.get(r['task_id']) or {}).get('why', ''),
            'spent': s.get('line', ''), 'task_spent': (tasks_spent.get(r['task_id']) or {}).get('line', ''),
            'run': r['id'], 'name': _run_name(r), 'role': r['role'], 'provider': r['provider'],
            'state': r['state'], 'task': r['task_id'], 'goal': r['goal_id'], 'agent': r['agent'],
            'title': _title(b, r['task_id']) if r['task_id'] else _goal_title(
                b.q1('SELECT * FROM pm_goals WHERE id=?', r['goal_id'])) if r['goal_id'] else '',
            'started': r['pid_started'] or r['created'], 'beat': r['heartbeat'], 'session': r['session'],
            'steps': recent[-STEPS:]})
    def names(rs):
        return ', '.join(f"{x['name']} ({x['model_label']}, {_ago(x['started'], now)})" for x in rs)
    busy = names(x for x in running if x['role'] not in ('planner', 'quick'))
    planners = [x for x in running if x['role'] == 'planner']
    planning = '' if len(planners) < store.PLANNERS else \
        f'all {store.PLANNERS} planners are running: {names(planners)}'
    if mode != 'running':
        halt = f'the manager is {mode}'
    elif now - beat > ONLINE_S:
        halt = f'the manager service is not stepping (last tick {fe_board.ago(beat)})'
    else:
        halt = ''
    # D262: the nightly bench holds new runs while it measures (read raw: the board's
    # own Python may have no psutil, and the service clears a hold whose bench died).
    hold = json.loads(store.setting(b, 'bench_hold') or 'null')
    if hold and not halt:
        paused = len(hold.get('suspended') or [])
        halt = (f"the nightly bench holds new runs (since {time.strftime('%H:%M', time.localtime(hold['since']))}"
                + (f'; {paused} worker process{"es" if paused != 1 else ""} paused while the GPU measures' if paused else '')
                + ')')
    full = '' if used < slots else f'the one run slot is in use: {busy}' if slots == 1 \
        else f'all {slots} run slots are in use: {busy}'
    if full and slots == 1:
        out = [p for p in providers if p['state'] != 'ready']
        if out:
            full += ' (one slot because ' + '; '.join(f"{p['name']} is out: {p['why']}" for p in out) + ')'
        elif not published:
            full += ' (the service serving predates D210 and does not say how many slots it has)'
    holds = fe_board.checkout_holds(b.con)
    checkouts = store.checkouts()  # the checkouts the manager runs tasks in
    try:
        parked = {r['task_id']: dict(r) for r in b.q('SELECT * FROM pm_parked WHERE restored=0')}
    except sqlite3.OperationalError:
        parked = {}  # a store from before D297
    pins ={tid: r['owner_tier'] for tid, r in models.routes(b).items() if r['owner_tier']}
    out_now = {p['name']: p for p in providers if p['state'] != 'ready'}
    waiting = []

    def wait(ref, title, why, you=False, docs=(), short=''):
        # short: the reason in a few words, for /status to group by ('after T43')
        waiting.append({'ref': ref, 'title': title, 'why': why, 'you': you, 'docs': list(docs),
                        'short': short or why})

    # The order schedule() takes them in: tasks to revise, ready tasks, then goals to plan.
    for m in b.q("SELECT m.*, t.status, t.title, t.depends FROM pm_tasks m JOIN tasks t ON t.id=m.task_id "
                 "ORDER BY CASE m.phase WHEN 'revise' THEN 0 WHEN 'ready' THEN 1 ELSE 2 END, m.task_id"):
        m = dict(m)
        tid, ref = m['task_id'], f"T{m['task_id']}"
        if any(x['task'] == tid for x in running) or m['status'] in ('done', 'dropped'):
            continue
        if m['status'] == 'idea':
            wait(ref, m['title'], 'a proposal: waits for you to approve it', True,
                 short='approve or drop the proposal')
        elif m['status'] == 'blocked' or m['phase'] == 'blocked':
            kept = parked.get(tid)  # D297, D314: its work kept aside so its checkout runs other work
            wait(ref, m['title'], 'blocked: waits on your answer'
                 + (f" (its work is parked at {kept['url']}, and {kept['agent']} runs other work meanwhile)"
                    if kept else ''),
                 True, short='answer its question' + (' (parked)' if kept else ''))
        elif m['status'] == 'review':
            # D212: what it wrote to be read is named with it, so reviewing starts there
            docs = [d['path'] for d in fe_board.task_docs(b, tid)] if docs_too else []
            wait(ref, m['title'], ('landed: waits for you to review and accept it' if m['phase'] == 'landed'
                 else 'in review on the board: the manager leaves it there')
                 + (' (read ' + ', '.join(docs) + ')' if docs else ''), True, docs,
                 short=('review and accept' if m['phase'] == 'landed' else 'in review on the board')
                 + (' (read ' + ', '.join(docs) + ')' if docs else ''))
        elif m['phase'] in ('revise', 'ready'):
            states = {}
            for d in (int(x) for x in (m['depends'] or '').split(',') if x):
                s = b.q1('SELECT status FROM tasks WHERE id=?', d)
                if s and s[0] not in ('done', 'dropped'):
                    states[d] = s[0]
            deps = list(states)
            # a task reopened in its own checkout waits for it too (D212): T37 for A2 while T43 holds it
            mine = holds.get(m['agent']) if m['agent'] else None
            taken = f"its checkout {m['agent']} is held ({fe_board.hold_text(mine)})" \
                if mine and mine['task'] != tid else ''
            accepted = ' is accepted' if all(s == 'review' for s in states.values()) else ''
            if deps:
                wait(ref, m['title'], 'waits on ' + ', '.join(f'T{d} ({states[d].replace("_", " ")})' for d in deps)
                     + (' to be accepted' if accepted else ''),
                     short='after ' + ', '.join(f'T{d}' for d in deps) + accepted)
            elif pins.get(tid) and models.provider_of(pins[tid]) in out_now:
                # D238: a task Yotam pinned to a model waits for that model's CLI; it is not
                # quietly run on another one.
                who = models.label(pins[tid])
                wait(ref, m['title'], f'pinned to {who}, which waits: '
                     + out_now[models.provider_of(pins[tid])]['why'], short=f'until {who} can run again')
            elif halt or full:
                wait(ref, m['title'], 'next in line, but ' + (halt or full) + ('; and ' + taken if taken else ''),
                     short=halt or 'next, when a run slot frees')
            elif taken:
                wait(ref, m['title'], 'waits: ' + taken, short=f"when {m['agent']} is free")
            elif not m['agent'] and all(a in holds for a in checkouts):
                wait(ref, m['title'], 'waits for a free checkout: ' + '; '.join(
                    f"{a} {fe_board.hold_text(holds[a])}" for a in checkouts), short='when a checkout frees')
            else:
                game = studio_config.get('game.label')  # a running game holds its checkout too (D165)
                wait(ref, m['title'], 'starts at the next tick if its checkout is free (no other session, '
                     + (f'no changes, no {game} there)' if game else 'no changes there)'), short='starts at the next tick')
        elif m['phase'] == 'failed':
            wait(ref, m['title'], 'failed: reply on it to try again', True, short='failed: reply on it to retry')
    for g in b.q("SELECT * FROM pm_goals WHERE status IN ('planning','blocked','talking') ORDER BY id"):
        if any(x['goal'] == g['id'] and x['role'] == 'planner' for x in running):
            continue
        title = _goal_title(g)
        if g['status'] == 'talking':  # D226: a planning conversation waits on him
            plan = 'plan' in g.keys() and g['plan']
            wait(f"G{g['id']}", title, 'a plan waits for you: Approve plan in its thread, or reply to change it'
                 if plan else 'the planner waits for your reply in its thread', True,
                 short='approve the plan, or reply' if plan else 'reply in its planning thread')
        elif g['status'] == 'blocked':
            wait(f"G{g['id']}", title, 'waits for your clarification', True, short='clarify the goal')
        else:
            wait(f"G{g['id']}", title, 'to be planned, but ' + (halt or planning) if halt or planning
                 else 'planning starts at the next tick')
    inbox = []
    for e in b.q('SELECT * FROM pm_inbox ORDER BY created DESC LIMIT 8'):
        e = dict(e)
        goal = b.q1('SELECT id, status FROM pm_goals WHERE source=?', e['id'])
        if e['state'] == 'rejected':
            became = 'rejected (the reason was posted back)'
        elif e['state'] == 'pending':
            became = 'not read yet: ' + (halt or 'the next tick reads it')
        elif e['kind'] in ('start', 'pause', 'resume', 'stop'):
            became = e['kind']
        elif e['kind'] in ('approve', 'drop', 'accept', 'reason'):
            became = {'approve': 'approved', 'drop': 'dropped', 'accept': 'accepted',
                      'reason': 'new reasoning for'}[e['kind']] + f" T{e['task_id']}"
        elif goal:
            became = f'goal G{goal[0]} ({goal[1]})'
        elif e['task_id']:
            run = next((x for x in running if x['task'] == e['task_id']), None)
            became = f"reply on T{e['task_id']}" + (
                f": its worker reads it when this run ends ({_ago(run['started'], now)} so far)"
                if run and run['started'] < e['created'] else '')
        else:
            became = 'added to the goal waiting on your clarification'
        inbox.append({'at': e['created'], 'kind': e['kind'], 'task': e['task_id'],
                      'body': fe_board.clip(' '.join(e['body'].split()), 140), 'became': became,
                      'goal': goal[0] if goal else None})
    return {'models': models.summary(b, now) if models_too else None,
            'mode': mode, 'heartbeat': beat, 'online': now - beat <= ONLINE_S, 'providers': providers,
            'usage': usage.usage(b, now),
            'slots': slots, 'used': used, 'halt': halt, 'full': full, 'planning': planning,
            'bench': halt if hold and halt.startswith('the nightly bench') else '',
            'planners': len(planners),
            'running': running, 'waiting': waiting, 'inbox': inbox, 'at': now}


def lines(o, now=None, steps=True, md=False):
    """The outlook as text, read at a glance: what is being worked on, for how long and
    the latest on it; what needs Yotam; what is queued and on what; then what each run
    has spent. md: Discord's markdown (/status), else plain text (`fe_manager.py status`)."""
    now = time.time() if now is None else now
    bold = (lambda x: f'**{x}**') if md else (lambda x: x)
    quote, small, item = ('> ', '-# ', '- ') if md else ('    ', '    ', '  ')
    out = [bold(f"Manager {o['mode']}" + ('' if o['online'] else ', not stepping'))
           + f" · {o['used']} of {o['slots']} run slot{'s' if o['slots'] > 1 else ''} in use"
           + (f" · {o['planners']} planning" if o.get('planners') else '')]
    out += [f"{item}{p['name']}: {p['why']}" for p in o['providers'] if p['state'] != 'ready']
    if o.get('bench'):
        out.append(item + o['bench'][0].upper() + o['bench'][1:])
    if o.get('usage'):
        # D243: account-wide, so Yotam's own sessions count in it
        out += ['', bold('Allowance used') + " (the account's, so your own sessions count too)"]
        out += [f"{item}{bold(u['provider'].capitalize())}: {u['text']}" for u in o['usage']]

    out += ['', bold('Working on') if o['running'] else 'Nothing running.']
    for r in o['running']:
        st = r.get('stats') or {}
        ref = f"T{r['task']}" if r['task'] else f"G{r['goal']}" if r['goal'] else r['role']
        what = fe_board.clip(r['title'], 90) if r['task'] else 'planning: ' + fe_board.clip(r['title'], 80)
        out.append(f"{bold(ref)} {what}")
        took = _ago(r['started'], now) if r['state'] == 'running' else f"queued {_ago(r['started'], now)}"
        if st.get('runs', 0) > 1:
            took = f"{models.mins(st['working_s'])} over {st['runs']} runs (this one {took})"
        out.append(f"{item if not md else ''}{r['model_label']}" + (f" in {r['agent']}" if r['agent'] else '')
                   + f" · {took}" + (f" · {models.label(st['model'])} after an escalation"
                                     if st.get('escalations') else ''))
        if steps and r.get('latest'):
            out.append(quote + 'Latest: ' + fe_board.clip(r['latest'], 220))
        if steps and r.get('doing'):
            out.append(small + f"last step {_ago(r['beat'], now)} ago: " + fe_board.clip(r['doing'], 110))
        if md:
            out.append('')

    you = [w for w in o['waiting'] if w['you']]
    if you:
        out += ['' if not md or not o['running'] else None, bold('Needs you')]
        out += [f"{item}{bold(w['ref'])} {fe_board.clip(w['title'], 60)}: {w.get('short') or w['why']}" for w in you]
    queued = {}
    for w in o['waiting']:
        if not w['you']:
            queued.setdefault(w.get('short') or w['why'], []).append(w)
    if queued:
        out += ['', bold('Queued')]
        for why, ws in queued.items():
            if len(ws) == 1:
                out.append(f"{item}{bold(ws[0]['ref'])} {fe_board.clip(ws[0]['title'], 60)}: {why}")
            else:
                out.append(f"{item}{why}:")
                out += [f"{'  ' if md else '    '}{item}{bold(w['ref'])} {fe_board.clip(w['title'], 60)}" for w in ws]

    spent = [r for r in o['running'] if r.get('stats') or r.get('task_spent') or r.get('spent')]
    if spent:
        out += ['', bold('Stats')]
        for r in spent:
            ref = f"T{r['task']}" if r['task'] else f"G{r['goal']}" if r['goal'] else r['role']
            out.append(f"{item}{bold(ref)} {r.get('task_spent') or r.get('spent') or models.line(r['stats'])}")
    tiers = (o.get('models') or {}).get('tiers') or {}
    if any(t['tasks'] for t in tiers.values()):
        out.append(small + 'Every managed task so far: ' + '; '.join(
            f"{models.label(k)} {t['tasks']} tasks, {models.short(t['output_tokens'])} tokens out, "
            f"median {models.mins(t['median_working_s'])} working" for k, t in tiers.items() if t['tasks']))
    return [x for x in out if x is not None]


def receipt(o, tid=None):
    """What Discord answers a message with, said from where the manager actually is."""
    if o['halt']:
        return f"Received and saved, but {o['halt']}" + ('; use /resume to continue work.'
                                                       if o['mode'] != 'running' else '.')
    run = next((r for r in o['running'] if tid and r['task'] == tid), None)
    if run:
        return (f"Received. T{tid}'s worker is running ({_ago(run['started'], o['at'])} so far); "
                'your reply reaches it when this run ends. /status shows where it is.')
    if tid:
        return "Received. I'll act on it at the next step and post the result here."
    if o.get('planning'):
        return (f"Received. It is queued: {o['planning']}. Planning starts when one ends; "
                '/status shows where it is.')
    return "Received. Planning starts now and can take a few minutes; I'll post the result here."
