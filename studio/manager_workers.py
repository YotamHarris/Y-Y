"""CLI adapters and restart-surviving worker processes. Uses subscription logins."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

import fe_board
import manager_store as store
import manager_usage
import studio_config

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The service runs under pythonw, which has no console. A console program started from a
# process with none (a worker's `pythonw fe_manager.py gpu`, a land's gates) opens a window of
# its own that takes the focus. What the service starts runs on python.exe, CREATE_NO_WINDOW,
# so it and everything under it share one hidden console.
def console_python(exe=sys.executable):
    w = Path(exe).with_name('python.exe')
    return str(w) if Path(exe).name.lower() == 'pythonw.exe' and w.exists() else exe


PYTHON = console_python()
RESULT_GRACE = 60  # s a finished claude -p may take to exit before its tree is ended
# The evidence a worker's result carries, one check each: the game's definition of done ([checks]
# names); 'tests' is always one, and the only one the studio itself requires to pass.
CHECKS = tuple(studio_config.get('checks.names', ['tests']))
# The checks that may pass with a debt record, which the game's debt adapter validates.
DEBT_CHECKS = tuple(studio_config.get('checks.debt', []))


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string'}
DETOUR_CAUSES = ('place', 'tool', 'silent', 'environment', 'workaround')
CREW = obj({'changes': {'type': 'array', 'items': obj({'commit': TEXT, 'what': TEXT, 'why': TEXT, 'numbers': TEXT})},
            'rejected': {'type': 'array', 'items': obj({'what': TEXT, 'why': TEXT})},
            'target': obj({'cause': TEXT, 'key': TEXT}), 'tool': TEXT})
SCHEMA = obj({
    # D259: 'compact' is a worker at a phase boundary; its wrapper compacts the session and resumes it.
    'status': {'type': 'string', 'enum': ['complete', 'continue', 'blocked', 'planned', 'chat', 'compact']},
    'summary': TEXT, 'head': TEXT,
    # The planner's short name for its goal (D193); empty from a worker.
    'goal_name': TEXT,
    # What Yotam reads in Discord (manager_brief); summary is the full record behind it.
    # D224: each ask is one decision with two to four options, the recommended first: a button each in Discord.
    'problem': TEXT, 'bottom_line': TEXT,
    'asks': {'type': 'array', 'items': obj({'question': TEXT, 'options': {'type': 'array', 'items': TEXT}})},
    # A planned task's reasoning is why it is done (D195): Yotam edits it before approving.
    # D218: the model the planner suggests for each task, and why (manager_models.choose checks it).
    'tasks': {'type': 'array', 'items': obj({'title': TEXT, 'reasoning': TEXT, 'body': TEXT, 'problem': TEXT,
              'depends': {'type': 'array', 'items': {'type': 'integer'}},
              'model': {'type': 'string', 'enum': ['sonnet', 'opus', 'sol']}, 'model_reason': TEXT})},
    'checks': {'type': 'array', 'items': obj({'name': {'type': 'string', 'enum': list(CHECKS)},
               'outcome': {'type': 'string', 'enum': ['passed', 'passed_with_debt', 'not_applicable', 'failed']},
               'command': TEXT, 'detail': TEXT, 'debt_record': TEXT})},
    'artifacts': {'type': 'array', 'items': TEXT},
    # A delivered proposal whose acceptance authorizes implementation; empty for other work.
    'plan_document': TEXT,
    # The times the run had to work around the project instead of doing its task (the process
    # crew's measure, fe_usage.py detours): a place, a tool, a silent failure, the environment,
    # or a script written because a tool was missing.
    'detours': {'type': 'array', 'items': obj({'cause': {'type': 'string', 'enum': list(DETOUR_CAUSES)},
                'what': TEXT, 'minutes': {'type': 'number'}})},
    # A cleanup crew's account (D319), in plain words, which fe_crew.report lays out for Yotam: each
    # commit's what and why and the measure it moved, what was turned down and why, and the process
    # crew's target and tool as fe_usage.py detours prints them. Empty for every other task.
    'crew': CREW,
})
CREW_EMPTY = {'changes': [], 'rejected': [], 'target': {'cause': '', 'key': ''}, 'tool': ''}


# A run started before these existed still returns a valid result without them.
LATER = {'problem': '', 'bottom_line': '', 'asks': [], 'goal_name': '', 'detours': [], 'plan_document': '',
         'crew': CREW_EMPTY}


def validate_result(result):
    if isinstance(result, dict):
        for k, v in LATER.items():
            result.setdefault(k, json.loads(json.dumps(v)))
    if not isinstance(result, dict) or set(result) != set(SCHEMA['properties']):
        raise ValueError('missing structured result fields')
    if result['status'] not in SCHEMA['properties']['status']['enum']:
        raise ValueError('invalid result status')
    if any(not isinstance(result[k], str) for k in ('summary', 'head', 'problem', 'bottom_line', 'goal_name', 'plan_document')):
        raise ValueError('invalid summary/head')
    if result['plan_document'] and not re.fullmatch(r'docs/[\w-]+\.md', result['plan_document']):
        raise ValueError('plan_document must be a standalone docs/NAME.md plan')
    # a plain line is an ask from a run started before D224
    if not isinstance(result['asks'], list) or not all(
            isinstance(a, str) or (isinstance(a, dict) and isinstance(a.get('question'), str)
                                   and isinstance(a.get('options', []), list)) for a in result['asks']):
        raise ValueError('invalid asks')
    if any(not isinstance(result[k], list) for k in ('tasks', 'checks', 'artifacts', 'detours')):
        raise ValueError('invalid result arrays')
    for c in result['checks']:
        if isinstance(c, dict):
            c.setdefault('debt_record', '')  # compatibility with already-running workers
        if not isinstance(c, dict) or c.get('name') not in CHECKS or c.get('outcome') not in (
                'passed', 'passed_with_debt', 'not_applicable', 'failed') or not isinstance(c.get('detail'), str):
            raise ValueError('invalid check')
        if c['outcome'] == 'passed_with_debt' and c['name'] not in DEBT_CHECKS:
            raise ValueError(f'only {" or ".join(DEBT_CHECKS) or "no check"} may pass with debt')
    if not all(isinstance(a, str) for a in result['artifacts']):
        raise ValueError('invalid artifact path')
    crew = result['crew']
    if not isinstance(crew, dict):
        raise ValueError('invalid crew report')
    for k, v in CREW_EMPTY.items():
        crew.setdefault(k, json.loads(json.dumps(v)))
    rows = lambda k: crew[k] if isinstance(crew[k], list) and all(isinstance(x, dict) for x in crew[k]) else None  # noqa: E731
    if rows('changes') is None or rows('rejected') is None or not isinstance(crew['target'], dict) \
            or not isinstance(crew['tool'], str):
        raise ValueError('invalid crew report')
    return result


# D325: a quick answer (D317, D322) returns only the message Yotam reads. The workers'
# report fields (summary, bottom_line, ...) made it write a report about its answer.
QUICK_SCHEMA = obj({'answer': TEXT})


def schema_for(role):
    return QUICK_SCHEMA if role == 'quick' else SCHEMA


def quick_result(raw):
    """A quick run's answer as the result the manager handles: a chat turn."""
    if not isinstance(raw, dict) or not isinstance(raw.get('answer'), str) or not raw['answer'].strip():
        raise ValueError('missing answer')
    return validate_result({'status': 'chat', 'summary': raw['answer'].strip(), 'head': '',
                            'tasks': [], 'checks': [], 'artifacts': []})


