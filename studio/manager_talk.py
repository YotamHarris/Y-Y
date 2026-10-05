"""Planning with Yotam first (D226): a goal is a conversation in its own Discord thread,
the way a Claude Code session in plan mode is. His message opens the thread; the planner
reads, answers and asks in a session that every reply of his resumes; it proposes a plan,
and **Approve plan** creates the tasks, which start. Nothing is edited while planning.

Goal states: planning (a planner turn is queued or running), talking (waiting for
Yotam), active (it has tasks), complete. A goal from before D226 can still be blocked or
answered. State lives in columns and tables added beside the schema (D181)."""
import json
import time

import manager_store as store
import manager_plan_docs as plan_docs
import studio_config

ADDED = (
    # the planner's session (each reply resumes it), what Yotam said since its last turn,
    # the conversation so far (a fresh session's memory) and the plan waiting for Approve
    ('pm_goals', 'session', "TEXT NOT NULL DEFAULT ''"),
    ('pm_goals', 'pending', "TEXT NOT NULL DEFAULT ''"),
    ('pm_goals', 'talk', "TEXT NOT NULL DEFAULT ''"),
    ('pm_goals', 'plan', "TEXT NOT NULL DEFAULT ''"),
    ('pm_outbox', 'goal_id', 'INTEGER'),   # a message for a goal's thread
    ('pm_asks', 'goal_id', 'INTEGER'),     # a planner's question
)
DDL = """
CREATE TABLE IF NOT EXISTS pm_goal_threads(goal_id INTEGER PRIMARY KEY, channel_id TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS pm_inbox_goal(event_id TEXT PRIMARY KEY, goal_id INTEGER NOT NULL)
"""
TALK_KEEP = 60000  # characters of conversation kept for a fresh session


def ensure_tables(con):
    for stmt in DDL.split(';'):
        if stmt.strip():
            con.execute(stmt)
    for table, column, kind in ADDED:
        cols = {c[1] for c in con.execute(f'PRAGMA table_info({table})')}
        if cols and column not in cols:
            con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')


def goal_of_event(b, event_id):
    r = b.q1('SELECT goal_id FROM pm_inbox_goal WHERE event_id=?', event_id)
    return r[0] if r else None


def planner_running(b, gid):
    return bool(b.q1("SELECT 1 FROM pm_runs WHERE goal_id=? AND role='planner' AND state IN ('queued','running')",
                     gid))


def _said(b, gid, who, text):
    b.con.execute('UPDATE pm_goals SET talk=substr(talk || ?, -?) WHERE id=?',
                  (f'\n\n{who}: {text.strip()}', TALK_KEEP, gid))


def start(b, event_id, body):
    """Yotam's message in the project channel: a new conversation (D226)."""
    gid = b.con.execute("INSERT INTO pm_goals(body,source,created,status) VALUES(?,?,?,'planning')",
                        (body, event_id, time.time())).lastrowid
    _said(b, gid, studio_config.owner(), body)
    b.add_message('owner', 'manager', f'Goal G{gid}', body)
    store.notify(b, f'talk-open:{gid}', f'Planning G{gid} with you, as in a Claude Code session in plan mode: I read '
                 'the code, answer and ask, and propose a plan; **Approve plan** creates the tasks and starts them. '
                 'Reply in this thread at any point.', goal=gid)
    return gid


def hear(b, gid, body):
    """Yotam's reply in a goal's thread: the planner's next turn, or the one after the
    turn it is in. A reply also supersedes a plan waiting for Approve."""
    g = b.q1('SELECT status FROM pm_goals WHERE id=?', gid)
    if not g:
        raise ValueError(f'no goal G{gid}')
    _said(b, gid, studio_config.owner(), body)
    b.con.execute("UPDATE pm_goals SET pending=trim(pending || ? || ?) WHERE id=?", ('\n\n', body.strip(), gid))
    if not planner_running(b, gid):
        b.con.execute("UPDATE pm_goals SET status='planning' WHERE id=?", (gid,))
    b.add_message('owner', 'manager', f'G{gid}: owner reply', body)


def next_prompt(b, g, first):
    """What the planner's next turn is told: a resumed session only hears Yotam's new
    words; a fresh one gets the whole first prompt and the conversation so far."""
    pending = (g['pending'] or '').strip()
    if g['session']:
        return (f'{studio_config.owner()} replied in the planning thread:\n' + (pending or '(no new words)') +
                '\n\n(Plan mode, as before: return chat with your reply in summary, or planned with the tasks. '
                'Read the code you need; edit nothing.)' + plan_docs.instructions(b, g['id']))
    talk = (g['talk'] or '').strip()
    return first + ('\n\nThe conversation so far:\n' + talk if talk.count('\n\n') > 0 else '') + plan_docs.instructions(b, g['id'])


