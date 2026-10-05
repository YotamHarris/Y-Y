#!/usr/bin/env python3
"""The studio's background project manager (D181).

setup --guild ID --channel ID --owner ID [--token]  configure; token is prompted locally
doctor | install | start | stop | pause | resume | status
goal TEXT       record a locally authorized goal (Discord accepts natural messages)
gpu [--bench] -- CMD ...  a managed worker's GPU command: shared, or a bench that goes first (D200);
                with no GPU adapter (studio.toml [adapters] gpu) the command just runs
NAME-night      a nightly job of the game's now (studio.toml [[nightly]]: bench-night, D262)
serve           from a checkout: a boot, which starts the settled release unless one
                serves; from a release: the supervisor (start/install keep it hidden)
upgrade         check origin/main for a new manager now, retrying a rejected one
release         the serving release, the settled one, and any handoff (D189)

Run in the venv installed from manager-requirements.txt. Configuration, logs and
run artifacts live beside the board database; the token lives in Credential Manager.
"""
import argparse
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import fe_board
import fe_sync
import manager_host as host
import manager_models as models
import manager_nightly as nightly
import manager_release as release
import manager_store as store
import manager_workers as workers
import studio_config


POLL_S = float(os.environ.get('FE_MANAGER_POLL_S', 60))  # the longest the loop waits between steps


class Offline:
    """FE_MANAGER_OFFLINE: no Discord, for the handoff's end-to-end test."""
    fatal_error = None

    async def start(self, token):
        await asyncio.Event().wait()

    async def close(self):
        pass


