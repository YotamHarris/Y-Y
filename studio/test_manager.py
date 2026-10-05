"""No accounts, GPU, Discord network, or live board required.

python -m unittest discover -s engine/tools -p test_manager.py -v
"""
import asyncio
import concurrent.futures
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock, AsyncMock

import studio_config
sys.path.append(str(studio_config.game_tools_dir()))  # D329: the game's adapters (fe_gpu, ...) and fe.py
import fe
import fe_board as board
import fe_codex
import fe_gpu
import fe_identity
import fe_sync
import fe_manager
import manager_core as core
import manager_brief as brief
import manager_discord as transport
import manager_host as host
import manager_models as models
import manager_outlook as outlook
import manager_park as park
import manager_report as report
import manager_store as store
import manager_usage as usage
import manager_workers as workers


def result(status='complete', head='a' * 40, **extra):
    r = dict(status=status, summary='Validated change', head=head, tasks=[], artifacts=[],
             checks=[dict(name=c, outcome='passed' if c == 'tests' else 'not_applicable',
                          command='python -m unittest' if c == 'tests' else '',
                          detail='Tests passed' if c == 'tests' else 'Tool-only change') for c in workers.CHECKS])
    r.update(extra)
    return r


# The two real incidents, read out of pm_runs.error in the shared board store. codex
# names the hour its allowance comes back, five days out, in the same text as its own
# ERROR log line; the second dump repeats the message with no log line at all.
_LIMIT = ('{"type": "%s", "message": "You\u2019ve hit your usage limit. Visit '
          'https://chatgpt.com/codex/settings/usage to purchase more credits or try again at '
          'Oct 5th, 2026 10:12 AM."}')
CODEX_LIMIT = ('exit 1: ' + _LIMIT % 'error' + '\n' + _LIMIT % 'turn.failed' + '\n'
               '2026-09-29T23:55:51.221171Z ERROR codex_core::session: failed to record rollout '
               'items: thread 01a0ef2f-2c18-7c51-ae1e-951fefd548a0 not found\n')
