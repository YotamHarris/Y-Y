"""What Yotam reads in Discord: the problem, the bottom line, where the rest is.

A task thread is read on a phone by someone who has not followed the work, so
every message leads with what we are fixing in plain words, then what it needs
from him as short questions, then where the full record is. The worker's own
report (its summary, the checks, the review) is the record: it goes on the board
and is attached as a text file, never pasted into the message.
"""
import hashlib
import json
import time

import fe_board
import manager_store as store

INLINE = 600  # a report this short is quoted in the message instead of attached

# D224: a worker's questions as Yotam answers them, a button per option in the task's
# thread; the last answer resumes the worker with all of them. An added table (D181).
ASKS_DDL = """CREATE TABLE IF NOT EXISTS pm_asks(
  id TEXT PRIMARY KEY, task_id INTEGER NOT NULL, dedup TEXT NOT NULL, asks TEXT NOT NULL,
  answers TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0)"""


def ensure_tables(con):
    con.execute(ASKS_DDL)


def ask_parts(a):
    """(question, options) of an ask: D224's {question, options}, or an older plain line."""
    if isinstance(a, dict):
        opts = [' '.join(str(o).split()) for o in a.get('options') or []]
        return str(a.get('question') or '').strip(), [o for o in opts if o][:4]
    return str(a or '').strip(), []


def ask_line(a):
    q, opts = ask_parts(a)
    return q + (''.join(f'\n   **{chr(97 + j)}.** {o}' + (' (recommended)' if j == 0 else '')
                        for j, o in enumerate(opts)) if len(opts) > 1 else '')


def open_asks(b, aid):
    """The ask set while it is the task's latest and the task still waits on it, else None."""
    r = b.q1('SELECT * FROM pm_asks WHERE id=?', aid)
    if not r or r['done']:
        return None
    if 'goal_id' in r.keys() and r['goal_id']:
        # a planner's question (D226): while it is the goal's latest and the goal waits on him
        latest = b.q1('SELECT id FROM pm_asks WHERE goal_id=? ORDER BY created DESC, rowid DESC LIMIT 1', r['goal_id'])
        g = b.q1('SELECT status FROM pm_goals WHERE id=?', r['goal_id'])
        return dict(r) if latest and latest[0] == aid and g and g[0] == 'talking' else None
    latest = b.q1('SELECT id FROM pm_asks WHERE task_id=? ORDER BY created DESC, rowid DESC LIMIT 1', r['task_id'])
    phase = b.q1('SELECT phase FROM pm_tasks WHERE task_id=?', r['task_id'])
    return dict(r) if latest and latest[0] == aid and phase and phase[0] == 'blocked' else None


def answer(b, aid, answers, final=False):
    """Record answers {question index: text}. Returns (reply, answered, total): reply is
    the message that resumes the worker once every question has one (or `final`, from the
    free-text form), else ''. Raises ValueError when the questions are answered or outdated."""
    with b.tx():
        r = open_asks(b, aid)
        if not r:
            raise ValueError('These questions are already answered or outdated; reply in the thread instead.')
        asks = json.loads(r['asks'])
        got = {int(k): v for k, v in json.loads(r['answers']).items()}
        got.update({int(k): ' '.join(str(v).split()) for k, v in answers.items() if str(v).strip()})
        done = final or len(got) >= len(asks)
        b.con.execute('UPDATE pm_asks SET answers=?, done=? WHERE id=?', (json.dumps(got), int(done), aid))
    if not done:
        return '', len(got), len(asks)
    lines = ['Answers to your questions:']
    for i, a in enumerate(asks):
        lines.append(f'{i + 1}. {ask_parts(a)[0]}\n   -> {got.get(i, "(no answer: use your judgement)")}')
    return '\n'.join(lines), len(got), len(asks)


def problem_key(tid):
    return f'problem:{tid}'


def remember_problem(b, tid, text):
    """The first plain statement of a task's problem is its reminder for good, so
    every message in the thread leads with the same sentence."""
    text = (text or '').strip()
    if tid and text and not store.setting(b, problem_key(tid)):
        store.set_setting(b, problem_key(tid), text)


def problem(b, tid):
    if not tid:
        return ''
    saved = store.setting(b, problem_key(tid))
    if saved:
        return saved
    t = b.q1('SELECT title FROM tasks WHERE id=?', tid)
    return t[0] if t else ''


def attach(name, text):
    """The full report as a text file in the board's file store (under which the
    transport uploads), named for the task so it reads as one on a phone."""
    data = text.encode('utf-8')
    d = fe_board.files_dir() / 'reports' / hashlib.sha256(data).hexdigest()[:20]
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if not p.exists():
        p.write_bytes(data)
    return str(p)


