# Mobile development setup

YYEngine uses Agent Studio's native Python board and Discord manager. The coordinator token
stays in ignored .env, the Discord token lives in Windows Credential Manager, and Apple
signing settings stay in the existing GitHub testflight-<game> environments.

## Agent Studio and provider logins

Install Python 3.11+, Git, Node.js, LLVM (`winget install --id LLVM.LLVM -e`), and Visual Studio 2022
with Desktop development with C++. Windows builds use Ninja and clang-cl with Visual Studio's
Windows SDK and C++ libraries. `scripts/build.ps1` imports the x64 toolchain environment and
uses a local path alias when a checkout path contains shell metacharacters such as `&`.
The first build preserves an older Visual Studio build tree beside `build/windows` and creates
a Ninja tree; TapDemo is now `build/windows/TapDemo.exe`.
Run scripts/setup.ps1 for pinned CMake/Ninja. Install native Codex and Claude executables and
sign in with codex login and claude auth login. The native manager requires subscription
logins; it does not inherit provider API keys.

Create or reuse the Discord application. Enable Message Content Intent and invite it with
bot and applications.commands scopes. Grant View Channels, Send Messages, Send Messages in
Threads, Create Public Threads, Read Message History, Embed Links and Attach Files in the
private project channel. The configured owner and additional developer IDs are allowlisted.

```powershell
./scripts/studio.ps1 -Action Setup -Guild SERVER_ID -Channel CHANNEL_ID -Owner USER_ID -Token -Activate
./scripts/studio.ps1 -Action Open
npm run doctor
```

-Token prompts locally with hidden input. Never put a token in a command argument or chat.
During migration Setup reuses Discord IDs, both developer IDs, native executable paths and
the token from the existing ignored .env. Later edits to the developer allowlist or executable
paths use %LOCALAPPDATA%/YYEngine/board/manager/config.json. Rerunning Setup preserves those
settings. Registry paths are generated locally; no machine-specific paths are committed.

Setup registers A1 as your primary checkout and creates detached worktrees for A2 and A3.
The native manager reserves A1 for your sessions and uses the other two for approved tasks.
It runs a release of published code and follows new published versions. Activate installs
the YYEngine Project Manager sign-in task. Keep the PC awake and signed in.

The board is http://127.0.0.1:45320 and is bound to loopback. Double-click MobileStudio.cmd
to open it. Discord works independently of browser access and needs no incoming public port.
The board's native unauthenticated local server is not configured for public sharing.

## GitHub coordinator credentials

Double-click Configure.cmd or run scripts/configure.ps1. It preserves unrelated ignored .env
settings and optionally uploads Apple variables/secrets using hidden local prompts.
Set GITHUB_TOKEN with Contents and Actions read/write for YotamHarris/Y-Y. Git publication
also uses your Windows Git credential helper. Providers do not receive the service token.
The repository must permit main publication by the coordinator's Git account; if repository
rules require PRs, publication stops and keeps the candidate checkout.

The hosted Checks workflow validates Python services, the Windows game and the unsigned iOS
simulator. The existing iOS TestFlight workflow continues to wait for the checks at the exact
commit. Main game pushes select affected games; an explicit build can be requested from the
board's Manager tab, Discord's project channel, or scripts/studio.ps1 -Action Build.

The optional Apple wizard needs a setup token with Actions read and Environments read/write;
creating an environment additionally requires Administration read/write. The temporary setup
token is not saved. Existing environment settings and approval rules are preserved.

## 4. Prepare Apple without owning a Mac

Enroll in the Apple Developer Program. In the Apple Developer portal, register an explicit app ID matching your bundle ID. In App Store Connect, create the app and an internal group named **YY Internal**, and add you and your friend as eligible App Store Connect users/testers. Set the app metadata and answer any required export-compliance questions truthfully for the actual app. TapDemo has no custom encryption.

Generate a CSR/private key on Windows using Git for Windows' bundled OpenSSL:

```powershell
./scripts/create-signing.ps1
```

Upload `.yy/signing/distribution.csr` when creating an **Apple Distribution** certificate in the Apple Developer portal. Download the `.cer`, then export the matching key/certificate as a `.p12`:

```powershell
./scripts/create-signing.ps1 -CertificatePath C:\path\distribution.cer
```

