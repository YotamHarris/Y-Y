# Account and machine setup

Double-click **Configure.cmd** in the repository folder, or run `./scripts/configure.ps1` from PowerShell, for guided, one-at-a-time entry of Discord tokens/IDs, the GitHub token, and provider settings. The launcher uses the repository directory and keeps its window open when the script finishes. Secret prompts are hidden. It updates the ignored `.env`, retains saved values when you press Enter, and preserves unrelated settings. Subscription mode uses native CLI logins; API mode prompts for the provider key explicitly.

The optional Apple section accepts paths to your `.p12`, `.mobileprovision`, and `.p8` files, plus their password/IDs and app settings. It uploads variables and encrypted secrets directly to the game's GitHub environment using GitHub CLI, without saving extra copies locally. Install the CLI if needed with `winget install --id GitHub.cli --exact --source winget` and rerun Configure.cmd. The script also checks standard installation directories, so a fresh terminal is not needed after a standard install. [GitHub secret CLI](https://cli.github.com/manual/gh_secret_set)

Use a setup token restricted to this repository with **Actions read** and **Environments read/write**, or temporarily add Environments read/write to the bot token (which already has Actions access). If the environment does not exist, create `testflight-tapdemo` under repository **Settings → Environments → New environment** first. Automatic environment creation instead needs **Administration read/write** on the setup token. The script preserves existing environment rules and reports the failing step/HTTP status without printing secret values. After fixing permissions you can retry within the same wizard, keeping entered Apple settings in memory. The setup token is not saved; normal bot operation does not need these extra permissions. [GitHub environment creation permissions](https://docs.github.com/en/rest/deployments/environments#create-or-update-an-environment)

Create the accounts, Discord bot invitation/intents, Apple app/profile, and internal tester group using the steps below. The script collects credentials for those existing accounts; it does not enroll accounts or start a build.

## 1. Publish the baseline

The supplied repo started with no commits. Review the files, set your Git identity, and publish the baseline before running agent tasks:

```powershell
git add .
git commit -m "Add YYEngine, TapDemo, and Discord development pipeline"
git push -u origin main
```

Git ignores `.env`, `.yy`, `.tools`, signing materials, and build outputs. No credentials belong in a commit. The unsigned checks can run immediately. The signing workflow initially fails with a named missing setting until Apple configuration is complete.

In GitHub Settings → Actions, enable Actions if disabled. Allow squash merging in the repository's general settings. Branch protection is optional: the bot waits for its checks and review before merging without requiring protection settings or Administration permission.

Open the repository's **Actions → Checks** workflow. The names **automation**, **windows**, and **ios** are its three jobs, defined in [checks.yml](../.github/workflows/checks.yml). They run service tests on Linux, compile and smoke-test the game on Windows, and compile and launch an unsigned iOS simulator app on a hosted Mac. No Apple signing credentials are needed for these checks. The separate **iOS TestFlight** workflow in [ios-testflight.yml](../.github/workflows/ios-testflight.yml) needs the Apple settings below.

Create a fine-grained GitHub token for this repository with Contents read/write, Pull requests read/write, and Actions read/write. Metadata read is included automatically. The bot reads CI results through the Actions API; no Checks token permission is needed. Set `GITHUB_TOKEN` in your local `.env`. The token is used by the coordinator only and is excluded from provider subprocess environments. Existing repository rules still apply if you choose to configure them later.

## 2. Create the Discord bot

In the [Discord Developer Portal](https://discord.com/developers/applications), create an application and bot. Copy the application ID, bot token, server ID, and your two user IDs into `.env` (enable Developer Mode to copy IDs). Set exactly two distinct users in `DISCORD_USER_IDS`.

Enable the **Message Content Intent** on the Bot page, because the bot reads messages in its task threads. Invite it with `bot` and `applications.commands` scopes and these channel permissions: View Channels, Send Messages, Send Messages in Threads, Create Public Threads, and Read Message History. Choose a private server/channel shared by the two of you. The bot additionally enforces the server/user allowlist for every command and message.

Run `npm run register` to register the five guild commands. All command requests are acknowledged before work begins; ongoing messages go into ordinary Discord threads. Agent text is sent with mentions disabled.

## 3. Configure Codex and Claude

Use a native installed CLI executable, not an npm `.cmd` shim. You can set absolute `YY_CODEX_PATH` and `YY_CLAUDE_PATH` in `.env`; this is helpful when Task Scheduler has a different PATH. Sign in manually under the account that will run the bot:

```powershell
codex login
codex login status
claude auth login
claude auth status
```

Default authentication is `subscription` for both. The bot removes API keys and other provider overrides from those subprocess environments, checks the selected login method, and never falls back to API billing. Claude subscription runs use normal print mode, because bare mode does not use the subscription login. An expired login, usage limit, or unresolved tool permission pauses the task for local attention.

For explicit API billing, set the relevant `YY_CODEX_AUTH=api` / `YY_CLAUDE_AUTH=api`, set its API key locally, and configure the CLI's matching API login. Keep it in `.env` or an OS credential mechanism. Codex uses the supported `codex exec --json` interface; Claude uses `claude -p --output-format stream-json`. No browser automation or extracted subscription tokens are used. [Codex authentication](https://learn.chatgpt.com/docs/auth), [Claude authentication](https://code.claude.com/docs/en/authentication)

The initial tool rules allow game-file edits and selected validation commands. They do not bypass provider permission enforcement. If a necessary command is denied, refine the task or deliberately adjust the local adapter rules and rerun tests. Worktrees separate Git changes; they are not a separate OS account or VM. Both Discord users have authority to request code execution on this PC through the configured providers.

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

Then start the bot and request `/change game:tapdemo request:Award two points for each successful tap and update the scoring test`. Watch the PR/checks/merge/build messages, install the new TestFlight version, and confirm the scoring change. Check touch alignment, pause/resume, audio, safe areas, 60 FPS behavior, memory, battery/thermal behavior, and installation size on your actual iPhone. Desktop/simulator measurements do not establish iPhone performance.