CODEX_LIMIT_REPEATED = 'exit 1: ' + (_LIMIT % 'error' + '\n') * 4
# 2026-09-30 12:00 local: the clock the parser cases are read against.
NOW = datetime.datetime(2026, 9, 30, 12, 0, 0).timestamp()


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'FE_BOARD_DIR': str(self.root / 'board'),
                                          'FE_PERFORMANCE_DIR': str(self.root / 'performance')})
        self.env.start()
        self.b = board.Board()
        self.config = dict(repo=str(self.root), codex=sys.executable, claude=sys.executable,
                           guild_id='11', channel_id='22', owner_id='33', created=time.time() - 1,
                           usage_report=False,  # D253: no reading of the real transcripts
                           nightly_bench=False,  # D262: no real bench from a test's tick
                           nightly_crew='off')  # D265: nor a crew night
        self.manager = core.Manager(self.b, self.config)
        self.manager.reg = {'remote': 'origin', 'branch': 'main', 'agents': {
            'A1': {'path': str(self.root / 'primary'), 'primary': True},
            'A2': {'path': str(self.root / 'worker'), 'port': 45444},
            'A3': {'path': str(self.root / 'worker3'), 'port': 45666}}}

    def tearDown(self):
        self.b.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_token_setup_keeps_cli_paths_when_shell_path_differs(self):
        cfg_path = host.config_path()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(self.config), encoding='utf-8')
        with patch.object(host.shutil, 'which', return_value=None), \
                patch.object(host.getpass, 'getpass', return_value='test-token'), \
                patch.object(host, 'credential_write') as write:
            host.configure(None, None, None, token=True)
        saved = host.load_config()
        self.assertEqual(saved['codex'], self.config['codex'])
        self.assertEqual(saved['claude'], self.config['claude'])
        self.assertEqual(saved['guild_id'], self.config['guild_id'])
        self.assertNotIn('test-token', cfg_path.read_text(encoding='utf-8'))
        write.assert_called_once_with('test-token')

    def goal(self, tasks=None, approve=True):
        store.receive(self.b, 'owner:1', 'reply', 'Implement a bounded tool improvement')
        self.manager.input_events()
        gid = self.b.q1('SELECT id FROM pm_goals')[0]
        tasks = [dict(t, reasoning=t.get('reasoning', 'So the tool does what it says.'))
                 for t in tasks or [dict(title='Tool works', body='Implement and test', depends=[])]]
        ids = store.plan_tasks(self.b, gid, tasks)
        for tid in ids if approve else ():
            self.b.update_task('owner', tid, status='ready')  # Yotam approves the proposals (D195)
        return gid, ids

    def test_performance_recovery_proposals_wait_for_owner_and_weekly_review_is_deduplicated(self):
        from bench_store import Store, save
        cache = Store()
        scenario = {'name': 'mutant-4-healing'}
        save(cache.root / 'proposals/p.json', dict(title='Recover performance', scenario=scenario,
             reference='b'*40, commit='a'*40, reasoning='Measured cumulative drift exceeded 1 ms.',
             target_ms={'gpu_ms': 1.0, 'combined_ms': 1.0}, drift_ms={'gpu_ms': 1.1, 'combined_ms': 1.1}))
        save(cache.root / 'debt/d.json', dict(status='open', scenario=scenario, commit='a'*40,
             delta_ms={'gpu_ms': .2, 'combined_ms': .3}))
        self.manager.performance_proposals()
        proposal = json.loads((cache.root/'proposals/p.json').read_text())
        tid = proposal['board_task']
        self.assertEqual(self.b.task(tid)['status'], 'idea')
        self.assertTrue(self.b.q1('SELECT task_id FROM pm_tasks WHERE task_id=?', tid))
        self.assertFalse(self.b.q1('SELECT id FROM pm_runs WHERE task_id=?', tid))
        self.manager.performance_proposals()
        self.assertEqual(len(self.b.q('SELECT id FROM tasks')), 1)
        self.assertEqual(len(self.b.q("SELECT id FROM pm_outbox WHERE dedup LIKE 'performance-week:%'")), 1)

    def task(self, phase='working'):
        gid, ids = self.goal()
        tid = ids[0]
        self.b.claim('A2', tid)
        self.b.update_task('A2', tid, status='in_progress')
        self.b.con.execute('UPDATE pm_tasks SET agent=?,phase=?,base=? WHERE task_id=?', ('A2', phase, 'b'*40, tid))
        store.lease(self.b, 'checkout:A2', 'task:' + str(tid))
        return gid, tid

    def new_run(self, tid, role='worker', data=None, state='finished', provider='codex'):
        rid = store.queue_run(self.b, role, provider, self.root / 'worker', 'prompt',
                              task=tid, goal=1, agent='A2', session='exact-session')
        self.b.con.execute('UPDATE pm_runs SET state=?,result=? WHERE id=?',
                           (state, json.dumps(data or result()), rid))
        return dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', rid))

    def queued(self, m):
        """Stand in for a dispatch: one run on the board, as queue_task would leave it."""
        store.queue_run(self.b, 'worker', 'claude', self.root, 'prompt',
                        task=m['task_id'], goal=m['goal_id'], agent='A2')
        return True

    def test_migration_preserves_v1_board_and_is_idempotent(self):
        path = self.root / 'v1.db'
        c = sqlite3.connect(path)
        c.executescript(board.DDL)
        c.execute('PRAGMA user_version=1')
        c.execute("INSERT INTO notes(id,author,body,created,updated) VALUES(1,'owner','keep me',0,0)")
        c.commit()
        c.close()
        for _ in range(2):
            with board.Board(path) as b:
                self.assertEqual(b.note(1)['body'], 'keep me')
                self.assertEqual(b.q1('PRAGMA user_version')[0], board.SCHEMA)
                self.assertEqual(store.setting(b, 'mode'), 'paused')

    def test_live_schema_does_not_migrate_while_an_old_checkout_is_active(self):
        shared = self.root / 'BodySimulation/board/board.db'
        shared.parent.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(shared)) as c:
            c.executescript(board.DDL)
            c.execute('PRAGMA user_version=1')
        source = self.root / 'old/engine/tools/fe_board.py'
        source.parent.mkdir(parents=True)
        source.write_text('SCHEMA = 1\n', encoding='utf-8')
        registry = {'agents': {'A2': {'path': str(self.root / 'old')}}}
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root), 'FE_BOARD_DIR': str(shared.parent)}), \
             patch.object(board, 'registry', return_value=registry):
            # The newer tool still works (the old checkout may be leased and unable to
            # pull); only the version the old tool would refuse waits.
            with board.Board() as b:
                self.assertEqual(store.setting(b, 'mode'), 'paused')
        with contextlib.closing(sqlite3.connect(shared)) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_a_checkout_mid_rebase_counts_as_outdated(self):
        # A3's Stop hook rebased onto trunk and conflicted: for that moment its tree held
        # trunk's tools, the store was bumped, and A3's own tools refused it afterwards.
        source = self.root / 'rebasing/engine/tools/fe_board.py'
        source.parent.mkdir(parents=True)
        source.write_text(f'SCHEMA = {board.SCHEMA}\n', encoding='utf-8')
        registry = {'agents': {'A3': {'path': str(self.root / 'rebasing')}}}
        with patch.dict(os.environ, {'FE_BOARD_DIR': str(self.root / 'nowhere')}), \
             patch.object(board, 'registry', return_value=registry):
            self.assertEqual(board.outdated_checkouts(), [])
            (self.root / 'rebasing/.git/rebase-merge').mkdir(parents=True)
            self.assertEqual(board.outdated_checkouts(), ['A3'])

    def test_a_server_on_an_older_schema_is_replaced(self):
        # The A2 server kept v2 code from the day before; at the same SERVER_VERSION it
        # was never replaced and answered 409 once the store was v3.
        stale = {'ok': True, 'version': board.SERVER_VERSION, 'schema': board.SCHEMA - 1}
        current = dict(stale, schema=board.SCHEMA)
        answers = iter([stale])
        with patch.object(board, 'server_health', side_effect=lambda: next(answers, current)), \
             patch.object(board, 'server_up', return_value=False), \
             patch.object(board.urllib.request, 'urlopen') as shutdown, \
             patch.object(board.subprocess, 'Popen') as popen, \
             patch.object(board, 'open', create=True, return_value=io.BytesIO()), \
             patch.object(board, 'board_dir', return_value=self.root / 'srv'):
            self.assertTrue(board.ensure_server(quiet=True))
        self.assertIn('/api/shutdown', shutdown.call_args[0][0].full_url)
        popen.assert_called()
        with patch.object(board, 'server_health', return_value=current), \
             patch.object(board.subprocess, 'Popen') as popen:
            self.assertTrue(board.ensure_server(quiet=True))
        popen.assert_not_called()

    def test_live_schema_waits_for_the_managers_own_checkout_too(self):
        # The manager runs from a checkout that is not in the agent registry; a store it
        # could no longer open must not be migrated behind its back.
        shared = self.root / 'BodySimulation/board/board.db'
        shared.parent.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(shared)) as c:
            c.executescript(board.DDL)
            c.execute('PRAGMA user_version=1')
        for name, schema in (('registered', board.SCHEMA), ('manager-repo', board.SCHEMA - 1)):
            source = self.root / name / 'engine/tools/fe_board.py'
            source.parent.mkdir(parents=True)
            source.write_text(f'SCHEMA = {schema}\n', encoding='utf-8')
        (shared.parent / 'manager').mkdir()
        (shared.parent / 'manager/config.json').write_text(
            json.dumps({'repo': str(self.root / 'manager-repo')}), encoding='utf-8')
        registry = {'agents': {'A2': {'path': str(self.root / 'registered')}}}
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root), 'FE_BOARD_DIR': str(shared.parent)}), \
             patch.object(board, 'registry', return_value=registry):
            self.assertEqual(board.outdated_checkouts(),
                             ['the manager checkout ' + str(self.root / 'manager-repo')])
            with board.Board() as b:
                self.assertEqual(b.q1('PRAGMA user_version')[0], 1)
            (self.root / 'manager-repo/engine/tools/fe_board.py').write_text(
                f'SCHEMA = {board.SCHEMA}\n', encoding='utf-8')
            self.assertEqual(board.outdated_checkouts(), [])
            with board.Board() as b:
                self.assertEqual(b.q1('PRAGMA user_version')[0], board.SCHEMA)

    def test_duplicate_input_and_replayed_plan(self):
        self.assertTrue(store.receive(self.b, 'x', 'reply', 'goal'))
        self.assertFalse(store.receive(self.b, 'x', 'reply', 'changed'))
        self.manager.input_events()
        self.manager.input_events()
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_goals')[0], 1)
        tasks = [dict(title='One', body='Test', reasoning='Why', depends=[])]
        store.plan_tasks(self.b, 1, tasks)
        store.plan_tasks(self.b, 1, tasks)
        self.assertEqual(len(self.b.tasks()), 1)

    def test_plan_is_atomic_and_rejects_forward_dependency(self):
        store.receive(self.b, 'x', 'reply', 'goal')
        self.manager.input_events()
        with self.assertRaises(ValueError):
            store.plan_tasks(self.b, 1, [dict(title='One', body='Test', reasoning='Why', depends=[]),
                                          dict(title='Two', body='Test', reasoning='Why', depends=[2])])
        self.assertEqual(len(self.b.tasks()), 0)

    def test_acceptance_unlocks_dependency_not_landing(self):
        _, ids = self.goal([dict(title='One', body='test', depends=[]), dict(title='Two', body='test', depends=[0])])
        self.b.con.execute("UPDATE pm_tasks SET phase='landed',landed_head=? WHERE task_id=?", ('a'*40, ids[0]))
        self.b.update_task('manager', ids[0], status='review')
        with self.assertRaises(board.BoardError):
            self.b.claim('A3', ids[1])
        store.accept(self.b, ids[0])
        self.assertTrue(self.b.claim('A3', ids[1]))

    def test_cannot_accept_unlanded_or_close_as_manager(self):
        _, ids = self.goal()
        with self.assertRaises(ValueError):
            store.accept(self.b, ids[0])
        with self.assertRaises(board.BoardError):
            self.b.update_task('manager', ids[0], status='done')

    def test_concurrent_claim_and_checkout_lease(self):
        _, ids = self.goal()
        def attempt(name):
            with board.Board() as b:
                with b.tx():
                    if not store.lease(b, 'checkout:A2', name):
                        return False
                    return b.claim(name, ids[0])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            wins = list(pool.map(attempt, ['A2', 'A3']))
        self.assertEqual(sum(wins), 1)

    def test_stale_lease_cannot_release_new_owner(self):
        self.assertTrue(store.lease(self.b, 'gpu', 'one', 10, now=0))
        self.assertFalse(store.lease(self.b, 'gpu', 'two', 10, now=9))
        self.assertTrue(store.lease(self.b, 'gpu', 'two', 10, now=11))
        store.release(self.b, 'one')
        self.assertEqual(self.b.q1('SELECT owner FROM pm_leases')[0], 'two')

    def test_heartbeat_without_work_does_not_call_ai(self):
        store.control(self.b, 'resume')
        with patch.object(self.manager, 'launch') as launch:
            self.manager.tick()
            self.manager.tick()
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_runs')[0], 0)

    def test_pause_finishes_results_without_dispatch(self):
        _, tid = self.task()
        self.new_run(tid)
        with patch.object(fe_sync, 'git_out', side_effect=lambda _, *a: 'a'*40 if a[0] == 'rev-parse' else ''), \
             patch.object(board, 'on_trunk', return_value=True), \
             patch.object(self.manager, 'launch') as launch:
            self.manager.tick()
        launch.assert_not_called()
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks')[0], 'landed')

    def test_stop_only_owned_workers_preserves_task(self):
        _, tid = self.task()
        self.new_run(tid, state='running')
        with patch.object(workers, 'stop_owned') as stop:
            store.control(self.b, 'stop')
            self.manager.tick()
        stop.assert_called_once()
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks')[0], 'revise')
        self.assertEqual(self.b.q1('SELECT attempts FROM pm_tasks')[0], 0)

    def test_restart_adopts_live_worker_and_holds_checkout(self):
        _, tid = self.task()
        self.new_run(tid, state='running')
        with patch.object(workers, 'owned_process', return_value=object()):
            self.manager.reconcile()
        self.assertEqual(self.b.q1('SELECT state FROM pm_runs')[0], 'running')
        self.assertIsNotNone(self.b.q1('SELECT * FROM pm_leases WHERE resource=?', 'checkout:A2'))

    def test_crashed_worker_retains_checkout_and_bounded_retry(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        with patch.object(workers, 'owned_process', return_value=None):
            self.manager.reconcile()
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks')[0], 'revise')
        r = self.new_run(tid, state='running')
        with patch.object(workers, 'owned_process', return_value=None):
            self.manager.reconcile()
        self.assertEqual(self.b.task(tid)['status'], 'blocked')
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks')[0], 'failed')

    def test_a_failed_run_keeps_yotams_reply_for_the_next(self):
        """D214: T39's reply ("melee on a key, left click always shoots") went into a run
        that was killed; the retry's feedback was the error alone."""
        _, tid = self.task()
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        prompt = self.manager.prompt('worker', tid, feedback='\nOwner: melee on a key, left click always shoots')
        rid = store.queue_run(self.b, 'worker', 'claude', self.root / 'worker', prompt, task=tid, goal=m['goal_id'],
                              agent='A2')
        self.b.con.execute("UPDATE pm_runs SET state='running' WHERE id=?", (rid,))
        self.b.con.execute("UPDATE pm_tasks SET feedback=? WHERE task_id=?", ('\nOwner: and slower', tid))
        self.manager.fail(dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', rid)), 'missing structured result fields')
        fb = self.b.q1('SELECT feedback FROM pm_tasks WHERE task_id=?', tid)[0]
        self.assertIn('Owner: melee on a key, left click always shoots', fb)
        self.assertIn('Owner: and slower', fb)
        self.assertIn('The last run failed: missing structured result fields', fb)
        self.assertEqual(self.b.task(tid)['status'], 'claimed')
        self.assertIn('melee on a key', self.manager.prompt('worker', tid, feedback=fb))

    def test_allowance_fails_over_permission_blocks(self):
        _, tid = self.task()
        r = self.new_run(tid, state='failed')
        self.manager.fail(r, 'Rate limit exceeded', provider_error=True)
        self.assertEqual(self.manager.provider('codex'), 'claude')
        r = self.new_run(tid, state='failed', provider='claude')
        self.manager.fail(r, 'permission denied')
        self.assertEqual(self.b.task(tid)['status'], 'blocked')

    def spent(self, tid, error='exit 1: {"type":"error","message":"You have hit your usage limit."}'):
        """A run the CLI itself ended, reconciled the way the supervisor reconciles it."""
        rid = self.new_run(tid, state='failed')['id']
        self.b.con.execute('UPDATE pm_runs SET error=? WHERE id=?', (error, rid))
        self.manager.reconcile()
        return rid

    def test_allowance_failure_is_silent_and_costs_no_attempt(self):
        _, tid = self.task()
        self.b.con.execute('DELETE FROM pm_outbox')  # the plan's own notice, not a failure
        rid = self.spent(tid)
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        self.assertEqual((m['attempts'], m['phase']), (0, 'revise'))
        # D214: waiting to try again is claimed, not in progress, and still holds its checkout
        self.assertEqual(self.b.task(tid)['status'], 'claimed')
        self.assertEqual(board.checkout_holds(self.b.con)['A2']['task'], tid)
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 0)
        blocked = self.b.q1('SELECT * FROM pm_providers WHERE name=?', 'codex')
        self.assertEqual(blocked['reason'], 'allowance')
        self.assertGreater(blocked['retry_at'], time.time())
        self.assertEqual(self.b.q1("SELECT count(*) FROM events WHERE kind='manager.allowance'")[0], 1)
        self.assertIn('ran out of allowance', m['feedback'])
        self.assertNotIn('exit 1', m['feedback'])
        self.assertIn('usage limit', self.b.q1('SELECT error FROM pm_runs WHERE id=?', rid)[0])

    def test_two_allowance_failures_never_block_and_switch_provider(self):
        _, tid = self.task()
        for _ in range(2):
            self.spent(tid, 'exit 1: rate limit reached')
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        self.assertEqual((m['attempts'], m['phase']), (0, 'revise'))
        self.assertEqual(self.b.task(tid)['status'], 'claimed')
        with patch.object(fe_sync, 'sessions', return_value=[]), patch.object(fe_sync, 'app_processes', return_value=[]):
            self.assertTrue(self.manager.queue_task(m))
        self.assertEqual(self.b.task(tid)['status'], 'in_progress')  # D214: its worker is queued
        queued = self.b.q1("SELECT * FROM pm_runs WHERE state='queued'")
        self.assertEqual(queued['provider'], 'claude')
        self.assertIn('ran out of allowance', queued['prompt'])

    def test_an_allowance_that_never_clears_still_reaches_the_owner(self):
        _, tid = self.task()
        self.b.con.execute('DELETE FROM pm_outbox')
        for _ in range(core.ALLOWANCE_SILENCE):
            self.spent(tid, 'exit 1: rate limit reached')
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 0)
        self.assertEqual(self.b.q1('SELECT attempts FROM pm_tasks')[0], 0)
        self.spent(tid, 'exit 1: rate limit reached')
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 1)
        self.assertEqual(self.b.q1('SELECT attempts FROM pm_tasks')[0], 1)

    def test_model_prose_about_a_credit_limit_is_not_an_allowance(self):
        # A worker's own words about a credit limit must not pause a provider or cost
        # an attempt; short of landed, it simply resumes.
        _, tid = self.task('working')
        prose = 'the manager must not block a goal when ChatGPT reports a credit limit.'
        r = self.new_run(tid, data=result(summary=prose), provider='claude')
        clean = lambda _, *a: 'a'*40 if a[0] == 'rev-parse' else ''
        with patch.object(fe_sync, 'git_out', side_effect=clean), patch.object(board, 'on_trunk', return_value=False):
            self.manager.completed(r)
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        self.assertEqual((m['attempts'], m['phase']), (0, 'revise'))
        self.assertIsNone(self.b.q1('SELECT * FROM pm_providers'))

    def test_a_goal_is_named_so_its_tasks_say_what_they_belong_to(self):
        # D193: the planner names the goal; its tasks, their threads and prompts carry it.
        store.receive(self.b, 'owner:plan', 'reply', 'set up the knit regeneration per prototype')
        self.manager.input_events()
        gid = self.b.q1('SELECT id FROM pm_goals')[0]
        self.assertIn('goal_name', self.manager.prompt('planner', goal=gid))
        rid = store.queue_run(self.b, 'planner', 'codex', self.root, 'prompt', goal=gid)
        plan = result('planned', goal_name='Per-prototype  Knit',
                      tasks=[dict(title='Profiles', body='Scope and acceptance', problem='', reasoning='Why', depends=[])])
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=? WHERE id=?", (json.dumps(plan), rid))
        self.manager.reconcile()
        self.assertEqual(store.goal_label(self.b, gid), f'G{gid} · Per-prototype Knit')
        store.receive(self.b, 'owner:ok', 'plan', rid[:12], goal=gid)  # D226: Approve plan
        self.manager.input_events()
        tid = self.b.q1('SELECT task_id FROM pm_tasks WHERE goal_id=?', gid)[0]
        self.assertIn(f'for G{gid} · Per-prototype Knit', self.b.q1(
            "SELECT body FROM pm_outbox WHERE dedup=?", f'task:{tid}')[0])
        self.b.con.execute('UPDATE pm_tasks SET agent=? WHERE task_id=?', ('A2', tid))
        self.assertIn(f'(G{gid} · Per-prototype Knit)', self.manager.prompt('worker', tid))
        # Yotam's rename stands; a later planner's name does not replace it
        with self.b.tx():
            store.name_goal(self.b, gid, 'V165 improvements')
            store.name_goal(self.b, gid, 'Something else', keep=True)
        self.assertEqual(store.goal_label(self.b, gid), f'G{gid} · V165 improvements')
        # a worker result without the field (an older release's) still validates
        old = result()
        self.assertEqual(workers.validate_result(old)['goal_name'], '')

    def test_planner_allowance_failure_leaves_the_goal_planning(self):
        store.receive(self.b, 'owner:plan', 'reply', 'Plan a bounded tool improvement')
        self.manager.input_events()
        gid = self.b.q1('SELECT id FROM pm_goals')[0]

        def planner_fails(error):
            rid = store.queue_run(self.b, 'planner', 'codex', self.root, 'prompt', goal=gid)
            self.b.con.execute("UPDATE pm_runs SET state='failed',error=? WHERE id=?", (error, rid))
            self.manager.reconcile()

        for _ in range(2):
            planner_fails('exit 1: You have run out of credits')
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals WHERE id=?', gid)[0], 'planning')
        self.assertEqual(self.b.q1("SELECT count(*) FROM pm_outbox WHERE dedup NOT LIKE 'talk-open:%'")[0], 0)
        for _ in range(2):
            planner_fails('planner produced an invalid result')
        # D226: a planning conversation waits for Yotam's next reply in its thread
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals WHERE id=?', gid)[0], 'talking')
        self.assertTrue(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup LIKE 'failure:%' AND goal_id=?", gid))

    def test_resume_uses_exact_id_and_scrubs_billing_env(self):
        r = dict(id='a'*32, provider='codex', role='worker', session='chosen-session', agent=None)
        cmd = workers.command(r, self.config, self.root)
        self.assertIn('chosen-session', cmd)
        self.assertNotIn('--last', cmd)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'must-not-inherit', 'CODEX_THREAD_ID': 'parent'}):
            env = workers.clean_env(r)
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('CODEX_THREAD_ID', env)
        self.assertIn('shell_environment_policy.set.FE_MANAGER_RUN=' + json.dumps(r['id']), cmd)
        self.assertIn('shell_environment_policy.set.FE_BOARD_DIR=' + json.dumps(str(board.board_dir())), cmd)

    def test_a_planner_reads_the_web_and_writes_nothing(self):
        """D256: the read-only runs get the web tools and keep the read-only shell."""
        for role in ('planner', 'conversation'):
            r = dict(id='a'*32, provider='claude', role=role, session=None, agent=None)
            cmd = workers.command(r, self.config, self.root)
            tools = cmd[cmd.index('--tools') + 1].split(',')
            allowed = cmd[cmd.index('--allowedTools') + 1].split(',')
            for tool in ('WebFetch', 'WebSearch'):
                self.assertIn(tool, tools)
                self.assertIn(tool, allowed)
            self.assertNotIn('Edit', tools)
            self.assertEqual(cmd[cmd.index('--permission-mode') + 1], 'dontAsk')
            cmd = workers.command(dict(r, provider='codex'), self.config, self.root)
            self.assertLess(cmd.index('--search'), cmd.index('exec'))
            self.assertIn('sandbox_mode="read-only"', cmd)
        worker = workers.command(dict(r, provider='codex', role='worker'), self.config, self.root)
        self.assertNotIn('--search', worker)

    def test_a_planner_may_run_the_lookup_tools_in_either_shell(self):
        """T94: AGENTS.md sends every agent to fe_index and fe_docs; dontAsk refused them (17 denials a week)."""
        r = dict(id='a'*32, provider='claude', role='planner', session=None, agent=None)
        allowed = workers.command(r, self.config, self.root)
        allowed = allowed[allowed.index('--allowedTools') + 1].split(',')
        for shell in ('Bash', 'PowerShell'):
            for rule in (f"{studio_config.tool_cmd('fe_index.py')} *", f"{studio_config.tool_cmd('fe_docs.py')} show *",
                         f"{studio_config.tool_cmd('fe_board.py')} task show *", 'git log *'):
                self.assertIn(f'{shell}({rule})', allowed)
        for write in ('fe_docs.py build', 'fe_board.py task new', 'fe_board.py msg', 'fe_manager.py adopt'):
            self.assertFalse([a for a in allowed if write in a], write)

    def test_a_worker_short_of_trunk_resumes_to_land_it(self):
        # T29 blocked on its second "attempt" though nothing was wrong with the work. A
        # result short of landed resumes the same session; it costs no attempt.
        _, tid = self.task('working')
        sent = self.b.q1('SELECT count(*) FROM pm_outbox')[0]
        r = self.new_run(tid, provider='claude')
        clean = lambda _, *a: 'a'*40 if a[0] == 'rev-parse' else ''
        with patch.object(fe_sync, 'git_out', side_effect=clean), patch.object(board, 'on_trunk', return_value=False):
            self.manager.completed(r)
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        self.assertEqual((m['phase'], m['attempts'], m['worker_session']), ('revise', 0, 'exact-session'))
        self.assertIn('not on origin/main', m['feedback'])
        self.assertNotEqual(self.b.task(tid)['status'], 'blocked')
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], sent)
        self.assertEqual(json.loads(self.b.q1('SELECT result FROM pm_runs WHERE id=?', r['id'])[0])['status'],
                         'continue')

    def test_a_managed_stop_hook_leaves_landing_to_the_worker(self):
        with patch.dict(os.environ, {'FE_MANAGER_RUN': 'worker'}), patch.object(fe_sync, 'git') as git:
            self.assertEqual(fe_sync.cmd_stop({}), 0)
        git.assert_not_called()

    def test_an_interactive_stop_in_a_held_checkout_does_not_push(self):
        # An interactive session in A3 while T29's worker held it: its Stop hook would
        # have rebased and pushed the run's unreviewed commits. The hold is the board's
        # (D191): the task in progress holds it with no lease row, dropped it does not.
        _, tid = self.task()
        store.release(self.b, 'task:' + str(tid))
        reg = {'remote': 'origin', 'branch': 'main', 'agents': {'A2': {'path': str(self.root)}}}
        env = {k: v for k, v in os.environ.items() if k != 'FE_MANAGER_RUN'}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(fe_sync, 'hook_repo', return_value=self.root), \
             patch.object(fe_sync, 'load_registry', return_value=reg), \
             patch.object(fe_sync, 'identify', return_value=('A2', 'path', True)), \
             patch.object(fe_sync, 'counts', return_value=(3, 6)), \
             patch.object(fe_sync, 'branch_of', return_value='main'), \
             patch.object(fe_sync, 'sync_push') as push:
            self.assertEqual(fe_sync.cmd_stop({}), 0)
            push.assert_not_called()
            self.b.update_task('owner', tid, status='blocked')
            fe_sync.cmd_stop({})
            push.assert_not_called()
            self.b.update_task('owner', tid, status='dropped')
            push.return_value = fe_sync.SyncResult(True, 'pushed')
            fe_sync.cmd_stop({})
            push.assert_called_once()

    def test_the_board_moves_a_checkout_hold(self):
        # D191: T29 and T33 were dropped on the board, failed in the manager, and kept
        # A3 and A2 leased for good. The board's status is what holds a checkout.
        _, tid = self.task(phase='failed')
        self.b.update_task('manager', tid, status='blocked')
        self.manager.reconcile()
        self.assertIn('blocked on the board', fe_sync.checkout_hold('A2'))
        self.assertIsNotNone(self.b.q1("SELECT * FROM pm_leases WHERE resource='checkout:A2'"))
        self.b.update_task('owner', tid, status='dropped')
        self.assertIsNone(fe_sync.checkout_hold('A2'))
        self.assertIsNone(self.b.q1("SELECT * FROM pm_leases WHERE resource='checkout:A2'"))
        # an older supervisor that still renews it is not believed, and the next tick removes it
        store.lease(self.b, 'checkout:A2', 'task:' + str(tid))
        self.assertIsNone(fe_sync.checkout_hold('A2'))
        self.manager.reconcile()
        self.assertIsNone(self.b.q1("SELECT * FROM pm_leases WHERE resource='checkout:A2'"))
        self.assertEqual(board.checkout_holds(self.b.con), {})
        # set in progress again, it resumes as a reply would, and holds A2 again
        self.b.update_task('owner', tid, status='in_progress')
        m = self.b.q1('SELECT phase,attempts FROM pm_tasks WHERE task_id=?', tid)
        self.assertEqual((m['phase'], m['attempts']), ('revise', 0))
        self.assertIn('in progress on the board', fe_sync.checkout_hold('A2'))

    def test_a_task_taken_out_of_the_work_ends_its_worker(self):
        _, tid = self.task()
        self.new_run(tid, state='running')
        self.assertIn('its worker is running', fe_sync.checkout_hold('A2'))
        # a run in review is still writing in its checkout: it holds it until it ends
        self.b.update_task('owner', tid, status='review')
        self.assertIsNotNone(fe_sync.checkout_hold('A2'))
        with patch.object(workers, 'stop_owned') as stop:
            self.b.update_task('owner', tid, status='ready')
        stop.assert_called_once()
        self.assertEqual(self.b.q1('SELECT state FROM pm_runs')[0], 'handled')
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks WHERE task_id=?', tid)[0], 'ready')
        self.assertIsNone(fe_sync.checkout_hold('A2'))
        # a blocked task is where Yotam put it: the manager does not start it
        self.b.update_task('owner', tid, status='blocked')
        self.b.con.execute("UPDATE pm_tasks SET phase='revise' WHERE task_id=?", (tid,))
        with patch.object(self.manager, 'queue_task') as queue:
            self.manager.schedule()
        queue.assert_not_called()

    def test_missing_evidence_cannot_reach_review(self):
        _, tid = self.task()
        r = self.new_run(tid, data=result(checks=[]))
        with patch.object(fe_sync, 'git_out', return_value='a'*40):
            self.manager.completed(r)
        self.assertNotEqual(self.b.q1('SELECT phase FROM pm_tasks')[0], 'review')

    def test_changed_head_invalidates_review(self):
        _, tid = self.task('reviewing')
        self.b.con.execute('UPDATE pm_tasks SET head=? WHERE task_id=?', ('a'*40, tid))
        r = self.new_run(tid, role='reviewer', data=result('pass'))
        with patch.object(fe_sync, 'git_out', side_effect=lambda _, *a: 'c'*40 if a[0] == 'rev-parse' else ''):
            self.manager.completed(r)
        self.assertIsNone(self.b.q1('SELECT reviewed_head FROM pm_tasks')[0])

    def test_a_leftover_reviewer_run_hands_the_task_back(self):
        _, tid = self.task('reviewing')
        r = self.new_run(tid, role='reviewer', data={'status': 'pass'})
        self.manager.completed(r)
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        self.assertEqual((m['phase'], m['attempts']), ('revise', 0))
        self.assertEqual(self.b.q1('SELECT state FROM pm_runs WHERE id=?', r['id'])[0], 'handled')

    def test_full_lifecycle_goal_to_phone_acceptance(self):
        # D188: the worker lands its own commit; nothing runs between it and Yotam.
        _, tid = self.task()
        r = self.new_run(tid)
        clean = lambda _, *a: 'a'*40 if a[0] == 'rev-parse' else ''
        with patch.object(fe_sync, 'git_out', side_effect=clean), patch.object(board, 'on_trunk', return_value=True):
            self.manager.completed(r)
        m = dict(self.b.q1('SELECT * FROM pm_tasks'))
        self.assertEqual((m['phase'], m['landed_head']), ('landed', 'a'*40))
        self.assertEqual(self.b.task(tid)['status'], 'review')
        self.assertIsNotNone(self.b.q1("SELECT 1 FROM messages WHERE topic=? AND kind='review'", f'T{tid}'))
        store.receive(self.b, 'phone:accept', 'accept', task=tid)
        self.manager.input_events()
        self.manager.reconcile()
        self.assertEqual(self.b.task(tid)['status'], 'done')
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals')[0], 'complete')

    def parking_world(self):
        """Real clones for A2 and A3 of one bare origin; three tasks: T(blocked) holds A3
        with a commit, a modified file and an untracked one, T(busy) is in progress in A2,
        T(ready) waits for a checkout."""
        git = lambda cwd, *a: subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True,
                                             text=True).stdout.strip()
        origin, a2, a3 = self.root / 'origin.git', self.root / 'worker', self.root / 'worker3'
        git(self.root, 'init', '-q', '--bare', '-b', 'main', str(origin))
        git(self.root, 'clone', '-q', str(origin), str(a2))
        git(a2, 'config', 'user.email', 't@t')
        git(a2, 'config', 'user.name', 't')
        (a2 / 'base.txt').write_text('base\n', encoding='utf-8')
        git(a2, 'add', '-A')
        git(a2, 'commit', '-qm', 'base')
        git(a2, 'push', '-q', '-u', 'origin', 'HEAD:main')
        git(self.root, 'clone', '-q', str(origin), str(a3))
        git(a3, 'config', 'user.email', 't@t')
        git(a3, 'config', 'user.name', 't')
        base = git(a3, 'rev-parse', 'HEAD')
        _, (blocked, busy, ready) = self.goal([dict(title=t, body='test', depends=[]) for t in ('Blocked', 'Busy', 'Ready')])
        for tid, agent in ((blocked, 'A3'), (busy, 'A2')):
            self.b.claim(agent, tid)
            self.b.update_task(agent, tid, status='in_progress')
            self.b.con.execute("UPDATE pm_tasks SET agent=?,phase='working',base=?,worker_provider='claude',"
                               'worker_session=? WHERE task_id=?', (agent, base, f'session-{tid}', tid))
        self.b.con.execute("UPDATE pm_tasks SET phase='blocked' WHERE task_id=?", (blocked,))
        self.b.update_task('manager', blocked, status='blocked')
        (a3 / 'work.txt').write_text('committed work\n', encoding='utf-8')
        git(a3, 'add', '-A')
        git(a3, 'commit', '-qm', 'the blocked task')
        (a3 / 'base.txt').write_text('changed, not committed\n', encoding='utf-8')
        (a3 / 'new.txt').write_text('untracked\n', encoding='utf-8')
        return git, a2, a3, blocked, busy, ready, git(a3, 'rev-parse', 'HEAD')

    def test_a_blocked_task_parks_so_its_checkout_runs_another(self):
        # D297: T96 blocked on Yotam held A3 while T98 waited with a run slot free.
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        origin, br = self.root / 'origin.git', f'fe/parked/T{blocked}'
        quiet = (patch.object(fe_sync, 'sessions', return_value=[]),
                 patch.object(fe_sync, 'app_processes', return_value=[]),
                 patch.object(fe_sync, 'load_registry', return_value=self.manager.reg))
        with contextlib.ExitStack() as stack:
            for p in quiet:
                stack.enter_context(p)
            self.manager.schedule()
            run = self.b.q1("SELECT * FROM pm_runs WHERE state='queued'")
            self.assertEqual((run['task_id'], run['agent']), (ready, 'A3'))
            self.assertEqual(git(a3, 'rev-parse', 'HEAD'), git(a3, 'rev-parse', 'origin/main'))
            self.assertEqual(git(a3, 'status', '--porcelain'), '')
            # D314: the dirty tree became one WIP commit, pushed, forced, to a real branch
            # on the bare origin -- a normal branch Yotam can open and read.
            wip_tip = git(origin, 'rev-parse', f'refs/heads/{br}')
            self.assertNotEqual(wip_tip, tip)
            self.assertEqual(git(origin, 'rev-parse', f'refs/heads/{br}^'), tip)
            self.assertEqual(git(origin, 'show', f'refs/heads/{br}:base.txt'), 'changed, not committed')
            self.assertEqual(git(origin, 'show', f'refs/heads/{br}:new.txt'), 'untracked')
            row = dict(self.b.q1('SELECT * FROM pm_parked WHERE task_id=?', blocked))
            self.assertEqual((row['branch'], row['tip'], row['wip'], row['restored']), (br, wip_tip, tip, 0))
            self.assertIn(br, row['url'])
            self.assertIsNone(self.b.q1('SELECT agent FROM pm_tasks WHERE task_id=?', blocked)[0])
            self.assertEqual(board.checkout_holds(self.b.con)['A3']['task'], ready)
            self.assertIn(br, self.b.q1("SELECT body FROM pm_outbox WHERE dedup LIKE 'park:%'")[0])
            why = {w['ref']: w['why'] for w in outlook.outlook(self.b, self.config)['waiting']}
            self.assertIn(br, why[f'T{blocked}'])
            self.assertIn('A3', why[f'T{blocked}'])
            # No local ref is kept anywhere: the branch on origin is the only copy.
            for repo in (a2, a3):
                self.assertEqual(git(repo, 'for-each-ref', 'refs/fe/parked'), '')
            # Yotam answers while A3 runs the other task; A2 frees, and the work comes back there.
            store.receive(self.b, 'discord:answer', 'reply', 'Take it as it is', task=blocked)
            self.manager.input_events()
            self.b.update_task('owner', busy, status='review')
            self.manager.schedule()
        run = self.b.q1("SELECT * FROM pm_runs WHERE state='queued' AND task_id=?", blocked)
        self.assertEqual(run['agent'], 'A2')
        self.assertIsNone(run['session'])  # a session does not move between checkouts
        self.assertIn('restored in A2', run['prompt'])
        self.assertEqual(git(a2, 'rev-parse', 'HEAD'), tip)  # the WIP commit is undone
        self.assertEqual((a2 / 'base.txt').read_text(encoding='utf-8'), 'changed, not committed\n')
        self.assertEqual((a2 / 'new.txt').read_text(encoding='utf-8'), 'untracked\n')
        self.assertEqual(self.b.task(blocked)['claimed_by'], 'A2')
        # The row stays, marked restored, so a sweep can still find and delete the branch.
        self.assertEqual(self.b.q1('SELECT restored FROM pm_parked WHERE task_id=?', blocked)[0], 1)
        self.assertEqual(git(origin, 'rev-parse', f'refs/heads/{br}'), wip_tip)
        for repo in (a2, a3):
            self.assertEqual(git(repo, 'for-each-ref', 'refs/fe/parked'), '')

    def test_a_parked_task_back_in_its_own_checkout_resumes_its_session(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        with patch.object(fe_sync, 'sessions', return_value=[]), patch.object(fe_sync, 'app_processes', return_value=[]), \
                patch.object(fe_sync, 'load_registry', return_value=self.manager.reg):
            self.manager.schedule()
            self.b.con.execute("UPDATE pm_runs SET state='handled' WHERE task_id=?", (ready,))
            self.b.update_task('owner', ready, status='review')
            store.receive(self.b, 'discord:answer', 'reply', 'Carry on', task=blocked)
            self.manager.input_events()
            self.manager.schedule()
        run = self.b.q1("SELECT * FROM pm_runs WHERE state='queued' AND task_id=?", blocked)
        self.assertEqual((run['agent'], run['session']), ('A3', f'session-{blocked}'))
        self.assertIn('restored exactly as you left them', run['prompt'])
        self.assertEqual(git(a3, 'rev-parse', 'HEAD'), tip)
        self.assertIn('new.txt', git(a3, 'status', '--porcelain'))

    def test_restoring_in_another_checkout_after_the_original_clone_is_gone(self):
        # D314: nothing travels through the clone it was parked in -- restore fetches
        # the branch from origin, so it still works once that clone no longer exists.
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        (a3 / 'binary.dat').write_bytes(b'\x00\xff\r\n\x01\n')
        (a3 / 'mixed.txt').write_bytes(b'LF\nCRLF\r\n')
        expected = {p.name: p.read_bytes() for p in a3.iterdir() if p.is_file()}
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked, why='other work'))
        shutil.rmtree(a3, ignore_errors=True)
        note = park.restore(self.b, self.manager.reg, 'A2', blocked)
        self.assertIn('restored in A2', note)
        self.assertEqual({p.name: p.read_bytes() for p in a2.iterdir() if p.is_file()}, expected)
        self.assertEqual(git(a2, 'rev-parse', 'HEAD'), tip)
        self.assertEqual((a2 / 'base.txt').read_text(encoding='utf-8'), 'changed, not committed\n')
        self.assertEqual((a2 / 'new.txt').read_text(encoding='utf-8'), 'untracked\n')
        self.assertEqual(self.b.q1('SELECT restored FROM pm_parked WHERE task_id=?', blocked)[0], 1)

    def test_parked_branch_is_deleted_once_the_task_is_done(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked, why='other work'))
        origin, br = self.root / 'origin.git', f'fe/parked/T{blocked}'
        git(origin, 'rev-parse', f'refs/heads/{br}')  # exists while the task is not done
        # The row remains after restore, including through review, until acceptance.
        park.restore(self.b, self.manager.reg, 'A2', blocked)
        self.b.update_task('owner', blocked, status='review')
        park.sweep(self.b, self.manager.reg)
        git(origin, 'rev-parse', f'refs/heads/{br}')
        self.b.con.execute("UPDATE tasks SET status='done' WHERE id=?", (blocked,))
        # Also remove refs left by D297 or by a branch fetch in either checkout.
        for repo in (a2, a3):
            for ref in (f'refs/fe/parked/T{blocked}', f'refs/fe/parked/T{blocked}-wip',
                        f'refs/heads/{br}', f'refs/remotes/origin/{br}'):
                git(repo, 'update-ref', ref, tip)
        self.manager.reconcile()
        with self.assertRaises(subprocess.CalledProcessError):
            git(origin, 'rev-parse', f'refs/heads/{br}')
        self.assertIsNone(self.b.q1('SELECT 1 FROM pm_parked WHERE task_id=?', blocked))
        for repo in (a2, a3):
            self.assertEqual(git(repo, 'for-each-ref', 'refs/fe/parked', f'refs/heads/{br}',
                                 f'refs/remotes/origin/{br}'), '')
        # A sweep with the row already gone, or the branch already deleted, is a no-op.
        park.sweep(self.b, self.manager.reg)

    def test_parking_writes_the_migrated_table_by_column_name(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        expected = (a3 / 'new.txt').read_bytes()
        self.b.con.execute('DROP TABLE pm_parked')
        self.b.con.execute('CREATE TABLE pm_parked(task_id INTEGER PRIMARY KEY, agent TEXT NOT NULL, '
                           'path TEXT NOT NULL, tip TEXT NOT NULL, wip TEXT, created REAL NOT NULL)')
        park.ensure_tables(self.b.con)
        park.ensure_tables(self.b.con)
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked))
        row = park.parked(self.b, blocked)
        self.assertEqual((row['branch'], row['wip'], row['restored']), (park.branch(blocked), tip, 0))
        park.restore(self.b, self.manager.reg, 'A2', blocked)
        self.assertEqual(git(a2, 'rev-parse', 'HEAD'), tip)
        self.assertEqual((a2 / 'new.txt').read_bytes(), expected)

    def test_existing_local_parking_is_published_before_restore(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        git(a3, 'stash', 'push', '--include-untracked')
        wip = git(a3, 'rev-parse', 'stash@{0}')
        git(a3, 'update-ref', f'refs/fe/parked/T{blocked}', tip)
        git(a3, 'update-ref', f'refs/fe/parked/T{blocked}-wip', wip)
        git(a3, 'stash', 'drop')
        git(a3, 'reset', '--hard', 'origin/main')
        self.b.con.execute('DROP TABLE pm_parked')
        self.b.con.execute('CREATE TABLE pm_parked(task_id INTEGER PRIMARY KEY, agent TEXT NOT NULL, '
                           'path TEXT NOT NULL, tip TEXT NOT NULL, wip TEXT, created REAL NOT NULL)')
        self.b.con.execute('INSERT INTO pm_parked VALUES(?,?,?,?,?,?)',
                           (blocked, 'A3', str(a3), tip, wip, time.time()))
        park.ensure_tables(self.b.con)
        park.sweep(self.b, self.manager.reg)
        row = park.parked(self.b, blocked)
        self.assertEqual((row['branch'], row['wip']), (park.branch(blocked), tip))
        git(self.root / 'origin.git', 'rev-parse', f'refs/heads/{row["branch"]}')
        shutil.rmtree(a3, ignore_errors=True)
        park.restore(self.b, self.manager.reg, 'A2', blocked)
        self.assertEqual(git(a2, 'rev-parse', 'HEAD'), tip)
        self.assertEqual((a2 / 'base.txt').read_text(encoding='utf-8'), 'changed, not committed\n')
        self.assertEqual((a2 / 'new.txt').read_text(encoding='utf-8'), 'untracked\n')

    def test_parking_a_clean_tree_does_not_add_a_wip_commit(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        git(a3, 'reset', '--hard', tip)
        (a3 / 'new.txt').unlink()
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked))
        self.assertIsNone(park.parked(self.b, blocked)['wip'])
        self.assertEqual(git(self.root / 'origin.git', 'rev-parse', f'refs/heads/{park.branch(blocked)}'), tip)
        park.restore(self.b, self.manager.reg, 'A2', blocked)
        self.assertEqual(git(a2, 'rev-parse', 'HEAD'), tip)
        self.assertEqual(git(a2, 'status', '--porcelain'), '')

    def test_failed_park_preserves_head_staging_and_file_bytes(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        git(a3, 'add', 'base.txt')
        (a3 / 'base.txt').write_bytes(b'working bytes after staging\n')
        (a3 / 'staged.txt').write_bytes(b'staged addition\n')
        git(a3, 'add', 'staged.txt')
        (a3 / 'work.txt').unlink()
        before_status = git(a3, 'status', '--porcelain')
        index = a3 / '.git' / 'index'
        before_index = index.read_bytes()
        before_files = {p.name: p.read_bytes() for p in a3.iterdir() if p.is_file()}
        real_git = park._git
        for step in ('add', 'commit-tree', 'push', 'reset'):
            failed = False

            def fail_once(path, *args, **kw):
                nonlocal failed
                if not failed and args[0] == step and (step != 'reset' or args[-1] == 'origin/main'):
                    failed = True
                    if step in ('add', 'reset'):
                        real_git(path, *args, **kw)  # fail after the mutation, including a destructive reset
                    raise RuntimeError('injected ' + step + ' failure')
                return real_git(path, *args, **kw)

            with self.subTest(step=step), patch.object(park, '_git', side_effect=fail_once):
                self.assertFalse(park.park(self.b, self.manager.reg, 'A3', blocked))
            self.assertTrue(failed)
            self.assertEqual(index.read_bytes(), before_index)
            self.assertEqual(git(a3, 'rev-parse', 'HEAD'), tip)
            self.assertEqual(git(a3, 'status', '--porcelain'), before_status)
            self.assertEqual({p.name: p.read_bytes() for p in a3.iterdir() if p.is_file()}, before_files)
            self.assertIsNone(park.parked(self.b, blocked))
            self.assertEqual(self.b.task(blocked)['status'], 'blocked')
            self.assertEqual(self.b.q1('SELECT agent FROM pm_tasks WHERE task_id=?', blocked)[0], 'A3')

    def test_a_dropped_parked_branch_retries_failed_deletion(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked))
        self.b.update_task('owner', blocked, status='dropped')
        with patch.object(park, '_git', side_effect=RuntimeError('origin unreachable')):
            park.sweep(self.b, self.manager.reg)
        self.assertIsNotNone(park.parked(self.b, blocked))
        br = f'refs/heads/{park.branch(blocked)}'
        git(self.root / 'origin.git', 'rev-parse', br)
        park.sweep(self.b, self.manager.reg)
        self.assertIsNone(park.parked(self.b, blocked))
        self.assertEqual(git(self.root / 'origin.git', 'for-each-ref', br), '')

    def test_a_second_park_replaces_the_branch_and_ignores_evidence(self):
        git, a2, a3, blocked, busy, ready, tip = self.parking_world()
        (a3 / '.git' / 'info' / 'exclude').write_text('ignored.txt\n', encoding='utf-8')
        (a3 / 'ignored.txt').write_bytes(b'local evidence\n')
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked))
        first = park.parked(self.b, blocked)['tip']
        park.restore(self.b, self.manager.reg, 'A3', blocked)
        (a3 / 'new.txt').write_bytes(b'revised work\n')
        self.assertTrue(park.park(self.b, self.manager.reg, 'A3', blocked))
        second = park.parked(self.b, blocked)['tip']
        self.assertNotEqual(first, second)
        origin, br = self.root / 'origin.git', f'refs/heads/{park.branch(blocked)}'
        self.assertEqual(git(origin, 'rev-parse', br), second)
        self.assertEqual(git(origin, 'rev-parse', br + '^'), tip)
        self.assertNotIn('ignored.txt', git(origin, 'ls-tree', '--name-only', br))
        self.assertEqual(git(a3, 'status', '--porcelain'), '')
        self.assertEqual((a3 / 'ignored.txt').read_bytes(), b'local evidence\n')

    def test_a_blocked_task_stays_put_while_someone_works_in_its_checkout(self):
        _, _, a3, blocked, _, ready, tip = self.parking_world()
        turn = [{'id': 's', 'agent': 'A3', 'state': 'working', 'heartbeat': time.time()}]
        with patch.object(fe_sync, 'sessions', return_value=turn), patch.object(fe_sync, 'app_processes', return_value=[]):
            self.manager.schedule()
        self.assertIsNone(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        self.assertEqual(board.checkout_holds(self.b.con)['A3']['task'], blocked)
        self.assertIn('new.txt', subprocess.run(['git', 'status', '--porcelain'], cwd=a3, capture_output=True,
                                                text=True).stdout)

    def test_busy_checkout_never_assigned_and_primary_reserved(self):
        with patch.object(fe_sync, 'sessions', return_value=[]), patch.object(fe_sync, 'app_processes', return_value=[]), \
             patch.object(fe_sync, 'checkout_state', return_value=(['busy'], [], 0)) as check:
            self.assertIsNone(self.manager.free_agent())
        self.assertEqual([c.args[0] for c in check.call_args_list], ['A2', 'A3'])

    def gpu_world(self, replies, alive=None):
        """fe_gpu against fake games: `replies(port, line)` answers the control channel."""
        sent = []

        def send(port, line, timeout=60):
            sent.append((port, line))
            return replies(port, line)
        reg = {'A1': {'path': str(self.root / 'primary'), 'port': 45333, 'primary': True},
               'A2': {'path': str(self.root / 'worker'), 'port': 45444}}
        real_alive = fe_gpu.pid_alive
        stack = contextlib.ExitStack()
        for p in (patch.object(fe_gpu, 'send', side_effect=send),
                  patch.object(fe_identity, 'registry', return_value=reg),
                  patch.object(fe_identity, 'agent_name', return_value='A1'),
                  patch.object(fe_gpu, 'pid_alive', side_effect=lambda pid: (alive or {}).get(pid, real_alive(pid))),
                  patch.object(fe_gpu.time, 'sleep')):
            stack.enter_context(p)
        return stack, sent

    def a2_game(self, pid=700):
        return (pid, str(self.root / 'worker/engine/target/release/app.exe'),
                'engine\\target\\release\\app.exe --hidden --control 45444')

    def test_a_debugging_launch_shares_the_gpu(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        store.control(self.b, 'resume')
        stack, sent = self.gpu_world(lambda port, line: 'ok')
        with stack, patch.dict(os.environ, {'FE_MANAGER_RUN': r['id']}), \
             patch.object(fe_gpu, 'games', return_value=[self.a2_game()]), \
             patch.object(fe_gpu, 'run_hidden', return_value=0) as call:
            self.assertEqual(fe_manager.gpu(['--', 'app.exe', '--hidden']), 0)
        call.assert_called_once()  # at once, beside the other game, nothing frozen or held
        self.assertIn('FE_MANAGER_GPU', call.call_args.kwargs['env'])
        self.assertEqual(sent, [])
        self.assertIsNone(self.b.q1("SELECT * FROM pm_leases WHERE resource='gpu'"))

    def test_a_game_with_no_gpu_adapter_and_no_nightly_job(self):
        """The studio in another game: with no [adapters] gpu the wrapper runs the command at
        once, and with no [[nightly]] job nothing holds the work and the crew is due from 06:00."""
        import manager_crew
        import manager_nightly
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        with patch.dict(os.environ, {'FE_MANAGER_RUN': r['id']}), \
                patch.object(fe_manager.studio_config, 'adapter', return_value=None):
            self.assertEqual(fe_manager.gpu(['--', sys.executable, '-c', 'raise SystemExit(3)']), 3)
        tz = datetime.datetime.now().astimezone().tzinfo
        with patch.object(manager_nightly, 'jobs', return_value=[]):
            self.assertIsNone(manager_nightly.held(self.b))
            self.assertFalse(manager_crew.due(self.b, dict(nightly_crew='shadow'),
                                              datetime.datetime(2026, 10, 3, 5, 0, tzinfo=tz)))
            self.assertTrue(manager_crew.due(self.b, dict(nightly_crew='shadow'),
                                             datetime.datetime(2026, 10, 3, 6, 0, tzinfo=tz)))
            self.assertFalse(manager_nightly.view(self.b)['bench']['running'])

    def test_a_bench_freezes_the_other_games_runs_and_thaws_them(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        store.control(self.b, 'resume')
        stack, sent = self.gpu_world(lambda port, line: 'ok frozen pid 700 at frame 9' if line.startswith('freeze')
                                     else 'ok thawed')
        seen = {}

        def bench(argv, env=None):
            seen['claims'] = fe_gpu.live()
            seen['notice'] = fe_gpu.notice()  # the bench is not told about its own claim
            seen['env'] = env
            return 0
        with stack, patch.dict(os.environ, {'FE_MANAGER_RUN': r['id']}), \
             patch.object(fe_gpu, 'games', return_value=[self.a2_game()]), \
             patch.object(fe_gpu, 'run_hidden', side_effect=bench):
            self.assertEqual(fe_manager.gpu(['--bench', '--', 'python', 'test-x.py']), 0)
        self.assertEqual(sent[0][0], 45444)
        self.assertTrue(sent[0][1].startswith(f'freeze {fe_gpu.LEASE_S} A1'))
        self.assertEqual(sent[-1], (45444, 'thaw'))
        self.assertEqual([c['state'] for c in seen['claims']], ['running'])
        self.assertIn('FE_GPU_BENCH', seen['env'])
        self.assertEqual(seen['env']['FE_GPU_BENCH'], str(seen['claims'][0]['id']))
        self.assertIn('FE_MANAGER_GPU', seen['env'])
        self.assertEqual(self.b.q1('SELECT state FROM gpu_claims')[0], 'done')
        self.assertEqual(fe_gpu.live(), [])
        # A bench named in the command is one too, without --bench.
        stack, sent = self.gpu_world(lambda port, line: 'ok frozen pid 700' if line.startswith('freeze') else 'ok')
        with stack, patch.dict(os.environ, {'FE_MANAGER_RUN': r['id']}), \
             patch.object(fe_gpu, 'games', return_value=[self.a2_game()]), \
             patch.object(fe_gpu, 'run_hidden', return_value=0):
            fe_manager.gpu(['--', 'app.exe', '--bench', '1000'])
        self.assertTrue(any(line.startswith('freeze') for _, line in sent))

    def test_a_game_that_cannot_freeze_has_its_owner_asked_and_the_bench_waits(self):
        stack, sent = self.gpu_world(lambda port, line: 'err unknown command freeze')
        with stack, patch.object(fe_gpu, 'games', side_effect=[[self.a2_game()], [self.a2_game()], []]), \
             patch.object(fe_gpu, 'run_hidden', return_value=0) as call:
            self.assertEqual(fe_gpu.bench(['app.exe', '--bench', '600']), 0)
        call.assert_called_once()
        asks = self.b.q("SELECT * FROM messages WHERE recipient='A2' AND reply_to IS NULL ORDER BY id")
        self.assertEqual(len(asks), 1)  # asked once, however long it waited
        self.assertEqual(asks[0]['kind'], 'blocker')
        self.assertIn('fe_gpu.py wait', asks[0]['body'])
        done = self.b.q("SELECT * FROM messages WHERE reply_to=?", asks[0]['id'])
        self.assertIn('yours again', done[0]['body'])

    def test_yotams_game_freezes_the_agents_and_an_idle_one_is_closed_for_them(self):
        alive = {900: True, 700: True}

        def replies(port, line):
            if port == 45310 and line == 'idle':
                return 'ok idle 400.0 s frozen no'
            if port == 45310 and line.startswith('save'):
                return 'ok saved'
            if port == 45310 and line == 'quit':
                alive[900] = False
                return 'ok'
            return 'ok frozen pid 700' if line.startswith('freeze') else 'ok thawed'
        stack, sent = self.gpu_world(replies, alive)
        play = (900, str(self.root / 'primary/engine/target/play/release/app.exe'), 'app.exe')
        with stack, patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}), \
             patch.object(fe_gpu, 'games', return_value=[play, self.a2_game()]):
            fe_gpu.player(900, 45310)
        lines = [line for _, line in sent]
        self.assertTrue(lines[0].startswith("freeze 30 Yotam's game"))
        self.assertFalse(any(port == 45310 and line.startswith('freeze') for port, line in sent))
        self.assertTrue(any(line.startswith('save ') for line in lines))
        self.assertEqual(sent[-1], (45444, 'thaw'))
        note = self.b.q1("SELECT * FROM messages WHERE recipient='owner'")
        self.assertIn('idle-closed', note['body'])
        self.assertEqual(fe_gpu.live(), [])

    def test_a_bench_waits_behind_yotams_game(self):
        with self.b.tx() as c:
            c.execute(fe_gpu.TABLE)
        stack, _ = self.gpu_world(lambda port, line: 'ok')
        with stack:
            fe_gpu.claim(self.b, 'player', 'game PID 900', 45310, state='running')
            self.assertIn("Yotam's game has the GPU", fe_gpu.notice())
            with patch.object(fe_gpu, 'run_hidden') as call:
                self.assertEqual(fe_gpu.share(['app.exe'], should_stop=lambda: True), 130)
            call.assert_not_called()

    def test_run_hidden_relays_the_child_s_output(self):
        """A plain subprocess.call(creationflags=NO_WINDOW) comes back with the child's stdout
        inherited but unreadable whenever the caller's own stdout is not a console CreateProcess
        can attach it to (the manager's service, a git-bash shell) -- the bug every `run.py`
        workaround was written around. run_hidden captures it through a pipe instead."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fe_gpu.run_hidden([sys.executable, '-c', "print('relayed')"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), 'relayed')

    def test_the_hook_tells_a_launch_from_a_command_that_names_one(self):
        # The game's GPU adapter (studio.toml [adapters] gpu) reads the command for fe_sync's hook.
        self.assertEqual(fe_gpu.gpu_runs('grep -rl -- "--bench" engine/tools/*.py | head -5'), [])
        self.assertEqual(fe_gpu.gpu_runs("Get-CimInstance Win32_Process -Filter \"Name='app.exe'\""), [])
        runs = fe_gpu.gpu_runs('cd D:/x && FE_PREWARM=0 FE_BENCH_CSV=a.csv "$TEMP/p/release/app.exe" --bench 1000')
        self.assertEqual(len(runs), 1)
        self.assertIn('--bench', runs[0])
        self.assertEqual(len(fe_gpu.gpu_runs('python engine/tools/test-spotlight.py --bench')), 1)
        self.assertIs(fe_sync.studio_config.adapter('gpu'), fe_gpu)

    def test_fe_py_waits_out_a_frozen_game(self):
        with patch.object(fe, 'send', side_effect=['err frozen: A1\'s bench #3; commands wait', 'ok frame 5']) as s, \
             patch.object(fe.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(fe.main(['--port', '45444', 'status']), 0)
        self.assertEqual(s.call_count, 2)
        self.assertIn('ok frame 5', out.getvalue())
        self.assertIn('waits until the game thaws', err.getvalue())

    def test_a_task_past_its_hours_is_told_once_then_ended_and_blocked(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        now = time.time()
        started = now - store.TASK_HOURS * 3600 + 60
        self.b.con.execute('UPDATE pm_runs SET pid_started=?, heartbeat=? WHERE id=?', (started, now, r['id']))
        self.assertEqual(store.overtime_note(self.b, r['id']), '')  # a minute to go
        self.b.con.execute('UPDATE pm_runs SET pid_started=? WHERE id=?', (started - 120, r['id']))
        note = store.overtime_note(self.b, r['id'])
        self.assertIn('Stop now', note)
        self.assertIn('"blocked"', note)
        self.assertIn('Write no report', note)  # D321: the manager writes it
        self.assertEqual(store.overtime_note(self.b, r['id']), '')  # told once
        # Yotam's answer on the task starts a fresh three hours.
        store.receive(self.b, 'reply:1', 'reply', 'carry on', tid)
        self.assertLess(store.task_clock(self.b, tid)[0], 1)
        self.b.con.execute('DELETE FROM pm_inbox')
        # Not returned by the end of the grace: ended, reported from its log, blocked on Yotam.
        self.b.con.execute('UPDATE pm_runs SET pid_started=? WHERE id=?',
                           (now - store.TASK_HOURS * 3600 - store.OVERTIME_GRACE_S - 60, r['id']))
        with patch.object(workers, 'owned_process', return_value=Mock()), \
             patch.object(workers, 'stop_owned') as stop, \
             patch.object(workers, 'last_activity', return_value=['ran: python test-knot.py', 'said: bisecting']):
            self.manager.reconcile()
        stop.assert_called_once()
        self.assertEqual(self.b.q1('SELECT state FROM pm_runs WHERE id=?', r['id'])[0], 'handled')
        self.assertEqual(self.b.task(tid)['status'], 'blocked')
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks WHERE task_id=?', tid)[0], 'blocked')
        ask = self.b.q1("SELECT * FROM messages WHERE recipient='owner' AND subject LIKE '%out of time%'")
        self.assertIn('bisecting', ask['body'])  # an empty log: the report falls back to its last steps
        self.assertIn('the manager ended its run', ask['body'])
        self.assertIsNotNone(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup=?", 'timeout:' + r['id']))
        # The wrapper's own late update does not undo it.
        self.b.con.execute("UPDATE pm_runs SET state='failed' WHERE id=? AND state='running'", (r['id'],))
        self.assertEqual(self.b.q1('SELECT state FROM pm_runs WHERE id=?', r['id'])[0], 'handled')

    def test_the_last_activity_reads_either_providers_log(self):
        rid = 'ab' * 16
        folder = workers.run_dir(rid)
        folder.mkdir(parents=True)
        lines = [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Testing the idea.'},
                                                               {'type': 'tool_use', 'name': 'Bash',
                                                                'input': {'command': 'python fe.py shot a.png'}}]}},
                 {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'cargo test'}},
                 {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Done with the bench.'}}]
        (folder / 'events.jsonl').write_text('\n' + '\n'.join(json.dumps(x) for x in lines) + '\n', encoding='utf-8')
        self.assertEqual(workers.last_activity(rid), ['said: Testing the idea.', 'ran: python fe.py shot a.png',
                                                      'ran: cargo test', 'said: Done with the bench.'])

    def log(self, rid, events):
        folder = workers.run_dir(rid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'events.jsonl').write_text(chr(10).join(json.dumps(x) for x in events) + chr(10), encoding='utf-8')

    def claude_tool(self, i, name, **inp):
        return {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': f't{i}', 'name': name,
                                                              'input': inp}]}}

    def claude_result(self, i, text, error=False):
        return {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': f't{i}',
                                                         'content': text, 'is_error': error}]}}

    def test_the_overtime_report_reads_what_blocked_a_claude_run_from_its_log(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running', provider='claude')
        events = [self.claude_tool(1, 'Edit', file_path='engine/a.rs', old_string='x', new_string='y'),
                  self.claude_result(1, 'ok'),
                  self.claude_tool(2, 'Bash', command='git commit -m x'),
                  self.claude_result(2, '[main abc1234] x 1 file changed'),
                  *[e for i in (3, 4, 5) for e in (self.claude_tool(i, 'Bash', command='python fe_bench.py compare HEAD'),
                                                  self.claude_result(i, 'bench' + chr(10) + 'hitch limit exceeded 4.2 ms', True))],
                  {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'The hitch is still there.'}]}}]
        self.log(r['id'], events)
        with patch.object(fe_sync, 'git_out', return_value=''):
            text = report.blocked_report(self.b, tid, r, ended_by_manager=True)
        self.assertIn('the manager ended its run', text)
        self.assertIn('failed 3 times: python fe_bench.py compare HEAD  ->  hitch limit exceeded 4.2 ms', text)
        self.assertIn('ran 3 times (3 failed): python fe_bench.py compare HEAD', text)
        self.assertIn('3 benches and regressions (3 failed)', text)
        self.assertIn('1 file(s) edited: engine/a.rs', text)
        self.assertIn('committed: abc1234', text)
        self.assertIn('it last said: The hitch is still there.', text)
        self.assertIn('0 modified file(s)', text)

    def test_the_overtime_report_reads_a_codex_log_too(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        item = lambda t, **kw: {'type': 'item.completed', 'item': dict(type=t, **kw)}
        self.log(r['id'], [
            {'type': 'item.started', 'item': {'type': 'command_execution', 'id': 'c1', 'command': 'cargo test'}},
            item('command_execution', id='c1', command='cargo test', aggregated_output='boom' + chr(10) + 'test failed', exit_code=101),
            item('file_change', id='f1', changes=[{'path': 'engine/b.rs', 'kind': 'update'}]),
            {'type': 'turn.failed', 'error': {'message': 'Selected model is at capacity.'}}])
        with patch.object(fe_sync, 'git_out', return_value=' M engine/b.rs'):
            text = report.blocked_report(self.b, tid, r, ended_by_manager=False, agent_note='Out of ideas.')
        self.assertIn('stopped by itself', text)
        self.assertIn('failed: cargo test  ->  test failed', text)
        self.assertIn('the log ends with turn.failed', text)
        self.assertIn('update engine/b.rs'.split()[1], text)
        self.assertTrue(text.endswith("The worker's own note:" + chr(10) + "Out of ideas."))

    def test_the_overtime_report_falls_back_when_the_log_will_not_read(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        with patch.object(report, 'scan', side_effect=ValueError('bad log')),              patch.object(workers, 'last_activity', return_value=['ran: x']):
            text = report.blocked_report(self.b, tid, r, ended_by_manager=True)
        self.assertIn('- ran: x', text)

    def blocked_by_itself_after(self, hours):
        _, tid = self.task()
        data = result('blocked', summary='Stopping.', asks=[])
        r = self.new_run(tid, data=data, state='finished', provider='claude')
        now = time.time()
        self.b.con.execute('UPDATE pm_runs SET pid_started=?, heartbeat=? WHERE id=?',
                           (now - hours * 3600, now, r['id']))
        self.log(r['id'], [self.claude_tool(1, 'Bash', command='cargo test'), self.claude_result(1, 'no', True)])
        with patch.object(fe_sync, 'git_out', return_value=''):
            self.manager.completed(dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', r['id'])))
        return self.b.q1("SELECT body FROM messages WHERE recipient='owner' AND subject=?",
                         f'T{tid}: decision needed')['body']

    def test_a_worker_that_blocks_itself_past_its_hours_gets_the_managers_report(self):
        body = self.blocked_by_itself_after(store.TASK_HOURS + 0.2)
        self.assertIn('What blocked it:', body)
        self.assertIn('failed: cargo test', body)
        self.assertIn("The worker's own note:" + chr(10) + 'Stopping.', body)

    def test_a_worker_that_blocks_itself_inside_its_hours_keeps_its_own_report(self):
        self.assertEqual(self.blocked_by_itself_after(0.5), 'Stopping.')

    def test_discord_auth_rejects_other_users_servers_channels_and_fake_threads(self):
        self.assertTrue(transport.authorized(self.config, 33, 11, 22))
        self.assertTrue(transport.authorized(self.config, 33, 11, 44, 22, ['44']))
        for args in [(34, 11, 22), (33, 12, 22), (33, 11, 45, 22, ['44']), (33, None, 22)]:
            self.assertFalse(transport.authorized(self.config, *args))

    def test_notification_outbox_deduplicates_and_survives_restart(self):
        store.notify(self.b, 'once', 'hello', ping=True)
        store.notify(self.b, 'once', 'hello', ping=True)
        self.b.close()
        self.b = board.Board()
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 1)
        self.assertIsNone(self.b.q1('SELECT sent FROM pm_outbox')[0])

    def test_a_reply_on_a_landed_task_reopens_it_and_it_stays_open(self):
        """D212: T37 and T39 were sent back with Yotam's changes, and the next reconcile
        landed them again: their head was the commit already on origin/main. They stay
        with their worker, say so, and the worker is queued with his reply."""
        _, tid = self.task(phase='landed')
        self.b.con.execute('UPDATE pm_tasks SET head=?,landed_head=? WHERE task_id=?', ('a' * 40, 'a' * 40, tid))
        self.b.update_task('manager', tid, status='review')
        store.control(self.b, 'resume')
        store.receive(self.b, 'interaction:7', 'reply', 'The tubes look like stripes now; bring the old look back.', tid)
        with patch.object(board, 'on_trunk', return_value=True), patch.object(self.manager, 'launch'), \
                patch.object(self.manager, 'queue_task', side_effect=self.queued) as queue:
            self.manager.tick()
            self.manager.tick()
        m = self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid)
        self.assertEqual(m['phase'], 'revise')
        # D214: claimed until its worker is queued (the stand-in queue here does not)
        self.assertEqual(self.b.task(tid)['status'], 'claimed')
        self.assertIn('bring the old look back', m['feedback'])
        self.assertIn('goes back to its worker', self.b.q1("SELECT body FROM pm_outbox WHERE dedup='reopen:interaction:7'")[0])
        self.assertEqual(queue.call_args[0][0]['task_id'], tid)
        o = outlook.outlook(self.b, self.config)
        self.assertNotIn(f'T{tid}', [w['ref'] for w in o['waiting'] if w['you']])
        # A new commit of its worker on origin/main still lands as before.
        self.b.con.execute("UPDATE pm_runs SET state='handled'")
        self.b.con.execute("UPDATE pm_tasks SET head=? WHERE task_id=?", ('b' * 40, tid))
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.reconcile()
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks WHERE task_id=?', tid)[0], 'landed')
        self.assertEqual(self.b.task(tid)['status'], 'review')

    def test_a_document_a_task_wrote_is_read_from_the_task(self):
        """D212: T40's answer was a plan (docs/enemy-attacks-plan.md). The documents a task's
        landing commit wrote, and the .md files its worker reported, are what it gives to
        read; the histories, the summaries and another task's commits are not."""
        repo = self.root / 'repo'
        repo.mkdir()
        git = lambda *a: subprocess.run(['git', *a], cwd=repo, check=True, capture_output=True)
        git('init', '-q')
        git('config', 'user.email', 't@t')
        git('config', 'user.name', 't')
        (repo / 'docs').mkdir()
        (repo / 'docs' / 'other.md').write_text('# Someone else\n', encoding='utf-8')
        git('add', '-A')
        git('commit', '-qm', 'another task')
        (repo / 'docs' / 'plan.md').write_text('# The attack plan\n\nTelegraph, then strike.\n', encoding='utf-8')
        (repo / 'docs' / 'decisions.md').write_text('### D1\n', encoding='utf-8')
        (repo / 'docs' / 'decisions').mkdir()
        (repo / 'docs' / 'decisions' / 'lighting.md').write_text('### D2\n', encoding='utf-8')
        (repo / 'docs' / 'decisions-summary.md').write_text('x\n', encoding='utf-8')
        (repo / 'docs' / 'old.md').write_text('# Touched before\n', encoding='utf-8')
        git('add', '-A')
        git('commit', '-qm', 'the plan')
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True).stdout.strip()
        docs = board.docs_of(repo, head, ['docs/plan.md (437 lines)', 'commit abc on origin/main'])
        self.assertEqual([d['path'] for d in docs], ['docs/old.md', 'docs/plan.md'])
        self.assertIn('Telegraph, then strike.', docs[1]['text'])
        self.assertEqual(board.docs_of(repo, head, ['docs/other.md'])[-1]['path'], 'docs/other.md')
        self.assertEqual(board.docs_of(repo, 'f' * 40), [])  # not fetched here yet
        _, tid = self.task(phase='landed')
        self.b.con.execute('UPDATE pm_tasks SET head=?,landed_head=?,evidence=? WHERE task_id=?',
                           (head, head, json.dumps({'artifacts': ['docs/plan.md']}), tid))
        self.b.update_task('manager', tid, status='review')
        self.assertEqual([d['path'] for d in board.task_docs(self.b, tid, repo)], ['docs/old.md', 'docs/plan.md'])
        with patch.object(board, 'task_docs', lambda b, t, repo_=None: board.docs_of(repo, head)):
            w = next(w for w in outlook.outlook(self.b, self.config)['waiting'] if w['ref'] == f'T{tid}')
        self.assertEqual(w['docs'], ['docs/old.md', 'docs/plan.md'])
        self.assertIn('(read docs/old.md, docs/plan.md)', w['why'])

    def test_a_pdf_among_a_tasks_artifacts_is_kept_for_discord_and_other_files_are_not(self):
        """A plan document's PDF (T55) travels like a screenshot: copied to the board's file
        store under a readable name. A fake PDF, a type off the allow-list and a file outside
        the checkout are dropped; images behave as before."""
        repo = self.root / 'art'
        (repo / 'out').mkdir(parents=True)
        (repo / 'out' / 'plan.pdf').write_bytes(b'%PDF-1.7\nbody')
        (repo / 'out' / 'fake.pdf').write_bytes(b'not a pdf')
        (repo / 'out' / 'tool.exe').write_bytes(b'MZ')
        (repo / 'out' / 'shot.png').write_bytes(b'\x89PNG\r\n\x1a\nxx')
        (self.root / 'outside.pdf').write_bytes(b'%PDF-1.7\n')
        got = core.Manager.artifacts(repo, ['out/plan.pdf', 'out/fake.pdf', 'out/tool.exe',
                                            'out/shot.png', '../outside.pdf'])
        self.assertEqual([Path(g).name for g in got][0], 'plan.pdf')
        self.assertEqual(len(got), 2)
        self.assertTrue(Path(got[0]).read_bytes().startswith(b'%PDF-'))
        self.assertTrue(Path(got[1]).suffix == '.png')
        self.assertIn('docs', Path(got[0]).parts)
        with self.assertRaises(board.BoardError):
            board.store_file(b'MZ', 'x.exe')
        with self.assertRaises(board.BoardError):
            board.store_image(b'%PDF-1.7')

    def test_a_complete_goal_closes_its_planning_thread(self):
        import discord
        gid, tid = self.task(phase='landed')
        self.b.con.execute('UPDATE pm_tasks SET landed_head=? WHERE task_id=?', ('a' * 40, tid))
        self.b.con.execute('INSERT INTO pm_goal_threads VALUES(?,?)', (gid, '888'))
        self.manager.reconcile()
        key = f'close:G{gid}'
        self.assertIsNone(self.b.q1('SELECT 1 FROM pm_outbox WHERE dedup=?', key))  # still being worked
        self.b.update_task('owner', tid, status='done')
        self.manager.reconcile()
        self.manager.reconcile()
        rows = self.b.q('SELECT dedup, goal_id, body FROM pm_outbox WHERE dedup LIKE ? ORDER BY id', f'%{gid}')
        self.assertEqual(sum(1 for r in rows if r['dedup'] == key), 1)
        self.assertEqual(rows[-1]['dedup'], key)  # after the goal-complete notice
        self.assertEqual(rows[-1]['goal_id'], gid)
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.send = AsyncMock(return_value=Mock(id=1))
            thread.edit = AsyncMock()
            client.destination = AsyncMock(return_value=thread)
            await client.deliver_row(dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', key)))
            thread.edit.assert_awaited_with(archived=True)
            await client.close()
        asyncio.run(exercise())
        # worked on again, it may be closed again
        self.b.update_task('owner', tid, status='in_progress')
        self.b.con.execute("UPDATE pm_goals SET status='active' WHERE id=?", (gid,))
        self.b.con.execute('UPDATE pm_outbox SET sent=? WHERE dedup=?', ('["1"]', key))
        self.manager.reconcile()
        self.assertIsNone(self.b.q1('SELECT 1 FROM pm_outbox WHERE dedup=?', key))

    def test_a_task_closed_on_the_board_closes_its_discord_thread(self):
        import discord
        _, tid = self.task(phase='landed')
        self.b.con.execute('UPDATE pm_tasks SET landed_head=? WHERE task_id=?', ('a' * 40, tid))
        self.b.con.execute('INSERT INTO pm_discord_threads VALUES(?,?)', (tid, '999'))
        store.notify(self.b, 'landed', 'On the main branch.', tid)
        self.b.update_task('owner', tid, status='done')
        self.manager.reconcile()
        self.manager.reconcile()
        rows = self.b.q('SELECT dedup, body FROM pm_outbox WHERE task_id=? ORDER BY id', tid)
        self.assertEqual([r['dedup'] for r in rows][-2:], ['landed', f'close:{tid}'])
        self.assertIn('done on the board', rows[-1]['body'])
        # the transport archives the thread once that line is sent, and only then
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.send = AsyncMock(return_value=Mock(id=1))
            thread.edit = AsyncMock()
            client.destination = AsyncMock(return_value=thread)
            for dedup in ('landed', f'close:{tid}'):
                await client.deliver_row(dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', dedup)))
                self.assertEqual(thread.edit.await_count, 0 if dedup == 'landed' else 1)
            thread.edit.assert_awaited_with(archived=True)
            await client.close()
        asyncio.run(exercise())
        # reopened, it may be closed again
        self.b.update_task('owner', tid, status='in_progress')
        self.manager.reconcile()
        self.assertIsNone(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', f'close:{tid}'))
        self.b.update_task('owner', tid, status='dropped')
        self.manager.reconcile()
        self.assertIn('dropped', self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', f'close:{tid}')[0])

    def test_discord_send_ack_crash_does_not_duplicate(self):
        import discord
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            channel = Mock()
            embed = discord.Embed(description='Already sent')
            embed.set_footer(text='pm-event:1:0')
            async def history(**kw):
                yield Mock(author=Mock(id=777), embeds=[embed], id=555)
            channel.history = history
            channel.send = AsyncMock()
            client.destination = AsyncMock(return_value=channel)
            store.notify(self.b, 'once', 'Already sent')
            row = dict(self.b.q1('SELECT * FROM pm_outbox'))
            row['attempts'] = 1
            await client.deliver_row(row)
            channel.send.assert_not_called()
            self.assertEqual(json.loads(self.b.q1('SELECT sent FROM pm_outbox')[0]), ['555'])
            await client.close()
        asyncio.run(exercise())

    def test_a_provider_that_updated_itself_mid_service_keeps_its_slot(self):
        """D210: Codex updated into a new folder after the service read its config left the
        manager on one slot until a restart; each tick finds it again and says so."""
        new = self.root / 'bin' / 'c6fe824d' / 'codex.exe'
        new.parent.mkdir(parents=True)
        new.write_bytes(b'')
        self.config['codex'] = str(self.root / 'bin' / 'ca9abb0b' / 'codex.EXE')
        self.assertEqual(self.manager.available(), ['claude', 'codex'])
        self.assertEqual(Path(self.config['codex']).resolve(), new.resolve())
        self.assertEqual(self.manager.cap(), 2)
        store.control(self.b, 'resume')
        with patch.object(self.manager, 'launch'):
            self.manager.tick()
        self.assertEqual(json.loads(store.setting(self.b, store.SLOTS)),
                         {'slots': 2, 'providers': ['claude', 'codex']})

    def working(self, tid, gid):
        """A worker running on tid, as the service left it at its last tick."""
        rid = store.queue_run(self.b, 'worker', 'claude', self.root / 'worker', 'prompt', task=tid, goal=gid,
                              agent='A2', session='sess-1')
        self.b.con.execute("UPDATE pm_runs SET state='running',pid_started=? WHERE id=?", (time.time() - 1500, rid))
        store.set_setting(self.b, 'heartbeat', time.time())
        store.set_setting(self.b, store.SLOTS, json.dumps({'slots': store.slots(self.manager.available()),
                                                           'providers': self.manager.available()}))
        return rid

    def test_a_goal_is_planned_while_both_workers_run(self):
        """D216 (amends D210): G12 waited behind T37's and T39's workers for a run slot.
        Planners have their own lane: with both slots busy a goal is planned at once, and
        only a goal past store.PLANNERS planners waits, for a planner, saying so."""
        self.config['codex'] = str(self.root / 'gone' / 'x' / 'codex.exe')
        store.control(self.b, 'resume')
        gid, tid = self.task()
        rid = self.working(tid, gid)
        store.receive(self.b, 'owner:2', 'reply', 'Another bounded improvement')
        self.manager.input_events()
        g1 = self.b.q1("SELECT id FROM pm_goals WHERE source='owner:2'")[0]
        tid2 = store.plan_tasks(self.b, g1, [dict(title='Two', body='Test', reasoning='Why', depends=[])])[0]
        other = store.queue_run(self.b, 'worker', 'claude', self.root / 'worker3', 'prompt', task=tid2,
                                goal=gid, agent='A3')
        self.b.con.execute("UPDATE pm_runs SET state='running' WHERE id=?", (other,))
        store.receive(self.b, 'discord:9', 'reply', 'How are we looking for the next 8 hours?')
        self.manager.input_events()
        g2 = self.b.q1("SELECT id FROM pm_goals WHERE source='discord:9'")[0]
        o = outlook.outlook(self.b, self.config)
        self.assertEqual((o['slots'], o['used']), (2, 2))
        self.assertEqual(next(w for w in o['waiting'] if w['ref'] == f'G{g2}')['why'],
                         'planning starts at the next tick')
        self.assertTrue(outlook.receipt(o).startswith('Received. Planning starts now'))
        self.assertIn('codex: not found at', '\n'.join(outlook.lines(o)))
        self.assertIn('reaches it when this run ends', outlook.receipt(o, tid))
        # schedule() agrees: the planner queues beside the two workers, no worker more.
        self.manager.schedule()
        self.assertTrue(self.b.q1("SELECT 1 FROM pm_runs WHERE role='planner' AND goal_id=?", g2))
        o = outlook.outlook(self.b, self.config)
        self.assertEqual((o['slots'], o['used'], o['planners']), (2, 2, 1))
        self.assertIn('2 of 2 run slots in use · 1 planning', outlook.lines(o)[0])
        # Past store.PLANNERS, a goal waits for a planner, not for a run slot.
        for n in range(store.PLANNERS):
            store.receive(self.b, f'discord:1{n}', 'reply', f'Question {n}')
        self.manager.input_events()
        self.manager.schedule()
        self.assertEqual(self.b.q1("SELECT count(*) FROM pm_runs WHERE role='planner'")[0], store.PLANNERS)
        last = self.b.q1("SELECT max(id) FROM pm_goals")[0]
        o = outlook.outlook(self.b, self.config)
        w = next(w for w in o['waiting'] if w['ref'] == f'G{last}')
        self.assertIn(f'all {store.PLANNERS} planners are running', w['why'])
        self.assertNotIn('run slot', w['why'])
        self.assertTrue(outlook.receipt(o).startswith(f'Received. It is queued: all {store.PLANNERS} planners'))
        # The workers' slots freeing changes nothing for it; a planner ending does.
        self.b.con.execute("UPDATE pm_runs SET state='handled' WHERE id IN (?,?)", (rid, other))
        self.manager.schedule()
        self.assertFalse(self.b.q1("SELECT 1 FROM pm_runs WHERE role='planner' AND goal_id=?", last))
        self.b.con.execute("UPDATE pm_runs SET state='handled' WHERE role='planner' AND goal_id=?", (g2,))
        self.b.con.execute("UPDATE pm_goals SET status='answered' WHERE id=?", (g2,))
        self.manager.schedule()
        self.assertTrue(self.b.q1("SELECT 1 FROM pm_runs WHERE role='planner' AND goal_id=?", last))

    def test_a_planner_reads_the_trunk_not_a_checkout(self):
        """D216: a planner reads a worktree of origin/main beside the releases, never a
        checkout an agent is changing; without one it reads the manager's own repo."""
        origin, repo = self.root / 'origin.git', self.root / 'repo'
        git = lambda cwd, *a: subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True)
        git(self.root, 'init', '-q', '--bare', '-b', 'main', str(origin))
        git(self.root, 'clone', '-q', str(origin), str(repo))
        git(repo, 'config', 'user.email', 't@t')
        git(repo, 'config', 'user.name', 't')
        (repo / 'pushed.md').write_text('on the trunk\n', encoding='utf-8')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'pushed')
        git(repo, 'push', '-q', 'origin', 'HEAD:main')
        (repo / 'pushed.md').write_text('an agent is changing this\n', encoding='utf-8')
        reg = {'remote': 'origin', 'branch': 'main', 'agents': {}}
        self.config['repo'] = str(repo)
        manager = core.Manager(self.b, self.config)
        store.control(self.b, 'resume')
        store.receive(self.b, 'discord:9', 'reply', 'Plan something')
        manager.input_events()
        with patch.object(fe_sync, 'load_registry', return_value=reg):
            manager.schedule()
        cwd = Path(self.b.q1("SELECT cwd FROM pm_runs WHERE role='planner'")[0])
        self.assertEqual(cwd.parent, self.root / 'board' / 'manager' / 'releases')
        self.assertEqual((cwd / 'pushed.md').read_text(encoding='utf-8'), 'on the trunk\n')
        # No trunk to read (the repo is no git checkout): the manager's own repo, as before.
        self.assertIsNone(core.release.reading(self.root / 'nowhere'))

    def test_the_outlook_names_what_waits_on_yotam_and_on_other_tasks(self):
        _, ids = self.goal([dict(title='One', body='test', depends=[]), dict(title='Two', body='test', depends=[0]),
                            dict(title='Three', body='test', depends=[])], approve=False)
        self.b.update_task('owner', ids[0], status='ready')
        self.b.update_task('owner', ids[1], status='ready')
        self.b.con.execute("UPDATE pm_tasks SET phase='landed',landed_head=? WHERE task_id=?", ('a' * 40, ids[0]))
        self.b.update_task('manager', ids[0], status='review')
        store.control(self.b, 'resume')
        store.set_setting(self.b, 'heartbeat', time.time())
        why = {w['ref']: (w['why'], w['you']) for w in outlook.outlook(self.b, self.config)['waiting']}
        self.assertEqual(why[f'T{ids[0]}'], ('landed: waits for you to review and accept it', True))
        self.assertEqual(why[f'T{ids[1]}'], (f'waits on T{ids[0]} (review) to be accepted', False))
        self.assertEqual(why[f'T{ids[2]}'], ('a proposal: waits for you to approve it', True))
        store.control(self.b, 'pause')
        o = outlook.outlook(self.b, self.config)
        self.assertEqual(o['halt'], 'the manager is paused')
        self.assertIn('use /resume', outlook.receipt(o))

    def test_the_agents_view_shows_the_managers_worker_not_a_stale_doing(self):
        """D210: `who` names the run on a checkout and its session, and leaves out a
        `doing` said in an earlier session."""
        gid, tid = self.task()
        self.working(tid, gid)
        self.b.touch('A2', state='working', activity='T5: a floor, days ago', task_id=tid)
        self.b.con.execute("UPDATE presence SET activity_at=? WHERE agent='A2'", (time.time() - 3 * 86400,))
        now = time.time()
        self.b.con.execute('INSERT INTO sessions(id,agent,state,started,heartbeat) VALUES(?,?,?,?,?)',
                           ('sess-1', 'A2', 'working', now - 1500, now))
        out = io.StringIO()
        with patch.object(board, 'registry', return_value=self.manager.reg), \
                patch.object(board.fe_codex, 'threads', return_value=[]), \
                patch.object(board, 'manager_outlook', lambda b, steps=True: outlook.outlook(b, self.config)), \
                contextlib.redirect_stdout(out):
            board.cmd_who(self.b, 'owner', None)
        text = out.getvalue()
        self.assertIn(f"manager: T{tid}'s worker (claude)", text)
        self.assertIn(f"sess-1 = T{tid}'s worker", text.replace('Claude Code ', ''))
        self.assertNotIn('a floor, days ago', text)

    def test_discord_receipt_is_durable_deduplicated_and_owner_only(self):
        async def exercise():
            wake = asyncio.Event()
            client = transport.build_client(self.config, wake)
            message = Mock(id=1234, content='Please inspect this', attachments=[],
                           author=Mock(id=33, bot=False), guild=Mock(id=11),
                           channel=Mock(id=22, parent_id=None))
            store.control(self.b, 'resume')
            store.set_setting(self.b, 'heartbeat', time.time())  # the service is stepping
            await client.on_message(message)
            await client.on_message(message)
            self.assertTrue(wake.is_set())
            self.assertEqual(self.b.q1('SELECT count(*) FROM pm_inbox')[0], 1)
            # D226: its planning thread is its receipt, opened by the manager's next step
            self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 0)
            self.manager.input_events()
            opened = self.b.q1('SELECT * FROM pm_outbox')
            self.assertIn('Planning G', opened['body'])
            self.assertIsNotNone(opened['goal_id'])
            self.assertIsNone(opened['sent'])
            message.id = 1235
            message.author.id = 999
            await client.on_message(message)
            self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 1)
            message.author.id = 33
            store.control(self.b, 'pause')
            await client.on_message(message)
            self.assertIn('paused', self.b.q1('SELECT body FROM pm_outbox ORDER BY id DESC LIMIT 1')[0])
            await client.close()
        asyncio.run(exercise())

    def test_the_talk_channel_answers_and_changes_nothing(self):
        """D317: a message in the talk channel is answered by a read-only Sonnet turn in its
        own lane, even while paused; it opens no goal, reopens no task and takes no slot."""
        import manager_quick as quick
        _, (tid,) = self.goal()
        self.b.con.execute("UPDATE pm_tasks SET phase='landed',head=?,landed_head=? WHERE task_id=?",
                           ('b' * 40, 'b' * 40, tid))
        self.b.update_task('manager', tid, status='review')
        goals = self.b.q1('SELECT count(*) FROM pm_goals')[0]
        store.control(self.b, 'pause')

        async def exercise():
            wake = asyncio.Event()
            client = transport.build_client(self.config, wake)
            channels = [Mock(id=43), Mock(id=44)]
            channels[0].name, channels[1].name = 'general', 'meatbag-talk'
            # found by its name; what was said there before it was found is not answered
            self.assertEqual((await client.find_talk(Mock(guild=Mock(text_channels=channels)), ('view_channel',))).id, 44)
            message = Mock(id=2000, content='why did it blur the edge?', attachments=[],
                           author=Mock(id=33, bot=False), guild=Mock(id=11), channel=Mock(id=44, parent_id=None))
            await client.on_message(message)
            stranger = Mock(id=2001, content='hi', attachments=[], author=Mock(id=999, bot=False),
                            guild=Mock(id=11), channel=Mock(id=44, parent_id=None))
            await client.on_message(stranger)
            talk = Mock(id=44)
            talk.send = AsyncMock(return_value=Mock(id=5))
            client.get_channel = Mock(return_value=talk)
            with board.Board() as b:
                where = await client.destination(b, {'dedup': 'quick:abc', 'task_id': None, 'goal_id': None})
            await client.close()
            return wake.is_set(), where
        woke, where = asyncio.run(exercise())
        self.assertTrue(woke)
        self.assertEqual(where.id, 44)
        self.assertTrue(store.setting(self.b, 'discord_cursor:44'))
        self.assertIn('Ask me anything here', self.b.q1("SELECT body FROM pm_outbox WHERE dedup='quick-onboarding'")[0])
        self.assertEqual([tuple(r) for r in self.b.q("SELECT kind, body FROM pm_inbox WHERE id LIKE 'discord:%'")],
                         [('ask', 'why did it blur the edge?')])
        self.manager.input_events()
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_goals')[0], goals)  # no goal opened
        with patch.object(core.release, 'reading', return_value=self.root):
            (rid,) = quick.queue(self.manager)
            self.assertEqual(quick.queue(self.manager), [])  # one turn at a time
        run = dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', rid))
        self.assertEqual((run['role'], run['provider'], run['session'], run['task_id']), ('quick', 'claude', None, None))
        self.assertEqual(self.b.q1('SELECT tier FROM pm_run_stats WHERE run_id=?', rid)[0], 'sonnet')
        self.assertIn('why did it blur the edge?', run['prompt'])
        self.assertIn(f'T{tid} ', run['prompt'])  # what is in review, so a question about it is quick
        cmd = workers.command(run, self.config, self.root)
        self.assertIn('--allowedTools', cmd)  # it only reads
        # D325: it returns only the message he reads, never the workers' report fields
        self.assertEqual(json.loads(cmd[cmd.index('--json-schema') + 1]), workers.QUICK_SCHEMA)
        self.assertIn('explain', run['prompt'])
        self.assertNotIn('summary', run['prompt'])
        chat = workers.quick_result({'answer': ' The edge fades because ... '})
        self.assertEqual((chat['status'], chat['summary']), ('chat', 'The edge fades because ...'))
        with self.assertRaises(ValueError):
            workers.quick_result({'summary': 'Answered the question.'})
        self.assertIn('Answering', transport.progress_text(self.b, run))
        # before its stream names the model, the live line names what the class last ran
        self.b.con.execute("INSERT INTO pm_run_stats(run_id,role,provider,tier,model,queued,started) "
                           "VALUES('earlier','quick','claude','sonnet','claude-sonnet-5-5',1,1)")
        self.assertIn('Sonnet 5.5', transport.progress_text(self.b, run))
        self.assertEqual(transport.run_thread(self.b, run), '44')
        # it takes no worker slot: both are still free for tasks
        self.assertEqual(outlook.outlook(self.b, self.config, steps=False, docs_too=False)['used'], 0)
        # paused, the manager still starts it, and nothing else
        with patch.object(core.subprocess, 'Popen') as popen, patch('psutil.Process') as proc:
            popen.return_value.pid, proc.return_value.create_time.return_value = 1, 1.0
            self.manager.launch(only=('quick',))
            self.assertEqual(popen.call_count, 1)
        self.b.con.execute('UPDATE pm_runs SET pid=NULL WHERE id=?', (rid,))
        store.receive(self.b, 'discord:2002', 'ask', 'and the seam?')
        self.manager.input_events()
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=?,session='Q1' WHERE id=?",
                           (json.dumps(result('chat', head='', summary='The blur is the D112 edge fade.')), rid))
        self.manager.reconcile()
        said = self.b.q1("SELECT * FROM pm_outbox WHERE dedup=?", 'quick:' + rid[:12])
        self.assertIn('D112 edge fade', said['body'])
        self.assertEqual((self.b.task(tid)['status'], self.b.q1('SELECT phase FROM pm_tasks WHERE task_id=?', tid)[0]),
                         ('review', 'landed'))  # the task is untouched
        # the words sent meanwhile resume the same session
        with patch.object(core.release, 'reading', return_value=self.root):
            second = dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', quick.queue(self.manager)[0]))
        self.assertEqual(second['session'], 'Q1')
        self.assertTrue(second['prompt'].startswith('Yotam, in the talk channel:\nand the seam?'))
        # a session that will not resume is retried fresh, with the same words, unsaid
        self.manager.fail(second, 'exit 1: No conversation found with session ID: Q1', provider_error=True)
        self.assertFalse(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup LIKE 'quick-failed:%'"))
        with patch.object(core.release, 'reading', return_value=self.root):
            third = dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', quick.queue(self.manager)[0]))
        self.assertIsNone(third['session'])
        self.assertIn('and the seam?', third['prompt'])
        self.assertIn('Edit nothing', third['prompt'])
        # a fresh turn that fails is said, in the channel
        self.manager.fail(third, 'exit 1: boom', provider_error=True)
        self.assertIn('boom', self.b.q1("SELECT body FROM pm_outbox WHERE dedup LIKE 'quick-failed:%'")[0])
        self.assertEqual(quick.channel_name('Meatbag - Talk'), quick.CHANNEL)

    def test_ask_in_a_task_thread_answers_there_and_reaches_no_worker(self):
        """D322: /ask in a task's thread is answered in the thread by its own read-only
        session, about that task; the task stays where it was and holds nothing."""
        import discord
        import manager_quick as quick
        _, (tid,) = self.goal()
        self.b.con.execute("UPDATE pm_tasks SET phase='landed',head=?,landed_head=?,evidence=? WHERE task_id=?",
                           ('b' * 40, 'b' * 40, json.dumps({'summary': 'Faded the edge over 2 mm.'}), tid))
        self.b.update_task('manager', tid, status='review')
        self.b.con.execute('INSERT INTO pm_discord_threads VALUES(?,?)', (tid, '55'))
        feedback = self.b.q1('SELECT feedback FROM pm_tasks WHERE task_id=?', tid)[0]

        def interaction(iid, channel):
            return Mock(id=iid, user=Mock(id=33), guild=Mock(id=11), channel=channel,
                        response=Mock(send_message=AsyncMock()))

        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            ask = client.tree.get_command('ask', guild=discord.Object(id=11)).callback
            here = interaction(301, Mock(id=55, parent_id=22))
            await ask(here, question='why 2 mm?')
            outside = interaction(302, Mock(id=22, parent_id=None))  # the project channel itself
            await ask(outside, question='anything')
            await client.close()
            return here.response.send_message.await_args, outside.response.send_message.await_args
        here, outside = asyncio.run(exercise())
        self.assertIn('why 2 mm?', here.args[0])
        self.assertIn(f'nothing changes T{tid}', here.args[0])
        self.assertTrue(outside.kwargs.get('ephemeral'))
        self.assertEqual(self.b.q1("SELECT count(*) FROM pm_inbox WHERE kind='ask'")[0], 1)
        self.manager.input_events()
        m = self.b.q1('SELECT phase, feedback FROM pm_tasks WHERE task_id=?', tid)
        self.assertEqual((m['phase'], m['feedback'], self.b.task(tid)['status']), ('landed', feedback, 'review'))
        with patch.object(core.release, 'reading', return_value=self.root):
            (rid,) = quick.queue(self.manager)
        run = dict(self.b.q1('SELECT * FROM pm_runs WHERE id=?', rid))
        self.assertIsNone(run['task_id'])  # not the task's worker: the board sees nothing hold its checkout
        self.assertIn(f'in the thread of T{tid}', run['prompt'])
        self.assertIn('Faded the edge over 2 mm.', run['prompt'])
        self.assertIn('why 2 mm?', run['prompt'])
        self.assertEqual(transport.run_thread(self.b, run), '55')
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=?,session='A1' WHERE id=?",
                           (json.dumps(result('chat', head='', summary='2 mm matches the blood layer (D112).')), rid))
        self.manager.reconcile()
        row = dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', 'ask:' + rid[:12]))
        self.assertEqual(row['task_id'], tid)
        self.assertIn('blood layer', row['body'])
        self.assertEqual(self.b.task(tid)['status'], 'review')
        self.assertEqual(quick._get(self.b, f'T{tid}', 'session'), 'A1')  # the next /ask here resumes it

        async def deliver():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.send = AsyncMock(return_value=Mock(id=1))
            client.destination = AsyncMock(return_value=thread)
            await client.deliver_row(row)
            await client.close()
            return [c.custom_id for c in thread.send.await_args.kwargs['view'].children]
        self.assertNotIn('pm:pause', asyncio.run(deliver()))  # an answer, not a report

    def test_a_discord_file_is_kept_or_its_refusal_said(self):
        """G27: Yotam sent the planner a Mixamo FBX alone and the bot, keeping only images,
        dropped the message without a word. A model or clip is kept and its path passed on;
        anything refused is said in the thread, with or without text beside it."""
        def attached(name, data, content_type='application/octet-stream'):
            return Mock(id=7, filename=name, content_type=content_type, size=len(data),
                        read=AsyncMock(return_value=data))

        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            store.control(self.b, 'resume')
            store.set_setting(self.b, 'heartbeat', time.time())
            message = Mock(id=2001, content='', author=Mock(id=33, bot=False), guild=Mock(id=11),
                           channel=Mock(id=22, parent_id=None),
                           attachments=[attached('Zombie Attack.fbx', b'Kaydara FBX Binary  \x00body')])
            await client.on_message(message)
            body = self.b.q1('SELECT body FROM pm_inbox')[0]
            self.assertTrue(body.startswith('\n[file Zombie Attack.fbx] '))
            self.assertTrue(Path(body.split('] ', 1)[1]).read_bytes().startswith(b'Kaydara'))
            self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], 0)
            message.id, message.attachments = 2002, [attached('tool.exe', b'MZ'),
                                                     attached('fake.fbx', b'not an fbx')]
            await client.on_message(message)
            self.assertEqual(self.b.q1('SELECT count(*) FROM pm_inbox')[0], 1)
            note = self.b.q1("SELECT body FROM pm_outbox WHERE dedup='skipped:discord:2002'")[0]
            self.assertIn('tool.exe (not a kind I keep', note)
            self.assertIn('fake.fbx (not a real .fbx file)', note)
            self.assertIn('nothing was passed on', note)
            message.id, message.content = 2003, 'the clip'
            message.attachments = [attached('big.glb', b'glTF' + b'0' * 8)]
            message.attachments[0].size = board.MAX_FILE + 1
            await client.on_message(message)
            self.assertEqual(self.b.q1("SELECT body FROM pm_inbox WHERE id='discord:2003'")[0], 'the clip')
            note = self.b.q1("SELECT body FROM pm_outbox WHERE dedup='skipped:discord:2003'")[0]
            self.assertIn('big.glb (over 25 MB)', note)
            self.assertIn('The rest of your message went through', note)
            message.attachments[0].read.assert_not_called()
            await client.close()
        asyncio.run(exercise())

    def test_fake_cli_process_result_and_exact_session_persist(self):
        _, tid = self.task()
        r = self.new_run(tid, state='queued')
        fake = self.root / 'fake.py'
        fake.write_text('import json,sys\n' +
                        'sys.stdin.read()\n' +
                        'print(json.dumps({"type":"system","session_id":"fake-exact-session"}),flush=True)\n' +
                        'print(json.dumps({"type":"result","structured_output":' + repr(result()) + '}),flush=True)\n', encoding='utf-8')
        with patch.object(workers, 'command', return_value=[sys.executable, str(fake)]):
            self.root.joinpath('worker').mkdir()
            self.assertEqual(workers.worker_main(r['id'], self.config), 0)
        done = self.b.q1('SELECT * FROM pm_runs')
        self.assertEqual(done['state'], 'finished')
        self.assertEqual(done['session'], 'fake-exact-session')
        self.assertTrue((workers.run_dir(r['id']) / 'events.jsonl').exists())

    def test_a_worker_at_a_phase_boundary_is_compacted_and_resumed_in_one_run(self):
        """D259: T62 grew to 328k in one run; a worker that returns `compact` at a phase
        boundary has its session compacted and resumed by its own wrapper, with its note."""
        _, tid = self.task()
        r = self.new_run(tid, state='queued', provider='claude')
        seen = self.root / 'seen.jsonl'
        fake = self.root / 'fake.py'
        fake.write_text(
            'import json,sys\n'
            f'seen = {str(seen)!r}\n'
            'text = sys.stdin.read()\n'
            'n = sum(1 for _ in open(seen, encoding="utf-8")) if __import__("os").path.exists(seen) else 0\n'
            'open(seen, "a", encoding="utf-8").write(json.dumps({"argv": sys.argv[1:], "stdin": text}) + "\\n")\n'
            'print(json.dumps({"type":"system","session_id":"the-session"}),flush=True)\n'
            'status = "compact" if n == 0 else "complete"\n'
            'res = ' + repr(result()) + '\n'
            'res.update(status=status, summary="built, tests pass; next: measure" if n == 0 else "done")\n'
            'print(json.dumps({"type":"result","structured_output":res}),flush=True)\n', encoding='utf-8')
        compact = self.root / 'compact.py'
        compact.write_text(
            'import json,sys\n'
            f'open({str(seen)!r}, "a", encoding="utf-8").write(json.dumps({{"compact": sys.argv[1:]}}) + "\\n")\n'
            'print(json.dumps({"type":"result","result":"Compacted"}),flush=True)\n', encoding='utf-8')
        compacts, real = [], workers.compact_command

        def compact_command(run, config, note):
            compacts.append((run['session'], note))
            return [sys.executable, str(compact), real(run, config, note)[2]]
        with patch.object(workers, 'command', return_value=[sys.executable, str(fake)]), \
                patch.object(workers, 'compact_command', side_effect=compact_command), \
                patch.object(workers, 'RESULT_GRACE', 1):
            self.root.joinpath('worker').mkdir()
            code = workers.worker_main(r['id'], self.config)
        done = self.b.q1('SELECT * FROM pm_runs')
        self.assertEqual(done['state'], 'finished', done['error'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(done['result'])['status'], 'complete')
        self.assertEqual(compacts, [('the-session', 'built, tests pass; next: measure')])
        calls = [json.loads(l) for l in seen.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(calls), 3)  # the run, its /compact, the resumed run
        self.assertTrue(calls[1]['compact'][0].startswith('/compact Keep: the task number'))
        self.assertIn('built, tests pass; next: measure', calls[2]['stdin'])
        self.assertIn('compacted', calls[2]['stdin'])

    def test_a_cli_left_running_after_its_result_is_ended(self):
        # T29: claude -p stayed alive after its result on two `tail -f` monitors,
        # and the wrapper waited on its stdout for good.
        import psutil
        _, tid = self.task()
        r = self.new_run(tid, state='queued')
        fake = self.root / 'fake.py'
        fake.write_text('import json,subprocess,sys,time\n' +
                        'sys.stdin.read()\n' +
                        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])\n' +
                        'print(json.dumps({"child": child.pid}),flush=True)\n' +
                        'print(json.dumps({"type":"result","structured_output":' + repr(result()) + '}),flush=True)\n' +
                        'time.sleep(600)\n', encoding='utf-8')
        start = time.monotonic()
        with patch.object(workers, 'command', return_value=[sys.executable, str(fake)]), \
             patch.object(workers, 'RESULT_GRACE', 1):
            self.root.joinpath('worker').mkdir()
            self.assertEqual(workers.worker_main(r['id'], self.config), 0)
        self.assertLess(time.monotonic() - start, 60)
        self.assertEqual(self.b.q1('SELECT state FROM pm_runs')[0], 'finished')
        log = (workers.run_dir(r['id']) / 'events.jsonl').read_text(encoding='utf-8')
        child = json.loads(log.splitlines()[0])['child']
        self.assertFalse(psutil.pid_exists(child) and psutil.Process(child).status() != psutil.STATUS_ZOMBIE)

    def test_a_resumed_session_s_empty_first_result_does_not_end_the_run(self):
        """D214: T39's retry resumed its session, which reported a leftover background
        task's empty turn first (num_turns 0, no result). Taken for the end, the reaper
        killed the worker a minute into its work ("missing structured result fields"),
        and its session, never stopped, held A3 as busy for 15 minutes."""
        _, tid = self.task()
        r = self.new_run(tid, state='queued')
        sid = 'resumed-session'
        self.b.session_touch(sid, 'A2', 'working')
        fake = self.root / 'fake.py'
        fake.write_text(
            'import json,sys,time\n'
            'sys.stdin.read()\n'
            f'print(json.dumps({{"type":"system","subtype":"task_notification","session_id":"{sid}"}}),flush=True)\n'
            'print(json.dumps({"type":"result","subtype":"success","is_error":False,"num_turns":0,'
            '"result":"","structured_output":None}),flush=True)\n'
            'for i in range(10):\n'
            '    time.sleep(0.3)\n'
            '    print(json.dumps({"type":"assistant","message":{"content":[{"type":"text","text":"step %d" % i}]}}),flush=True)\n'
            'print(json.dumps({"type":"result","num_turns":12,"structured_output":' + repr(result()) + '}),flush=True)\n',
            encoding='utf-8')
        with patch.object(workers, 'command', return_value=[sys.executable, str(fake)]), \
                patch.object(workers, 'RESULT_GRACE', 1):
            self.root.joinpath('worker').mkdir()
            self.assertEqual(workers.worker_main(r['id'], self.config), 0)
        done = self.b.q1('SELECT * FROM pm_runs')
        self.assertEqual(done['state'], 'finished', done['error'])
        self.assertEqual(json.loads(done['result'])['status'], 'complete')
        self.assertIn('said: step 9', workers.last_activity(r['id']))
        self.assertFalse(self.b.q1('SELECT 1 FROM sessions WHERE id=?', sid))
        self.assertFalse(workers.ends_run({'type': 'result', 'num_turns': 0, 'result': '', 'structured_output': None}))
        self.assertTrue(workers.ends_run({'type': 'result', 'num_turns': 3, 'result': ''}))
        self.assertTrue(workers.ends_run({'type': 'result', 'is_error': True}))

    def test_a_stopped_worker_s_session_no_longer_holds_its_checkout(self):
        """D214: a worker ended from outside never runs its Stop hook."""
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        self.b.con.execute("UPDATE pm_runs SET session='killed-session' WHERE id=?", (r['id'],))
        self.b.session_touch('killed-session', 'A2', 'working')
        with patch.object(workers, 'owned_process', return_value=Mock()), patch.object(workers, 'end_tree') as end:
            workers.stop_owned(r, self.b)
        end.assert_called_once()
        self.assertFalse(fe_sync.working_elsewhere('A2', self.b.sessions()))

    def test_pid_reuse_does_not_kill_unrelated_process(self):
        import psutil
        run = dict(id='abc', pid=os.getpid(), pid_started=psutil.Process().create_time() - 1)
        self.assertIsNone(workers.owned_process(run))
        run['pid_started'] += 1
        self.assertIsNone(workers.owned_process(run))  # wrong command even with same birth time

    def test_board_cannot_accept_managed_task_before_integration(self):
        _, tid = self.task()
        self.b.update_task('A2', tid, status='review')
        with self.assertRaises(board.BoardError):
            board.post(self.b, '/api/task/update', dict(id=tid, status='done'))

    def test_stale_acceptance_is_rejected_at_execution(self):
        _, tid = self.task('landed')
        self.b.update_task('manager', tid, status='review')
        self.b.con.execute('UPDATE pm_tasks SET landed_head=? WHERE task_id=?', ('b'*40, tid))
        store.receive(self.b, 'old-button', 'accept', 'a'*12, tid)
        self.manager.input_events()
        self.assertEqual(self.b.task(tid)['status'], 'review')
        self.assertEqual(self.b.q1("SELECT state FROM pm_inbox WHERE id='old-button'")[0], 'rejected')

    def test_two_slot_limit_includes_reviewers(self):
        store.control(self.b, 'resume')
        self.goal()
        for role in ('worker', 'reviewer'):
            store.queue_run(self.b, role, 'codex', self.root, 'prompt')
        with patch.object(self.manager, 'queue_task') as queue:
            self.manager.schedule()
        queue.assert_not_called()
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_runs')[0], 2)

    def test_one_provider_left_still_carries_two_runs_on_claude(self):
        """D212 (amending D197): Yotam wants two workers, A2 and A3, and with Codex spent
        both run on Opus."""
        store.control(self.b, 'resume')
        self.goal([dict(title='First', body='Implement and test', depends=[]),
                   dict(title='Second', body='Implement and test', depends=[])])
        store.block_provider(self.b, 'codex', 'allowance', time.time() + 3600, 1, 3600.0)
        self.assertEqual(self.manager.available(), ['claude'])
        self.assertEqual(self.manager.cap(), 2)
        self.assertEqual(self.manager.provider('claude'), 'claude')
        with patch.object(self.manager, 'queue_task', side_effect=self.queued) as queue:
            self.manager.schedule()
        self.assertEqual(len(queue.call_args_list), 2)
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_runs')[0], 2)

    def test_two_providers_still_carry_two_runs(self):
        store.control(self.b, 'resume')
        self.goal([dict(title='First', body='Implement and test', depends=[]),
                   dict(title='Second', body='Implement and test', depends=[])])
        self.assertEqual(self.manager.available(), ['claude', 'codex'])
        self.assertEqual(self.manager.cap(), 2)
        with patch.object(self.manager, 'queue_task', side_effect=self.queued) as queue:
            self.manager.schedule()
        self.assertEqual(len(queue.call_args_list), 2)
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_runs')[0], 2)

    def test_a_task_left_for_review_or_integration_goes_back_to_its_worker(self):
        _, tid = self.task('review')
        for phase in ('review', 'integrate'):
            self.b.con.execute("UPDATE pm_tasks SET phase=?,feedback='' WHERE task_id=?", (phase, tid))
            with patch.object(self.manager, 'queue_task', return_value=False) as queue:
                self.manager.schedule()
            m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
            self.assertEqual(m['phase'], 'revise')
            self.assertIn('D188', m['feedback'])
            queue.assert_called()
        self.assertIsNone(self.b.q1("SELECT 1 FROM pm_runs WHERE role<>'worker'"))

    def test_supervisor_singleton(self):
        with host.singleton():
            with self.assertRaises(OSError):
                with host.singleton():
                    self.fail('acquired duplicate supervisor lock')

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object')
    def test_wrapper_exit_terminates_child_job(self):
        import psutil
        script = self.root / 'job_test.py'
        script.write_text('import sys,subprocess\n' +
                          f'sys.path.insert(0,{str(Path(__file__).parent)!r})\n' +
                          'import manager_host\nmanager_host.worker_job()\n' +
                          'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n' +
                          'print(p.pid,flush=True)\n', encoding='utf-8')
        p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        pid = int(p.stdout.strip())
        try:
            child = psutil.Process(pid)
            child.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass

    def landing_repo(self, land_script):
        """A worker checkout one commit ahead of a bare origin, whose fe_land.py is LAND_SCRIPT."""
        def git(cwd, *args):
            return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True,
                                  text=True, check=True, creationflags=workers.NO_WINDOW).stdout.strip()
        remote, repo = self.root / 'land-remote', self.root / 'land-worker'
        remote.mkdir()
        repo.mkdir()
        git(remote, 'init', '--bare', '-b', 'main')
        git(repo, 'init', '-b', 'main')
        git(repo, 'config', 'user.email', 'manager-test@example.invalid')
        git(repo, 'config', 'user.name', 'Manager test')
        git(repo, 'remote', 'add', 'origin', str(remote))
        tools = repo / studio_config.studio_rel()  # D330: the studio's place in a checkout
        tools.mkdir(parents=True)
        (tools / 'fe_land.py').write_text(land_script, encoding='utf-8')
        (tools / 'fe_board.py').write_text('', encoding='utf-8')  # what marks the studio there
        (repo / '.gitignore').write_text('engine/artifacts/\n', encoding='utf-8')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'base')
        git(repo, 'pu' + 'sh', '-q', '-u', 'origin', 'main')
        (repo / 'change.txt').write_text('the task', encoding='utf-8')
        git(repo, 'add', 'change.txt')
        git(repo, 'commit', '-qm', 'the task')
        rid = 'abc123'
        workers.run_dir(rid).mkdir(parents=True, exist_ok=True)
        return git, remote, repo, dict(id=rid, role='worker', cwd=str(repo), agent='A2')

    def test_the_manager_lands_a_committed_worker_with_no_model_turn(self):
        # D253: T48 waited on its own gates for 97 minutes at 600k tokens a turn. The worker
        # commits and stops; the supervisor runs fe_land.py and the result carries its lines.
        git, remote, repo, run = self.landing_repo(
            'import json, subprocess, sys, pathlib\n'
            'pathlib.Path("args.txt").write_text(" ".join(sys.argv[1:]))\n'
            'subprocess.run(["git", "pu" + "sh", "-q", "origin", "HEAD:main"], check=True)\n'
            'a = pathlib.Path("engine/artifacts"); a.mkdir(parents=True, exist_ok=True)\n'
            '(a / "land-A2.json").write_text(json.dumps({"ok": True, "gates": [\n'
            '  {"gate": "test", "status": "ok", "secs": 1, "line": "test ok | 412 passed"},\n'
            '  {"gate": "bench", "status": "ok", "secs": 9, "line": "compare: passed, +0.1 ms"}]}))\n'
            'print("  bench  ok  9s  compare: passed, +0.1 ms")\n')
        beats = []
        out = workers.land(run, result(head='old'), heartbeat=lambda: beats.append(1))
        head = git(repo, 'rev-parse', 'HEAD')
        self.assertEqual(git(remote, 'rev-parse', 'main'), head)
        self.assertEqual((out['status'], out['head']), ('complete', head))
        checks = {c['name']: c for c in out['checks']}
        self.assertEqual(checks['performance']['outcome'], 'passed')
        self.assertIn('compare: passed', checks['performance']['detail'])
        self.assertIn('412 passed', checks['tests']['detail'])
        self.assertEqual(checks['regression']['outcome'], 'not_applicable')  # no regress gate ran
        self.assertIn('Landed by the manager', out['summary'])
        self.assertTrue(workers.evidence_passes(out, head))
        self.assertEqual((repo / 'args.txt').read_text(), '')

    def test_a_failed_land_resumes_the_worker_with_the_gate_that_stopped(self):
        git, remote, repo, run = self.landing_repo(
            'print("  build  ok      40s  build ok")\n'
            'print("  test   FAILED  20s  1 failed: ragdoll::gait::stands")\n'
            'raise SystemExit(1)\n')
        before = git(remote, 'rev-parse', 'main')
        out = workers.land(run, result())
        self.assertEqual(git(remote, 'rev-parse', 'main'), before)
        self.assertEqual(out['status'], 'continue')
        self.assertIn('ragdoll::gait::stands', out['summary'])
        self.assertIn('Your note:\nValidated change', out['summary'])

    def test_the_manager_lands_only_what_the_workers_evidence_supports(self):
        git, remote, repo, run = self.landing_repo('raise SystemExit("must not run")\n')
        bad = result()
        for c in bad['checks']:
            if c['name'] == 'player_path':
                c.update(outcome='failed', detail='the drag misses the arm')
        before = git(remote, 'rev-parse', 'main')
        self.assertIs(workers.land(run, bad), bad)
        (repo / 'change.txt').write_text('uncommitted', encoding='utf-8')
        self.assertEqual(workers.land(run, result())['status'], 'complete')  # a dirty tree: unlanded says so
        self.assertEqual(git(remote, 'rev-parse', 'main'), before)

    def test_the_board_shows_the_gate_a_land_is_running(self):
        """T62 sat 50 minutes in a regress while the board showed its worker's last step."""
        _, tid = self.task()
        r = self.new_run(tid, state='running', provider='claude')
        self.assertEqual(workers.landing(r), '')  # not landing
        folder = workers.run_dir(r['id'])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'land.log').write_text('', encoding='utf-8')
        art = Path(r['cwd']) / 'engine' / 'artifacts'
        art.mkdir(parents=True, exist_ok=True)
        self.assertEqual(workers.landing(r), 'ran: landing: starting fe_land')
        now = time.time()
        (art / 'land-A2.json').write_text(json.dumps({
            'head': 'h', 'of': 4, 'at': now, 'running': 'regress', 'since': now - 300,
            'last': '[fe-regress] mutant-cutter: 52 hashes, exit 0, 44.0 s',
            'gates': [{'gate': 'baseline', 'status': 'ok', 'secs': 1560, 'line': ''}]}), encoding='utf-8')
        line = workers.landing(r, now)
        self.assertEqual(line, 'ran: landing: regress (2 of 4) for 5 min; done baseline ok; '
                               'last: [fe-regress] mutant-cutter: 52 hashes, exit 0, 44.0 s')
        o = outlook.outlook(self.b, now=now)
        run = next(x for x in o['running'] if x['run'] == r['id'])
        self.assertEqual(run['doing'], line[len('ran: '):])
        (art / 'land-A2.json').write_text(json.dumps({'head': 'h', 'ok': True, 'gates': [], 'at': now}),
                                          encoding='utf-8')
        self.assertEqual(workers.landing(r, now), '')  # landed

    def test_fe_land_benches_only_when_asked(self):
        """D262: a runtime change lands with its build and tests; main is benched nightly."""
        import fe_land
        runtime = ['engine/crates/app/src/main.rs']
        gates = lambda *a: [g for g, _, _ in fe_land.plan(runtime, fe_land_args(*a))]  # noqa: E731

        def fe_land_args(*argv):
            import argparse
            ap = argparse.ArgumentParser()
            for f in ('--refactor', '--bench', '--no-push'):
                ap.add_argument(f, action='store_true')
            ap.add_argument('--scenario', nargs='*', default=[])
            ap.add_argument('--task')
            ap.add_argument('--version')
            return ap.parse_args(list(argv))
        self.assertNotIn('bench', gates())
        self.assertIn('build', gates())
        self.assertIn('bench', gates('--bench'))
        self.assertIn('bench', gates('--scenario', 'spotlight-fps'))
        undo = lambda f: [g for g, _, _ in fe_land.plan([f], fe_land_args())]  # noqa: E731
        for f in ('panel.rs', 'commands.rs', 'edit_journal.rs', 'panel_settings.rs', 'lighting.rs', 'entities.rs', 'tune.rs', 'look.rs'):
            self.assertIn('undo', undo('engine/crates/app/src/' + f), f)
        self.assertNotIn('undo', undo('engine/crates/app/src/shadow.rs'))
        self.assertNotIn('undo', undo('docs/roadmap.md'))

    def test_fe_land_says_which_gate_runs_while_it_runs(self):
        import fe_land
        seen = []
        fake = self.root / 'gate.py'
        fake.write_text('print("[fe-regress] forearm-circular: 40 hashes", flush=True)\n', encoding='utf-8')
        with patch.object(fe_land, 'ARTIFACTS', self.root / 'art'), \
                patch.object(fe_land, 'agent', return_value='A9'), \
                patch.object(fe_land, 'git', side_effect=lambda *a: {'rev-parse': 'abc', 'log': 'abc x', 'rev-list': '1'}.get(a[0], '')), \
                patch.object(fe_land, 'plan', return_value=[('regress', [sys.executable, str(fake)], '')]), \
                patch.object(fe_land.subprocess, 'run'):
            real = fe_land.json.dumps

            def dumps(obj, **kw):
                seen.append(dict(obj))
                return real(obj, **kw)
            with patch.object(fe_land.json, 'dumps', side_effect=dumps), patch('sys.stdout'):
                self.assertEqual(fe_land.main([]), 0)
        running = [s for s in seen if s.get('running')]
        self.assertEqual(running[-1]['running'], 'regress')
        self.assertEqual(running[-1]['last'], '[fe-regress] forearm-circular: 40 hashes')
        self.assertTrue(seen[-1]['ok'])
        self.assertNotIn('running', seen[-1])

    def test_a_docs_only_land_counts_its_docs_gates_as_tests(self):
        """T62: a plan's land ran docs and index, no build; its tests stayed not_applicable and
        the manager sent the landed task back as unfinished."""
        r = result()
        for c in r['checks']:
            if c['name'] == 'tests':
                c.update(outcome='not_applicable', command='', detail="the manager's fe_land runs it")
        gates = [{'gate': 'docs', 'status': 'ok', 'line': 'docs ok'},
                 {'gate': 'index', 'status': 'ok', 'line': 'check ok'}, {'gate': 'push', 'status': 'ok', 'line': ''}]
        tests = next(c for c in workers.landed_checks(r, gates) if c['name'] == 'tests')
        self.assertEqual(tests['outcome'], 'passed')
        self.assertEqual(tests['detail'], 'docs: docs ok; index: check ok')
        gates.insert(0, {'gate': 'build', 'status': 'ok', 'line': 'build ok'})
        tests = next(c for c in workers.landed_checks(r, gates) if c['name'] == 'tests')
        self.assertEqual(tests['detail'], 'build: build ok')

    def test_what_the_service_starts_runs_on_a_console_python(self):
        """Under pythonw every python a worker's gpu batch started opened a window and took the focus."""
        w = self.root / 'py' / 'pythonw.exe'
        w.parent.mkdir()
        w.write_bytes(b'')
        self.assertEqual(workers.console_python(str(w)), str(w))  # no python.exe beside it
        w.with_name('python.exe').write_bytes(b'')
        self.assertEqual(workers.console_python(str(w)), str(w.with_name('python.exe')))
        self.assertEqual(workers.console_python(sys.executable), sys.executable)

    def test_land_flags_come_from_the_workers_checks(self):
        r = result()
        for c in r['checks']:
            if c['name'] == 'regression':
                c.update(outcome='passed', command='python engine/tools/fe_regress.py check', detail='identical')
            if c['name'] == 'performance':
                c.update(outcome='passed', detail='ok', command=(
                    '"C:/rel/engine/tools/fe_bench.py" --repo . compare HEAD spotlight-fps knife-thrust'))
        self.assertEqual(workers.land_args(r), ['--refactor'])  # D262: the land never benches
        self.assertEqual(workers.land_args(result()), [])
        for c in r['checks']:
            if c['name'] == 'regression':
                c.update(outcome='not_applicable', command='python engine/tools/fe_land.py --refactor',
                         detail="the manager's fe_land runs it")
            if c['name'] == 'performance':
                c.update(command='')
        self.assertEqual(workers.land_args(r), ['--refactor'])
        for c in r['checks']:  # T62: a docs-only plan whose detail mentions fe_land is no refactor
            if c['name'] == 'regression':
                c.update(command='git show --stat HEAD',
                         detail='No runtime file changed. fe_land will still run its docs and index gates.')
        self.assertEqual(workers.land_args(r), [])

    def test_a_worker_rebases_onto_moved_trunk_and_lands(self):
        # A real Git test; all repositories/remotes are temporary.
        def git(cwd, *args):
            return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True,
                                  text=True, check=True, creationflags=workers.NO_WINDOW).stdout.strip()
        remote, repo, other = self.root / 'remote', self.root / 'git-worker', self.root / 'git-other'
        remote.mkdir()
        repo.mkdir()
        git(remote, 'init', '--bare')
        git(repo, 'init', '-b', 'main')
        git(repo, 'config', 'user.email', 'manager-test@example.invalid')
        git(repo, 'config', 'user.name', 'Manager test')
        git(repo, 'remote', 'add', 'origin', str(remote))
        (repo / 'test.txt').write_text('baseline', encoding='utf-8')
        git(repo, 'add', 'test.txt')
        git(repo, 'commit', '-qm', 'baseline')
        git(repo, 'pu' + 'sh', '-u', 'origin', 'main')
        subprocess.run(['git', 'clone', '-q', '-b', 'main', str(remote), str(other)], check=True,
                       creationflags=workers.NO_WINDOW)
        git(other, 'config', 'user.email', 'manager-test@example.invalid')
        git(other, 'config', 'user.name', 'Manager test')
        (other / 'other.txt').write_text('another agent', encoding='utf-8')
        git(other, 'add', 'other.txt')
        git(other, 'commit', '-qm', 'another agent lands first')
        git(other, 'pu' + 'sh', '-q')
        (repo / 'test.txt').write_text('candidate', encoding='utf-8')
        git(repo, 'commit', '-am', 'candidate', '-q')
        with patch.dict(os.environ, {'FE_MANAGER_RUN': 'worker'}):
            landed = fe_sync.sync_push(repo, self.manager.reg)
        self.assertTrue(landed.ok, landed.message)
        self.assertEqual(git(remote, 'rev-parse', 'main'), git(repo, 'rev-parse', 'HEAD'))
        self.assertEqual(git(remote, 'log', '--format=%s', '-2', 'main').splitlines(),
                         ['candidate', 'another agent lands first'])

    def test_pushed_work_is_reviewable_even_after_a_failed_run(self):
        # T28: the push went through, the run was recorded failed, the board said blocked.
        _, tid = self.task('failed')
        self.b.con.execute('UPDATE pm_tasks SET head=? WHERE task_id=?', ('c'*40, tid))
        self.b.update_task('manager', tid, status='blocked')
        with patch.object(board, 'on_trunk', return_value=False):
            self.manager.reconcile()
            self.assertEqual(self.b.task(tid)['status'], 'blocked')
            with self.assertRaises(board.BoardError):
                self.b.update_task('owner', tid, status='done')
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.reconcile()
            self.assertEqual(self.b.task(tid)['status'], 'review')
            self.assertEqual(self.b.q1('SELECT phase,landed_head FROM pm_tasks WHERE task_id=?', tid)[:],
                             ('landed', 'c'*40))
            self.assertTrue(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup=?", f'landed:{tid}:' + 'c'*40))
            self.b.update_task('owner', tid, status='done')
        self.assertEqual(self.b.task(tid)['status'], 'done')

    def test_unfinished_worker_resumes_its_session_without_asking_yotam(self):
        # T29: the headless turn ended under a running GPU suite and the worker "blocked".
        _, tid = self.task('working')
        sent = self.b.q1('SELECT count(*) FROM pm_outbox')[0]
        r = self.new_run(tid, data=result('continue', summary='Suite still running; resume me'), provider='claude')
        self.manager.completed(r)
        m = self.b.q1('SELECT phase,worker_session,worker_provider,feedback FROM pm_tasks WHERE task_id=?', tid)
        self.assertEqual((m[0], m[1], m[2]), ('revise', 'exact-session', 'claude'))
        self.assertIn('Suite still running', m[3])
        self.assertNotEqual(self.b.task(tid)['status'], 'blocked')
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], sent)
        self.assertIsNone(self.b.q1("SELECT 1 FROM messages WHERE topic=? AND kind='question'", f'T{tid}'))

    def test_endless_continue_reaches_yotam(self):
        _, tid = self.task('working')
        sent = self.b.q1('SELECT count(*) FROM pm_outbox')[0]
        for i in range(core.CONTINUE_SILENCE):
            r = self.new_run(tid, data=result('continue'), provider='claude')
            self.b.con.execute('UPDATE pm_runs SET created=? WHERE id=?', (1000 + i, r['id']))
            r['created'] = 1000 + i
            self.manager.completed(r)
        self.assertEqual(self.b.task(tid)['status'], 'blocked')
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_outbox')[0], sent + 1)

    def test_owner_accepts_pushed_work_straight_from_the_board(self):
        _, tid = self.task('failed')
        self.b.con.execute('UPDATE pm_tasks SET head=? WHERE task_id=?', ('d'*40, tid))
        with patch.object(board, 'on_trunk', return_value=True):
            store.accept(self.b, tid)
        self.assertEqual(self.b.task(tid)['status'], 'done')

    # ------------------------------------------------ D284: a landed task's Accept always works

    def landed_review(self, head='a' * 40):
        """A task in review with its message queued, as `land` leaves it."""
        _, tid = self.task('landed')
        self.b.con.execute('UPDATE pm_tasks SET head=?,landed_head=? WHERE task_id=?', (head, head, tid))
        self.b.update_task('manager', tid, status='review')
        store.notify(self.b, f'landed:{tid}:{head}', 'On the main branch.', tid, True)
        return tid, head

    def deliver(self, dedup, attempts=0, files=None, send=None):
        """Send one queued row through the transport; returns (thread, button ids per send)."""
        import discord
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.guild = Mock(filesize_limit=10 << 20)
            thread.send = send or AsyncMock(return_value=Mock(id=1))
            async def history(**kw):
                return
                yield
            thread.history = history
            client.destination = AsyncMock(return_value=thread)
            row = dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', dedup))
            row['attempts'] = attempts
            if files is not None:
                row['files'] = json.dumps([str(f) for f in files])
            await client.deliver_row(row)
            await client.close()
            return thread
        thread = asyncio.run(exercise())
        return thread, [[c.custom_id for c in call.kwargs['view'].children] for call in thread.send.await_args_list]

    def click(self, custom):
        import discord
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            c = Mock(id=abs(hash(custom)) % 10**6, type=discord.InteractionType.component, data={'custom_id': custom},
                     user=Mock(id=33), guild=Mock(id=11), channel=Mock(id=22, parent_id=None),
                     response=Mock(send_message=AsyncMock(), send_modal=AsyncMock()))
            await client.on_interaction(c)
            await client.close()
            return c.response
        return asyncio.run(exercise())

    def test_a_review_message_carries_its_buttons_whenever_it_is_sent(self):
        tid, head = self.landed_review()
        want = [f'pm:accept:{tid}:{head[:12]}', f'pm:changes:{tid}:{head[:12]}', 'pm:pause']
        # a reply moved the task to revise between queueing and sending
        self.b.con.execute("UPDATE pm_tasks SET phase='revise' WHERE task_id=?", (tid,))
        self.b.update_task('manager', tid, status='claimed')
        self.assertEqual(self.deliver(f'landed:{tid}:{head}')[1], [want])
        # a retry after a failed send (attempts set) carries them too
        self.assertEqual(self.deliver(f'landed:{tid}:{head}', attempts=4)[1], [want])
        # delivery is recorded as the control having arrived
        self.assertEqual(store.setting(self.b, store.control_key(tid, head)), '1')

    def test_accept_works_on_a_task_whose_head_is_on_trunk_whatever_its_phase(self):
        tid, head = self.landed_review()
        self.b.con.execute("UPDATE pm_tasks SET phase='revise' WHERE task_id=?", (tid,))
        self.b.update_task('manager', tid, status='claimed')
        said = self.click(f'pm:accept:{tid}:{head[:12]}').send_message.await_args.args[0]
        self.assertIn('Recorded', said)
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.input_events()
        self.assertEqual(self.b.task(tid)['status'], 'done')

    def test_a_stale_or_finished_button_says_so_and_accepts_nothing(self):
        tid, head = self.landed_review()
        new = 'b' * 40
        self.b.con.execute('UPDATE pm_tasks SET head=?,landed_head=? WHERE task_id=?', (new, new, tid))
        said = self.click(f'pm:accept:{tid}:{head[:12]}').send_message.await_args.args[0]
        self.assertIn('newer result', said)
        self.assertIn(new[:12], said)
        self.assertIsNone(self.b.q1("SELECT 1 FROM pm_inbox WHERE kind='accept'"))
        self.assertTrue(self.click(f'pm:changes:{tid}:{new[:12]}').send_modal.await_count)  # the latest one works
        self.b.update_task('owner', tid, status='done')
        said = self.click(f'pm:accept:{tid}:{new[:12]}').send_message.await_args.args[0]
        self.assertIn('already accepted', said)

    def test_a_review_that_never_got_its_control_gets_it_once(self):
        _, tid = self.task('landed')
        head = 'c' * 40
        self.b.con.execute('UPDATE pm_tasks SET head=?,landed_head=? WHERE task_id=?', (head, head, tid))
        self.b.update_task('manager', tid, status='review')
        # the first message went out bare (nothing recorded): the pass posts the control, once
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.reconcile()
            self.manager.reconcile()
        rows = self.b.q("SELECT dedup FROM pm_outbox WHERE dedup LIKE 'accept-control:%'")
        self.assertEqual([r[0] for r in rows], [f'accept-control:{tid}:{head}'])
        want = [f'pm:accept:{tid}:{head[:12]}', f'pm:changes:{tid}:{head[:12]}', 'pm:pause']
        self.assertEqual(self.deliver(f'accept-control:{tid}:{head}')[1], [want])
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.reconcile()
        self.assertEqual(len(self.b.q("SELECT 1 FROM pm_outbox WHERE dedup LIKE 'accept-control:%'")), 1)

    def test_the_repair_pass_is_quiet_for_a_review_that_has_its_control(self):
        tid, head = self.landed_review()
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.reconcile()  # its own message is still waiting to be sent: it carries the buttons
        self.assertIsNone(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup LIKE 'accept-control:%'"))
        self.deliver(f'landed:{tid}:{head}')
        with patch.object(board, 'on_trunk', return_value=True):
            self.manager.reconcile()
        self.assertIsNone(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup LIKE 'accept-control:%'"))
        # and not for a commit that is not on origin/main
        self.b.con.execute('UPDATE pm_tasks SET head=?,landed_head=? WHERE task_id=?', ('d' * 40, 'd' * 40, tid))
        with patch.object(board, 'on_trunk', return_value=False):
            self.manager.reconcile()
        self.assertIsNone(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup LIKE 'accept-control:%'"))

    def test_one_failed_delivery_does_not_end_the_delivery_loop(self):
        store.notify(self.b, 'one', 'first')
        store.notify(self.b, 'two', 'second')
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client.wait_until_ready = AsyncMock()
            client.is_closed = Mock(side_effect=[False, True])
            client.deliver_row = AsyncMock(side_effect=[RuntimeError('not a Discord error'), None])
            with patch.object(transport.asyncio, 'sleep', AsyncMock()):
                await client.deliver()
            await client.close()
            return client.deliver_row.await_count
        self.assertEqual(asyncio.run(exercise()), 2)  # the second row was still tried
        one = self.b.q1("SELECT attempts,retry_at FROM pm_outbox WHERE dedup='one'")
        self.assertEqual(one['attempts'], 1)
        self.assertGreater(one['retry_at'], time.time())  # backed off, retried later

    def test_more_than_ten_attachments_follow_in_their_own_messages(self):
        tid, head = self.landed_review()
        made = []
        for n in range(11):
            f = board.files_dir() / f'shot{n}.png'
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'x' * 10)
            made.append(f)
        thread, buttons = self.deliver(f'landed:{tid}:{head}', files=made)
        sent = [(len(c.kwargs['files']), c.kwargs['embed'].footer.text) for c in thread.send.await_args_list]
        self.assertEqual([n for n, _ in sent], [10, 1])
        self.assertTrue(sent[1][1].endswith(':f1'))
        self.assertIn(f'pm:accept:{tid}:{head[:12]}', buttons[0])
        self.assertEqual(buttons[1], [])

    def test_attachments_discord_refuses_do_not_keep_the_buttons_from_arriving(self):
        import discord
        tid, head = self.landed_review()
        f = board.files_dir() / 'shot.png'
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'x' * 10)
        async def send(**kw):
            if kw['files']:
                raise discord.HTTPException(Mock(status=400, reason='Bad Request'), 'Invalid Form Body')
            return Mock(id=9)
        thread, buttons = self.deliver(f'landed:{tid}:{head}', files=[f], send=AsyncMock(side_effect=send))
        self.assertEqual(len(buttons), 2)
        self.assertIn(f'pm:accept:{tid}:{head[:12]}', buttons[1])
        self.assertIn('shot.png', thread.send.await_args_list[1].kwargs['embed'].fields[0].value)
        self.assertIsNotNone(self.b.q1('SELECT sent FROM pm_outbox WHERE dedup=?', f'landed:{tid}:{head}')[0])

    # ---------------------------------------------------------- provider cool-off

    def blocked_provider(self, name='codex'):
        return dict(self.b.q1('SELECT * FROM pm_providers WHERE name=?', name))

    def test_the_real_chatgpt_limit_waits_as_long_as_it_said(self):
        _, tid = self.task()
        now = NOW  # the fixture's reset hour is a fixed date, so the clock is too
        with patch('time.time', return_value=now):
            self.spent(tid, CODEX_LIMIT)
            p = self.blocked_provider()
            self.assertEqual(p['reason'], 'allowance')
            self.assertAlmostEqual(p['window_s'], workers.reset_seconds(CODEX_LIMIT, now), delta=5)
            self.assertAlmostEqual(p['retry_at'], now + p['window_s'], delta=30)
            self.assertGreater(p['window_s'], core.ALLOWANCE_BACKOFF[0])
            self.assertEqual(self.manager.provider('codex'), 'claude')

    def test_a_limit_five_days_out_is_held_to_a_day(self):
        self.assertEqual(self.manager.block_provider('codex', 'allowance', CODEX_LIMIT, now=NOW),
                         workers.RESET_MAX)
        self.assertEqual(self.blocked_provider()['retry_at'], NOW + workers.RESET_MAX)
        self.assertEqual(self.manager.block_provider('codex', 'allowance', CODEX_LIMIT_REPEATED, now=NOW),
                         workers.RESET_MAX)

    def test_a_named_reset_beats_the_ladder_however_often_it_repeats(self):
        _, tid = self.task()
        for expected_blocks in (1, 2, 3):
            self.spent(tid, 'exit 1: rate limit reached, try again in 45 minutes')
            p = self.blocked_provider()
            self.assertEqual((p['window_s'], p['blocks']), (45 * 60, expected_blocks))

    def test_an_unexplained_block_escalates_and_then_holds(self):
        _, tid = self.task()
        for expected in core.ALLOWANCE_BACKOFF + (core.ALLOWANCE_BACKOFF[-1],) * 2:
            now = time.time()
            self.spent(tid, 'exit 1: rate limit reached')
            p = self.blocked_provider()
            self.assertEqual(p['window_s'], expected)
            self.assertAlmostEqual(p['retry_at'], now + expected, delta=30)

    def test_a_run_the_provider_carries_through_resets_the_ladder(self):
        _, tid = self.task()
        for _ in range(2):
            self.spent(tid, 'exit 1: rate limit reached')
        self.assertEqual(self.blocked_provider()['blocks'], 2)
        with patch.object(fe_sync, 'git_out', side_effect=lambda _, *a: 'a'*40 if a[0] == 'rev-parse' else ''):
            self.manager.completed(self.new_run(tid))
        self.assertIsNone(self.b.q1("SELECT * FROM pm_providers WHERE name='codex'"))
        self.spent(tid, 'exit 1: rate limit reached')
        self.assertEqual(self.blocked_provider()['window_s'], core.ALLOWANCE_BACKOFF[0])

    def test_a_block_on_one_provider_leaves_the_other_ladder_alone(self):
        _, tid = self.task()
        for _ in range(2):
            self.spent(tid, 'exit 1: rate limit reached')
        rid = self.new_run(tid, state='failed', provider='claude')['id']
        self.b.con.execute('UPDATE pm_runs SET error=? WHERE id=?', ('exit 1: rate limit reached', rid))
        self.manager.reconcile()
        self.assertEqual(self.blocked_provider('claude')['window_s'], core.ALLOWANCE_BACKOFF[0])
        self.assertEqual(self.blocked_provider()['window_s'], core.ALLOWANCE_BACKOFF[1])

    def test_an_authentication_block_is_not_on_the_allowance_ladder(self):
        _, tid = self.task()
        self.spent(tid, 'exit 1: rate limit reached')
        self.spent(tid, 'exit 1: not logged in')
        p = self.blocked_provider()
        self.assertEqual((p['reason'], p['blocks'], p['window_s']), ('authentication', 0, core.AUTH_BLOCK))

    def test_resume_clears_every_block_and_its_count(self):
        _, tid = self.task()
        for _ in range(3):
            self.spent(tid, 'exit 1: rate limit reached')
        store.control(self.b, 'resume')
        self.assertIsNone(self.b.q1('SELECT * FROM pm_providers'))
        self.spent(tid, 'exit 1: rate limit reached')
        self.assertEqual(self.blocked_provider()['window_s'], core.ALLOWANCE_BACKOFF[0])

    def test_the_snapshot_and_the_status_line_carry_the_deadline(self):
        _, tid = self.task()
        self.spent(tid, 'exit 1: rate limit reached, try again in 45 minutes')
        providers = store.snapshot(self.b)['providers']
        self.assertEqual(len(providers), 1)
        p = providers[0]
        self.assertEqual(set(p), {'name', 'reason', 'retry_at', 'blocks', 'window_s'})
        self.assertAlmostEqual(p['retry_at'], time.time() + 45 * 60, delta=30)
        clock = time.strftime('%H:%M', time.localtime(p['retry_at']))
        self.assertEqual(store.provider_line(p), f'codex waiting: allowance, retrying at {clock}')

    def test_a_deadline_past_a_day_shows_its_date(self):
        line = store.provider_line({'name': 'codex', 'reason': 'authentication',
                                    'retry_at': time.time() + core.AUTH_BLOCK})
        self.assertRegex(line, r'^codex waiting: authentication, retrying at \d{4}-\d{2}-\d{2} \d{2}:\d{2}$')


class PlanDocumentTests(unittest.TestCase):
    setUp, tearDown = ManagerTests.setUp, ManagerTests.tearDown
    goal = ManagerTests.goal

    def delivered(self, legacy=False, plan=True):
        _, ids = self.goal([dict(title='A plan for undo', body='Write docs/edit-undo.md, build nothing yet.', depends=[])])
        tid = ids[0]
        evidence = result(artifacts=['engine/artifacts/plans/edit-undo.pdf'] if plan else [])
        if not legacy:
            evidence['plan_document'] = 'docs/edit-undo.md' if plan else ''
        self.b.con.execute("UPDATE pm_tasks SET phase='landed',landed_head=?,evidence=? WHERE task_id=?",
                           ('a' * 40, json.dumps(evidence), tid))
        self.b.update_task('manager', tid, status='review')
        return tid

    def conversion(self, gid, status='planned', **extra):
        with patch.object(core.release, 'reading', return_value=self.root):
            self.manager.plan()
        r = dict(self.b.q1("SELECT * FROM pm_runs WHERE goal_id=? AND state='queued'", gid))
        tasks = [dict(title='Undo restores an edit', reasoning='Approach: the approved snapshot journal.',
                      body='Implement docs/edit-undo.md and test real Ctrl+Z.', depends=[]),
                 dict(title='New controls stay undoable', reasoning='Approach: audit the approved journal.',
                      body='Add the approved coverage gate.', depends=[0])]
        said = result(status, tasks=tasks if status == 'planned' else [], **extra)
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=?,session='S1' WHERE id=?",
                           (json.dumps(said), r['id']))
        self.manager.reconcile()
        return r

    def test_acceptance_converts_the_plan_and_dispatches_without_another_approval(self):
        import manager_plan_docs as plans
        tid = self.delivered()
        plans.reconcile(self.b)
        self.assertIsNone(self.b.q1("SELECT id FROM pm_goals WHERE source LIKE 'accepted-plan:%'"))
        store.accept(self.b, tid)  # Discord acceptance uses this same entry point
        gid = plans.start(self.b, tid)
        r = self.conversion(gid)
        self.assertIn('already authorized', r['prompt'])
        ids = [row[0] for row in self.b.q('SELECT task_id FROM pm_tasks WHERE goal_id=? ORDER BY task_id', gid)]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(self.b.task(i)['status'] == 'ready' for i in ids))
        self.assertEqual(self.b.task(ids[1])['depends'], [ids[0]])
        self.assertIn(f'T{tid}', self.b.task(ids[0])['links'])
        self.assertIn('docs/edit-undo.md', self.b.task(ids[0])['links'])
        self.assertEqual(self.b.q1('SELECT plan FROM pm_goals WHERE id=?', gid)[0], '')
        # The real worker scheduler claims the first task; its dependent waits for acceptance.
        with patch.object(core.Manager, 'free_agent', return_value='A3'), \
                patch.object(fe_sync, 'sessions', return_value=[]), \
                patch.object(fe_sync, 'app_processes', return_value=[]), \
                patch.object(fe_sync, 'sync_pull', return_value=Mock(ok=True)), \
                patch.object(fe_sync, 'git_out', return_value='b' * 40):
            self.manager.schedule()
        worker = self.b.q1("SELECT * FROM pm_runs WHERE role='worker' AND state='queued'")
        self.assertEqual(worker['task_id'], ids[0])
        self.assertEqual(self.b.task(ids[0])['status'], 'in_progress')
        self.assertEqual(self.b.task(ids[1])['status'], 'ready')
        # Repeated ticks and service restarts cannot duplicate the conversion.
        plans.reconcile(self.b)
        self.manager = core.Manager(self.b, self.config)
        plans.reconcile(self.b)
        self.assertEqual(plans.start(self.b, tid), gid)
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_tasks WHERE goal_id=?', gid)[0], 2)
        self.assertIsNone(plans.approval(self.b, gid))

    def test_board_acceptance_from_an_old_server_survives_restart_and_reads_legacy_pdf(self):
        import manager_plan_docs as plans
        tid = self.delivered(legacy=True)
        self.b.update_task('owner', tid, status='done')  # board/CLI, independent of the service's code
        self.manager = core.Manager(self.b, self.config)
        plans.reconcile(self.b)
        gid = self.b.q1("SELECT id FROM pm_goals WHERE source LIKE 'accepted-plan:%'")[0]
        self.assertIn('docs/edit-undo.md', self.b.q1('SELECT body FROM pm_goals WHERE id=?', gid)[0])
        self.conversion(gid)
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_tasks WHERE goal_id=?', gid)[0], 2)

    def test_ordinary_acceptance_does_not_start_implementation(self):
        import manager_plan_docs as plans
        tid = self.delivered(plan=False)
        store.accept(self.b, tid)
        plans.reconcile(self.b)
        self.assertIsNone(self.b.q1("SELECT id FROM pm_goals WHERE source LIKE 'accepted-plan:%'"))

    def test_historical_approvals_need_explicit_recovery_to_avoid_duplicate_work(self):
        import manager_plan_docs as plans
        tid = self.delivered(legacy=True)
        self.b.update_task('owner', tid, status='done')
        self.b.con.execute('UPDATE tasks SET updated=0 WHERE id=?', (tid,))
        plans.reconcile(self.b)
        self.assertIsNone(self.b.q1("SELECT id FROM pm_goals WHERE source LIKE 'accepted-plan:%'"))
        self.assertIsNotNone(plans.start(self.b, tid))  # the accepted undo plan's recovery

    def test_unresolved_choices_resume_with_the_original_approval(self):
        import manager_plan_docs as plans
        import manager_talk as talk
        tid = self.delivered()
        store.accept(self.b, tid)
        gid = plans.start(self.b, tid)
        self.conversion(gid, 'chat', asks=[{'question': 'Which unresolved choice?', 'options': ['A', 'B']}])
        self.assertIsNotNone(plans.approval(self.b, gid))
        self.assertIsNone(self.b.q1('SELECT task_id FROM pm_tasks WHERE goal_id=?', gid))
        talk.hear(self.b, gid, 'A')
        r = self.conversion(gid)
        self.assertIn('already authorized', r['prompt'])
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_tasks WHERE goal_id=?', gid)[0], 2)
        # After the accepted document is consumed, new work in this thread needs Approve.
        talk.hear(self.b, gid, 'Also plan something else')
        self.conversion(gid)
        self.assertTrue(self.b.q1('SELECT plan FROM pm_goals WHERE id=?', gid)[0])
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_tasks WHERE goal_id=?', gid)[0], 2)

    def test_new_owner_steering_is_read_before_tasks_start(self):
        import manager_plan_docs as plans
        tid = self.delivered()
        store.accept(self.b, tid)
        gid = plans.start(self.b, tid)
        self.b.con.execute("UPDATE pm_goals SET pending='Change the depth to 100' WHERE id=?", (gid,))
        # A pending message arriving during a turn prevents that turn's tasks from starting.
        with patch.object(core.release, 'reading', return_value=self.root):
            self.manager.plan()
        self.b.con.execute("UPDATE pm_goals SET pending='Change the depth to 100' WHERE id=?", (gid,))
        r = dict(self.b.q1("SELECT * FROM pm_runs WHERE goal_id=? AND state='queued'", gid))
        said = result('planned', tasks=[dict(title='Undo works', reasoning='Approved journal.', body='Test undo.', depends=[])])
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=? WHERE id=?", (json.dumps(said), r['id']))
        self.manager.reconcile()
        self.assertIsNone(self.b.q1('SELECT task_id FROM pm_tasks WHERE goal_id=?', gid))
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals WHERE id=?', gid)[0], 'planning')

    def test_plan_metadata_rejects_paths_outside_the_plan_directory(self):
        for path in ('../plan.md', 'docs/../plan.md', 'C:/plan.md', 'docs/plan.pdf'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                workers.validate_result(result(plan_document=path))
        self.assertEqual(workers.validate_result(result())['plan_document'], '')

    def test_accepting_pdf_tooling_or_formatting_does_not_approve_the_example_plan(self):
        import manager_plan_docs as plans
        evidence = result(artifacts=['engine/artifacts/plans/edit-undo.pdf'])
        for title in ('Plan documents print as illustrated PDFs', 'The undo plan arrives illustrated, as a PDF'):
            self.assertEqual(plans.document({'title': title, 'body': 'Use docs/edit-undo.md'}, evidence), '')
        task = {'title': 'A plan for undo', 'body': 'Write docs/edit-undo.md'}
        self.assertEqual(plans.document(task, evidence), 'docs/edit-undo.md')
        self.assertEqual(plans.document(task, dict(evidence, plan_document='')), '')


class BriefTests(unittest.TestCase):
    """What Yotam reads: the problem, the bottom line and his call, then where the rest is."""
    setUp, tearDown = ManagerTests.setUp, ManagerTests.tearDown
    goal, task, new_run = ManagerTests.goal, ManagerTests.task, ManagerTests.new_run

    def last(self):
        return dict(self.b.q1('SELECT * FROM pm_outbox ORDER BY id DESC LIMIT 1'))

    def test_the_thread_opens_on_the_tasks_problem_with_the_spec_attached(self):
        spec = 'Scope: ' + 'the probe radius and the knot test, ' * 30
        self.goal([dict(title='Near misses hit the knot', body=spec, depends=[],
                        problem='A shot that grazes the knot slides past it instead of killing.')])
        row = self.last()
        self.assertTrue(row['body'].startswith('**What we are fixing:** A shot that grazes the knot'))
        self.assertIn('**Why:** So the tool does what it says.', row['body'])
        self.assertIn('**Your call:** Approve it', row['body'])
        self.assertNotIn('probe radius', row['body'])
        report = Path(json.loads(row['files'])[0])
        self.assertEqual(report.name, f'T{row["task_id"]}-report.txt')
        self.assertTrue(report.is_relative_to(board.files_dir()))
        self.assertEqual(report.read_text(encoding='utf-8'), '## What to do\n' + spec.strip())

    def test_a_planned_task_is_a_proposal_until_yotam_approves_it(self):
        # D195: each task opens as an idea with its reasoning, and runs only once approved.
        import discord
        store.receive(self.b, 'owner:ramble', 'reply', 'the brute should stagger when shot in the leg')
        self.manager.input_events()
        gid = self.b.q1('SELECT id FROM pm_goals')[0]
        self.b.con.execute("UPDATE pm_goals SET talk='' WHERE id=?", (gid,))  # a goal from before D226's threads
        self.assertIn('reasoning', self.manager.prompt('planner', goal=gid))
        self.assertIn('few and whole', self.manager.prompt('planner', goal=gid))  # D196: not smaller tasks
        rid = store.queue_run(self.b, 'planner', 'codex', self.root, 'prompt', goal=gid)
        why = 'A leg shot that does nothing reads as a miss; the player should see the brute buckle.'
        plan = result('planned', goal_name='Brute stagger', tasks=[
            dict(title='Leg hits reach the brute', body='Scope: the push', reasoning=why, problem='', depends=[]),
            dict(title='The stagger', body='Scope: the stumble', reasoning='It sells the hit.', problem='', depends=[0]),
            dict(title='Tune it', body='Scope: knobs', reasoning='Maybe later.', problem='', depends=[])])
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=? WHERE id=?", (json.dumps(plan), rid))
        self.manager.reconcile()
        first, second, third = [r[0] for r in self.b.q('SELECT task_id FROM pm_tasks ORDER BY task_id')]
        t = self.b.task(first)
        self.assertEqual(t['status'], 'idea')
        self.assertEqual(store.reasoning(t['body']), why)
        self.assertEqual(store.without_reasoning(t['body']), '## What to do\nScope: the push')
        self.assertIn('Nothing starts until you approve it there',
                      self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', 'result:' + rid)[0])
        self.assertIn(f'**Why:** {why}', self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', f'task:{first}')[0])
        self.assertIn(f'only after T{first}', self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', f'task:{second}')[0])
        store.control(self.b, 'resume')
        with patch.object(self.manager, 'queue_task', return_value=False) as queue:
            self.manager.schedule()
        queue.assert_not_called()
        # the thread's buttons, and the form that holds the current reasoning
        async def exercise(tid, dedup):
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.send = AsyncMock(return_value=Mock(id=1))
            client.destination = AsyncMock(return_value=thread)
            await client.deliver_row(dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', dedup)))
            ids = [c.custom_id for c in thread.send.await_args.kwargs['view'].children]
            click = Mock(id=5, data={}, response=Mock(send_modal=AsyncMock(), send_message=AsyncMock()))
            await client.decide(click, 'why', tid)
            await client.close()
            form = click.response.send_modal.await_args
            return ids, form and form.args[0].children[0].default
        ids, default = asyncio.run(exercise(first, f'task:{first}'))
        self.assertEqual(ids, [f'pm:approve:{first}', f'pm:why:{first}', f'pm:drop:{first}', 'pm:pause'])
        self.assertEqual(default, why)
        # a reply adds to the reasoning; Edit reasoning replaces it; neither starts it
        store.receive(self.b, 'discord:r1', 'reply', 'Only the brute, not the mutant.', first)
        self.manager.input_events()
        self.assertEqual(store.reasoning(self.b.task(first)['body']), why + '\n\nYotam: Only the brute, not the mutant.')
        self.assertIsNotNone(self.b.q1('SELECT 1 FROM pm_outbox WHERE dedup=?', 'proposal:discord:r1'))
        store.receive(self.b, 'discord:r2', 'reason', 'The brute must visibly buckle on a leg shot.', first)
        self.manager.input_events()
        body = self.b.task(first)['body']
        self.assertEqual(store.reasoning(body), 'The brute must visibly buckle on a leg shot.')
        self.assertIn('Scope: the push', body)
        self.assertEqual(self.b.task(first)['status'], 'idea')
        self.b.con.execute('UPDATE pm_tasks SET agent=? WHERE task_id=?', ('A2', first))
        self.assertIn('The brute must visibly buckle', self.manager.prompt('worker', first))
        self.assertIn('by the task skill', self.manager.prompt('worker', first))  # D224: the rules are the skill's
        # approve, drop; a second decision on the same task is refused
        for key, kind, tid in [('a1', 'approve', second), ('a2', 'approve', first), ('d3', 'drop', third),
                               ('a4', 'approve', first)]:
            store.receive(self.b, key, kind, '', tid)
        self.manager.input_events()
        self.assertEqual([self.b.task(x)['status'] for x in (first, second, third)], ['ready', 'ready', 'dropped'])
        self.assertIn(f'and T{first} is done', self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', 'approve:a1')[0])
        self.assertIn('no longer a proposal', self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', 'input-error:a4')[0])
        with patch.object(self.manager, 'queue_task', return_value=False) as queue:
            self.manager.schedule()
        self.assertEqual([c.args[0]['task_id'] for c in queue.call_args_list], [first])
        ids, _ = asyncio.run(exercise(first, 'proposal:discord:r2'))
        self.assertEqual(ids, ['pm:pause'])  # approved: the buttons are gone

    def test_a_retry_the_manager_makes_itself_asks_nothing_and_does_not_ping(self):
        _, tid = self.task()
        store.set_setting(self.b, 'problem:' + str(tid), 'Crates fall through the floor.')
        r = self.new_run(tid, data=result('complete', summary='Long note. ' * 80,
                                          bottom_line='The fix misses crates in later slots.'))
        self.manager.fail(r, 'Invalid result: ' + 'Long detail. ' * 80)
        row = self.last()
        self.assertFalse(row['ping'])
        self.assertIn('**What we are fixing:** Crates fall through the floor.', row['body'])
        self.assertIn("report came back malformed. The fix misses crates in later slots. "
                      'It is trying again by itself (attempt 2 of 2).', row['body'])
        self.assertIn('**Your call:** nothing for now.', row['body'])
        self.assertIn('session `exact-session`', row['body'])
        self.assertNotIn('Long detail', row['body'])
        # The second failure stops the task: now it is his call, and it pings.
        r = self.new_run(tid, data=result('complete'))
        self.manager.fail(r, 'Invalid result: still wrong')
        row = self.last()
        self.assertTrue(row['ping'])
        self.assertIn('It has failed 2 times, so it has stopped.', row['body'])
        self.assertIn('**Your call:** Reply here with what should change', row['body'])
        self.assertIn('> Invalid result: still wrong', row['body'])

    def test_a_question_leads_with_the_problem_and_numbers_the_asks(self):
        _, tid = self.task()
        r = self.new_run(tid, data=result('blocked', summary='The detail of the choice.',
                                          problem='The lamp dims too slowly.',
                                          bottom_line='Two ways to fix it; they differ in cost.',
                                          asks=['Dim it on the GPU (faster, recommended)?', 'Or on the CPU?']))
        self.manager.completed(r)
        row = self.last()
        self.assertTrue(row['ping'])
        body = row['body']
        self.assertLess(body.index('The lamp dims too slowly.'), body.index('Two ways to fix it'))
        self.assertIn('**Your call:**\n1. Dim it on the GPU (faster, recommended)?\n2. Or on the CPU?', body)
        self.assertIn(f'**More:** board T{tid}', body.replace('> The detail of the choice.\n\n', ''))

    def test_a_result_from_before_the_brief_still_validates(self):
        old = result()
        for k in ('problem', 'bottom_line', 'asks'):
            old.pop(k, None)
        self.assertEqual(workers.validate_result(old)['asks'], [])
        self.assertEqual(brief.bottom_line(dict(summary='It works now. Here is how.')), 'It works now.')


class ImageReachesDiscordTests(unittest.TestCase):
    """D304: every image a run or an agent shows Yotam goes to Discord as an attachment, by
    every route. T89, T96 and T101 each sent him text where the pictures should have been:
    only a landed task's post carried its artifacts, and a board message's images went as
    markdown."""
    setUp, tearDown = ManagerTests.setUp, ManagerTests.tearDown
    goal, task, new_run = ManagerTests.goal, ManagerTests.task, ManagerTests.new_run

    def png(self, name):
        """A small distinct PNG in the run's checkout; returns its bytes."""
        data = b'\x89PNG\r\n\x1a\n' + name.encode()
        d = self.root / 'worker'
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        return data

    def sent(self, dedup_prefix):
        row = dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup LIKE ? ORDER BY id DESC LIMIT 1',
                             dedup_prefix + '%'))
        return row, [Path(f).read_bytes() for f in json.loads(row['files']) if Path(f).is_file()]

    def question(self, **extra):
        return result('blocked', summary='Pick a concept.', bottom_line='Two concepts, pick one.',
                      asks=[{'question': 'Which concept?', 'options': ['1', '2']}], **extra)

    def test_a_question_carries_its_artifacts_and_the_board_shows_them_too(self):
        _, tid = self.task()
        one, two = self.png('concept-1.png'), self.png('concept-2.png')
        self.manager.completed(self.new_run(tid, data=self.question(artifacts=['concept-1.png', 'concept-2.png'])))
        _, files = self.sent('question:')
        self.assertIn(one, files)
        self.assertIn(two, files)
        body = self.b.q1("SELECT body FROM messages WHERE subject=? ORDER BY id DESC", f'T{tid}: decision needed')[0]
        self.assertEqual(len(board.IMAGE_RE.findall(body)), 2)

    def test_an_artifact_written_with_a_note_still_goes(self):
        _, tid = self.task()
        one = self.png('concept-1.png')
        self.manager.completed(self.new_run(tid, data=self.question(artifacts=['concept-1.png (the lighter handle)'])))
        row, files = self.sent('question:')
        self.assertIn(one, files)
        self.assertNotIn('not attached', row['body'])

    def test_an_artifact_that_cannot_go_is_named_not_lost(self):
        _, tid = self.task()
        self.manager.completed(self.new_run(tid, data=self.question(artifacts=['engine/artifacts/gone.png'])))
        row, _ = self.sent('question:')
        self.assertIn('not attached: engine/artifacts/gone.png: no such file', row['body'])

    def test_a_worker_that_keeps_stopping_shows_what_it_has(self):
        _, tid = self.task()
        shot = self.png('shot.png')
        with patch.object(self.manager, 'continues', return_value=core.CONTINUE_SILENCE):
            self.manager.completed(self.new_run(tid, data=result('continue', artifacts=['shot.png'])))
        self.assertIn(shot, self.sent('question:')[1])

    def test_a_landed_task_still_carries_its_artifacts(self):
        _, tid = self.task()
        shot = self.png('after.png')
        with patch.object(fe_sync, 'git_out', side_effect=lambda _, *a: 'a' * 40 if a[0] == 'rev-parse' else ''), \
             patch.object(board, 'on_trunk', return_value=True):
            self.manager.completed(self.new_run(tid, data=result(artifacts=['after.png'])))
        self.assertIn(shot, self.sent('landed:')[1])

    def test_a_planners_reply_carries_its_artifacts(self):
        gid, _ = self.goal()
        ref = self.png('reference.png')
        self.manager.completed(self.new_run(None, role='planner', data=self.question(artifacts=['reference.png'])))
        self.assertIn(ref, self.sent('talk:')[1])  # D226: a reply in the goal's thread
        self.b.con.execute("UPDATE pm_goals SET talk='' WHERE id=?", (gid,))  # a goal from before D226
        self.manager.completed(self.new_run(None, role='planner', data=self.question(artifacts=['reference.png'])))
        self.assertIn(ref, self.sent('result:')[1])

    def test_an_agents_board_reply_with_images_goes_with_them(self):
        _, tid = self.task()
        self.png('before.png')
        mid = self.b.add_message('A3', 'owner', f'T{tid}: before and after',
                                 board.with_images('Before, then after.', [self.root / 'worker' / 'before.png']),
                                 kind='reply', topic=f'T{tid}')
        self.manager.mirror_board()
        self.assertIn((self.root / 'worker' / 'before.png').read_bytes(), self.sent(f'board:{mid}')[1])

    def test_task_attach_reaches_the_tasks_thread(self):
        _, tid = self.task()
        self.png('concept-1.png')
        board.cmd_task(self.b, 'A3', board.parser().parse_args(
            ['task', 'attach', f'T{tid}', str(self.root / 'worker' / 'concept-1.png')]))
        self.manager.mirror_board()
        self.assertIn((self.root / 'worker' / 'concept-1.png').read_bytes(), self.sent('board:')[1])


class CrewReportTests(unittest.TestCase):
    """D319: Yotam could not tell what a crew night did. T117's thread said "Done and on the main
    branch" and then "ready for the manager to retry"; its directive, verdict and numbers were in an
    attached text file, and T94's Target: and Tool: prose never matched a detour."""
    setUp, tearDown = ManagerTests.setUp, ManagerTests.tearDown
    goal, task, new_run = ManagerTests.goal, ManagerTests.task, ManagerTests.new_run

    COMMIT = dict(sha='c' * 40, subject='Keep bounce controls independent of GPU resources', task=None, body=(
        'Directive: Give bounce controls a home independent of the GPU renderer.\n'
        'Problem: Changing a saved bounce control requires lighting to import the GPU bounce module.\n'
        'Expect: no lighting -> bounce dependency; replay hashes identical.\n\nCrew: T1\nCrew-Review: keep\n'))

    def crew_task(self, title):
        _, tid = self.task()
        self.b.con.execute('UPDATE tasks SET title=? WHERE id=?', (title, tid))
        return tid

    def land(self, tid, data, commits, review=None):
        import fe_crew
        with patch.object(fe_sync, 'git_out', side_effect=lambda _, *a: 'a' * 40 if a[0] == 'rev-parse' else ''), \
             patch.object(board, 'on_trunk', return_value=True), \
             patch.object(fe_crew, 'landed_commits', return_value=commits), \
             patch.object(fe_crew, 'saved_review', return_value=review):
            self.manager.completed(self.new_run(tid, data=data))
        msg = self.b.q1("SELECT body FROM messages WHERE subject=?", f'T{tid}: ready for acceptance')[0]
        post = self.b.q1("SELECT body FROM pm_outbox WHERE dedup LIKE 'landed:%' ORDER BY id DESC")[0]
        return msg, post

    def test_a_code_crew_landing_says_what_changed_its_numbers_verdict_and_what_was_turned_down(self):
        tid = self.crew_task('Code crew: 2026-10-05')
        crew = dict(changes=[dict(commit='c' * 7, what='The bounce lighting controls got their own home.',
                                  why='Changing one meant reading the GPU renderer too.',
                                  numbers='lighting loop 18 -> 17 module edges')],
                    rejected=[dict(what='Splitting the main render function', why='the reviewer found it moved load')],
                    target=dict(cause='', key=''), tool='')
        msg, post = self.land(tid, result(problem='Lighting controls were tangled with the renderer.',
                                          bottom_line='They have their own home now.', crew=crew), [self.COMMIT],
                              review=dict(verdict='keep', model='sonnet', summary='Load fell. Names are clear.'))
        self.assertTrue(msg.startswith(f'Code crew T{tid}: 1 commit on the main branch.'))
        for line in ('1. The bounce lighting controls got their own home.',
                     '   Why: Changing one meant reading the GPU renderer too.',
                     '   Numbers: lighting loop 18 -> 17 module edges',
                     '   Reviewer: keep (sonnet): Load fell.',
                     '- Splitting the main render function: the reviewer found it moved load',
                     'Checks: 1 passed, 8 not applicable.'):
            self.assertIn(line, msg)
        self.assertLess(msg.index('Checks:'), msg.index('---- The technical record'))  # the record follows
        self.assertIn('Validated change', msg)
        self.assertIn('**What we are fixing:** Lighting controls were tangled', post)
        self.assertIn('• The bounce lighting controls got their own home. (lighting loop 18 -> 17 module edges)'
                      ' — reviewer: keep', post)
        self.assertIn('Turned down: Splitting the main render function', post)

    def test_a_crew_run_with_no_report_falls_back_to_its_directives(self):
        tid = self.crew_task('Code crew: 2026-10-05')
        msg, _ = self.land(tid, result(), [self.COMMIT])
        self.assertIn('1. Give bounce controls a home independent of the GPU renderer.', msg)
        self.assertIn('   Why: Changing a saved bounce control requires lighting', msg)
        self.assertIn('   Numbers: expected: no lighting -> bounce dependency', msg)
        self.assertIn("Reviewer: keep (its Crew-Review trailer; the review's own words were not kept)", msg)
        self.assertIn('Turned down: not reported', msg)

    def test_any_other_landing_is_unchanged(self):
        _, tid = self.task()
        msg, post = self.land(tid, result(), [self.COMMIT])
        self.assertTrue(msg.startswith(f'T{tid} landed:'))
        self.assertNotIn('What changed', msg + post)

    def test_a_process_crews_target_and_tool_are_keys_not_prose(self):
        import fe_crew
        t94 = {'summary': 'Target: environment | sh: inline python (the "Permission ... denied" pattern)\n'
                          'Tool: no new tool. Planner runs now run `python engine/tools/fe_index.py ...`'}
        t118 = {'summary': 'Target: workaround | run.py (also pairs.py, p20.py)\n'
                           'Tool: python engine/tools/fe_manager.py gpu -- COMMAND ARGS (and gpu --bench)'}
        self.assertEqual(fe_crew.reported_targets(t94), [('environment', 'sh: inline python')])
        self.assertEqual(fe_crew.reported_tools(t94), [])
        self.assertEqual(fe_crew.reported_targets(t118), [('workaround', 'run.py')])
        self.assertEqual(fe_crew.reported_tools(t118), ['python engine/tools/fe_manager.py gpu'])
        given = dict(t118, crew=dict(target=dict(cause='tool', key='sh: fe_gpu share'), tool='`fe_gpu.py run`'))
        self.assertEqual((fe_crew.reported_targets(given), fe_crew.reported_tools(given)),
                         ([('tool', 'sh: fe_gpu share')], ['fe_gpu.py run']))

    def test_the_morning_after_reaches_each_crew_tasks_thread_once(self):
        import fe_crew
        import manager_crew
        code, proc = (self.b.add_task('manager', f'{k}: 2026-10-05', body='b', status='ready')
                      for k in ('Code crew', 'Process crew'))
        s = dict(day='2026-10-06',
                 code=dict(crew=[dict(task=code, score=0.0, passed=True, code_lines=7, dup=[5, 5], rework=None,
                                      reviews=['keep'])]),
                 process=dict(targets=[dict(task=proc, cause='workaround', key='run.py', before=6.0, since=0.0),
                                       dict(task=proc, tool='fe_manager.py gpu', uses=3, sessions=6)]))
        for _ in range(2):
            with self.b.tx():
                manager_crew.after_numbers(self.b, fe_crew.followups(s))
        rows = self.b.q("SELECT topic, body FROM messages WHERE subject LIKE '%the morning after'")
        self.assertEqual(sorted(r['topic'] for r in rows), sorted([f'T{code}', f'T{proc}']))
        by = {r['topic']: r['body'] for r in rows}
        self.assertIn('score +0 passed, code lines +7, duplicated tokens 5 -> 5, reviews keep', by[f'T{code}'])
        self.assertIn('target workaround run.py: 6.0 -> 0.0 per 100 sessions', by[f'T{proc}'])
        self.assertIn('tool `fe_manager.py gpu`: used 3 times in 6 sessions', by[f'T{proc}'])
        self.assertEqual(len(self.b.q("SELECT id FROM pm_outbox WHERE dedup LIKE 'crew-after:%'")), 2)

    def test_a_result_without_a_crew_report_is_still_valid(self):
        r = result()
        r.pop('crew', None)
        self.assertEqual(workers.validate_result(r)['crew']['changes'], [])
        with self.assertRaises(ValueError):
            workers.validate_result(result(crew=dict(changes='three commits')))


class ReleaseTests(unittest.TestCase):
    """D189: the service runs pushed code from a release and hands itself to the next one.

    Real processes and real Git: a scratch repo holding these very tools, pushed to a
    scratch remote, and the service booted from its checkout with no Discord."""

    def setUp(self):
        import manager_release as release
        self.release = release
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'FE_BOARD_DIR': str(root / 'board'), 'FE_MANAGER_OFFLINE': '1',
                                           'FE_MANAGER_POLL_S': '0.3', 'FE_MANAGER_UPGRADE_S': '0.5'})
        self.env.start()
        self.home, remote = root / 'home', root / 'remote.git'
        git = lambda cwd, *a: subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True,
                                             creationflags=fe_sync.NO_WINDOW)
        self.git = git
        git(root, 'init', '-q', '--bare', '-b', 'main', str(remote))
        git(root, 'clone', '-q', str(remote), str(self.home))
        for k, v in (('user.name', 'test'), ('user.email', 't@example.com'), ('core.autocrlf', 'false')):
            git(self.home, 'config', k, v)
        tools = self.home / 'engine' / 'tools'
        tools.mkdir(parents=True)
        here = Path(__file__).resolve().parent
        # D330: the game's tools (its adapters) first, then the studio's over the stubs that
        # forward to it: one flat folder, the layout before the submodule, which still runs.
        import studio_config
        game = studio_config.game_tools_dir()
        for f in [*game.glob('*.py'), *here.glob('*.py'), here / release.REQUIREMENTS]:
            if f.is_file():
                (tools / f.name).write_bytes(f.read_bytes())
        (tools / 'fe_agents.json').write_text(json.dumps({'remote': 'origin', 'branch': 'main', 'agents': {
            'A9': {'path': str(root / 'nowhere')}}}), encoding='utf-8')
        # The game's settings, where the registry is among them (studio_config.agents_path()).
        import studio_config
        (self.home / studio_config.CONFIG).write_bytes((studio_config.repo_root() / studio_config.CONFIG).read_bytes())
        self.push('the manager')
        cfg = host.config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(dict(repo=str(self.home), codex=sys.executable, claude=sys.executable,
                                       guild_id='11', channel_id='22', owner_id='33')), encoding='utf-8')
        with board.Board() as b:
            store.set_setting(b, 'mode', 'paused')

    def tearDown(self):
        import psutil
        for p in psutil.process_iter(['cmdline']):
            try:
                if any(self.tmp.name.lower() in (c or '').lower() for c in p.info['cmdline'] or []):
                    p.kill()
                    p.wait(10)
            except psutil.Error:
                pass
        self.env.stop()
        self.tmp.cleanup()

    def push(self, message, name=None, text=None):
        if name:
            p = self.home / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(p.read_text(encoding='utf-8') + text if p.exists() else text, encoding='utf-8')
        self.git(self.home, 'add', '-A')
        self.git(self.home, 'commit', '-q', '-m', message)
        self.git(self.home, 'push', '-q', 'origin', 'HEAD:main')
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.home, capture_output=True, text=True,
                              check=True).stdout.strip()

    def state(self, name):
        with board.Board() as b:
            return self.release.get(b, name) or {}

    def until(self, what, timeout=90):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = what()
            if got:
                return got
            time.sleep(0.2)
        import psutil
        procs = [f'{p.pid} {" ".join(p.info["cmdline"] or [])[-120:]}' for p in psutil.process_iter(['cmdline'])
                 if any(self.tmp.name.lower() in (c or '').lower() for c in p.info['cmdline'] or [])]
        logs = ''.join(f'\n-- {n}:\n' + (board.board_dir() / 'manager' / n).read_text(
            encoding='utf-8', errors='replace')[-2500:] for n in ('service.log', 'manager.log')
            if (board.board_dir() / 'manager' / n).exists())
        self.fail(f'timed out; handoff {self.state(self.release.HANDOFF)}; service '
                  f'{self.state(self.release.SERVICE)}; processes:\n' + '\n'.join(procs) + logs)

    def serving(self, commit):
        s = self.state(self.release.SERVICE)
        return s if s.get('commit') == commit and s.get('settled') else None

    def test_a_pushed_manager_change_hands_over_and_a_broken_one_is_rejected(self):
        import psutil
        c1 = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.home, capture_output=True, text=True).stdout.strip()
        # A boot from the checkout starts a release; the checkout itself never serves.
        subprocess.run([sys.executable, str(self.home / 'engine/tools/fe_manager.py'), 'serve'], check=True,
                       timeout=120, creationflags=fe_sync.NO_WINDOW)
        first = self.until(lambda: self.serving(c1))
        self.assertEqual(Path(first['path']).parent, self.release.releases_dir())
        # A second boot while it serves starts nothing.
        self.assertIsNone(self.release.boot(host.load_config()))

        # An edit in the checkout reaches nothing that runs: the service runs its release.
        (self.home / 'engine/tools/manager_store.py').write_text('raise SystemExit("half-edited")\n')
        self.git(self.home, 'checkout', '--', 'engine/tools/manager_store.py')

        # A pushed change to a file the manager runs: a candidate starts beside it and takes over.
        c2 = self.push('manager v2', 'engine/tools/manager_store.py', '\n# v2\n')
        second = self.until(lambda: self.serving(c2))
        self.assertNotEqual(second['pid'], first['pid'])
        self.until(lambda: not psutil.pid_exists(first['pid']), 30)
        self.assertEqual(self.release.read_pointer()['commit'], c2)
        with board.Board() as b:
            note = b.q1("SELECT body,ping FROM pm_outbox WHERE dedup=?", 'release:' + c2)
        self.assertIn(f'Manager updated to {c2[:12]} (was {c1[:12]})', note['body'])
        self.assertIn('manager v2', note['body'])
        self.assertFalse(note['ping'])

        # A push that changes nothing it runs starts no candidate.
        c3 = self.push('docs only', 'docs/note.md', 'words\n')
        time.sleep(3)
        self.assertEqual(self.state(self.release.SERVICE)['pid'], second['pid'])
        self.assertNotEqual(self.state(self.release.HANDOFF).get('commit'), c3)

        # A broken one fails its own check; the service keeps serving and Yotam hears once.
        c4 = self.push('manager v3, broken', 'engine/tools/manager_core.py', '\ndef broken(:\n')
        h = self.until(lambda: (lambda h: h if h.get('state') == 'rejected' else None)(
            self.state(self.release.HANDOFF)))
        self.assertEqual(h['commit'], c4)
        self.assertIn('manager_core', h['reason'])
        self.assertEqual(self.state(self.release.SERVICE)['pid'], second['pid'])
        self.assertTrue(psutil.pid_exists(second['pid']))
        with board.Board() as b:
            rows = b.q("SELECT body,ping FROM pm_outbox WHERE dedup LIKE 'release-rejected:%'")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['ping'])
        self.assertIn(c4[:12], rows[0]['body'])

    def test_a_boot_starts_the_newest_push_and_not_one_that_died_starting(self):
        import psutil
        c1 = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.home, capture_output=True, text=True).stdout.strip()
        cfg = host.load_config()
        self.release.boot(cfg)
        first = self.until(lambda: self.serving(c1))
        psutil.Process(first['pid']).kill()
        self.until(lambda: not host.serving(), 30)
        # Nobody serves, so a boot starts what was pushed since, without a handoff.
        c2 = self.push('manager v2', 'engine/tools/manager_store.py', '\n# v2\n')
        self.release.boot(cfg)
        second = self.until(lambda: self.serving(c2))
        self.assertEqual(self.release.read_pointer()['commit'], c2)
        # A pushed service that dies before its first step is rejected at the next boot,
        # which starts the settled release instead.
        c3 = self.push('manager v3, dies', 'engine/tools/manager_core.py', '\n# v3\n')
        psutil.Process(second['pid']).kill()
        self.until(lambda: not host.serving(), 30)
        with board.Board() as b:
            self.release.put(b, self.release.SERVICE, dict(second, commit=c3, key=self.release.key(
                str(self.home), c3), pid=99999999, settled=False))
        self.release.boot(cfg)
        third = self.until(lambda: self.serving(c2))
        self.assertNotEqual(third['pid'], second['pid'])
        self.assertIn(self.release.key(str(self.home), c3), self.state(self.release.REJECTED) or [])


