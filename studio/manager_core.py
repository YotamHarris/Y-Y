"""Event-driven orchestration. Model output proposes work; code owns transitions."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

import fe_board
import fe_sync
import manager_brief as brief
import manager_context as context
import manager_models as models
import manager_nightly as nightly
import manager_park as park
import manager_plan_docs as plan_docs
import manager_quick as quick
import manager_release as release
import manager_report as report
import manager_store as store
import manager_usage as usage
import manager_talk as talk
import manager_workers as workers
import studio_config

# D224: the rules are the project's own, not the manager's. A run reads them where Yotam
# and every other session do: AGENTS.md (CLAUDE.md imports it, D252) and the task skill. The prompt is the
# work itself: the task and its Why for a worker, the goal for the planner.
SKILL = studio_config.get('prompts.task_skill', '.agents/skills/task/SKILL.md')
USAGE_REPORT = 'usage_report'  # pm_settings: the day of the last morning usage report (D253)
RULES = (f"You run under {studio_config.name()}'s background manager (D181). Read {SKILL} (the task skill) "
         'first and follow it: it says how a task is written, worked, asked about and reported. '
         'Return only the structured result its Reporting section describes, with empty arrays and '
         'strings for fields you do not use.\n')

# D188: a task that was waiting for a reviewer or the integrator goes back to its worker.
LANDS_ITSELF = ('The reviewer and integrator roles are gone (D188): you review your own work and land '
                f'it. Rebase onto origin/main with `{studio_config.tool_cmd("fe_sync.py")} push`, resolve any '
                'conflict, revalidate what it touches, and return complete once HEAD is on origin/main.')

# Consecutive 'continue' results (unfinished work, nothing for Yotam to decide) the
# manager resumes in silence. A worker that never finishes must still reach him.
CONTINUE_SILENCE = 6
# A run's artifacts carried to Discord (D304); the transport sends ten to a message and the rest after.
MAX_ARTIFACTS = 30
TEXT_ARTIFACTS = ('.md', '.txt', '.log')

# Consecutive spent allowances a task or goal may lose in silence. One that never
# clears must still reach Yotam rather than re-dispatching for ever.
ALLOWANCE_SILENCE = 4

# How long a provider waits when its own text gives no reset time, one entry per block
# in a row, holding at the last. A ChatGPT weekly limit outlasts any single wait, so
# the manager backs off instead of burning a run every half hour to learn that again.
ALLOWANCE_BACKOFF = (1800, 2 * 3600, 6 * 3600, 24 * 3600)
# Only a fresh login clears an authentication failure; nothing is gained by retrying.
AUTH_BLOCK = 365 * 86400
# D238: a session belongs to one CLI, so a run that changes CLI starts fresh and says so.
NEW_SESSION = ('The previous run of this task was on the {old} CLI and this one is on {new}: '
               'a session does not cross CLIs, so none of that conversation is carried over. '
               'Read the task, the board thread and the checkout (git status, git log, git diff) '
               'before you continue the work.')
FOLLOW_UP = '\nFollow-up context:\n'  # a worker's prompt ends with it (Manager.prompt)


def given(r):
    """The follow-up context a run's prompt carried: Yotam's replies and earlier runs'
    notes, which a failed run must not take away with it (D214)."""
    p = r.get('prompt') or ''
    i = p.rfind(FOLLOW_UP)
    return p[i + len(FOLLOW_UP):].strip() if i >= 0 else ''


def worker_notes(**fill):
    """Worker prompt notes from an inline table or a file's `## name` sections."""
    path = studio_config.get('prompts.worker_notes')
    try:
        text = (''.join(f'## {name}\n{body}\n' for name, body in path.items()) if isinstance(path, dict)
                else (studio_config.repo_root() / path).read_text(encoding='utf-8') if path else '')
    except OSError:
        return {}
    notes = {}
    for part in re.split(r'^## +', text, flags=re.M)[1:]:
        name, _, body = part.partition('\n')
        body = body.strip()
        for k, v in fill.items():
            body = body.replace('{' + k + '}', str(v))
        notes[name.strip()] = body
    return notes


def board_marks(files):
    """The board images among FILES as markdown to end a board message with (D304), so the
    board's thread shows what Discord's does."""
    marks = [f'![{Path(f).stem}](/files/{Path(f).name})' for f in files
             if Path(f).parent == fe_board.files_dir() and re.fullmatch(fe_board.FILE_NAME, Path(f).name)]
    return ''.join('\n\n' + m for m in marks)


