# Working in YYEngine

- Read README.md and docs/setup.md before changing architecture or deployment.
- Game behavior is compiled C++20. Keep deterministic gameplay independent of SDL so it can be tested without a window.
- SDL3 is pinned to a commit in CMakeLists.txt. Do not replace it with a moving branch.
- For game tasks, change only engine/, the selected games/<id>/ project, and tests/. The coordinator rejects changes to other paths. Repo maintenance outside a game task can update those paths when explicitly requested.
- Run npm run check for service changes and scripts/build.ps1 for engine/game changes. Use scripts/build.ps1 -Smoke for rendering or lifecycle changes.
- After completing most tasks, commit and push the task's changes once the required automated validations pass, without asking for confirmation. Leave unrelated work out of the commit; report failed checks or push blockers. The coordinator still owns Git publication for bot/provider tasks.
- Never add credentials, .env, signing material, .yy, or build outputs to Git. Do not use provider processes to push, open PRs, merge, or distribute apps; the coordinator owns those operations.
- Do not claim a TestFlight build is ready until Apple processing, tester assignment, and internal testing state have been verified. Real iPhone performance must be measured on a device.

## Agent Studio and mobile tasks

- Agent Studio is the pinned vendored snapshot in studio/. Its native Python board and manager replace the previous service.
- Managed tasks follow studio/skills/task/SKILL.md. YYEngine rules above take precedence over generic PC-development instructions and documentation requirements in that skill.
- Managed providers commit and return their exact HEAD and evidence. Never invoke studio/fe_sync.py push, tools/mobile/yy_mobile.py build, Git publication, or app delivery from a provider process. The mobile supervisor validates and publishes; GitHub signs and uploads.
- Mobile evidence includes deterministic tests, the player's touch path, visual evidence for rendering changes, and device measurements when iPhone performance is the goal. Mark irrelevant checks not_applicable with a reason; never substitute desktop metrics for device measurements.
- TestFlight delivery is independent of owner acceptance. Request another build from the board's Manager tab, say “make a new TestFlight build” in the Discord project channel, or run scripts/studio.ps1 -Action Build locally.
