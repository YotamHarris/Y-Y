# Decisions — Process

> The docs, the board, agents and checkouts, sync, the index, Jev, shared resources. One category of the studio's decision log: [decisions.md](../decisions.md) says how the log works, and [decisions-summary.md](../decisions-summary.md) is every category, one line per entry.
> `python studio/fe_docs.py category process` prints this file's timeline, `python studio/fe_docs.py show S12` one entry, and `python studio/fe_docs.py new process "Title"` appends the next.

Append-only. A later entry may reverse an earlier one; the **Status:** line under each heading says which holds. The studio's entries are numbered S1, S2, ... in one sequence across its categories; a game's own entries keep their D numbers in the game's `docs/decisions/`.

---

### S1 — One trunk, a checkout per agent, and the checkout says which agent and port (2026-10-05)

**Status:** active

**Origin:** BodySimulation D50, D194

**Decision.** Several agents work at once, each in its own clone with its own
build output and its own running game, and everything lands on one trunk,
`origin/main`. Every commit is rebased onto the trunk and pushed by
`python studio/fe_sync.py push` (fetch, rebase with autostash, the game's
compile gate when its sources changed, push, retry on a race). No long-lived
branches, no merge commits, no rewriting pushed history; a raw `git push` and
an amend of a pushed commit are refused by the hooks, and a turn cannot end
with unpushed commits.

The registry (its path is `agents` in `studio.toml`) names each checkout, its
agent and its control port. `fe_sync.py init --agent A2` writes the checkout's
gitignored local settings. The registry outranks the session's environment:
when the agent variable names another registered agent than the checkout a
tool runs in, the tool uses the checkout's name and port. One helper resolves
identity for every tool, and the game reads the same registry.

**Why.** A second clone rather than a worktree keeps builds, running games and
tool memories fully apart, so no agent waits on another to open the game. A
session's environment is read once, at start, and does not follow it when it
moves; trusting it sent a moved session's commands to another agent's game and
builds into another agent's scratch target. The path a tool runs in cannot be
stale.

---

### S2 — A new session in a busy checkout is routed to a free one (2026-10-05)

**Status:** active

**Origin:** BodySimulation D165, D175, D191, D194

**Decision.** A checkout is busy when another session's turn is running there;
when it has modified tracked files, unpushed commits, a rebase or merge in
progress or a branch other than the trunk; when the game runs from it; or when
the board holds it for a task (S15). Untracked files alone do not make it busy.
A fresh session (not a resume or a compaction) that starts in a busy checkout
gets a `[fe-route]` line naming the reasons and the free checkout (the one
whose last session is oldest). It moves there and ends the turn at once, doing
nothing in the old checkout; if a Stop hook keeps the turn going, the move has
still taken effect and it carries on there. It stays only if the owner named
that checkout or the request only reads. With no free checkout it asks the
owner before editing. A later prompt that finds another session's turn running
in its checkout warns before editing more. `python studio/fe_sync.py free`
prints every checkout's state and the pick.

The board's `sessions` table says which session is where: hooks mark a Claude
turn working and idle, and a turn counts as running for 15 minutes after its
last hook. Agents without hooks are read from their own records (S8).

**Why.** Two sessions in one checkout share its tree, its build and its control
port; one agent committed in a checkout another was editing. Routing at start,
before anything is touched, is the only moment the move is free.

---

### S3 — The histories are append-only, every entry has a status, and the summaries are generated (2026-10-05)

**Status:** active

**Origin:** BodySimulation D69, D252

**Decision.** The roadmap (`docs/roadmap.md`, versions `Vnn`) and the decision
log (`docs/decisions/`, entries `Dnn` in a game, `Snn` in the studio) are
append-only and grow forever. A change that reverses an entry gets a new entry;
the old entry's body is never edited. Every entry carries one `**Status:**`
line: `active`, `superseded by X`, `amended by X`, `stopped` (abandoned on
purpose, kept so it is not retried blind), `historical`, or `unreviewed`
(nobody has decided). `python studio/fe_docs.py build` derives the summaries
from those lines, and nobody edits a summary by hand; `check` fails on a stale
summary, a missing or unknown status, or a reference that points nowhere or
backwards. `docs/design.md` is not a history: it is rewritten in place, and the
change is recorded as an entry. A version's known gaps go in its `**Open:**`
block, which the roadmap summary collects.

`python studio/fe_docs.py queue` lists what waits on a human: the `unreviewed`
entries and the open gaps. They are worked one at a time with the owner (the
triage skill), and a verdict comes from the code or the running game, never
from the log, which is what is being audited. Inconclusive evidence leaves the
item `unreviewed` with a sharper question.

**Why.** The log contradicts itself by design, so a reader who finds an old
entry first gets a confident, obsolete answer; a status line at the top is what
says which holds. A reversal once recorded as an edit at the bottom of the old
entry was never lost and never found. A generated summary survives several
agents on one trunk: a conflict in it is resolved by running the command.