def evidence_passes(result, head):
    try:
        validate_result(result)
    except ValueError:
        return False
    checks = result['checks']
    return (result['head'] == head and len(checks) == len(CHECKS)
            and {c['name'] for c in checks} == set(CHECKS)
            and all(check_passes(c, head) for c in checks)
            and any(c['name'] == 'tests' and c['outcome'] == 'passed' for c in checks))


def check_passes(c, head):
    return (c['outcome'] in ('passed', 'passed_with_debt', 'not_applicable') and c['detail'].strip()
            and (c['outcome'] == 'not_applicable' or isinstance(c.get('command'), str) and c['command'].strip())
            and (c['outcome'] != 'passed_with_debt' or valid_debt(c.get('debt_record'), head)))


def valid_debt(path, head):
    """A waiver is durable registry evidence for this HEAD, not worker prose: the game's debt
    adapter judges it ([adapters] debt); with none, no debt is valid."""
    debt = studio_config.adapter('debt')
    return bool(debt) and debt.valid_debt(path, head)


# D253: the checks fe_land.py's gates answer once the supervisor has landed the work ([checks] land).
LAND_CHECKS = {k: tuple(v) for k, v in studio_config.get('checks.land', {}).items()}
# The check whose pass claims bit-identity, so the land runs `fe_land.py --refactor` ([checks] refactor).
REFACTOR_CHECK = studio_config.get('checks.refactor')
LAND_HOURS = 2  # a land past this is ended and handed back to the worker
ARTIFACTS = studio_config.rel(studio_config.artifacts_dir())  # where a checkout's fe_land.py logs, from its root


