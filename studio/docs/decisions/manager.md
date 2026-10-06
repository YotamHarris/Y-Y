# Decisions — Manager

> The background manager: planning, workers, models, landing, Discord. One category of the studio's decision log: [decisions.md](../decisions.md) says how the log works, and [decisions-summary.md](../decisions-summary.md) is every category, one line per entry.
> `python studio/fe_docs.py category manager` prints this file's timeline, `python studio/fe_docs.py show S12` one entry, and `python studio/fe_docs.py new manager "Title"` appends the next.

Append-only. A later entry may reverse an earlier one; the **Status:** line under each heading says which holds.

---

### S12 — A local manager dispatches owner-approved goals and reaches the owner through Discord (2026-10-05)

**Status:** active

**Origin:** BodySimulation D181, D188, D191

**Decision.** `python studio/fe_manager.py` is a hidden, continuously running
supervisor (a sign-in task with a restart policy) that works the board through
the agents' CLIs on their existing subscription logins. It leases the worker
checkouts; the owner's own checkout is never leased. An owner-approved goal
authorises it to create and claim that goal's tasks; it has its own identity
on the board, and model output never impersonates the owner. Only the
configured owner's messages in the configured Discord server and channel, or
explicit local owner controls, are owner actions. New direction, fiction,
spending limits and acceptance standards still need the owner. A landed task
waits in review until the owner accepts it, and its dependents wait with it;
only the owner sets `done`. The bot token lives in the OS credential store,
never in git or a prompt. Idle checks make no model calls; input and run
completion wake it at once. Routine updates are quiet; decisions, failures,
acceptance requests and completed goals ping the owner. Setup is in the
studio's `docs/background-manager.md`.

**Why.** The owner wanted to stop opening sessions by hand and direct the
project from a phone, while keeping the one gate that matters: nothing he has
not accepted becomes a base for further work. The manager is development
infrastructure: no game runtime depends on it.

---

### S13 — A manager message leads with the problem, asks the bottom line, and attaches the rest, images included (2026-10-05)

**Status:** active

**Origin:** BodySimulation D187, D304, D284

**Decision.** Every message the manager posts about a task or goal has one
shape: **What we are fixing** (the task's problem in plain words, fixed by its
first statement so every message in a thread leads with the same sentence),
**Where it stands** (a sentence or two), **Your call** (short questions,
answerable in a word, with the recommended option, or "nothing for now"), and
**More** (the full report attached as a file, the board task, the commit, the
CLI session and its checkout). A mention means it is the owner's call: a retry
the manager makes by itself says so quietly. The rule for images lives in the
outbox every message passes through, not at each call site: every image a run
names in its artifacts, or a board message marks, is attached; one that cannot
go is named with the reason, never dropped silently; attachments past the
transport's limit go in follow-up messages.

**Why.** The owner reads on a phone. Pasting a worker's technical report into
the thread told him nothing he could act on. Three times he asked where the
pictures were and got text, because each call site had to remember to attach
them and the next one forgot; a rule placed where every message passes holds
for messages not written yet.

---

### S14 — Only pushed code runs the manager, from a release that hands over to the next (2026-10-05)

**Status:** active

**Origin:** BodySimulation D189, D190

**Decision.** The service, every worker wrapper and every launch wrapper run
from a release: a detached worktree of an `origin/main` commit, never a
checkout's files. The service fetches once a minute; a commit that changes the
manager's code (a fixed list of files, keyed blob by blob) becomes a candidate
that imports every module and runs `doctor`, waits for the service lock, takes
over between two steps and posts a quiet notice. A candidate that fails its
check, is not ready in time, or dies before its first step is rejected by its
code key: the old version keeps serving, the owner is told once, and the key
is not retried until a push changes it. A boot with nobody serving starts the
newest pushed manager unless its code was rejected. A setup problem (a CLI
signed out) rejects no code. A CLI that updated itself into a new versioned
folder is found again. `python studio/fe_manager.py release` says which
version serves. To change the manager, push.

**Why.** A service that loaded its modules once while edits sat uncommitted in
its checkout validated with one schema while its new workers wrote another,
and every run of the evening failed. Running only pushed code, and handing over
without a gap, makes that state impossible rather than something to remember.

---