def compose(problem_text, bottom, asks=(), more=(), inline='', why=''):
    lines = []
    if problem_text:
        lines.append('**What we are fixing:** ' + problem_text.strip())
    if why:
        lines.append('**Why:** ' + why.strip())
    if bottom:
        lines.append('**Where it stands:** ' + bottom.strip())
    asks = [ask_line(a) for a in asks if ask_parts(a)[0]]
    if len(asks) == 1:
        lines.append('**Your call:** ' + asks[0])
    elif asks:
        lines.append('**Your call:**\n' + '\n'.join(f'{i}. {a}' for i, a in enumerate(asks, 1)))
    else:
        lines.append('**Your call:** nothing for now.')
    if inline:
        lines.append('> ' + inline.strip().replace('\n', '\n> '))
    if more:
        lines.append('**More:** ' + ' · '.join(more))
    return '\n\n'.join(lines)


def post(b, key, tid, bottom, asks=(), report='', ping=False, files=(), more=(), problem_text=None,
         session=None, cwd=None, why='', goal=None):
    """Queue one message in this shape. report is the full record; session and cwd
    name the CLI session behind it, so it can be opened from a terminal."""
    more = list(more)
    report = (report or '').strip()
    inline = ''
    files = store.with_images_of(report, files)  # D304: an image the report shows, even attached as text
    if report and len(report) > INLINE:
        name = f'T{tid}-report.txt' if tid else 'report.txt'
        files.append(attach(name, report))
        more.insert(0, f'full report attached ({name})')
    elif report and report != (bottom or '').strip():
        inline = report
    if tid:
        more.append(f'board T{tid}')
    if session:
        more.append(f'session `{session}`' + (f' in `{cwd}`' if cwd else ''))
    text = compose(problem(b, tid) if problem_text is None else problem_text, bottom, asks, more, inline, why)
    store.notify(b, key, text, tid, ping, files, goal=goal)
    choices = [a for a in asks if ask_parts(a)[0]]
    if tid and key.startswith('question:') and any(len(ask_parts(a)[1]) > 1 for a in choices):
        # D224: the transport gives each option a button; the answers resume the worker
        b.con.execute('INSERT OR IGNORE INTO pm_asks(id,task_id,dedup,asks,created) VALUES(?,?,?,?,?)',
                      (hashlib.sha1(key.encode()).hexdigest()[:10], tid, key,
                       json.dumps([{'question': q, 'options': o} for q, o in map(ask_parts, choices)]), time.time()))


PROPOSAL_ASK = ('Approve it to start it, Edit reasoning to change why it is done (its worker plans its '
                'tests from that), or Drop it. A reply here is added to the reasoning.')


def propose(b, key, tid):
    """A proposed task as Yotam decides it (D195): what it fixes, why, the buttons (the
    transport adds them while it is still an idea), the whole task attached."""
    t = b.task(tid)
    gid = b.q1('SELECT goal_id FROM pm_tasks WHERE task_id=?', tid)[0]
    wait = [f'T{d}' for d in t['depends']
            if (b.q1('SELECT status FROM tasks WHERE id=?', d) or ['done'])[0] not in ('done', 'dropped')]
    bottom = (f'Proposed as T{tid} for {store.goal_label(b, gid)}: {t["title"]}. Nothing starts '
              'until you approve it' + (f', and then only after {", ".join(wait)}.' if wait else '.'))
    post(b, key, tid, bottom, [PROPOSAL_ASK], report=store.without_reasoning(t['body']),
         why=store.reasoning(t['body']) or '(none given)')


def first_line(text, limit=300):
    """A report's lead sentence, for a result written before bottom_line existed."""
    text = ' '.join((text or '').split())
    for stop in ('. ', '? ', '! '):
        i = text.find(stop)
        if 0 < i < limit:
            return text[:i + 1]
    return text[:limit] + ('…' if len(text) > limit else '')


def bottom_line(result):
    return (result.get('bottom_line') or '').strip() or first_line(result.get('summary', ''))


def run_result(r):
    try:
        return json.loads(r['result']) if r.get('result') else {}
    except (TypeError, ValueError):
        return {}


# The manager's own failure strings, as the owner would put them.
CAUSES = (
    ('Invalid result', "The worker's report came back malformed."),
    ('Worker wrapper exited before initialization', 'The worker quit before it started.'),
    ('Worker process exited without a durable result', 'The worker quit without reporting back.'),
)


def cause(error, provider, kind):
    for prefix, plain in CAUSES:
        if error.startswith(prefix):
            return plain
    return f'The {provider} run failed ({kind}).'