def _git(path, *args):
    return subprocess.run(['git', *args], cwd=path, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', creationflags=NO_WINDOW).stdout.strip()


def land_args(result):
    """fe_land.py's flags from the worker's own checks: --refactor when it claims bit-identity
    (its regression check passed, or names `fe_land.py --refactor` as its command; never read from
    the detail's prose, where "fe_land runs the docs gates" made a docs-only plan replay 13 scenes).
    Never a bench (D262): a worker that measured did so itself, and main is benched nightly."""
    checks = {c['name']: c for c in result.get('checks') or []}
    regression = checks.get(REFACTOR_CHECK, {})
    return ['--refactor'] if regression.get('outcome') == 'passed' or '--refactor' in (
        regression.get('command') or '') else []


def landed_checks(result, gates):
    """The worker's checks, with each one a gate that ran replaced by that gate's line."""
    out = []
    for c in result['checks']:
        ran = [g for g in gates if g['gate'].split()[0] in LAND_CHECKS.get(c['name'], ())]
        if c['name'] == 'tests' and not ran:
            # A docs-only land (T62's plan) builds nothing: its docs and index checks are its tests,
            # and the manager accepts no task without a passed tests check.
            ran = [g for g in gates if g['gate'].split()[0] in ('docs', 'index')]
        if ran and all(g['status'] == 'ok' for g in ran) and not (
                c['name'] in DEBT_CHECKS and c['outcome'] == 'passed_with_debt'):
            c = dict(c, outcome='passed', command=f'{studio_config.tool_cmd("fe_land.py")} (run by the manager, D253)',
                     detail='; '.join(f'{g["gate"]}: {g["line"]}' for g in ran))
        out.append(c)
    return out


def land(run, result, heartbeat=lambda: None, popen=subprocess.Popen):
    """D253: the worker committed and stopped; the supervisor runs its close, fe_land.py, so no
    model turn waits on the gates. Landed: the result names the pushed HEAD and carries the gate
    lines as evidence. Stopped at a gate: a continue result whose note is that gate, so the same
    session resumes to fix it. Anything else (pushed by hand, a dirty tree, a checkout without
    fe_land.py) is returned as it was, for Manager.unlanded to judge."""
    mobile = studio_config.adapter('mobile')
    if mobile is not None:
        return mobile.land(run, result, heartbeat=heartbeat)
    path = Path(run['cwd'])
    tool = studio_config.studio_in(path) / 'fe_land.py'
    if run['role'] != 'worker' or result.get('status') != 'complete' or not tool.exists():
        return result
    if _git(path, 'status', '--porcelain', '--untracked-files=no'):
        return result
    # Only what the worker's own evidence supports is pushed: every check the gates do not answer.
    head = _git(path, 'rev-parse', 'HEAD')
    checks = result.get('checks') or []
    if {c['name'] for c in checks} != set(CHECKS) or not all(
            check_passes(c, head) for c in checks if c['name'] not in LAND_CHECKS):
        return result
    _git(path, 'fetch', '-q', 'origin')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', 'HEAD', 'origin/main'], cwd=path,
                      capture_output=True, creationflags=NO_WINDOW).returncode == 0:
        return result  # it pushed itself
    args = land_args(result)
    log = run_dir(run['id']) / 'land.log'
    started = time.time()
    with log.open('w', encoding='utf-8') as out:
        p = popen([PYTHON, str(tool), *args], cwd=path, env=clean_env(run), stdout=out,
                  stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        while True:
            try:
                code = p.wait(timeout=20)
                break
            except subprocess.TimeoutExpired:
                heartbeat()  # a bench prints nothing for minutes; the run is not stalled
                if time.time() - started > LAND_HOURS * 3600:
                    end_tree(p)
                    code = None
                    break
    text = log.read_text(encoding='utf-8', errors='replace')
    shown = '\n'.join(text.splitlines()[-25:])
    cmd = ' '.join([f'python {tool.relative_to(path).as_posix()}', *args])
    if code != 0:
        why = f'did not end within {LAND_HOURS} h' if code is None else 'stopped'
        return dict(result, status='continue', summary=(
            f'The manager ran `{cmd}` on your commit and it {why} (D253):\n{shown}\n'
            f'The whole output is {ARTIFACTS}/land-{run.get("agent") or "<agent>"}.log. Fix what it names, '
            'commit, and return complete again: the manager runs the land again. '
            'Your note:\n' + result['summary']))
    try:
        gates = json.loads((path / ARTIFACTS / f'land-{run.get("agent")}.json')
                           .read_text(encoding='utf-8'))['gates']
    except (OSError, ValueError, KeyError):
        gates = []
    head = _git(path, 'rev-parse', 'HEAD')
    return dict(result, head=head, checks=landed_checks(result, gates),
                summary=result['summary'] + f'\n\nLanded by the manager: `{cmd}`\n{shown}')


def landing(run, now=None):
    """What the manager's land of this run is doing now, for the board: 'ran: landing: regress
    (1 of 4) for 26 min; last: ...', or '' when it is not landing. T62 sat 50 minutes in a regress
    while the board showed the worker's last step. Read from fe_land's land-<agent>.json, which
    names the running gate until the land ends; one older than this run's land.log is a past land."""
    started = run_dir(run['id']) / 'land.log'
    try:
        since = started.stat().st_ctime
    except OSError:
        return ''
    try:
        j = json.loads((Path(run['cwd']) / ARTIFACTS / f"land-{run.get('agent')}.json")
                       .read_text(encoding='utf-8'))
    except (OSError, ValueError):
        j = {}
    if j.get('at', 0) < since - 5:
        return 'ran: landing: starting fe_land'
    if 'running' not in j:
        return ''
    done = ', '.join(f"{g['gate']} {g['status']}" for g in j.get('gates') or [])
    mins = int(((now or time.time()) - j.get('since', since)) // 60)
    return (f"ran: landing: {j['running']} ({len(j.get('gates') or []) + 1} of {j.get('of', '?')}) for {mins} min"
            + (f"; done {done}" if done else '') + (f"; last: {j['last']}" if j.get('last') else ''))


def run_dir(rid):
    if not rid or any(c not in '0123456789abcdef' for c in rid):
        raise ValueError('invalid run ID')
    return fe_board.board_dir() / 'manager' / 'runs' / rid


def clean_env(run):
    env = dict(os.environ)
    mobile = studio_config.adapter('mobile')
    if mobile is not None:
        env = mobile.provider_environment(env)
    # Avoid API billing and avoid borrowing the parent chat's identity.
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                'CODEX_THREAD_ID', 'CLAUDECODE', 'FE_AGENT', 'FE_CONTROL_PORT',
                'CODEX_SESSION_ID', 'CODEX_PERMISSION_PROFILE', 'CODEX_CI',
                'CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'CODEX_APP_TOOLS_PIPE_PATH',
                'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
                'OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL'):
        env.pop(key, None)
    env['FE_MANAGER_RUN'] = run['id']
    env['FE_BOARD_DIR'] = str(fe_board.board_dir())
    env['PYTHONUTF8'] = '1'
    if run.get('agent'):
        env['FE_AGENT'] = run['agent']
        env['FE_CONTROL_PORT'] = str(fe_board.registry()['agents'][run['agent']]['port'])
    return env


# T94: the read-only runs are told (AGENTS.md) to look code and histories up with these tools, and
# dontAsk refused every call of them, in both shells: 17 denials in 14 sessions, 4.8M tokens a week.
READONLY_COMMANDS = (
    'git status *', 'git diff *', 'git log *', 'git show *',
    *(f'{studio_config.tool_cmd(tool)} {verb}*' for tool, verb in (
        ('fe_index.py', ''), ('fe_docs.py', 'show '), ('fe_docs.py', 'category '), ('fe_docs.py', 'status '),
        ('fe_docs.py', 'queue '), ('fe_board.py', 'inbox '), ('fe_board.py', 'task show '),
        ('fe_board.py', 'task list '), ('fe_board.py', 'who '), ('fe_manager.py', 'status '),
        ('fe_manager.py', 'who '))),
)
READONLY_SHELL = [f'{shell}({c})' for c in READONLY_COMMANDS for shell in ('Bash', 'PowerShell')]


def command(run, config, folder):
    provider = run['provider']
    exe = config[provider]
    if provider == 'claude':
        import manager_host  # D298: the newest CLI, so `opus` and `sonnet` mean the latest of each
        exe = manager_host.claude_cli(exe)
    readonly = run['role'] in ('planner', 'conversation', 'quick')
    if provider == 'codex':
        # D256: a planner reads the web; --search is Codex's own web_search tool, which runs
        # on OpenAI's side and so works in the read-only sandbox.
        cmd = [exe] + (['--search'] if readonly else ['--approve-for-me']) + ['exec']
        if run.get('session'):
            cmd += ['resume', run['session']]
        # D238: the tier's own model; a run dispatched to Codex as a fallback has the
        # provider's name here and takes the CLI's configured default, as before.
        if run.get('model') and run['model'] != provider:
            cmd += ['-m', run['model']]
        cmd += ['--json', '-c', 'forced_login_method="chatgpt"', '-c', 'model_provider="openai"',
                '-c', 'approval_policy="never"' if readonly else 'approval_policy="on-request"', '-c',
                'sandbox_mode="read-only"' if readonly else 'sandbox_mode="workspace-write"',
                '--output-schema', str(folder / 'schema.json'), '-o', str(folder / 'answer.json'), '-']
        if not readonly:
            cmd[-1:-1] = ['-c', 'sandbox_workspace_write.writable_roots=' + json.dumps([str(fe_board.board_dir())])]
        # Codex shell tools may reconstruct their environment instead of inheriting
        # the CLI process's environment. Carry the supervisor identity explicitly,
        # including on exact-session resume where the run ID has changed.
        env = clean_env(run)
        for key in ('FE_MANAGER_RUN', 'FE_BOARD_DIR', 'FE_AGENT', 'FE_CONTROL_PORT', 'PYTHONUTF8'):
            if key in env:
                cmd[-1:-1] = ['-c', 'shell_environment_policy.set.' + key + '=' + json.dumps(env[key])]
    else:
        # D218: the run's own model (manager_models.choose); a run from before it is on Opus.
        # --thinking-display summarized: the log keeps what it thought, for the board's transcript
        cmd = [exe, '-p', '--model', run.get('model') or 'opus', '--thinking-display', 'summarized',
               '--verbose', '--output-format', 'stream-json', '--permission-mode',
               'dontAsk' if readonly else 'auto', '--permission-prompts', 'none',
               '--json-schema', json.dumps(schema_for(run['role']))]
        if readonly:
            # D256: the web tools too, so a plan can rest on a library's docs or a paper
            cmd += ['--tools', 'Read,Glob,Grep,Bash,PowerShell,WebFetch,WebSearch', '--allowedTools',
                    ','.join(['Read', 'Glob', 'Grep', 'WebFetch', 'WebSearch'] + READONLY_SHELL)]
        elif run['role'] == 'worker':
            mobile = studio_config.adapter('mobile')
            if mobile is not None and hasattr(mobile, 'worker_edit_rules'):
                rules = mobile.worker_edit_rules()
                if rules:
                    cmd += ['--allowedTools', ','.join(rules)]
        if run.get('session'):
            cmd += ['--resume', run['session']]
    return cmd


# D259: what a compaction keeps. The worker asks for one at a phase boundary (built, measured,
# ready to commit); the summary replaces the conversation, so whatever is only in it must survive.
COMPACT_FOCUS = ("Keep: the task number and what it is for; the commits and the files changed; every "
                 "screenshot read and what it showed; each measured number with the command that gave it; "
                 "what was decided or ruled out, and why; the steps left. Drop file contents, diffs and "
                 "tool output that are on disk or in git: they can be read again.")
COMPACT_MAX = 8  # compactions in one run; past it the request is a plain continue


def compact_command(run, config, note):
    """The /compact turn for a Claude session (D259): a slash command in -p, on the same session."""
    return [config[run['provider']], '-p', f'/compact {COMPACT_FOCUS} Where the work stands: {note}',
            '--model', run.get('model') or 'opus', '--verbose', '--output-format', 'stream-json',
            '--resume', run['session']]


def carry_on(note):
    return (f"The session was compacted at your phase boundary (D259). Where you said the work stands:\n{note}\n\n"
            "Carry on with the next part of the task under the same rules, and return the structured result "
            "as before.")


def failure_kind(text):
    text = text.lower()
    if any(s in text for s in ('rate_limit', 'rate limit', 'usage limit', 'quota', 'out of extra usage',
                               'hit your limit', 'insufficient_quota', 'credit limit', 'credit balance',
                               'out of credits', 'too many requests', 'resource_exhausted')):
        return 'allowance'
    if any(s in text for s in ('unauthorized', 'authentication', 'not logged in', 'login required',
                               'invalid_api_key', 'oauth token', 'please log in')):
        return 'authentication'
    if any(s in text for s in ('permission_denied', 'permission denied', 'permission_denials')):
        return 'permission'
    return 'worker'


# A spent allowance says when it comes back; the manager waits that long instead of
# guessing. Never less than a quarter of an hour (a hint already past is no reason to
# hammer the CLI) and never more than a day, so a weekly limit still gets a daily
# probe and a misread hint can never park a provider for a week.
RESET_MIN, RESET_MAX = 900, 24 * 3600
_UNITS = {'s': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1,
          'm': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
          'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
          'd': 86400, 'day': 86400, 'days': 86400}
_MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun',
                                       'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
# Only a deadline the text itself offers counts, and only after a cue that says it is
# one. The cue keeps the parser off the timestamps a CLI dump is full of: codex's own
# "2026-09-29T23:55:51.221171Z ERROR ..." line sits in the same text as the
# "try again at Oct 5th, 2026 10:12 AM" that is the answer.
_CUE = r'(?:tr(?:y|ies) again|retry|reset[s]?|renews?|available again|come back|wait|until)'
_UNIT_RE = '|'.join(sorted(_UNITS, key=len, reverse=True))
_RELATIVE = re.compile(_CUE + r'[^.\n]{0,24}?\b(?:in|for)?\s*'
                       r'((?:\d+\s*(?:' + _UNIT_RE + r')\b[\s,]*(?:and\s+)?)+)', re.I)
_ANCHOR = _CUE + r'[^.\n]{0,16}?\b(?:at|on)?\s*'
_ISO = re.compile(_ANCHOR + r'(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?'
                  r'\s*(Z|[+-]\d{2}:?\d{2})?', re.I)
_DATE = re.compile(_ANCHOR + r'([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})'
                   r'(?:\s*,?\s*(?:at\s*)?(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:([AaPp])\.?[Mm]\.?)?)?', re.I)
_CLOCK = re.compile(_ANCHOR + r'(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:([AaPp])\.?[Mm]\.?)?', re.I)


def _hour(hour, meridiem):
    if not meridiem:
        return hour
    return hour % 12 + (12 if meridiem.lower() == 'p' else 0)


def _ahead(when, now):
    """Seconds from now to a local datetime, or None when it is already past."""
    delta = when.timestamp() - now
    return delta if delta > 0 else None


def _relative(text):
    m = _RELATIVE.search(text)
    if not m:
        return None
    total, seen = 0, False
    for count, unit in re.findall(r'(\d+)\s*([A-Za-z]+)', m[1]):
        unit = unit.lower()
        if unit not in _UNITS:
            break
        total += int(count) * _UNITS[unit]
        seen = True
    return total if seen else None


def _iso(text, now):
    m = _ISO.search(text)
    if not m:
        return None
    try:
        when = datetime.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6] or 0))
    except ValueError:
        return None
    zone = (m[7] or '').replace(':', '')
    if not zone:
        return _ahead(when.astimezone(), now)
    offset = 0 if zone.upper() == 'Z' else (1 if zone[0] == '+' else -1) * (
        int(zone[1:3]) * 3600 + int(zone[3:5]) * 60)
    return _ahead(when.replace(tzinfo=datetime.timezone(datetime.timedelta(seconds=offset))), now)


