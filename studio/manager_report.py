"""The report on a task blocked past its hours (D321): what it was stuck on and what it
did with the time, written by the manager from the runs' own logs so the worker does
not spend its last turn writing one. Claude's stream-json and Codex's --json read alike
(manager_transcript.items). Code only, no model; a log that will not read falls back
to the run's last steps (D201) so a block is never lost."""
import collections
import json
import os
import re

import fe_sync
import manager_store as store
import manager_transcript as transcript
import manager_workers as workers
import studio_config

EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'edit'}
READ_TOOLS = {'Read', 'Grep', 'Glob', 'LSP'}
READING = 'reading and searching'
# What a command was for, first match wins: the time went to these. The game's own kinds come
# first, from studio.toml's [[report.kinds]] (label, pattern); with none, builds, tests and landing.
KINDS = [
    *((k['label'], re.compile(k['pattern'])) for k in studio_config.get('report.kinds', [
        {'label': 'builds, tests and landing', 'pattern': r'fe_land|unittest|pytest|\btest[-_][\w-]+\.py'}])),
    ('git', re.compile(r'\bgit\b')),
    (READING, re.compile(r'fe_index|fe_docs|fe_board|Get-Content|Select-String|\brg\b|\bcat\b|\bls\b')),
]
LOOP = 3          # the same command this many times is a loop
REACH = 200       # calls from the end searched for a failed build, bench or test
TAIL = 40         # calls from the end that "what blocked it" looks at
LINE = 200        # characters of a command or an error line
COMMIT = re.compile(r'\[[\w./-]+ (?:\(root-commit\) )?([0-9a-f]{7,40})\]')


def kind_of(call):
    if call['name'] in EDIT_TOOLS:
        return 'edits'
    if call['name'] in READ_TOOLS:
        return READING
    head = _command(call['summary'])[:90]  # what it starts with says what it is for; a chain's tail does not
    for label, rx in KINDS:
        if rx.search(head):
            return label
    return 'other commands'


SHELL = re.compile(r'^"?[^"]*powershell\.exe"?\s+-Command\s+', re.I)


def _clip(text, n=LINE):
    return ' '.join(str(text).split())[:n]


def _command(summary):
    """A command as it reads: without the PowerShell wrapper Codex runs everything through."""
    text = SHELL.sub('', summary.strip())
    return _clip(text[1:-1] if text[:1] == text[-1:] and text[:1] in ("'", '"') else text)


def _last_line(text):
    lines = [x.strip() for x in str(text).splitlines() if x.strip()]
    return _clip(lines[-1]) if lines else ''


def _edited(call):
    """The files an edit call touched: Claude's input starts with the path, Codex's is
    'update PATH, add PATH'."""
    if call['name'] == 'edit':
        return [p.split(' ', 1)[-1] for p in call['input'].split(', ') if p.strip()]
    return [call['input'].split('\n', 1)[0].strip()] if call['input'].strip() else []


def scan(rids):
    """The digest of the runs' logs, oldest first: {calls, said, ended}. A call is
    {name, summary, input, kind, error, out}; `out` is the last line of what came back."""
    calls, byid, said, ended = [], {}, [], None
    for rid in rids:
        try:
            f = (workers.run_dir(rid) / 'events.jsonl').open('rb')
        except OSError:
            continue
        with f:
            for raw in f:
                try:
                    e = json.loads(raw.decode('utf-8', 'replace'))
                except ValueError:
                    continue
                for it in transcript.items(e) if isinstance(e, dict) else []:
                    k = it['k']
                    if k == 'tool':
                        c = {'name': it['name'], 'summary': it['summary'], 'input': it['input'], 'error': False,
                             'out': '', 'full': ''}
                        c['kind'] = kind_of(c)
                        calls.append(c)
                        byid[it['id']] = c
                    elif k == 'result' and it['id'] in byid:
                        c = byid[it['id']]
                        c['error'], c['out'], c['full'] = it['error'], _last_line(it['text']), it['text']
                    elif k == 'say':
                        said.append(it['text'])
                    elif k == 'end':
                        ended = it
    return {'calls': calls, 'said': said, 'ended': ended}


