"""Which model runs a task, what each run spent, and the policy that learns from it (D218).

Opus is no longer every worker's default. A task goes to the fastest tier that its
kind of work has shown it can carry: the planner suggests a tier with its reason, the
policy here checks the suggestion against what each tier has delivered on that
category of work (landed without being escalated), and a Sonnet run that fails,
stalls or runs out of time is handed to the strong tier with the better record in that
kind of work (D240: Opus, or the ChatGPT model GPT-6.1 Sol). Every run records its
model, its tokens per model and where its time went (the model, tools, the GPU and
waits for it), so the record the policy reads keeps growing and the routing improves
with it. No network or model calls here.
"""
import json
import os
from pathlib import Path
import re
import time

import manager_usage
import studio_config

# D238: a tier is a model and the CLI that runs it, not a model id alone.
TIERS = ('sonnet', 'opus', 'sol')
PROVIDER = {'sonnet': 'claude', 'opus': 'claude', 'sol': 'codex'}
# D298: a tier is a class of model, and it runs the class's latest. Claude's are the CLI's own
# aliases, which an up-to-date CLI resolves to the newest Sonnet or Opus (the manager runs the
# newest CLI installed, manager_host.claude_cli); Sol is the newest gpt-N-sol in the Codex CLI's
# own model list (latest_codex), the id below only when that list is unreadable.
# config.json's "models" still pins any tier to an exact id.
MODEL_IDS = {'sonnet': 'sonnet', 'opus': 'opus', 'sol': 'gpt-6.1-sol'}
LABEL = {'sonnet': 'Sonnet', 'opus': 'Opus', 'sol': 'Sol', 'codex': 'Codex'}
CLAUDE_TIERS = tuple(t for t in TIERS if PROVIDER[t] == 'claude')

# The categories of work, in the order a tie is broken. A core category is the engine's
# simulation, rendering or frame budget: the work where a slower, stronger model pays
# for itself. Everything else starts on Sonnet unless its record says otherwise.
# The game's own are studio.toml's [[routing.categories]] (name, core, pattern), in that order; a
# task's category is persisted by name, so a name never changes. With none, two off the core.
CATEGORIES = tuple((c['name'], bool(c.get('core')), c['pattern']) for c in studio_config.get('routing.categories', [
    {'name': 'tools', 'pattern': r'\btool|script|\bcli\b|fe_\w+\.py|manager|discord|\bhook|python|\bboard\b|index|artifact'},
    {'name': 'docs', 'pattern': r'\bdocs?\b|roadmap|decision entr|readme|skill|claude\.md|agents\.md|write[- ]?up|proposal|\bplan\b'},
]))
CORE = {name for name, core, _ in CATEGORIES if core}

# D240: the strong tiers take core work and a stalled Sonnet run. Each category has a home among
# them (the pick with no record, and the tie-break): rendering, where shots decide, goes to the
# ChatGPT model, the rest of the core to Opus. A category's record can move it to the other.
# D270: while the Codex window is under CODEX_GATE percent used, Sol leads every strong pick
# (HOME_DEFAULT) whatever the record says; at or above it, or with no reading, the homes and
# records of D240 stand (HOME, HOME_SPENT_DEFAULT) so the rest of the window lasts the day.
STRONG = tuple(t for t in TIERS if t != 'sonnet')
HOME = dict(studio_config.get('routing.home', {}))
# What a core pick's reason calls the work, and what it adds for the tier it went to (D240).
CORE_WORK = studio_config.get('routing.core_work', 'core work')
HOME_WHY = dict(studio_config.get('routing.home_why', {}))
HOME_DEFAULT = 'sol'
HOME_SPENT_DEFAULT = 'opus'
CODEX_GATE = 70.0

MIN_TRIALS = 3        # a tier's record in a category counts from this many finished tasks
TRUST = 0.75          # Sonnet's share landed unescalated past which a strong suggestion moves to Sonnet
DISTRUST = 0.5        # below which a tier's suggestion moves to another model
CORE_LANDED = 0.85    # Sonnet's share landed past which it goes first even on core work
SONNET_CONTINUES = 2  # consecutive unfinished Sonnet turns before a strong tier takes the session
# Claude's stream reports a message's input tokens exactly but its output only as it began
# (469 of 56,757 on one run): until a turn's result settles it, output is estimated from the
# visible text and tool input the stream carried, 4 characters a token, times what the
# hidden thinking adds (the median over 22 finished runs of their result's output against
# that count: 3.3, range 2.2 to 6.6).
OUTPUT_PER_VISIBLE_TOKEN = 3.3
SYNTHETIC = '<synthetic>'  # Claude Code's placeholder turns name no model (D253)

STATS_DDL = (
    """CREATE TABLE IF NOT EXISTS pm_run_stats(
 run_id TEXT PRIMARY KEY, task_id INTEGER, goal_id INTEGER, role TEXT NOT NULL DEFAULT '',
 provider TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
 queued REAL, started REAL, ended REAL,
 input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
 cost_usd REAL NOT NULL DEFAULT 0, turns INTEGER NOT NULL DEFAULT 0,
 model_s REAL NOT NULL DEFAULT 0, tool_s REAL NOT NULL DEFAULT 0,
 gpu_wait_s REAL NOT NULL DEFAULT 0, gpu_s REAL NOT NULL DEFAULT 0, gpu_runs INTEGER NOT NULL DEFAULT 0,
 frozen_s REAL NOT NULL DEFAULT 0, by_model TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE IF NOT EXISTS pm_task_models(
 task_id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT '', planner_tier TEXT NOT NULL DEFAULT '',
 planner_reason TEXT NOT NULL DEFAULT '', owner_tier TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT '',
 first_tier TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 escalations INTEGER NOT NULL DEFAULT 0, escalated_why TEXT NOT NULL DEFAULT '')""",
)


UNMETERED = 'unmetered_runs'  # pm_settings: runs in flight that started before D218 (JSON list)


def unmetered(b):
    import manager_store as store
    try:
        return set(json.loads(store.setting(b, UNMETERED, '[]')))
    except ValueError:
        return set()


def ensure_tables(con):
    """Added tables only, which no older tool reads: no schema bump (D181)."""
    for stmt in STATS_DDL:
        con.execute(stmt)


