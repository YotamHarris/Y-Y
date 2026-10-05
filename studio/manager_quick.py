"""Quick answers (D317, D322): Yotam asks while he goes through a review, and the manager
answers in a minute, where he asked. Nothing asked this way changes the work: it opens no
goal, reopens no task and reaches no worker. Two places ask:

- the talk channel (scope `talk`): every message there;
- a task's thread (scope `T12`): the `/ask` command, about that task. A plain reply in
  the thread still goes to its worker (D212).

Each scope is one read-only Sonnet session in the trunk's worktree, each question resuming
it, a fresh one after QUIET_S of silence. It has its own lane, as the planners do (D216): no
run slot, no checkout, and it answers while the manager is paused. The run carries no task:
to the board it is not the task's worker, so it holds nothing.

State is pm_settings: per scope `quick:SCOPE:` session, pending (words not yet answered),
asked (the words its running turn was given), last (when it last answered) and run; and
`quick-run:RID`, the scope of a run."""
import json
import time

import manager_store as store
import studio_config

# the channel's name, when the config gives no talk_channel_id ([routing] talk_channel)
CHANNEL = studio_config.get('routing.talk_channel', 'talk')
SETTING = 'talk_channel'  # the channel's ID, as the Discord client found it
TALK = 'talk'
QUIET_S = 3 * 3600  # silence after which a question starts a fresh session
TIER = 'sonnet'
TASK_KEEP = 4000  # characters of a task's body and of its last report put in the first prompt
PROMPT = (
    f"You are {studio_config.name()}'s background manager (D181). {studio_config.owner()}, who directs the project, is "
    "asking you a question on Discord while he goes through reviews (D317). This is a "
    "conversation, not a task: there is no task skill to follow and no report to write.\n\n"
    "Find out what you need first. Read only what the question needs: the board "
    f"({studio_config.tool_cmd('fe_board.py')} task show T12, {studio_config.tool_cmd('fe_manager.py')} status), "
    f"git (git log, git show), the docs ({studio_config.tool_cmd('fe_docs.py')} show D64) and the code "
    f"({studio_config.tool_cmd('fe_index.py')} find|show NAME). Edit nothing and start nothing.\n\n"
    "Then explain, the way a colleague who knows the code would explain it to him across the "
    "desk. Most of his questions ask for understanding (what is this, why is it so, could it "
    "be otherwise), so:\n"
    "- Open with the direct answer to what he asked, in a sentence or two.\n"
    "- Then explain it: how it works, why it is that way, and what would change if it were "
    "otherwise. When he asks \"can it be X instead?\", say whether, what it would take and "
    "what it would cost or break.\n"
    "- Plain words. He knows the game and its design, not every function. A decision number "
    "or a file name is a pointer, never the explanation: say what the rule is, then cite it.\n"
    "- As long as the question needs: a few short paragraphs is normal; one line is fine for a "
    "one-line question. Discord markdown; a short list when it helps.\n"
    "- Speak to him (\"you\"), in the first person. Your answer is the message itself: never "
    f"describe it (\"Explained ...\", \"Answered {studio_config.owner()}'s question ...\") and never add that "
    "nothing changed.\n"
    "- Say plainly what you are unsure of, and what you checked when it matters.\n"
    "- When he asks for a change, say in one line that a plain reply in the task's thread (or "
    "its Request changes button) sends it to the worker.\n\n"
    "Return your answer, the whole message he reads, in `answer`.\n")


def channel_name(name):
    """'meatbag - talk' and 'meatbag-talk' are the same channel."""
    return '-'.join(name.lower().replace('-', ' ').split())


def channel(b):
    return store.setting(b, SETTING) or None


def is_quick(row):
    """An outbox row for the talk channel."""
    return row['dedup'].startswith('quick')


def is_answer(row):
    """An outbox row that answers a question: the talk channel's, or /ask's in a task's thread."""
    return row['dedup'].startswith(('quick', 'ask'))


def _key(scope, name):
    return f'quick:{scope}:{name}'


def _get(b, scope, name, default=''):
    return store.setting(b, _key(scope, name), default) or default


def _set(b, scope, name, value):
    store.set_setting(b, _key(scope, name), value)


def _add(b, scope, words, first=False):
    old = _get(b, scope, 'pending').strip()
    _set(b, scope, 'pending', '\n\n'.join(x for x in ((words, old) if first else (old, words)) if x.strip()))


def task_of(scope):
    return int(scope[1:]) if scope and scope[0] == 'T' and scope[1:].isdigit() else None


def scope_of_run(b, rid):
    return store.setting(b, 'quick-run:' + rid) or TALK


def hear(b, body, task=None):
    """His question: the talk channel's next turn, or TASK's (/ask in its thread)."""
    if task and not b.q1('SELECT 1 FROM tasks WHERE id=?', task):
        raise ValueError(f'no task T{task}')
    _add(b, f'T{task}' if task else TALK, body.strip())


def running(b, scope=TALK):
    rid = _get(b, scope, 'run')
    return bool(rid and b.q1("SELECT 1 FROM pm_runs WHERE id=? AND state IN ('queued','running','finished','failed')", rid))