def reply_text(result):
    """The planner's turn as a message in the thread: its own words, then its questions."""
    import manager_brief as brief
    text = (result.get('summary') or result.get('bottom_line') or '').strip()
    asks = [brief.ask_line(a) for a in result.get('asks') or [] if brief.ask_parts(a)[0]]
    if asks:
        text += '\n\n' + '\n'.join(f'**{i}.** {a}' for i, a in enumerate(asks, 1))
    return text or '(no reply)'


def plan_text(result, gid):
    lines = [(result.get('summary') or '').strip(), '', f'**The plan for G{gid}** — Approve plan creates these '
             'tasks and starts them; reply here to change it.']
    for i, t in enumerate(result['tasks'], 1):
        deps = ', '.join(f'{d + 1}' for d in t.get('depends') or [])
        lines.append(f'\n**{i}. {t["title"].strip()}**' + (f' (after {deps})' if deps else '')
                     + (f' · {t["model"]}' if t.get('model') else ''))
        lines.append(' '.join(str(t.get('reasoning') or '').split())[:900])
    return '\n'.join(lines).strip()


def answered(b, r, result, files=(), unsent=()):
    """A planner turn ended (manager_core.completed). FILES are what it showed (its
    artifacts, D304) and UNSENT the line naming any that could not go."""
    import manager_brief as brief
    gid, key = r['goal_id'], r['id'][:12]
    if r.get('session'):
        b.con.execute('UPDATE pm_goals SET session=? WHERE id=?', (r['session'], gid))
    _said(b, gid, 'Planner', result.get('summary') or '')
    if plan_docs.answered(b, r, result):
        return
    tail = ''.join('\n\n' + u for u in unsent)
    if result['status'] == 'planned' and result.get('tasks'):
        store.check_plan(result['tasks'])
        b.con.execute('UPDATE pm_goals SET plan=? WHERE id=?',
                      (json.dumps({'key': key, 'tasks': result['tasks'], 'summary': result.get('summary', '')}), gid))
        store.notify(b, f'plan:{gid}:{key}', plan_text(result, gid) + tail, goal=gid, ping=True, files=files)
    else:
        dedup = f'talk:{gid}:{key}'
        store.notify(b, dedup, reply_text(result) + tail, goal=gid, ping=True, files=files)
        choices = [a for a in result.get('asks') or [] if len(brief.ask_parts(a)[1]) > 1]
        if choices:
            import hashlib
            b.con.execute('INSERT OR IGNORE INTO pm_asks(id,task_id,dedup,asks,created,goal_id) VALUES(?,?,?,?,?,?)',
                          (hashlib.sha1(dedup.encode()).hexdigest()[:10], 0, dedup,
                           json.dumps([{'question': q, 'options': o} for q, o in map(brief.ask_parts, choices)]),
                           time.time(), gid))
    # Words that came in while it worked go to its next turn at once.
    waiting = (b.q1('SELECT pending FROM pm_goals WHERE id=?', gid)[0] or '').strip()
    b.con.execute('UPDATE pm_goals SET status=? WHERE id=?', ('planning' if waiting else 'talking', gid))


def approve(b, gid, key):
    """Approve plan: its tasks are created ready and start when a slot and a checkout are free."""
    g = b.q1('SELECT plan FROM pm_goals WHERE id=?', gid)
    plan = json.loads(g[0]) if g and g[0] else None
    if not plan or plan['key'] != key:
        raise ValueError(f'That plan for G{gid} was replaced or already approved; approve the latest one in its thread.')
    ids = store.plan_tasks(b, gid, plan['tasks'], approved=True, key=key) or []
    b.con.execute('DELETE FROM pm_settings WHERE key=?', (f'approved-document:{gid}',))
    b.con.execute("UPDATE pm_goals SET plan='', status='active' WHERE id=?", (gid,))
    store.notify(b, f'plan-approved:{gid}:{key}', 'Approved. ' + '; '.join(
        f'T{t} {b.task(t)["title"]}' for t in ids) + '. Each reports in its own thread and starts when a run slot '
        'and a checkout are free. This thread stays open: write here to plan more.', goal=gid)
    return ids


def thread_conversation(b, gid):
    """A goal opened as a conversation (D226); one from before plans in one shot."""
    return bool(gid and b.q1("SELECT 1 FROM pm_goals WHERE id=? AND talk<>''", gid))


def thread_of(b, gid):
    r = b.q1('SELECT channel_id FROM pm_goal_threads WHERE goal_id=?', gid)
    return r[0] if r else None


def goal_for_channel(b, channel_id):
    r = b.q1('SELECT goal_id FROM pm_goal_threads WHERE channel_id=?', str(channel_id))
    return r[0] if r else None