def model_id(config, tier):
    pinned = ((config or {}).get('models') or {}).get(tier)
    if pinned:
        return pinned
    if tier == 'sol':
        return latest_codex('sol') or MODEL_IDS['sol']
    return MODEL_IDS.get(tier, MODEL_IDS['opus'])


def _version(text):
    return tuple(int(x) for x in re.findall(r'\d+', text))


CODEX_CACHE = None  # the tests point this at a fixture list


def latest_codex(family, path=None):
    """The newest listed gpt-N-FAMILY in the Codex CLI's model list (D298), which the CLI keeps
    fresh itself (~/.codex/models_cache.json, or under CODEX_HOME); '' when it is unreadable."""
    path = path or CODEX_CACHE
    path = Path(path) if path else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'models_cache.json'
    try:
        rows = json.loads(path.read_text(encoding='utf-8')).get('models') or []
    except (OSError, ValueError, AttributeError):
        return ''
    found = [r['slug'] for r in rows if isinstance(r, dict) and r.get('visibility', 'list') != 'hide'
             and re.fullmatch(rf'gpt-[\d.]+-{re.escape(family)}', str(r.get('slug', '')))]
    return max(found, key=_version, default='')


def tier_label(b, tier):
    """A tier names its class; once it has run, the model its latest run was (D300)."""
    if tier == 'sol':
        return label(latest_codex('sol') or MODEL_IDS['sol'])
    r = b.q1("SELECT model FROM pm_run_stats WHERE tier=? AND model LIKE 'claude-%' ORDER BY "
             "coalesce(started, queued, 0) DESC LIMIT 1", tier) if tier in CLAUDE_TIERS else None
    return label(r[0]) if r else label(tier)


def provider_of(tier):
    """The CLI a tier runs on (D238). An unknown tier is Claude's, as every tier was."""
    return PROVIDER.get(tier, 'claude')


def label(tier_or_model):
    t = str(tier_or_model or '')
    if t in LABEL:
        return LABEL[t]
    m = re.match(r'claude-(\w+)-(\d+)(?:-(\d+))?', t)
    if m:
        return m.group(1).capitalize() + ' ' + m.group(2) + ('.' + m.group(3) if m.group(3) and len(m.group(3)) < 3 else '')
    # D238: the Codex CLI's own ids, 'gpt-6.1-sol' -> 'GPT-6.1 Sol', so a config override reads too.
    m = re.match(r'gpt-([\d.]+)(?:-([a-z]+))?$', t)
    if m:
        return 'GPT-' + m.group(1) + (' ' + m.group(2).capitalize() if m.group(2) else '')
    return t or '?'


def tier_of_model(model):
    m = str(model or '').lower()
    return next((t for t in TIERS if t in m), '')


# ------------------------------------------------------------------ classification

def classify(title, body=''):
    """(category, core): the kind of work, from the words of its title (counted three
    times) and its body. A task naming no category is 'tools', off the core."""
    title, body = str(title or '').lower(), str(body or '').lower()
    scores = {name: 3 * len(re.findall(pattern, title)) + len(re.findall(pattern, body))
              for name, _, pattern in CATEGORIES}
    top = max(scores.values())
    if not top:
        return 'tools', False
    # Work that touches the core is core work even when its own words outnumber the engine's
    # (a knot's crater is a carve): the strongest core category within half the top wins.
    core = max((n for n in CORE if scores[n] >= max(3, top / 2)), key=lambda n: scores[n], default=None)
    name = core or max(scores, key=lambda n: scores[n])
    return name, name in CORE


# ------------------------------------------------------------------ the record

def _row(b, tid):
    r = b.q1('SELECT * FROM pm_task_models WHERE task_id=?', tid)
    return dict(r) if r else None


def _upsert(b, tid, **fields):
    if not _row(b, tid):
        b.con.execute('INSERT INTO pm_task_models(task_id) VALUES(?)', (tid,))
    if fields:
        b.con.execute('UPDATE pm_task_models SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE task_id=?',
                      (*fields.values(), tid))


def suggest(b, tid, tier, reason=''):
    """The planner's suggestion for a task it planned."""
    t = b.task(tid)
    category, _ = classify(t['title'], t['body'])
    _upsert(b, tid, category=category, planner_tier=tier if tier in TIERS else '',
            planner_reason=' '.join(str(reason or '').split())[:300])


def set_owner_tier(b, tid, tier):
    """Yotam's choice: any tier holds whatever the record says; 'auto' hands it back. A pinned
    tier takes its own CLI with it (D238) and waits for it rather than running on another."""
    if tier not in TIERS + ('auto', ''):
        raise ValueError('model must be ' + ', '.join(TIERS) + ' or auto')
    if not b.q1('SELECT 1 FROM pm_tasks WHERE task_id=?', tid):
        raise ValueError(f'T{tid} is not a managed task')
    with b.tx():
        _upsert(b, tid, owner_tier='' if tier == 'auto' else tier)
        b.event('owner', 'manager.model', f'T{tid}', f'model {tier}')


def record(b, category=None):
    """{category: {tier: {'tried', 'landed', 'escalated', 'failed', 'rate'}}} over the
    finished managed tasks, one entry per tier a task was first tried on. Sonnet's try counts
    landed only when it landed without being escalated (the strong tier that took it over is
    not credited: it was not the first choice)."""
    out = {}
    for r in b.q("SELECT x.category, x.first_tier, x.escalations, m.phase, t.status FROM pm_task_models x "
                 "JOIN pm_tasks m ON m.task_id=x.task_id JOIN tasks t ON t.id=x.task_id "
                 "WHERE x.first_tier<>''" + (' AND x.category=?' if category else ''),
                 *((category,) if category else ())):
        landed = r['phase'] == 'landed' or r['status'] == 'done'
        failed = r['phase'] == 'failed' or (r['status'] == 'dropped' and not landed)
        if not (landed or failed or r['escalations']):
            continue  # still in the work
        c = out.setdefault(r['category'] or 'tools', {}).setdefault(
            r['first_tier'], {'tried': 0, 'landed': 0, 'escalated': 0, 'failed': 0})
        c['tried'] += 1
        if r['escalations'] and r['first_tier'] == 'sonnet':
            c['escalated'] += 1
        elif landed:
            c['landed'] += 1
        else:
            c['failed'] += 1
    for tiers in out.values():
        for c in tiers.values():
            c['rate'] = c['landed'] / c['tried'] if c['tried'] else None
    return out.get(category, {}) if category else out


