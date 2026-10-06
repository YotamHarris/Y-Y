# Background project manager (S12)

> The manager was built for BodySimulation; the `Dnn` numbers below are that
> game's decision entries, where each rule came from. The studio's own entries
> are `Snn` (`python studio/fe_docs.py show S12`), each naming its origin.
> `<name>` is the game's `name` in its `studio.toml`.

The manager runs on your Windows PC and talks with you in one private Discord
channel. Give it a goal in normal language. It plans bounded tasks, runs up to
two Codex/Claude CLI sessions, independently reviews their evidence, and lands
passing commits through `fe_sync`. Each task has a Discord thread and board record.
Discord accepts the configured `owner_ids` allowlist, defaulting to `owner_id`.
Each new goal records its requester and everyone who writes or uses a control in
its planning or task threads. Goal and task pings mention those participants who
remain allowed. Older goals without a recorded requester, and manager-wide
alerts, retain the configured owner fallback (S36).

Each goal has a short name the planner gives it (D193), shown on its tasks' board
chips ("G3 · V165 improvements"), in its task threads' titles and on the Manager
tab; `fe_manager.py name G3 "V165 improvements"` renames it.

**A planned task is a proposal until you approve it (D195).** The manager takes
no step you have not approved. The planner proposes few, whole tasks (D196): each
is as much of the work as one agent can carry to the end, split only where one
part must land before another can start. Each comes with its reasoning: why it is
done, what should be true once it is, and how that would be seen. Each
one opens as an `idea` on the board and in its own Discord thread, which shows
what it fixes, the reasoning, and three buttons: **Approve** (it becomes `ready`
and starts when a checkout is free and its dependencies are done), **Edit
reasoning** (a form holding the current reasoning; what you save replaces it) and
**Drop**. A reply in a proposal's thread is added to its reasoning. Nothing is
implemented before you approve. The reasoning is the head of the task's body (its
`## Why` section), so the board is the same record: editing it there, moving the
task to `ready` or dropping it does the same. The worker reads the reasoning you
approved and plans its tests from it.

**Landing is not acceptance.** A landed task stays in `review`. Press **Accept**
in Discord or accept it on the board to unlock its dependent tasks. **Request
changes** opens a text form and resumes work. Unrelated tasks keep moving.
A task's commit on `origin/main` counts as landed however it got there (a run
recorded as failed after its push went through, a hand push): the manager moves
it to `review` and says so, and it can be accepted at once. Work not yet on
`origin/main` cannot be accepted; the refusal names the commit and its phase.

## Create the Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and choose **New Application**. Name it **<name> Manager** (the game's `name` in studio.toml).
2. On **Bot**, enable **Message Content Intent**. Natural messages in the project
   channel need this intent. Keep the token private; **Reset Token** creates one.
3. On **Installation**, enable **Guild Install**. Add the `bot` and
   `applications.commands` scopes. Grant **View Channels**, **Send Messages**,
   **Send Messages in Threads**, **Create Public Threads**, **Read Message History**,
   **Embed Links**, and **Attach Files**. Administrator is unnecessary.
4. Use the installation link to invite the bot to your server. In the private
   project channel, explicitly allow the bot to view and send messages and create
   threads. Task threads inherit that private channel's visibility.
5. Leave **Interactions Endpoint URL** empty. This implementation uses an outbound
   Gateway connection; no tunnel, public port, or web server is needed.

Discord's [bot creation guide](https://docs.discord.com/developers/quick-start/getting-started)
and [Gateway intent reference](https://discordpy.readthedocs.io/en/stable/intents.html)
describe the portal settings. Only the configured owner's numeric ID in the
configured server/channel or a manager-created task thread can issue instructions.

## Local setup and activation

Use PowerShell in the repository. Python 3.11 or later is required. The helper
creates a dedicated virtual environment outside Git. **Do not paste the token
into chat or pass it as a command-line argument**; `-Token` prompts with hidden input
and stores it in Windows Credential Manager under `<name>/DiscordBot`.

```powershell
./studio/setup-manager.ps1 -Guild YOUR_SERVER_ID -Channel YOUR_CHANNEL_ID -Owner YOUR_USER_ID -Token
```

Once the three IDs are saved locally (a second setup); use:

```powershell
./studio/setup-manager.ps1 -Token -Activate
```

Press Enter after this command, then wait for the **Discord bot token** prompt.
Paste the token only at that prompt and press Enter again; no characters appear
while entering it. `-Token` is a switch: never put the token after it on the
command line. If you already did, reset the token in the Discord portal first.

Checkouts may lag the manager's board schema (a leased one may not pull until it
is integrated), and neither `doctor` nor the service refuses because of it. A
newer board store refuses older
tools, so it is not migrated while any checkout that could open it still predates
the schema: the registered agents and, since it is not in the registry, the repo
the manager's own configuration names. Until then a newer tool applies the
migration's steps in place (they only add tables and defaulted columns, which an
older tool never reads) and keeps working, but leaves the version number alone,
so the older tool still opens the store; the first newer tool to open it once no
checkout is behind bumps it. (It used to refuse instead, which deadlocked the
manager: a leased checkout may not pull, so its worker's GPU wrapper stayed down
until integration, which needs the wrapper.) A future step that an older tool
could not ignore must not be applied in place.
Back up the board before that migration. The manager never takes over a dirty,
untracked, unpushed, occupied, or game-owning checkout. A1 remains the player's.