### S15 — The board is what holds a checkout, and the owner's edit to a task moves it (2026-10-05)

**Status:** active

**Origin:** BodySimulation D191, D214

**Decision.** One function answers what holds a checkout: a managed task holds
its agent's checkout while the board has it `claimed`, `in_progress` or
`blocked`, or while a worker of it is queued or running; nothing else does. The
router, the sync hooks, the scheduler, `fe_board.py who` and the Agents view
all ask it, and leases mirror it. The owner's edit moves it: setting a task to
`dropped`, `done`, `ready` or `idea` ends its workers and frees the checkout in
the same transaction; `ready` or `idea` also resets its attempts; a failed or
blocked task set back to work resumes. The scheduler starts only tasks the
board has ready, claimed or in progress, so a task the owner moves stays where
he put it. The board says what runs: a task the manager holds with no worker
running reads `claimed`, and `in_progress` once its worker is queued. A run's
session ends with its process, so a killed worker frees its checkout at once.

**Why.** A lease the supervisor renewed for any unfinished phase kept failed
tasks' checkouts for good, invisibly: the owner had dropped the tasks, and
nothing he could see or edit explained why every new session was routed to
the same checkout. The truth of what holds a checkout belongs where he can see
it and change it.

---

### S16 — A blocked task's work is parked on a branch on origin so its checkout runs another task (2026-10-05)

**Status:** active

**Origin:** BodySimulation D297, D314

**Decision.** When a ready task finds no free checkout, the task blocked
longest with no worker is parked: its uncommitted files become one WIP commit
on top of its own commits, pushed (forced) to `fe/parked/T<n>` on origin, and
the checkout is reset to the trunk. It is never parked while a session's turn
runs there, while the game runs from it, during a rebase, or off the trunk
branch; any failure before the push leaves the tree, the index and HEAD
exactly as found. When the owner answers, the task runs like any other: the
branch is fetched from origin into whichever checkout is free, the WIP commit
is undone so its files read as modified again on the task's own base, and the
worker is told where its work came from (its session resumes only in its own
checkout). The branch is kept until the owner accepts or drops the task, then
deleted. The messages name the branch's URL.

**Why.** One unanswered question otherwise left a run slot empty for as long as
it went unanswered. Parking only on demand keeps a quick answer in place with
its session. A branch on origin is something the owner can open and read, and
a restore never depends on the clone that parked it surviving. The work is not
rebased at restore, which could conflict when he answers; the worker's own
push rebases it.

---

### S17 — A goal has a name, and finished work closes its thread (2026-10-05)

**Status:** active

**Origin:** BodySimulation D193, D192, D296

**Decision.** The planner names every goal it reads, in two to five plain
words, and the name is kept once set (`python studio/fe_manager.py name G3
"NAME"` renames it). The name shows wherever the goal's tasks do: board chips,
the Manager tab, thread titles (`T35 | NAME: title`) and the worker's prompt.
When a task becomes `done` or `dropped`, the manager posts one last line in
its thread and archives it; when a goal is complete (every task done or
dropped), its planning thread is closed the same way. Not closed: a task in
review (its controls live in the thread) and a conversation the owner may
continue. A message in an archived thread reopens it, and work reopened may be
closed again.

**Why.** "Goal G3" told the owner nothing about what a task belonged to. Every
finished task and goal otherwise stays in the channel's thread list; the list
should hold only what is still going.

---

### S18 — Planning is a conversation that ends in one approval (2026-10-05)

**Status:** active

**Origin:** BodySimulation D195, D226, D279

**Decision.** A message in the project channel opens a goal and a thread on
itself. The planner talks first and plans second, as a session in plan mode
does: each reply of the owner's resumes the same planner session with only his
new words. A turn returns either a reply (with questions as option buttons) or
a plan. A plan lists each task's title, model and Why; **Approve plan** creates
its tasks ready, and they start when a slot and a checkout are free. A newer
plan supersedes an older one, and an outdated button is refused. Nothing is
implemented before approval; a task handed over with `python studio/fe_manager.py
adopt T50` is a proposal too. The owner's words are his: they are quoted
exactly, a reading is marked as the planner's, and words that fit more than one
design ("explore", "can that be done?") become a question or an exploration
task. When the owner accepts a task that delivered a plan document, a planner
turns that document into few, whole implementation tasks, created ready, once;
unresolved choices come back as questions.

