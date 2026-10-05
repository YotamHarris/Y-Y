"""Accepting a delivered plan authorizes its implementation, once per landed plan."""
import json
from pathlib import PurePosixPath
import re
import time

import manager_store as store
import studio_config

SINCE = 'plan-document-acceptance-since'


def document(task, evidence):
    """Explicit metadata first; older workers identify fe_plan's PDF and its source."""
    path = evidence.get('plan_document', '')
    if 'plan_document' in evidence:
        return path if re.fullmatch(r'docs/[\w-]+\.md', path) else ''
    # A PDF may be an example used to test the document tools, or a formatting
    # update to a still-unapproved plan. Those acceptances do not approve its design.
    if not re.search(r'\ba (?:written )?(?:plan|proposal)\b', task['title'], re.I):
        return ''
    for artifact in evidence.get('artifacts', []):
        match = re.search(rf'(?:^|/){re.escape(studio_config.rel(studio_config.artifacts_dir()))}/plans/([\w-]+)\.pdf$',
                          artifact.replace('\\', '/'))
        if match:
            path = f'docs/{match[1]}.md'
            if path in task['body']:
                return path
    return ''


def initialize(b):
    # Do not replay historical approvals: some old plans were already implemented
    # through separate goals. A named accepted task can be recovered with start().
    with b.tx():
        b.con.execute('INSERT OR IGNORE INTO pm_settings(key,value) VALUES(?,?)', (SINCE, str(time.time())))


def start(b, tid):
    """Recover or queue a plan that Yotam accepted, from any acceptance surface."""
    task = b.task(tid)
    m = b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid)
    if task['status'] != 'done' or not m or not m['landed_head']:
        return None
    evidence = json.loads(m['evidence'] or '{}')
    path = document(task, evidence)
    if not path:
        return None
    source = f'accepted-plan:{tid}:{m["landed_head"]}'
    with b.tx():
        previous = b.q1('SELECT id FROM pm_goals WHERE source=?', source)
        if previous:
            return previous[0]
        body = (f'{studio_config.owner()} accepted T{tid}: {task["title"]}, commit {m["landed_head"]}.\n'
                f'Implement the approved plan in {path}. Read that document and the current code; '
                'convert its recommended implementation into few, whole tasks by the task skill. '
                'Keep the approved scope and recommendations. Ask only for a choice the approved '
                'document does not settle, or a material change required by current evidence. '
                'Do not repeat work that has already landed.\n\nApproved task:\n' + task['body'])
        gid = b.con.execute('INSERT INTO pm_goals(body,source,created,status,name,talk) '
                            "VALUES(?,?,?,'planning',?,?)",
                            (body, source, time.time(), 'Implement ' + PurePosixPath(path).stem,
                             'Owner acceptance: ' + body)).lastrowid
        store.set_setting(b, f'approved-document:{gid}', json.dumps({'task': tid, 'path': path}))
        store.notify(b, f'implement-plan:{source}', f'T{tid} accepted. I am converting {path} into '
                     'implementation tasks. They are already approved and will start when a run slot '
                     'and a checkout are free.', task=tid)
    return gid


def reconcile(b):
    """Board/CLI acceptance also reaches the service, including across a restart."""
    for row in b.q("SELECT t.id FROM tasks t JOIN pm_tasks m ON m.task_id=t.id "
                   "WHERE t.status='done' AND t.updated>=? AND m.landed_head IS NOT NULL",
                   float(store.setting(b, SINCE))):
        start(b, row[0])


def approval(b, gid):
    raw = store.setting(b, f'approved-document:{gid}')
    return json.loads(raw) if raw else None


def instructions(b, gid):
    approved = approval(b, gid)
    if not approved:
        return ''
    return (f'\nT{approved["task"]} is an accepted plan document: {approved["path"]}. '
            'Its implementation is already authorized. Return planned with implementation tasks '
            'within that document; the manager creates them ready without another approval. '
            'Do not just acknowledge the acceptance. Read the document and current code. '
            'Return chat with asks only for an unresolved choice or a material departure.\n')


def answered(b, r, result):
    """Consume this acceptance only once; later plans in the thread need Approve."""
    gid = r['goal_id']
    approved = approval(b, gid)
    if not approved or result['status'] != 'planned' or not result.get('tasks'):
        return False
    if (b.q1('SELECT pending FROM pm_goals WHERE id=?', gid)[0] or '').strip():
        return False  # newer owner steering must be heard before anything starts
    ids = store.plan_tasks(b, gid, result['tasks'], approved=True, key='accepted-document') or []
    for tid in ids:
        b.add_links('manager', tid, [f'T{approved["task"]}', approved['path']])
    b.con.execute('DELETE FROM pm_settings WHERE key=?', (f'approved-document:{gid}',))
    store.notify(b, f'implemented-plan:{gid}', f'Approved plan T{approved["task"]} became ' +
                 '; '.join(f'T{tid} {b.task(tid)["title"]}' for tid in ids) +
                 '. The tasks start when a run slot and a checkout are free.', task=approved['task'])
    return True
