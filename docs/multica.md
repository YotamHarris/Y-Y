# Multica for YYEngine

Multica v0.6.1 runs in Docker Engine inside the Ubuntu WSL distribution. A native
Windows Multica CLI connects the board to the existing Discord coordinator, which
owns provider runs, checks, PRs, merges, and app delivery. TapDemo has its own project.

## Start and share

Double-click **TapDemoBoard.cmd**, or the **TapDemo board** desktop shortcut. It
starts the server, public tunnel and bot in the background and opens the project.
Your existing bot is reused if already running. From PowerShell:

```powershell
./scripts/multica.ps1 -Action Start -Public
./scripts/multica.ps1 -Action Status
./scripts/multica.ps1 -Action Stop
```

The start command prints an HTTPS Cloudflare Quick Tunnel URL. Share that URL with
your friend. Keep this PC awake and connected. Restarting a stopped tunnel creates
a new address; restarting the script while the tunnel is running reuses it. This
is a temporary sharing setup, not an always-on host. The local app is at
http://localhost:3072. Start without `-Public` to close the tunnel and run locally.

The launcher keeps WSL alive while the server runs. Stop preserves the database
and uploaded files. Start does not upgrade the pinned Multica release. Run Stop
before moving the repository or shutting down the WSL distribution.

## Sign in and connect the repository

New accounts are restricted to allowed or invited emails. Configure your addresses:

```powershell
./scripts/multica.ps1 -Action Configure -AllowedEmails you@example.com,friend@example.com
& ./.yy/multica/bin/multica.exe --profile yyengine login
```

Complete sign-in in the browser opened by the CLI. Until email delivery is
configured, the generated one-time code is printed only in local backend logs.
After requesting a code, use a second terminal to retrieve it:

```powershell
./scripts/multica.ps1 -Action LoginCode -Email you@example.com
```

After browser sign-in finishes and the CLI reports authentication success:

```powershell
./scripts/multica.ps1 -Action Connect
```

Connect creates/reuses the TapDemo project, adds the manager guide and acceptance
cards, and writes `.yy/multica/board.json`. It maps your board account to the first
authorized Discord user by default. Set `YY_MULTICA_OWNER_EMAIL` and
`YY_MULTICA_OWNER_DISCORD_ID` locally when that mapping differs. Restart the bot
once after connecting. Connect disables additional workspace creation.

The same coordinator handles both surfaces, with one serial queue and isolated
worktrees. Leave cards unassigned to native Multica agents. The manager connection
appears through mirrored cards, comments and heartbeat metadata, rather than a
native Multica runtime that could execute the same request separately.

## Request work and follow progress

Create an issue in TapDemo and post one of these commands as a mapped owner:

| Comment | Behavior |
| --- | --- |
| `/yy ask Explain the scoring` | Read-only discussion. |
| `/yy plan Describe the goal` | Read-only proposal of up to eight whole tasks. |
| `/yy approve` | Approve the proposed tasks once; implementation queues automatically. |
| `/yy change provider=codex Add two points per hit` | Implement through existing checks, independent review, CI, merge and TestFlight. |
| `/yy change provider=claude depends=YYEN-12 Update the score display` | Wait for the referenced coordinator task to complete successfully. |
| `/yy build` | Build and distribute current main. |
| `/yy resume Your answer` | Continue a paused task with your answer, or retry after fixing prerequisites. |
| `/yy cancel` | Cancel remaining work and preserve the branch. |

Use a new card for a new task. Card creation, dragging statuses and ordinary
comments do not authorize code execution. Invited observers cannot run commands;
to enable your second developer, add their Multica member UUID and existing
authorized Discord user ID to the `owners` mapping in local `board.json`, then
restart the bot. Commands do not silently switch your chosen provider.

Discord tasks automatically receive matching cards, including their original
thread link. Comments retain stage transitions, results and workflow links.
Metadata shows `pipeline_status`, `waiting_on`, `latest_progress`, `question`,
`pr_url` and `build_url`. The manager guide has a `last_seen` heartbeat while the
connection runs. Sync occurs every ten seconds; outages retain pending updates.

