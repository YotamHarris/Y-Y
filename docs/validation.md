# Validation and acceptance

## Agent Studio mobile migration (2026-10-05)

Agent Studio was imported at 89ded6f2d5ce6b4ebc1c2e389e89c1bf4dbe6175.
The Python board and Discord manager replace the old board, TypeScript service and Docker/tunnel
launchers. The mobile adapter retains trusted game validation and the existing iOS pipeline.

npm run check passed 35 Python tests plus the native board selftest.
The service check runs the build/signing/tooling tests, native manager integration tests and
Agent Studio's board selftest in an isolated registry. Mobile cases cover planner conversations,
approval deduplication, acceptance requiring publication, developer allowlists, scope and secret
rejection, trusted coordinator landing, explicit build requests, lost-dispatch recovery,
exact-commit run selection, and readiness requiring the selected game's verification step.

The imported test_manager.py suite also contains BodySimulation GPU/control tests whose fe.py
and fe_gpu modules are not distributed by Agent Studio. Its Rust health/line-count tests require
BodySimulation configuration. Those PC-specific suites are retained with the upstream snapshot;
YYEngine's required check uses its own native manager integration coverage and board selftest.

The credential setup tests passed. scripts/build.ps1 -Smoke passed the Windows Release build,
C++ tests and 120-frame smoke (mean 16.46 ms, worst 16.92 ms; desktop measurements only).
Native manager doctor verified local Discord configuration and both subscription logins.
The live native manager connected to Discord and served its published release. A Linux CI
failure exposed Windows-only fixture path handling; normalization was fixed, and the board
selftest also passed under Ubuntu WSL. Selftests run in a child process so background readers
exit before Windows removes their fixture databases.

Hosted signed iOS builds and real iPhone performance cannot be verified by local service tests.
The existing signing workflow and Fastlane readiness verification have not been replaced.
A live owner-approved game change and its installation on an iPhone remain end-to-end acceptance.

## Existing iOS delivery evidence

Unsigned Windows and simulator checks previously passed. Signed build 10 uploaded and passed
Apple processing; assignment to YY Internal succeeded. The last recorded Apple diagnostic was
READY_FOR_BETA_TESTING, so internal testing readiness and installation still need verification.
This migration does not claim that an existing or new TestFlight build is ready.
