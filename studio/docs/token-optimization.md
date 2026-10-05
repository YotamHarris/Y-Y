# Token optimization — the running log

What we have done about what the agents' tokens cost, what each night
measured, and what is next. **Read this before working on token cost; add to
it when you change or measure something.** The rules themselves are decision
entries (`python engine/tools/fe_docs.py category manager` and `process`); this
file is the thread through them. Newest facts go at the bottom of each
section; "Where it stands" is rewritten in place.

## How the cost works

A model turn re-reads the whole conversation (cached, but paid), so **a run
costs about its turns times its context**. Output is a small part: the night
of 2026-10-01 read 535M cached tokens for 2.4M written. Two things follow:

- **Every tool call is a turn at full context.** A check, a wait, a poll or a
  push the model makes itself costs the whole conversation once more.
- **Everything a turn reads stays in the context** and is paid again on every
  later turn: a 15 KB file read early in a 60-turn session is read 60 times.

So the levers are: start smaller (base context, the brief), grow slower (read
items, not files; one line per result), take fewer turns (one call for many
steps, the process does what needs no judgment), and end sessions before they
are huge (the session cap).

Waiting itself is free: while a background command runs no turn happens, and a
manager worker's cache lives an hour (every cache write in its transcripts is
`ephemeral_1h`), so a wait under an hour does not even rebuild the cache.

## Where it stands (2026-10-02)

- Landed: D252 (one instruction file, the feature reference by topic), D253
  (fe_usage, the brief, fe_land, the hook's warnings, `gpu --batch`, the 200k
  cap, the meter fix), D257 (the manager lands a finished worker; fe_land takes
  its own regress baseline), D258 (the decisions by category).