def codex_used(b, now=None):
    """The percent of the Codex allowance used (D244): the fullest window that is still current,
    or None with no reading, only stale ones, or any failure to read."""
    try:
        r = manager_usage.describe('codex', manager_usage.load(b).get('codex'), now)
    except Exception:
        return None
    live = [w['used'] for w in r['windows'] if not w['stale']]
    return max(live) if live else None


def leads(used):
    """Sol leads every strong pick while the Codex window is under CODEX_GATE percent used."""
    return used is not None and used < CODEX_GATE


def gate_note(used):
    if used is None:
        return 'no Codex reading: by record'
    pct = f'Codex at {used:.0f}%'
    return f'{pct}: Sol leads' if leads(used) else f'{pct}: mixed by record'


def home(category, used=None):
    return HOME_DEFAULT if leads(used) else HOME.get(category, HOME_SPENT_DEFAULT)


def _known(c):
    return (c or {}).get('tried', 0) >= MIN_TRIALS


def better_strong(rec, category, used=None):
    """The strong tier with the better record in `category`, or None unless every strong
    tier has MIN_TRIALS finished tries there, or Sol is leading (`used`, D270). A tie goes
    to the category's home."""
    cat = rec.get(category, {})
    if leads(used) or not all(_known(cat.get(t)) for t in STRONG):
        return None
    return max(STRONG, key=lambda t: (cat[t]['rate'], t == home(category)))


def stronger(rec, category, used=None):
    """The strong tier a category's core work, and a stalled Sonnet run, goes to."""
    return better_strong(rec, category, used) or home(category, used)


def tally(rec, category, tier):
    c = rec.get(category, {}).get(tier, {})
    return f"{c.get('landed', 0)} of {c.get('tried', 0)}"


def so_far(rec, category, tier):
    n = rec.get(category, {}).get(tier, {}).get('tried', 0)
    return f"{label(tier)} landed {tally(rec, category, tier)} {category} task{'s' * (n != 1)}"


def by_record(rec, category, used=None):
    """(tier, reason) with no pin and no planner: Sonnet off the core and on core work it
    has landed CORE_LANDED of; otherwise a strong tier (`stronger`), which off the core means
    Sonnet has landed under DISTRUST."""
    core = category in CORE
    sonnet = rec.get(category, {}).get('sonnet', {})
    sofar = so_far(rec, category, 'sonnet')
    if core:
        if _known(sonnet) and sonnet['rate'] >= CORE_LANDED:
            return 'sonnet', f'{category} is core work, but {sofar}'
    elif not (_known(sonnet) and sonnet['rate'] < DISTRUST):
        return 'sonnet', f'{category} is off the core' + (f' and {sofar}' if sonnet.get('tried') else ': Sonnet first')
    tier = stronger(rec, category, used)
    if not core:
        return tier, f'only {sofar}'
    if better_strong(rec, category, used):
        other = next(t for t in STRONG if t != tier)
        return tier, f'{category} is core work: {so_far(rec, category, tier)}, {label(other)} {tally(rec, category, other)}'
    return tier, f'{category} is {CORE_WORK}' + HOME_WHY.get(tier, '')


_READ = object()


def decide(b, tid, rec=None, used=_READ):
    """(tier, reason, category) for T`tid`'s next worker run; writes nothing. `rec` is
    record(b) and `used` codex_used(b), when the caller decides for many tasks at once."""
    t = b.task(tid)
    x = _row(b, tid) or {}
    category = x.get('category') or classify(t['title'], t['body'])[0]
    rec = record(b) if rec is None else rec
    used = codex_used(b) if used is _READ else used
    cat = rec.get(category, {})
    sonnet = cat.get('sonnet', {})
    planner = x.get('planner_tier') or ''
    named = cat.get(planner, {})
    other = better_strong(rec, category, used)
    if x.get('owner_tier'):
        tier, why = x['owner_tier'], f'{studio_config.owner()} chose it'
    elif x.get('escalations'):
        tier = x.get('tier') if x.get('tier') in STRONG else stronger(rec, category, used)
        why = f'escalated from Sonnet to {label(tier)}: ' + (x.get('escalated_why') or 'it did not finish')
    elif planner in STRONG and category not in CORE and _known(sonnet) and sonnet['rate'] >= TRUST:
        tier, why = 'sonnet', f"the planner asked for {label(planner)}, but {so_far(rec, category, 'sonnet')}"
    elif planner == 'sonnet' and _known(sonnet) and sonnet['rate'] < DISTRUST:
        tier, why = stronger(rec, category, used), f"the planner asked for Sonnet, but only {so_far(rec, category, 'sonnet')}"
    elif planner in STRONG and _known(named) and named['rate'] < DISTRUST and other and other != planner:
        tier, why = other, f"the planner asked for {label(planner)}, but only {so_far(rec, category, planner)}"
    elif planner:
        tier, why = planner, 'the planner chose it' + (f": {x['planner_reason']}" if x.get('planner_reason') else '')
    else:
        tier, why = by_record(rec, category, used)
    if tier in STRONG and not x.get('owner_tier') and not x.get('escalations'):
        if leads(used):
            asked = f", not {label(tier)} as the planner chose" if tier != 'sol' else ''
            tier, why = 'sol', gate_note(used) + asked
        else:
            why = f'{gate_note(used)}: {why}'
    return tier, why, category


def choose(b, tid):
    """(tier, reason) for T`tid`'s next worker run, and remembers it."""
    tier, why, category = decide(b, tid)
    _upsert(b, tid, category=category, tier=tier, reason=why[:300],
            **({} if (_row(b, tid) or {}).get('first_tier') else {'first_tier': tier}))
    return tier, why


def escalate(b, tid, why):
    """A strong tier takes the task over from Sonnet: the one with the better record in the
    task's category (`stronger`: the category's home on a tie or with too few tries). The next
    run resumes the session on the same CLI, and on the other starts a fresh one with the
    feedback (queue_task). Returns the tier that took it, or '' when nothing changed: a task
    on a strong tier or pinned to Sonnet is not escalated, and never twice."""
    x = _row(b, tid) or {}
    if x.get('owner_tier') == 'sonnet' or (x.get('tier') or '') != 'sonnet' or x.get('escalations'):
        return ''
    t = b.task(tid)
    used = codex_used(b)
    tier = stronger(record(b), x.get('category') or classify(t['title'], t['body'])[0], used)
    _upsert(b, tid, tier=tier, escalations=(x.get('escalations') or 0) + 1, escalated_why=str(why)[:300])
    b.event('manager', 'manager.escalate', f'T{tid}', f'Sonnet -> {label(tier)} ({gate_note(used)}): {why}'[:300])
    return tier


