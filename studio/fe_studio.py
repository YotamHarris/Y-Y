"""Set a game up to be developed with the studio (S1, S5).

    python studio/fe_studio.py init --name NAME --owner NAME [--board-port P] [--force]
    python studio/fe_studio.py where      # what the studio sees: the game, its config, its data

Run `init` from the game's root once the studio is its submodule at studio/.
It writes what the game needs and leaves anything that already exists alone,
unless --force:

    studio.toml                 the game, as the studio reads it (templates/studio.toml)
    .claude/settings.json       the studio's hooks (merged into an existing file)
    AGENTS.md, CLAUDE.md        the game's rules, importing studio/AGENTS.md
    studio.agents.json          the agent registry: add each checkout to it
    docs/decisions.md, docs/decisions/<category>.md, docs/roadmap.md, docs/research.md
    .gitignore                  the studio's scratch folder

then runs `fe_docs.py build`. Each game is its own instance: its board store,
port, scheduled task and Discord bot are named after it (S12).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

# The studio's hooks (S9, S1). The game adds its own beside them.
HOOKS = {
    "SessionStart": ["fe_sync.py session-start", "fe_board.py open --no-browser --quiet"],
    "UserPromptSubmit": ["fe_sync.py prompt"],
    "PreToolUse": [("Bash|PowerShell", "fe_sync.py pre-bash")],
    "PostToolUse": ["fe_board.py hook-post"],
    "SessionEnd": ["fe_board.py hook-end"],
    "Stop": ["fe_sync.py stop", "fe_board.py hook-stop", "fe_jev.py hook-stop"],
}
TIMEOUTS = {"fe_sync.py session-start": 90, "fe_sync.py prompt": 60, "fe_sync.py pre-bash": 30,
            "fe_sync.py stop": 900, "fe_board.py open --no-browser --quiet": 30}

DECISIONS_FRONT = """# Decisions

The game's decision log: one file per category under [decisions/](decisions/), entries
D1, D2, ..., append-only. A later entry may reverse an earlier one; the **Status:** line
under each heading says which holds. `python studio/fe_docs.py category` lists the
categories, `show D12` prints one entry, `new CATEGORY "Title"` appends the next. The
studio's own rules are its log, `studio/docs/decisions/` (S entries).
"""
ROADMAP = """# Roadmap

The versions, newest last, append-only. Each is `### Vn title (Dnn)` with a **Status:**
line and, while it ships with known gaps, an **Open:** block.
"""
RESEARCH = """# Research

Sources consulted for this game: a stable id, title and author, link, date reviewed,
scope, relevance, limitations and status (candidate, adopted, deferred, superseded).
"""


def write(path: Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        print(f"  kept     {path.as_posix()}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  wrote    {path.as_posix()}")


def hooks(root: Path, studio: str) -> None:
    path = root / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    have = settings.setdefault("hooks", {})
    added = 0
    for event, cmds in HOOKS.items():
        groups = have.setdefault(event, [])
        present = {h.get("command") for g in groups for h in g.get("hooks", [])}
        for c in cmds:
            matcher, c = c if isinstance(c, tuple) else (None, c)
            script, _, args = c.partition(" ")
            command = f'python "$CLAUDE_PROJECT_DIR/{studio}/{script}"' + (f" {args}" if args else "")
            if command in present:
                continue
            hook = {"type": "command", "command": command}
            if c in TIMEOUTS:
                hook["timeout"] = TIMEOUTS[c]
            group = next((g for g in groups if g.get("matcher") == matcher), None)
            if group is None:
                group = {"hooks": []} if matcher is None else {"matcher": matcher, "hooks": []}
                groups.append(group)
            group["hooks"].append(hook)
            added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"  {'merged' if added else 'kept'}   {path.as_posix()} ({added} hook(s) added)")


def cmd_init(args) -> int:
    root = Path.cwd()
    studio = HERE.relative_to(root).as_posix() if HERE.is_relative_to(root) else "studio"
    if studio != "studio":
        print(f"[fe-studio] note: the studio is at {studio}/; the convention is studio/ (hooks and rules name it)")
    print(f"[fe-studio] setting up {args.name} in {root}")
    toml = (TEMPLATES / "studio.toml").read_text(encoding="utf-8")
    write(root / "studio.toml", toml.format(name=args.name, owner=args.owner, board_port=args.board_port), args.force)
    hooks(root, studio)
    game = (TEMPLATES / "AGENTS.game.md").read_text(encoding="utf-8").replace("<!-- fill: GAME NAME -->", args.name)
    write(root / "AGENTS.md", game, args.force)
    write(root / "CLAUDE.md", f"# {args.name} — Claude Code\n\nThe rules are AGENTS.md; this file only imports it.\n\n"
                              "@AGENTS.md\n", args.force)
    write(root / "studio.agents.json", json.dumps({"remote": "origin", "branch": "main", "agents": {}}, indent=2) + "\n",
          args.force)
    write(root / "docs" / "decisions.md", DECISIONS_FRONT, args.force)
    for slug in ("design", "engine", "process"):
        write(root / "docs" / "decisions" / f"{slug}.md",
              f"# Decisions — {slug.title()}\n\nAppend-only. The **Status:** line under each heading says "
              "which entry holds.\n\n---\n", args.force)
    write(root / "docs" / "roadmap.md", ROADMAP, args.force)
    write(root / "docs" / "research.md", RESEARCH, args.force)
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if ".studio/" not in lines:
        ignore.write_text("\n".join(lines + [".studio/", ".claude/settings.local.json"]) + "\n", encoding="utf-8")
        print(f"  merged   .gitignore")
    subprocess.run([sys.executable, str(HERE / "fe_docs.py"), "build"], cwd=root)
    print("[fe-studio] next: fill the <!-- fill: --> slots in AGENTS.md, add each checkout to "
          "studio.agents.json and run `python studio/fe_sync.py init --agent NAME` in it, then "
          f"`python {studio}/fe_board.py open`; for the manager, `python {studio}/fe_manager.py setup`.")
    return 0


def cmd_where() -> int:
    sys.path.insert(0, str(HERE))
    import studio_config as sc
    for label, value in (("game", sc.repo_root()), ("name", sc.name()), ("owner", sc.owner()),
                         ("studio", sc.studio_dir()), ("game tools", sc.game_tools_dir()),
                         ("data", sc.data_dir()), ("agents", sc.agents_path()),
                         ("adapters", ", ".join(f"{k}={v}" for k, v in sc.get("adapters", {}).items()) or "none")):
        print(f"{label:11} {value}")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="fe_studio.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="set this game up for the studio")
    init.add_argument("--name", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--board-port", type=int, default=45300)
    init.add_argument("--force", action="store_true", help="overwrite files that exist")
    sub.add_parser("where", help="what the studio sees")
    args = ap.parse_args(argv)
    return cmd_init(args) if args.cmd == "init" else cmd_where()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
