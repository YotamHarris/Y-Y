"""Opt-in CLI contract smoke test using existing subscription logins.

This uses an empty temporary repository, no Discord and no live board. It asks
each CLI for a small structured reply, then resumes its exact session ID.
    python engine/tools/test_manager_live.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import fe_board
import manager_host
import manager_store as store
import manager_workers as workers


def environment_probe(b, cfg, repo, provider):
    session = None
    for stage in ('new', 'resume'):
        target = repo / (provider + '_' + stage + '_environment.json')
        prompt = ('Run a shell command that uses Python to read only FE_MANAGER_RUN and FE_BOARD_DIR '
                  'from os.environ and write them as a JSON object with those exact keys to '
                  + target.name + '. Read the actual environment; do not guess or supply values. '
                  'Do not inspect other files or edit anything else. Return status chat, summary '
                  '"environment checked", head "", and empty tasks/checks/artifacts arrays.')
        rid = store.queue_run(b, 'worker', provider, repo, prompt, session=session)
        code = workers.worker_main(rid, cfg)
        row = b.q1('SELECT * FROM pm_runs WHERE id=?', rid)
        if code or not target.exists():
            raise RuntimeError(f'{provider} {stage}: environment probe failed: {row["error"]}')
        actual = json.loads(target.read_text(encoding='utf-8-sig'))
        if actual != {'FE_MANAGER_RUN': rid, 'FE_BOARD_DIR': str(fe_board.board_dir())}:
            raise RuntimeError(f'{provider} {stage}: managed shell identity was not preserved')
        if not row['session'] or session and row['session'] != session:
            raise RuntimeError('environment probe changed the resumed session')
        session = row['session']
        print(f'{provider} {stage}: shell received current run identity and board path', flush=True)


def main():
    cfg = manager_host.load_config()
    with tempfile.TemporaryDirectory(prefix='fe-manager-cli-') as tmp:
        root = Path(tmp)
        repo = root / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Manager adapter test'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'manager-test@example.invalid'], check=True)
        old = os.environ.get('FE_BOARD_DIR')
        os.environ['FE_BOARD_DIR'] = str(root / 'board')
        try:
            with fe_board.Board() as b:
                for provider in ('codex', 'claude'):
                    if '--environment-only' in sys.argv:
                        environment_probe(b, cfg, repo, provider)
                        continue
                    session = None
                    for stage in ('new', 'resume'):
                        prompt = ('CLI adapter contract test. Do not use tools or inspect files. '
                                  'Return status chat, summary "adapter ok", head "", tasks [], '
                                  'checks [], artifacts [], matching the requested JSON schema.')
                        rid = store.queue_run(b, 'planner', provider, repo, prompt, session=session)
                        code = workers.worker_main(rid, cfg)
                        row = b.q1('SELECT * FROM pm_runs WHERE id=?', rid)
                        if code or not row['session']:
                            print(f'{provider} {stage}: FAILED {row["error"]}')
                            return 1
                        response = json.loads(row['result'])
                        if response['status'] != 'chat' or session and row['session'] != session:
                            print(f'{provider} {stage}: invalid response or changed session')
                            return 1
                        session = row['session']
                        print(f'{provider} {stage}: structured reply and exact session verified', flush=True)
                    filename = provider + '_probe.txt'
                    prompt = (f'Unattended worker adapter test in an empty temporary Git repository. '
                              f'Create {filename} containing exactly "adapter ok" followed by a newline. '
                              'Stage only that file and commit it locally. Do not push or inspect other directories. '
                              'If Git hits a filesystem permission error, request tool approval through the '
                              'configured automatic reviewer once; do not change permission settings yourself. '
                              'Then return status complete, summary "commit ok", head as the actual HEAD SHA, '
                              'and empty tasks/checks/artifacts arrays. If denied, return blocked with the reason.')
                    rid = store.queue_run(b, 'worker', provider, repo, prompt)
                    code = workers.worker_main(rid, cfg)
                    row = b.q1('SELECT * FROM pm_runs WHERE id=?', rid)
                    if code or json.loads(row['result'])['status'] != 'complete' or not (repo / filename).is_file():
                        print(f'{provider} unattended commit: FAILED {row["error"] or row["result"]}')
                        return 1
                    tracked = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain'],
                                             capture_output=True, text=True, check=True).stdout
                    if tracked:
                        print(f'{provider} unattended commit: dirty tree')
                        return 1
                    print(f'{provider} unattended file edit and local commit verified', flush=True)
        finally:
            if old is None:
                os.environ.pop('FE_BOARD_DIR', None)
            else:
                os.environ['FE_BOARD_DIR'] = old
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