async def supervise(config, service):
    from manager_core import Manager
    wake = asyncio.Event()
    if os.environ.get('FE_MANAGER_OFFLINE'):
        client, token = Offline(), None
    else:
        from manager_discord import build_client
        client, token = build_client(config, wake), host.credential_read()
    handed = asyncio.Event()

    def step():
        with fe_board.Board() as b:
            if service.yields(b):
                handed.set()
                return
            Manager(b, config).tick()
            service.stepped(b)
            try:
                service.watch(b)
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                logging.getLogger(__name__).error('release watch: %s', e)

    def fingerprint():
        with fe_board.Board() as b:
            return (store.setting(b, 'mode'), b.last_event(),
                    tuple(tuple(r) for r in b.q('SELECT id,state FROM pm_runs WHERE state<>\'handled\'')),
                    b.q1("SELECT count(*) FROM pm_inbox WHERE state='pending'")[0],
                    b.q1('SELECT value FROM pm_settings WHERE key=?', release.HANDOFF))

    async def loop():
        previous, checked = None, 0
        while True:
            current = await asyncio.to_thread(fingerprint)
            if current != previous or wake.is_set() or time.monotonic() - checked >= POLL_S:
                wake.clear()
                # Keep Discord responsive during Git checks and process cleanup.
                await asyncio.to_thread(step)
                if handed.is_set():
                    return  # a ready candidate takes the lock once this process lets go (D189)
                checked = time.monotonic()
                previous = current  # changes produced during this step wake the next one
            try:
                await asyncio.wait_for(wake.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

    # Fail the service on an unrecoverable transport/scheduler failure so Task
    # Scheduler restarts it. Workers survive and are reconciled on restart.
    tasks = [asyncio.create_task(loop()), asyncio.create_task(client.start(token))]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if client.fatal_error:
            raise RuntimeError(client.fatal_error)
    finally:
        await client.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def batch_lines(path):
    """A batch file's commands: one a line; blank lines and `#` comments skipped."""
    with open(path, encoding='utf-8-sig') as f:
        return [s.strip() for s in f if s.strip() and not s.lstrip().startswith('#')]


def run_batch(path):
    """D253: the launches of one `gpu --batch FILE`, in order, under the claim its caller took.
    Last night a worker made 118 one-launch calls (each a turn that re-reads the context); a
    batch is one. Every command runs whatever the others did; each prints its exit, its time
    and its last lines, and the batch fails if any did."""
    import shlex
    lines = batch_lines(path)
    failed = 0
    for i, line in enumerate(lines, 1):
        t0 = time.time()
        # Windows parses a command line itself (quotes kept by shlex would name no file).
        # Hidden: a worker's own call may come from pythonw, and a console program started from a
        # process with no console opens a window that takes the focus.
        p = subprocess.run(line if os.name == 'nt' else shlex.split(line), capture_output=True, text=True,
                           encoding='utf-8', errors='replace', creationflags=workers.NO_WINDOW)
        out = [s for s in ((p.stdout or '') + (p.stderr or '')).splitlines() if s.strip()]
        failed += p.returncode != 0
        print(f'== [{i}/{len(lines)}] exit {p.returncode} in {time.time() - t0:.0f}s: {line[:140]}')
        for s in out[-6:]:
            print('   ' + s[:200])
    print(f'[fe-manager] batch: {len(lines) - failed} of {len(lines)} ok')
    return 1 if failed else 0


def gpu(argv):
    """A managed worker's GPU command (D200). A bench (`--bench`, or a command that
    names it) asks the agents with an open game to stash and waits for the GPU; any
    other command shares it, waiting only while a bench waits or runs. A game with no
    GPU adapter has no lease: the command runs at once."""
    rid = os.environ.get('FE_MANAGER_RUN')
    if not rid:
        raise ValueError('gpu requires FE_MANAGER_RUN from a managed worker')
    announced = bool(argv) and argv[0] == '--bench'
    if announced:
        argv = argv[1:]
    if argv and argv[0] == '--':
        argv = argv[1:]
    if not argv:
        raise ValueError('gpu [--bench] -- COMMAND ARGUMENTS is required')
    with fe_board.Board() as b:
        run = b.q1("SELECT * FROM pm_runs WHERE id=? AND state='running'", rid)
        if not run:
            raise ValueError('managed run is not active')

        def stopped():
            return store.setting(b, 'mode') == 'stopped'
        env = dict(os.environ, FE_MANAGER_GPU='gpu:' + uuid.uuid4().hex)
        timing = {}
        gpu_ = studio_config.adapter('gpu')
        try:
            if gpu_ is None:
                t0 = time.time()
                try:
                    return subprocess.run(argv, env=env, creationflags=fe_sync.NO_WINDOW).returncode
                finally:
                    timing['ran'] = time.time() - t0
            if announced or any('--bench' in a for a in argv):
                return gpu_.bench(argv, note=f'T{run["task_id"]}' if run['task_id'] else '',
                                  should_stop=stopped, env=env, timing=timing)
            return gpu_.share(argv, should_stop=stopped, env=env, timing=timing)
        finally:
            # D218: the wait for the GPU and the GPU command's own time go on the run's record.
            models.add_wait(rid, 'gpu_wait_s', timing.get('waited', 0), timing.get('ran', 0))


def start(config):
    problems = host.doctor(config)
    if problems:
        raise ValueError('\n'.join(problems))
    with fe_board.Board() as b:
        store.control(b, 'start')
    release.boot(config)


def serve(config, candidate):
    """A release's supervisor: the one that serves, or a candidate taking over (D189)."""
    service = release.Service(config, candidate)
    if not candidate and not os.environ.get('FE_MANAGER_OFFLINE'):
        problems = host.doctor(config)
        if problems:
            # The setup, not the code: nothing is rejected, the next boot tries again.
            with fe_board.Board() as b:
                import manager_brief as brief
                with b.tx():
                    brief.post(b, 'setup:' + '|'.join(problems), None,
                               'The manager cannot start: ' + '; '.join(problems) + '.',
                               ['Fix the setup on the PC (fe_manager.py doctor); it retries every minute.'],
                               ping=True, problem_text='')
            raise ValueError('\n'.join(problems))
    if candidate:
        with fe_board.Board() as b:
            if not service.announce(b, service.check()):
                return 1

        def still():
            with fe_board.Board() as b:
                return service.still_wanted(b)
        wait = release.READY_S
    else:
        wait, still = 0, (lambda: True)
    with host.singleton(wait, still):
        with fe_board.Board() as b:
            service.started(b)
        try:
            release.prune(config['repo'])
        except OSError as e:
            logging.getLogger(__name__).error('release prune: %s', e)
        asyncio.run(supervise(config, service))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('setup')
    for name in ('guild', 'channel', 'owner'):
        s.add_argument('--' + name)
    s.add_argument('--token', action='store_true')
    for name in ('doctor', 'install', 'start', 'stop', 'pause', 'resume', 'upgrade', 'release'):
        sub.add_parser(name)
    s = sub.add_parser('status', help='what runs, for how long and its latest; what waits; the stats')
    s.add_argument('--json', action='store_true', help="also the raw snapshot of the manager's tables")
    s = sub.add_parser('serve')
    s.add_argument('--candidate', metavar='TOKEN', help='a release taking over from the one serving')
    s = sub.add_parser('goal')
    s.add_argument('text', nargs='+')
    s = sub.add_parser('name', help='name a goal: name G3 "V165 improvements" (D193)')
    s.add_argument('goal')
    s.add_argument('text', nargs='+')
    s = sub.add_parser('models', help="each model's record, the routing policy and each task's spend (D218)")
    s.add_argument('tasks', nargs='*', help='T12 ... (default: the latest managed tasks)')
    s = sub.add_parser('adopt', help='hand a board task you wrote by the task skill to the manager (D224)')
    s.add_argument('task')
    s = sub.add_parser('model', help="set a task's model: model T12 "
                       + '|'.join(models.TIERS) + '|auto, or model planner '
                       + '|'.join(models.CLAUDE_TIERS))
    s.add_argument('task')
    s.add_argument('tier', choices=[*models.TIERS, 'auto'])
    s = sub.add_parser('gpu')
    s.add_argument('--bench', action='store_true', help='the command benches: it goes first (D200)')
    s.add_argument('--batch', metavar='FILE', help='D253: one command a line, all under one claim')
    s.add_argument('command', nargs=argparse.REMAINDER)
    s = sub.add_parser('_worker')
    s.add_argument('run_id')
    jobs = {}
    for j in nightly.jobs():  # the game's nightly jobs (D262: bench-night), each with its module's flags
        mod = nightly.module(j)
        for name, internal in ((f'{j["name"]}-night', False), (f'_{j["name"]}_night', True)):
            mod.arguments(sub.add_parser(name, **({} if internal else {'help': getattr(mod, 'HELP', None)})), internal)
            jobs[name] = (mod, internal)
    s = sub.add_parser('crew-night', help="D265: the cleanup crew's night now: the scorecard, posted; with "
                       'nightly_crew "on", its two tasks too')
    s.add_argument('--dry-run', action='store_true', help='print the scorecard and the task bodies; keep and post nothing')
    sub.add_parser('_crew_night')
    s = sub.add_parser('_batch')
    s.add_argument('file')
    args = p.parse_args(argv)
    try:
        if args.cmd == 'setup':
            print(host.configure(args.guild, args.channel, args.owner, args.token))
            return 0
        if args.cmd == 'gpu':
            if args.batch:
                lines = batch_lines(args.batch)
                bench = args.bench or any('--bench' in line for line in lines)
                return gpu((['--bench'] if bench else []) + ['--', sys.executable, os.path.abspath(__file__),
                                                             '_batch', os.path.abspath(args.batch)])
            return gpu((['--bench'] if args.bench else []) + args.command)
        if args.cmd == '_batch':
            return run_batch(args.file)
        if args.cmd in ('status', 'pause', 'resume', 'stop', 'goal', 'name', 'upgrade', 'release', 'models', 'model',
                        'adopt'):
            with fe_board.Board() as b:
                if args.cmd == 'adopt':
                    tid = int(args.task.upper().lstrip('T'))
                    gid = store.adopt(b, tid)
                    t = b.task(tid)
                    print(f'T{tid} is the manager\'s now (G{gid}): '
                          + ('approve it in its Discord thread or set it ready on the board to start it.'
                             if t['status'] == 'idea' else 'it starts when a run slot and a checkout are free.'))
                elif args.cmd == 'models':
                    tids = [int(t.upper().lstrip('T')) for t in args.tasks] or None
                    print('\n'.join(models.report(b, tids)))
                elif args.cmd == 'model':
                    if args.task.lower() == 'planner':
                        # D238: the planner routes the tasks and reads the repo; it stays on Claude.
                        if args.tier not in models.CLAUDE_TIERS:
                            raise ValueError('the planner takes ' + ' or '.join(models.CLAUDE_TIERS))
                        with b.tx():
                            store.set_setting(b, 'planner_model', args.tier)
                        print(f'Planner: {models.label(args.tier)}')
                    else:
                        tid = int(args.task.upper().lstrip('T'))
                        models.set_owner_tier(b, tid, args.tier)
                        print(f'T{tid}: ' + (f'{models.label(args.tier)} from its next run' if args.tier != 'auto'
                                             else 'the policy picks its model again'))
                elif args.cmd == 'name':
                    gid = int(args.goal.upper().lstrip('G'))
                    with b.tx():
                        store.name_goal(b, gid, ' '.join(args.text))
                    print(store.goal_label(b, gid))
                elif args.cmd == 'goal':
                    store.receive(b, 'local:' + uuid.uuid4().hex, 'reply', ' '.join(args.text))
                    print('Goal recorded. Use start/resume when ready to dispatch.')
                elif args.cmd == 'status':
                    if args.json:
                        snap = store.snapshot(b)
                        print(json.dumps(snap, indent=2))
                        for p_ in snap['providers']:
                            print(store.provider_line(p_))
                    import manager_outlook as outlook
                    cfg = host.load_config() if host.config_path().exists() else None
                    print('\n'.join(outlook.lines(outlook.outlook(b, cfg))))
                    print('\n' + release.line(b))
                elif args.cmd == 'release':
                    print(json.dumps(release.status(b), indent=2))
                elif args.cmd == 'upgrade':
                    with b.tx():
                        store.set_setting(b, release.UPGRADE, time.time())
                    print('The service checks origin/main at its next step; see `release`.')
                else:
                    store.control(b, args.cmd)
                    print('Manager ' + store.setting(b, 'mode'))
            return 0
        cfg = host.load_config()
        if args.cmd in jobs:
            mod, internal = jobs[args.cmd]
            return mod.command(args, cfg, internal)
        if args.cmd in ('crew-night', '_crew_night'):
            import manager_crew
            with fe_board.Board() as b:
                s, made = manager_crew.night(b, cfg, dry_run=getattr(args, 'dry_run', False))
                if args.cmd == '_crew_night' or not getattr(args, 'dry_run', False):
                    store.set_setting(b, manager_crew.NIGHT, s['day'])
            if not getattr(args, 'dry_run', False):
                print(f"crew night {s['day']}: scorecard kept and posted" + (f"; started {made}" if made else ''))
            return 0
        if args.cmd == '_worker':
            host.worker_job()
            return workers.worker_main(args.run_id, cfg)
        if args.cmd == 'doctor':
            problems = host.doctor(cfg)
            print('\n'.join(problems) if problems else 'Ready: Discord configuration, subscription logins and checkouts verified.')
            return int(bool(problems))
        if args.cmd == 'install':
            host.install_task(cfg['repo'])
            print('Installed sign-in task with restart on failure. Run start to activate.')
            return 0
        if args.cmd == 'start':
            start(cfg)
            print('Manager started.')
            return 0
        if args.cmd == 'serve':
            path = host.config_path().with_name('manager.log')
            logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
                path, maxBytes=5_000_000, backupCount=3, encoding='utf-8')],
                format='%(asctime)s %(levelname)s %(name)s %(message)s')
            if not release.in_release():
                release.boot(cfg)  # a checkout never serves: it starts the settled release
                return 0
            return serve(cfg, args.candidate)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, fe_board.BoardError) as e:
        print(f'[fe-manager] {e}', file=sys.stderr)
        logging.getLogger(__name__).error('%s', e)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