class Manager:
    def __init__(self, board, config):
        self.b, self.config = board, config
        self.reg = fe_board.registry()
        self.root = Path(config['repo'])
        plan_docs.initialize(board)

    def available(self):
        """Every provider configured and not waiting out a block, claude first."""
        return store.available(self.b, self.config)

    def provider(self, preferred='claude'):
        # Claude is the default for every role, on the model manager_models picks (D218);
        # Codex is only a fallback when Claude is missing or blocked.
        free = self.available()
        return preferred if preferred in free else (free[0] if free else None)

    def provider_for(self, tier, pinned):
        """The CLI a task's tier runs on (D238), or None while it cannot run. A tier Yotam
        pinned waits for its own CLI: a pin is his choice of model, and running the task on
        another one quietly is worse than waiting out the block (the wait is on the board,
        the Manager tab and `fe_manager.py status`). A tier the policy chose falls back to
        whatever is free, at that CLI's own default model, as before D238."""
        free = self.available()
        want = models.provider_of(tier)
        if want in free:
            return want
        return None if pinned else (free[0] if free else None)

    def cap(self):
        return store.slots(self.available())

    def prompt(self, role, tid=None, goal=None, feedback='', gathered=False, root=None):
        """`gathered`: a worker in a new session also gets the brief (D253), what its task names
        read for it; a resumed session has it already."""
        text, who = RULES, studio_config.owner()
        if role == 'planner':
            g = self.b.q1('SELECT * FROM pm_goals WHERE id=?', goal)
            text += (f'\nYou are the planner, in plan mode with {who} in a Discord thread, as in a Claude '
                     f'Code session (D226), its tasks few and whole: follow the task skill\'s "Planning with {who} first", "His words '
                     'are his" and "Writing a task". Read the summaries and the code you need; edit nothing. '
                     'Each turn returns chat or planned. chat: your reply in summary, shown to him in full as '
                     'your message (plain prose, as in a session), and your questions in asks, each with its '
                     'options. planned: once you and he agree what to build, the tasks (reasoning is the Why '
                     'with its Approach line, body is What to do, depends holds zero-based indices of earlier '
                     'tasks), and summary says the plan in a few lines; he approves it with one button, which '
                     'creates and starts them. Name the goal in goal_name, two to five plain words '
                     f'("{studio_config.get("prompts.goal_name_example", "Faster nightly checks")}").\n'
                     + models.planner_brief(self.b) +
                     'His message:\n' + g['body'])
            text += '\nOther goals (context, avoid duplicate work):\n' + json.dumps([
                dict(x) for x in self.b.q('SELECT id,name,body,status FROM pm_goals WHERE id<>? '
                                          'ORDER BY id DESC LIMIT 10', goal)])
        else:
            t = self.b.task(tid)
            m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
            # The task as Yotam approved it: its Why is what the tests must show (D195).
            # The game's own lines (its GPU lease, its nightly bench) come from [prompts] worker_notes.
            notes = worker_notes(python=workers.PYTHON, fe_manager=release.tool('fe_manager.py'))
            text += (f'\nWork board task T{tid} by the task skill, to the end.\n\n'
                     f'# T{tid}: {t["title"]}  ({store.goal_label(self.b, m["goal_id"])})\n\n'
                     f'{t["body"].strip()}\n\n'
                     + (context.brief(t['body'], Path(root) if root else context.ROOT) if gathered else '') +
                     f'Here: checkout {m["agent"] or "?"}' + (f'; {notes["gpu"]}' if notes.get('gpu') else '.') + '\n')
            text += (f'Tools are in "{release.tools()}". '
                     'Never push: commit and return complete; the manager runs fe_land.py and '
                     'resumes you if a gate stops it (D253).'
                     + (' ' + notes['bench'] if notes.get('bench') else '') + '\n')
            text += FOLLOW_UP + feedback
        return text

    def input_events(self):
        b = self.b
        for ev in b.q("SELECT * FROM pm_inbox WHERE state='pending' ORDER BY created"):
            try:
                with b.tx():
                    kind, tid = ev['kind'], ev['task_id']
                    if kind in ('start', 'pause', 'resume', 'stop'):
                        store.control(b, kind)
                    elif kind == 'accept':
                        current = b.q1('SELECT coalesce(landed_head, head) FROM pm_tasks WHERE task_id=?', tid)
                        if ev['body'] and (not current or not current[0] or not current[0].startswith(ev['body'])):
                            raise ValueError('acceptance refers to an older commit; use the latest review')
                        store.accept(b, tid)
                        store.notify(b, 'accept:' + ev['id'], f'T{tid} accepted. Dependent tasks may now start.', tid)
                    elif kind in ('approve', 'reason', 'drop'):
                        self.decide(ev)
                    elif kind == 'ask':
                        quick.hear(b, ev['body'], tid)  # the talk channel or /ask: an answer, never a change (D317, D322)
                    elif tid and b.q1("SELECT 1 FROM pm_tasks m JOIN tasks t ON t.id=m.task_id "
                                      "WHERE m.task_id=? AND t.status='idea'", tid):
                        # A reply to a proposal is added to its reasoning (D195).
                        t = b.task(tid)
                        why = (store.reasoning(t['body']) + f'\n\n{studio_config.owner()}: ' + ev['body'].strip()).strip()
                        b.update_task('owner', tid, body=store.with_reasoning(t['body'], why))
                        b.add_message('owner', 'manager', f'T{tid}: owner reply', ev['body'], topic=f'T{tid}')
                        brief.propose(b, 'proposal:' + ev['id'], tid)
                    elif tid:
                        m = b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid)
                        if not m:
                            raise ValueError('unknown managed task')
                        b.add_message('owner', 'manager', f'T{tid}: owner reply', ev['body'], topic=f'T{tid}')
                        # Queue steering until the current worker has exited; never run two writers.
                        b.con.execute('UPDATE pm_tasks SET feedback=feedback || ? WHERE task_id=?',
                                      ('\nOwner: ' + ev['body'], tid))
                        if m['phase'] in ('blocked', 'failed', 'landed'):
                            b.con.execute("UPDATE pm_tasks SET phase='revise',attempts=0 WHERE task_id=?", (tid,))
                            # claimed, not in progress: nothing works on it until its worker is queued (D214)
                            b.update_task('manager', tid, status='claimed')
                            # Said, so a reopened task is not mistaken for one waiting on him (D212).
                            store.notify(b, 'reopen:' + ev['id'], f'T{tid} goes back to its worker with your '
                                         f'reply; it starts when a run slot and {m["agent"] or "a checkout"} '
                                         'are free. /status shows where it is.', tid)
                    elif kind == 'plan':
                        talk.approve(b, talk.goal_of_event(b, ev['id']), ev['body'])  # Approve plan (D226)
                    elif talk.goal_of_event(b, ev['id']):
                        talk.hear(b, talk.goal_of_event(b, ev['id']), ev['body'])  # in its planning thread
                    else:
                        talk.start(b, ev['id'], ev['body'])  # a new conversation in its own thread (D226)
                    b.con.execute("UPDATE pm_inbox SET state='handled' WHERE id=?", (ev['id'],))
            except (ValueError, fe_board.BoardError) as e:
                with b.tx():
                    b.con.execute("UPDATE pm_inbox SET state='rejected' WHERE id=?", (ev['id'],))
                    store.notify(b, 'input-error:' + ev['id'], str(e), ev['task_id'], True)

    def decide(self, ev):
        """Yotam's call on a proposed task (D195): approve it, rewrite why it is done, or
        drop it. Only a proposal, an idea on the board, takes one."""
        b, tid, kind = self.b, ev['task_id'], ev['kind']
        if not tid or not b.q1('SELECT 1 FROM pm_tasks WHERE task_id=?', tid):
            raise ValueError('unknown managed task')
        t = b.task(tid)
        if t['status'] != 'idea':
            raise ValueError(f'T{tid} is no longer a proposal: it is {t["status"]} on the board.')
        if kind == 'approve':
            b.update_task('owner', tid, status='ready')
            wait = [f'T{d}' for d in t['depends'] if b.task(d)['status'] not in ('done', 'dropped')]
            store.notify(b, 'approve:' + ev['id'], f'T{tid} approved. It starts when a checkout is free'
                         + (f' and {", ".join(wait)} {"is" if len(wait) == 1 else "are"} done.'
                            if wait else '.'), tid)
        elif kind == 'drop':
            b.update_task('owner', tid, status='dropped')
        else:
            if not ev['body'].strip():
                raise ValueError('the reasoning cannot be empty')
            b.update_task('owner', tid, body=store.with_reasoning(t['body'], ev['body']))
            brief.propose(b, 'proposal:' + ev['id'], tid)

    def mirror_board(self):
        b = self.b
        cursor = int(store.setting(b, 'board_cursor', '0'))
        for m in b.q('SELECT * FROM messages WHERE id>? ORDER BY id', cursor):
            topic = m['topic'] or ''
            tid = int(topic[1:]) if topic.startswith('T') and topic[1:].isdigit() else None
            managed = tid and b.q1('SELECT 1 FROM pm_tasks WHERE task_id=?', tid)
            if managed:
                # Own mirrored owner replies have already been processed above.
                if m['sender'] == 'owner' and m['subject'] != f'T{tid}: owner reply':
                    store.receive(b, f'board:{m["id"]}', 'reply', m['body'], tid)
                elif m['sender'] not in ('owner', 'manager'):
                    store.notify(b, f'board:{m["id"]}', m['subject'] + '\n' + m['body'], tid,
                                 m['kind'] in ('question', 'blocker'))
            store.set_setting(b, 'board_cursor', m['id'])

    def fail(self, r, error, provider_error=False):
        """provider_error: the text is the CLI's own exit output, not a message the
        manager wrote. Only that may pause a provider or buy a free retry; a manager
        message quotes the model's prose, which would otherwise let a review rejection
        that merely mentions a credit limit re-dispatch itself for ever."""
        b = self.b
        kind = workers.failure_kind(error)
        if r['role'] == 'quick':
            with b.tx():  # D317: said in the talk channel; nothing else waits on it
                b.con.execute("UPDATE pm_runs SET state='handled',error=? WHERE id=?", (error[:6000], r['id']))
                if provider_error and kind in ('allowance', 'authentication'):
                    self.block_provider(r['provider'], kind, error)
                quick.failed(b, r, error, kind if provider_error else '')
            return
        with b.tx():
            b.con.execute("UPDATE pm_runs SET state='handled',error=? WHERE id=?", (error[:6000], r['id']))
            if provider_error and kind in ('allowance', 'authentication'):
                self.block_provider(r['provider'], kind, error)
            # A spent allowance says nothing about the work and needs nothing from the
            # owner: keep the attempt budget and let the next schedule() re-dispatch on
            # the other provider, without a Discord message.
            quiet = provider_error and kind == 'allowance' and self.allowance_streak(r) <= ALLOWANCE_SILENCE
            # D238: a task pinned to a tier of this CLI does not move to the other one; it waits.
            route = models.routes(b, [r['task_id']]).get(r['task_id']) if r['task_id'] else None
            pinned = (route or {}).get('owner_tier') or ''
            held = bool(pinned) and models.provider_of(pinned) == r['provider']
            if r['task_id']:
                t = b.q1('SELECT * FROM pm_tasks WHERE task_id=?', r['task_id'])
                # What the failed run was told (Yotam's replies among it) and anything he sent
                # while it ran go on to the next run; the bare error used to replace them (D214).
                kept = '\n'.join(x for x in (given(r), t['feedback'].strip()) if x)
                # D218, D240: a Sonnet run that fails on the work is a strong model's to retry, at no attempt's cost.
                escalated = not (provider_error and kind in ('allowance', 'authentication')) and \
                    kind != 'permission' and r['role'] == 'worker' and \
                    models.run_tier(b, r['id']) == 'sonnet' and \
                    models.escalate(b, r['task_id'], 'its run failed: ' + ' '.join(error.split())[:160])
                if quiet:
                    attempts, phase, feedback = t['attempts'], 'revise', self.carry_over(kept, r['provider'])
                elif escalated:
                    attempts, phase = t['attempts'], 'revise'
                    feedback = ((kept + '\n\n') if kept else '') + 'The last run (on Sonnet) failed; you are ' \
                        f'{models.label(escalated)} taking the task over from it: ' + error[:max(500, 5000 - len(kept))]
                else:
                    attempts = t['attempts'] + 1
                    phase = 'failed' if attempts >= 2 or kind == 'permission' else 'revise'
                    feedback = ((kept + '\n\nThe last run failed: ') if kept else '') + error[:max(500, 6000 - len(kept))]
                b.con.execute('UPDATE pm_tasks SET attempts=?,phase=?,feedback=?,worker_session=COALESCE(?,worker_session) '
                              'WHERE task_id=?', (attempts, phase, feedback,
                                                   r.get('session') if r['role'] == 'worker' else None, r['task_id']))
                if phase == 'failed':
                    b.update_task('manager', r['task_id'], status='blocked')
                elif b.task(r['task_id'])['status'] == 'in_progress':
                    b.update_task('manager', r['task_id'], status='claimed')  # waits to try again (D214)
            elif r['goal_id']:
                # An exhausted allowance is not a planning error; it must never block a goal.
                # Safe to read from the text: a planner run fails either on the CLI's own
                # output or on one of the manager's fixed strings, never on model prose.
                count = sum(1 for e in b.q('SELECT error FROM pm_runs WHERE goal_id=? AND error IS NOT NULL',
                                           r['goal_id']) if workers.failure_kind(e[0]) != 'allowance')
                if count >= 2 or kind == 'permission':
                    # D226: a conversation waits for Yotam; his next reply in its thread tries again
                    b.con.execute('UPDATE pm_goals SET status=? WHERE id=?',
                                  ('talking' if talk.thread_conversation(b, r['goal_id']) else 'blocked', r['goal_id']))
            if quiet:
                # Durable without Discord: the pm_providers row plus the board event feed.
                b.event('manager', 'manager.allowance', f'T{r["task_id"]}' if r['task_id'] else None,
                        f'{r["provider"]} allowance exhausted; '
                        + (f'it is pinned to {models.label(pinned)} and waits for it.' if held
                           else 'retrying on the other provider.'))
            else:
                bottom = brief.cause(error, r['provider'], kind)
                said = brief.run_result(r).get('bottom_line', '').strip()
                bottom += (' ' + said) if said else ''
                asks, ping = [], False
                if provider_error and kind == 'authentication':
                    asks, ping = [f'Sign the {r["provider"]} CLI back in on the PC, then send /resume.'], True
                elif r['task_id'] and escalated:
                    bottom += f' Sonnet could not finish it, so {models.label(escalated)} takes it over from where it stopped.'
                elif r['task_id'] and phase == 'revise':
                    bottom += f' It is trying again by itself (attempt {attempts + 1} of 2).'
                elif r['task_id']:
                    bottom += (' It was refused a permission, so it has stopped.' if kind == 'permission'
                               else f' It has failed {attempts} times, so it has stopped.')
                    asks, ping = ['Reply here with what should change and it runs again, or drop it on the board.'], True
                elif b.q1("SELECT 1 FROM pm_goals WHERE id=? AND status IN ('blocked','talking')", r['goal_id']):
                    bottom += ' Planning has stopped.'
                    asks, ping = ['Reply here with the goal reworded or narrowed, and it plans again.'], True
                talking = not r['task_id'] and talk.thread_conversation(b, r['goal_id'])
                if talking:
                    asks = asks or ['Reply here to try again.']
                brief.post(b, 'failure:' + r['id'], r['task_id'], bottom, asks, report=error, ping=ping,
                           session=r.get('session'), cwd=r['cwd'], goal=r['goal_id'] if talking else None)

    def block_provider(self, provider, kind, error, now=None):
        """Stop dispatching to a provider until it can serve again, and say when that is.
        Its own text is believed first (a ChatGPT limit names the hour it comes back);
        with no hint the wait escalates with each block in a row, so a weekly limit is
        not probed every half hour. A run the provider carries through clears the row,
        and so does the owner's resume."""
        now = time.time() if now is None else now
        row = self.b.q1("SELECT blocks FROM pm_providers WHERE name=? AND reason='allowance'", provider)
        blocks = (row[0] if row else 0) + 1
        if kind != 'allowance':
            blocks, window = 0, AUTH_BLOCK
        else:
            window = workers.reset_seconds(error, now) or ALLOWANCE_BACKOFF[
                min(blocks, len(ALLOWANCE_BACKOFF)) - 1]
        store.block_provider(self.b, provider, kind, now + window, blocks, window)
        return window

    def allowance_streak(self, r):
        """Runs in a row this task (or, for a planner, this goal) has lost to a spent
        allowance, the one being recorded included. Any other outcome ends the streak."""
        column, value = ('task_id', r['task_id']) if r['task_id'] else ('goal_id', r['goal_id'])
        if not value:
            return 1
        rows = self.b.q(f"SELECT error FROM pm_runs WHERE {column}=? AND state='handled'"
                        + ('' if r['task_id'] else ' AND task_id IS NULL') + ' ORDER BY created DESC', value)
        streak = 0
        for row in rows:
            if not row[0] or workers.failure_kind(row[0]) != 'allowance':
                break
            streak += 1
        return streak

    @staticmethod
    def carry_over(feedback, provider, note=None):
        """The next worker needs the state of the work, not the CLI dump (that stays in pm_runs.error).
        `note` says what else it must know: by default that an allowance ran out, and for a run
        that changes CLI, that nothing of the previous conversation comes with it (D238)."""
        note = note or (f'The previous run stopped when {provider} ran out of allowance; '
                        'the work is unchanged, continue the task.')
        return feedback if note in feedback else (feedback + '\n' + note).strip()[-6000:]

    def continues(self, r):
        """How many of this task's latest runs in r's role, r included, stopped with continue."""
        n = 0
        for row in self.b.q('SELECT result FROM pm_runs WHERE task_id=? AND role=? AND created<=? '
                            'ORDER BY created DESC', r['task_id'], r['role'], r['created']):
            try:
                if json.loads(row[0] or '{}').get('status') != 'continue':
                    break
            except ValueError:
                break
            n += 1
        return n

    def completed(self, r):
        b = self.b
        if r['role'] == 'quick':
            result = workers.validate_result(json.loads(r['result']))
            with b.tx():
                store.clear_provider(b, r['provider'])
                quick.answered(b, r, result)  # D317, D322: its answer, where he asked
                b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))
            return
        if r['role'] not in ('planner', 'worker'):
            # A reviewer or integrator run from before D188: its worker finishes the task.
            with b.tx():
                b.con.execute("UPDATE pm_tasks SET phase='revise',feedback=? WHERE task_id=? AND phase<>'landed'",
                              (LANDS_ITSELF, r['task_id']))
                b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))
            return
        raw_result = json.loads(r['result'])
        if r['role'] == 'worker' and isinstance(raw_result, dict) and 'plan_document' not in raw_result:
            raw_result['plan_document'] = plan_docs.document(b.task(r['task_id']), raw_result)
        result = workers.validate_result(raw_result)
        # The provider delivered a whole run: whatever the work says, its allowance is
        # back and the next block starts from the shortest wait again.
        store.clear_provider(b, r['provider'])
        if r['role'] == 'planner' and talk.thread_conversation(b, r['goal_id']):
            files, unsent = self.shown(r, result)
            with b.tx():
                store.name_goal(b, r['goal_id'], result['goal_name'], keep=True)
                talk.answered(b, r, result, files, unsent)  # D226: its reply or its plan, in the goal's thread
                b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))
            return
        if r['role'] == 'planner':
            files, unsent = self.shown(r, result)
            with b.tx():
                store.name_goal(b, r['goal_id'], result['goal_name'], keep=True)
                if result['status'] == 'planned':
                    store.plan_tasks(b, r['goal_id'], result['tasks'])
                else:
                    b.con.execute('UPDATE pm_goals SET status=? WHERE id=?',
                                  ('blocked' if result['status'] in ('blocked', 'continue') else 'answered',
                                   r['goal_id']))
                asks = result['asks'] or (['Reply here with your answer.']
                                          if result['status'] in ('blocked', 'continue') else [])
                bottom = brief.bottom_line(result)
                if result['status'] == 'planned':
                    n = len(result['tasks'])
                    bottom += (f' I propose {n} task{"s" * (n != 1)} for {store.goal_label(b, r["goal_id"])}, '
                               'each in its own thread. Nothing starts until you approve it there.')
                    asks = list(asks) + ['Open each task\'s thread: approve it, edit its reasoning, or drop it.']
                brief.post(b, 'result:' + r['id'], None, bottom, asks,
                           report=result['summary'], ping=result['status'] in ('blocked', 'continue'),
                           problem_text=result['problem'], files=files, more=unsent)
                b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))
            return
        tid = r['task_id']
        m = dict(b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        with b.tx():
            brief.remember_problem(b, tid, result['problem'])
        if result['status'] not in ('blocked', 'continue'):
            # Short of landed, the work is unfinished, not failed: the same session goes on
            # (D188). Only a question, a crash or a provider error stops it.
            path = Path(r['cwd'])
            head = fe_sync.git_out(path, 'rev-parse', 'HEAD')
            problem = self.unlanded(result, head, path, r['agent'])
            if not problem:
                self.land(m, r, head, result)
                return
            result = dict(result, status='continue', summary=problem + '\nYour note:\n' + result['summary'])
            b.con.execute('UPDATE pm_runs SET result=? WHERE id=?', (json.dumps(result), r['id']))
        if result['status'] == 'continue' and self.continues(r) < CONTINUE_SILENCE:
            # Unfinished, nothing for Yotam to decide: the wrapper has already outlived every
            # process the run started, so resume the same session.
            with b.tx():
                # D218, D240: Sonnet stopping unfinished twice in a row hands the task to a strong model.
                if models.run_tier(b, r['id']) == 'sonnet' and self.continues(r) >= models.SONNET_CONTINUES:
                    models.escalate(b, tid, f'it stopped unfinished {self.continues(r)} times in a row')
                b.con.execute("UPDATE pm_tasks SET phase='revise',feedback=?,worker_provider=?,worker_session=? "
                              'WHERE task_id=?', ('Continue where you stopped; every process your previous '
                                                  'turn started has exited. Your own note:\n' + result['summary'],
                                                  r['provider'], r['session'], tid))
                b.event('manager', 'manager.continue', f'T{tid}', 'worker stopped unfinished; resuming.')
                b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))
            return
        if result['status'] in ('blocked', 'continue'):
            bottom, asks = brief.bottom_line(result), result['asks'] or [
                'Reply here with your answer; the full question is in the attached report.']
            if result['status'] == 'continue':
                bottom = f'It stopped unfinished {CONTINUE_SILENCE} times in a row without asking anything.'
                asks = ['Reply "carry on" to let it continue, or tell it what to change.']
                result['summary'] = (f'T{tid} stopped unfinished {CONTINUE_SILENCE} times in a row without '
                                     'a question; it needs a look. Last note:\n' + result['summary'])
            if r['role'] == 'worker' and store.task_clock(b, tid)[0] >= store.TASK_HOURS * 3600:
                # D321: past its hours the manager says what blocked it, from the logs; the worker's note goes below.
                result['summary'] = report.blocked_report(b, tid, r, ended_by_manager=False,
                                                          agent_note=result['summary'])
                bottom = f'T{tid} ran past its {store.TASK_HOURS} hours and is blocked; what held it up is in the report.'
                if not result['asks']:
                    asks = ['Reply "carry on" for another 3 hours, tell it what to change, or drop it.']
            files, unsent = self.shown(r, result)  # D304: the concepts to choose between go with the question
            with b.tx():
                b.con.execute("UPDATE pm_tasks SET phase='blocked',feedback=?,worker_session=CASE WHEN ?='worker' THEN ? "
                              'ELSE worker_session END WHERE task_id=?',
                              ('Question: ' + result['summary'], r['role'], r['session'], tid))
                b.update_task('manager', tid, status='blocked')
                b.add_message('manager', 'owner', f'T{tid}: decision needed',
                              result['summary'] + board_marks(files), kind='question', topic=f'T{tid}')
                brief.post(b, 'question:' + r['id'], tid, bottom, asks, report=result['summary'], ping=True,
                           files=files, more=unsent, session=r['session'], cwd=r['cwd'])
                b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))
            return

    @staticmethod
    def unlanded(result, head, path, agent):
        """Why a worker's result is not yet a landed task, or None when it is."""
        if result['status'] != 'complete':
            return f'You returned {result["status"]}, which is not a worker result: finish and land the task.'
        if fe_sync.git_out(path, 'status', '--porcelain'):
            return 'The tree is not clean: commit or discard what is left, then land it.'
        if not workers.evidence_passes(result, head):
            return (f'The evidence does not pass for HEAD {head[:12]}: every check needs passed or '
                    'not_applicable, tests passed, and head must be this commit.')
        if not fe_board.on_trunk(head, agent):
            return (f'HEAD {head[:12]} is not on origin/main and the manager could not land it (D253): '
                    f'land it with `{studio_config.tool_cmd("fe_land.py")}` (resolve any rebase conflict and revalidate).')
        return None

    def land(self, m, r, head, result):
        """The worker pushed it: hand the task to Yotam to test and accept."""
        b, tid = self.b, m['task_id']
        path = Path(r['cwd'])
        files, unsent = self.shown(r, result)
        marks = board_marks(files)
        # D212: a document it wrote to be read (a plan, a spec) goes with the message, as it is at its commit
        docs = fe_board.docs_of(path, head, result['artifacts'])
        files = [f for f in files if Path(f).name not in {Path(d['path']).name for d in docs}]
        files += [brief.attach(Path(d['path']).name, d['text']) for d in docs]
        doc_diff = fe_sync.git_out(path, 'diff', '--unified=0', f'{m["base"]}..{head}',
                                   '--', studio_config.get('docs.decisions_dir', 'docs/decisions'),
                                   studio_config.get('docs.roadmap', 'docs/roadmap.md')) if m['base'] else ''
        refs = re.findall(r'^\+#{2,4}\s+([DV]\d+)\b', doc_diff, re.M)
        spent = models.task_stats(b, tid)
        is_plan = plan_docs.document(b.task(tid), result)
        acceptance = ('Accept to approve this plan and start its implementation tasks, or request changes.'
                      if is_plan else 'Accept to unlock dependent tasks, or request changes.')
        import manager_crew
        crew = manager_crew.landed_report(b.task(tid)['title'], tid, path, m['base'], head, result)  # D319
        with b.tx():
            b.con.execute("UPDATE pm_tasks SET phase='landed',head=?,landed_head=?,evidence=?,worker_provider=?,"
                          'worker_session=?,feedback=? WHERE task_id=?',
                          (head, head, json.dumps(result), r['provider'], r['session'], '', tid))
            b.update_task('manager', tid, status='review')
            b.add_links('manager', tid, [head, *refs])
            summary = (f'T{tid} landed: {head[:12]}\n{result["summary"]}\n'
                       + acceptance + '\n' + '\n'.join(
                           f'{c["name"]}: {c["outcome"]} — {c["detail"]}\n{c["command"]}'
                           for c in result['checks']))
            if crew:  # D319: what the crew did leads; the technical record follows it
                summary = crew['full'] + '\n' + acceptance + '\n\n---- The technical record\n' + summary
            b.add_message('manager', 'owner', f'T{tid}: ready for acceptance', summary + marks,
                          kind='review', topic=f'T{tid}')
            brief.post(b, f'landed:{tid}:{head}', tid, 'Done and on the main branch. ' + brief.bottom_line(result)
                       + (('\n' + crew['short']) if crew and crew['short'] else ''),
                       (['Accept to implement this plan, or Request changes.'] if is_plan else
                        result['asks'] or ['Does it work when you try it? Accept, or Request changes.']),
                       report=summary, ping=True, files=files,
                       more=[f'commit {head[:12]}'] + ([f'to read: {", ".join(d["path"] for d in docs)} '
                                                       '(attached; on the board under the task)'] if docs else [])
                       + ([models.line(spent)] if spent else []) + unsent,  # D218: what it took
                       # D319: a crew run's own problem, not the first one an earlier run stated
                       problem_text=(result['problem'] or None) if crew else None,
                       session=r['session'], cwd=r['cwd'])
            store.release(b, 'task:' + str(tid))
            b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (r['id'],))

    def reconcile(self):
        b = self.b
        # A task taken out of the work on the board (dropped, closed, back to ready) ends
        # its worker (D191); the edit's own process does this too when it can.
        for row in b.q("SELECT DISTINCT r.task_id, t.status FROM pm_runs r JOIN tasks t ON t.id=r.task_id "
                       "WHERE r.state IN ('queued','running') AND t.status IN (%s)"
                       % ','.join('?' * len(fe_board.STOPS)), *fe_board.STOPS):
            b.end_runs(row['task_id'], row['status'])
        for row in b.q("SELECT * FROM pm_runs WHERE state IN ('queued','running','finished','failed') ORDER BY created"):
            r = dict(row)
            if r['state'] == 'finished':
                try:
                    self.completed(r)
                except (ValueError, KeyError, TypeError, fe_board.BoardError) as e:
                    self.fail(r, 'Invalid result: ' + str(e))
            elif r['state'] == 'failed':
                self.fail(r, r['error'] or 'worker failed', provider_error=True)
            elif r['state'] == 'running' and not workers.owned_process(r):
                self.fail(r, 'Worker process exited without a durable result; inspect its log and preserved checkout.')
            elif r['state'] == 'running' and r['role'] == 'worker' and store.task_clock(b, r['task_id'])[0] \
                    > store.TASK_HOURS * 3600 + store.OVERTIME_GRACE_S:
                self.time_out(r)
            elif r['state'] == 'running' and time.time() - r['heartbeat'] > 1800 and not nightly.held(b):
                # (a worker the nightly bench suspended prints nothing and is not stalled, D262)
                brief.post(b, 'stalled:' + r['id'], r['task_id'],
                           'The worker has printed nothing for 30 minutes and still holds its checkout.',
                           ['Leave it running, or send /stop to interrupt it?'], ping=True,
                           session=r.get('session'), cwd=r['cwd'])
        # Work already on origin/main is landed however it got there (a run recorded as
        # failed after its push went through, a hand push): hand it to Yotam for review
        # instead of leaving it blocked. Not while a run still works on the task, and not
        # the commit that already landed once: a task Yotam reopened with a reply still has
        # it as its head until its worker makes a new one, and it waits for that (D212).
        for row in b.q("SELECT m.task_id FROM pm_tasks m JOIN tasks t ON t.id=m.task_id "
                       "WHERE m.head IS NOT NULL AND m.phase <> 'landed' "
                       "AND m.head IS NOT m.landed_head "
                       "AND t.status NOT IN ('done','dropped') AND NOT EXISTS (SELECT 1 FROM pm_runs r "
                       "WHERE r.task_id=m.task_id AND r.state IN ('queued','running','finished','failed'))"):
            tid = row['task_id']
            if b.land_pushed(tid):
                head = b.q1('SELECT head FROM pm_tasks WHERE task_id=?', tid)[0]
                docs = fe_board.task_docs(b, tid)  # D212
                with b.tx():
                    b.update_task('manager', tid, status='review')
                    evidence = json.loads(b.q1('SELECT evidence FROM pm_tasks WHERE task_id=?', tid)[0] or '{}')
                    brief.post(b, f'landed:{tid}:{head}', tid, 'It is on the main branch. ' + brief.bottom_line(evidence),
                               evidence.get('asks') or ['Does it work when you try it? Accept, or reply with what to change.'],
                               report=evidence.get('summary', ''), ping=True,
                               files=[brief.attach(Path(d['path']).name, d['text']) for d in docs],
                               more=[f'commit {head[:12]}'] + ([f'to read: {", ".join(d["path"] for d in docs)} '
                                                               '(attached; on the board under the task)'] if docs else []))
        self.repair_controls()
        # D314: a parked branch is Yotam's to read only until he accepts or drops its task.
        park.sweep(b, self.reg)
        # The leases mirror what the board holds (D191), and a lease the board no longer
        # backs goes: a failed task Yotam dropped frees its checkout.
        holds = fe_board.checkout_holds(b.con)
        for agent, h in holds.items():
            store.lease(b, 'checkout:' + agent, 'task:' + str(h['task']))
        for row in b.q("SELECT resource, owner FROM pm_leases WHERE resource LIKE 'checkout:%'"):
            h = holds.get(row['resource'].split(':', 1)[1])
            if not h or row['owner'] != 'task:' + str(h['task']):
                b.con.execute('DELETE FROM pm_leases WHERE resource=?', (row['resource'],))
        # Acceptance through the existing board is equivalent to Discord acceptance.
        for g in b.q("SELECT * FROM pm_goals WHERE status IN ('active','talking')"):
            states = b.q('SELECT t.status FROM tasks t JOIN pm_tasks m ON m.task_id=t.id WHERE m.goal_id=?', g['id'])
            if states and all(s[0] in ('done', 'dropped') for s in states):
                with b.tx():
                    b.con.execute("UPDATE pm_goals SET status='complete' WHERE id=?", (g['id'],))
                    store.notify(b, f'goal-complete:{g["id"]}', f'Goal G{g["id"]} completed and accepted.', ping=True,
                                 goal=g['id'] if talk.thread_conversation(b, g['id']) else None)
        # A task closed on the board closes its Discord thread (D192): one last line, after
        # everything already queued for it, and the transport archives the thread once it
        # is sent. Reopened, the task may be closed again.
        for row in b.q('SELECT d.task_id, t.status FROM pm_discord_threads d JOIN tasks t ON t.id=d.task_id'):
            tid, key = row['task_id'], f'close:{row["task_id"]}'
            if row['status'] in ('done', 'dropped'):
                store.notify(b, key, f'T{tid} is {row["status"]} on the board; closing this thread.', tid)
            else:
                b.con.execute('DELETE FROM pm_outbox WHERE dedup=? AND sent IS NOT NULL', (key,))
        # The same for a goal's planning thread once the goal is complete (D296); a goal
        # that is worked on again loses the sent line and may be closed again.
        for row in b.q('SELECT d.goal_id, g.status FROM pm_goal_threads d JOIN pm_goals g ON g.id=d.goal_id'):
            gid, key = row['goal_id'], f'close:G{row["goal_id"]}'
            if row['status'] == 'complete':
                store.notify(b, key, f'G{gid} is complete; closing this thread.', goal=gid)
            else:
                b.con.execute('DELETE FROM pm_outbox WHERE dedup=? AND sent IS NOT NULL', (key,))

    def repair_controls(self):
        """A task in review whose Accept control never reached Yotam gets the message again,
        once, with the buttons (D284). The transport records the control when it sends one; a
        message still waiting to be sent carries its own."""
        b = self.b
        for m in b.q("SELECT m.task_id, m.head, m.agent, m.evidence FROM pm_tasks m JOIN tasks t ON t.id=m.task_id "
                     "WHERE t.status='review' AND m.head IS NOT NULL"):
            tid, head = m['task_id'], m['head']
            if store.setting(b, store.control_key(tid, head)) or b.q1(
                    "SELECT 1 FROM pm_outbox WHERE sent IS NULL AND dedup IN (?,?)",
                    f'landed:{tid}:{head}', f'accept-control:{tid}:{head}'):
                continue
            if not fe_board.on_trunk(head, m['agent']):
                continue
            evidence = json.loads(m['evidence'] or '{}')
            brief.post(b, f'accept-control:{tid}:{head}', tid,
                       'It is on the main branch and waiting for you; the buttons on its first message never '
                       'arrived, so here they are. ' + brief.bottom_line(evidence),
                       evidence.get('asks') or ['Does it work when you try it? Accept, or Request changes.'],
                       more=[f'commit {head[:12]}'])

    def time_out(self, r):
        """D201: a task past its hours whose worker did not stop and report when told is
        ended here; the manager reports where it got to from the run's log, and the task
        waits on Yotam, its work left in the checkout."""
        b, tid = self.b, r['task_id']
        spent, _ = store.task_clock(b, tid)
        b.con.execute("UPDATE pm_runs SET state='handled',error=? WHERE id=?",
                      (f'ended after {store.hours(spent)}: the task time limit (D201)', r['id']))
        workers.stop_owned(r, b)
        summary = report.blocked_report(b, tid, r, ended_by_manager=True)  # D321: from the logs, not the worker
        with b.tx():
            if models.run_tier(b, r['id']) == 'sonnet':
                models.escalate(b, tid, 'it ran out of its hours')  # D218, D240: a strong model carries on if Yotam says so
            b.con.execute("UPDATE pm_tasks SET phase='blocked',feedback=?,worker_session=? WHERE task_id=?",
                          ('Question: ' + summary, r['session'], tid))
            b.update_task('manager', tid, status='blocked')
            b.add_message('manager', 'owner', f'T{tid}: out of time', summary, kind='question', topic=f'T{tid}')
            brief.post(b, 'timeout:' + r['id'], tid,
                       f'T{tid} ran out its {store.TASK_HOURS} hours without finishing; it is stopped and blocked.',
                       ['Reply "carry on" for another 3 hours, tell it what to change, or drop it.'],
                       report=summary, ping=True, session=r['session'], cwd=r['cwd'])

    def free_agent(self, prefer=None):
        sessions, procs = fe_sync.sessions(), fe_sync.app_processes()
        holds = fe_board.checkout_holds(self.b.con)
        for name in sorted(store.checkouts(self.reg), key=lambda n: n != prefer):
            if name not in self.reg['agents'] or name in holds:
                continue
            busy, soft, _ = fe_sync.checkout_state(name, self.reg, sessions, procs)
            if not busy and not soft:
                return name
        return None

    def task_provider(self, tid):
        # D238: the tier says which CLI runs the task; `decide` writes nothing, and `choose`
        # in queue_task settles on the same tier once the checkout is ours.
        tier, _, _ = models.decide(self.b, tid)
        return self.provider_for(tier, pinned=bool((models.routes(self.b, [tid]).get(tid) or {}).get('owner_tier')))

    def park_for(self, m):
        """D297: a task that could run but finds no checkout free parks the task blocked
        longest on Yotam's answer (its own checkout's, for a task that must go back there)."""
        if not self.task_provider(m['task_id']) or (not m['agent'] and self.free_agent()):
            return False
        try:
            pick = park.candidate(self.b, self.reg, fe_sync.sessions(), fe_sync.app_processes(), only=m['agent'])
            return bool(pick) and park.park(self.b, self.reg, *pick, why=f"T{m['task_id']}")
        except Exception as e:  # parking never stops the manager; the task waits as before
            self.b.event('manager', 'manager.park', None, f'could not park for T{m["task_id"]}: {e}'[:300])
            return False

    def queue_task(self, m):
        b, tid, role = self.b, m['task_id'], 'worker'
        tier, _, _ = models.decide(b, tid)
        provider = self.task_provider(tid)
        if not provider:
            return False
        parked = None if m['agent'] else park.parked(b, tid)
        agent = m['agent'] or self.free_agent(prefer=parked and parked['agent'])
        if not agent:
            return False
        path = Path(self.reg['agents'][agent]['path'])
        held = fe_board.checkout_holds(b.con).get(agent)
        if held and held['task'] != tid:
            return False
        if m['agent'] and not held:
            busy, soft, _ = fe_sync.checkout_state(agent, self.reg, fe_sync.sessions(), fe_sync.app_processes())
            if busy or soft:
                return False
        # Do not overlap manual sessions even when resuming our own dirty checkout.
        if fe_sync.working_elsewhere(agent, fe_sync.sessions(), None):
            return False
        if fe_sync.owned_processes(agent, path, fe_sync.app_processes()):
            return False
        with b.tx():
            if not store.lease(b, 'checkout:' + agent, 'task:' + str(tid)):
                return False
            if m['phase'] == 'ready':
                if not b.claim(agent, tid):
                    store.release(b, 'task:' + str(tid))
                    return False
                b.update_task(agent, tid, status='in_progress')
            elif b.task(tid)['status'] != 'in_progress':
                b.update_task('manager', tid, status='in_progress')  # its worker starts now (D214)
        moved = None
        if parked:
            # D297: its parked work comes back as it was, on its own base (no pull).
            try:
                moved = park.restore(b, self.reg, agent, tid)
            except RuntimeError as e:
                brief.post(b, f'unpark:{tid}:{parked["tip"][:12]}', tid,
                           f'Its parked work could not be restored in {agent}, so it has not started. '
                           f'The work is still kept at {parked["url"] or parked["path"]}.',
                           ['Reply here to try again, or tell me what to do with it.'], report=str(e), ping=True)
                with b.tx():
                    b.update_task('manager', tid, status='blocked')
                    store.release(b, 'task:' + str(tid))
                return False
            b.con.execute('UPDATE pm_tasks SET agent=? WHERE task_id=?', (agent, tid))
            b.con.execute('UPDATE tasks SET claimed_by=? WHERE id=?', (agent, tid))
        elif not m['agent']:
            pulled = fe_sync.sync_pull(path, self.reg)
            if not pulled.ok:
                brief.post(b, f'pull:{tid}', tid, f'Its checkout {agent} could not be brought up to date, '
                           'so it has not started.', ['Clean up that checkout, then reply here to start it.'],
                           report=pulled.message, ping=True)
                with b.tx():
                    b.update_task('manager', tid, status='ready')
                    store.release(b, 'task:' + str(tid))
                return False
            b.con.execute('UPDATE pm_tasks SET agent=?,base=? WHERE task_id=?',
                          (agent, fe_sync.git_out(path, 'rev-parse', 'HEAD'), tid))
        session = m['worker_session'] if m['worker_provider'] == provider else None
        feedback = m['feedback']
        if moved:
            # D297: restored from parking; a session does not follow its work to another checkout.
            feedback = self.carry_over(feedback, provider, moved)
            session = session if agent == parked['agent'] else None
        size = context.session_context(session) if session and provider == 'claude' else None
        if size and size > context.RESUME_CAP:
            # D253: a resume re-reads the whole session every turn; past the cap, start afresh.
            last = b.q1("SELECT result FROM pm_runs WHERE task_id=? AND role='worker' AND result<>'' "
                        'ORDER BY created DESC LIMIT 1', tid)
            stat = fe_sync.git_out(path, 'diff', '--stat', m['base']) if m['base'] else ''
            feedback = self.carry_over(feedback, provider, context.fresh_note(size, last[0] if last else '', stat))
            session = None
        if m['worker_session'] and m['worker_provider'] and m['worker_provider'] != provider:
            # D238: a session does not cross CLIs, so this run starts fresh and is told so.
            feedback = self.carry_over(feedback, m['worker_provider'],
                                       NEW_SESSION.format(old=m['worker_provider'], new=provider))
        with b.tx():
            # D218: the fastest model this kind of work has shown it can carry, not Opus by default.
            # D238: a tier runs on its own CLI; on any other, that CLI's own default model.
            tier, why = models.choose(b, tid) if models.provider_of(tier) == provider else ('', '')
            rid = store.queue_run(b, role, provider, path,
                                  self.prompt(role, tid, feedback=feedback, gathered=session is None, root=path),
                                  task=tid, goal=m['goal_id'], agent=agent, session=session,
                                  tier=tier, model=models.model_id(self.config, tier) if tier else '')
            store.notify(b, 'run:' + rid, f'T{tid}: {models.tier_label(b, tier) if tier else models.label(provider)} {role} starting in {agent}'
                         + (f' ({why})' if why else '') + '.', tid)
            b.con.execute("UPDATE pm_tasks SET phase='working',feedback='',worker_provider=?,worker_session=? "
                          'WHERE task_id=?', (provider, session, tid))
        return True

    @staticmethod
    def artifacts(root, paths, dropped=None):
        """Only task evidence under its checkout; copy to durable board image storage.
        Each path left out is added to DROPPED as 'path: why' (D304), so it is said, not lost."""
        out, dropped = [], dropped if dropped is not None else []
        names = []
        for a in paths:  # "x.png", "x.png (before)", "a.png, b.png", as docs_of reads them
            words = [w.strip(',;()`\'"') for w in str(a).split()]
            found = [w for w in words if w and (root / w).is_file()]
            names += [a] if (root / str(a)).is_file() or not found else found
        for n, name in enumerate(dict.fromkeys(names)):
            if n >= MAX_ARTIFACTS:
                dropped.append(f'{name}: past the first {MAX_ARTIFACTS}')
                continue
            p = (root / name).resolve()
            if not p.is_relative_to(root.resolve()):
                dropped.append(f'{name}: outside the checkout')
                continue
            if not p.is_file():
                dropped.append(f'{name}: no such file')
                continue
            if p.stat().st_size > fe_board.MAX_IMAGE:
                dropped.append(f'{name}: over {fe_board.MAX_IMAGE >> 20} MB')
                continue
            try:
                if p.suffix.lstrip(".").lower() in fe_board.FILE_KINDS:
                    out.append(str(fe_board.store_file(p.read_bytes(), p.name)))
                    continue
                if p.suffix.lower() in TEXT_ARTIFACTS:  # a document or a log it wants read: as text
                    out.append(brief.attach(p.name, p.read_text(encoding='utf-8', errors='replace')))
                    continue
                mark = fe_board.store_image(p.read_bytes(), p.name)
                match = fe_board.IMAGE_RE.search(mark)
                if match:
                    out.append(str(fe_board.files_dir() / match[2]))
            except fe_board.BoardError as e:
                dropped.append(f'{name}: {e}')
        return out

    def shown(self, r, result):
        """What a finished run shows Yotam (D304): its artifacts as board files, and a line
        naming any it named that could not go. Every message made from the run takes both."""
        dropped = []
        files = self.artifacts(Path(r['cwd']), result['artifacts'], dropped)
        return files, ['not attached: ' + '; '.join(dropped)] if dropped else []

    def plan(self):
        """Planners have their own lane (D216): they only read, so they take no run slot and
        no checkout. A goal is planned while both workers run, in the trunk's worktree."""
        b = self.b
        count = b.q1("SELECT count(*) FROM pm_runs WHERE role='planner' AND state IN ('queued','running')")[0]
        for g in b.q("SELECT * FROM pm_goals WHERE status='planning' ORDER BY id"):
            if count >= store.PLANNERS:
                return
            if b.q1("SELECT 1 FROM pm_runs WHERE goal_id=? AND role='planner' AND state IN ('queued','running')", g['id']):
                continue
            p = self.provider()
            if not p:
                return
            try:
                home = release.reading(self.root) or self.root
            except (RuntimeError, OSError, subprocess.SubprocessError):
                home = self.root  # no worktree of the trunk: read the manager's own checkout, as before
            tier = models.planner_tier(b) if p == 'claude' else ''
            g = dict(g)
            if p != 'claude' and g['session']:
                g['session'] = ''  # a Claude session does not resume in Codex: the conversation goes in whole
            prompt = talk.next_prompt(b, g, self.prompt('planner', goal=g['id']))
            with b.tx():
                store.queue_run(b, 'planner', p, home, prompt, goal=g['id'], session=g['session'] or None,
                                tier=tier, model=models.model_id(self.config, tier) if tier else '')
                b.con.execute("UPDATE pm_goals SET pending='' WHERE id=?", (g['id'],))
            count += 1

    def schedule(self):
        b = self.b
        self.plan()
        cap = self.cap()
        count = b.q1("SELECT count(*) FROM pm_runs WHERE role NOT IN ('planner','quick') AND state IN ('queued','running')")[0]
        if count >= cap:
            return
        # A task left waiting for a reviewer or the integrator (before D188) is its worker's again.
        b.con.execute("UPDATE pm_tasks SET phase='revise',feedback=COALESCE(feedback,'') || ? "
                      "WHERE phase IN ('review','reviewing','integrate','integrating') AND NOT EXISTS "
                      "(SELECT 1 FROM pm_runs r WHERE r.task_id=pm_tasks.task_id AND r.state IN ('queued','running'))",
                      ('\n' + LANDS_ITSELF,))
        # Finish existing work before opening more tasks.
        for row in b.q("SELECT * FROM pm_tasks WHERE phase IN ('revise','ready') "
                       "ORDER BY CASE phase WHEN 'revise' THEN 0 ELSE 1 END, task_id"):
            m = dict(row)
            t = b.task(m['task_id'])
            # Only what the board has waiting or in progress runs: a task Yotam set blocked,
            # in review or out of the work stays where he put it (D191).
            if t['status'] not in ('ready', 'claimed', 'in_progress') or not b.deps_done(t):
                continue
            if self.queue_task(m) or self.park_for(m) and self.queue_task(
                    dict(b.q1('SELECT * FROM pm_tasks WHERE task_id=?', m['task_id']))):
                count += 1
            if count >= cap:
                return

    def launch(self, only=None):
        """ONLY: the roles to start. A paused manager still answers in the talk channel (D317)."""
        import psutil
        for row in self.b.q("SELECT * FROM pm_runs WHERE state='queued'"):
            r = dict(row)
            if only and r['role'] not in only:
                continue
            if store.setting(self.b, 'mode') != 'running' and r['role'] != 'quick':
                continue
            if workers.owned_process(r):
                continue
            if r['pid']:
                self.fail(r, 'Worker wrapper exited before initialization')
                continue
            folder = workers.run_dir(r['id'])
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'wrapper.log').open('ab') as log:
                # The wrapper runs the service's own release, never a checkout's files (D189).
                p = subprocess.Popen([workers.PYTHON, str(release.tool('fe_manager.py')),
                                      '_worker', r['id']], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=workers.NO_WINDOW, env={**os.environ, 'FE_BOARD_DIR': str(fe_board.board_dir())})
            try:
                self.b.con.execute("UPDATE pm_runs SET pid=?,pid_started=? WHERE id=? AND state='queued'",
                                   (p.pid, psutil.Process(p.pid).create_time(), r['id']))
            except psutil.NoSuchProcess:
                # A very fast wrapper may already have persisted its result.
                pass

    def tick(self):
        mobile = studio_config.adapter('mobile')
        if mobile is not None:
            mobile.tick(self.b, self.config)
        store.set_setting(self.b, 'heartbeat', time.time())
        # What this service can run, for the outlook (D210): its own view, not a reader's.
        ready = self.available()
        store.set_setting(self.b, store.SLOTS, json.dumps({'slots': store.slots(ready), 'providers': ready}))
        try:
            usage.poll_codex(self.b)  # D243: Codex's own usage reading, for the status
        except Exception:
            pass  # display only; never stops the manager
        self.mirror_board()
        self.input_events()
        plan_docs.reconcile(self.b)
        mode = store.setting(self.b, 'mode')
        if mode == 'stopped':
            store.stop_runs(self.b)
            return
        self.reconcile()
        quick.queue(self)  # D317, D322: his questions, answered in their own lane
        self.performance_proposals()
        try:
            models.backfill(self.b, limit=10)  # D218: the runs that ended before metering, a few a tick
        except Exception as e:  # accounting never stops the manager
            self.b.event('manager', 'manager.backfill', None, f'could not read old runs: {e}'[:300])
        try:
            self.usage_report()  # D253: where last night's tokens and time went, once each morning
        except Exception as e:  # a report never stops the manager
            self.b.event('manager', 'manager.usage', None, f'could not report usage: {e}'[:300])
        for j in nightly.jobs():
            try:
                self.nightly_job(j)  # D262: the game's nights (the bench at 04:00, holding the work)
            except Exception as e:  # a night never stops the manager
                self.b.event('manager', f'manager.{j["name"]}', None,
                             f'could not start the nightly {j["name"]}: {e}'[:300])
        try:
            self.nightly_crew()  # D265: after the bench lets go, the cleanup crew's scorecard and tasks
        except Exception as e:  # the crew never stops the manager
            self.b.event('manager', 'manager.crew', None, f'could not start the crew night: {e}'[:300])
        if mode == 'running' and not nightly.held(self.b):
            self.schedule()
            self.launch()
        elif not nightly.held(self.b) and self.b.q1("SELECT 1 FROM pm_runs WHERE role='quick' AND state='queued'"):
            self.launch(only=('quick',))

    def nightly_job(self, j, now=None, popen=subprocess.Popen):
        """D262: once a night from its hour, a game's nightly job (studio.toml [[nightly]]: the
        bench of origin/main) runs in its own process, hidden. While one that holds the work
        lives no run starts (the bench suspends the running workers only while the GPU
        measures, and files what it finds as one task)."""
        from datetime import datetime
        import psutil
        now = datetime.now().astimezone() if now is None else now
        name, mod, holds = j['name'], nightly.module(j), bool(j.get('holds_work'))
        if holds and nightly.held(self.b):
            return False
        past = store.setting(self.b, nightly.key(j, 'request'))  # `bench-night --night DAY --service`
        if past:
            store.set_setting(self.b, nightly.key(j, 'request'), '')
        elif not nightly.due(self.b, self.config, j, now):
            return False
        else:
            store.set_setting(self.b, nightly.key(j, 'night'), now.date().isoformat())
        log = fe_board.board_dir() / 'manager' / f'{name}-night.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        args = getattr(mod, 'request_args', lambda v: ['--request', v])(past) if past else []
        with log.open('ab') as out:
            p = popen([workers.PYTHON, str(release.tool('fe_manager.py')), f'_{name}_night', *args],
                      stdin=subprocess.DEVNULL, stdout=out, stderr=out, creationflags=workers.NO_WINDOW,
                      env={**os.environ, 'FE_BOARD_DIR': str(fe_board.board_dir()), 'PYTHONUNBUFFERED': '1'})
        if holds:
            # The hold is the service's from the spawn, so no run starts before the job takes it.
            try:
                started = psutil.Process(p.pid).create_time()
            except psutil.Error:
                return False
            store.set_setting(self.b, nightly.key(j, 'hold'), json.dumps(dict(
                pid=p.pid, started=started, since=time.time(), suspended=[])))
        note = getattr(mod, 'started_note', lambda v: f' ({v})')(past) if past else ''
        self.b.event('manager', f'manager.{name}', None, f'nightly {name} started (pid {p.pid})' + note)
        return True

    def usage_report(self, now=None):
        """D253: after 10:00, once a day, fe_usage's reading of the night (yesterday 18:00 to
        today 10:00) goes to the channel. Code reads the transcripts and the meter; no model runs."""
        from datetime import datetime, timedelta
        now = datetime.now().astimezone() if now is None else now
        day = now.date().isoformat()
        if not self.config.get('usage_report', True):
            return False
        if now.hour < 10 or store.setting(self.b, USAGE_REPORT) == day:
            return False
        store.set_setting(self.b, USAGE_REPORT, day)
        import fe_usage
        hi = now.replace(hour=10, minute=0, second=0, microsecond=0)
        a = fe_usage.analyse((hi - timedelta(days=1)).replace(hour=18), hi, 5)
        if not a['meter']['runs'] and not a['sessions']:
            return False
        store.notify(self.b, f'usage:{day}', fe_usage.summary(a))
        return True

    def nightly_crew(self, now=None, popen=subprocess.Popen):
        """D265: once a night, after the bench's hold is gone (from 06:00 without one), the crew's
        night runs in its own hidden process: the scorecard, kept and posted, and with the crew
        on its two tasks. The day is marked at the spawn, so a failed night is not retried."""
        from datetime import datetime
        import manager_crew as crew
        now = datetime.now().astimezone() if now is None else now
        if not crew.due(self.b, self.config, now):
            return False
        store.set_setting(self.b, crew.NIGHT, now.date().isoformat())
        log = fe_board.board_dir() / 'manager' / 'crew-night.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('ab') as out:
            popen([workers.PYTHON, str(release.tool('fe_manager.py')), '_crew_night'],
                  stdin=subprocess.DEVNULL, stdout=out, stderr=out, creationflags=workers.NO_WINDOW,
                  env={**os.environ, 'FE_BOARD_DIR': str(fe_board.board_dir()), 'PYTHONUNBUFFERED': '1'})
        self.b.event('manager', 'manager.crew', None, f'crew night started ({crew.mode(self.config)})')
        return True

    def performance_proposals(self):
        """Expose cumulative drift as an idea, never dispatch without the owner."""
        debt = studio_config.adapter('debt')  # the game's bench registry; none, nothing to propose
        if debt is None:
            return
        cache, save = debt.registry(), debt.save
        for path in cache.root.glob('proposals/*.json'):
            with cache.lock('proposal:' + path.stem):
                proposal = json.loads(path.read_text())
                if proposal.get('board_task'):
                    continue
                body = ('## Why\n' + proposal['reasoning'] + '\n\n## What to do\n'
                        'Profile and recover the measured costs to the fixed reference. Keep player behavior.\n'
                        'Scenario: ' + proposal['scenario']['name'] + '\n'
                        'Reference: ' + proposal['reference'] + '\n'
                        'Recovery targets (ms): ' + json.dumps(proposal['target_ms']) + '\n'
                        'Current drift (ms): ' + json.dumps(proposal['drift_ms']) + '\n'
                        'Evidence: ' + str(path))
                # Board idea approval is the existing owner gate; this has no
                # dependency on the completed feature task and never marks it ready.
                tid = self.b.add_task('manager', proposal['title'], body=body, status='idea', priority=2)
                proposal['board_task'] = tid
                save(path, proposal)
                store.adopt(self.b, tid, proposed=True)  # creates its approval thread; remains an idea
        if time.time() - float(store.setting(self.b, 'performance_review', '0')) >= 7 * 86400:
            debts = [json.loads(p.read_text()) for p in cache.root.glob('debt/*.json')]
            opened = [d for d in debts if d.get('status') == 'open']
            if opened:
                self.b.event('manager', 'performance.review', None,
                             f'Weekly performance debt review: {len(opened)} open; registry {cache.root}')
                worst = sorted(opened, key=lambda d: max(d['delta_ms'].values()), reverse=True)[:8]
                lines = [f"{d['scenario']['name']} {d['commit'][:12]}: GPU {d['delta_ms']['gpu_ms']:+.3f} ms, aggregate {d['delta_ms']['combined_ms']:+.3f} ms"
                         for d in worst]
                store.notify(self.b, 'performance-week:' + str(int(time.time() // (7 * 86400))),
                             f'Weekly performance debt review: {len(opened)} open items. Costs overlap; these are not summed.\n'
                             + '\n'.join(lines) + '\nRecovery proposals require your approval. Registry: ' + str(cache.root))
            store.set_setting(self.b, 'performance_review', str(time.time()))
