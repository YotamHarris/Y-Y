# agent-studio

The process that runs a game's development with AI agents (Claude Code and
Codex), split out of BodySimulation so every game can use it:

- **The board** (`fe_board.py`, `board_ui/`). The owner's thoughts, the agents'
  tasks and the messages between them, kept in one SQLite store per game. It
  has a web UI and a CLI, and the hooks tell each session what is new.
- **The background manager** (`fe_manager.py`, `manager_*.py`).
  - Goals are planned in Discord and become tasks once the owner approves the plan.
  - Workers run on free checkouts, each on the model class its work has earned.
  - Finished work is landed for the owner to accept.
  - Nightly jobs and a cleanup crew run on their own.
- **Sync and landing** (`fe_sync.py`, `fe_land.py`). Every agent has its own
  checkout on one trunk, hooks keep them in step, and one blocking call
  closes a change: the gates, then the push.
- **The decision log** (`fe_docs.py`): append-only histories, a status on
  every entry, and generated summaries.
- **Finding code** (`fe_index.py`): a symbol index that prints one item at a time.
- **Measuring the process**: `fe_usage.py` and `fe_tokens.py` show where the
  tokens and time went, and Jev (`fe_jev.py`) reads prose inside the hooks.

The rules every agent follows are [AGENTS.md](AGENTS.md). The reasoning behind
them is the studio's decision log, [docs/decisions/](docs/decisions/), with
entries S1, S2 and so on. Every entry names the BodySimulation decision it
came from.

## Using it in a game

The studio is always mounted at `studio/`:

```bash
git submodule add -b main https://github.com/YotamHarris/agent-studio.git studio
python studio/fe_studio.py init --name MyGame --owner "Your name" --board-port 45310
```

`init` writes the files the game needs:
- `studio.toml`, which tells the studio about the game: its name, owner, ports,
  tools folder, land gates, adapters and nightly jobs.
- The hooks in `.claude/settings.json`.
- `AGENTS.md` (from [templates/AGENTS.game.md](templates/AGENTS.game.md)) and `CLAUDE.md`.
- An empty agent registry.
- The docs skeleton.

Commit them, then clone each agent checkout with `--recurse-submodules`.

Each game is its own instance:
- **Board:** its own store in `%LOCALAPPDATA%\<name>\board`, served on its own port.
- **Manager:** its own scheduled task, `<name> Project Manager`.
- **Discord:** its own bot token in the Credential Manager (`<name>/DiscordBot`)
  and its own Discord channel.

Set up the manager with `python studio/fe_manager.py setup`; the steps are in
[docs/background-manager.md](docs/background-manager.md).

### The game's side

The studio calls into the game only through `studio.toml`.

**Data**
- `[[land.gate]]`: the game's gates, such as build, test, a replay check or a bench.
- `[land.version]`: the version file a runtime change must bump.
- `[game]`: the game's process names, which the routing and the Agents view watch.
- `[[sync.push_gate]]`: a compile check run before a push.
- `[checks]` and `[routing]`: what a worker must prove, and which model gets which work.
- `[docs]`, `[index]`, `[usage]`, `[health]`, `[loc]` and `[jev]`: the game's
  categories, languages and paths.

**Adapters** (modules in the game's tools folder)
- `[adapters] gpu`: a lease on hardware only one game may use at a time.
  It needs `pre_bash`, `session_lines`, `notice`, `is_bench` and `pid_alive`.
- `[adapters] debt`: accepted performance debt (`valid_debt`, `registry`, `save`).
- `[[nightly]]`: a job the manager runs once a night (BodySimulation's is the bench).

A game with none of these still gets the board, the manager, sync, landing,
the docs and the index.

Game tools that import a studio module put `studio/` on `sys.path` first, as
BodySimulation's `engine/tools/_studio.py` does.

## Changing the studio

Edit inside `studio/` in any game and commit there, on `main`. Then run
`python studio/fe_sync.py push`. It pushes the studio first, then the game with
its gitlink moved to the new commit. A game never pins a studio commit that
the studio's origin lacks. A new rule is an S entry, added with
`python studio/fe_docs.py new --studio manager "Title"`.

## Tests

From a game's root:

```bash
python -m unittest discover -s studio -p "test_*.py"
python studio/fe_board.py selftest
python studio/fe_jev.py selftest
```

`test_manager.py` runs in the manager's venv
(`%LOCALAPPDATA%\<name>\board\manager\venv`).
