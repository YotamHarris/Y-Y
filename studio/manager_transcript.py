"""A managed run's whole transcript, as the board's transcript view reads it: the
manager's prompt, what the worker thought (its thinking summaries, logged since
`--thinking-display summarized`), what it said, every tool it ran with what came
back, and how the run ended. Read from the run's own log (events.jsonl), Claude's
stream-json and Codex's --json alike, a byte offset at a time so a running worker's
view only reads what it added. Reads only."""
import json

import fe_board
import manager_models as models
import manager_workers as workers

CLIP = 6000       # characters of a tool's input or result kept; the rest is counted
PROMPT_CLIP = 40000


def _clip(text, n=CLIP):
    text = text if isinstance(text, str) else json.dumps(text, indent=1, ensure_ascii=False)
    return (text[:n], len(text)) if len(text) > n else (text, 0)


def _result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict):
            parts.append(c.get('text') if c.get('type') == 'text' else f"[{c.get('type', 'part')}]")
    return '\n'.join(p for p in parts if p)


def _summary(name, inp):
    """What a tool call did, in one line: its own description first (D210)."""
    inp = inp if isinstance(inp, dict) else {}
    for k in ('description', 'command', 'file_path', 'pattern', 'path', 'url', 'query', 'prompt'):
        if inp.get(k):
            return ' '.join(str(inp[k]).split())[:240]
    return ''


def _input(name, inp):
    """A tool call's input as its tool reads it: the command, the file and the change,
    else its JSON."""
    if not isinstance(inp, dict):
        return inp
    if name in ('Bash', 'PowerShell') and inp.get('command'):
        return '$ ' + inp['command']
    if name == 'Edit' and inp.get('file_path'):
        return f"{inp['file_path']}\n--- old\n{inp.get('old_string', '')}\n+++ new\n{inp.get('new_string', '')}"
    if name == 'Write' and inp.get('file_path'):
        return f"{inp['file_path']}\n{inp.get('content', '')}"
    if name == 'Read' and inp.get('file_path'):
        return inp['file_path'] + (f" (from line {inp['offset']}, {inp.get('limit', '')} lines)" if inp.get('offset') else '')
    return inp


def items(event):
    """The transcript items one log event makes."""
    out = []
    t = event.get('type')
    msg = event.get('message') if isinstance(event.get('message'), dict) else {}
    content = msg.get('content') if isinstance(msg.get('content'), list) else []
    if t == 'system' and event.get('subtype') == 'init':
        out.append({'k': 'note', 'text': f"session {event.get('session_id', '')} · {event.get('model', '')} · "
                                          f"{event.get('cwd', '')}"})
    elif t == 'assistant':
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'thinking' and (c.get('thinking') or '').strip():
                out.append({'k': 'think', 'text': c['thinking'].strip()})
            elif c.get('type') == 'text' and (c.get('text') or '').strip():
                out.append({'k': 'say', 'text': c['text'].strip()})
            elif c.get('type') == 'tool_use':
                text, more = _clip(_input(c.get('name'), c.get('input') or {}))
                out.append({'k': 'tool', 'id': c.get('id'), 'name': c.get('name', ''),
                            'summary': _summary(c.get('name'), c.get('input')), 'input': text, 'more': more})
    elif t == 'user':
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_result':
                text, more = _clip(_result_text(c.get('content')))
                out.append({'k': 'result', 'id': c.get('tool_use_id'), 'text': text, 'more': more,
                            'error': bool(c.get('is_error'))})
            elif c.get('type') == 'text' and (c.get('text') or '').strip():
                out.append({'k': 'user', 'text': _clip(c['text'], PROMPT_CLIP)[0]})
    elif t == 'result':
        done = event.get('structured_output') or event.get('result') or ''
        text, _ = _clip(done if isinstance(done, str) else json.dumps(done, indent=1, ensure_ascii=False))
        out.append({'k': 'end', 'ok': not event.get('is_error') and event.get('subtype') == 'success',
                    'subtype': event.get('subtype', ''), 'turns': event.get('num_turns'),
                    'seconds': (event.get('duration_ms') or 0) / 1000, 'text': text})
    elif t in ('item.started', 'item.completed'):  # Codex
        item = event.get('item') or {}
        kind = item.get('type')
        if t == 'item.completed' and kind == 'agent_message' and item.get('text'):
            out.append({'k': 'say', 'text': item['text'].strip()})
        elif t == 'item.completed' and kind == 'reasoning' and item.get('text'):
            out.append({'k': 'think', 'text': item['text'].strip()})
        elif kind == 'command_execution':
            if t == 'item.started':
                out.append({'k': 'tool', 'id': item.get('id'), 'name': 'shell', 'summary': ' '.join(
                    str(item.get('command', '')).split())[:240], 'input': str(item.get('command', '')), 'more': 0})
            else:
                text, more = _clip(item.get('aggregated_output') or '')
                out.append({'k': 'result', 'id': item.get('id'), 'text': text, 'more': more,
                            'error': item.get('exit_code') not in (0, None)})
        elif t == 'item.completed' and kind == 'file_change':
            files = ', '.join(f"{c.get('kind', '')} {c.get('path', '')}" for c in item.get('changes') or [])
            out.append({'k': 'tool', 'id': item.get('id'), 'name': 'edit', 'summary': files[:240],
                        'input': files, 'more': 0})
    elif t == 'turn.failed' or t == 'error':
        out.append({'k': 'end', 'ok': False, 'subtype': t, 'text': json.dumps(event.get('error') or event)[:CLIP]})
    return out