def in_review(b):
    rows = b.q("SELECT t.id, t.title, coalesce(m.landed_head, m.head) AS head FROM tasks t "
               "JOIN pm_tasks m ON m.task_id=t.id WHERE t.status='review' ORDER BY t.id")
    return '\n'.join(f'- T{r["id"]} {r["title"]} (commit {(r["head"] or "")[:12]})' for r in rows)


def task_brief(b, tid):
    """What a fresh /ask session knows of its task before it reads anything."""
    t = b.task(tid)
    m = b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid)
    lines = [f'He is asking in the thread of T{tid}: {t["title"]} (on the board: {t["status"]}'
             + (f', manager phase {m["phase"]}, commit {(m["landed_head"] or m["head"] or "")[:12]}' if m else '')
             + '). Its whole run transcript is the board\'s and the log; the task:', t['body'].strip()[:TASK_KEEP]]
    try:
        said = json.loads(m['evidence'] or '{}').get('summary', '') if m else ''
    except (ValueError, TypeError):
        said = ''
    if said:
        lines += ['', 'Its worker\'s last report:', said.strip()[:TASK_KEEP]]
    return '\n'.join(lines) + '\n'


def thread_of_run(b, r):
    """Where a run's live line goes: its task's thread, or the talk channel."""
    tid = task_of(scope_of_run(b, r['id']))
    if not tid:
        return channel(b)
    x = b.q1('SELECT channel_id FROM pm_discord_threads WHERE task_id=?', tid)
    return x[0] if x else None


def where(scope):
    return 'in the thread of ' + scope if task_of(scope) else 'in the talk channel'


def prompt(b, scope, words, fresh):
    if not fresh:
        return f'{studio_config.owner()}, {where(scope)}:\n' + words + '\n\n(As before: read what you need, edit nothing, then explain it to him in `answer`.)'
    tid = task_of(scope)
    review = '' if tid else in_review(b)
    return (PROMPT + (task_brief(b, tid) if tid else '') + ('\nIn review now:\n' + review + '\n' if review else '')
            + f'\n{studio_config.owner()}, {where(scope)}:\n' + words)


def say(b, scope, key, text):
    """A message where the question was asked."""
    tid = task_of(scope)
    store.notify(b, ('ask' if tid else 'quick') + key, text, tid)


def queue(m, now=None):
    """Each tick: each scope's unanswered words become a turn, when none runs. M is the Manager."""
    b = m.b
    rids = []
    for (key,) in b.q("SELECT key FROM pm_settings WHERE key LIKE 'quick:%:pending' AND value<>''"):
        scope = key.split(':')[1]
        words = _get(b, scope, 'pending').strip()
        if not words or running(b, scope):
            continue
        p = m.provider()
        if not p:
            say(b, scope, f'-wait:{scope}:{int(time.time() // 3600)}', 'Neither Claude nor Codex can run right '
                'now (`/status` says why); I answer as soon as one can.')
            continue
        now = time.time() if now is None else now
        session = _get(b, scope, 'session')
        if p != 'claude' or now - float(_get(b, scope, 'last', 0) or 0) > QUIET_S:
            session = ''  # a Claude session does not resume in Codex; a long silence starts afresh
        try:
            import manager_release as release
            home = release.reading(m.root) or m.root
        except Exception:
            home = m.root  # no worktree of the trunk: the manager's own checkout, as the planner does
        import manager_models as models
        tier = TIER if p == 'claude' else ''
        with b.tx():
            rid = store.queue_run(b, 'quick', p, home, prompt(b, scope, words, not session), session=session or None,
                                  tier=tier, model=models.model_id(m.config, tier) if tier else '')
            store.set_setting(b, 'quick-run:' + rid, scope)
            _set(b, scope, 'run', rid)
            _set(b, scope, 'asked', words)
            _set(b, scope, 'pending', '')
        rids.append(rid)
    return rids


def answered(b, r, result):
    """A turn ended (manager_core.completed): its answer, where he asked."""
    import manager_talk as talk
    scope = scope_of_run(b, r['id'])
    if r.get('session') and r['provider'] == 'claude':
        _set(b, scope, 'session', r['session'])
    _set(b, scope, 'last', time.time())
    _set(b, scope, 'asked', '')
    say(b, scope, ':' + r['id'][:12], talk.reply_text(result))


def failed(b, r, error, kind):
    """A turn that failed. A resumed session that would not resume is retried fresh, once,
    with the same words; otherwise he is told where he asked, and his next question tries again."""
    scope = scope_of_run(b, r['id'])
    words = _get(b, scope, 'asked').strip()
    _set(b, scope, 'asked', '')
    _set(b, scope, 'session', '')
    if r.get('session') and kind not in ('allowance', 'authentication') and words:
        _add(b, scope, words, first=True)
        return
    if kind == 'allowance':
        _add(b, scope, words, first=True)
        why = f'{r["provider"]} has spent its allowance; I answer on the other one, or when it is back.'
    else:
        why = 'I could not answer that: ' + ' '.join(error.split())[:300] + '\nAsk again and I try afresh.'
    say(b, scope, '-failed:' + r['id'][:12], why)
