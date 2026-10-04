# YYEngine

A small C++20/SDL3 game engine and a Windows-hosted Discord development bot. You and a friend can discuss game ideas, choose Codex or Claude, and explicitly request changes that are checked, reviewed, merged, built on a hosted Mac, and distributed through TestFlight.

**Implemented locally:** TapDemo, engine/game tests, a runnable Discord service, persistent coordination, provider adapters, PR/merge integration, and iOS CI/signing/distribution scripts. The initial hosted unsigned iOS simulator build and launch passed. **Remaining acceptance:** a live Discord change/merge, the first signed TestFlight build, and installation on an iPhone. See [validation evidence](docs/validation.md).

## Try the game on Windows

Install Node.js 24.13+, Python 3.11+, Git, and Visual Studio 2022 with **Desktop development with C++** and a Windows SDK. Codex and Claude Code must be native executables if used by the bot.

From the repository root in PowerShell:

```powershell
./scripts/setup.ps1
./scripts/build.ps1 -Smoke
./build/windows/Release/TapDemo.exe
```

The smoke run exits after 120 frames and writes `build/windows/metrics.json` and `smoke.bmp`. CMake and Ninja are installed into the ignored `.tools` environment, not globally. TapDemo contains moving targets, scoring, a 30-second round, sounds, and tap-to-restart. The desktop uses mouse input; iOS uses touch. Game coordinates exclude safe-area insets and letterboxing.

## Connect Discord and enable iOS builds

Follow [the setup guide](docs/setup.md) for Discord permissions, GitHub automation credentials, local CLI authentication, Windows signing-certificate generation, and TestFlight configuration. Branch protection is optional. Then:

Double-click **Configure.cmd** in the repository folder to enter credentials in a dedicated PowerShell window. You can also run the script directly:

```powershell
./scripts/configure.ps1         # Prompt for credentials; optionally upload Apple secrets
npm run check
npm run doctor
npm run register
./scripts/start.ps1
```

The bot runs while this PC is awake and connected. You can run `scripts/start.ps1` through Windows Task Scheduler under the same account that owns your CLI logins. Configure it to run at logon, with the repo as its working directory, and restart on failure. Do not launch a second instance. Cloud builds continue independently while the PC is offline.

## Discord workflow

| Command | Behavior |
| --- | --- |
| `/ask game:tapdemo request:...` | Start a read-only discussion thread. Both developers can continue chatting there. |
| `/change game:tapdemo request:... provider:codex` | Start implementation, checks, independent review, PR, automatic merge, and TestFlight delivery. |
| `/build game:tapdemo` | Build and distribute the current main commit. |
| `/status task:<id>` | Show state, PR, build link, and failures; omit the ID inside a task thread. |
| `/status task:<id> resume:true` | Resume after clarification, login, interruption, or failure. Failed builds rerun the same workflow. |
| `/cancel task:<id>` | Cancel remaining work; preserve branches and report any merge already completed. |

`/ask` and `/change` accept `provider:claude` and an optional `model`. To turn a discussion into implementation, invoke `/change` from its thread: the new task inherits the transcript. Ordinary discussion messages never start a new implementation task. An answer in a paused change thread resumes that previously requested task.

Game tasks can edit the selected game, engine, and tests. Changes to workflows, service code, build configuration, and other games need normal development in the repo. Jobs run serially; working branches use `codex/`. Each review starts a separate provider invocation without the implementation session.

## Layout and checks

- `engine/`: portable game interface, fixed clock, input mapping, SDL renderer/audio/assets, and runtime telemetry.
- `games/tapdemo/`: SDL-free deterministic gameplay model plus the compiled game and assets.
- `tools/bot/`: Discord Gateway service, SQLite state/outbox, CLI providers, Git/GitHub coordinator, and tests.
- `config/games.json`: game IDs, CMake targets, bundle IDs, versions, and internal tester groups.
- `platform/ios/`, `.github/workflows/`, `fastlane/`: iOS packaging, simulator checks, signing, and TestFlight delivery.

```powershell
npm run check                   # TypeScript and service behavior tests
./scripts/build.ps1 -Smoke      # Release build, C++ tests, rendering smoke
```

On a Mac, `cmake --preset ios-simulator` and `cmake --build build/ios-simulator --config Release -- CODE_SIGNING_ALLOWED=NO` build an unsigned app. CI then launches it in a simulator and checks that 120 frames completed. Pure engine tests can also run without SDL using `cmake --preset headless`, `cmake --build --preset headless`, and `ctest --preset headless` (Ninja and a compiler required).

## Add another game

```powershell
python scripts/new-game.py puzzle --target Puzzle --bundle-id com.yourteam.puzzle
./scripts/build.ps1
npm run register
```

The script clones TapDemo as a starting project and adds it to the game registry. Replace its model and game behavior. CMake discovers registered projects at configure time, giving each its own app, assets namespace, and iOS bundle ID. Create a matching `testflight-puzzle` GitHub environment and Apple app/profile. Shared engine changes build all registered games; game-only changes distribute only that game. Initial unsigned iOS smoke testing exercises TapDemo; new games need equivalent gameplay/simulator tests before relying on their automated delivery.

See [architecture and recovery](docs/architecture.md) and [acceptance evidence](docs/validation.md). Android, 3D, scripting, a visual editor, and App Store production submission are deferred.

## Multica task board

See [Multica setup and sharing](docs/multica.md) for the local self-hosted task board,
TapDemo project, shared Discord/board coordinator, and temporary public HTTPS link.
Double-click **TapDemoBoard.cmd** or the **TapDemo board** desktop shortcut to start
and open it. Owner comments `/yy plan`, `/yy approve`, `/yy change`, `/yy ask`,
`/yy build`, `/yy resume` and `/yy cancel` use the same persistent bot queue.
Use `./scripts/multica.ps1 -Action Stop` to close public access.
