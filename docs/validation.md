# Validation and external acceptance

## Local evidence

- Windows Release compilation using MSVC 19.40 and SDL 3.2.28 pinned to `7f3ae3d57459e59943a4ecfefc8f6277ec6bf540`.
- CTest checks deterministic gameplay, correct scoring/misses, safe-area input mapping, invalid viewports, fixed-step catch-up limits, pause behavior, round completion, and restart.
- TapDemo executed 120 rendered frames with Direct3D 11 and produced a screenshot and telemetry. The executable is approximately 2.36 MB. Short desktop measurements vary with the host/display and do not establish iPhone frame time or memory budgets.
- TypeScript compilation and 25 service/Git tests passed. They cover request/transcript deduplication, durable outbox, restart recovery, authorization, provider authentication isolation/results, game scope, implementation/review order, serialization, stale branches, merge reconciliation, provider limits, repair limits, cancellation, build retries, Apple processing state, required CI checks, shell metacharacters, and secret redaction. A real temporary Git remote/worktree test verifies that the developer checkout stays unchanged and precommitted out-of-scope edits are rejected.
- `npm audit --omit=dev` reports zero vulnerabilities at validation time.
- Ruby 3.3 syntax checks and four distribution behavior tests (ten assertions) passed locally using a portable Ruby runtime kept inside ignored `.tools`, including verification of the Apple JWT signature format.
- Three Python tooling tests passed for game scaffolding, registration/asset separation, and distribution game selection. Both workflows passed actionlint 1.7.7, with external shellcheck/pyflakes integration disabled; Python source passed syntax compilation.

## Included hosted checks

`Checks` runs the service tests, pure Ruby distribution tests, Windows Release/rendering smoke, and an unsigned iOS simulator build/120-frame launch. The TestFlight workflow archives and signs the selected app, verifies package upload/processing/group state, and preserves symbols/logs/state artifacts.

The Ruby tests use a fake Apple client to verify delayed processing, invalid builds, idempotent assignment, and readiness requirements. They do not replace a live Apple API/upload test.

## Remaining account/device acceptance

The repository has not been published by this implementation, and no live Discord command, PR merge, macOS build, signed upload, or iPhone install has been claimed. Complete docs/setup.md, run the initial unsigned/signed CI workflows, then request a scoring change through Discord and verify it in TestFlight. Apple account enrollment, app/profile/API-key creation, tester invitations, GitHub protection/credentials, and Discord setup require your accounts.

Keep the first live workflow logs and iPhone results as acceptance evidence. Diagnose missing configuration/signing separately from code failures; use `/status resume:true` after fixing prerequisites so the existing build is reconciled rather than manually starting duplicate uploads.
