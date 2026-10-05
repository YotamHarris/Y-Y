# YYEngine

A small C++20/SDL3 game engine and a Windows-hosted Discord development bot. You and a friend can discuss game ideas, choose Codex or Claude, and explicitly request changes that are checked, reviewed, merged, built on a hosted Mac, and distributed through TestFlight.

**Implemented:** TapDemo, engine/game tests, the Discord/Multica manager, persistent coordination, provider adapters, PR/merge integration, and iOS CI/signing/distribution. Hosted Windows and unsigned iOS simulator checks pass. Signed build 10 uploaded and passed Apple processing; internal tester activation still needs confirmation. **Remaining acceptance:** a live game change/merge, verified TestFlight testing readiness, and installation/performance on an iPhone. See [validation evidence](docs/validation.md).

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

Write a goal in the project channel. The manager opens a planning thread on your
message, reads the code, discusses choices and proposes a few whole tasks. Reply
naturally, then **Approve plan** once. Keep that conversation for later goals.
Approved tasks receive separate threads and cards; up to two workers use isolated
worktrees. Validation and independent review precede automatic commit/push and merge.

Published work asks for **Accept** or **Request changes**. Acceptance unlocks
dependent tasks; TestFlight delivery continues separately. Describe revisions in
the task conversation to reopen the same task with its worktree and saved context.
Questions offer choices and **Answer in my own words**. Routine updates have no
buttons, and each run has one live progress line that disappears when it finishes.

Ask read-only questions in **meatbag-talk** or the board's **quick questions** card,
even while workers are busy or paused. Claude resumes its conversation; quick chat
starts fresh after three quiet hours. Codex uses the saved transcript. Quick chat
cannot start or control work.

| Global command | Behavior |
| --- | --- |
| `/status` | Active workers, waits on GitHub/Apple, decisions, queue, and reported usage. |
| `/models` | Show configured worker and quick-chat providers/models. |
| `/pause` | Let active work finish; hold queued workers. |
| `/resume` | Release queued work and resume stop-interrupted tasks. |
| `/stop` | Interrupt managed local processes, preserving work and sessions. |

You can also say status, pause, resume or stop in the project channel or manager
card. Answer a paused task or say continue after fixing a prerequisite. Say cancel
in that task's conversation to stop it. “Make a new TestFlight build” explicitly
requests a build of current main.

Game tasks can edit the selected game, engine and tests. Service, workflow and
configuration changes need normal repository development. Working branches use
`codex/`; publication is serialized and every independent review uses a fresh
read-only invocation without the worker's session.

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
and open it. Write goals in ordinary comments on the manager card and questions on the quick-questions card.
Refine a proposed plan in its card and reply **approve** to start it. The board and
Discord use the same persistent coordinator.
Use `./scripts/multica.ps1 -Action Stop` to close public access.
