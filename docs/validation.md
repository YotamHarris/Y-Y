# Validation and external acceptance

## Local evidence

- Windows Release compilation using MSVC 19.40 and SDL 3.2.28 pinned to `7f3ae3d57459e59943a4ecfefc8f6277ec6bf540`.
- CTest checks deterministic gameplay, correct scoring/misses, safe-area input mapping, invalid viewports, fixed-step catch-up limits, pause behavior, round completion, and restart.
- TapDemo executed 120 rendered frames with Direct3D 11 and produced a screenshot and telemetry. The executable is approximately 2.36 MB. Short desktop measurements vary with the host/display and do not establish iPhone frame time or memory budgets.
- TypeScript compilation and 28 service/Git tests passed. They cover request/transcript deduplication, durable outbox, restart recovery, authorization, provider authentication isolation/results, game scope, implementation/review order, serialization, stale branches, merge reconciliation, provider limits, repair limits, cancellation, build retries, Apple processing state, required CI checks through the Actions API, shell metacharacters, and secret redaction. A real temporary Git remote/worktree test verifies that the developer checkout stays unchanged and precommitted out-of-scope edits are rejected.
- The credential wizard tests passed under Windows PowerShell 5.1 and PowerShell 7. They use fake credentials to verify environment-file preservation, secret transport through stdin, native stderr handling, and recovery from GitHub permission failures without persisting Apple private keys.
- `npm audit --omit=dev` reports zero vulnerabilities at validation time.
- Ruby 3.3 syntax checks and four distribution behavior tests (ten assertions) passed locally using a portable Ruby runtime kept inside ignored `.tools`, including verification of the Apple JWT signature format.
- Three Python tooling tests passed for game scaffolding, registration/asset separation, and distribution game selection. Both workflows passed actionlint 1.7.7, with external shellcheck/pyflakes integration disabled; Python source passed syntax compilation.

## Included hosted checks

`Checks` runs the service tests, pure Ruby distribution tests, Windows Release/rendering smoke, and an unsigned iOS simulator build/120-frame launch. The TestFlight workflow archives and signs the selected app, verifies package upload/processing/group state, and preserves symbols/logs/state artifacts.

The Ruby tests use a fake Apple client to verify delayed processing, invalid builds, idempotent assignment, and readiness requirements. They do not replace a live Apple API/upload test.

The [initial hosted Checks run](https://github.com/YotamHarris/Y-Y/actions/runs/37123615810) passed automation and the unsigned iOS simulator build/120-frame launch. Windows compiled successfully but failed during the script's CTest path lookup. That lookup has been corrected and the same command-name fallback was verified locally with CTest and the 120-frame rendering smoke run. The new hosted run must confirm the Windows fix.

## Remaining account/device acceptance

Signed TapDemo build 10 [uploaded successfully](https://github.com/YotamHarris/Y-Y/actions/runs/37276849496) and passed Apple processing after the unused camera/Bluetooth backends were excluded. Assignment to YY Internal succeeded. The [latest readiness diagnostics](https://github.com/YotamHarris/Y-Y/actions/runs/37280295605) reported `READY_FOR_BETA_TESTING`, so testing/installation readiness is still unconfirmed. The diagnostic polling run was deliberately cancelled after capturing that state. Owner sign-in to App Store Connect is pending to inspect the tester setup. A complete live game change/PR/merge and real iPhone installation/performance remain acceptance work. Branch protection is optional.

Keep the first live workflow logs and iPhone results as acceptance evidence. Diagnose missing configuration/signing separately from code failures; reply continue on the existing task after fixing prerequisites so the existing build is reconciled rather than manually starting duplicate uploads.

## Conversational manager (2026-10-05)

The comparison used BodySimulation's `manager_talk.py`, `manager_discord.py`,
and decisions D226/D317: planning in conversation, one approval, stale proposal
rejection, replies buffered during turns, separate task threads, live progress,
and read-only questions during other work. YYEngine retains its own scope,
independent review, and delivery checks.

`npm run check` passed 48 service/Git tests. New cases exercise owner prose,
refinement/approval, generated-reply exclusion, persisted migration cursors,
questions that leave task/card state intact, replies during planning, stale
approval rejection, approved task clarification/retry, explicit natural build
requests, and a read-only answer while an implementation is still running.

A real Codex read-only question through the coordinator answered in 11.45 seconds,
describing TapDemo's moving targets, one-point hits, 30-second round, and restart,
and confirmed the question started no edits or build. The running service was
restarted and the manager guide updated. On YYEN-10, an ordinary owner question
was picked up by the running Multica bridge and received the real provider's
answer in comments. Its task stayed read-only, gained no worktree, commit, PR,
or build, and the acceptance card stayed in progress until the coordinator
explicitly recorded the successful check. Discord button clicks and a complete approved live
game-change path still need human acceptance; unit coverage does not establish
those external interactions.

## BodySimulation manager migration (2026-10-05)

The expanded migration ports ongoing planning rounds, two isolated workers,
serial publication, separate quick chat, native Claude session resume,
structured owner choices/forms, task revisions, acceptance distinct from
delivery, global pause/resume/stop/status/models, concise reports and receipt
reconciliation. Routine Status/Cancel buttons were removed from code and 16
existing controls were cleared from eight Discord messages.

`npm run check` passed 60 tests, including slot refill while another worker is
active, pause/stop preservation, exact-commit acceptance, revisions, steering
during a merge, new planning rounds, multi-question answers, session fallback,
reviewer isolation and recovery of a lost Discord send response. Claude's native
subscription authentication was verified locally. These tests do not establish
human Discord button/form acceptance or a new live game change.

Live migration checks: the restarted bot connected, and Discord registration
replaced the old menu with the five global commands. A new meatbag-talk public
thread was created in general (the bot cannot create guild channels), and board
card YYEN-13 provides the same quick-question lane. A real Sonnet board answer
completed in 12.6 seconds; a second question reused both its task and native
Claude session. Replies arrived on the board, with no worktree, PR or build.
A disposable native Codex workspace-write probe changed its one requested file
successfully after matching BodySimulation's automatic approval mode and explicit
Windows sandbox setting. Game validations now request rendering smoke as well.

A real Discord quick-chat answer was delivered in meatbag-talk with zero
components. The approved red-ball worker then successfully made its scoped
rendering edit. Its sandbox could not access the compiler; the worker handoff
now explicitly leaves required build/smoke checks with the coordinator instead
of asking the owner to run them. Its candidate is preserved for trusted
validation. Publication and device acceptance are not claimed by this check.

## Worker versus delivery status (2026-10-05)

The version-display change was merged and uploaded while its GitHub workflow
waited at Distribute to internal testers. No local worker was active. Status
now separates local workers from GitHub/Apple waits and replaces stale CI text
on remote phases. Published planning goals wait in review for owner acceptance.
Live progress cleans archived threads without leaving them open and isolates
each channel failure, so an old thread cannot suppress other task updates.

`npm run check` passed 62 tests. Regression cases cover archived-thread cleanup,
updates surviving a different channel failure, delivery labels without a coding
agent, stable phase timing across polls and parent goals waiting in review.