**Why.** A one-shot planner took a message and ran with it, and once read an
exploration as a request for one particular design. Planning in conversation
puts the ambiguity in front of the owner before any work is spent; accepting a
plan should advance its work, not end the workflow at the document.

---

### S19 — Smaller steps means a less agentic manager, not smaller tasks (2026-10-05)

**Status:** active

**Origin:** BodySimulation D196

**Decision.** The planner proposes few, whole tasks: each is as much of the
work as one agent can carry to the end, split only where one part must land
before another can start (usually one to three, never more than 20). Each task
carries its reasoning, the `## Why`: why it is done, what should be true once
it is, how that would be seen. The manager takes no step the owner has not
approved (S18).

**Why.** The owner asked for "smaller steps" and the planner first read it as
smaller tasks. He meant the manager's steps: it should act less on its own and
wait for him. Splitting work into small tasks multiplies handoffs, checkouts and
acceptances; the context lives with the agent that carries the whole task.

---

### S20 — Planners and quick answers have their own lane, read the trunk and the web, and write nothing (2026-10-05)

**Status:** active

**Origin:** BodySimulation D216, D256, D294

**Decision.** Read-only runs (the planner, a conversation turn, a quick answer,
S30) take no worker run slot and no checkout; they are capped by their own
count so a burst of messages cannot start ten strong-model runs. A read-only
run works in a worktree of `origin/main` beside the releases, never in a
checkout an agent may be changing. It has the reading tools, the lookup
allowlist (S7) and the web (search and fetch; on Codex, the CLI's own web
search, which leaves the read-only sandbox as it is), and nothing that writes.

**Why.** A goal sat "waiting for a slot" behind two workers that could each run
for hours; planning should always be able to run, as the manager does. A
planner reading a checkout read someone's uncommitted edits. The web was never
left out on purpose: it fell out with the writes, and planning without it
cannot check a library or a technique.

---

### S21 — The rules for a task are the task skill, not the manager's prompt (2026-10-05)

**Status:** active

**Origin:** BodySimulation D224

**Decision.** The task skill (`.agents/skills/task/SKILL.md`) is the one set of
rules for writing a task (a title, a `## Why` that stands alone, a `## What to
do` with only what is specific to the task, its model), working it, asking,
reporting, and what changes under the manager. The manager's planner and
workers follow it, and so does the owner's own session (`/task T50`), so the
two can be compared. A worker's prompt is the work: a pointer to the skill,
the task with its Why first, where it runs, then the owner's replies. Each
question a worker asks is `{question, options}`, two to four options with the
recommended first; Discord shows a row of buttons per question and an "answer
in my own words" form, and the last answer resumes the worker. To change how
tasks are done, edit the skill.

**Why.** Five thousand characters of rules in every worker's prompt duplicated
the instruction file in other words, and the rules for writing a task existed
only in the planner's prompt. Rules in one Markdown file can be read, edited
and followed by hand; questions with options can be answered from a phone.

---

### S22 — The worker reviews its own work, the manager lands it, and nobody waits on the gates in a turn (2026-10-05)

**Status:** active

**Origin:** BodySimulation D188, D253, D257, D262

**Decision.** A managed task has one role, the worker: it implements the task,
reviews its own diff as a sceptical reviewer would, commits, and returns
`complete`. It never runs the close. Its wrapper then runs the game's landing
(`python studio/fe_land.py`, with the gates `studio.toml` declares: build,
tests, regression, docs, index), then the push; the manager lands the task
(review column, message to the owner) only when the result is complete, the
tree clean, the evidence passes for HEAD and HEAD is on `origin/main`. A gate
that fails resumes the same session with the gate's last lines, at no
attempt's cost; attempts count only crashes, invalid results and provider
errors. A session by hand runs `fe_land.py` itself, in the background, and is
told when it ends. Measurement that needs exclusive hardware runs as a nightly
job against the night before, bisects a regression to the commits that caused
it and files them as one task; no commit waits on it.

**Why.** Separate reviewer and integrator roles cost attempts and could loop
with a moving trunk; the worker has the context to resolve a conflict. A close
held by a model cost tens of millions of re-read tokens a task waiting on
gates that needed no judgment unless they failed. Per-commit benches held the
shared hardware for hours a day, mostly waiting; a slowdown found the next
morning, batched with the others, costs far less.