def run_tier(b, rid):
    r = b.q1('SELECT tier FROM pm_run_stats WHERE run_id=?', rid)
    return r[0] if r else ''


def planner_tier(b):
    """The planner is a Claude tier (D218, D238): routing the tasks is its own job."""
    import manager_store as store
    t = store.setting(b, 'planner_model', 'opus')
    return t if t in CLAUDE_TIERS else 'opus'


# ------------------------------------------------------------------ the meter

def queued(b, rid, task, goal, role, provider, tier, model, now=None):
    b.con.execute('INSERT OR IGNORE INTO pm_run_stats(run_id,task_id,goal_id,role,provider,tier,model,queued) '
                  'VALUES(?,?,?,?,?,?,?,?)', (rid, task, goal, role, provider, tier, model,
                                               time.time() if now is None else now))


class Meter:
    """A run's spend, read from its event stream as it arrives: the model and tokens
    per model (Claude's stream-json, Codex's --json), and its time split into the
    model's and the tools' (the union of the intervals any tool call was open)."""

    def __init__(self, started=None, model=''):
        self.started = time.time() if started is None else started
        self.settled = {}    # model -> token counts from the result events so far
        self.live = {}       # message id -> (model, usage) since the last result
        self.open = {}       # tool call id -> when it started
        self.tool_s, self.tool_since = 0.0, None
        # D238: the run's own model, so a Codex stream that never names one still meters
        # its tokens under the model it was dispatched on rather than a generic 'codex'.
        self.turns, self.cost, self.model = 0, 0.0, model
        self.visible = {}    # model -> characters of text and tool input since the last result
        self.usage = None    # D243: the account's usage windows as the stream last reported them

    def _tool_open(self, key, now):
        if not self.open:
            self.tool_since = now
        self.open[key] = now

    def _tool_close(self, key, now):
        if self.open.pop(key, None) is not None and not self.open and self.tool_since is not None:
            self.tool_s += now - self.tool_since
            self.tool_since = None

    @staticmethod
    def _add(into, model, inp=0, out=0, cr=0, cw=0, cost=0.0):
        c = into.setdefault(model, {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'cost_usd': 0.0})
        c['input'] += int(inp or 0)
        c['output'] += int(out or 0)
        c['cache_read'] += int(cr or 0)
        c['cache_write'] += int(cw or 0)
        c['cost_usd'] += float(cost or 0)

    def feed(self, event, now=None):
        now = time.time() if now is None else now
        kind = event.get('type')
        msg = event.get('message') if isinstance(event.get('message'), dict) else {}
        if kind == 'assistant' and msg:
            # Claude Code's own placeholder turns (an API error, "no response requested")
            # say `<synthetic>`: no model ran them, so they never name the run's model (D253).
            named = msg.get('model')
            model = (named if named != SYNTHETIC else '') or self.model
            if model and not event.get('parent_tool_use_id'):
                self.model = model
            if isinstance(msg.get('usage'), dict):
                self.live[msg.get('id') or len(self.live)] = (model, msg['usage'])
            for c in msg.get('content') or []:
                if not isinstance(c, dict):
                    continue
                if c.get('type') == 'tool_use':
                    self._tool_open(c.get('id'), now)
                    self.visible[model] = self.visible.get(model, 0) + len(json.dumps(c.get('input') or {}))
                elif c.get('type') == 'text':
                    self.visible[model] = self.visible.get(model, 0) + len(c.get('text') or '')
        elif kind == 'user' and msg:
            for c in msg.get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'tool_result':
                    self._tool_close(c.get('tool_use_id'), now)
        elif kind == 'result':
            usage = event.get('modelUsage')
            if isinstance(usage, dict) and usage:
                for model, u in usage.items():
                    self._add(self.settled, model, u.get('inputTokens'), u.get('outputTokens'),
                              u.get('cacheReadInputTokens'), u.get('cacheCreationInputTokens'), u.get('costUSD'))
            else:
                self.settled.update(self._live_counts())
            self.live, self.visible = {}, {}
            self.turns += int(event.get('num_turns') or 0)
            self.cost += float(event.get('total_cost_usd') or 0)
            for key in list(self.open):
                self._tool_close(key, now)
        elif kind == 'rate_limit_event':
            self.usage = manager_usage.from_claude(event, now) or self.usage
        # Codex --json
        elif kind == 'thread.started' and event.get('model'):
            self.model = event['model']
        elif kind == 'turn.completed' and isinstance(event.get('usage'), dict):
            u = event['usage']
            cached = int(u.get('cached_input_tokens') or 0)
            self._add(self.settled, self.model or 'codex', int(u.get('input_tokens') or 0) - cached,
                      u.get('output_tokens'), cached, 0)
            self.turns += 1
        elif kind in ('item.started', 'item.completed'):
            item = event.get('item') or {}
            if item.get('type') in ('command_execution', 'mcp_tool_call', 'file_change', 'web_search'):
                (self._tool_open if kind == 'item.started' else self._tool_close)(item.get('id'), now)

    def _live_counts(self):
        out = {}
        for model, u in self.live.values():
            self._add(out, model or self.model or '?', u.get('input_tokens'), u.get('output_tokens'),
                      u.get('cache_read_input_tokens'), u.get('cache_creation_input_tokens'))
        for model, chars in self.visible.items():
            c = out.setdefault(model or self.model or '?', {'input': 0, 'output': 0, 'cache_read': 0,
                                                            'cache_write': 0, 'cost_usd': 0.0})
            c['output'] = max(c['output'], int(chars / 4 * OUTPUT_PER_VISIBLE_TOKEN))
        return out

    def totals(self, now=None):
        now = time.time() if now is None else now
        by = json.loads(json.dumps(self.settled))
        for model, c in self._live_counts().items():
            self._add(by, model, c['input'], c['output'], c['cache_read'], c['cache_write'])
        tool = self.tool_s + (now - self.tool_since if self.tool_since is not None else 0)
        active = max(0.0, now - self.started)
        return {'model': self.model, 'by_model': by, 'tool_s': tool, 'model_s': max(0.0, active - tool),
                'estimated': bool(self.live or self.visible),
                'turns': self.turns, 'cost_usd': self.cost or sum(c['cost_usd'] for c in by.values()),
                **{k: sum(c[v] for c in by.values()) for k, v in (
                    ('input_tokens', 'input'), ('output_tokens', 'output'),
                    ('cache_read_tokens', 'cache_read'), ('cache_write_tokens', 'cache_write'))}}

    def save(self, b, rid, now=None, ended=False):
        t = self.totals(now)
        b.con.execute(
            'UPDATE pm_run_stats SET model=CASE WHEN ?<>\'\' THEN ? ELSE model END, started=COALESCE(started,?),'
            'input_tokens=?,output_tokens=?,cache_read_tokens=?,cache_write_tokens=?,cost_usd=?,turns=?,'
            'model_s=?,tool_s=?,by_model=?' + (',ended=?' if ended else '') + ' WHERE run_id=?',
            (t['model'], t['model'], self.started, t['input_tokens'], t['output_tokens'], t['cache_read_tokens'],
             t['cache_write_tokens'], t['cost_usd'], t['turns'], t['model_s'], t['tool_s'],
             json.dumps(t['by_model']), *((time.time() if now is None else now,) if ended else ()), rid))


