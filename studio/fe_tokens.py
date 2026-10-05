#!/usr/bin/env python3
"""Where the agents' input tokens go: a tally of Claude Code transcripts.

D95 was decided on this measurement: over 30 sessions ~94 % of all tool
output was searching and reading code, and `sed -n` windows paged around a
grep hit were 35 % of it. This tool re-runs that tally so the effect of the
symbol index (`fe_index.py`) can be checked rather than assumed.

    fe_tokens.py [--since DATE] [--until DATE] [--last N] [--all-projects]
                                  one tally over the matching sessions
    fe_tokens.py --split DATE [--since DATE] [--until DATE]
                                  the same tally before and after DATE,
                                  side by side. D95 landed on 2026-09-24,
                                  mid-day, and that day's sessions built it:
                                  judge it with --split 2026-09-25
    fe_tokens.py ... --top N      also list the N files most often paged

Sessions are read from ~/.claude/projects/<slug>/*.jsonl and their
`subagents/` transcripts; by default only projects whose folder names
BodySimulation (both checkouts and their worktrees). A session belongs to the
side of --split its first record falls on. Characters are the text of each
tool result as the model received it; tokens are estimated at 4 characters
each. Images (screenshots) are counted but carry no characters.

What to look at when judging D95:
  - `sed/cat windows` and `grep in Bash` shares should fall, `fe_index`
    should rise;
  - `search+read per edit` (search and read characters per Edit/Write call)
    normalises for how much work a session did;
  - `fe_index` calls by subcommand show whether agents use it at all.
Codex sessions are not read (their transcripts have another format).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio_config  # noqa: E402  ([usage] in studio.toml: which projects, the game's commands)

TAG = "[fe-tokens]"
# The transcripts read by default: projects whose folder names this (every checkout and worktree).
MATCH = studio_config.get("usage.transcript_match") or studio_config.name()
CODE_EXT = studio_config.get("usage.code_ext", [])   # the game's source languages, before the studio's
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"

# Bash command classes, first match wins. The order matters: a command that
# runs the index or the docs tool counts as that even when it pipes to head.
BASH_CLASSES = [
    ("fe_index", re.compile(r"\bfe_index\.py\b")),
    ("fe_docs", re.compile(r"\bfe_docs\.py\b")),
    *((k, re.compile(v)) for k, v in studio_config.get("usage.bash_classes", [])),  # the game's: build, app control
    ("git", re.compile(r"^\s*(cd [^;&|]+(&&|;)\s*)?git\b")),
    ("sed/cat windows", re.compile(r"\bsed\s+-n\s+['\"]?\d+,\d+p|(^|[;&|]\s*|\s)(cat|head|tail|awk|less|more)\s+[^|]*\.\w+")),
    ("grep in Bash", re.compile(r"(^|[;&|(]\s*|\s)(grep|rg|egrep|findstr|ag)\s")),
    ("other", re.compile(r"")),
]
PS_CLASSES = [
    ("fe_index", re.compile(r"\bfe_index\.py\b")),
    ("sed/cat windows", re.compile(r"\bGet-Content\b|\bgc\s|\bcat\s", re.I)),
    ("grep in Bash", re.compile(r"\bSelect-String\b|\bsls\s|\brg\s|\bfindstr\b", re.I)),
    ("other", re.compile(r"")),
]
SEARCH = {"Grep", "Glob", "Read", "sed/cat windows", "grep in Bash", "fe_index", "fe_docs"}
FILE_ARG = re.compile(r"([\w./\\-]+\.(?:" + "|".join([*CODE_EXT, "md", "py", "toml", "ps1", "json"]) + r"))\b")


def result_text(content) -> tuple[int, int]:
    """(characters, images) of a tool_result's content."""
    if isinstance(content, str):
        return len(content), 0
    chars = imgs = 0
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            chars += len(b.get("text", ""))
        elif b.get("type") == "image":
            imgs += 1
    return chars, imgs


def classify(name: str, inp: dict) -> str:
    if name == "Bash":
        cmd = inp.get("command", "")
        return next(k for k, pat in BASH_CLASSES if pat.search(cmd))
    if name == "PowerShell":
        cmd = inp.get("command", "")
        return next(k for k, pat in PS_CLASSES if pat.search(cmd))
    if name.startswith("mcp__"):
        return "mcp tools"
    return name


