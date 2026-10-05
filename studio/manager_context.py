"""The task brief a worker starts from (D253): what the task names, gathered by code.

The audit of 2026-10-01/02 found the median worker made 22 tool calls before its
first edit, and most of them fetched what the task's own text already named: the
decision and roadmap entries it cites (`fe_docs.py show`) and where the code it
names lives (`grep`, `ls`, `fe_index.py find`). Each of those calls is a turn, and
every turn re-reads the whole context. The manager gathers them once, here, into
the first prompt, capped so a long task does not carry a long brief.

Fails open: an error leaves that part of the brief out, never the task.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import studio_config

HERE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # the service has no console: hide what it starts
PYTHON = str(Path(sys.executable).with_name("python.exe")) if Path(sys.executable).name.lower() == "pythonw.exe" \
    and Path(sys.executable).with_name("python.exe").exists() else sys.executable
ROOT = studio_config.repo_root()

ENTRY_CAP = 3500       # characters of one entry; the rest is a `fe_docs.py show` away
ENTRIES_CAP = 16000    # characters of every entry together
SYMBOLS_MAX = 14       # names looked up
HITS_PER_NAME = 3
REF = re.compile(r"\b([DVS])(\d{1,4})\b")  # S: the studio's own log (D329)
TICK = re.compile(r"`([^`\s]{3,80})`")
CODE_NAME = re.compile(r"^[A-Za-z_][\w]*(?:(?:::|\.)[A-Za-z_][\w]*)*$")
FILE_NAME = re.compile(r"^[\w./-]+\.(?:rs|slang|py|js|css|toml|md|ps1)(?::\d+)?$")


def entries(body: str) -> str:
    """The text of every D/V entry the body cites, in the order it cites them."""
    try:
        sys.path.insert(0, str(HERE))
        import fe_docs
        d, v = fe_docs.load()
        by_id = {e.ident: (e, e.path) for e in d + v + fe_docs.load_studio()}  # D258, D329
        cache: dict = {}
    except Exception:  # noqa: BLE001 - the brief is optional
        return ""
    seen, out, total = set(), [], 0
    for m in REF.finditer(body):
        ident = m.group(1) + m.group(2)
        if ident in seen or ident not in by_id:
            continue
        seen.add(ident)
        e, path = by_id[ident]
        lines = cache.setdefault(path, fe_docs.read(path))
        text = "\n".join(lines[e.line - 1:e.end]).strip()
        if len(text) > ENTRY_CAP:
            text = text[:ENTRY_CAP].rsplit("\n", 1)[0] + f"\n[... `fe_docs.py show {ident}` for the rest]"
        if total + len(text) > ENTRIES_CAP:
            out.append(f"[{ident} and later: `fe_docs.py show` them; the brief's cap is reached]")
            break
        out.append(text)
        total += len(text)
    return "\n\n".join(out)


def names(body: str) -> tuple[list[str], list[str]]:
    """(code names, file names) in backticks: `Tool::ALL`, `look::PALETTE`, `weapon.rs`."""
    code, files = [], []
    for t in TICK.findall(body):
        t = t.rstrip(".,;:()")
        if FILE_NAME.match(t):
            if t not in files:
                files.append(t)
        # A plain word (`check`, `look`) is a verb or a value, not a name worth a lookup.
        elif CODE_NAME.match(t) and ("::" in t or "." in t or "_" in t or re.search(r"[a-z][A-Z]", t)):
            if t not in code:
                code.append(t)
    return code[:SYMBOLS_MAX], files[:SYMBOLS_MAX]


def symbols(body: str, root: Path = ROOT) -> str:
    """Where the code the body names lives: fe_index find, at most a few hits a name."""
    code, files = names(body)
    out = []
    if code:
        try:
            r = subprocess.run([PYTHON, str(HERE / "fe_index.py"), "find", *code], cwd=root,
                               capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace",
                               creationflags=NO_WINDOW)
            name, hits = None, 0
            for line in r.stdout.splitlines():
                m = re.match(r"\[fe-index\] find '(.+?)' \| (\d+) match", line)
                if m:
                    name, hits = m.group(1), 0
                    continue
                if name and line.startswith("  ") and hits < HITS_PER_NAME:
                    out.append(line.rstrip()[:200])
                    hits += 1
        except Exception:  # noqa: BLE001
            pass
    if files:
        try:
            tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                                     timeout=30, creationflags=NO_WINDOW).stdout.splitlines()
            for f in files:
                base = f.split(":")[0]
                hits = [p for p in tracked if p == base or p.endswith("/" + base)][:HITS_PER_NAME]
                out.append(f"  {f}: " + (", ".join(hits) if hits else "no tracked file"))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(out)


RESUME_CAP = 200_000   # tokens: a Claude session past this starts afresh rather than resumes
TAIL_BYTES = 600_000   # how much of a transcript's end is read for its last turn


def session_context(session: str) -> int | None:
    """The context of a Claude session's last turn (input + cache read + cache write), from
    the end of its transcript, or None when there is no transcript to read."""
    try:
        sys.path.insert(0, str(HERE))
        import fe_tokens
        hits = list(fe_tokens.PROJECTS.glob(f"*/{session}.jsonl")) if session else []
        if not hits:
            return None
        path = max(hits, key=lambda p: p.stat().st_mtime)
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
        import json
        for line in reversed(lines):
            if '"usage"' not in line:
                continue
            try:
                m = (json.loads(line).get("message") or {})
            except ValueError:
                continue
            u = m.get("usage") if m.get("model") != "<synthetic>" else None
            if isinstance(u, dict):
                return sum(int(u.get(k) or 0) for k in
                           ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    except Exception:  # noqa: BLE001 - unknown size: resume as before
        return None
    return None


def fresh_note(size: int, last_result: str, diff_stat: str) -> str:
    """What a worker that starts afresh instead of resuming needs to carry on (D253)."""
    import json
    summary = ""
    try:
        r = json.loads(last_result or "{}")
        summary = "\n".join(str(r.get(k) or "") for k in ("status", "summary", "problem") if r.get(k))
    except ValueError:
        summary = (last_result or "")[:1500]
    return (f"The previous run's session had grown to {size // 1000}k tokens, past the {RESUME_CAP // 1000}k "
            "a resume may carry (D253): every turn re-reads it. So this run starts a new session, and none "
            "of that conversation comes with it. Continue from the checkout: read `git status`, `git log` "
            "and `git diff` before you change anything."
            + (f"\nThe previous run's last result:\n{summary[:1500]}" if summary else "")
            + (f"\nThe checkout against the task's base:\n{diff_stat[-1500:]}" if diff_stat else ""))


def brief(body: str, root: Path = ROOT) -> str:
    """The block the worker's prompt carries after the task, or '' when there is nothing."""
    parts = []
    found = symbols(body, root)
    if found:
        parts.append("Where the code it names is (fe_index find; the spans are origin/main's):\n" + found)
    text = entries(body)
    if text:
        parts.append("The entries it cites:\n\n" + text)
    if not parts:
        return ""
    return ("\n## Gathered for you (D253)\n\nThe manager read these for the task, so do not fetch them "
            "again; `fe_index.py show` an item before you edit it, and read further only what this "
            "does not answer.\n\n" + "\n\n".join(parts) + "\n")