def stuck_on(d):
    """What blocked it, as lines: the failing commands of the last calls (counted), a
    command run over and over, how the log ended, and the last thing it said."""
    out, calls = [], d['calls']
    fails = collections.OrderedDict()
    # A failed search is rarely what blocked it: look further back for a build, bench or test that failed,
    # and show the searches only when nothing else did.
    failed = [c for c in calls[-REACH:] if c['error'] and c['kind'] != READING]         or [c for c in calls[-TAIL:] if c['error']]
    for c in failed:
        key = _command(c['summary'])
        n, _ = fails.get(key, (0, ''))
        fails[key] = (n + 1, c['out'])
    for key, (n, err) in list(fails.items())[-3:]:
        out.append(f'- failed{f" {n} times" if n > 1 else ""}: {key}' + (f'  ->  {err}' if err else ''))
    again = collections.Counter(_command(c['summary']) for c in calls if c['kind'] not in ('edits', READING)
                                and c['summary'])
    for key, n in again.most_common(2):
        if n >= LOOP:
            bad = sum(1 for c in calls if c['error'] and _command(c['summary']) == key)
            out.append(f'- ran {n} times{f" ({bad} failed)" if bad else ""}: {key}')
    end = d['ended']
    if end and not end.get('ok', True):
        out.append(f'- the log ends with {end.get("subtype") or "an error"}: {_clip(end.get("text", ""), 240)}')
    if not out and calls:
        out.append('- no command was failing at the end; it was still working. Its last steps:')
        out += ['  - ' + _command(c['summary']) for c in calls[-3:]]
    for text in d['said'][-2:]:
        out.append('- it last said: ' + _clip(text, 300))
    return out or ['- (its log is empty)']


def spent_on(d, cwd=''):
    """What the time went to: calls by kind with their failures, the files edited and
    the commits made."""
    calls = d['calls']
    kinds = collections.OrderedDict()
    for c in calls:
        n, bad = kinds.get(c['kind'], (0, 0))
        kinds[c['kind']] = (n + 1, bad + (1 if c['error'] else 0))
    out = [f'- {n} {label}' + (f' ({bad} failed)' if bad else '')
           for label, (n, bad) in sorted(kinds.items(), key=lambda x: -x[1][0])]
    files = list(dict.fromkeys(p.replace(cwd + os.sep, '') if cwd else p
                               for c in calls if c['kind'] == 'edits' for p in _edited(c) if p))
    if files:
        out.append(f'- {len(files)} file(s) edited: ' + ', '.join(files[:6]) + (', ...' if len(files) > 6 else ''))
    commits = list(dict.fromkeys(m for c in calls if 'git commit' in c['input'] for m in COMMIT.findall(c['full'])))
    if commits:
        out.append('- committed: ' + ', '.join(commits))
    return out or ['- nothing recorded']


def standing(run):
    """Where the checkout stands now: modified files and commits not on origin."""
    path = run['cwd']
    try:
        changed = fe_sync.git_out(path, 'status', '--porcelain').splitlines()
        ahead = fe_sync.git_out(path, 'log', '--oneline', 'origin/main..HEAD').splitlines()
    except Exception as e:  # a checkout that cannot be read is said, not hidden
        return [f'- (could not read {path}: {_clip(e, 100)})']
    out = [f'- {len(changed)} modified file(s)' + (': ' + ', '.join(x[3:] for x in changed[:6]) if changed else '')
           + (', ...' if len(changed) > 6 else '')]
    out.append(f'- {len(ahead)} commit(s) in the checkout not on origin/main' + (': ' + '; '.join(_clip(x, 80) for x in ahead[:3]) if ahead else ''))
    return out


def fallback(run):
    """The old report (D201): the run's last steps, when its log would not read."""
    doing = workers.last_activity(run['id'])
    return 'What it was doing last, from its log:\n' + ('\n'.join('- ' + x for x in doing) or '- (its log is empty)')


def runs_since_answer(b, tid):
    """The worker runs the task's clock counts (manager_store.task_clock), oldest first."""
    since = b.q1('SELECT MAX(created) FROM pm_inbox WHERE task_id=?', tid)[0] or 0
    return [dict(r) for r in b.q("SELECT id, cwd, agent, state, created FROM pm_runs WHERE task_id=? "
                                 "AND role='worker' AND state<>'queued' AND created>=? ORDER BY created", tid, since)]


def blocked_report(b, tid, run, ended_by_manager, agent_note=''):
    """The report for a task that ran past its hours and is now blocked: how long, how
    it ended, what blocked it, what the time went to, where the checkout stands, then the
    worker's own note if it left one. `run` is the run that ended."""
    spent, n = store.task_clock(b, tid)
    head = (f'T{tid} has taken {store.hours(spent)} over {n} run(s), past its {store.TASK_HOURS} hours, '
            + ('and did not stop when it was told, so the manager ended its run (D201).' if ended_by_manager
               else 'and stopped by itself, blocked (D201).')
            + f' Its work is left in {run["agent"]}\'s checkout as it was, nothing pushed. '
              'This report is the manager\'s, read from the run logs (D321).')
    try:
        d = scan([r['id'] for r in runs_since_answer(b, tid)] or [run['id']])
        if not d['calls'] and not d['said']:
            raise ValueError('an empty log')  # nothing to digest: say what little there is
        body = ('\n\nWhat blocked it:\n' + '\n'.join(stuck_on(d))
                + '\n\nWhat the time went to:\n' + '\n'.join(spent_on(d, run['cwd']))
                + '\n\nWhere the checkout stands:\n' + '\n'.join(standing(run)))
    except Exception:
        body = '\n\n' + fallback(run)
    note = f'\n\nThe worker\'s own note:\n{agent_note.strip()}' if agent_note and agent_note.strip() else ''
    return head + body + note