def read(rid, start=0):
    """{items, next, size}: the items of the run's log from byte `start` up to its last
    whole line; `next` is where the next read starts."""
    try:
        with (workers.run_dir(rid) / 'events.jsonl').open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            start = min(max(0, int(start)), size)
            f.seek(start)
            data = f.read()
    except OSError:
        return {'items': [], 'next': 0, 'size': 0}
    end = data.rfind(b'\n') + 1
    out = []
    for line in data[:end].decode('utf-8', 'replace').splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.extend(items(e))
    return {'items': out, 'next': start + end, 'size': size}


def runs(b, ref):
    """The runs a transcript covers, oldest first: every run of task T43, the planner
    runs of goal G12, or one run by its id; with what each ran on and how to open it."""
    if ref[:1] == 'T' and ref[1:].isdigit():
        rows = b.q('SELECT * FROM pm_runs WHERE task_id=? ORDER BY created', int(ref[1:]))
    elif ref[:1] == 'G' and ref[1:].isdigit():
        rows = b.q("SELECT * FROM pm_runs WHERE goal_id=? AND task_id IS NULL ORDER BY created", int(ref[1:]))
    else:
        workers.run_dir(ref)  # validates the id
        rows = b.q('SELECT * FROM pm_runs WHERE id=?', ref)
    rows = [dict(r) for r in rows]
    stats = models.runs_stats(b, [r['id'] for r in rows])
    out = []
    for r in rows:
        s = stats.get(r['id']) or {}
        out.append({'id': r['id'], 'role': r['role'], 'provider': r['provider'], 'state': r['state'],
                    'agent': r['agent'], 'cwd': r['cwd'], 'session': r['session'], 'task': r['task_id'],
                    'goal': r['goal_id'], 'created': r['created'], 'started': r['pid_started'] or r['created'],
                    'beat': r['heartbeat'], 'error': r['error'],
                    'model_label': models.label(s.get('model') or s.get('tier') or r['provider']),
                    'spent': s.get('line', ''), 'prompt': _clip(r['prompt'] or '', PROMPT_CLIP)[0]})
    title = ''
    if ref[:1] == 'T' and ref[1:].isdigit():
        t = b.q1('SELECT title FROM tasks WHERE id=?', int(ref[1:]))
        title = t[0] if t else ''
    elif ref[:1] == 'G' and ref[1:].isdigit():
        g = b.q1('SELECT name, body FROM pm_goals WHERE id=?', int(ref[1:]))
        title = (g[0] or fe_board.clip(g[1], 100)) if g else ''
    return {'ref': ref, 'title': title, 'runs': out}