BodySimulation's manager informed approval consumption, dependency ordering,
preserved work, persistent transcripts and explicit owner questions. Approval
atomically creates tasks with stable event IDs, preventing duplicate execution
after restart. Dependencies wait for `completed` or verified `ready`, including
TestFlight acceptance for change tasks. A failed prerequisite does not count as
success. Revisions to an unapproved plan use `/yy resume` with steering; an already
approved plan needs a new card for a new goal. YYEngine retains its own review and
deployment gates; BodySimulation's GPU and landing tools are not required.

The initial project records the completed baseline, the signing failure, live
change/merge acceptance and real iPhone testing. Acceptance tracking cards do not
start work automatically. The failed build's mirrored card can be resumed after
signing is repaired; the coordinator reruns its existing workflow.

Invite your friend from workspace settings. They sign in with their own address
and accept the invitation. Without email delivery, retrieve their requested code
with LoginCode and give it to them privately. Multica's invitation permits their
account even if it was not in AllowedEmails. Workspace members can invoke the
manager when explicitly mapped to an authorized developer. Those commands run
provider tools under your Windows account on this PC.

Provider instructions preserve YYEngine's coordinator boundary: code changes and
checks are allowed; provider-driven pushes, PRs, merges, deployment, and app
distribution are prohibited. This instruction is not an OS sandbox. Review the
provider's changes through the coordinator's existing checks and independent review.

## Local state and maintenance

- `.yy/multica/.env`: generated database/JWT secrets, email allowlist, public URL.
- `.yy/multica/bin/`: checksum-verified Windows CLI and Cloudflare tunnel binaries.
- `.yy/multica/tunnel.json`, `wsl.json`, and tunnel logs: managed background process state.
- `.yy/multica/board.json`: project IDs and board-to-Discord owner mapping.
- `.yy/tasks.sqlite`: shared tasks, transcripts, approval state and separate delivery receipts.
- `%USERPROFILE%/.multica/profiles/yyengine/`: CLI authentication and daemon state.
- WSL Docker volumes `yyengine-multica_pgdata` and `yyengine-multica_uploads`: persistent data.

These paths are outside Git or ignored. No existing bot credentials or Apple
signing material are passed to the server containers. Raw HTTP ports bind only
to loopback. The Nginx gateway carries HTTP and WebSocket traffic on the same
public origin; production login uses random codes, never a fixed development
code. Public signup is off. Deployment telemetry is disabled.

To deliver login codes through Gmail, enable 2-Step Verification and create a
[Google app password](https://myaccount.google.com/apppasswords) named Multica.
Double-click **ConfigureMulticaEmail.cmd** in the repository folder. It opens a
setup window with the hidden password prompt and keeps it open to show the result.
You can also run this from the repository root:

```powershell
./scripts/configure-multica-email.ps1
```

Enter the app password at the hidden local prompt. The command saves Gmail SMTP
settings in the ignored `.yy/multica/.env`, restarts the server while preserving
the public tunnel, and requests a verification email to `yotam.harris@gmail.com`.
Check Inbox and Spam to confirm arrival. It uses `smtp.gmail.com:465` with implicit
TLS and certificate validation. Use `-SenderEmail other@gmail.com` to change the
sender. Do not enter your normal Google password. Google app passwords require
[2-Step Verification](https://support.google.com/accounts/answer/185833) and may
be unavailable for some account/security configurations.

Alternatively, add `RESEND_API_KEY` and a verified `RESEND_FROM_EMAIL` to the
ignored `.yy/multica/.env`, remove `SMTP_HOST` (SMTP takes priority), then rerun
Start. Enter secrets locally, never in Git or a chat. For a stable public address, replace the
Quick Tunnel with a named Cloudflare Tunnel/domain or deploy to an always-on host
using the same origin and proxy configuration.

On a fresh machine, install Ubuntu WSL and Docker Engine/Compose using
[Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/),
then run `./scripts/multica.ps1 -Action Setup`. Setup downloads the pinned Windows
binaries, checks their SHA256 hashes, generates secrets, and pulls server images.
The current Windows CLI download targets x64.

Upstream references: [self-hosting](https://github.com/multica-ai/multica/blob/v0.6.1/SELF_HOSTING.md),
[advanced configuration](https://github.com/multica-ai/multica/blob/v0.6.1/SELF_HOSTING_ADVANCED.md),
and [Quick Tunnel limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