`-Activate` runs `doctor`, installs a Windows sign-in task, and starts the hidden
service. It checks both CLIs' subscription authentication, configuration, installed
dependencies, and checkout schema versions. It cannot verify Discord permissions
before connecting. Keep the PC awake, online, and signed in. After a reboot the
service starts at sign-in; no Codex or Claude window is needed.

Send `/status` in the private channel. Then give it a small first goal with clear
acceptance criteria, inspect the task thread, and accept the result from your phone
before handing it a larger backlog. The real Discord round trip requires the bot
and token and is a separate commissioning check from the offline test suite.

## Controls and everyday use

- `/status`: read at a glance, in four parts: **Working on** (each task, its
  model and checkout, how long it has worked over how many runs, the worker's
  latest message and its last step), **Needs you** (proposals to approve,
  questions, landed tasks to review), **Queued** (grouped by what they wait on),
  then **Stats** (each running task's tokens and time, and every managed task's
  totals per model). D210; `fe_manager.py status` prints the same as plain text
  (`--json` adds the raw tables). Above those parts sits **Allowance used** (D244): one
  line per provider with the percent of each usage window spent, when it resets
  and how old the reading is (`read 12m ago`). They are the account's numbers, so
  your own sessions count too. Claude's come from its stream, Codex's from its
  own logs, and a provider with no reading yet says so rather than 0%. Display
  only: nothing is held or re-routed on them.
- **Planning threads (D226):** write what you want in the project channel. The
  message opens a thread on itself, and the planner talks with you there as a
  Claude Code session in plan mode would: it reads the code, answers, asks (with
  option buttons) and proposes a plan, and each reply of yours resumes the same
  session. The plan message shows each task's title and Why (with its approach);
  **Approve plan** creates the tasks, ready, and they start. Reply instead to
  change it. The thread stays open: write there to plan more. While a planner
  or a worker runs, its thread carries one live message (how long, the model,
  its latest words and steps), removed when the run ends.
- **The talk channel (D317):** ask quick questions in `#meatbag-talk`, for
  example while you go through reviews: what a task changed, why, where its
  evidence is, what waits on what. A read-only Sonnet session answers there in
  a minute or so, even while the manager is paused. Each message continues the
  same conversation, and a fresh one starts after three hours of quiet. Nothing
  said there changes the work: a change still goes in the task's thread. The
  bot finds the channel by name when it connects; another name or ID goes in
  the config as `talk_channel` or `talk_channel_id`. Give the bot View Channel,
  Send Messages, Read Message History, Embed Links and Attach Files there.
- **`/ask` in a task's thread (D322):** `/ask question:` asks about that task
  and is answered in the thread the same way, by a conversation of that thread's
  own. The task is not reopened and its worker never sees the question. A plain
  reply in the thread still goes to the worker.
- **Questions (D224):** a worker that needs your call returns each question with
  two to four options, its recommendation first. Its message in the task's
  thread has a button per option and **Answer in my own words** (a form); the
  last answer resumes the worker with all of them. Typing a reply in the thread
  still works.
- **Your own tasks (D224):** the rules for writing and working a task are the
  task skill, `.agents/skills/task/SKILL.md`; the manager's prompts carry only
  the task and its Why. Write a task by it on the board, then work it yourself
  (`/task T50` in a Claude Code session) or hand it over:
  `fe_manager.py adopt T50` makes it the manager's, as if it had planned it.