- **Not yet measured:** no night has run since. The next `fe_usage.py night`
  (or the manager's morning post) is the first comparison against the
  baseline below.
- The manager hands itself over to pushed code (D189); check it serves the
  version with D257 with `python engine/tools/fe_manager.py release`.

## Measurements

`python engine/tools/fe_usage.py night [--since T --until T]`; the manager posts
its summary after 10:00 each day. One row per night, never edited.

| night | spend | runs / tasks | cache read | output | base context | calls before first edit | polling and waiting | notes |
|---|---|---|---|---|---|---|---|---|
| 2026-10-01 18:00 → 10-02 10:00 | $414 | 44 / 16 | 535M | 2.4M | ~82k a worker turn | median 22 | ~273 min | baseline, before D252–D258. T60 $85, T58 $47, T48 $42 |

**The baseline night in detail** (from the transcripts and `pm_run_stats`):

- **Base context:** 63–94k tokens on a worker's first turn; CLAUDE.md alone
  ~21k tokens, its "Iterating on a running instance" section ~55 KB of it.
- **Growth:** sessions reached 400–650k. Resumed sessions started large: T47 at
  344k, T48 at 170k.
- **Reading:** ranged and whole-file Reads, `sed -n` (134) and `cat` (112) kept
  ~85M tokens in context; `main.rs` was read 97 times in 12 sessions.
- **The opening:** 22 calls before the first edit; 14 of 19 workers read the
  whole task skill, 11 ran `fe_docs show`, 13 `cd`, 12 `ls`.
- **The close, run by hand:** build 65×, test 41×, regress 36×, push 21×,
  bench 46×. In the five costliest sessions the gates, waits and pushes alone
  re-read ~120M tokens, a quarter of the night:

  | session | gate/wait calls | minutes | re-read | what happened |
  |---|---|---|---|---|
  | T47 | 106 | 100 | ~51M | resumed at 343k; 33 background launches each checked by hand; five build+test runs at 600k+ |
  | T48 | 70 | 97 | ~28M | landed, then a decision-number conflict made it land again; three background waits of 4–10 min |
  | T57 | 66 | 54 | ~20M | started a bench in the background and waited on it, round after round, at 370–447k |
  | T58 | 52 | 41 | ~16M | stash/build/pop for a regress baseline; `seq … sleep` loops of 8 min; wrote three scripts to run the bench |
  | T51 | 47 | 38 | ~6M | read `fe_bench.py`'s source to learn how to bench; wrote its own acceptance drivers |

- **GPU launches:** 118 separate `fe_manager.py gpu` calls.
- **Meter:** three runs were recorded as model `<synthetic>` (a placeholder
  message of Claude Code's), $85 of them; fixed in D253 and backfilled.

## Changes, in order

| date | change | decision | commit | aimed at |
|---|---|---|---|---|
| 2026-10-02 | `fe_index show` matches a heading by any part of it | — | 0dd5d4d | reading one topic of the reference |
| 2026-10-02 | AGENTS.md is the one rule file, CLAUDE.md imports it; the feature reference moved to `docs/engine-reference.md`, read by topic; skills generated from `.agents/skills` | D252 | 45a221e | base context (~11k tokens less a turn) |
| 2026-10-02 | `fe_usage.py night`, and the manager's morning post; the meter ignores `<synthetic>` | D253 | 6660b88 | measuring all of this |
| 2026-10-02 | The brief: the manager puts the cited entries and where the named code lives in a new worker's first prompt; a session past 200k starts fresh with a note | D253 | 384193d | the opening ritual; resumed giants |
| 2026-10-02 | `fe_land.py`: every closing gate and the push in one blocking call, one line per gate | D253 | 1caf763 | the hand-run close |
| 2026-10-02 | The shell hook warns (never blocks) on `sleep` loops and on windows of source files | D253 | 0824292 | polling; whole-file reads |
| 2026-10-02 | `fe_manager.py gpu --batch FILE`: many launches under one claim, in one call | D253 | be10c3e, 7c8c500 | 118 GPU calls |
| 2026-10-02 | The manager runs `fe_land.py` when a worker returns complete: no model turn waits on the gates; a failed gate resumes the session with its lines | D257 | 00e4151, 87f03f4 | the close's last waiting turn |
| 2026-10-02 | `fe_land.py --refactor` takes the regress baseline of the commits' base itself | D257 | 00e4151 | T58's stash dance |
| 2026-10-02 | The decisions split by category; `fe_docs.py category NAME` prints one area's timeline, `new` appends the next entry | D258 | cb96bc6 | reading one area instead of the whole summary; finding the next number |
| 2026-10-02 | A worker compacts at each phase boundary (read, built, measured, committed): it returns `compact`, its wrapper sends `/compact` with what to keep and resumes it; AGENTS.md's "Compact instructions" say what a summary keeps | D259 | ddb9e46 | T62: one run grew 47k → 328k, 26M of its 32M input past 150k |
| 2026-10-02 | A land shows on the board as it runs: fe_land streams each gate's output and names the running gate in `land-<agent>.json`; the run's step reads `landing: regress (2 of 4) for 5 min; last: ...` | D257 | 423a75a | T62 sat 50 min in a land the board showed as the worker's last step |
| 2026-10-02 | No commit waits on a bench: push and land bench nothing, workers bench only a task whose point is its cost; the manager benches main at 04:00, holding the work, bisects a slowdown and files the commits as one task | D262 | (this change) | workers' benches: T60 30 claims and 70.6 min, T43 54; 3.9 h of bench GPU and as long waiting in two days |
| 2026-10-04 | Read-only runs (planner, conversation) may run `fe_index`, `fe_docs show/category/status/queue`, `fe_board inbox/task show/task list/who` and `fe_manager status/who` in both shells; `dontAsk` had refused them | D294 | (this change) | process crew T94: `sh: inline python` / `fe_index` / `fe_docs` / `fe_board` "denied in don't ask mode", 17 in 14 sessions a week, 4.8M tokens; should fall to near zero |
| 2026-10-05 | `fe_gpu.run_hidden` captures a GPU command's stdout/stderr through a pipe instead of trusting Windows to hand it the caller's inherited console handle, which it does not from a service or a git-bash shell; `share`/`bench`/`fe_video.gpu_launch` use it | D315 | (this change) | process crew T118: `workaround` `run.py`/`pairs.py`/`p20.py`, 7 sessions a week writing their own "redirect to a log" wrapper around `fe_manager.py gpu`'s silence, 4.17M tokens; should fall to near zero |

## Next, not done yet

- **Acceptance drivers.** T51 and T57 each wrote their own `click.py`,
  `chan.py`, `run.py`: the next repeated pattern a tool could absorb.
- **A screenshot checker.** Images stay in the main context for good; a small
  subagent could read the acceptance shots and return one line each.
- **AGENTS.md is 39 KB** against a 30 KB target.
- **The brief misses enum variants** (`Tool::Knife` is not indexed).
- **`fe_usage` reads only Claude transcripts**; Codex runs outside the manager
  are not counted (managed Codex runs are, through the meter).
- **Measure D262:** the first nights' bench time and pauses, the findings, and the GPU and bench turns the workers no longer spend.
- **Measure D259:** how many runs compact, the context before and after, and whether a resumed phase redoes work it lost in the summary.
- **`decisions-summary.md` grew to 45 KB** with its category sections; for one
  area, `fe_docs.py category NAME` (~1 KB) instead of the whole file.

## Tried and dropped

- **"Resumed runs are under-metered."** A first reading said T47's transcript
  held 136M cache read against 40M metered. It was the window: T47's first run
  started before 18:00. Nothing was wrong with the meter there.
- **Blocking flags on every tool that waits.** Planned for D253 and not built:
  the tools already block. The polling came from agents backgrounding a
  blocking call and then polling it, which the hook's warning addresses.