class ReleaseKeyTests(unittest.TestCase):
    """D189: the release key covers what the manager runs: the studio's files (or the commit a
    studio submodule is pinned to) and the game's studio.toml and adapters, and nothing else."""

    def test_the_key_follows_the_studio_and_not_the_game(self):
        import manager_release as release
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            repo = Path(d)
            git = lambda *a: subprocess.run(['git', *a], cwd=repo, check=True, capture_output=True, text=True,  # noqa: E731
                                            creationflags=fe_sync.NO_WINDOW).stdout.strip()
            git('init', '-q', '-b', 'main')
            for k, v in (('user.name', 'test'), ('user.email', 't@example.com'), ('core.autocrlf', 'false')):
                git('config', k, v)

            def commit(path, text):
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(text, encoding='utf-8')
                git('add', '-A')
                git('commit', '-q', '-m', path)
                return git('rev-parse', 'HEAD')
            commit('engine/tools/fe_x.py', 'a\n')
            commit('engine/tools/board_ui/app.js', 'b\n')
            c1 = commit('studio.toml', 'name = "G"\n')
            c2 = commit('engine/crates/x.rs', 'fn x() {}\n')
            c3 = commit('engine/tools/fe_x.py', 'a2\n')
            with patch.object(release.studio_config, 'studio_rel', return_value='engine/tools'):
                self.assertEqual(sorted(release.files(repo, c1)),
                                 ['engine/tools/board_ui/app.js', 'engine/tools/fe_x.py', 'studio.toml'])
                self.assertEqual(release.key(repo, c1), release.key(repo, c2))  # the game's code: no new release
                self.assertNotEqual(release.key(repo, c2), release.key(repo, c3))
            # The studio as a submodule: the key is the commit the game pins.
            for sha, name in (('1' * 40, 'c4'), ('2' * 40, 'c5')):
                git('update-index', '--add', '--cacheinfo', f'160000,{sha},studio')
                git('commit', '-q', '-m', name)
            c4, c5 = git('rev-parse', 'HEAD~1'), git('rev-parse', 'HEAD')
            with patch.object(release.studio_config, 'studio_rel', return_value='studio'):
                self.assertEqual(release.files(repo, c4)['studio'], '1' * 40)
                self.assertNotEqual(release.key(repo, c4), release.key(repo, c5))
                self.assertIn('c5', release.changes(repo, c4, c5))


