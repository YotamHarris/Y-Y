# Working in YYEngine

- Read README.md and docs/setup.md before changing architecture or deployment.
- Game behavior is compiled C++20. Keep deterministic gameplay independent of SDL so it can be tested without a window.
- SDL3 is pinned to a commit in CMakeLists.txt. Do not replace it with a moving branch.
- For game tasks, change only engine/, the selected games/<id>/ project, and tests/. The coordinator rejects changes to other paths. Repo maintenance outside a game task can update those paths when explicitly requested.
- Run npm run check for service changes and scripts/build.ps1 for engine/game changes. Use scripts/build.ps1 -Smoke for rendering or lifecycle changes.
- Never add credentials, .env, signing material, .yy, or build outputs to Git. Do not use provider processes to push, open PRs, merge, or distribute apps; the coordinator owns those operations.
- Do not claim a TestFlight build is ready until Apple processing, tester assignment, and internal testing state have been verified. Real iPhone performance must be measured on a device.