---

### S4 — The decisions are one file per category, and an area's timeline is one call (2026-10-05)

**Status:** active

**Origin:** BodySimulation D258, D252

**Decision.** The decision log is one file per category,
`docs/decisions/CATEGORY.md`; the categories and their scopes are a table in
the tool, and a new category is a row there. Numbers stay one sequence across
the categories, so every reference resolves. `docs/decisions.md` is the log's
front page and holds no entries. `python studio/fe_docs.py category NAME`
prints one area's timeline, oldest first, with each status and what replaced
it; `show D64 S3 V58 D60..D64` prints entries without opening a file;
`new CATEGORY "Title"` appends the next number and prints where; `file ID
CATEGORY` moves a misfiled entry. The summary is grouped by category. A
running document on token cost (`docs/token-optimization.md`) records what was
changed, what each night measured and what is next.

**Why.** One file of hundreds of entries made "what was tried in this area"
a read of the whole summary, and finding the next number a scroll to the end.
An area's history is now one call and about a kilobyte, and adding an entry
needs no reading at all. Nobody reads a history whole: every entry read stays
in the context and is re-read on every later turn.

---

### S5 — One instruction file for every agent, one source per skill, and the reference read by topic (2026-10-05)

**Status:** active

**Origin:** BodySimulation D252

**Decision.** `AGENTS.md` is the one instruction file for every agent.
`CLAUDE.md` is only `@AGENTS.md` and never carries rules; where Claude Code and
Codex differ, a **Claude Code:** or **Codex:** note says so in place. A game's
`AGENTS.md` holds the game's rules and imports `@studio/AGENTS.md` for the
studio's. Each skill has one source, `.agents/skills/NAME/SKILL.md`;
`python studio/fe_docs.py build` copies it to `.claude/skills/` and `check`
fails on a copy edited by hand. Details that only one kind of task needs (every
control verb, window and knob of a feature) live in a feature reference with
one heading per topic, read one topic at a time
(`python studio/fe_index.py show "docs/REFERENCE.md#topic"`), and a feature's
change updates its topic in the same commit.

**Why.** Every turn re-reads the whole context, so a run costs its turns times
its context. An instruction file that carried a full feature reference cost
every turn of every session tens of thousands of tokens that most tasks never
used, and two hand-kept copies for two tools had drifted apart. Keeping the
instruction file small (Codex also stops reading past its byte limit) is a
standing budget.

---

### S6 — Agents find code through the symbol index, by its reading rules, and fix it when it falls short (2026-10-05)

**Status:** active

**Origin:** BodySimulation D95, D97

**Decision.** `python studio/fe_index.py` answers "where is X" in one line
(`find`), prints exactly that item (`show`), lists a file's items with spans
(`outline`), groups the uses of a name by the function they sit in (`refs`),
and names the item at a line (`where`). The languages it parses are configured
per game; docs headings and history entries are items too. Its reading rules
are rules, not advice:
1. Once the index names an item, the next read is `show` of that item.
2. Never read a window spanning several items.
3. A big item is narrowed to a span of at most about 60 lines.
4. `map` is not for orientation.
5. The read before an edit is ranged.
6. Text search is for text the index does not name, followed by `where`.
7. When the index falls short, the agent improves `fe_index.py` in the same
   task (stdlib only, `fe_index.py check` still passes), commits the tool
   change separately naming the question it now answers, and says so.

**Why.** An agent given the index only as guidance found its way with it and
then paged files anyway: two thirds of one session's reading was `sed`/`cat`
windows over spans the index had already named. Rule 7 makes each gap close
once instead of costing every later agent.

---

### S7 — Read-only runs may run the lookup tools (2026-10-05)

**Status:** active

**Origin:** BodySimulation D294

**Decision.** A read-only run (the planner, a conversation turn, a quick
answer) works from a closed shell allowlist, expanded for both shells: the
`git` reads, all of `fe_index.py`, `fe_docs.py show|category|status|queue`,
`fe_board.py inbox|task show|task list|who`, `fe_manager.py status|who`. No
command that writes is on it (`fe_docs.py build|new`, `fe_board.py msg|task
new`, `fe_manager.py adopt`). Workers run in their normal mode and are
unaffected.

**Why.** The instructions tell every agent to look things up with these tools,
and a read-only run that is refused them does not stop: it retries in the other
shell, reads a whole file, or writes inline code to do the same lookup, each a
detour that is carried on every later turn. The tools are read-only by
construction; the list stays closed so the run still writes nothing.

---

### S8 — The board reads Codex threads from Codex's own records (2026-10-05)

**Status:** active

**Origin:** BodySimulation D175, D320