class HostTests(unittest.TestCase):
    def test_a_cli_that_updated_itself_into_a_new_folder_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / 'bin' / 'ca9abb0b' / 'codex.EXE'
            new = Path(d) / 'bin' / 'c6fe824d' / 'codex.exe'
            new.parent.mkdir(parents=True)
            new.write_bytes(b'')
            self.assertEqual(Path(host.moved(str(old))).resolve(), new.resolve())
            self.assertEqual(host.moved(str(new)), str(new))
            gone = str(Path(d) / 'nowhere' / 'x' / 'codex.exe')
            self.assertEqual(host.moved(gone), gone)


class ResetHintTests(unittest.TestCase):
    """The wait comes from what the provider itself said, not from a flat guess."""

    def read(self, text):
        return workers.reset_seconds(text, NOW)

    def test_each_form_the_providers_use(self):
        for text, seconds in (
                ('Rate limit reached. Please try again in 3h 20m.', 3 * 3600 + 20 * 60),
                ('usage limit reached; try again in 45 minutes', 45 * 60),
                ('Too many requests, retry in 2 hours', 2 * 3600),
                ('usage limit; available again in 1h 5min', 3600 + 5 * 60),
                ('quota exhausted, resets at 14:00', 2 * 3600),
                ('rate limit reached, try again at 2:30 PM', 2 * 3600 + 30 * 60)):
            with self.subTest(text=text):
                self.assertEqual(self.read(text), seconds)

    def test_a_zoned_timestamp_is_read_in_its_own_zone(self):
        when = datetime.datetime(2026, 9, 30, 13, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.read('limit resets 2026-09-30T13:30:00Z'), int(when.timestamp() - NOW))

    def test_a_timestamp_with_no_zone_is_read_as_local(self):
        when = datetime.datetime(2026, 9, 30, 15, 45).astimezone()
        self.assertEqual(self.read('try again at 2026-09-30 15:45'), int(when.timestamp() - NOW))

    def test_a_clock_already_past_today_means_tomorrow(self):
        self.assertEqual(self.read('quota exhausted, resets at 11:00'), 23 * 3600)

    def test_the_real_chatgpt_limits_read_five_days_out(self):
        for text in (CODEX_LIMIT, CODEX_LIMIT_REPEATED):
            with self.subTest(text=text[:48]):
                self.assertEqual(self.read(text), workers.RESET_MAX)

    def test_the_clamp_holds_both_ends(self):
        self.assertEqual(self.read('try again in 30 seconds'), workers.RESET_MIN)
        self.assertEqual(self.read('try again in 3 days'), workers.RESET_MAX)
        self.assertEqual(self.read('try again in 20 minutes'), 20 * 60)   # in between, untouched

    def test_text_with_no_deadline_gives_none(self):
        for text in ('You have hit your usage limit.', 'exit 1: rate limit reached',
                     'insufficient_quota: purchase more credits', ''):
            with self.subTest(text=text):
                self.assertIsNone(self.read(text))

    def test_a_log_timestamp_is_not_a_deadline(self):
        # codex's own ERROR line sits in the same dump as its reset hint; nothing cues it.
        self.assertIsNone(self.read('2026-09-29T23:55:51.221171Z ERROR codex_core::session: '
                                    'failed to record rollout items'))


