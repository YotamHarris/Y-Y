"""The cleanup crew's night (D265): after the nightly bench lets go, the scorecard, and two tasks.

The game's nightly jobs that hold the work (studio.toml [[nightly]]: the bench,
D262, from 04:00) go first. Once their hold is gone the crew's
night runs in its own hidden process (`fe_manager.py _crew_night`): fe_crew.py
reads git, the transcripts and the board, keeps the scorecard as
`<board>/manager/crew/DAY.json` and posts it to the channel, and each crew
task's first measured morning to that task's own thread (D319). With
`nightly_crew: "on"` it also writes the two tasks, a "Code crew" one worked by
the tidy-code skill and a "Process crew" one by tidy-process, and hands them
to the manager ready (Yotam's rule: they start on their own; each still waits
in review for his Accept). The model is routed as any task's (D218).

`nightly_crew` is `"shadow"` by default: the scorecard alone, every night,
so the baseline and the noise of each measure are known before a crew is
judged by them (D265). `"off"` stops it.
"""

from __future__ import annotations

import json
from pathlib import Path

import fe_board
import manager_nightly as nightly
import manager_store as store

NIGHT = 'crew_night'   # pm_settings: the day of the last crew night
HOUR = 6               # with no holding night tonight (the bench's), the crew is due from 06:00
LATEST = 10            # a night missed by 10:00 waits for the next one
MODES = ('shadow', 'on', 'off')


def mode(config):
    m = config.get('nightly_crew', 'shadow')
    return m if m in MODES else 'shadow'


def due(b, config, now):
    if mode(config) == 'off' or now.hour >= LATEST:
        return False
    day = now.date().isoformat()
    if store.setting(b, NIGHT) == day or nightly.held(b):
        return False
    return nightly.ran(b, day) or now.hour >= HOUR


def open_task(b, prefix):
    """A crew task of this kind still open: the next one waits for it."""
    return b.q1("SELECT id FROM tasks WHERE title LIKE ? AND status NOT IN ('done','dropped') ORDER BY id DESC",
                prefix + '%')


def night(b, config, dry_run=False, log=print):
    """Score, keep, post; with the crew on, write and hand over its two tasks. Run in its own process."""
    import fe_crew
    if config.get('repo'):
        fe_crew.point_at(Path(config['repo']))
    s = fe_crew.score()
    bodies = ((fe_crew.CODE, fe_crew.code_body(s)), (fe_crew.PROCESS, fe_crew.process_body(s)))
    if dry_run:
        log(fe_crew.summary(s))
        for prefix, body in bodies:
            log(f'\n==== {prefix}: {s["day"]}\n{body}')
        return s, []
    out = fe_board.board_dir() / 'manager' / 'crew'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{s["day"]}.json').write_text(json.dumps(s, default=str), encoding='utf-8')
    made, waiting = [], []
    if mode(config) == 'on':
        for prefix, body in bodies:
            if open_task(b, prefix):
                waiting.append(f'{prefix} T{open_task(b, prefix)[0]}')
                continue
            with b.tx():
                tid = b.add_task('manager', f'{prefix}: {s["day"]}', body=body, status='ready', priority=3)
            store.adopt(b, tid, proposed=False)
            made.append(tid)
    tail = ('\nStarted: ' + ', '.join(f'T{t}' for t in made) if made else '') + \
           ('\nStill open, so not started again: ' + ', '.join(waiting) if waiting else '') + \
           ('\n(Shadow: measured only, no crew tasks. `nightly_crew: "on"` starts them.)' if mode(config) == 'shadow' else '')
    with b.tx():
        store.notify(b, f'crew-night:{s["day"]}', fe_crew.summary(s)[:1990 - len(tail)] + tail)
        after_numbers(b, fe_crew.followups(s))
    return s, made


def landed_report(title, tid, repo, base, head, result):
    """D319: a landed crew task's report (fe_crew.report), or None for any other task. A report that
    fails says so in its place and never stops the landing."""
    if not title.startswith(('Code crew:', 'Process crew:')):
        return None
    import fe_crew
    try:
        commits = fe_crew.landed_commits(Path(repo), tid, base, head)
        return fe_crew.report(tid, title, result, commits,
                              {c['sha']: fe_crew.saved_review(c, Path(repo)) for c in commits})
    except Exception as e:  # noqa: BLE001 -- the landing goes on; the message says what is missing
        return {'full': f'(The crew report could not be built: {e!r}. The technical record follows.)', 'short': ''}


def after_numbers(b, followups):
    """D319: a crew task's first measured morning goes to its own thread, under the change it measures."""
    for tid, text in followups:
        key = f'crew-after:T{tid}'
        if b.q1('SELECT 1 FROM pm_outbox WHERE dedup=?', key) or not b.task(tid):
            continue
        b.add_message('manager', 'owner', f'T{tid}: the morning after', text, kind='fyi', topic=f'T{tid}')
        store.notify(b, key, f'**The morning after:** {text}', tid)