FE_INDEX_SUBS = re.compile(r"\b(find|show|outline|refs|where|map|check)\b")


def fe_index_sub(cmd: str) -> str:
    """The first subcommand after the tool is named (`fe_index.py show`, or
    `T=.../fe_index.py; python $T show`)."""
    m = FE_INDEX_SUBS.search(cmd, cmd.find("fe_index.py"))
    return m.group(1) if m else "?"


class Tally:
    def __init__(self):
        self.sessions = 0
        self.calls = Counter()
        self.chars = Counter()
        self.images = 0
        self.edits = 0
        self.fe_index = Counter()
        self.paged = Counter()          # file -> chars returned by windows/dumps/reads
        self.read_full = self.read_ranged = 0

    def add_session(self, paths: list[Path]) -> None:
        self.sessions += 1
        for path in paths:
            pending: dict[str, tuple[str, dict]] = {}
            try:
                fh = path.open(encoding="utf-8")
            except OSError:
                continue
            with fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    msg = r.get("message")
                    if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                        continue
                    for b in msg["content"]:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            pending[b.get("id", "")] = (b.get("name", "?"), b.get("input") or {})
                        elif b.get("type") == "tool_result":
                            name, inp = pending.pop(b.get("tool_use_id", ""), ("?", {}))
                            self.add_call(name, inp, b.get("content"))

    def add_call(self, name: str, inp: dict, content) -> None:
        chars, imgs = result_text(content)
        self.images += imgs
        kind = classify(name, inp)
        self.calls[kind] += 1
        self.chars[kind] += chars
        if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            self.edits += 1
        if kind == "fe_index":
            self.fe_index[fe_index_sub(inp.get("command", ""))] += 1
        if name == "Read" and not imgs:
            if inp.get("offset") or inp.get("limit"):
                self.read_ranged += 1
            else:
                self.read_full += 1
            self.paged[Path(str(inp.get("file_path", "?"))).name] += chars
        elif kind == "sed/cat windows":
            for f in set(FILE_ARG.findall(inp.get("command", ""))):
                self.paged[Path(f.replace("\\", "/")).name] += chars

    # ---- derived
    @property
    def total(self) -> int:
        return sum(self.chars.values())

    def share(self, kind: str) -> float:
        return 100.0 * self.chars[kind] / self.total if self.total else 0.0

    def search_total(self) -> int:
        return sum(v for k, v in self.chars.items() if k in SEARCH)


def sessions(all_projects: bool) -> list[tuple[str, list[Path]]]:
    """[(first timestamp, [transcript, subagent transcripts...])] oldest first."""
    out = []
    if not PROJECTS.is_dir():
        return out
    for proj in PROJECTS.iterdir():
        if not proj.is_dir() or (not all_projects and MATCH not in proj.name):
            continue
        for main in proj.glob("*.jsonl"):
            first = ""
            try:
                with main.open(encoding="utf-8") as fh:
                    for line in fh:
                        m = re.search(r'"timestamp"\s*:\s*"([^"]+)"', line)
                        if m:
                            first = m.group(1)
                            break
            except OSError:
                continue
            if not first:
                continue
            subs = sorted((proj / main.stem / "subagents").glob("*.jsonl"))
            out.append((first, [main] + subs))
    out.sort(key=lambda s: s[0])
    return out


def block_text(content) -> str:
    """The text of a tool_result's content (images dropped)."""
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")


def turns(path: Path) -> list[dict]:
    """A main transcript's turns, oldest first (D161: Jev's DoD check and its
    evals read what an agent reported and the evidence it gathered).

    A turn starts at a prompt the user typed and holds the tool calls that
    followed it (`name`, `input`, the result's text clipped to 4000
    characters), the assistant's text blocks (`texts`) and `final`, the text
    written after the turn's last tool call: its report. Sidechain records,
    hook attachments and tool results never start a turn."""
    out: list[dict] = []
    cur: dict | None = None
    after: list[str] = []
    pending: dict[str, dict] = {}
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("isSidechain") or r.get("isMeta"):
                continue
            msg = r.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            blocks = [b for b in blocks if isinstance(b, dict)]
            if r.get("type") == "user":
                results = [b for b in blocks if b.get("type") == "tool_result"]
                for b in results:
                    call = pending.pop(b.get("tool_use_id", ""), None)
                    if call is not None:
                        call["result"] = block_text(b.get("content"))[:4000]
                if results:
                    continue
                if cur is not None:
                    cur["final"] = "\n\n".join(after)
                text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                cur = {"at": r.get("timestamp", ""), "prompt": text, "calls": [], "texts": [], "final": ""}
                after = []
                out.append(cur)
            elif r.get("type") == "assistant" and cur is not None:
                for b in blocks:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        cur["texts"].append(b["text"])
                        after.append(b["text"])
                    elif b.get("type") == "tool_use":
                        call = {"name": b.get("name", "?"), "input": b.get("input") or {}, "result": ""}
                        cur["calls"].append(call)
                        pending[b.get("id", "")] = call
                        after = []
    if cur is not None:
        cur["final"] = "\n\n".join(after)
    return out