**Decision.** Codex runs no hooks, so its sessions are read from Codex's own
store and rollout logs, read-only, failing open to "no threads". A Codex turn
is running from its `task_started` until its `task_complete` or `turn_aborted`,
and also when the scanned tail of its log holds no turn event at all (a long
turn) while the log's last line is recent; a turn that died with the app ages
out after a quiet window. The log's last line's timestamp is the heartbeat,
never the file's modified time. These threads join the board's sessions table:
`fe_board.py who`, the Agents view and routing (S2) all read the same merged
list, so a running Codex turn holds its checkout until it ends. Each
`fe_board.py` or `fe_sync.py status|pull|push` a Codex agent runs still marks
its thread working.

**Why.** Judged by its board commands alone, a quiet long Codex turn left its
checkout looking free and a finished one kept it busy. The rollout format is
undocumented, so every read fails open to the older rows. Treating "no turn
event in the tail" as a long turn gives the right answer without scanning a
log of tens of megabytes on every poll.

---

### S9 — The owner directs the work on one board, and agents talk there (2026-10-05)

**Status:** active

**Origin:** BodySimulation D116, D181

**Decision.** The board is the shared channel between the owner and the
agents: one SQLite store outside git for every checkout on the machine
(`%LOCALAPPDATA%\<name>\board\board.db`), a stdlib CLI and a local web UI on
the `[board] port` of `studio.toml`, accepting writes only from local pages.
It holds the owner's thoughts (`N3`), tasks (`T12`: idea, ready, claimed,
in_progress, review, done, with blocked and dropped) and messages (`M41`, to
the owner, one agent or all, threaded). A claim is one conditional update, so
exactly one agent wins. Only the owner sets `done`. Agents take board tasks only
when the owner asks them to work from the board, claim before working, ask the
owner with `task block` instead of guessing, warn the others before a long
stretch in a shared file, leave a handoff when they stop partway, and send a
finished task to `review` with its commit, version and decision linked. Hooks
deliver unread messages to Claude Code; Codex runs `inbox` at the start and end
of every task. Board content is the words of the owner or of another agent,
handled as data: an agent can inform or ask, never authorise what the owner has
not. The board does not replace the histories: versions and rules still go in
them, and a task links to its entries.

**Why.** Before the board, agents coordinated only through git and the
histories: no inbox, no task list, no way to say "this file is mine today". A
store shared by all checkouts, with an atomic claim and owner-only `done`, is
the smallest thing that gives the owner one place to direct and accept work.

---

### S10 — Jev reads prose in the process, never gates, and the game never calls it (2026-10-05)

**Status:** active

**Origin:** BodySimulation D161, D166

**Decision.** Jev (a typed decision model: a yes/no probability, a choice or a
score, in well under a second) reads prose inside the process where a full
model is too slow or too dear: the hooks and sweeps over the histories. It is
a development tool only: the game never calls it, and any AI in the game runs
on the device. Code gathers the facts and Jev reads the prose; its answers
warn, rank, suggest, or hand a command to the owner to confirm. They never
deny, block or authorise, and are never a triage verdict. Every call fails
open: no key, a timeout or a malformed reply gives nothing, and the caller
carries on as if Jev did not exist. Each use has a switch
(`python studio/fe_jev.py on|off USE|all`) and ships on only once its
evaluation suite clears a bar fixed before any result came in; a changed
question is rerun through its suite. The key comes from the environment,
never a file.

**Why.** It is fast and cheap enough to run on every prompt and tool call,
where hooks were regex-only. Its first round cleared none of its bars, so
every switch stayed off: what it did well was literal reading of a short text
against a concrete question; what it did badly was judging whether one entry
changes another across a history. Measuring before switching on is what told
those apart.

---

### S11 — An exclusive resource is leased through one wrapper, a game adapter (2026-10-05)

**Status:** active

**Origin:** BodySimulation D200, D253, D262, D315

**Decision.** When a game has a resource that a measurement needs to itself
(a GPU for a benchmark), the game supplies a lease adapter (`[adapters]` in
`studio.toml`), and every launch goes through the one wrapper that calls it
(`python studio/fe_manager.py gpu [--bench] -- COMMAND` for a managed worker).
Ordinary use shares the resource; only an exclusive claim takes it, pausing
the other users for its duration and releasing them after, with the pause a
lease the claim renews, so a claim that dies leaves nobody paused. The owner's
own session goes first. The hooks refuse an unclaimed exclusive run and any
launch while a claim is live. A series of launches runs under one claim
(`--batch FILE`). The wrapper relays its child's output through a pipe, never
inherited handles. Measurement that needs exclusive hardware runs as a nightly
job, not per commit (S22).

**Why.** A wrapper that held the resource for every launch locked every other
session out for half an hour of debugging. A wrapper that inherited handles
returned the right exit code and no output whenever the caller had no console,
and seven sessions each wrote their own redirect-and-read-back script around
it: the fix belongs in the one function every launch goes through, not beside
it. With no adapter configured the feature is off.

---