def started(b, run):
    """A run the wrapper starts: its row, made here for a run queued before D218."""
    queued(b, run['id'], run.get('task_id'), run.get('goal_id'), run.get('role') or '', run.get('provider') or '',
           '', '', run.get('created'))
    b.con.execute('UPDATE pm_run_stats SET started=? WHERE run_id=?', (time.time(), run['id']))
    r = b.q1('SELECT tier, model FROM pm_run_stats WHERE run_id=?', run['id'])
    return dict(r) if r else {'tier': '', 'model': ''}


def add_wait(rid, field, seconds, ran=None):
    """A GPU command's wait for the GPU (and its run), or a game's frozen wait, onto the
    managed run that made it. Fails open: accounting never stops the work."""
    if not rid or field not in ('gpu_wait_s', 'frozen_s'):
        return
    try:
        import fe_board
        with fe_board.Board() as b:
            extra = ',gpu_s=gpu_s+?,gpu_runs=gpu_runs+1' if ran is not None else ''
            b.con.execute(f'UPDATE pm_run_stats SET {field}={field}+?{extra} WHERE run_id=?',
                          (float(seconds), *((float(ran),) if ran is not None else ()), rid))
    except Exception:
        pass


# ------------------------------------------------------------------ reading it back

def _run_time(r, now):
    if not r['started']:
        return 0.0
    return max(0.0, (r['ended'] or now) - r['started'])


def task_stats(b, tid, now=None):
    """One task's spend over all its runs: {tier, model, runs, tokens by model, and the
    seconds working (model, tools) and waiting (the GPU, a frozen game, the queue)}."""
    return tasks_stats(b, [tid], now).get(tid)


def _ids(sql, column, ids):
    ids = [x for x in ids]
    return (sql + f" AND {column} IN ({','.join('?' * len(ids))})", tuple(ids))


def _collect(b, rows, key, now):
    out = {}
    blind = unmetered(b)
    for r in rows:
        s = out.setdefault(r[key], {'runs': 0, 'models': {}, 'input_tokens': 0, 'output_tokens': 0,
                                    'cache_read_tokens': 0, 'cache_write_tokens': 0, 'cost_usd': 0.0,
                                    'model_s': 0.0, 'tool_s': 0.0, 'gpu_s': 0.0, 'gpu_wait_s': 0.0,
                                    'frozen_s': 0.0, 'queue_s': 0.0, 'active_s': 0.0, 'model': '', 'tier': '',
                                    'running': False})
        s['runs'] += 1
        for k in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'cost_usd',
                  'model_s', 'tool_s', 'gpu_s', 'gpu_wait_s', 'frozen_s'):
            s[k] += r[k] or 0
        active = _run_time(r, now)
        s['active_s'] += active
        if r['started'] and not r['ended']:
            s['running'] = True
            if r['run_id'] in blind:
                s['unsplit_s'] = s.get('unsplit_s', 0.0) + active  # no clock in its log until it ends
            else:
                # A live run's model and tool time are as of its last save; the rest of its time is the model's.
                s['model_s'] += max(0.0, active - (r['model_s'] or 0) - (r['tool_s'] or 0))
        if r['queued'] and (r['started'] or not r['ended']):
            s['queue_s'] += max(0.0, (r['started'] or now) - r['queued'])
        try:
            by = json.loads(r['by_model'] or '{}')
        except ValueError:
            by = {}
        for model, c in by.items():
            m = s['models'].setdefault(model, {'tokens': 0, 'output': 0, 'cost_usd': 0.0})
            m['tokens'] += sum(c.get(k, 0) for k in ('input', 'output', 'cache_read', 'cache_write'))
            m['output'] += c.get('output', 0)
            m['cost_usd'] += c.get('cost_usd', 0.0)
        s['model'] = r['model'] or s['model']
        s['tier'] = r['tier'] or tier_of_model(r['model']) or s['tier']
    for s in out.values():
        s['tokens'] = s['input_tokens'] + s['output_tokens'] + s['cache_read_tokens'] + s['cache_write_tokens']
        s['waiting_s'] = s['gpu_wait_s'] + s['frozen_s']
        s['working_s'] = max(0.0, s['active_s'] - s['waiting_s'])
        s['line'] = line(s)
    return out


def tasks_stats(b, tids=None, now=None):
    now = time.time() if now is None else now
    sql, args = 'SELECT * FROM pm_run_stats WHERE task_id IS NOT NULL', ()
    if tids is not None:
        if not tids:
            return {}
        sql, args = _ids(sql, 'task_id', [int(t) for t in tids])
    out = _collect(b, b.q(sql + ' ORDER BY queued', *args), 'task_id', now)
    for tid, s in out.items():
        x = _row(b, tid) or {}
        s['tier'] = x.get('tier') or s['tier']
        s['why'] = x.get('reason', '')
        s['category'] = x.get('category', '')
        s['escalations'] = x.get('escalations', 0)
        s['owner_tier'] = x.get('owner_tier', '')
        s['line'] = line(s)
    return out