class SchemaMigrationTests(unittest.TestCase):
    """A v2 store gains the cool-off columns without losing what it holds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'board.db'

    def tearDown(self):
        self.tmp.cleanup()

    def make_v2(self):
        current = [s for s in store.DDL.split(';') if 'pm_providers' in s]
        self.assertEqual(len(current), 1)
        ddl = board.DDL + store.DDL.replace(
            current[0] + ';', 'CREATE TABLE pm_providers(name TEXT PRIMARY KEY, '
                              'reason TEXT NOT NULL, retry_at REAL NOT NULL);')
        con = sqlite3.connect(str(self.path), isolation_level=None)
        con.execute('PRAGMA journal_mode=WAL')
        for stmt in ddl.split(';'):
            if stmt.strip():
                con.execute(stmt)
        con.execute(board.ADDED_DDL)
        con.execute('INSERT INTO pm_providers VALUES(?,?,?)', ('codex', 'allowance', 1790000000.0))
        con.execute('PRAGMA user_version=2')
        con.close()

    def test_a_v2_store_migrates_in_place(self):
        self.make_v2()
        with board.Board(self.path) as b:
            self.assertEqual(b.con.execute('PRAGMA user_version').fetchone()[0], board.SCHEMA)
            self.assertEqual(dict(b.q1("SELECT * FROM pm_providers WHERE name='codex'")),
                             {'name': 'codex', 'reason': 'allowance', 'retry_at': 1790000000.0,
                              'blocks': 0, 'window_s': 0.0})
            store.block_provider(b, 'codex', 'allowance', 1790003600.0, 2, 3600.0)
        with board.Board(self.path) as b:      # opening it again migrates nothing further
            self.assertEqual(b.con.execute('PRAGMA user_version').fetchone()[0], board.SCHEMA)
            p = store.snapshot(b)['providers'][0]
            self.assertEqual((p['blocks'], p['window_s']), (2, 3600.0))

    def test_a_deferred_v2_store_gains_the_columns_and_bumps_later(self):
        # The T29 deadlock: A3 was leased at schema 2 and could not pull, so every
        # schema-3 tool (the manager's GPU wrapper among them) refused the store.
        root = Path(self.tmp.name)
        self.path = root / 'BodySimulation/board/board.db'
        self.path.parent.mkdir(parents=True)
        self.make_v2()
        source = root / 'leased/engine/tools/fe_board.py'
        source.parent.mkdir(parents=True)
        source.write_text('SCHEMA = 2\n', encoding='utf-8')
        registry = {'agents': {'A3': {'path': str(root / 'leased')}}}
        with patch.dict(os.environ, {'LOCALAPPDATA': str(root), 'FE_BOARD_DIR': str(self.path.parent)}), \
             patch.object(board, 'registry', return_value=registry):
            for _ in range(2):
                with board.Board() as b:
                    self.assertEqual(b.q1('PRAGMA user_version')[0], 2)
                    store.block_provider(b, 'codex', 'allowance', 1790003600.0, 2, 3600.0)
                    p = store.snapshot(b)['providers'][0]
                    self.assertEqual((p['blocks'], p['window_s']), (2, 3600.0))
            source.write_text(f'SCHEMA = {board.SCHEMA}\n', encoding='utf-8')
            with board.Board() as b:
                self.assertEqual(b.q1('PRAGMA user_version')[0], board.SCHEMA)
                self.assertEqual(store.snapshot(b)['providers'][0]['blocks'], 2)

    def test_a_fresh_store_is_born_at_the_current_schema(self):
        with board.Board(self.path) as b:
            self.assertEqual(b.con.execute('PRAGMA user_version').fetchone()[0], board.SCHEMA)
            self.assertEqual([c[1] for c in b.con.execute('PRAGMA table_info(pm_providers)')],
                             ['name', 'reason', 'retry_at', 'blocks', 'window_s'])


# D298: the Codex CLI's model list, as a fixture, so Sol resolves the same on every machine
CODEX_FIXTURE = Path(tempfile.mkdtemp(prefix='fe-codex-models-')) / 'models_cache.json'
CODEX_FIXTURE.write_text(json.dumps({'models': [
    {'slug': 'gpt-6-sol', 'visibility': 'list'}, {'slug': 'gpt-6.1-sol', 'visibility': 'list'},
    {'slug': 'gpt-5.6-sol', 'visibility': 'list'}, {'slug': 'gpt-6-luna', 'visibility': 'list'}]}), encoding='utf-8')
models.CODEX_CACHE = CODEX_FIXTURE


class ModelTests(unittest.TestCase):
    """D218: which model runs a task, what each run spent, and the policy that learns."""
    setUp, tearDown = ManagerTests.setUp, ManagerTests.tearDown
    goal, task, new_run = ManagerTests.goal, ManagerTests.task, ManagerTests.new_run

    def managed(self, title, body='Implement and test', model='', reason=''):
        n = self.b.q1('SELECT count(*) FROM pm_goals')[0]
        store.receive(self.b, f'owner:m{n}', 'reply', f'Goal {n}: {title}')
        self.manager.input_events()
        gid = self.b.q1('SELECT max(id) FROM pm_goals')[0]
        tid, = store.plan_tasks(self.b, gid, [dict(title=title, body=body, depends=[], model=model,
                                                   model_reason=reason, reasoning='So it works.')])
        self.b.update_task('owner', tid, status='ready')
        return tid

    def finish(self, tid, first, landed=True, escalations=0):
        """A finished task in the record, tried first on `first`."""
        models._upsert(self.b, tid, first_tier=first, tier=first, escalations=escalations)
        self.b.con.execute('UPDATE pm_tasks SET phase=? WHERE task_id=?', ('landed' if landed else 'failed', tid))

    def test_work_is_sorted_into_core_and_off_core(self):
        self.assertEqual(models.classify('A pause button and timescale slider in the menu bar'), ('ui', False))
        self.assertEqual(models.classify('Fix the surface shader\'s motion vectors'), ('rendering', True))
        self.assertEqual(models.classify('Knot kill blows a crater', 'carve chunks out of the flesh'), ('physics', True))
        self.assertEqual(models.classify('Record the game as a video'), ('tools', False))
        self.assertEqual(models.classify(''), ('tools', False))

    def test_off_core_work_goes_to_sonnet_and_core_work_to_opus(self):
        ui = self.managed('A pause button in the menu bar')
        shader = self.managed('Rewrite the shading of the blood film in volume.slang')
        self.assertEqual(models.choose(self.b, ui)[0], 'sonnet')
        self.assertEqual(models.choose(self.b, shader)[0], 'sol')
        physics = self.managed('Make the ragdoll step with the frame budget in mind')
        self.assertEqual(models.choose(self.b, physics)[0], 'opus')
        # The planner's suggestion is stored and, with no record against it, followed.
        asked = self.managed('A slider for the fog', model='opus', reason='touches the frame loop')
        tier, why = models.choose(self.b, asked)
        self.assertEqual(tier, 'opus')
        self.assertIn('touches the frame loop', why)

    def test_the_record_overrides_the_planner(self):
        for i in range(3):
            self.finish(self.managed(f'Panel window {i}'), 'sonnet')
        asked = self.managed('Another panel window', model='opus')
        tier, why = models.choose(self.b, asked)
        self.assertEqual(tier, 'sonnet')
        self.assertIn('Sonnet landed 3 of 3 ui', why)
        # ... and a poor Sonnet record sends its suggestion to Opus.
        for i in range(3):
            self.finish(self.managed(f'Board tool script {i}'), 'sonnet', landed=False, escalations=1)
        asked = self.managed('One more board tool script', model='sonnet')
        self.assertEqual(models.choose(self.b, asked)[0], 'opus')
        self.assertEqual(models.summary(self.b)['policy']['tools'], 'opus')
        self.assertEqual(models.summary(self.b)['policy']['ui'], 'sonnet')

    def history(self, title, tier, landed, tried):
        """`tried` finished tasks of one kind of work first tried on `tier`, `landed` of them landed."""
        for i in range(tried):
            self.finish(self.managed(f'{title} {i}'), tier, landed=i < landed)

    def test_each_category_has_a_first_model_under_an_empty_record(self):
        """D240: off the core Sonnet, rendering the ChatGPT model, the rest of the core Opus."""
        s = models.summary(self.b)
        self.assertEqual(s['policy'], dict(performance='opus', rendering='sol', physics='opus', refactor='opus',
                                           ui='sonnet', tools='sonnet', docs='sonnet', content='sonnet',
                                           gameplay='sonnet'))
        self.assertEqual(s['handover'], dict(performance='opus', rendering='sol', physics='opus', refactor='opus',
                                             ui='opus', tools='opus', docs='opus', content='opus', gameplay='opus'))
        for title, tier in [('Cut the frame time and the hitch the bench found', 'opus'),
                            ('Rewrite the shading of the blood film in volume.slang', 'sol'),
                            ('Make the solver step ragdoll joints in order', 'opus'),
                            ('Refactor the bindings into one table', 'opus'),
                            ('A pause button in the menu bar', 'sonnet')]:
            tid = self.managed(title)
            self.assertEqual(models.choose(self.b, tid)[0], tier, title)
        self.assertIn('where shots decide', models.choose(self.b, self.managed('Tint the fog in the shader'))[1])

    def test_a_record_favouring_a_tier_moves_core_work_to_it(self):
        shader = 'Blood shader'
        # One strong tier's record alone moves nothing: both need MIN_TRIALS finished tries.
        self.history(shader, 'opus', 3, 3)
        self.assertEqual(models.summary(self.b)['policy']['rendering'], 'sol')
        # Both known: rendering goes where it landed more, and says so.
        self.history(shader + ' on sol', 'sol', 1, 3)
        tid = self.managed('Another blood shader')
        tier, why = models.choose(self.b, tid)
        self.assertEqual(tier, 'opus')
        self.assertIn('Opus landed 3 of 3 rendering tasks', why)
        self.assertEqual(models.summary(self.b)['handover']['rendering'], 'opus')
        # ... and it goes the other way for the tier that has landed more.
        self.history('Ragdoll solver', 'sol', 3, 3)
        self.history('Ragdoll solver on opus', 'opus', 1, 3)
        self.assertEqual(models.summary(self.b)['policy']['physics'], 'sol')
        self.assertEqual(models.choose(self.b, self.managed('A ragdoll solver change'))[0], 'sol')
        # A tie goes to the category's home.
        self.history('Refactor the table', 'sol', 2, 3)
        self.history('Refactor the bindings', 'opus', 2, 3)
        self.assertEqual(models.summary(self.b)['policy']['refactor'], 'opus')

    def codex(self, *used, age=0.0, seconds=604800):
        """A Codex reading with one window per number, `age` seconds old."""
        ws = [{'name': f'w{i}', 'used': float(u), 'resets_at': None, 'seconds': seconds} for i, u in enumerate(used)]
        usage.record(self.b, 'codex', {'at': time.time() - age, 'windows': ws, 'note': ''})

    def test_sol_leads_every_strong_pick_while_the_codex_window_is_under_the_gate(self):
        """D270: under 70% used a task that is not Sonnet's runs on Sol, whatever the record says."""
        self.history('Ragdoll solver', 'opus', 3, 3)
        self.history('Ragdoll solver on sol', 'sol', 0, 3)
        self.codex(41)
        physics = self.managed('Ragdoll solver again')
        self.assertEqual(models.choose(self.b, physics), ('sol', 'Codex at 41%: Sol leads'))
        self.assertEqual(models.choose(self.b, self.managed('Rewrite the bindings refactor'))[0], 'sol')
        # The planner's Opus suggestion is led by Sol too, and says so.
        asked = self.managed('A frame budget change', model='opus', reason='touches the frame loop')
        tier, why = models.choose(self.b, asked)
        self.assertEqual(tier, 'sol')
        self.assertEqual(why, 'Codex at 41%: Sol leads, not Opus as the planner chose')
        s = models.summary(self.b)
        self.assertEqual((s['codex_used'], s['gate']), (41.0, 'Codex at 41%: Sol leads'))
        self.assertEqual(set(s['policy'].values()) - {'sonnet'}, {'sol'})
        self.assertEqual(set(s['handover'].values()), {'sol'})
        self.assertIn('every strong task', s['advice'][0])

    def test_at_the_gate_and_past_it_the_record_decides_as_before(self):
        self.history('Ragdoll solver', 'opus', 3, 3)
        self.history('Ragdoll solver on sol', 'sol', 1, 3)
        for used in (70, 83):
            self.codex(used)
            tier, why = models.choose(self.b, self.managed(f'Ragdoll solver at {used}'))
            self.assertEqual(tier, 'opus')
            self.assertTrue(why.startswith(f'Codex at {used}%: mixed by record: physics is core work: Opus landed 3 of 3'), why)
        # With no record the spent window falls back to D240's homes: Opus, and rendering Sol.
        self.codex(90)
        self.assertEqual(models.choose(self.b, self.managed('Fix the surface shader'))[0], 'sol')
        self.assertEqual(models.choose(self.b, self.managed('Make the frame budget faster'))[0], 'opus')
        self.assertEqual(models.escalate(self.b, self.stalled('Ragdoll step'), 'it stalled'), 'opus')

    def stalled(self, title):
        tid = self.managed(title)
        models._upsert(self.b, tid, tier='sonnet', first_tier='sonnet')
        return tid

    def test_no_usable_codex_reading_falls_open_to_the_record(self):
        tier, why = models.choose(self.b, self.managed('Ragdoll solver'))
        self.assertEqual(tier, 'opus')
        self.assertTrue(why.startswith('no Codex reading: by record: '), why)
        self.assertIsNone(models.codex_used(self.b))
        # A reading older than its window, or one whose window has reset, is no reading.
        self.codex(10, age=8 * 86400)
        self.assertIsNone(models.codex_used(self.b))
        self.assertEqual(models.choose(self.b, self.managed('Ragdoll solver two'))[0], 'opus')
        usage.record(self.b, 'codex', {'at': time.time(), 'note': '',
                                       'windows': [{'name': 'w', 'used': 5.0, 'resets_at': time.time() - 60,
                                                    'seconds': 604800}]})
        self.assertIsNone(models.codex_used(self.b))
        with patch.object(usage, 'load', side_effect=ValueError('boom')):
            self.assertIsNone(models.codex_used(self.b))
        # The fullest current window is the one that binds.
        self.codex(20, 74)
        self.assertEqual(models.codex_used(self.b), 74.0)
        self.assertEqual(models.choose(self.b, self.managed('Ragdoll solver three'))[0], 'opus')

    def test_the_gate_leaves_sonnet_and_a_pin_alone(self):
        self.codex(10)
        self.assertEqual(models.choose(self.b, self.managed('A pause button in the menu bar')),
                         ('sonnet', 'ui is off the core: Sonnet first'))
        pinned = self.managed('Ragdoll solver pinned')
        models.set_owner_tier(self.b, pinned, 'opus')
        self.assertEqual(models.choose(self.b, pinned), ('opus', 'Yotam chose it'))
        # A good Sonnet record still takes core work past CORE_LANDED.
        self.history('Ragdoll solver', 'sonnet', 3, 3)
        self.assertEqual(models.choose(self.b, self.managed('Ragdoll solver more'))[0], 'sonnet')
        # A stalled Sonnet run goes to Sol under the gate, and its event names the reading.
        self.assertEqual(models.escalate(self.b, self.stalled('Make the frame budget faster'), 'it stalled'), 'sol')
        self.assertIn('Codex at 10%: Sol leads', self.b.q1("SELECT summary FROM events WHERE kind='manager.escalate' ORDER BY id DESC")[0])

    def test_the_planner_is_told_the_gate(self):
        self.assertIn('no usable Codex reading', models.planner_brief(self.b))
        self.codex(41)
        self.assertIn('Codex is at 41% used', models.planner_brief(self.b))

    def test_a_tie_in_rendering_goes_to_the_chatgpt_model(self):
        self.history('Blood shader', 'sol', 2, 3)
        self.history('Blood shader on opus', 'opus', 2, 3)
        self.assertEqual(models.stronger(models.record(self.b), 'rendering'), 'sol')
        self.assertEqual(models.summary(self.b)['policy']['rendering'], 'sol')

    def test_the_planner_is_checked_against_the_named_tiers_record(self):
        self.history('Blood shader', 'opus', 3, 3)
        self.history('Blood shader on sol', 'sol', 0, 3)
        # Asked for the ChatGPT model, which has landed none of three: the one that landed them.
        asked = self.managed('Another blood shader', model='sol')
        tier, why = models.choose(self.b, asked)
        self.assertEqual(tier, 'opus')
        self.assertIn('the planner asked for Sol, but only Sol landed 0 of 3 rendering tasks', why)
        # With no record against it the planner's suggestion stands.
        free = self.managed('Fix the tooltip', model='sol', reason='shots decide')
        self.assertEqual(models.choose(self.b, free), ('sol', 'no Codex reading: by record: the planner chose it: shots decide'))
        # Off the core a good Sonnet record outranks a strong suggestion, as before.
        self.history('Panel window', 'sonnet', 3, 3)
        tier, why = models.choose(self.b, self.managed('Another panel window', model='sol'))
        self.assertEqual(tier, 'sonnet')
        self.assertIn('the planner asked for Sol, but Sonnet landed 3 of 3 ui', why)

    def test_a_stalled_sonnet_run_goes_to_the_strong_tier_with_the_better_record(self):
        def stalled(title):
            tid = self.managed(title)
            models._upsert(self.b, tid, tier='sonnet', first_tier='sonnet')
            return tid

        # Empty record: rendering to the ChatGPT model, everything else to Opus.
        self.assertEqual(models.escalate(self.b, stalled('Blood shader'), 'it stalled'), 'sol')
        self.assertEqual(models.escalate(self.b, stalled('A pause button in the menu bar'), 'it stalled'), 'opus')
        self.assertEqual(models.escalate(self.b, stalled('Ragdoll solver step'), 'it stalled'), 'opus')
        # A record under MIN_TRIALS counts for nothing.
        self.history('Blood glow', 'opus', 2, 2)
        self.assertEqual(models.escalate(self.b, stalled('Blood shader again'), 'it stalled'), 'sol')
        # With both known, the better record wins, in either direction.
        self.history('Blood glow of the film', 'opus', 3, 3)
        self.history('Blood glow of the skin', 'sol', 1, 3)
        tid = stalled('Blood shader once more')
        self.assertEqual(models.escalate(self.b, tid, 'the build broke'), 'opus')
        self.assertEqual(models.decide(self.b, tid)[:2],
                         ('opus', 'escalated from Sonnet to Opus: the build broke'))
        self.history('Ragdoll solver', 'sol', 3, 3)
        self.history('Ragdoll solver on opus', 'opus', 1, 3)
        self.assertEqual(models.escalate(self.b, stalled('Ragdoll solver again'), 'it stalled'), 'sol')
        # A tie goes to the category's home: rendering to sol, otherwise Opus.
        self.history('Menu button', 'opus', 3, 3)
        self.history('Menu button on sol', 'sol', 3, 3)
        self.assertEqual(models.escalate(self.b, stalled('A new menu button'), 'it stalled'), 'opus')

    def test_a_task_is_never_escalated_twice_nor_off_a_strong_tier_nor_against_a_pin(self):
        tid = self.managed('Blood shader')
        models._upsert(self.b, tid, tier='sonnet', first_tier='sonnet')
        self.assertEqual(models.escalate(self.b, tid, 'first'), 'sol')
        self.assertEqual(models.escalate(self.b, tid, 'second'), '')
        self.assertEqual(models.routes(self.b, [tid])[tid]['escalations'], 1)
        strong = self.managed('Ragdoll solver step')
        models._upsert(self.b, strong, tier='opus', first_tier='opus')
        self.assertEqual(models.escalate(self.b, strong, 'it stalled'), '')
        pinned = self.managed('A pause button in the menu bar')
        models.set_owner_tier(self.b, pinned, 'sonnet')
        models._upsert(self.b, pinned, tier='sonnet', first_tier='sonnet')
        self.assertEqual(models.escalate(self.b, pinned, 'it stalled'), '')

    def test_models_prints_three_models_and_the_policy_per_category(self):
        self.history('Blood shader', 'opus', 3, 3)
        out = '\n'.join(models.report(self.b))
        for want in ('Sonnet (claude)', 'Opus (claude)', 'GPT-6.1 Sol (codex)', 'hand-over'):
            self.assertIn(want, out)
        rendering = next(l for l in out.splitlines() if l.strip().startswith('rendering'))
        self.assertIn('3/3', rendering)
        self.assertIn('core', rendering)

    def test_yotam_can_pin_a_model(self):
        tid = self.managed('A pause button in the menu bar')
        models.set_owner_tier(self.b, tid, 'opus')
        self.assertEqual(models.choose(self.b, tid), ('opus', 'Yotam chose it'))
        models.set_owner_tier(self.b, tid, 'auto')
        self.assertEqual(models.choose(self.b, tid)[0], 'sonnet')
        with self.assertRaises(ValueError):
            models.set_owner_tier(self.b, tid, 'gpt')
        self.assertEqual(board.post(self.b, '/api/manager', {'action': 'model', 'task': tid, 'tier': 'opus'}), {})
        self.assertEqual(models.routes(self.b, [tid])[tid]['owner_tier'], 'opus')

    def test_a_ui_task_is_dispatched_on_sonnet_5_5(self):
        tid = self.managed('A pause button in the menu bar')
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        with patch.object(fe_sync, 'sessions', return_value=[]), patch.object(fe_sync, 'app_processes', return_value=[]), \
                patch.object(fe_sync, 'sync_pull', return_value=Mock(ok=True)), \
                patch.object(fe_sync, 'git_out', return_value='b' * 40), \
                patch.object(fe_sync, 'checkout_state', return_value=([], [], 0)):
            self.assertTrue(self.manager.queue_task(m))
        run = dict(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        stats = self.b.q1('SELECT * FROM pm_run_stats WHERE run_id=?', run['id'])
        self.assertEqual((stats['tier'], stats['model']), ('sonnet', 'sonnet'))
        cmd = workers.command(dict(run, model=stats['model']), self.config, self.root)
        self.assertEqual(cmd[cmd.index('--model') + 1], 'sonnet')
        self.assertIn('Sonnet worker starting', self.b.q1('SELECT body FROM pm_outbox ORDER BY id DESC')[0])
        # A run queued before D218 has no model and stays on Opus.
        self.assertEqual(workers.command(dict(run, model=''), self.config, self.root)[3], 'opus')

    def test_a_failed_sonnet_run_hands_its_session_to_opus_at_no_attempt(self):
        _, tid = self.task()
        models._upsert(self.b, tid, tier='sonnet', first_tier='sonnet')
        r = self.new_run(tid, state='failed', provider='claude')
        self.b.con.execute("UPDATE pm_run_stats SET tier='sonnet' WHERE run_id=?", (r['id'],))
        self.b.con.execute('UPDATE pm_runs SET error=? WHERE id=?', ('exit 1: the build broke', r['id']))
        self.manager.reconcile()
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        self.assertEqual((m['attempts'], m['phase'], m['worker_session']), (0, 'revise', 'exact-session'))
        self.assertIn('Opus taking the task over', m['feedback'])
        self.assertEqual(models.choose(self.b, tid)[0], 'opus')
        x = models.routes(self.b, [tid])[tid]
        self.assertEqual(x['escalations'], 1)
        # An escalation counts against Sonnet at once, landed or not.
        self.assertEqual(models.record(self.b, 'tools')['sonnet'],
                         dict(tried=1, landed=0, escalated=1, failed=0, rate=0.0))

    def test_sonnet_stopping_unfinished_twice_escalates(self):
        _, tid = self.task()
        models._upsert(self.b, tid, tier='sonnet', first_tier='sonnet')
        with patch.object(fe_sync, 'git_out', return_value='a' * 40), patch.object(board, 'on_trunk', return_value=False):
            for i in range(2):
                r = self.new_run(tid, data=result(status='continue'), provider='claude')
                self.b.con.execute("UPDATE pm_run_stats SET tier='sonnet' WHERE run_id=?", (r['id'],))
                self.manager.reconcile()
                self.assertEqual(models.routes(self.b, [tid])[tid]['tier'], 'sonnet' if i == 0 else 'opus')

    def test_the_meter_reads_tokens_and_splits_model_from_tool_time(self):
        m = models.Meter(started=100.0)
        usage = {'input_tokens': 10, 'output_tokens': 50, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 200}
        m.feed({'type': 'assistant', 'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': usage,
                'content': [{'type': 'tool_use', 'id': 't1'}]}}, now=110.0)
        m.feed({'type': 'assistant', 'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': usage,
                'content': [{'type': 'tool_use', 'id': 't2'}]}}, now=111.0)
        m.feed({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1'}]}}, now=130.0)
        live = m.totals(now=135.0)
        self.assertEqual((live['output_tokens'], live['cache_read_tokens']), (50, 1000))  # one message, counted once
        self.assertAlmostEqual(live['tool_s'], 25.0)  # t2 is still open
        m.feed({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't2'}]}}, now=140.0)
        m.feed({'type': 'result', 'num_turns': 3, 'total_cost_usd': 0.5, 'modelUsage': {
            'claude-sonnet-5-5': {'inputTokens': 12, 'outputTokens': 60, 'cacheReadInputTokens': 1500,
                                  'cacheCreationInputTokens': 300, 'costUSD': 0.4},
            'claude-haiku-4-5': {'inputTokens': 5, 'outputTokens': 7, 'costUSD': 0.1}}}, now=150.0)
        t = m.totals(now=160.0)
        self.assertEqual(t['model'], 'claude-sonnet-5-5')
        self.assertEqual((t['output_tokens'], t['cache_read_tokens'], t['turns']), (67, 1500, 3))
        self.assertEqual(sorted(t['by_model']), ['claude-haiku-4-5', 'claude-sonnet-5-5'])
        self.assertAlmostEqual(t['tool_s'], 30.0)
        self.assertAlmostEqual(t['model_s'], 30.0)
        # The stream's output count is only where a message began: until the result, the
        # visible text stands for it, with what the hidden thinking adds.
        e = models.Meter(started=0.0)
        e.feed({'type': 'assistant', 'message': {'id': 'q', 'model': 'claude-opus-5', 'usage': {'output_tokens': 2},
                'content': [{'type': 'text', 'text': 'x' * 400}]}}, now=1.0)
        est = e.totals(now=2.0)
        self.assertEqual((est['output_tokens'], est['estimated']), (int(100 * models.OUTPUT_PER_VISIBLE_TOKEN), True))
        e.feed({'type': 'result', 'modelUsage': {'claude-opus-5': {'outputTokens': 500}}}, now=3.0)
        self.assertEqual((e.totals(now=4.0)['output_tokens'], e.totals(now=4.0)['estimated']), (500, False))
        # Codex's --json
        c = models.Meter(started=0.0)
        c.feed({'type': 'item.started', 'item': {'id': 'i1', 'type': 'command_execution'}}, now=1.0)
        c.feed({'type': 'item.completed', 'item': {'id': 'i1', 'type': 'command_execution'}}, now=4.0)
        c.feed({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 9}})
        ct = c.totals(now=10.0)
        self.assertEqual((ct['input_tokens'], ct['cache_read_tokens'], ct['output_tokens'], ct['tool_s']), (20, 80, 9, 3.0))

    def test_the_night_is_reported_once_each_morning(self):
        # D253: code reads the night and the manager posts it after 10:00, once a day.
        from datetime import datetime
        from unittest import mock
        import fe_usage
        night = {'window': ['2026-10-01T18:00+03:00', '2026-10-02T10:00+03:00'],
                 'meter': {'runs': 2, 'tasks': 1, 'cost_usd': 9.0, 'cache_read_tokens': 5e6, 'output_tokens': 4e4},
                 'opening': {'calls_before_first_edit_median': 22}, 'base_ctx_median': 80000, 'polling_min': 12,
                 'spend': [{'task': 60, 'cost_usd': 9.0}], 'steps_by_carried': [{'step': 'Read ranged', 'carried': 4e6}],
                 'steps_by_time': [{'step': 'sh: fe_build build', 'secs': 600}], 'resumed_big': [], 'sessions': [1]}
        tz = datetime.now().astimezone().tzinfo
        m = core.Manager(self.b, dict(self.config, usage_report=True))
        outbox = lambda: self.b.q("SELECT body FROM pm_outbox WHERE dedup LIKE 'usage:%'")  # noqa: E731
        with mock.patch.object(fe_usage, 'analyse', return_value=night) as read:
            self.assertFalse(m.usage_report(datetime(2026, 10, 2, 9, 30, tzinfo=tz)))   # not before 10:00
            self.assertTrue(m.usage_report(datetime(2026, 10, 2, 10, 5, tzinfo=tz)))
            self.assertFalse(m.usage_report(datetime(2026, 10, 2, 15, 0, tzinfo=tz)))   # once a day
            self.assertEqual(read.call_count, 1)
        self.assertEqual(len(outbox()), 1)
        self.assertIn('$9 over 2 runs', outbox()[0][0])
        self.assertFalse(core.Manager(self.b, self.config).usage_report(datetime(2026, 10, 3, 11, 0, tzinfo=tz)))

    def test_the_nightly_bench_starts_once_at_four_and_holds_the_work(self):
        """D262: no commit waits on a bench. From 04:00 the service starts one, once a night,
        and while it lives no run launches and no suspended worker is called stalled."""
        import manager_bench
        import manager_nightly
        bench_job = manager_nightly.job('bench')  # studio.toml [[nightly]]
        tz = datetime.datetime.now().astimezone().tzinfo
        at = lambda h, m=0: datetime.datetime(2026, 10, 3, h, m, tzinfo=tz)  # noqa: E731
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 creationflags=workers.NO_WINDOW)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        spawned = []

        def popen(cmd, **kw):
            spawned.append(cmd)
            return child
        m = core.Manager(self.b, dict(self.config, nightly_bench=True))
        self.assertFalse(m.nightly_job(bench_job, at(3, 59), popen))
        self.assertTrue(m.nightly_job(bench_job, at(4, 0), popen))
        self.assertEqual(spawned[0][-1], '_bench_night')
        self.assertEqual(manager_bench.held(self.b)['pid'], child.pid)
        self.assertFalse(m.nightly_job(bench_job, at(4, 30), popen))  # once a night
        self.assertEqual(len(spawned), 1)
        store.control(self.b, 'resume')
        with patch.object(m, 'schedule') as schedule, patch.object(m, 'launch') as launch, \
                patch.object(m, 'nightly_job'):  # tick reads the real clock: 04:00-08:00 would start a real bench
            m.tick()
        schedule.assert_not_called()
        launch.assert_not_called()
        board_line = '\n'.join(outlook.lines(outlook.outlook(self.b, steps=False, models_too=False)))
        self.assertIn('The nightly bench holds new runs (since', board_line)
        child.kill()
        child.wait()
        self.assertIsNone(manager_bench.held(self.b))  # the bench ended: the hold goes with it
        with patch.object(m, 'schedule') as schedule, patch.object(m, 'launch') as launch, \
                patch.object(m, 'nightly_job'):  # tick reads the real clock: 04:00-08:00 would start a real bench
            m.tick()
        schedule.assert_called_once()
        self.assertFalse(core.Manager(self.b, dict(self.config, nightly_bench=True)).nightly_job(
            bench_job, datetime.datetime(2026, 10, 4, 9, 0, tzinfo=tz), popen))  # past 08:00 a missed night waits
        self.assertFalse(core.Manager(self.b, self.config).nightly_job(
            bench_job, datetime.datetime(2026, 10, 4, 4, 0, tzinfo=tz), popen))  # switched off

    def test_the_crew_night_follows_the_bench_once_and_waits_for_open_crew_tasks(self):
        """D265: the crew's night starts once the bench's hold is gone (from 06:00 with no bench),
        once a day, never past 10:00; a kind whose last task is still open is not started again."""
        import manager_bench
        import manager_crew
        import psutil
        tz = datetime.datetime.now().astimezone().tzinfo
        at = lambda h, m=0, d=3: datetime.datetime(2026, 10, d, h, m, tzinfo=tz)  # noqa: E731
        spawned = []

        def popen(cmd, **kw):
            spawned.append(cmd)
        m = core.Manager(self.b, dict(self.config, nightly_crew='shadow'))
        self.assertFalse(m.nightly_crew(at(5, 0), popen))      # no bench tonight yet, before 06:00
        store.set_setting(self.b, manager_bench.NIGHT, '2026-10-03')
        store.set_setting(self.b, manager_bench.HOLD, json.dumps(dict(pid=os.getpid(), started=psutil.Process().create_time(),
                                                                      since=time.time(), suspended=[])))
        self.assertFalse(m.nightly_crew(at(5, 0), popen))      # the bench still holds the work
        store.set_setting(self.b, manager_bench.HOLD, '')
        self.assertTrue(m.nightly_crew(at(5, 10), popen))      # it let go: the crew follows at once
        self.assertEqual(spawned[0][-1], '_crew_night')
        self.assertFalse(m.nightly_crew(at(7, 0), popen))      # once a night
        self.assertTrue(m.nightly_crew(at(6, 0, d=4), popen))  # no bench the next night: from 06:00
        self.assertFalse(core.Manager(self.b, dict(self.config, nightly_crew='shadow')).nightly_crew(at(10, 30, d=5), popen))
        self.assertFalse(core.Manager(self.b, self.config).nightly_crew(at(7, 0, d=5), popen))  # off
        self.assertEqual(len(spawned), 2)
        tid = self.b.add_task('manager', 'Code crew: 2026-10-03', body='## Why\nx', status='ready')
        self.assertEqual(manager_crew.open_task(self.b, 'Code crew')[0], tid)
        self.b.update_task('owner', tid, status='done')
        self.assertIsNone(manager_crew.open_task(self.b, 'Code crew'))

    def test_the_bench_takes_over_the_hold_its_launcher_was_given(self):
        """D262: the service holds the work under the pid it spawned, and a venv's python.exe is
        a launcher whose child is the bench. On 2026-10-03 the bench read its launcher's hold as
        another bench's and quit within 3 s; a stranger's hold still stops it."""
        import manager_bench
        import psutil
        hold = lambda p: json.dumps(dict(pid=p.pid, started=p.create_time(), since=time.time(), suspended=[]))  # noqa: E731
        store.set_setting(self.b, manager_bench.HOLD, hold(psutil.Process().parent()))
        with manager_bench.Hold(self.b):
            self.assertEqual(manager_bench.held(self.b)['pid'], os.getpid())
        self.assertIsNone(manager_bench.held(self.b))
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 creationflags=workers.NO_WINDOW)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        store.set_setting(self.b, manager_bench.HOLD, hold(psutil.Process(child.pid)))
        with self.assertRaises(RuntimeError):
            manager_bench.Hold(self.b).__enter__()

    def test_a_failed_bench_night_tells_the_channel(self):
        """D262: on 2026-10-04 the night died at its start on a damaged record and only its log
        said so; a failed night now posts, and the hold is released."""
        import manager_bench
        with patch.object(manager_bench.Night, 'run', side_effect=ValueError('missing performance object ab')):
            with self.assertRaises(ValueError):
                manager_bench.night(self.b, dict(self.config, repo=str(self.root)))
        body = self.b.q1("SELECT body FROM pm_outbox WHERE dedup LIKE 'bench-night-failed:%'")
        self.assertIn('missing performance object ab', body[0])
        self.assertIsNone(manager_bench.held(self.b))

    def test_the_nightly_view_keeps_the_flags_and_each_case_over_time(self):
        """Yotam, 2026-10-04: what costs performance is recorded after each night, a failed case
        is in that night's results, and each case's time shows over the nights."""
        import manager_nightly
        perf = self.root / 'performance'
        ref, mid, head = 'a' * 40, 'b' * 40, 'c' * 40

        def record(sha, case, ms, march):
            p = perf / 'records' / sha / 'env' / f'{case}.json'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(dict(scenario=dict(name=case), observations=[dict(summary=dict(
                combined_ms=ms, passes=dict(march=march, blood=.1, total=ms)))])))
        record(ref, 'blood-runs-near', 1.10, .50)
        record(mid, 'blood-runs-near', 1.19, .59)
        record(head, 'blood-runs-near', 1.19, .59)
        (perf / 'nightly').mkdir(parents=True)
        (perf / 'nightly' / '2026-10-04.json').write_text(json.dumps(dict(
            head=head, reference=ref, at=time.time(), cases=['blood-runs-near'], commits=[dict(commit=mid, subject='T77')],
            offenders=[], verdicts={'blood-runs-near': dict(outcome='passed_with_debt', delta_ms=dict(combined_ms=.09))},
            unrunnable=[dict(case='corridor-bounce', at_head=False, why='Error: replay line 4: unsupported command "stats"')],
            attributed=[dict(commit=mid, subject='T77', case='blood-runs-near', parent=ref, against=ref,
                             delta_ms=dict(combined_ms=.09), own_delta_ms=dict(combined_ms=.09), within_noise=False),
                        dict(commit=mid, subject='T77', case='mutant-1-healing', parent=ref, against=ref,
                             delta_ms=dict(combined_ms=.05), own_delta_ms=dict(combined_ms=.02), within_noise=True)])))
        self.b.event('manager', 'manager.bench', None, 'nightly bench started (pid 7) as a rehearsal of tonight')
        self.b.event('manager', 'manager.bench', None, 'nightly bench failed: boom')
        v = manager_nightly.view(self.b)['bench']
        self.assertEqual([(f['commit'], f['case'], f['kind']) for f in v['flagged']],
                         [(mid[:10], 'blood-runs-near', 'within the allowance')])  # the one within noise is no flag
        self.assertEqual(v['flagged'][0]['passes'][0], dict(pass_name='march', delta_ms=.09))
        h = v['history']
        self.assertEqual([p['label'] for p in h['points']], ['before 2026-10-04', '2026-10-04'])
        rows = {c['case']: c for c in h['cases']}
        self.assertEqual(rows['blood-runs-near']['values'], [1.1, 1.19])
        self.assertEqual((rows['corridor-bounce']['values'], len(rows['corridor-bounce']['failed'])), ([None, None], 1))
        self.assertEqual((v['rehearsal']['failed'], v['rehearsal']['report']), ('boom', None))
        self.assertEqual([n['day'] for n in v['nights']], ['2026-10-04'])  # a rehearsal is not a night
        self.assertIsNone(v['nights'][0]['failed'])

    def test_the_nightly_view_shows_the_bench_live_and_every_night(self):
        """The board's Nightly tab: the running bench and its claim, each night that started
        (failed, or with its report), and the crew's tasks and scorecards."""
        import fe_gpu
        import manager_nightly
        self.b.con.execute(fe_gpu.TABLE)
        store.set_setting(self.b, 'bench_hold', json.dumps(dict(pid=os.getpid(), started=0, since=time.time(),
                                                                suspended=[[1, 1.0]])))
        self.b.con.execute("INSERT INTO gpu_claims(kind, agent, pid, note, state, created, asked) "
                           "VALUES('bench','local',?,'commit performance mutant-1-intact','waiting',?,?)",
                           (os.getpid(), time.time(), json.dumps({'A2': 1})))
        self.b.event('manager', 'manager.bench', None, 'nightly bench started (pid 7) for the night of 2026-10-03')
        self.b.event('manager', 'manager.bench', None, 'nightly bench failed: missing performance object ab')
        nightly = self.root / 'performance' / 'nightly'
        nightly.mkdir(parents=True)
        (nightly / '2026-10-02.json').write_text(json.dumps(dict(
            head='b' * 40, reference='a' * 40, at=1.0, cases=['mutant-1-intact'], commits=[dict(commit='b' * 40, subject='s')],
            offenders=[], verdicts={'mutant-1-intact': dict(outcome='passed', delta_ms=dict(combined_ms=0.1))})))
        crew = self.root / 'board' / 'manager' / 'crew'
        crew.mkdir(parents=True)
        (crew / '2026-10-03.json').write_text(json.dumps(dict(day='2026-10-03', code=dict(health=dict(penalty=5.0)))))
        tid = self.b.add_task('manager', 'Code crew: 2026-10-03', body='b', status='ready')
        with patch.object(manager_nightly, 'crew_commits', return_value=[
                dict(sha='c' * 40, at=1, subject='Name a step', crew=tid, review='keep', directive='Separate X')]):
            v = manager_nightly.view(self.b)
        b = v['bench']
        self.assertTrue(b['running'])
        self.assertEqual((b['claim']['state'], b['claim']['asked'], b['hold']['suspended']), ('waiting', ['A2'], 1))
        self.assertEqual([(n['day'], bool(n['failed']), n['report'] and n['report']['worst_ms']) for n in b['nights']],
                         [('2026-10-03', True, None), ('2026-10-02', False, 0.1)])
        t = v['crew']['tasks'][0]
        self.assertEqual((t['crew'], t['day'], t['commits'][0]['review']), ('code', '2026-10-03', 'keep'))
        self.assertEqual(v['crew']['nights'][0]['penalty'], 5.0)

    def test_a_past_night_asked_of_the_service_starts_at_once(self):
        """D262: `bench-night --night DAY --service` benches a night that did not run, outside
        the 04:00 window, without spending tonight's."""
        import manager_bench
        import manager_nightly
        bench_job = manager_nightly.job('bench')  # studio.toml [[nightly]]
        tz = datetime.datetime.now().astimezone().tzinfo
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 creationflags=workers.NO_WINDOW)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        spawned = []

        def popen(cmd, **kw):
            spawned.append(cmd)
            return child
        m = core.Manager(self.b, dict(self.config, nightly_bench=True))
        noon = datetime.datetime(2026, 10, 4, 13, 0, tzinfo=tz)
        self.assertFalse(m.nightly_job(bench_job, noon, popen))
        store.set_setting(self.b, manager_bench.REQUEST, '2026-10-04')
        self.assertTrue(m.nightly_job(bench_job, noon, popen))
        self.assertEqual(spawned[0][-3:], ['_bench_night', '--night', '2026-10-04'])
        self.assertFalse(store.setting(self.b, manager_bench.REQUEST))
        self.assertNotEqual(store.setting(self.b, manager_bench.NIGHT), '2026-10-04')
        at, before = manager_bench.night_times('2026-10-04')
        self.assertEqual((at.hour, before.day), (4, 3))

    def test_a_result_without_detours_is_still_valid(self):
        """D265: a run started before the detours field returns a valid result without it."""
        r = dict(status='complete', summary='s', head='h', tasks=[], checks=[], artifacts=[])
        workers.validate_result(r)
        self.assertEqual(r['detours'], [])

    def test_a_bench_that_dies_leaves_no_worker_suspended(self):
        import manager_bench
        import psutil
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 creationflags=workers.NO_WINDOW)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        p = psutil.Process(child.pid)
        p.suspend()
        store.set_setting(self.b, manager_bench.HOLD, json.dumps(dict(
            pid=999999, started=0, since=time.time(), suspended=[[p.pid, p.create_time()]])))
        self.assertIsNone(manager_bench.held(self.b))
        self.assertNotEqual(p.status(), psutil.STATUS_STOPPED)
        self.assertEqual(store.setting(self.b, manager_bench.HOLD), '')

    def test_the_bench_pauses_the_workers_but_not_their_games(self):
        import manager_bench
        _, tid = self.task()
        r = self.new_run(tid, state='running')
        procs = [Mock(**{'name.return_value': n, 'pid': i, 'create_time.return_value': 1.0})
                 for i, n in enumerate(('claude.exe', 'cargo.exe', 'app.exe'), 1)]
        wrapper = Mock(**{'children.return_value': procs})
        hold = manager_bench.Hold(self.b)
        self.b.con.execute('UPDATE pm_runs SET pid=1 WHERE id=?', (r['id'],))
        with patch.object(workers, 'owned_process', return_value=True), \
                patch('psutil.Process', return_value=wrapper):
            with patch.object(manager_bench, 'resume') as resume:
                with hold.paused() as n:
                    self.assertEqual(n, 2)
                    saved = json.loads(store.setting(self.b, manager_bench.HOLD))
                    self.assertEqual([s[0] for s in saved['suspended']], [1, 2])
                resume.assert_called_once_with([[1, 1.0], [2, 1.0]])
        procs[0].suspend.assert_called_once()
        procs[2].suspend.assert_not_called()  # the GPU claim freezes the game; suspended it could not answer

    def test_the_bench_pauses_the_workers_only_once_the_gpu_is_free(self):
        """D262: on 2026-10-04 the night suspended T95's and T96's workers, then waited for
        their games, which have no channel to freeze: neither could ever end. The workers run
        while the bench waits, and are paused only around the measurement."""
        import fe_gpu
        import manager_bench
        order, blockers = [], [[(5, 'C:/x/engine/target/release/app.exe', 'app.exe --control 0')], []]

        def games():
            order.append('wait')
            return blockers.pop(0) if blockers else []

        @contextlib.contextmanager
        def paused():
            order.append('pause')
            yield 2
            order.append('resume')
        night = manager_bench.Night.__new__(manager_bench.Night)
        night.hold = Mock(paused=paused)
        with patch.object(fe_gpu, 'games', games), patch.object(fe_gpu, 'owner', return_value=None), \
                patch.object(fe_gpu, 'live', return_value=[]), \
                patch.object(fe_gpu, 'run_hidden', side_effect=lambda *a, **k: order.append('run') or 0):
            with night.pause():
                self.assertEqual(fe_gpu.bench(['x'], note='t', poll=0), 0)
        self.assertEqual(order, ['wait', 'wait', 'pause', 'run', 'resume'])
        self.assertIsNone(fe_gpu.around_run)

    def test_a_placeholder_turn_never_names_the_runs_model(self):
        # D253: T60's run was labelled `<synthetic>` by Claude Code's last placeholder turn.
        m = models.Meter(started=0.0)
        m.feed({'type': 'assistant', 'message': {'id': 'a', 'model': 'claude-sonnet-5-5',
                'usage': {'output_tokens': 40}, 'content': []}}, now=1.0)
        m.feed({'type': 'assistant', 'message': {'id': 'b', 'model': models.SYNTHETIC,
                'usage': {'output_tokens': 0}, 'content': [{'type': 'text', 'text': 'No response requested.'}]}}, now=2.0)
        t = m.totals(now=3.0)
        self.assertEqual(t['model'], 'claude-sonnet-5-5')
        self.assertNotIn(models.SYNTHETIC, t['by_model'])
        # Rows metered before the fix are relabelled from what they spent, else their tier.
        _, tid = self.task()
        spent, empty = self.new_run(tid, state='handled', provider='claude'), self.new_run(tid, state='handled', provider='claude')
        self.b.con.execute("UPDATE pm_run_stats SET tier='sonnet', model=?, by_model=? WHERE run_id=?", (
            models.SYNTHETIC, json.dumps({'claude-sonnet-5-5': {'output': 9, 'cost_usd': 1.0}}), spent['id']))
        self.b.con.execute("UPDATE pm_run_stats SET tier='opus', model=?, by_model='{}' WHERE run_id=?",
                           (models.SYNTHETIC, empty['id']))
        self.assertEqual(models.relabel_synthetic(self.b), 2)
        got = dict(self.b.q("SELECT run_id, model FROM pm_run_stats WHERE run_id IN (?,?)", spent['id'], empty['id']))
        self.assertEqual((got[spent['id']], got[empty['id']]), ('claude-sonnet-5-5', models.MODEL_IDS['opus']))

    def test_a_worker_run_records_what_it_spent(self):
        _, tid = self.task()
        r = self.new_run(tid, state='queued', provider='claude')
        self.b.con.execute("UPDATE pm_run_stats SET tier='sonnet', model='claude-sonnet-5-5' WHERE run_id=?", (r['id'],))
        fake = self.root / 'fake.py'
        events = [{'type': 'system', 'session_id': 'fake-session'},
                  {'type': 'assistant', 'message': {'id': 'a', 'model': 'claude-sonnet-5-5', 'content': [
                      {'type': 'tool_use', 'id': 't'}], 'usage': {'input_tokens': 1, 'output_tokens': 2}}},
                  {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't'}]}},
                  {'type': 'result', 'num_turns': 2, 'structured_output': result(),
                   'modelUsage': {'claude-sonnet-5-5': {'inputTokens': 3, 'outputTokens': 40, 'costUSD': 0.01}}}]
        fake.write_text('import json,sys,time\nsys.stdin.read()\n' + ''.join(
            f'print(json.dumps({e!r}),flush=True)\ntime.sleep(0.2)\n' for e in events), encoding='utf-8')
        seen = []
        with patch.object(workers, 'command', side_effect=lambda run, *a: seen.append(run['model']) or [sys.executable, str(fake)]):
            self.root.joinpath('worker').mkdir()
            self.assertEqual(workers.worker_main(r['id'], self.config), 0)
        self.assertEqual(seen, ['claude-sonnet-5-5'])
        s = dict(self.b.q1('SELECT * FROM pm_run_stats WHERE run_id=?', r['id']))
        self.assertEqual((s['output_tokens'], s['turns'], s['model']), (40, 2, 'claude-sonnet-5-5'))
        self.assertTrue(s['started'] and s['ended'] >= s['started'])
        self.assertGreater(s['tool_s'], 0.1)
        line = models.task_stats(self.b, tid)['line']
        self.assertIn('Sonnet 5.5', line)
        self.assertIn('40 tokens out', line)
        self.assertIn(tid, models.board_view(self.b))

    def test_gpu_and_frozen_waits_go_on_the_run(self):
        _, tid = self.task()
        r = self.new_run(tid, state='running', provider='claude')

        def share(argv, should_stop=None, env=None, timing=None):
            timing.update(waited=42.0, ran=8.0)
            return 0
        with patch.dict(os.environ, {'FE_MANAGER_RUN': r['id']}), patch.object(fe_gpu, 'share', side_effect=share):
            self.assertEqual(fe_manager.gpu(['--', 'app.exe', '--shot', 'x.png']), 0)
        models.add_wait(r['id'], 'frozen_s', 5.0)
        s = self.b.q1('SELECT * FROM pm_run_stats WHERE run_id=?', r['id'])
        self.assertEqual((s['gpu_wait_s'], s['gpu_s'], s['gpu_runs'], s['frozen_s']), (42.0, 8.0, 1, 5.0))
        self.b.con.execute('UPDATE pm_run_stats SET started=?, ended=? WHERE run_id=?', (1000.0, 1100.0, r['id']))
        st = models.task_stats(self.b, tid)
        self.assertEqual((st['waiting_s'], st['working_s']), (47.0, 53.0))
        self.assertIn('waiting 47s (GPU 42s, frozen 5s)', st['line'])

    def test_fe_py_counts_a_frozen_wait_for_a_managed_run(self):
        replies = iter(['err frozen by a bench', 'ok'])
        with patch.object(fe, 'send', side_effect=lambda *a: next(replies)), patch.object(fe.time, 'sleep'), \
                patch.dict(os.environ, {'FE_MANAGER_RUN': 'abc'}), patch.object(models, 'add_wait') as add, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fe.main(['status']), 0)
        self.assertEqual(add.call_args[0][:2], ('abc', 'frozen_s'))

    def test_old_runs_are_read_back_from_their_logs(self):
        _, tid = self.task()
        r = self.new_run(tid, state='handled', provider='claude')
        self.b.con.execute('DELETE FROM pm_run_stats')
        self.b.con.execute('UPDATE pm_runs SET pid_started=?, heartbeat=? WHERE id=?', (1000.0, 1600.0, r['id']))
        folder = workers.run_dir(r['id'])
        folder.mkdir(parents=True)
        (folder / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'assistant', 'message': {'id': 'x', 'model': 'claude-opus-5', 'usage': {'output_tokens': 5}}},
            {'type': 'result', 'duration_api_ms': 200_000,
             'modelUsage': {'claude-opus-5': {'inputTokens': 1, 'outputTokens': 900}}}]), encoding='utf-8')
        self.assertEqual(models.backfill(self.b), 1)
        self.assertEqual(models.backfill(self.b), 0)
        s = self.b.q1('SELECT * FROM pm_run_stats WHERE run_id=?', r['id'])
        self.assertEqual((s['tier'], s['output_tokens'], s['model_s'], s['tool_s']), ('opus', 900, 200.0, 400.0))
        self.assertEqual(models.routes(self.b, [tid])[tid]['tier'], 'opus')
        # A run still going that started before D218 is on Opus, and is read once it ends.
        live = self.new_run(tid, state='running', provider='claude')
        self.b.con.execute('DELETE FROM pm_run_stats WHERE run_id=?', (live['id'],))
        self.assertEqual(models.backfill(self.b), 0)
        self.assertEqual(self.b.q1('SELECT model FROM pm_run_stats WHERE run_id=?', live['id'])[0], 'opus')
        # Its tokens so far come from its log each tick; its time stays unsplit until it ends.
        lf = workers.run_dir(live['id'])
        lf.mkdir(parents=True)
        (lf / 'events.jsonl').write_text(json.dumps({'type': 'assistant', 'message': {
            'id': 'y', 'model': 'claude-opus-5', 'usage': {'output_tokens': 77}}}), encoding='utf-8')
        self.b.con.execute('UPDATE pm_runs SET pid_started=? WHERE id=?', (time.time() - 600, live['id']))
        self.b.con.execute('UPDATE pm_run_stats SET started=(SELECT pid_started FROM pm_runs WHERE id=?) '
                           'WHERE run_id=?', (live['id'], live['id']))
        models.backfill(self.b)
        self.assertIn(live['id'], models.unmetered(self.b))
        st = models.runs_stats(self.b, [live['id']])[live['id']]
        self.assertEqual((st['output_tokens'], st['model_s']), (77, 0.0))
        self.assertIn('not yet split', st['line'])
        self.b.con.execute("UPDATE pm_runs SET state='handled' WHERE id=?", (live['id'],))
        self.assertEqual(models.backfill(self.b), 1)
        self.assertNotIn(live['id'], models.unmetered(self.b))

    def test_the_planner_is_told_the_record_and_suggests_models(self):
        store.receive(self.b, 'owner:9', 'reply', 'Add a menu')
        self.manager.input_events()
        gid = self.b.q1('SELECT id FROM pm_goals')[0]
        text = self.manager.prompt('planner', goal=gid)
        self.assertIn('Sonnet is several times faster', text)
        self.assertIn('ui: no finished tasks yet', text)
        item = workers.SCHEMA['properties']['tasks']['items']
        self.assertEqual(item['properties']['model']['enum'], ['sonnet', 'opus', 'sol'])
        self.assertIn('GPT-6.1 Sol', text)
        self.assertIn('model_reason', item['required'])
        lines = '\n'.join(models.report(self.b))
        self.assertIn('Policy by category', lines)
        self.assertIn('Planner: Opus', lines)


    def test_status_reads_at_a_glance(self):
        """/status: what runs, for how long and its latest, what needs Yotam, what is queued
        and on what, then the stats; the latest is the worker's last words however far back."""
        busy = self.managed('A pause button in the menu bar')
        r = self.new_run(busy, state='running', provider='claude')
        self.b.con.execute('UPDATE pm_runs SET pid_started=?, heartbeat=? WHERE id=?',
                           (time.time() - 1500, time.time() - 5, r['id']))
        folder = workers.run_dir(r['id'])
        folder.mkdir(parents=True, exist_ok=True)
        big = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'x' * 500_000}]}}
        (folder / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'assistant', 'message': {'id': 'a', 'content': [{'type': 'text', 'text': 'The slider works.'}]}},
            big,
            {'type': 'assistant', 'message': {'id': 'b', 'content': [
                {'type': 'tool_use', 'id': 't', 'name': 'Bash', 'input': {'description': 'Run the tests'}}]}},
            big]) + '\n', encoding='utf-8')
        after = self.managed('The fog slider')
        self.b.update_task('owner', after, depends=str(busy))
        idea = self.managed('A video button')
        self.b.update_task('owner', idea, status='idea')
        o = outlook.outlook(self.b, self.config)
        text = '\n'.join(outlook.lines(o, md=True))
        order = [text.index(h) for h in ('**Working on**', '**Needs you**', '**Queued**', '**Stats**')]
        self.assertEqual(order, sorted(order))
        self.assertIn(f'**T{busy}** A pause button in the menu bar', text)
        self.assertIn('> Latest: The slider works.', text)
        self.assertIn('ago: Run the tests', text)
        self.assertIn(f'**T{idea}** A video button: approve or drop the proposal', text)
        self.assertIn(f'**T{after}** The fog slider: after T{busy}', text)
        plain = '\n'.join(outlook.lines(o))
        self.assertNotIn('**', plain)
        self.assertIn('Working on', plain)

    def test_a_task_has_its_whole_transcript(self):
        """The board's transcript view: every run of a task, the manager's prompt, the
        worker's thinking and words, each tool call with its result, read on from an offset."""
        import manager_transcript as transcript
        tid = self.managed('A pause button in the menu bar')
        r = self.new_run(tid, state='running', provider='claude')
        self.assertIn('--thinking-display', workers.command(dict(r, model='opus'), self.config, self.root))
        folder = workers.run_dir(r['id'])
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / 'events.jsonl'
        log.write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'system', 'subtype': 'init', 'session_id': 's1', 'model': 'claude-opus-5', 'cwd': 'A2'},
            {'type': 'assistant', 'message': {'id': 'a', 'content': [
                {'type': 'thinking', 'thinking': 'The button belongs in the menu bar.'},
                {'type': 'text', 'text': 'Adding the button.'},
                {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'ls', 'description': 'List'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1',
                                                      'content': 'x' * (transcript.CLIP + 10)}]}}]) + '\n'
                       + '{"type": "assistant", "mess', encoding='utf-8')  # a line still being written
        first = transcript.read(r['id'])
        kinds = [i['k'] for i in first['items']]
        self.assertEqual(kinds, ['note', 'think', 'say', 'tool', 'result'])
        self.assertEqual(first['items'][3]['input'], '$ ls')
        self.assertEqual(first['items'][3]['summary'], 'List')
        self.assertEqual(first['items'][4]['more'], transcript.CLIP + 10)
        self.assertLess(first['next'], first['size'])  # the half line waits for the next read
        with log.open('a', encoding='utf-8') as f:
            f.write('age": {"id": "b", "content": [{"type": "text", "text": "Done."}]}}\n')
        more = transcript.read(r['id'], first['next'])
        self.assertEqual([i['text'] for i in more['items']], ['Done.'])
        view = transcript.runs(self.b, f'T{tid}')
        self.assertEqual((view['title'], [x['id'] for x in view['runs']]), ('A pause button in the menu bar', [r['id']]))
        self.assertEqual(view['runs'][0]['prompt'], 'prompt')
        with self.assertRaises(ValueError):
            transcript.runs(self.b, '../etc')

    def test_a_worker_prompt_is_the_task_and_its_why(self):
        """D224: the rules live in the task skill; a worker's prompt is the task, its Why
        first, and where to read the rules."""
        import manager_core
        import studio_config
        skill = studio_config.repo_root() / manager_core.SKILL
        text = skill.read_text(encoding='utf-8')
        for part in ('## Writing a task', '## Working a task', '## Asking the owner', '## Reporting', 'FE_MANAGER_RUN'):
            self.assertIn(part, text)
        tid = self.managed('A pause button in the menu bar')
        self.b.con.execute('UPDATE pm_tasks SET agent=? WHERE task_id=?', ('A2', tid))
        prompt = self.manager.prompt('worker', tid)
        self.assertIn(manager_core.SKILL, prompt)
        self.assertLess(prompt.index('## Why'), prompt.index('## What to do'))
        rules = prompt.replace(self.b.task(tid)['body'].strip(), '')
        self.assertLess(len(rules), 1200, rules)  # the old prompt carried ~5,000 characters of rules
        self.assertNotIn('Definition of Done', prompt)
        self.assertIn('task skill', self.manager.prompt('planner', goal=self.b.q1('SELECT max(id) FROM pm_goals')[0]))

    def test_the_games_worker_lines_come_from_its_notes_and_a_game_without_them_runs(self):
        """Studio phase 1: the GPU lease and the nightly bench are the game's lines, read from
        [prompts] worker_notes; without the file the prompt keeps its own lines and drops them,
        and the checkouts default to every registered one but the primary."""
        import manager_core
        import studio_config
        tid = self.managed('A pause button in the menu bar')
        self.b.con.execute('UPDATE pm_tasks SET agent=? WHERE task_id=?', ('A2', tid))
        prompt = self.manager.prompt('worker', tid)
        self.assertIn('Here: checkout A2; your GPU launches go through "', prompt)
        self.assertIn('(D253). No bench is required (D262)', prompt)
        notes = manager_core.worker_notes(python='PY', fe_manager='MGR')
        self.assertTrue(notes['gpu'].startswith('your GPU launches go through "PY" "MGR" gpu'))
        real = studio_config.get
        with patch.object(studio_config, 'get', lambda k, d=None: None if k in (
                'prompts.worker_notes', 'routing.checkouts') else real(k, d)):
            bare = self.manager.prompt('worker', tid)
            self.assertEqual(store.checkouts(self.manager.reg), ('A2', 'A3'))
        self.assertIn('Here: checkout A2.\n', bare)
        self.assertIn('resumes you if a gate stops it (D253).\n', bare)
        self.assertNotIn('GPU', bare)
        self.assertNotIn('bench', bare)

    def test_a_question_is_answered_with_a_button(self):
        """D224: a blocked worker's asks carry options; each option is a button, and the last
        answer resumes the worker with all of them. A typed reply still works; a stale set refuses."""
        import manager_brief as brief
        tid = self.managed('A pause button in the menu bar')
        r = self.new_run(tid, state='finished', provider='claude', data=dict(
            result(status='blocked'), summary='Two choices', bottom_line='Needs two calls',
            asks=[{'question': 'Where does the button go?', 'options': ['Menu bar', 'Tool window']},
                  {'question': 'Keyboard shortcut?', 'options': ['Space', 'P', 'None']}]))
        self.manager.reconcile()
        self.assertEqual(self.b.q1('SELECT phase FROM pm_tasks WHERE task_id=?', tid)[0], 'blocked')
        ask = self.b.q1('SELECT * FROM pm_asks WHERE task_id=?', tid)
        self.assertEqual(ask['dedup'], 'question:' + r['id'])
        body = self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', ask['dedup'])[0]
        self.assertIn('**a.** Menu bar (recommended)', body)
        # Discord: a row of buttons per question, the form, Pause; a click records an option
        import discord

        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.send = AsyncMock(return_value=Mock(id=1))
            client.destination = AsyncMock(return_value=thread)
            await client.deliver_row(dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', ask['dedup'])))
            view = thread.send.await_args.kwargs['view']
            click = Mock(id=6, data={}, response=Mock(send_modal=AsyncMock(), send_message=AsyncMock()))
            await client.answer(click, 'ans', tid, ['pm', 'ans', str(tid), ask['id'], '0', '0'])
            await client.close()
            return [(c.custom_id, c.label) for c in view.children], click.response.send_message.await_args.args[0]
        buttons, said = asyncio.run(exercise())
        self.assertEqual([b for b, _ in buttons], [f'pm:ans:{tid}:{ask["id"]}:0:0', f'pm:ans:{tid}:{ask["id"]}:0:1',
                                                   f'pm:ans:{tid}:{ask["id"]}:1:0', f'pm:ans:{tid}:{ask["id"]}:1:1',
                                                   f'pm:ans:{tid}:{ask["id"]}:1:2', f'pm:own:{tid}:{ask["id"]}', 'pm:pause'])
        self.assertEqual(buttons[0][1], '1a. Menu bar')
        self.assertIn('Menu bar', said)
        self.assertIn('1 of 2 answered', said)
        reply, n, total = brief.answer(self.b, ask['id'], {1: 'Space'})
        self.assertEqual((n, total), (2, 2))
        self.assertIn('Where does the button go?\n   -> Menu bar', reply)
        with self.assertRaises(ValueError):
            brief.answer(self.b, ask['id'], {1: 'P'})  # answered: the buttons are spent
        store.receive(self.b, 'interaction:1', 'reply', reply, tid)
        self.manager.input_events()
        m = self.b.q1('SELECT phase, feedback FROM pm_tasks WHERE task_id=?', tid)
        self.assertEqual(m['phase'], 'revise')
        self.assertIn('Keyboard shortcut?\n   -> Space', m['feedback'])
        # A plain-line ask (a run from before D224) still posts, with no buttons.
        self.assertEqual(brief.ask_parts('Approve it?'), ('Approve it?', []))
        workers.validate_result(dict(result(status='blocked'), asks=['Approve it?']))

    def test_yotam_hands_his_own_task_to_the_manager(self):
        """D224: a task written by the task skill on the board becomes the manager's."""
        tid = self.b.add_task('owner', 'Fog slider', '## Why\nSo the fog can be tuned.\n\n## What to do\nA slider.',
                              status='idea')
        bare = self.b.add_task('owner', 'No reasoning', 'Just do it', status='ready')
        with self.assertRaises(ValueError):
            store.adopt(self.b, bare)
        gid = store.adopt(self.b, tid)
        self.assertEqual(self.b.q1('SELECT goal_id FROM pm_tasks WHERE task_id=?', tid)[0], gid)
        self.assertTrue(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup=?", f'task:{tid}'))  # its proposal
        with self.assertRaises(ValueError):
            store.adopt(self.b, tid)

    def test_a_goal_is_planned_with_yotam_in_its_thread(self):
        """D226: a message opens a planning conversation in its own thread; the planner's
        session resumes on each reply; Approve plan creates the tasks ready, and they start."""
        import discord
        import manager_brief as brief
        import manager_talk as talk
        store.control(self.b, 'resume')
        store.receive(self.b, 'discord:900', 'reply', 'the flesh on the mutant should look like its skin')
        self.manager.input_events()
        gid = self.b.q1('SELECT max(id) FROM pm_goals')[0]
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals WHERE id=?', gid)[0], 'planning')
        self.assertTrue(self.b.q1("SELECT 1 FROM pm_outbox WHERE dedup=? AND goal_id=?", f'talk-open:{gid}', gid))
        with patch.object(core.release, 'reading', return_value=self.root):
            self.manager.plan()
        first = dict(self.b.q1("SELECT * FROM pm_runs WHERE goal_id=? AND role='planner'", gid))
        self.assertIn('plan mode', first['prompt'])
        self.assertIn('His words are his', first['prompt'])
        self.assertIn('should look like its skin', first['prompt'])
        self.assertIsNone(first['session'])
        # its first turn talks and asks, with options
        said = result('chat', head='', summary='I read it two ways. Which do you mean?', asks=[
            {'question': 'Which reading?', 'options': ['Flesh coloured like the skin', 'The skin itself regrows']}])
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=?,session=? WHERE id=?",
                           (json.dumps(said), 'S1', first['id']))
        self.manager.reconcile()
        g = self.b.q1('SELECT * FROM pm_goals WHERE id=?', gid)
        self.assertEqual((g['status'], g['session']), ('talking', 'S1'))
        reply = self.b.q1("SELECT * FROM pm_outbox WHERE dedup LIKE 'talk:%' AND goal_id=?", gid)
        self.assertIn('I read it two ways', reply['body'])
        self.assertIn('**b.** The skin itself regrows', reply['body'])
        ask = self.b.q1('SELECT * FROM pm_asks WHERE goal_id=?', gid)
        text, n, total = brief.answer(self.b, ask['id'], {0: 'The skin itself regrows'})
        self.assertEqual((n, total), (1, 1))
        store.receive(self.b, 'interaction:77', 'reply', text, goal=gid)
        self.manager.input_events()
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals WHERE id=?', gid)[0], 'planning')
        with patch.object(core.release, 'reading', return_value=self.root):
            self.manager.plan()
        second = dict(self.b.q1("SELECT * FROM pm_runs WHERE goal_id=? AND role='planner' AND state='queued'", gid))
        self.assertEqual(second['session'], 'S1')  # the same session: it remembers the conversation
        self.assertTrue(second['prompt'].startswith('Yotam replied in the planning thread'))
        self.assertIn('The skin itself regrows', second['prompt'])
        self.assertLess(len(second['prompt']), 600)
        self.assertEqual(self.b.q1('SELECT pending FROM pm_goals WHERE id=?', gid)[0], '')
        # its second turn plans; the plan waits for Approve
        planned = result('planned', head='', summary='One task: the skin closes over the wound.', tasks=[
            dict(title='The skin grows back over a wound', body='Scope: the closing skin', problem='',
                 reasoning='Yotam: "The skin itself regrows". Approach: the authored skin closes the hole.',
                 depends=[], model='opus', model_reason='the renderer')])
        self.b.con.execute("UPDATE pm_runs SET state='finished',result=?,session=? WHERE id=?",
                           (json.dumps(planned), 'S1', second['id']))
        self.manager.reconcile()
        self.assertFalse(self.b.q1('SELECT 1 FROM pm_tasks WHERE goal_id=?', gid))  # nothing until Approve
        key = second['id'][:12]
        row = dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', f'plan:{gid}:{key}'))
        self.assertIn('Approach: the authored skin closes the hole', row['body'])
        self.assertIn('**1. The skin grows back over a wound**', row['body'])

        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            thread = Mock(spec=discord.Thread)
            thread.send = AsyncMock(return_value=Mock(id=1))
            client.destination = AsyncMock(return_value=thread)
            await client.deliver_row(row)
            ids = [c.custom_id for c in thread.send.await_args.kwargs['view'].children]
            click = Mock(id=88, type=discord.InteractionType.component, data={'custom_id': f'pm:plan:{gid}:{key}'},
                         user=Mock(id=33), guild=Mock(id=11), channel=Mock(id=22, parent_id=None),
                         response=Mock(send_message=AsyncMock()))
            await client.on_interaction(click)
            # the goal's thread hangs off Yotam's own message
            root = Mock()
            root.get_partial_message.return_value.create_thread = AsyncMock(return_value=Mock(id=555, archived=False))
            with fe_board_Board() as b:
                made = await client.goal_thread(b, root, gid)
            await client.close()
            return ids, click.response.send_message.await_args.args[0], made, root.get_partial_message.call_args
        import fe_board
        fe_board_Board = fe_board.Board
        ids, said, made, on = asyncio.run(exercise())
        self.assertIn(f'pm:plan:{gid}:{key}', ids)
        self.assertIn('Approved', said)
        self.assertEqual((made.id, on.args[0]), (555, 900))
        self.assertEqual(talk.goal_for_channel(self.b, 555), gid)
        self.manager.input_events()
        tid = self.b.q1('SELECT task_id FROM pm_tasks WHERE goal_id=?', gid)[0]
        self.assertEqual(self.b.task(tid)['status'], 'ready')  # approved: it starts
        self.assertEqual(self.b.q1('SELECT status FROM pm_goals WHERE id=?', gid)[0], 'active')
        self.assertIn('From the approved plan', self.b.q1('SELECT body FROM pm_outbox WHERE dedup=?', f'task:{tid}')[0])
        store.receive(self.b, 'interaction:89', 'plan', key, goal=gid)  # a second Approve
        self.manager.input_events()
        self.assertEqual(self.b.q1('SELECT count(*) FROM pm_tasks WHERE goal_id=?', gid)[0], 1)
        # a run's live line in its thread
        r = dict(self.b.q1("SELECT * FROM pm_runs WHERE id=?", second['id']))
        self.assertIn('Planning', transport.progress_text(self.b, r))

    # ---------------------------------------------------------------- D238: the ChatGPT tier

    def dispatch(self, tid):
        """queue_task with the checkout free, as the supervisor would call it."""
        m = dict(self.b.q1('SELECT * FROM pm_tasks WHERE task_id=?', tid))
        with patch.object(fe_sync, 'sessions', return_value=[]), patch.object(fe_sync, 'app_processes', return_value=[]), \
                patch.object(fe_sync, 'sync_pull', return_value=Mock(ok=True)), \
                patch.object(fe_sync, 'git_out', return_value='b' * 40), \
                patch.object(fe_sync, 'checkout_state', return_value=([], [], 0)):
            return self.manager.queue_task(m)

    def test_yotam_can_pin_the_chatgpt_model(self):
        """D238: a third tier, on the Codex CLI, pinned the same way as the two Claude ones."""
        self.assertEqual(models.provider_of('sol'), 'codex')
        self.assertEqual(models.provider_of('opus'), 'claude')
        self.assertEqual(models.model_id(self.config, 'sol'), 'gpt-6.1-sol')
        self.assertEqual(models.model_id({'models': {'sol': 'gpt-6.2-sol'}}, 'sol'), 'gpt-6.2-sol')
        self.assertEqual(models.label('sol'), 'Sol')
        self.assertEqual(models.label('gpt-6.2-sol'), 'GPT-6.2 Sol')  # a config override still reads
        self.assertEqual(models.tier_of_model('gpt-6.1-sol'), 'sol')
        tid = self.managed('A pause button in the menu bar')
        models.set_owner_tier(self.b, tid, 'sol')
        self.assertEqual(models.choose(self.b, tid), ('sol', 'Yotam chose it'))
        models.set_owner_tier(self.b, tid, 'auto')
        self.assertEqual(models.choose(self.b, tid)[0], 'sonnet')  # the policy never picks it itself
        self.assertEqual(board.post(self.b, '/api/manager', {'action': 'model', 'task': tid, 'tier': 'sol'}), {})
        self.assertEqual(models.routes(self.b, [tid])[tid]['owner_tier'], 'sol')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fe_manager.main(['model', f'T{tid}', 'sol']), 0)
            # The planner routes the tasks and reads the repo: it stays on a Claude tier.
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(fe_manager.main(['model', 'planner', 'sol']), 1)
        store.set_setting(self.b, 'planner_model', 'sol')
        self.assertEqual(models.planner_tier(self.b), 'opus')

    def test_a_task_pinned_to_the_chatgpt_model_runs_on_the_codex_cli(self):
        """D238: the tier carries its CLI, and the Codex command names the model."""
        tid = self.managed('A pause button in the menu bar')
        models.set_owner_tier(self.b, tid, 'sol')
        self.assertTrue(self.dispatch(tid))
        run = dict(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        self.assertEqual(run['provider'], 'codex')
        stats = self.b.q1('SELECT * FROM pm_run_stats WHERE run_id=?', run['id'])
        self.assertEqual((stats['tier'], stats['model'], stats['provider']), ('sol', 'gpt-6.1-sol', 'codex'))
        self.assertIn('GPT-6.1 Sol worker starting', self.b.q1('SELECT body FROM pm_outbox ORDER BY id DESC')[0])
        cmd = workers.command(dict(run, model=stats['model']), self.config, self.root)
        self.assertEqual(cmd[cmd.index('-m') + 1], 'gpt-6.1-sol')
        self.assertLess(cmd.index('exec'), cmd.index('-m'))
        # Resuming its own session keeps the model flag after the session id.
        resumed = workers.command(dict(run, model=stats['model'], session='sess-1'), self.config, self.root)
        self.assertEqual(resumed[resumed.index('resume') + 1], 'sess-1')
        self.assertEqual(resumed[resumed.index('-m') + 1], 'gpt-6.1-sol')
        # A run that fell back to Codex (no tier) still takes that CLI's own default model.
        self.assertNotIn('-m', workers.command(dict(run, model='codex'), self.config, self.root))

    def test_a_task_pinned_to_a_model_waits_for_its_cli(self):
        """D238: a pin is Yotam's choice of model, so a blocked CLI makes its task wait, and
        the wait says so; a task the policy routed still falls back to the other CLI."""
        pinned = self.managed('A pause button in the menu bar')
        models.set_owner_tier(self.b, pinned, 'sol')
        store.block_provider(self.b, 'codex', 'allowance', time.time() + 3600, 1, 3600)
        self.assertFalse(self.dispatch(pinned))
        self.assertFalse(self.b.q('SELECT id FROM pm_runs'))
        store.set_setting(self.b, store.SLOTS, json.dumps({'slots': 2, 'providers': ['claude']}))
        o = outlook.outlook(self.b, self.config)
        waits = [w for w in o['waiting'] if w['ref'] == f'T{pinned}']
        self.assertIn('pinned to Sol, which waits: codex waiting: allowance', waits[0]['why'])
        self.assertIn('until Sol can run again', '\n'.join(outlook.lines(o)))
        # Unpinned, the same task falls back to whatever is free, at that CLI's own model.
        models.set_owner_tier(self.b, pinned, 'auto')
        store.clear_provider(self.b, 'codex')
        store.block_provider(self.b, 'claude', 'allowance', time.time() + 3600, 1, 3600)
        self.assertTrue(self.dispatch(pinned))
        run = dict(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        stats = self.b.q1('SELECT * FROM pm_run_stats WHERE run_id=?', run['id'])
        self.assertEqual((run['provider'], stats['tier'], stats['model']), ('codex', '', 'codex'))

    def test_a_run_that_changes_cli_starts_a_fresh_session(self):
        """D238: a Claude session id is not a Codex one, so the next run starts fresh with the
        feedback, and is told that none of the conversation came with it."""
        tid = self.managed('A pause button in the menu bar')
        self.b.con.execute("UPDATE pm_tasks SET phase='revise',worker_provider='claude',worker_session='s-1',"
                           "feedback='Yotam: make the chip bigger.' WHERE task_id=?", (tid,))
        models.set_owner_tier(self.b, tid, 'sol')
        self.assertTrue(self.dispatch(tid))
        run = dict(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        self.assertIsNone(run['session'])
        self.assertIn('Yotam: make the chip bigger.', run['prompt'])
        self.assertIn('a session does not cross CLIs', run['prompt'])
        self.assertIsNone(self.b.q1('SELECT worker_session FROM pm_tasks WHERE task_id=?', tid)[0])

    def test_a_session_past_the_cap_starts_afresh_with_the_brief(self):
        """D253: resuming a 344k session re-read it on every turn; past the cap the next run starts
        a new session told where the work stands, and a new session carries the brief."""
        from unittest import mock
        import manager_context as context
        tid = self.managed('A pause button in the menu bar')
        self.b.con.execute("UPDATE tasks SET body=body || ? WHERE id=?", ('\nAs D69 says, keep `fe_docs.cmd_build`.', tid))
        self.b.con.execute("UPDATE pm_tasks SET phase='revise',worker_provider='claude',worker_session='s-big',"
                           "feedback='Yotam: make the chip bigger.' WHERE task_id=?", (tid,))
        models.set_owner_tier(self.b, tid, 'sonnet')
        with mock.patch.object(context, 'session_context', return_value=344_000) as size:
            self.assertTrue(self.dispatch(tid))
            size.assert_called_once_with('s-big')
        run = dict(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        self.assertIsNone(run['session'])
        self.assertIn('Yotam: make the chip bigger.', run['prompt'])
        self.assertIn('grown to 344k tokens', run['prompt'])
        self.assertIn('## Gathered for you (D253)', run['prompt'])
        self.assertIn('### D69 ', run['prompt'])
        self.assertIn('fe_docs.py', run['prompt'])  # fe_docs.cmd_build was looked up

    def test_a_batch_runs_every_launch_and_fails_if_one_did(self):
        """D253: one `gpu --batch FILE` call stands for many one-launch calls."""
        import contextlib
        import io
        import fe_manager
        f = self.root / 'batch.txt'
        f.write_text(f'# shots\n"{sys.executable}" -c "print(1)"\n\n"{sys.executable}" -c "import sys; sys.exit(3)"\n',
                     encoding='utf-8')
        self.assertEqual(len(fe_manager.batch_lines(f)), 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(fe_manager.run_batch(f), 1)
        self.assertIn('[1/2] exit 0', out.getvalue())
        self.assertIn('[2/2] exit 3', out.getvalue())
        self.assertIn('batch: 1 of 2 ok', out.getvalue())

    def test_a_session_under_the_cap_is_resumed_without_a_second_brief(self):
        from unittest import mock
        import manager_context as context
        tid = self.managed('A pause button in the menu bar')
        self.b.con.execute("UPDATE tasks SET body=body || ? WHERE id=?", ('\nAs D69 says.', tid))
        self.b.con.execute("UPDATE pm_tasks SET phase='revise',worker_provider='claude',worker_session='s-small'"
                           " WHERE task_id=?", (tid,))
        models.set_owner_tier(self.b, tid, 'sonnet')
        with mock.patch.object(context, 'session_context', return_value=90_000):
            self.assertTrue(self.dispatch(tid))
        run = dict(self.b.q1("SELECT * FROM pm_runs WHERE state='queued'"))
        self.assertEqual(run['session'], 's-small')
        self.assertNotIn('Gathered for you', run['prompt'])

    def test_a_chatgpt_run_is_metered_under_its_own_model(self):
        """D238: its tokens and time are its own row, not folded into a generic Codex."""
        tid = self.managed('A pause button in the menu bar')
        models.set_owner_tier(self.b, tid, 'sol')
        self.assertTrue(self.dispatch(tid))
        rid = self.b.q1("SELECT id FROM pm_runs WHERE state='queued'")[0]
        meter = models.Meter(started=1000.0, model=self.b.q1(
            'SELECT model FROM pm_run_stats WHERE run_id=?', rid)[0])
        meter.feed({'type': 'item.started', 'item': {'id': 'i1', 'type': 'command_execution'}}, now=1010.0)
        meter.feed({'type': 'item.completed', 'item': {'id': 'i1', 'type': 'command_execution'}}, now=1020.0)
        meter.feed({'type': 'turn.completed', 'usage': {'input_tokens': 500, 'cached_input_tokens': 400,
                                                        'output_tokens': 60}}, now=1030.0)
        meter.save(self.b, rid, now=1030.0, ended=True)
        s = models.task_stats(self.b, tid)
        self.assertEqual((s['tier'], s['model'], s['output_tokens']), ('sol', 'gpt-6.1-sol', 60))
        self.assertEqual(list(s['models']), ['gpt-6.1-sol'])
        self.assertIn('GPT-6.1 Sol', s['line'])
        summary = models.summary(self.b)
        self.assertIn('sol', summary['tiers'])
        self.assertNotIn('codex', summary['tiers'])
        self.assertEqual(summary['labels']['sol'], 'GPT-6.1 Sol')
        self.assertIn('GPT-6.1 Sol (codex) 1 task', '\n'.join(models.report(self.b)))


    def test_a_tier_is_a_class_that_runs_its_latest_model(self):
        """D298: Sol is the newest listed gpt-N-sol; a hidden or unreadable list falls back; a pin wins."""
        d = Path(tempfile.mkdtemp())
        f = d / 'models_cache.json'
        f.write_text(json.dumps({'models': [{'slug': 'gpt-6.1-sol', 'visibility': 'list'},
                                            {'slug': 'gpt-6.10-sol', 'visibility': 'list'},
                                            {'slug': 'gpt-7-sol', 'visibility': 'hide'},
                                            {'slug': 'gpt-6.2-luna', 'visibility': 'list'}]}), encoding='utf-8')
        self.assertEqual(models.latest_codex('sol', f), 'gpt-6.10-sol')
        self.assertEqual(models.latest_codex('sol', d / 'missing.json'), '')
        self.assertEqual(models.model_id(None, 'sol'), 'gpt-6.1-sol')  # the fixture's newest
        self.assertEqual((models.model_id(None, 'opus'), models.model_id(None, 'sonnet')), ('opus', 'sonnet'))
        self.assertEqual(models.model_id({'models': {'opus': 'claude-opus-5'}}, 'opus'), 'claude-opus-5')
        # the class is named by the model its latest run was, once it has run
        self.assertEqual(models.tier_label(self.b, 'opus'), 'Opus')
        r = self.new_run(self.task()[1], 'worker', provider='claude')
        models.queued(self.b, r['id'], r['task_id'], None, 'worker', 'claude', 'opus', 'opus', time.time())
        self.b.con.execute("UPDATE pm_run_stats SET tier='opus', model='claude-opus-5-5' WHERE run_id=?", (r['id'],))
        self.assertEqual(models.tier_label(self.b, 'opus'), 'Opus 5.5')

    def test_the_newest_claude_cli_runs_the_classes(self):
        """D298: of the configured CLI, the native install's versions and the desktop app's copy,
        the newest runs; a stand-in that is not a Claude CLI runs as configured."""
        home = Path(tempfile.mkdtemp())
        bin_ = home / '.local' / 'bin' / 'claude.exe'
        old = home / '.local' / 'share' / 'claude' / 'versions' / '2.1.267'
        new = home / 'AppData' / 'Roaming' / 'Claude' / 'claude-code' / '2.1.286' / 'abc' / 'claude.exe'
        for f in (bin_, old, new):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'')
        with patch.object(Path, 'home', return_value=home),                 patch.dict(os.environ, {'APPDATA': str(home / 'AppData' / 'Roaming'), 'LOCALAPPDATA': str(home / 'AppData' / 'Local')}),                 patch.object(host, '_cli_version', side_effect=lambda f: (2, 1, 267) if f == bin_ else
                             tuple(int(x) for x in next(p for p in f.parts if p[:1].isdigit()).split('.'))):
            self.assertEqual(host.claude_cli(str(bin_)), str(new))
            new.unlink()
            self.assertEqual(host.claude_cli(str(bin_)), str(bin_))  # a tie keeps the configured one
        # D326: outside the Store app, its copy is only in the package's LocalCache
        packaged = home / 'AppData' / 'Local' / 'Packages' / 'Claude_pzs8sxrjxfjjc' / 'LocalCache' / 'Roaming' / \
            'Claude' / 'claude-code' / '2.1.286' / 'abc' / 'claude.exe'
        packaged.parent.mkdir(parents=True, exist_ok=True)
        packaged.write_bytes(b'')
        with patch.object(Path, 'home', return_value=home), \
                patch.dict(os.environ, {'APPDATA': str(home / 'AppData' / 'Roaming'),
                                        'LOCALAPPDATA': str(home / 'AppData' / 'Local')}), \
                patch.object(host, '_cli_version', side_effect=lambda f: (2, 1, 267) if f == bin_ else
                             tuple(int(x) for x in next(p for p in f.parts if p[:1].isdigit()).split('.'))):
            self.assertEqual(host.claude_cli(str(bin_)), str(packaged))
        self.assertEqual(host.claude_cli(sys.executable), sys.executable)
        cmd = workers.command(dict(provider='claude', role='worker', model='opus'), self.config, Path('.'))
        self.assertEqual(cmd[0], self.config['claude'])

# D243: the two lines the CLIs write, as captured on this machine (the Codex one with its
# token totals shortened). Claude's utilization is a fraction; Codex's used_percent is not.
CLAUDE_LIMIT = ('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1791435600,'
                '"rateLimitType":"seven_day","utilization":0.27,"isUsingOverage":false,"unifiedWindows":'
                '{"five_hour":{"utilization":0.44,"resetsAt":1790895600},"seven_day":{"utilization":0.27,'
                '"resetsAt":1791435600}}},"uuid":"ca4e7198","session_id":"4531ce6d"}')
CODEX_LIMIT_LINE = ('{"timestamp":"2026-10-01T19:52:50.503Z","type":"event_msg","payload":{"type":"token_count",'
                    '"info":{"total_token_usage":{"total_tokens":8625}},"rate_limits":{"limit_id":"codex",'
                    '"limit_name":null,"primary":{"used_percent":5.0,"window_minutes":10080,"resets_at":1791478459},'
                    '"secondary":null,"credits":{"has_credits":false,"unlimited":false,"balance":"0"},'
                    '"individual_limit":null,"spend_control_reached":null,"plan_type":"prolite",'
                    '"rate_limit_reached_type":null}}}')
CODEX_IMPORTED_LINE = ('{"timestamp":"2026-10-01T20:03:16.876Z","type":"event_msg","payload":{"type":"token_count",'
                       '"info":{"total_token_usage":{"total_tokens":8625}},"rate_limits":null}}')
USAGE_NOW = 1790890902.0  # a few minutes after both captures


class UsageTests(unittest.TestCase):
    """D243: how much of each allowance is spent, as the status says it."""
    setUp, tearDown = ManagerTests.setUp, ManagerTests.tearDown

    def codex_home(self, *rollouts, threads=None):
        """A fake CODEX_HOME: rollouts as (name, [lines]); threads as (rollout name, updated_at, originator)."""
        home = self.root / 'codex'
        day = home / 'sessions' / '2026' / '10' / '01'
        day.mkdir(parents=True)
        for name, lines in rollouts:
            (day / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        if threads:
            con = sqlite3.connect(str(home / 'state_5.sqlite'))
            con.execute('CREATE TABLE threads (id TEXT, updated_at INTEGER, originator TEXT, rollout_path TEXT)')
            for name, at, who in threads:
                con.execute('INSERT INTO threads VALUES (?,?,?,?)', (name, at, who, str(day / name)))
            con.commit()
            con.close()
        return home

    def test_a_claude_reading_is_taken_from_the_stream_and_kept_after_the_run(self):
        meter = models.Meter(started=1000.0)
        meter.feed(json.loads(CLAUDE_LIMIT), now=USAGE_NOW)
        self.assertEqual([(w['name'], w['used']) for w in meter.usage['windows']], [('5-hour', 44.0), ('weekly', 27.0)])
        self.assertTrue(usage.record(self.b, 'claude', meter.usage))
        d = usage.describe('claude', usage.load(self.b)['claude'], USAGE_NOW + 720)
        self.assertEqual(d['state'], 'ok')
        self.assertEqual(d['read'], 'read 12m ago')
        self.assertIn('5-hour 44% (resets in 1h 06m)', d['text'])
        self.assertIn('weekly 27% (resets ', d['text'])
        self.assertTrue(d['text'].endswith('read 12m ago'))
        # an event with no windows is not a reading, and does not wipe the last one
        meter.feed({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed'}}, now=USAGE_NOW + 5)
        self.assertEqual(meter.usage['at'], USAGE_NOW)
        self.assertIsNone(usage.from_claude({'type': 'assistant'}))
        # a refusal is said on the line
        event = json.loads(CLAUDE_LIMIT)
        event['rate_limit_info']['status'] = 'rejected'
        self.assertEqual(usage.from_claude(event, USAGE_NOW)['note'], 'status rejected')

    def test_a_codex_reading_is_read_back_from_its_rollout_and_an_imported_log_is_not_one(self):
        home = self.codex_home(('rollout-a.jsonl', [CODEX_LIMIT_LINE, '{"type":"event_msg","payload":{"type":"agent_message"}}']),
                               ('rollout-b.jsonl', [CODEX_IMPORTED_LINE]))
        seen = fe_codex.rate_limits(now=USAGE_NOW, root=home)
        self.assertEqual(seen['rate_limits']['primary']['used_percent'], 5.0)
        self.assertAlmostEqual(seen['at'], 1790884370.503, places=2)
        reading = usage.from_codex(seen)
        self.assertEqual(reading['note'], 'prolite')  # no credits: nothing said of them
        self.assertTrue(usage.poll_codex(self.b, now=USAGE_NOW, root=home))
        self.assertEqual(usage.load(self.b)['codex']['windows'][0]['name'], 'weekly')

    def test_the_thread_store_picks_the_logs_and_skips_the_imported_threads(self):
        later = CODEX_LIMIT_LINE.replace('19:52:50.503Z', '20:30:00.000Z').replace('5.0', '9.0')
        home = self.codex_home(('rollout-own.jsonl', [CODEX_LIMIT_LINE]), ('rollout-import.jsonl', [later]),
                               threads=[('rollout-own.jsonl', int(USAGE_NOW) - 60, 'codex_cli'),
                                        ('rollout-import.jsonl', int(USAGE_NOW) - 30, None)])
        self.assertEqual(fe_codex.rate_limits(now=USAGE_NOW, root=home)['rate_limits']['primary']['used_percent'], 5.0)

    def test_the_freshest_reading_wins(self):
        old = usage.from_codex({'at': 100.0, 'rate_limits': json.loads(CODEX_LIMIT_LINE)['payload']['rate_limits']})
        new = dict(old, at=200.0, windows=[dict(old['windows'][0], used=11.0)])
        self.assertTrue(usage.record(self.b, 'codex', new))
        self.assertFalse(usage.record(self.b, 'codex', old))  # an older one, arriving later
        self.assertEqual(usage.load(self.b)['codex']['windows'][0]['used'], 11.0)
        self.assertFalse(usage.record(self.b, 'codex', None))

    def test_a_missing_reading_says_so_and_is_never_zero(self):
        o = outlook.outlook(self.b, self.config)
        self.assertEqual([(u['provider'], u['state'], u['text']) for u in o['usage']],
                         [('claude', 'none', usage.NO_READING), ('codex', 'none', usage.NO_READING)])
        text = '\n'.join(outlook.lines(o))
        self.assertIn('Claude: no reading yet; the next run on it takes one', text)
        self.assertNotIn('0%', text)

    def test_a_reading_older_than_its_window_or_past_its_reset_is_stale(self):
        usage.record(self.b, 'claude', usage.from_claude(json.loads(CLAUDE_LIMIT), USAGE_NOW))
        six_hours = USAGE_NOW + 6 * 3600  # past the 5-hour window's reset and its length
        d = usage.usage(self.b, six_hours)[0]
        self.assertEqual(d['state'], 'stale')
        self.assertEqual([w['stale'] for w in d['windows']], [True, False])
        self.assertIn('5-hour 44% (stale, reset since)', d['text'])
        self.assertIn('weekly 27% (resets', d['text'])

    def test_a_null_primary_or_secondary_gives_one_window_and_both_null_none(self):
        limits = json.loads(CODEX_LIMIT_LINE)['payload']['rate_limits']
        only_primary = usage.from_codex({'at': 1.0, 'rate_limits': limits})
        self.assertEqual([w['name'] for w in only_primary['windows']], ['weekly'])
        swapped = dict(limits, primary=None, secondary={'used_percent': 61.5, 'window_minutes': 300, 'resets_at': 9e9})
        only_secondary = usage.from_codex({'at': 1.0, 'rate_limits': swapped})
        self.assertEqual([(w['name'], w['used']) for w in only_secondary['windows']], [('5-hour', 61.5)])
        self.assertIsNone(usage.from_codex({'at': 1.0, 'rate_limits': dict(limits, primary=None)}))
        self.assertIsNone(usage.from_codex({'at': 1.0, 'rate_limits': None}))
        credits = dict(limits, credits={'has_credits': True, 'unlimited': False, 'balance': '12'})
        self.assertEqual(usage.from_codex({'at': 1.0, 'rate_limits': credits})['note'], 'prolite, credits 12')

    def test_every_view_takes_the_same_reading_from_the_outlook(self):
        usage.record(self.b, 'claude', usage.from_claude(json.loads(CLAUDE_LIMIT), USAGE_NOW))
        usage.record(self.b, 'codex', usage.from_codex(
            {'at': USAGE_NOW, 'rate_limits': json.loads(CODEX_LIMIT_LINE)['payload']['rate_limits']}))
        o = outlook.outlook(self.b, self.config, now=USAGE_NOW + 180)
        plain = outlook.lines(o, now=USAGE_NOW + 180)
        md = outlook.lines(o, now=USAGE_NOW + 180, md=True)
        self.assertTrue(any(x.startswith('Allowance used') for x in plain), plain)
        self.assertTrue(any(x.startswith('**Allowance used**') for x in md), md)
        self.assertTrue(any('Claude: 5-hour 44%' in x and 'read 3m ago' in x for x in plain), plain)
        self.assertTrue(any('**Claude**: 5-hour 44%' in x and 'read 3m ago' in x for x in md), md)
        self.assertTrue(any('Codex: weekly 5% ' in x and 'read 3m ago' in x and 'prolite' in x for x in plain), plain)
        json.dumps(o['usage'])  # the board serves it as it is


class SubmoduleSyncTests(unittest.TestCase):
    """D329: the studio is a submodule; sync follows its pin and pushes it first."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'protocol.file.allow', 'GIT_CONFIG_VALUE_0': 'always',
               'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
               'GIT_COMMITTER_EMAIL': 't@t'}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for name in ('studio.git', 'game.git'):
            self.git(self.tmp, 'init', '-q', '--bare', '-b', 'main', name)
        s0 = self.clone('studio.git', 's0')
        (s0 / 'a.py').write_text('1')
        self.git(s0, 'add', '.')
        self.git(s0, 'commit', '-qm', 'studio 1')
        self.git(s0, 'push', '-q', 'origin', 'HEAD:main')
        self.a = self.clone('game.git', 'a')
        (self.a / 'x').write_text('x')
        self.git(self.a, 'add', '.')
        self.git(self.a, 'commit', '-qm', 'game 1')
        self.git(self.a, 'submodule', 'add', '-q', '-b', 'main', str(self.tmp / 'studio.git'), 'studio')
        self.git(self.a, 'commit', '-qm', 'add studio')
        self.git(self.a, 'push', '-q', 'origin', 'HEAD:main')
        self.reg = {'remote': 'origin', 'branch': 'main', 'agents': {}}

    def git(self, cwd, *args):
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, (args, p.stderr))
        return p.stdout.strip()

    def clone(self, src, name):
        self.git(self.tmp, 'clone', '-q', str(self.tmp / src), name)
        return self.tmp / name

    def test_a_clone_gets_the_studio_on_its_branch_and_a_studio_commit_lands_first(self):
        b = self.clone('game.git', 'b')
        self.assertEqual(fe_sync.follow_submodules(b), [])
        self.assertEqual(fe_sync.branch_of(b / 'studio'), 'main')
        self.assertTrue((b / 'studio' / 'a.py').exists())
        (b / 'studio' / 'a.py').write_text('2')
        self.git(b / 'studio', 'commit', '-qam', 'studio 2')
        self.assertEqual(fe_sync.submodules_ahead(b), ['studio'])  # the Stop hook pushes it
        r = fe_sync.sync_push(b, self.reg, gate=False)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(self.git(self.tmp / 'studio.git', 'log', '-1', '--format=%s', 'main'), 'studio 2')
        self.assertEqual(self.git(self.tmp / 'game.git', 'log', '-1', '--format=%s', 'main'), 'studio: studio 2')
        r = fe_sync.sync_pull(self.a, self.reg)
        self.assertTrue(r.ok, r.message)
        self.assertEqual((self.a / 'studio' / 'a.py').read_text(), '2')
        self.assertEqual(fe_sync.branch_of(self.a / 'studio'), 'main')

    def test_the_game_never_pins_a_studio_commit_its_origin_lacks(self):
        (self.a / 'studio' / 'a.py').write_text('3')
        self.git(self.a / 'studio', 'commit', '-qam', 'studio 3')
        self.git(self.a, 'commit', '-qam', 'pin an unpushed studio commit')
        with patch.object(fe_sync, 'git', wraps=fe_sync.git) as g:
            g.side_effect = lambda repo, *a, **k: (subprocess.CompletedProcess(a, 1, '', 'refused')
                                                   if a[:1] == ('push',) and Path(repo).name == 'studio'
                                                   else fe_sync.run(['git', *a], cwd=repo))
            r = fe_sync.sync_push(self.a, self.reg, gate=False)
        self.assertFalse(r.ok)
        self.assertIn('studio', r.message)
        self.assertEqual(self.git(self.tmp / 'game.git', 'log', '-1', '--format=%s', 'main'), 'add studio')


if __name__ == '__main__':
    unittest.main()