- **Transcripts (D222):** every run card, the task drawer and the Manager tab's
  run list link to `#log/T43`, the task's whole transcript as a Claude Code
  session shows it: the manager's prompt, the worker's thinking (logged with
  `--thinking-display summarized`), its messages, each tool call with what came
  back, and the command that opens the session in Claude Code as a fork.
- `/pause`: active runs may finish (and land their tasks); no new workers start.
- `/resume`: allow dispatch again, and clear a provider's cool-off early (a
  login issue you have fixed; an allowance clears itself, D197).
- `/stop`: interrupt only managed process trees, keeping files and session IDs.
- **Accept**: accept the current landed commit, unlocking dependencies.
  For a delivered plan document (D279), it also approves implementing that
  document's recommendations: the manager converts it into tasks and creates
  them ready. There is no second approval for the same plan. Unresolved choices
  or material departures come back as questions; ordinary completed work keeps
  its usual acceptance behavior. Board and CLI acceptance work the same way,
  including after a service restart. Older workers' `fe_plan.py` PDF artifacts
  are recognized when their source document is named in the task. Historical
  accepted plans are recovered individually, so work already implemented by a
  separate goal is not duplicated.
  Compatibility is limited to tasks titled as authoring a plan or proposal;
  PDF tooling and formatting tasks do not approve the documents they use as
  examples. An explicit empty `plan_document` also disables this inference.
- **Request changes**: explain the changes in the task thread's form.
- **Pause**: available on manager update messages.

A task that is done or dropped on the board closes its thread (D192): one last
line, then the thread is archived. Setting the task back to work, or any new
message to it, opens it again.

The board's **Manager** tab opens on what the manager does (D210): *Now*, each
run with its task, checkout, time and last steps; *Waiting in the queue* and
*Waiting on you*, each with why (a busy slot and what holds it, a dependency, a
proposal or a landed task); and *Your recent messages* with what each became.
Below are goals, recent runs, errors, heartbeat, and pause/resume/stop
controls. The Agents view shows a managed run in its checkout's `doing` row. Offline controls change the desired mode; start the
service locally if it is not running. Development tooling has no game runtime
dependency and adds no player-facing menu item or engine version.

```powershell
$managerPython = "$env:LOCALAPPDATA/<name>/board/manager/venv/Scripts/python.exe"
& $managerPython studio/fe_manager.py doctor
& $managerPython studio/fe_manager.py status
& $managerPython studio/fe_manager.py pause
& $managerPython studio/fe_manager.py resume
& $managerPython studio/fe_manager.py stop
```

Every task message has one shape (D187): **What we are fixing** (the task's
problem in plain words, the same in every message of its thread), **Where it
stands**, **Your call** (short questions, or "nothing for now") and **More** (the
full report attached as `T<n>-report.txt`, the board task, the commit, and the
CLI session with its checkout). Routine task starts, reviews and retries the
manager makes by itself post quietly. Decisions, a task stopped after its second
failure, acceptance requests, and completed goals mention you. There is no daily digest.
Images are uploaded from the task checkout to durable board storage and Discord;
images beyond Discord's upload limit are identified in the message. Phone review
does not rely on localhost links.

## How execution is coordinated

The board database holds goals, scoped owner authorization, task phases, exact CLI
session IDs, resource leases, an input deduplication table, and an outgoing queue.
`manager` is its own board identity. Model results never become owner actions;
only the authenticated transport or a local owner command supplies those.

The supervisor polls local state cheaply, wakes immediately for incoming messages
and completed workers, and reconciles at least once per minute. Idle heartbeats
do not call a model. At most two worker runs are active, A2's and A3's, whichever
providers are left (`store.RUNS`, D212: with Codex spent both run on Opus).
Planners have their own lane (D216): they only read and hold no checkout, so they
take no run slot and a goal is planned while both workers run, up to
`store.PLANNERS` (two) at once. A planner reads a worktree of origin/main beside the
releases (`release.reading`), never a checkout an agent is changing. Every role
falls back to the other provider when the preferred one is missing or waiting out a
block, planning included, and an allowance failure is failed over in silence (D197).
There is no separate reviewer to place on a provider: a task's worker reviews its own
diff (D188).