---

### S23 — What needs no model, the process does (2026-10-05)

**Status:** active

**Origin:** BodySimulation D253

**Decision.** A model is spent on the task, not on its ritual:
- The manager writes the task brief into the worker's first prompt (the task,
  its thread, the linked entries' text, the sync state); the worker does not
  fetch any of it again.
- Waits block in the tool that waits; a hook warns on a sleep loop and on
  dumping an indexed file.
- A worker session already past a size cap is not resumed: a new one starts
  from the brief, the last result and the diff.
- `python studio/fe_usage.py night` is the audit as a tool: the meter per task,
  each session's context, every step's calls, time and carried cost (result
  tokens times the turns after it), the opening before the first edit, the
  files read again, the minutes polled. The manager posts its summary once a
  day.

**Why.** An audit of one night found the same opening ritual in every worker
(a median 22 tool calls before the first edit), hours of sleep loops, the
closing gates run by hand in a different order each time, and sessions resumed
at several hundred thousand tokens. All of it was predictable from the task row
and none of it needed judgment.

---

### S24 — A worker compacts its session at each phase boundary (2026-10-05)

**Status:** active

**Origin:** BodySimulation D259

**Decision.** Compaction happens at the work's phase boundaries, not when the
context is full: read and planned; built with the tests passing; measured;
committed. Under the manager the worker returns `compact` with a note saying
what is done and what phase is next; its wrapper compacts the same session
with a fixed focus (the task, commits and files, every screenshot read and what
it showed, each number and its command, what was ruled out and why, the steps
left) and resumes it with the note, at most a fixed number of times a run.
Codex compacts itself. By hand, an agent names the point
(`compaction point: ...`) and the owner compacts. The instruction file's
"Compact instructions" hold the same list for any compaction.

**Why.** A run costs about its turns times its context. One run of 162 turns
grew from 47k to 328k tokens, and two thirds of its turns carried more than
150k, because automatic compaction waits until the model's window is nearly
full. What matters is on disk, in git or in the note.

---

### S25 — A task runs on the model class its work has earned (2026-10-05)

**Status:** active

**Origin:** BodySimulation D218, D238, D240, D270, D300

**Decision.** A tier is a class of model and the CLI that runs it (for
example `sonnet` and `opus` on Claude Code, `sol` on Codex), and a class runs
its latest model: the CLI's own alias, or the newest of the class in the CLI's
own model list; the newest installed CLI runs it. A config entry may still pin
an exact id. A task's tier is chosen, never defaulted, in order: the owner's
pin (`python studio/fe_manager.py model T12 TIER|auto`); a tier the task was
escalated to; the planner's suggestion checked against the record; else the
policy by the work's category (the cheap tier off the core, a strong tier on
it). The record per category and tier (landed against escalated or failed,
counted for the first tier tried) overrides a guess past fixed thresholds. A
cheap run that fails, stalls twice or runs out of hours is handed once to the
strong tier with the better record. Which strong tier leads can follow the
providers' windows (one provider leads while its window is under a set share
used; past it, or with no reading, the record decides). A pinned tier waits for
its own CLI rather than run elsewhere. Every run's tier, model, tokens per
model, turns and time (working, and waiting on a lease) are recorded and shown
(`python studio/fe_manager.py models`).

**Why.** Defaulting every run to the strongest model was slow and dear; easy
work off the core runs well on a faster one, and the record, not a guess, says
where. Models ship faster than the manager's code: a class inherits its record
when a new model joins it, and nobody edits an id.

---

### S26 — What the manager does, what waits on what, and the allowance spent are one reading (2026-10-05)

**Status:** active

**Origin:** BodySimulation D210, D244