Create an App Store distribution provisioning profile for the app ID and this certificate. Download the `.mobileprovision` file. These steps use Windows and Apple's web portals; signing takes place on GitHub's hosted Mac.

Create an App Store Connect team API key with an **App Manager** or **Admin** role, obtain its key ID and issuer ID, and download its `.p8` key. The API key authenticates upload and tester assignment; the distribution certificate/profile signs the app. These are separate credentials.

## 5. Configure the GitHub testing environment

Create a GitHub environment named **testflight-tapdemo** with no required human approval, matching your chosen automatic upload workflow. Set:

| Type | Name | Value |
| --- | --- | --- |
| Variable | `APPLE_TEAM_ID` | Apple development team ID |
| Variable | `BUNDLE_ID` | Your registered bundle ID, also reflected in `config/games.json` |
| Variable | `PROFILE_NAME` | Exact provisioning profile name from the portal |
| Secret | `BUILD_CERTIFICATE_BASE64` | Base64 of the `.p12` file |
| Secret | `P12_PASSWORD` | Password chosen when exporting `.p12` |
| Secret | `BUILD_PROVISION_PROFILE_BASE64` | Base64 of the `.mobileprovision` file |
| Secret | `ASC_KEY_ID` | App Store Connect API key ID |
| Secret | `ASC_ISSUER_ID` | App Store Connect issuer ID |
| Secret | `ASC_KEY_CONTENT` | Complete `.p8` text, including PEM header/footer and newlines |

To encode a file locally, use `[Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\path\file.p12'))` and paste the result only into the appropriate GitHub secret. Do not send it to Discord. Each additional game gets its own `testflight-<game-id>` environment and app/profile. Certificate/API secrets can be inherited from repository secrets where appropriate.

The pipeline waits for checks at the exact commit, selects an installed stable Xcode 26+, creates an ephemeral macOS keychain, generates the Xcode project with CMake, archives/exports with pinned Fastlane, uploads, polls processing, assigns the internal group, and verifies Apple's internal testing state. Debugging symbols, package size, and state/log artifacts are retained for 30 days. Main pushes and explicit build requests use the same workflow; build numbers come from that workflow's monotonically increasing `run_number`. Do not recreate/reset this workflow's numbering for an existing app without adjusting the numbering scheme.

CI prefetches SDL as a commit archive and checks its SHA256 on every cache restore.
Windows checks, simulator checks, and TestFlight share the archive cache within each
runner OS. The unsigned simulator job also caches its configured build after a
successful compile, so later runs reuse CMake's compiler feature checks and SDL
objects. The cache requires the same Xcode, simulator SDK, CMake, runner image,
architecture, and CMake configuration; toolchain changes create a fresh cache.
Game sources still rebuild and the simulator smoke test still runs. Signed device
builds use the source archive cache but configure and archive afresh; signing
material and signed build directories are never cached. The first run for a new
cache key fills it automatically.

The iOS SDL build excludes camera, HIDAPI, controller, haptic, and sensor backends
because the games use touch, rendering, and audio. The signed archive check also
rejects camera/Bluetooth API references before export. If a future game needs
those features, enable its required backend and supply truthful user-facing
privacy purpose strings for that feature before changing this check.

If a binary uploaded and Apple accepted processing but tester delivery needs a
code fix, dispatch **iOS TestFlight** with its original `sha`, selected `game`, a
new `task_id`, and its existing `build_number`. This mode uses the current main
delivery code after its checks pass, preserves the original app commit/build
number, and runs only distribution and readiness verification. It does not sign,
archive, or upload another binary. Leave `build_number` empty for normal builds.

## 6. Complete acceptance on an iPhone

First dispatch **iOS TestFlight** manually with `game=tapdemo`, a published 40-character main SHA, and a unique task ID such as `initial-setup`. Confirm `Verify TestFlight readiness` succeeds and install the build from your internal TestFlight invitation. Hosted macOS minutes/storage and any explicitly configured API usage use their respective billing accounts.

Then start the bot and describe “Award two points for each successful tap and update the scoring test” in the project channel, refine the plan and approve it. Watch the publication/checks/build messages, install the new TestFlight version, and confirm the scoring change. Check touch alignment, pause/resume, audio, safe areas, 60 FPS behavior, memory, battery/thermal behavior, and installation size on your actual iPhone. Desktop/simulator measurements do not establish iPhone performance.