def _date(text, now):
    m = _DATE.search(text)
    month = _MONTHS.get(m[1][:3].lower()) if m else None
    if not month:
        return None
    try:
        when = datetime.datetime(int(m[3]), month, int(m[2]), _hour(int(m[4] or 0), m[6]), int(m[5] or 0))
    except ValueError:
        return None
    return _ahead(when.astimezone(), now)


def _clock(text, now):
    m = _CLOCK.search(text)
    if not m:
        return None
    hour, minute = _hour(int(m[1]), m[3]), int(m[2])
    if hour > 23 or minute > 59:
        return None
    # A bare clock is the next time it comes round, local.
    today = datetime.datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return _ahead(today, now) or _ahead(today + datetime.timedelta(days=1), now)


def reset_seconds(text, now=None):
    """How long to wait from the reset hint in a provider's own failure text, clamped to
    [RESET_MIN, RESET_MAX]; None when the text names no deadline. Deterministic: `now` is
    the epoch second the hint is read against."""
    now = time.time() if now is None else now
    for read in (_relative, _iso, _date, _clock):
        seconds = read(text) if read is _relative else read(text, now)
        if seconds is not None:
            return int(min(max(seconds, RESET_MIN), RESET_MAX))
    return None


def worker_main(rid, config):
    """This wrapper survives a supervisor restart; stdout and results are durable."""
    import psutil
    with fe_board.Board() as b:
        r = dict(b.q1('SELECT * FROM pm_runs WHERE id=?', rid))
        folder = run_dir(rid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'schema.json').write_text(json.dumps(schema_for(r['role'])), encoding='utf-8')
        with b.tx():
            if b.q1('SELECT state FROM pm_runs WHERE id=?', rid)[0] != 'queued':
                return 1
            b.con.execute("UPDATE pm_runs SET state='running',pid=?,pid_started=?,heartbeat=? WHERE id=?",
                          (os.getpid(), psutil.Process().create_time(), time.time(), rid))
        result, session, errors = None, r.get('session'), []
        # D218: what the run spends, as it spends it.
        import manager_models
        try:
            r['model'] = manager_models.started(b, r).get('model') or ''
        except Exception:
            r['model'] = ''
        meter, saved = manager_models.Meter(model=r['model']), [0.0]

        def save(ended=False):
            try:
                meter.save(b, rid, ended=ended)
                saved[0] = time.time()
            except Exception:
                pass  # accounting never stops the work
        try:
            if r['role'] not in ('worker', 'planner', 'quick'):
                # A reviewer or integrator queued before D188: the supervisor hands its
                # task back to the worker (Manager.completed).
                b.con.execute("UPDATE pm_runs SET state='finished',heartbeat=? WHERE id=?", (time.time(), rid))
                return 0
            log = (folder / 'events.jsonl').open('w', encoding='utf-8')

            def stream(cmd, text):
                nonlocal session, errors
                result = None
                p = subprocess.Popen(cmd, cwd=r['cwd'], env=clean_env(r),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
                p.stdin.write(text)
                p.stdin.close()
                reaped = []
                said = [time.time()]  # when the model last spoke or called a tool (D214)
                for line in p.stdout:
                    log.write(line)
                    log.flush()
                    b.con.execute('UPDATE pm_runs SET heartbeat=? WHERE id=?', (time.time(), rid))
                    try:
                        event = json.loads(line)
                    except ValueError:
                        errors.append(line[:500])
                        errors = errors[-20:]
                        continue
                    try:
                        meter.feed(event)
                        if event.get('type') == 'rate_limit_event':
                            manager_usage.record(b, 'claude', meter.usage)  # D243
                    except Exception:
                        pass  # accounting never stops the work
                    if event.get('type') == 'result' or time.time() - saved[0] > 15:
                        save()
                    session = event.get('thread_id') or event.get('session_id') or session
                    if session:
                        b.con.execute('UPDATE pm_runs SET session=? WHERE id=?', (session, rid))
                    if event.get('type') in ('assistant', 'user'):
                        said[0] = time.time()
                    if event.get('type') == 'result':
                        found = event.get('structured_output')
                        if not found and not event.get('is_error'):
                            try:
                                found = json.loads(event.get('result', ''))
                            except ValueError:
                                pass
                        # A resumed session can report an empty turn first (a background
                        # task's notification: no turns, no output) before the work starts;
                        # it must not replace a result or end the run (D214).
                        result = found or result
                        if event.get('is_error') or event.get('permission_denials'):
                            errors.append(json.dumps(event)[:2500])
                        if not reaped and ends_run(event):
                            reaped.append(bool(event.get('is_error')))
                            threading.Thread(target=reap_after_result, args=(p, reaped, None, said),
                                             daemon=True).start()
                    if event.get('type') in ('error', 'turn.failed'):
                        errors.append(json.dumps(event)[:2500])
                code = p.wait()
                p.stdout.close()
                save(ended=True)
                session_over(b, session)  # however it ended, no turn runs in it now (D214)
                if len(reaped) == 2:
                    # Ended by the reaper after its result: the exit code is ours, not the run's.
                    code = int(reaped[0])
                if r['provider'] == 'codex' and (folder / 'answer.json').exists():
                    result = json.loads((folder / 'answer.json').read_text(encoding='utf-8'))
                if code:
                    raise RuntimeError(f'exit {code}: ' + '\n'.join(errors)[-5000:])
                return result

            with log:
                result = (quick_result if r['role'] == 'quick' else validate_result)(stream(command(r, config, folder), r['prompt']))
                # D259: a worker at a phase boundary asks for a compaction; the session goes on
                # smaller, with no board round-trip. Codex compacts itself, so it only resumes.
                for _ in range(COMPACT_MAX):
                    if not (r['role'] == 'worker' and result['status'] == 'compact' and session):
                        break
                    r['session'] = session
                    if r['provider'] == 'claude':
                        stream(compact_command(r, config, result['summary']), '')
                    result = validate_result(stream(command(r, config, folder), carry_on(result['summary'])))
                if result['status'] == 'compact':
                    result = dict(result, status='continue')
            if r['role'] == 'worker':
                result = land(r, result, heartbeat=lambda: b.con.execute(
                    'UPDATE pm_runs SET heartbeat=? WHERE id=?', (time.time(), rid)))
            # Only a run still running: one the supervisor ended past its hours (D201) is handled.
            b.con.execute("UPDATE pm_runs SET state='finished',result=?,session=?,heartbeat=? "
                          "WHERE id=? AND state='running'", (json.dumps(result), session, time.time(), rid))
        except Exception as e:
            b.con.execute("UPDATE pm_runs SET state='failed',error=?,session=?,heartbeat=? "
                          "WHERE id=? AND state='running'",
                          (str(e) + '\n' + '\n'.join(errors)[-5000:], session, time.time(), rid))
            return 1
    return 0


_LAST_WORDS = {}  # rid -> (log size, words): the board asks every 2 s


def last_words(rid, chunk=400_000, most=8_000_000):
    """The run's last message in its own words (/status's latest, D218), read back from
    the end of its log a chunk at a time: a worker can run many tool calls between two
    messages. Empty when its last `most` bytes hold none."""
    try:
        size = (run_dir(rid) / 'events.jsonl').stat().st_size
    except OSError:
        return ''
    if _LAST_WORDS.get(rid, (None,))[0] == size:
        return _LAST_WORDS[rid][1]
    words = _last_words(rid, chunk, most)
    _LAST_WORDS[rid] = (size, words)
    return words


def _last_words(rid, chunk, most):
    try:
        with (run_dir(rid) / 'events.jsonl').open('rb') as f:
            f.seek(0, 2)
            end = f.tell()
            tail = b''
            while end > 0 and len(tail) < most:
                start = max(0, end - chunk)
                f.seek(start)
                tail = f.read(end - start) + tail
                end = start
                lines = tail.decode('utf-8', 'replace').splitlines()
                for line in reversed(lines[1:] if start else lines):
                    if '"text"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get('type') == 'item.completed' and (e.get('item') or {}).get('type') == 'agent_message':
                        return ' '.join(str(e['item'].get('text') or '').split())
                    msg = e.get('message') if e.get('type') == 'assistant' else None
                    texts = [c.get('text', '') for c in (msg or {}).get('content') or []
                             if isinstance(c, dict) and c.get('type') == 'text' and c.get('text', '').strip()]
                    if texts:
                        return ' '.join(texts[-1].split())
    except OSError:
        pass
    return ''


def last_activity(rid, n=12, tail=400_000):
    """The run's last few steps from its log (D201): what it said and what it ran,
    for Claude's stream-json and Codex's --json alike. A tail too short for n steps
    (one big tool result can fill 400 KB) is read again four times longer, to 8 MB."""
    try:
        with (run_dir(rid) / 'events.jsonl').open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail))
            lines = f.read().decode('utf-8', 'replace').splitlines()[1 if size > tail else 0:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get('message') if isinstance(e.get('message'), dict) else {}
        content = msg.get('content') if isinstance(msg.get('content'), list) else []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'text' and e.get('type') == 'assistant' and c.get('text', '').strip():
                out.append('said: ' + c['text'].strip())
            elif c.get('type') == 'tool_use':
                inp = c.get('input') or {}
                # The call's own description reads better than a heredoc's first line (D210).
                out.append('ran: ' + str(inp.get('description') or inp.get('command') or inp.get('file_path')
                                         or inp.get('pattern') or c.get('name')))
        item = e.get('item') or {}
        if e.get('type') == 'item.completed':
            if item.get('type') == 'agent_message' and item.get('text'):
                out.append('said: ' + item['text'].strip())
            elif item.get('command'):
                out.append('ran: ' + str(item['command']))
    if len(out) < n and size > tail and tail < 8_000_000:
        return last_activity(rid, n, tail * 4)
    return [' '.join(x.split())[:300] for x in out[-n:]]


def owned_process(run):
    """PID alone is unsafe after restart: match birth time and wrapper command."""
    import psutil
    try:
        p = psutil.Process(run['pid'])
        if abs(p.create_time() - run['pid_started']) > .01:
            return None
        args = p.cmdline()
        return p if '_worker' in args and run['id'] in args else None
    except (psutil.Error, TypeError):
        return None


def end_tree(p):
    """Terminate a psutil process and everything under it, children first."""
    import psutil
    children = p.children(recursive=True)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        p.terminate()
    except psutil.Error:
        pass
    _, live = psutil.wait_procs(children + [p], timeout=3)
    for child in live:
        try:
            child.kill()
        except psutil.Error:
            pass


def ends_run(event):
    """Whether a `result` event is the run's end: one carrying the answer, an error, or
    any turn of work. A resumed session first reports the empty turn a leftover
    background task's notification made (`num_turns` 0, no result, no output); taken
    for the end, it had T39's worker killed a minute into its work (D214)."""
    return bool(event.get('structured_output') or event.get('is_error') or event.get('num_turns')
                or str(event.get('result') or '').strip())


def reap_after_result(p, reaped, grace=None, said=None):
    """claude -p stays alive after its result while anything its session started in
    the background still runs (T29: two `tail -f` monitors), and the wrapper waited on
    its stdout for good. The result is the run's end: past the grace, end the tree.
    `said` ([time]) is when the model last spoke: while it still works past its result,
    the grace counts from there, so a live worker is never ended (D214).
    `reaped` gains a second entry when this had to."""
    import psutil
    grace = RESULT_GRACE if grace is None else grace
    start = time.time()
    while True:
        last = max(start, said[0]) if said else start
        try:
            p.wait(timeout=max(0.05, last + grace - time.time()))
            return
        except subprocess.TimeoutExpired:
            pass
        if not said or time.time() - said[0] >= grace:
            break
    try:
        root = psutil.Process(p.pid)
    except psutil.Error:
        return
    reaped.append(True)
    end_tree(root)


def session_over(b, session):
    """D214: a run's CLI session has no turn running once its process is gone. Ended from
    outside (the reaper, a stop, the hours' limit) its Stop hook never ran, so its row
    stayed `working` and its checkout read busy for WORKING_FOR: T39's retry could not
    start in A3 because its own killed session held it."""
    if b is not None and session:
        b.session_end(session)


def stop_owned(run, b=None):
    p = owned_process(run)
    if p:
        end_tree(p)
        session = run.get('session')
        if b is not None:
            row = b.q1('SELECT session FROM pm_runs WHERE id=?', run['id'])
            session = (row and row[0]) or session
        session_over(b, session)