**Decision.** One function builds the manager's outlook: what runs (each run's
task or goal, checkout, duration and last steps), what waits and why in the
scheduler's own order (a proposal waits for the owner, a landed task for his
review, a dependency names its task, a ready task names the busy slot or the
missing provider), what became of the owner's recent messages, and the
allowance: per provider, the percent of each usage window used, when it resets
and how old the reading is (marked stale when past its window; "no reading
yet", never 0%). The board's Manager tab and Agents view, `fe_board.py who`,
`python studio/fe_manager.py status` and Discord's `/status` and receipt all
render it. The service publishes what it can run at each tick, and readers use
that, never their own guess. Providers are found again every tick. The
allowance numbers are read from the CLIs' own logs, not asked of a model; they
choose nothing except where a routing rule says so (S25).

**Why.** The owner asked the manager a question and saw nothing happen: his
goal waited behind a worker in the one slot left after a CLI moved itself, and
no view said so. A single reading every surface renders cannot disagree with
itself.

---

### S27 — A managed task's whole transcript opens on the board (2026-10-05)

**Status:** active

**Origin:** BodySimulation D222

**Decision.** Every worker's and planner's stream is logged whole, with a
summary of its thinking. `#log/T43` on the board shows every run of a task,
oldest first: its model, checkout, state, spend, the prompt, then its thinking,
what it said, its tool calls (folded, each opening to its command and result)
and how it ended; `#log/G12` is a goal's planner runs. A running run's view
follows the log without moving the reader's scroll. The view gives the command
that opens the last session as a fork, so the worker is never disturbed. Both
providers' logs are read.

**Why.** The last few steps say what a run is doing, not why. Reading the
reasoning behind a result is how the owner judges a task and how the process
crew finds detours; forking keeps reading from ever touching the work.

---

### S28 — A run ends on its real result, and a task past its hours is reported from its log (2026-10-05)

**Status:** active

**Origin:** BodySimulation D201, D214, D321

**Decision.** Only a result that ends the work (an answer, an error, a turn of
work) ends a run; an empty turn's result does not, and the grace before the
process is reaped counts from the model's last message. A failed run's next
attempt keeps everything it was told (the owner's replies, earlier notes, words
sent meanwhile), then the error. A task has three hours of worker time since
the owner last approved or answered it; at three hours the worker is told once
to stop, leave its work unpushed and return `blocked` with one line, and twenty
minutes later the supervisor ends a run that has not. For every task blocked
past its hours the manager writes the report itself, from the runs' logs and
without a model: what blocked it (failing builds and tests with their last error
line, a command run over and over, how the log ended), what the time went to,
and where the checkout stands; the worker's own note stays below it.

**Why.** A wrapper that took a resumed session's empty first turn as the end
killed a worker mid-work and spent an attempt. A worker that has run out its
hours is the one worst placed to say why; its log says it cheaply and
deterministically, even once the worker is gone.

---

### S29 — An allowance failure is the manager's problem, not the owner's (2026-10-05)

**Status:** active

**Origin:** BodySimulation D197, D212

**Decision.** An allowance failure (the provider CLI's own exit text saying the
plan, quota or credit is spent) is not a worker failure: no message, no attempt
spent, and the run is dispatched again at once on the other provider. The
blocked provider waits as long as it said, held between a quarter of an hour
and a day; with no time given, each consecutive block waits longer. A run it
carries through clears the block. With every provider blocked the manager
idles and resumes itself. Only the CLI's own exit text is read as an allowance
(never a model's prose about one), and the silence is bounded: after four in a
row on one task the ordinary noisy path resumes. Authentication and permission
failures still reach the owner. The block is visible in the status (S26)
without a message.

**Why.** A spent allowance is a fact about the manager's plumbing, not a
decision anybody can make; it reached the owner's phone twice in a day and cost
each task an attempt. Bounding the silence keeps a limit that never clears from
hiding for ever.

---

### S30 — The talk channel and /ask answer and change nothing (2026-10-05)

**Status:** active

**Origin:** BodySimulation D317, D322, D212

**Decision.** A plain reply in a task's thread goes to its worker; on a landed
task it reopens it for good (it lands again only with a new commit). To ask
without starting work, the owner writes in the talk channel (configured by name
or id; only his messages, only those after it was first found) or uses
`/ask question:` in a task's thread. Either records an `ask`: it opens no goal,
reopens no task and reaches no worker. A quick read-only run (S20) answers in
that place, with its own session per place (the channel, or each task's thread,
whose first prompt carries the task and its worker's last report), resumed with
only the new words and started fresh after hours of silence. It carries no task,
holds no checkout and touches no task clock. It runs while the manager is
paused, not while it is stopped.

**Why.** The owner reviews from a phone and wants an answer in the time it
takes to read one, without the review turning into work. A command is explicit
where a prefix could be typed by accident, so the default stays: a reply is
for the worker.

---

### S31 — A plan document is an illustrated PDF the owner can read on a phone (2026-10-05)

**Status:** active

**Origin:** BodySimulation D241, D242, D212

**Decision.** A plan document (a proposal, a design, an attack plan) is written
for a phone: the recommendation first, a figure wherever the content is spatial
or numeric, standalone captions, every source credited. It is not finished
until its PDF is in the task's thread: `python studio/fe_plan.py check
docs/NAME.md` (missing figures, uncredited captions, size limits) and `pdf`
(a phone-shaped page; figures placed so no page is left half blank, no heading
ends a page and no caption is parted from its figure). Figures are declared
beside the document, each with its source, link and date fetched for research
images. A task's documents are read from the task itself (the Markdown its
landing commit added or changed, and those its worker named), never from other
commits or the histories, and open from the task on the board.

**Why.** A long Markdown file attached as text reads badly on a phone and
carries no picture, and the plans were the one thing the owner had to read
there at length.

---

### S32 — The cleanup crew is judged by health and detours, works from deltas, and reports what it changed and turned down (2026-10-05)

**Status:** active

**Origin:** BodySimulation D265, D280, D319

**Decision.** After the nightly measurement, two crews run as ordinary tasks
that wait for the owner's Accept. The **code crew** (`tidy-code` skill) gets
deltas with their causes (what got worse on the trunk in the last day and the
commits that touched it, new module cycles, the costliest debt, clones, files
that change together), never a score to raise. Every commit states a directive
(Problem, Evidence, Change, Expect, Advisory). Behaviour is gated (the
regression hashes identical, no fewer tests, no new lint warnings) and so is
structure (a new edge on a module cycle fails); complexity and duplication are
advisory, each justified in the directive. A blind reviewer reads only the
directive and the diff and returns keep, revise or revert; only `keep` lands.
The **process crew** (`tidy-process` skill) is judged by
`python studio/fe_usage.py detours` (place, tool, silent, environment,
workaround), by whether its target recurs less and its tool is used. Lines of
code and tokens per line are displays only. A crew run's result says, in plain
words, each change with its why and numbers, and what it turned down; the
manager builds the landed report from it, the commits and the kept reviews.

**Why.** Paid for fewer lines, an agent deletes tests and squeezes code; paid
for a score, it splits hot functions and raises coupling. Most model-written
refactorings do not preserve behaviour, so the gate comes first, and a reviewer
catches what the numbers miss. The owner must be able to accept a crew night
without opening git or a transcript.

---

### S33 — A landed task's Accept is the message's own and is taken for the latest commit (2026-10-05)

**Status:** active

**Origin:** BodySimulation D284

**Decision.** A review message carries Accept and Request changes for its
commit whenever it is finally sent, whatever phase the task has reached since.
A click is refused only when the task is done or dropped, has no commit, or the
button's commit is not the latest (it names the newer one); otherwise it accepts
the task in any phase. A review whose control never arrived is posted once more
as a control message (quietly). Delivery cannot be stopped by one row: any
exception is logged and backed off, attachments go ten to a message, and a
message Discord rejects for its files is sent again without them, naming what
was left out. There is no accepting by typing.

**Why.** The owner could not accept a task because its review message, with
eleven attachments, had been refused by Discord on every retry for days,
silently. A control decided at sending time and a click that wants an exact
snapshot go dead on any timing the manager does not control; every link of
that chain was the manager's code, and each failed without a word.

---


---

### S36 — Goal pings reach its allowed participants (2026-10-06)

**Status:** active

**Origin:** YYEngine G9

Discord authentication accepts the configured owner_ids allowlist, defaulting to the single owner_id. Authenticated input retains its author. A new goal retains its requester and everyone who writes or uses a control in its planning or task threads. Pings for that goal and its tasks mention those participants who remain allowed. Manager-wide notices and older goals with no recorded requester retain the owner fallback. Replayed input cannot change its author. Additive tables preserve existing stores.

**Validation:** test_discord_goals.py exercises transport receipt, partial answer buttons, planner replies, task questions, goal completion, allowlist removal, legacy goals and duplicate input without network access.