**Which model runs a task (D218, D238, D240).** Opus is not the default. At dispatch
`manager_models.choose` picks Sonnet, Opus or Sol (each a class that runs its latest model,
D300) for each task: the owner's pin first (`fe_manager.py model T12 sonnet|opus|sol|auto`, or
the task drawer's model select), then the strong model a task was escalated to,
then the planner's suggestion (each planned task carries `model` and
`model_reason`), checked against the record, else the record's own pick. With no
record that is Sonnet for work off the core (UI, tools, docs, content, gameplay
glue), Sol for rendering and look work where shots decide, and Opus for
the rest of the core (performance, physics, refactors). The record is per kind of
work: a task counts for the model it was first tried on, landed for it, escalated
or failed against it. From three finished tries, Sonnet takes off-core work the
planner wanted on a strong model when it has landed 75% there; a model whose own
work lands under half is replaced by the other strong model when that one has the
known, better record; and when both strong models have three tries in a
category the better record is the category's strong model (a tie goes to its
home: Sol for rendering, Opus otherwise). A Sonnet run that fails on the work,
stops unfinished twice in a row, or runs out of its hours is handed to that strong
model, once, at no attempt's cost: the same session on Claude, a fresh one with
the feedback on the Codex CLI. `fe_manager.py models` prints the three models and,
per kind of work, the first model, the record per model and the hand-over model.
The planner is told the record and what each model is for, and stays on Opus
(`fe_manager.py model planner sonnet|opus`). `config.json`'s `"models": {"sonnet":
..., "opus": ..., "sol": ...}` pins a tier to an exact id; without it Claude tiers are the newest CLI's aliases and Sol the
newest `gpt-N-sol` the Codex CLI lists (D300).

**A tier carries its CLI (D238).** A tier is a model and the CLI that runs it
(`manager_models.PROVIDER`), not a model id alone. Beside the two Claude tiers
there is `sol`: the ChatGPT Sol class, today `gpt-6.1-sol`, run on the Codex
CLI with `codex exec -m <the newest gpt-N-sol>`. The routing policy picks it for rendering
(D240) and the planner may suggest it; the owner can still pin it, in the task
drawer's model select or `fe_manager.py model T12 sol`. A task pinned to a tier
whose CLI is waiting out an allowance or an authentication block is not
dispatched at all — it waits for that CLI, and the Manager tab, the board's
Queued list, `fe_manager.py status` and `/status` say "pinned to Sol,
which waits: ...". A tier the policy chose still falls back to the other CLI, at
that CLI's own default model. A run that changes CLI cannot resume the previous
session (a Claude session id is not a Codex one): it starts fresh with the
task's feedback and is told in its prompt that none of the conversation came
with it. The planner stays on a Claude tier; `model planner sol` is refused.

**What every run spends (D218).** `pm_run_stats` meters each run as its stream
arrives: the model, tokens per model (in, out, cache read and write), turns, and
time (queued, the model's, the tools', the GPU wrapper's wait for the GPU and its
run, a frozen game's wait in `fe.py`). A task's working time is its runs' time
less those waits. Runs from before D218 are read back from their logs a few a
tick. The board shows each managed task's model chip, its spend in the drawer
and on its run, and the Manager tab's **Models** section (per model, per kind of
work, and what would go faster); `fe_manager.py models`, Discord `/models` and
`/status` say the same.

A task has one role, its worker (D188): it implements the task, reviews its own
diff, and lands it with `fe_sync.py push` (rebase onto origin/main, compile gate,
push), resolving and revalidating any conflict, since it has the context to. There
is no separate reviewer or integrator. Workers return schema-checked results with
per-category acceptance evidence. The controller lands a task only when the result
is complete, the tree clean, the evidence passes for HEAD, and HEAD is on
origin/main; anything short of that resumes the same session with what is missing
and costs no attempt (attempts count crashes and invalid results). The landed task
goes to the owner in the board's review column. A managed Claude Stop hook never
publishes, so a worker lands only when it decides the task is finished; ordinary
interactive sync keeps its existing behavior. These workflow checks are not a
security sandbox for malicious local code running as your Windows user.

The board is what holds a checkout (D191). A managed task holds its agent's
checkout while the board has it claimed, in progress or blocked (waiting on the owner,
its work kept there for his answer, until another task needs the checkout: then
its commits are parked with one extra WIP commit for any uncommitted changes, pushed to a branch on
origin, `fe/parked/T<n>`, a normal branch he can open and read, and restored from
there as they were when it runs again, in whatever checkout is free next, even one
that never held the work; restore undoes the WIP commit to return modified and
untracked files on the task's own base, and the branch stays until he accepts or drops the task,
D297, D314), or while a worker of it still runs; nothing
else does, whatever the manager's phase or a lease row says. the owner's edit moves the
hold at once: dropping a task, closing it or setting it back to ready or idea frees
the checkout and ends its worker; setting a failed or blocked task in progress
resumes it, as a reply does; the manager starts only tasks the board has ready or
in progress. `fe_board.checkout_holds` is the one answer: the router, the sync
hooks, the supervisor's leases (which mirror it and are removed when it drops them),
`fe_board.py who` and the Agents view's **held by** and **new session** rows all read
it. The Agents view's **new session** row is the router's own verdict on each
checkout.

When the game has a GPU lease (`[adapters] gpu` in studio.toml; BodySimulation's
is `engine/tools/fe_gpu.py`, D200), the GPU command wrapper holds nothing for
shots, replays and a game a worker debugs in: they share the GPU, waiting only
while a bench or the owner's game claims it. A command that benches takes
`--bench` (or names `--bench`): it claims the GPU through the adapter, which (in
BodySimulation) freezes every other game with a control channel (`freeze`, a
lease renewed every 10 s), runs, and thaws them. A game that cannot be frozen
gets its owner a board message to stash it. It never quits another instance.
With no adapter the wrapper just runs the command:

```text
python studio/fe_manager.py gpu [--bench] -- COMMAND ARGUMENTS
```

Managed prompts require every GPU command to use the wrapper. Claude's command
hook enforces it; Codex follows the same project instruction. Ordinary Claude
launches also wait out a live claim. The adapter's own CLI shows who has the GPU
(BodySimulation: `python engine/tools/fe_gpu.py status`).

**Three hours a task (D201).** A task has three hours of worker time since the owner
last approved or answered it. At three hours a Claude worker is told on its next
tool call to stop and return blocked with where it got to, what took this long
and what is left (the prompt says so for Codex). Twenty minutes later the
supervisor ends a run that has not returned, reports its last steps from the
run's log, and blocks the task on the owner; his reply gives it another three hours.

Child wrappers survive supervisor restarts. PID, process creation time, and run
ID prevent adopting/killing an unrelated process. A Windows Job Object kills a
wrapper's descendants if it dies. A Claude worker's result ends its run: if
`claude -p` has not exited a minute later (a background `tail -f` monitor of its
session keeps it alive), the wrapper ends its process tree and records the
result. Workspaces remain reserved through recovery
or questions. Two failed recovery attempts block the task and notify you.
A worker blocks only on a decision that is yours. One that stops unfinished
with nothing to decide (a headless turn ended under a running GPU suite)
returns `continue`: once every process it started has exited, the manager
resumes the same session without a message, up to six times in a row before
the task blocks and asks you to look.
An exhausted allowance is not one of them (D197): it costs no attempt, sends no
Discord message and never counts towards a goal's planning errors. It pauses
that provider until it can serve again, records a board event (the Manager tab
and `fe_manager.py status` both show `codex waiting: allowance, retrying at
14:32`), and the task is dispatched again at once on the other provider with a
short note that the work is unchanged; the raw CLI output stays in the run's
record.

How long that pause lasts is what the provider itself said, where it says it:
`try again in 3h 20m`, `try again in 45 minutes`, `resets at 14:00`, `limit
resets 2026-10-01T09:00Z`, and the form ChatGPT actually sent twice, `try again
at Oct 5th, 2026 10:12 AM`. Only a deadline cued as one is read, so the
timestamps a CLI dump is full of are not mistaken for it, and the wait is held
between a quarter of an hour and a day: a weekly limit still gets a daily probe
and a misread hint can never park a provider for a week. Where the text names
no deadline, each consecutive block of one provider waits longer than the last
- 30 minutes, 2 hours, 6 hours, then a day, held there - instead of retrying
every half hour and burning a run each time to learn the limit is still spent.
A run that provider carries through clears the block and the count, and so does
`/resume`. The wait and the count are in the store's `pm_providers` row
(board schema 3). Only the provider
CLI's own exit text is read that way: a rejection the manager writes quotes
the model's prose, which never pauses a provider however it words itself.
The silence is bounded too — after four allowances in a row on one task or
goal the ordinary noisy path resumes, so an allowance that never clears still
reaches you. With every provider waiting out a block nothing is dispatched and
the supervisor idles, picking the work up again on the first cheap pass after a
deadline passes; it does not ask you, because there is nothing to decide. The
Manager tab and `fe_manager.py status` are where that state is visible, since no
message announces it. Authentication and permission failures do require your
attention and keep their notice. No API-key fallback is configured.

Discord reconnects reconcile channel history, deduplicate owner events, and retry
outgoing messages. A stable footer marker reconciles a send acknowledged by Discord
just before a local crash. Deleted Discord history cannot reconstruct a missing
message; the board remains the durable record.

## Files, recovery, and checks

Local state is `%LOCALAPPDATA%/<name>/board/manager/`: `config.json`,
`manager.log`, `service.log`, `runs/<id>/events.jsonl`, run results, `releases/`
and `release.json`. No token is stored in these files. The `<name>
Project Manager` Windows scheduled task uses the current user's interactive login;
it runs at sign-in and every minute after, and each run only makes sure a service
is up, so a crashed service is back within a minute.

Preserve unfinished worker checkouts. To disable sign-in startup, disable that
task in Windows Task Scheduler; `/stop` leaves the Discord connection alive so it
can receive `/resume`.

## Updating the manager (D189)

The service never runs a checkout's files. It runs a **release**: a detached
worktree of the manager's repo at an origin/main commit, under `releases/`, and
every worker and GPU wrapper it starts runs from that same release. Editing the
manager in a checkout changes nothing that runs; **pushing** it does:

1. About once a minute the service fetches origin/main. When a pushed commit
   changes a file the manager runs (`fe_manager.py`, `fe_board.py`, `fe_sync.py`,
   `fe_codex.py`, `fe_jev.py`, any `manager_*.py`, `manager-requirements.txt`),
   it checks that commit out as a new release and starts it as a candidate
   beside itself. Other pushes are ignored.
2. The candidate loads every manager module and runs `doctor` (sign-in aside),
   then says it is ready and waits for the service lock.
3. The service lets go between two steps and exits; the candidate takes the
   lock, and once its first step goes through it is the release a sign-in boot
   starts (`release.json`) and posts a quiet "Manager updated to ..." line.
4. A candidate that fails its check, is not ready within three minutes, or dies
   before its first step is rejected: the old version keeps serving (or the next
   boot brings it back), you are told once, and that code is not tried again
   until a later push changes it or `fe_manager.py upgrade` asks.

Workers already running keep their own release until they finish; a release is
pruned only when no process runs from it, and the three newest are kept. A new
version must still read what the previous one wrote: results a previous
release's worker returns, and board rows (D187's results validate without their
new fields for this reason). `serve` from a checkout is only the boot; the
supervisor is `serve` from a release. With no service up (after sign-in, or a
crash) a boot starts the newest pushed manager directly, unless its code was
rejected, and falls back to the settled release; a service that dies before its
first step is rejected at the next boot (D190). A setup problem (`doctor`: a CLI
signed out or missing) rejects nothing: it is posted once and retried every
minute. A CLI that updates itself into a new versioned folder, as Codex does, is
found in the newest one.

```powershell
& $managerPython studio/fe_manager.py release   # serving, settled, handoff, rejected
& $managerPython studio/fe_manager.py upgrade   # check origin/main now; retry a rejected one
& $managerPython studio/fe_manager.py install   # the scheduled task with its one-minute boot
```

```powershell
python studio/fe_manager.py test   # S35: the venv, stderr folded, one summary line
python studio/fe_board.py selftest
python studio/fe_docs.py check
# Opt-in, uses the signed-in CLI allowances in an empty temporary repo:
& $managerPython studio/test_manager_live.py
# Check shell identity forwarding on both new and resumed sessions:
& $managerPython studio/test_manager_live.py --environment-only
```

The offline tests cover schema migration, idempotent input/plans, claims and
leases, recovery, allowance fallback, permissions, exact session resume,
a worker landing its own task onto a moved trunk, unlanded results resuming,
stale evidence, acceptance dependencies, GPU contention,
Discord authorization/retry and a simulated full task lifecycle. The live CLI
test checks structured output, exact-session resume, and unattended file edits and
local commits for both installed CLIs. Codex workers use automatic approval review
with a workspace sandbox; planners are read-only and read the web (D256: Claude's
WebSearch and WebFetch, Codex's `--search`). Claude workers use auto mode
with interactive permission prompts disabled. Denied operations become blockers.
The environment probe verifies that shell commands receive the current run ID and
board directory, including a new run ID when the same AI session resumes. Codex
receives these values through explicit shell environment settings as well as the
CLI process environment.
It does not claim live Discord or real engine acceptance coverage.