def runs_stats(b, rids, now=None):
    """{run: the same shape as a task's, for that run alone}."""
    if not rids:
        return {}
    sql, args = _ids('SELECT * FROM pm_run_stats WHERE 1', 'run_id', list(rids))
    return _collect(b, b.q(sql, *args), 'run_id', time.time() if now is None else now)


def routes(b, tids=None):
    """{task: {tier, why, category, owner_tier, escalations}} for tasks with a route, run or not."""
    sql, args = 'SELECT * FROM pm_task_models', ()
    if tids is not None:
        tids = [int(t) for t in tids]
        if not tids:
            return {}
        sql += f" WHERE task_id IN ({','.join('?' * len(tids))})"
        args = tuple(tids)
    return {r['task_id']: {'tier': r['tier'] or r['owner_tier'] or r['planner_tier'], 'why': r['reason'],
                           'planner_tier': r['planner_tier'], 'category': r['category'],
                           'owner_tier': r['owner_tier'], 'escalations': r['escalations']}
            for r in b.q(sql, *args)}


def short(n):
    n = float(n or 0)
    for unit, size in (('M', 1e6), ('k', 1e3)):
        if n >= size:
            return f'{n / size:.1f}{unit}'.replace('.0' + unit, unit)
    return str(int(n))


def mins(seconds):
    s = int(seconds or 0)
    return f'{s // 3600}h{s % 3600 // 60:02d}m' if s >= 3600 else f'{s // 60}m' if s >= 60 else f'{s}s'


def line(s):
    """'Sonnet 5.5 · 90k tokens out, 3.1M in (97% cached) · working 34m (model 20m, tools 14m) · waiting 6m
    (GPU 4m, frozen 2m) · queued 3m'. Cache reads are most of what goes in, and the cheapest."""
    if not s:
        return ''
    who = label(s.get('model') or s.get('tier'))
    if s.get('escalations'):
        who += ' (escalated from Sonnet)'
    waits = ', '.join(f'{k} {mins(v)}' for k, v in (('GPU', s['gpu_wait_s']), ('frozen', s['frozen_s'])) if v >= 1)
    fed = s['tokens'] - s['output_tokens']
    cached = f" ({100 * s['cache_read_tokens'] / fed:.0f}% cached)" if fed else ''
    unsplit = f", {mins(s['unsplit_s'])} not yet split: its run started before D218" if s.get('unsplit_s') else ''
    about = '~' if s.get('running') else ''
    return (f"{who} · {about}{short(s['output_tokens'])} tokens out, {short(fed)} in{cached} · working {mins(s['working_s'])} "
            f"(model {mins(s['model_s'])}, tools {mins(s['tool_s'])}{unsplit}"
            + (f", GPU {mins(s['gpu_s'])}" if s['gpu_s'] >= 1 else '') + ')'
            + (f" · waiting {mins(s['waiting_s'])} ({waits})" if s['waiting_s'] >= 1 else '')
            + (f" · queued {mins(s['queue_s'])}" if s['queue_s'] >= 60 else '')
            + (f" · {s['runs']} runs" if s['runs'] > 1 else ''))


