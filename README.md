# YYEngine

A C++20/SDL3 mobile game engine with Agent Studio's native board and Discord project manager.
TapDemo runs on Windows for development and iOS for internal testing. Agent Studio plans goals,
runs isolated Codex/Claude workers, and presents finished commits for owner acceptance.
YYEngine's mobile coordinator validates and publishes; the existing hosted Mac workflow signs,
uploads, waits for Apple processing, assigns internal testers and verifies testing readiness.

## Start development

Install Python 3.11+, Node.js, Git and Visual Studio 2022 with Desktop development with C++.

```powershell
./scripts/setup.ps1
./scripts/build.ps1 -Smoke
./build/windows/Release/TapDemo.exe
```

Gameplay stays deterministic and independent of SDL. SDL3 stays pinned in CMakeLists.txt.

## Board and Discord manager

Agent Studio is vendored at a fixed upstream commit; see [provenance](studio/UPSTREAM.md).
Configure native Codex and Claude subscription logins, then:

```powershell
./scripts/studio.ps1 -Action Setup -Guild YOUR_SERVER_ID -Channel YOUR_CHANNEL_ID -Owner YOUR_USER_ID -Token -Activate
./scripts/studio.ps1 -Action Open
```

Existing local Discord credentials and both developer IDs are reused during migration.
The token moves to Windows Credential Manager; manager IDs and the shared checkout registry
live under `%LOCALAPPDATA%/YYEngine`. Double-click **MobileStudio.cmd** for the board,
or open http://127.0.0.1:45320. It is local to this PC. Discord uses an outbound Gateway connection.

Write a goal in the Discord project channel and discuss it in its planning thread.
**Approve plan** creates tasks; the manager runs up to two isolated workers. **Accept**
the published result to unlock dependencies, or **Request changes** to resume it.
The board's Manager tab, tasks, thoughts, threads, transcripts, and Agents view are the
native Agent Studio views. `/status`, `/models`, `/pause`, `/resume`, `/stop`, and `/ask`
are registered by the native bot. Quick questions in meatbag-talk stay read-only.

Game workers may edit engine/, the game selected by mobile.game in studio.toml, and tests/.
They commit and return. The mobile supervisor checks scope, builds and smoke-tests the
candidate through trusted scripts, rebases and revalidates if main changed, then publishes
without force. Agent Studio's worker self-review and structured evidence remain in place.
Acceptance and TestFlight delivery are separate.

## TestFlight

Follow [setup](docs/setup.md) for GitHub and Apple credentials. Signing credentials remain
in GitHub environments; build numbering, processing, tester assignment and readiness checks
remain in the existing iOS TestFlight workflow.

Request a build from **Build for TestFlight** in the board's Manager tab, say
**make a new TestFlight build** in the Discord project channel, or run:

```powershell
./scripts/studio.ps1 -Action Build
./scripts/studio.ps1 -Action Status
```

A normal game push builds affected games automatically. Explicit requests build current main.
The manager tracks each exact commit and announces ready only after the workflow's
**Verify TestFlight readiness** step and the selected game job succeed. Ambiguous dispatches
are reconciled by their persisted request IDs. Failed delivery does not rerun implementation.
For a retry or reconciliation of an already uploaded binary, use the existing workflow's
build_number input as described in setup.

## Checks and layout

```powershell
npm run check
./tests/configure_test.ps1
./scripts/build.ps1 -Smoke
```

- engine/, games/, tests/: deterministic game code, SDL platform services and tests.
- studio/: pinned Agent Studio board, manager, providers and process tools.
- tools/mobile/: trusted mobile landing and TestFlight adapters.
- studio.toml: selected game, mobile evidence requirements, ports and adapter configuration.
- platform/ios/, fastlane/, .github/workflows/: iOS packaging, signing and distribution.

To add another app, run python scripts/new-game.py, create its matching Apple app/profile and
TestFlight environment, and select its ID in studio.toml before assigning manager tasks.
Give it equivalent game and simulator acceptance coverage. Android and App Store production
submission remain outside the current pipeline.

See [architecture](docs/architecture.md) and [validation](docs/validation.md).
