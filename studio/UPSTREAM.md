# Agent Studio in YYEngine

Source: https://github.com/YotamHarris/agent-studio

Imported commit: `89ded6f2d5ce6b4ebc1c2e389e89c1bf4dbe6175`.

Imported manager fix G9: `8e37ae10beff0c576b3318de4ad9063b6b009353`. This pinned upstream change is applied
to the base snapshot above while preserving YYEngine extensions.
Imported manager fix G10: `d1dd32b687c60ac4ebdb62bf7e8ea934e6c0ccdc` (message splitting and final controls).

This is a vendored snapshot so YYEngine deployments and worker checkouts get the
same reviewed manager and board. There is no dependency on a moving branch.

YYEngine additions are small extension points: the mobile adapter handles trusted
landing, provider environment filtering, explicit builds, and delivery polling;
the registry can live in shared machine state; Discord accepts a configured
developer allowlist. Game-specific code lives in `tools/mobile/yy_mobile.py`.
The brace symbol scanner and line counter also recognize C++ source/header files.
The upstream board UI and manager planning/task conversations are retained.

YYEngine's root AGENTS.md takes precedence over generic PC development rules.
Managed providers never publish or distribute. The supervisor publishes, and
the existing GitHub macOS workflow signs and distributes iOS apps.