def summary(b, now=None):
    """Per tier over every managed task's runs, and the record per category: what the
    Manager tab, `fe_manager.py models` and the planner read."""
    stats = tasks_stats(b, now=now)
    # D240: every model has a row, run or not, so the three read side by side
    tiers = {t: {'tasks': 0, 'tokens': 0, 'output_tokens': 0, 'working_s': 0.0, 'waiting_s': 0.0,
                 'queue_s': 0.0, 'cost_usd': 0.0, 'times': []} for t in TIERS}
    for tid, s in stats.items():
        t = s.get('tier') or tier_of_model(s.get('model')) or 'codex'
        x = tiers.setdefault(t, {'tasks': 0, 'tokens': 0, 'output_tokens': 0, 'working_s': 0.0,
                                 'waiting_s': 0.0, 'queue_s': 0.0, 'cost_usd': 0.0, 'times': []})
        x['tasks'] += 1
        for k in ('tokens', 'output_tokens', 'working_s', 'waiting_s', 'queue_s', 'cost_usd'):
            x[k] += s[k]
        x['times'].append(s['working_s'])
    for x in tiers.values():
        times = sorted(x.pop('times'))
        x['median_working_s'] = times[len(times) // 2] if times else 0
    rec = record(b)
    # D240: the policy picks among all three; `handover` is who takes a stalled Sonnet run
    used = codex_used(b, now)
    policy = {name: by_record(rec, name, used)[0] for name, _, _ in CATEGORIES}
    handover = {name: stronger(rec, name, used) for name, _, _ in CATEGORIES}
    return {'tiers': tiers, 'record': rec, 'policy': policy, 'handover': handover,
            'codex_used': used, 'gate': gate_note(used), 'advice': advice(b, stats, rec),
            'planner': tier_label(b, planner_tier(b)),
            'labels': {t: tier_label(b, t) for t in TIERS}}  # D238: the board names the tiers from here


def advice(b, stats=None, rec=None):
    """What would make the next tasks faster, read from the record: plain lines."""
    stats = tasks_stats(b) if stats is None else stats
    rec = record(b) if rec is None else rec
    used = codex_used(b)
    out = [gate_note(used) + (f' every strong task until the Codex window passes {CODEX_GATE:g}% used.'
                              if leads(used) else f' (Sol leads below {CODEX_GATE:g}%).')]
    for name, core, _ in CATEGORIES:
        s = rec.get(name, {}).get('sonnet', {})
        tried, rate = s.get('tried', 0), s.get('rate')
        if core and tried < MIN_TRIALS:
            pass
        elif tried < MIN_TRIALS:
            out.append(f'{name}: Sonnet goes first ({tried} of {MIN_TRIALS} tries so far before its record counts).')
        elif rate >= TRUST:
            out.append(f'{name}: Sonnet landed {s["landed"]} of {tried}; it takes this work even when the planner asks for a stronger model.'
                       if not core else f'{name}: Sonnet landed {s["landed"]} of {tried} core tasks; it now goes first.')
        elif rate < DISTRUST:
            out.append(f'{name}: Sonnet landed only {s["landed"]} of {tried}; {label(stronger(rec, name, used))} takes it '
                       f'until {studio_config.owner()} sets a task to Sonnet.')
        better = better_strong(rec, name, used)
        if better and better != home(name, used):
            out.append(f'{name}: {label(better)} landed {tally(rec, name, better)}, {label(home(name, used))} '
                       f'{tally(rec, name, home(name, used))}; {label(better)} now takes it, and a Sonnet run that stalls.')
    wait = sum(s['waiting_s'] for s in stats.values())
    work = sum(s['working_s'] for s in stats.values())
    if wait > 0.1 * (wait + work) and wait > 600:
        out.append(f'Waiting for the GPU took {mins(wait)}, {100 * wait / (wait + work):.0f}% of the workers\' time: '
                   + studio_config.get('routing.gpu_advice', 'fewer, batched GPU runs would give it back.'))
    queue = sum(s['queue_s'] for s in stats.values())
    if queue > 0.25 * max(work, 1) and queue > 1800:
        out.append(f'Approved tasks sat {mins(queue)} in the queue waiting for a slot or a checkout.')
    return out


def planner_brief(b):
    """The record, for the planner choosing each task's model (D218)."""
    rec, used = record(b), codex_used(b)
    work = lambda k, d: studio_config.get('routing.brief.' + k, d)  # noqa: E731  the game's words for its work
    rows = []
    for name, core, _ in CATEGORIES:
        cells = [f"{t} {c['landed']}/{c['tried']}" for t, c in sorted(rec.get(name, {}).items())]
        rows.append(f"  {name}{' (core)' if core else ''}: " + (', '.join(cells) or 'no finished tasks yet'))
    return ('Choose each task\'s model in model, with model_reason (each is a class of model and runs '
            'its latest). Sonnet is several times faster '
            f'and is the default: use it for {work("sonnet", "work off the core")}. Choose opus when the task '
            f'{work("opus", "is core work, a cross-cutting refactor or a hard bug hunt")}, '
            f'and sol (the ChatGPT model {label(model_id(None, "sol"))}, run on the Codex CLI) when it '
            f'{work("sol", "is work where reading screenshots decides")}: say which in model_reason. A Sonnet run '
            'that fails or stalls is handed to opus or sol, whichever has the better record in that kind of '
            'work, so a wrong Sonnet guess costs little. Credit rule (D270): while the Codex allowance is '
            f'under {CODEX_GATE:g}% used the manager runs every task that is not a Sonnet task on sol, '
            'whatever you pick and whatever the record says, so choose sol for strong work; at or above it, '
            f'or with no reading, strong work is mixed by record ({work("mixed", "each category to its home model")}, '
            'the better record wins). '
            + ('There is no usable Codex reading now' if used is None else f'Codex is at {used:.0f}% used now')
            + '. What each model has landed so far (landed/tried, by category):\n' + '\n'.join(rows) + '\n')


def report(b, tids=None, last=12, now=None):
    """`fe_manager.py models` and Discord: each tier's totals, the record and the policy
    per category, what would make it faster, and the latest managed tasks' spend."""
    s = summary(b, now)
    out = ['Models (D218, D240): ' + '; '.join(
        f"{s['labels'].get(t, label(t))} ({provider_of(t)}) " + (f"{x['tasks']} task{'s' * (x['tasks'] != 1)}, {short(x['output_tokens'])} tokens out, "
                                              f"median {mins(x['median_working_s'])} working" if x['tasks'] else 'no tasks yet')
        for t, x in sorted(s['tiers'].items(), key=lambda kv: (kv[0] not in TIERS, TIERS.index(kv[0]) if kv[0] in TIERS else 0)))]
    out.append('Policy by category (landed/tried on its first model, * some escalated; a stalled Sonnet run goes to hand-over):')
    out.append(f"  {'':<11} {'first':<6} {'':<5} {'  '.join(f'{t:<8}' for t in TIERS)}  hand-over")
    for name, core, _ in CATEGORIES:
        rec = s['record'].get(name, {})
        cells = '  '.join(f"{(str(c['landed']) + '/' + str(c['tried']) + ('*' * bool(c['escalated']))) if (c := rec.get(t)) else '-':<8}"
                          for t in TIERS)
        out.append(f"  {name:<11} {s['policy'][name]:<6} {'core ' if core else '     '}{cells}  {s['handover'][name]}")
    planner = planner_tier(b)
    out.append(f'Planner: {s["planner"]} (the {planner} class, its latest model; D298)')
    if s['advice']:
        out.append('To go faster:')
        out += ['  ' + a for a in s['advice']]
    if tids is None:
        tids = [r[0] for r in b.q('SELECT task_id FROM pm_tasks ORDER BY task_id DESC LIMIT ?', last)]
    stats, rt = tasks_stats(b, tids, now), routes(b, tids)
    if tids:
        out.append('Tasks:')
    for tid in tids:
        t = b.task(tid)
        r = rt.get(tid) or {}
        head = f"  T{tid} {t['title'][:60]} [{t['status']}]"
        if tid in stats:
            out.append(head + ' · ' + stats[tid]['line'])
        else:
            out.append(head + (f" · {label(r['tier'])} next" if r.get('tier') else ' · not run yet'))
        if r.get('why'):
            out.append(f"      model: {r['why']}")
    return out


def relabel_synthetic(b):
    """Runs metered before D253 whose model reads `<synthetic>`: name the model that
    spent the most in their by_model, else the tier's model. Returns how many."""
    n = 0
    for r in b.q("SELECT run_id, tier, by_model FROM pm_run_stats WHERE model=?", SYNTHETIC):
        try:
            by = {m: c for m, c in json.loads(r['by_model'] or '{}').items() if m != SYNTHETIC}
        except ValueError:
            by = {}
        real = max(by, key=lambda m: (by[m].get('cost_usd') or 0, by[m].get('output') or 0)) if by else ''
        real = real or MODEL_IDS.get(r['tier'] or '', '')
        if real:
            b.con.execute('UPDATE pm_run_stats SET model=? WHERE run_id=?', (real, r['run_id']))
            n += 1
    return n


def backfill(b, limit=None):
    """Runs that ended unmetered (before D218, or a wrapper that died before it could
    close its record): read their logs once. Their tool time is the part of the run the
    model's API time (the result's duration_api_ms) leaves, since the log carries no
    clock. A run still going that started before D218 is given its model now: Opus, the
    only model then. Returns how many ended runs it read."""
    import manager_workers as workers
    import manager_store as store
    relabel_synthetic(b)
    blind = unmetered(b)
    for r in b.q("SELECT * FROM pm_runs WHERE state='running' AND id NOT IN (SELECT run_id FROM pm_run_stats)"):
        blind.add(r['id'])
        claude = r['provider'] == 'claude'
        queued(b, r['id'], r['task_id'], r['goal_id'], r['role'], r['provider'], 'opus' if claude else '',
               'opus' if claude else r['provider'], r['created'])
        b.con.execute('UPDATE pm_run_stats SET started=? WHERE run_id=?', (r['pid_started'], r['id']))
        if r['task_id'] and r['role'] == 'worker' and claude and not (_row(b, r['task_id']) or {}).get('first_tier'):
            _upsert(b, r['task_id'], first_tier='opus', tier='opus', reason='before D218: Opus ran every task')
    # A row the backfill made carries the wrapper's own start (pid_started); a metered
    # wrapper stamps its own clock (started), so this finds the ones made before the flag.
    blind |= {r[0] for r in b.q("SELECT r.id FROM pm_runs r JOIN pm_run_stats s ON s.run_id=r.id WHERE "
                                "r.state='running' AND s.ended IS NULL AND s.started=r.pid_started")}
    going = {r[0] for r in b.q("SELECT id FROM pm_runs WHERE state='running'")}
    for rid in blind & going:
        # Its tokens so far, from its log: its wrapper predates the meter.
        meter = Meter(started=0)
        try:
            with (workers.run_dir(rid) / 'events.jsonl').open(encoding='utf-8', errors='replace') as f:
                for raw in f:
                    try:
                        meter.feed(json.loads(raw), now=0)
                    except ValueError:
                        continue
        except OSError:
            continue
        t = meter.totals(now=0)
        b.con.execute('UPDATE pm_run_stats SET model=CASE WHEN ?<>\'\' THEN ? ELSE model END,input_tokens=?,'
                      'output_tokens=?,cache_read_tokens=?,cache_write_tokens=?,turns=?,by_model=? WHERE run_id=?',
                      (t['model'], t['model'], t['input_tokens'], t['output_tokens'], t['cache_read_tokens'],
                       t['cache_write_tokens'], t['turns'], json.dumps(t['by_model']), rid))
    rows = b.q("SELECT r.* FROM pm_runs r LEFT JOIN pm_run_stats s ON s.run_id=r.id "
               "WHERE r.state NOT IN ('queued','running') AND (s.run_id IS NULL OR s.ended IS NULL) "
               "ORDER BY r.created" + (f' LIMIT {int(limit)}' if limit else ''))
    for r in rows:
        r = dict(r)
        start = r['pid_started'] or r['created']
        end = max(start, r['heartbeat'] or start)
        meter, api_ms = Meter(started=start), 0
        try:
            with (workers.run_dir(r['id']) / 'events.jsonl').open(encoding='utf-8', errors='replace') as f:
                for raw in f:
                    try:
                        e = json.loads(raw)
                    except ValueError:
                        continue
                    if e.get('type') == 'result':
                        api_ms += int(e.get('duration_api_ms') or 0)
                    meter.feed(e, now=start)
        except (OSError, ValueError):
            pass
        t = meter.totals(now=end)
        active = end - start
        model_s = min(active, api_ms / 1000) if api_ms else active
        have = b.q1('SELECT tier, model FROM pm_run_stats WHERE run_id=?', r['id'])
        model = t['model'] or (have and have['model']) or (r['provider'] if r['provider'] != 'claude' else 'opus')
        tier = (have and have['tier']) or (tier_of_model(model) if r['provider'] == 'claude' else '')
        with b.tx():
            queued(b, r['id'], r['task_id'], r['goal_id'], r['role'], r['provider'], tier, model, r['created'])
            b.con.execute('UPDATE pm_run_stats SET tier=?,model=?,started=?,ended=?,input_tokens=?,output_tokens=?,'
                          'cache_read_tokens=?,cache_write_tokens=?,cost_usd=?,turns=?,model_s=?,tool_s=?,by_model=? '
                          'WHERE run_id=?', (tier, model, start, end, t['input_tokens'], t['output_tokens'],
                                             t['cache_read_tokens'], t['cache_write_tokens'], t['cost_usd'], t['turns'],
                                             model_s, active - model_s, json.dumps(t['by_model']), r['id']))
            if r['task_id'] and r['role'] == 'worker' and tier:
                x = _row(b, r['task_id'])
                if not x or not x.get('first_tier'):
                    t_ = b.task(r['task_id'])
                    category = (x or {}).get('category') or classify(t_['title'], t_['body'])[0]
                    _upsert(b, r['task_id'], category=category, first_tier=tier,
                            tier=(x or {}).get('tier') or tier, reason=(x or {}).get('reason') or 'before D218: Opus ran every task')
    blind &= going | {r[0] for r in b.q("SELECT run_id FROM pm_run_stats WHERE ended IS NULL")}
    store.set_setting(b, UNMETERED, json.dumps(sorted(blind)))
    return len(rows)


def board_view(b, now=None):
    """{task: {tier, label, why, line, ...}} for every managed task: the board's cards, its
    drawer and the Agents view (D218)."""
    tids = [r[0] for r in b.q('SELECT task_id FROM pm_tasks')]
    stats, rt, rec = tasks_stats(b, tids, now), routes(b, tids), record(b)
    out = {}
    for tid in tids:
        s, r = stats.get(tid) or {}, rt.get(tid) or {}
        # What ran last (or runs now), and what the next run would run on and why.
        nxt, why, category = decide(b, tid, rec)
        ran = s.get('model') or (s.get('tier') if s else '')
        out[tid] = {'tier': s.get('tier') or nxt, 'label': label(ran) if ran else '', 'next': label(nxt),
                    'next_tier': nxt, 'why': why, 'ran_why': r.get('why') or '',
                    'planner_tier': r.get('planner_tier') or '', 'owner_tier': r.get('owner_tier') or '',
                    'category': category,
                    'escalations': r.get('escalations') or 0, 'line': s.get('line', ''), 'running': s.get('running', False),
                    **{k: s.get(k, 0) for k in ('output_tokens', 'tokens', 'working_s', 'waiting_s', 'gpu_wait_s',
                                                'frozen_s', 'queue_s', 'model_s', 'tool_s', 'gpu_s', 'runs', 'cost_usd')},
                    'models': s.get('models', {})}
    return out
