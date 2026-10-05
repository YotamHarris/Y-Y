# <!-- fill: GAME NAME --> — working rules for every agent

<!-- fill: one line on what the game is and where its code lives, e.g. "A Rust/Vulkan game in `engine/`." -->

@studio/AGENTS.md

This file holds the game's own rules; the studio's (the docs, the board, the
manager, finding code, checkouts and landing) are imported above.
**Codex:** imports are not expanded for you: read `studio/AGENTS.md` too.
`CLAUDE.md` is only `@AGENTS.md`; never add rules there. `studio.toml`
describes this game to the studio.

## World and fiction

The game's world, fiction and gameplay vocabulary live in
<!-- fill: path to the setting document, e.g. `docs/world/` -->. Read it before
any work that touches gameplay, enemies, damage, healing, art direction or
naming. It is the owner's document: propose fiction changes to the owner, do
not edit it directly. When a game constant is derived from it, record the
derivation as a decision entry and cite the document.

## Definition of done

A feature is done when all of the following hold, and the report says which
commands produced the evidence:

1. **Validated through the player's input path.** If the player does it with
   the mouse, the acceptance test drives the same code the mouse does
   (<!-- fill: the flags or control verbs that feed real input, e.g. `--drag x0,y0,x1,y1`, `fire X,Y` -->).
   A scenario that places things directly in the world is a debug tool for
   isolating bugs. It may support a claim about the sub-system it exercises;
   it never proves the feature works for the player.
2. **Obeys the design constants.** Every scripted scenario respects the same
   limits the interactive tool has (reach, size, speed). A scenario that needs
   to break one is labelled as bypassing it, and the report says so. If the
   constant itself is wrong, that is a decision entry, not a test-side
   workaround.
3. **Looked at.** Any rendering or physics claim comes with a screenshot taken
   by <!-- fill: the shot command -->, at a camera that shows the claimed
   effect, and the screenshot was actually read before the claim was written.
   Debug views (<!-- fill: the game's debug view modes -->) count as evidence;
   shaded views alone do not.
4. **Audited.** The game's shot-time audits
   (<!-- fill: the audits a shot reports, e.g. NaN count, invariant checks -->)
   are quoted for the acceptance run. A failing audit means not done.
5. **Within budget, measured nightly.** No commit waits on a benchmark: the
   manager benches `origin/main` every night against the night before, bisects
   a slowdown to the commits that made it and files them as one task. The
   bar: <!-- fill: the frame-time allowance and the worst-scenario rule -->,
   confirmed with repeated alternating pairs before a timing finding;
   correctness, audits, memory, allocations and hitches are findings whatever
   the time. Bench a task yourself only when its point is its cost:
   <!-- fill: the bench compare command -->. Otherwise the performance check is
   `not_applicable`, "measured nightly". Targets are release gates; budgets
   move only by decision. Bench frames are drawn at one fixed resolution.
6. **Reproducible.** Shots and benches use the fixed step; the same command
   gives the same hash. A scripted launch with a fixed frame count is
   reproducible; driving a running instance over the control channel is not.
7. **Reachable from the menu.** Anything that needs the owner's review (a
   scene or level, a tool, a mode, a debug view, a model, a setting) can be
   opened in the running game from the menu bar or from a window it opens. A
   launch flag, an environment variable or a control verb alone does not
   count; each may stay as the scripted path to the same code. The report
   says where in the menu it is.
8. **Every edit undoes.** A new verb, panel control or knob that edits state
   either undoes with Ctrl+Z or says why not, and the build and tests refuse
   one that is silent (<!-- fill: the undo audit command -->). The report says
   where each edit was tested; a control with no undo path goes in the
   version's `**Open:**` block.

## Refactors

A change that claims to change nothing is proven bit-identical, not looked at:

```
<!-- fill: the regression baseline command -->   # before: HEAD's hashes (a scratch worktree if the tree is dirty)
<!-- fill: the regression check command -->      # after: exit 1 on any difference
```

- A fixed set of player-path replays, each shot at a fixed frame with the UI
  hidden, and every hash its log prints compared. A hash that differs between
  two runs of one build is reported as flaky, never gated. A pure refactor
  takes no version.
- What two languages both declare (a binding, a struct layout, a list of
  passes) has one source and a test holding the copies together.
- `python studio/fe_land.py --refactor` takes its own baseline and checks
  against it.

## Iterating on a running instance

- **Agent runs stay hidden.** The game stays invisible when an agent marker
  (`FE_AGENT`, `CODEX_THREAD_ID`, `CLAUDECODE`) is set and for scripted
  launches; screenshots, replays and the control channel still render at full
  resolution. Real input does not need a visible window
  (<!-- fill: the input tool -->). Never minimize or move a window off-screen
  instead: that can resize or suspend it. Launch helpers with no console
  window.
