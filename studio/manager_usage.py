"""How much of the Claude and Codex allowance is spent (D243): the percentages the two
CLIs already write to disk, kept in the manager's store so the status says the same
thing when nothing runs. Claude's come from the `rate_limit_event`s in the worker stream
(`manager_models.Meter.feed`), Codex's from its own rollout log (`fe_codex.rate_limits`,
polled on the manager's tick). Both are the account's, so Yotam's own sessions count
too. Display only: nothing here holds, pauses or re-routes work. No network or model
calls; reading is side-effect free."""
import json
import time

import manager_store as store

KEY = 'usage'  # pm_settings: {provider: reading}
CLAUDE_WINDOWS = {'five_hour': ('5-hour', 18000), 'seven_day': ('weekly', 604800)}
NO_READING = 'no reading yet; the next run on it takes one'


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def window_name(minutes):
    m = int(minutes)
    return '5-hour' if m == 300 else 'weekly' if m == 10080 else f'{m // 1440}-day' if m % 1440 == 0 \
        else f'{m // 60}-hour' if m % 60 == 0 else f'{m}-minute'


def from_claude(event, now=None):
    """A reading from one stream event, or None when it is not a rate_limit_event that
    carries the windows. A window's `utilization` is a fraction (0.27 is 27%)."""
    info = event.get('rate_limit_info') if event.get('type') == 'rate_limit_event' else None
    windows = info.get('unifiedWindows') if isinstance(info, dict) else None
    if not isinstance(windows, dict):
        return None
    ws = []
    for key, w in windows.items():
        used = _num(w.get('utilization')) if isinstance(w, dict) else None
        if used is not None:
            name, seconds = CLAUDE_WINDOWS.get(key, (key.replace('_', ' '), 0))
            ws.append({'name': name, 'used': round(used * 100, 1), 'resets_at': _num(w.get('resetsAt')),
                       'seconds': seconds})
    if not ws:
        return None
    ws.sort(key=lambda w: w['seconds'] or 1 << 40)
    note = []
    if info.get('status') not in (None, 'allowed', 'allowed_warning'):
        note.append(f"status {info['status']}")
    if info.get('isUsingOverage'):
        note.append('using overage')
    return {'at': time.time() if now is None else now, 'windows': ws, 'note': ', '.join(note)}


def from_codex(seen):
    """A reading from fe_codex.rate_limits' {at, rate_limits}, or None. `primary` and
    `secondary` are each {used_percent, window_minutes, resets_at} or null."""
    limits = (seen or {}).get('rate_limits')
    if not isinstance(limits, dict):
        return None
    ws = []
    for key in ('primary', 'secondary'):
        w = limits.get(key)
        used = _num(w.get('used_percent')) if isinstance(w, dict) else None
        if used is not None:
            minutes = _num(w.get('window_minutes'))
            ws.append({'name': window_name(minutes) if minutes else key, 'used': round(used, 1),
                       'resets_at': _num(w.get('resets_at')), 'seconds': int(minutes * 60) if minutes else 0})
    if not ws:
        return None
    ws.sort(key=lambda w: w['seconds'] or 1 << 40)
    note = []
    if limits.get('plan_type'):
        note.append(str(limits['plan_type']))
    credits = limits.get('credits')
    if isinstance(credits, dict):
        if credits.get('unlimited'):
            note.append('unlimited credits')
        elif credits.get('has_credits') and credits.get('balance') not in (None, '', '0'):
            note.append(f"credits {credits['balance']}")
    if limits.get('rate_limit_reached_type'):
        note.append(f"limit reached: {limits['rate_limit_reached_type']}")
    return {'at': seen['at'], 'windows': ws, 'note': ', '.join(note)}


def load(b):
    try:
        d = json.loads(store.setting(b, KEY, '{}'))
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def record(b, provider, reading):
    """Keep the freshest reading per provider; True when this one was kept."""
    if not reading:
        return False
    with b.tx():
        d = load(b)
        if (d.get(provider) or {}).get('at', 0) > reading['at']:
            return False
        d[provider] = reading
        store.set_setting(b, KEY, json.dumps(d))
    return True


def poll_codex(b, now=None, root=None):
    """The manager's tick: take Codex's newest reading from its own rollout logs."""
    import fe_codex
    return record(b, 'codex', from_codex(fe_codex.rate_limits(now=now, root=root)))


def _span(s):
    s = max(0, int(s))
    return f'{s}s' if s < 60 else f'{s // 60}m' if s < 3600 else \
        f'{s // 3600}h {s % 3600 // 60:02d}m' if s < 86400 else f'{s // 86400}d {s % 86400 // 3600}h'


def _reset(w, now):
    at = w.get('resets_at')
    if not at:
        return ''
    if at <= now:
        return 'reset since'
    gap = f'in {_span(at - now)}'
    return f"resets {gap}" if at - now < 86400 else \
        f"resets {time.strftime('%a %H:%M', time.localtime(at))} ({gap})"


def describe(provider, reading, now=None):
    """One provider's reading as words and structure: {provider, state: ok|stale|none,
    windows: [{name, used, text, stale}], read, note, text}."""
    now = time.time() if now is None else now
    if not reading or not reading.get('windows'):
        return {'provider': provider, 'state': 'none', 'windows': [], 'read': '', 'note': '',
                'text': NO_READING}
    age = now - reading['at']
    ws = []
    for w in reading['windows']:
        # a reading is no longer true once its window has reset, or when it is older than the window
        stale = bool(w.get('resets_at') and w['resets_at'] <= now) or bool(w.get('seconds') and age > w['seconds'])
        why = _reset(w, now)
        ws.append({'name': w['name'], 'used': w['used'], 'stale': stale,
                   'text': f"{w['name']} {w['used']:g}%" + (' (stale' + (f', {why}' if why else '') + ')'
                                                          if stale else f' ({why})' if why else '')})
    read = f'read {_span(age)} ago'
    note = reading.get('note') or ''
    return {'provider': provider, 'state': 'stale' if any(w['stale'] for w in ws) else 'ok', 'windows': ws,
            'read': read, 'note': note,
            'text': ' · '.join([w['text'] for w in ws] + [read] + ([note] if note else []))}


def usage(b, now=None):
    """What every view says: one entry per provider, in the manager's order."""
    d = load(b)
    return [describe(p, d.get(p), now) for p in store.PROVIDERS]