def pct(n: int, total: int) -> str:
    return f"{100.0 * n / total:5.1f}%" if total else "    -"


def kb(n: int) -> str:
    return f"{n / 1000:8.0f}K"


def report(cols: list[tuple[str, Tally]], top: int) -> None:
    kinds = sorted({k for _, t in cols for k in t.calls}, key=lambda k: -sum(t.chars[k] for _, t in cols))
    head = "".join(f" | {name:>26}" for name, _ in cols)
    print(f"  {'':<18}{head}")
    print(f"  {'':<18}" + "".join(f" | {'calls':>7} {'chars':>9} {'share':>6}" for _ in cols))
    for k in kinds:
        row = "".join(f" | {t.calls[k]:>7} {kb(t.chars[k])} {pct(t.chars[k], t.total)}" for _, t in cols)
        print(f"  {k:<18}{row}")
    print(f"  {'total':<18}" + "".join(f" | {sum(t.calls.values()):>7} {kb(t.total)} {'':>6}" for _, t in cols))
    print()
    lines = [
        ("sessions", lambda t: f"{t.sessions}"),
        ("tokens (~chars/4)", lambda t: f"{t.total / 4 / 1e6:.2f} M"),
        ("search+read share", lambda t: pct(t.search_total(), t.total).strip()),
        ("per session", lambda t: f"{t.total / t.sessions / 1000:.0f}K chars" if t.sessions else "-"),
        ("edits (Edit/Write)", lambda t: f"{t.edits}"),
        ("search+read per edit", lambda t: f"{t.search_total() / t.edits / 1000:.1f}K chars" if t.edits else "-"),
        ("Read full / ranged", lambda t: f"{t.read_full} / {t.read_ranged}"),
        ("images", lambda t: f"{t.images}"),
        ("fe_index calls", lambda t: ", ".join(f"{k} {v}" for k, v in t.fe_index.most_common()) or "none"),
    ]
    for label, fn in lines:
        print(f"  {label:<22}" + "".join(f" | {fn(t):>26}" for _, t in cols))
    if top:
        for name, t in cols:
            print(f"\n  most paged ({name}): chars returned by Read, sed/cat windows and dumps")
            for f, n in t.paged.most_common(top):
                print(f"    {f:<34} {kb(n)}  {pct(n, t.total)}")


def main(argv: list[str]) -> int:
    since = until = split = None
    last = top = 0
    all_projects = False
    it = iter(argv[1:])
    for a in it:
        if a == "--since":
            since = next(it, "")
        elif a == "--until":
            until = next(it, "")
        elif a == "--split":
            split = next(it, "")
        elif a == "--last":
            last = int(next(it, "0"))
        elif a == "--top":
            top = int(next(it, "10"))
        elif a == "--all-projects":
            all_projects = True
        else:
            print(__doc__)
            return 2
    ss = [s for s in sessions(all_projects)
          if (not since or s[0] >= since) and (not until or s[0] < until)]
    if last:
        ss = ss[-last:]
    if not ss:
        print(f"{TAG} no sessions match")
        return 1
    if split:
        before, after = Tally(), Tally()
        for first, paths in ss:
            (before if first < split else after).add_session(paths)
        cols = [(f"before {split}", before), (f"from {split}", after)]
    else:
        t = Tally()
        for _, paths in ss:
            t.add_session(paths)
        cols = [(f"{ss[0][0][:10]}..{ss[-1][0][:10]}", t)]
    print(f"{TAG} {len(ss)} sessions | {PROJECTS}" + ("" if all_projects else f" ({MATCH} projects)"))
    report(cols, top)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