- **The control channel.** The game listens on `127.0.0.1:<port>` (the
  agent's port from the registry). Drive it with
  <!-- fill: the control client, e.g. `python tools/fe.py CMD ...` -->: each
  argument is one command, replies print in order, `shot` returns the audit
  report. A new verb is one row in the command table, which tests hold to the
  handler and to every replay. `help` lists them all.
- Use the fixed step before a test; `reset` returns to the launched state
  without relaunching. A shader or data change reloads in place; only a code
  change needs a rebuild.
- **The feature reference** is <!-- fill: e.g. `docs/game-reference.md` -->,
  one heading per topic: every control verb, window, knob and acceptance
  script of a feature. Read its topic, not the file:
  `python studio/fe_index.py show "docs/game-reference.md#topic"`. A feature's
  change updates its topic in the same commit.

## Building

<!-- fill: the build wrapper, e.g. `python tools/fe_build.py [build|check|test|lint]` --> is how the game is built.

- It answers in one summary line when it works, and one `path:line:col CODE
  message` per error when it does not; `test` reports passed and failed with
  each failure's line. The full output goes to a log in the artifacts folder:
  read it when the short form is not enough. The exit code is the compiler's.
- While this checkout's game is running it builds into a scratch target of
  its own (one per agent) and says so, since the running binary is locked.
- Prefer it to the raw compiler: eighty lines of progress for a one-line
  result stay in the context and are re-read on every later turn.

## Versioning

- <!-- fill: the version constant and its file, e.g. `BUILD_VERSION` in `src/main.rs` --> is bumped on every change that alters what the player can do or
  see, before the commit. The git commit is embedded at build time. Every
  report names the version it validated (it appears in the startup and shot
  logs).
- On a rebase conflict the version takes the larger number plus one.

## One GPU

Shots, replays and the games agents debug in share the GPU. A benchmark is
valid only while no other game draws, so it leases the GPU: the lease adapter
is `[adapters] gpu` in `studio.toml`, and
<!-- fill: the bench wrapper, e.g. `python tools/fe_gpu.py bench -- COMMAND` -->
pauses every open game (it finishes its frames, then neither steps, draws nor
submits), runs, and resumes them. A managed worker launches through
`python studio/fe_manager.py gpu [--bench] -- COMMAND`. The hooks refuse an
unclaimed bench and any launch while a claim is live. A paused game answers
that it is frozen and the control client waits and resends. The owner's own
game goes first; left idle while an agent waits, it is saved and closed.

## Adding assets

- **Concepts first.** Before creating a world asset, search
  <!-- fill: the concepts folder and its index --> for approved art that fits.
  Otherwise generate concept art of the setting and the specific environment
  first, and let the owner select and approve it. Keep every approved concept
  with its setting, tags, approval, prompt, the elements selected and links to
  the models derived from it; keep drafts separate; never mark a concept
  approved without the owner's selection.
- **Generated, not hand-made.** Visible world assets (props, fixtures,
  furniture, equipment) come from <!-- fill: the model generator -->, not
  hand-coded primitives; collision and gameplay are fitted to the generated
  model, with simple proxies beside it.
- **One recipe per model.** A model is one recipe file beside its source, and
  nothing else is edited by hand:
  <!-- fill: the model commands (new, bake, status, verify) -->. A fresh
  checkout bakes every model that is not fresh as part of the build.
- **A credit budget.** A model may cost at most
  <!-- fill: credits --> credits, concepts and retries included; the tool
  refuses a request past it, and only the owner may raise it for one model.
- Improve the tool and the add-model skill when a model needs a manual step;
  a changed default, gate or limit is a decision entry.

## Practical notes

- **The CPU and the GPU work in parallel.** The CPU prepares frame N while the
  GPU draws N-1, so a frame takes the longer of the two, not their sum. Before
  its submit a frame touches only its own slot and never waits on the GPU; a
  resource both frames use changes only behind a full idle.
- **Environment switches** are rows in one table
  (<!-- fill: path -->); a new one is a row there.
- **Crashes** end the game and leave a report
  (<!-- fill: the crash summary command -->).
- **Scratch is pruned.** Move what must last to
  <!-- fill: the kept artifacts folder -->.
- Patch scripts anchor on exact current text; never splice a region by a
  comment anchor without checking which item the comment belongs to.

<!-- fill: game-specific runtime notes (caches, validation layers, player's build) as a short index into the feature reference -->
