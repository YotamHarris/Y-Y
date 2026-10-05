#!/usr/bin/env python3
"""Find code by name and print only the part you need.

Agents were paying most of their input tokens to *find* code: a grep, then
`sed -n` windows guessed around the hit until the function showed up. This
tool keeps a symbol index of the tracked sources (Rust, Slang, Python, the
board UI's JavaScript, the Markdown docs, the body TOML files) so one call answers "where is X, lines
A-B" and a second prints exactly that span.

    fe_index.py find QUERY [--kind K] [--tests] [--regex] [-n N]
                                  symbols whose name contains QUERY
                                  (`Renderer::draw`, `csSurface`, `Phase 3`)
    fe_index.py show SYMBOL...    print exactly that item: a name, a
                                  qualified name, `path:L1-L2`, `path#Heading`
                                  or a history entry `D64` / `V58`
          [--sig]                 only its doc comment and signature
          [--max N]               cap the lines (default 400, 0 = all)
          [--in PATH]             pick among same-named symbols by file
          [-n]                    number the lines
    fe_index.py outline FILE [SYMBOL]
                                  the file's items with line spans; big
                                  impls/structs collapse unless named
    fe_index.py refs NAME [--lines] [--docs] [--comments]
                                  uses of NAME grouped by the symbol they sit in
    fe_index.py where PATH:LINE   the innermost symbol containing that line
    fe_index.py map [DIR]         one line per indexed file
    fe_index.py check             audit the index against independent counts

FILE and PATH may be any unique suffix (`main.rs`, `surface.slang`). The
index lives in engine/artifacts/index.json, one per checkout. Every call
re-parses only files whose size or mtime changed, so it is never stale and
never needs a build step.

Kinds: fn, struct, enum, union, trait, impl, mod, const, static, type,
macro, entry (a Slang `[shader(...)]` function), var (a Slang global),
import, class, section (a Markdown heading), table (a TOML header), key (a
JS object literal's key: `S.docs`), listener (a JS `addEventListener`:
`document.click`).
"""

from __future__ import annotations

import ast
import bisect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TAG = "[fe-index]"

HERE = Path(__file__).resolve().parent      # the studio's tools
sys.path.insert(0, str(HERE))
import studio_config  # noqa: E402  (the checkout, its scratch folder, [index] in studio.toml)

REPO = studio_config.repo_root()            # checkout root
CACHE = studio_config.artifacts_dir() / "index.json"
_self = Path(__file__).stat()
VERSION = f"1:{_self.st_mtime_ns}:{_self.st_size}"    # editing the parser drops the cache

EXCLUDE = (
    re.compile(r"(^|/)Cargo\.lock$"),
    re.compile(r"\.patch$"),
    *(re.compile("^" + re.escape(studio_config.get(f"docs.{k}", d)) + "$")   # generated
      for k, d in (("decisions_summary", "docs/decisions-summary.md"), ("roadmap_summary", "docs/roadmap-summary.md"))),
    *(re.compile(x) for x in studio_config.get("index.exclude", [])),      # the game's own (vendored sources, ...)
)
# Where crates live: every Cargo.toml under these folders is a crate whose roots `check` follows
# `mod` from ([index] cargo_roots; the whole checkout by default).
CARGO_ROOTS = studio_config.get("index.cargo_roots", ["."])
# C (the Box3D shim) goes through the Slang scanner: the same brace grammar,
# with preprocessor lines masked.
LANG = {".rs": "rust", ".slang": "slang", ".c": "slang", ".cpp": "slang", ".hpp": "slang", ".h": "slang", ".py": "python", ".md": "md", ".toml": "toml",
        ".js": "js", ".mjs": "js"}

COLLAPSE = 12          # containers with more members than this collapse in outline
SIG_WIDTH = 120

# A symbol is a list, to keep the cache small:
# [kind, name, qual, line, start, end, sig, flags, depth]
#   line  the line of the item keyword (1-based)
#   start the first line of its doc comment / attributes
#   end   its last line
K, NAME, QUAL, LINE, START, END, SIG, FLAGS, DEPTH = range(9)


# --------------------------------------------------------------------------
# Masking: blank comments (and optionally strings) keeping offsets and lines.

RUST_TOKEN = re.compile(
    r"""//[^\n]*"""
    r"""|/\*"""
    r"""|(?<![\w])b?r(\#*)\""""
    r"""|b?"(?:[^"\\]|\\.)*\""""
    r"""|b?'(?:[^'\\\n]|\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}|.))'""",
    re.S,
)
SLANG_TOKEN = re.compile(
    r"""//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|^[ \t]*\#[^\n]*""", re.S | re.M)
BLANK = re.compile(r"[^\n]")


def blank(s: str) -> str:
    return BLANK.sub(" ", s)


def mask(text: str, lang: str) -> tuple[str, str]:
    """(code, nocomment): code has comments and strings blanked, nocomment
    only comments. Both keep every offset and newline of `text`."""
    if lang == "js":
        return mask_js(text)
    code: list[str] = []
    noc: list[str] = []
    pos = 0
    n = len(text)
    tok = RUST_TOKEN if lang == "rust" else SLANG_TOKEN
    while True:
        m = tok.search(text, pos)
        if not m:
            break
        a = m.start()
        code.append(text[pos:a])
        noc.append(text[pos:a])
        g = m.group(0)
        if lang == "rust" and g == "/*":
            depth, j = 1, a + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            seg = text[a:j]
            code.append(blank(seg))
            noc.append(blank(seg))
            pos = j
            continue
        if lang == "rust" and m.group(1) is not None:
            close = '"' + m.group(1)
            j = text.find(close, m.end())
            j = n if j < 0 else j + len(close)
            seg = text[a:j]
            code.append(blank(seg))
            noc.append(seg)
            pos = j
            continue
        if g.startswith("/") or g.lstrip().startswith("#"):
            code.append(blank(g))
            noc.append(blank(g))
        else:
            code.append(blank(g))
            noc.append(g)
        pos = m.end()
    code.append(text[pos:])
    noc.append(text[pos:])
    return "".join(code), "".join(noc)


# --------------------------------------------------------------------------
# Brace-language item scanner (Rust and Slang share it).

def match_brace(code: str, i: int) -> int:
    """Index of the `}` matching the `{` at i (len(code)-1 when unbalanced)."""
    depth = 0
    for m in BRACES.finditer(code, i):
        if m.group(0) == "{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.start()
    return len(code) - 1


BRACES = re.compile(r"[{}]")
STOP = re.compile(r"[()\[\]{};=<>]")


def statements(code: str, lo: int, hi: int):
    """Yield (start, header_end, body, end) for each statement in code[lo:hi].
    body is (open, close) of the item's brace block or None; header_end is
    where the header stops (the `{` or `;`); end is one past the statement."""
    i = lo
    while i < hi:
        while i < hi and code[i] in " \t\r\n;":
            i += 1
        if i >= hi:
            return
        s = i
        depth = 0
        # Generic brackets before any `=`: an `=` inside them is an
        # associated type binding (`impl Iterator<Item = T> {`), not an
        # initialiser, so the `{` after them still opens the item's body.
        angle = 0
        eq = False
        body = None
        header_end = None
        j = i
        while True:
            m = STOP.search(code, j, hi)
            if not m:
                j = hi
                header_end = header_end if header_end is not None else hi
                break
            c = m.group(0)
            j = m.start()
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c == "<":
                if not eq and depth == 0:
                    angle += 1
            elif c == ">":
                if not eq and depth == 0 and code[j - 1:j] not in "-=":
                    angle = max(0, angle - 1)
            elif depth == 0 and angle == 0 and c == "=":
                nxt = code[j + 1:j + 2]
                prv = code[j - 1:j]
                if nxt not in "=>" and prv not in "=!<>":
                    eq = True
            elif depth == 0 and c == ";":
                if header_end is None:
                    header_end = j
                j += 1
                break
            elif c == "{":
                k = match_brace(code, j)
                if depth == 0 and not eq:
                    header_end = j
                    body = (j, k)
                    j = k + 1
                    break
                j = k                       # an expression block: skip it
            elif c == "}":                  # stray close: end of our range
                header_end = header_end if header_end is not None else j
                j += 1                      # malformed/conflicted input must advance
                break
            j += 1
        yield s, header_end, body, j
        i = j


ATTR_RUST = re.compile(r"\#!?\[")
WS = re.compile(r"\s+")
VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
RUST_FN = re.compile(
    rf"^{VIS}(?:default\s+)?(?:(?:const|async|unsafe|extern(?:\s+\"[^\"]*\")?)\s+)*fn\s+([A-Za-z_]\w*)")
RUST_TYPE = re.compile(rf"^{VIS}(?:unsafe\s+|auto\s+)*(struct|enum|union|trait)\s+([A-Za-z_]\w*)")
RUST_IMPL = re.compile(r"^(?:unsafe\s+)?impl\b(.*)$", re.S)
RUST_MOD = re.compile(rf"^{VIS}mod\s+([A-Za-z_]\w*)")
RUST_CONST = re.compile(rf"^{VIS}(const|static)\s+(?:mut\s+)?([A-Za-z_]\w*)")
RUST_ALIAS = re.compile(rf"^{VIS}type\s+([A-Za-z_]\w*)")
RUST_MACRO = re.compile(r"^macro_rules!\s*([A-Za-z_]\w*)")
# An item-position macro call with a brace body (`gpu_struct! { struct ... }`,
# `thread_local! { static ... }`): the items it wraps are indexed as items.
RUST_INVOKE = re.compile(r"^(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*!$")
RUST_EXTERN = re.compile(r"^(?:unsafe\s+)?extern(?:\s+\"[^\"]*\")?\s*$")


def strip_attrs(header: str, pat: re.Pattern) -> tuple[str, str]:
    """(attributes, rest): peel leading `#[...]` / `[...]` groups off."""
    attrs = []
    h = header.lstrip()
    while True:
        m = pat.match(h)
        if not m:
            break
        depth, j = 0, h.index("[")
        while j < len(h):
            if h[j] == "[":
                depth += 1
            elif h[j] == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        attrs.append(h[:j + 1])
        h = h[j + 1:].lstrip()
    return " ".join(attrs), h


def strip_generics(s: str) -> str:
    s = s.replace("->", "\x00")
    out, depth = [], 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).replace("\x00", "->")


def impl_target(rest: str) -> tuple[str, str]:
    """('Trait for Type' display, 'Type' qualifier) from what follows `impl`."""
    r = strip_generics(rest)
    r = re.split(r"\bwhere\b", r)[0]
    r = WS.sub(" ", r).strip()
    target = r.split(" for ", 1)[1] if " for " in r else r
    target = target.replace("&", " ").replace("mut ", " ").replace("dyn ", " ").strip()
    base = re.findall(r"[A-Za-z_]\w*", target)
    return r, (base[-1] if base else target)


def sig_of(noc: str, s: int, e: int) -> str:
    return WS.sub(" ", noc[s:e]).strip()[:SIG_WIDTH * 2]


class Scanner:
    def __init__(self, text: str, lang: str):
        self.text = text
        self.lang = lang
        self.code, self.noc = mask(text, lang)
        self.lines = text.splitlines()
        self.starts = [0]
        for m in re.finditer("\n", text):
            self.starts.append(m.end())
        self.syms: list[list] = []
        self.mods: list[tuple[str, str]] = []    # (inline mod path, child) for `mod x;`

    def line(self, off: int) -> int:
        return bisect.bisect_right(self.starts, off)

    def doc_start(self, first: int) -> int:
        """Walk up over the comments sitting directly above line `first` (the
        statement already starts at its attributes)."""
        n = first
        while n > 1:
            t = self.lines[n - 2].strip()
            if not (t.startswith("//") and not t.startswith("//!")):
                break
            n -= 1
        return n

    def add(self, kind, name, qual, kw_off, s, e, sig, flags, depth):
        first = self.line(s)
        self.syms.append([kind, name, qual, self.line(kw_off), self.doc_start(first),
                          self.line(max(s, e - 1)), sig, flags, depth])

    # ---- Rust
    def rust(self, lo, hi, prefix, depth, test, modpath):
        for s, he, body, e in statements(self.code, lo, hi):
            attrs, head = strip_attrs(self.code[s:he], ATTR_RUST)
            kw = s + (len(self.code[s:he]) - len(head.lstrip())) if head else s
            head = head.strip()
            if not head:
                continue
            flat = WS.sub(" ", head)
            noc_attrs = strip_attrs(self.noc[s:he], ATTR_RUST)[0]
            is_test = test or "cfg(test)" in noc_attrs.replace(" ", "")
            flags = "test" if is_test else ""
            sig = sig_of(self.noc, kw, he)
            m = RUST_FN.match(flat)
            if m:
                self.add("fn", m.group(1), prefix + m.group(1), kw, s, e, sig, flags, depth)
                continue
            m = RUST_TYPE.match(flat)
            if m:
                kind, name = m.group(1), m.group(2)
                self.add(kind, name, prefix + name, kw, s, e, sig, flags, depth)
                if kind == "trait" and body:
                    self.rust(body[0] + 1, body[1], prefix + name + "::", depth + 1, is_test, modpath)
                continue
            m = RUST_IMPL.match(flat)
            if m:
                disp, target = impl_target(m.group(1))
                self.add("impl", "impl " + disp, prefix + target, kw, s, e, sig, flags, depth)
                if body:
                    self.rust(body[0] + 1, body[1], prefix + target + "::", depth + 1, is_test, modpath)
                continue
            m = RUST_MOD.match(flat)
            if m:
                name = m.group(1)
                if body:
                    self.add("mod", name, prefix + name, kw, s, e, sig, flags, depth)
                    self.rust(body[0] + 1, body[1], prefix + name + "::", depth + 1, is_test,
                              modpath + [name])
                else:
                    self.mods.append(("/".join(modpath), name))
                continue
            m = RUST_CONST.match(flat)
            if m:
                self.add(m.group(1), m.group(2), prefix + m.group(2), kw, s, e, sig, flags, depth)
                continue
            m = RUST_ALIAS.match(flat)
            if m:
                self.add("type", m.group(1), prefix + m.group(1), kw, s, e, sig, flags, depth)
                continue
            m = RUST_MACRO.match(flat)
            if m:
                self.add("macro", m.group(1), prefix + m.group(1), kw, s, e, sig, flags, depth)
                continue
            if RUST_INVOKE.match(flat) and body:
                self.rust(body[0] + 1, body[1], prefix, depth, is_test, modpath)
                continue
            if RUST_EXTERN.match(flat) and body:
                self.rust(body[0] + 1, body[1], prefix, depth, is_test, modpath)

    # ---- Slang
    def slang(self, lo, hi, prefix, depth):
        for s, he, body, e in statements(self.code, lo, hi):
            attrs, head = strip_attrs(self.noc[s:he], re.compile(r"\[\[?"))
            ch = strip_attrs(self.code[s:he], re.compile(r"\[\[?"))[1]
            kw = s + (len(self.code[s:he]) - len(ch.lstrip())) if ch else s
            flat = WS.sub(" ", ch).strip()
            if not flat:
                continue
            sig = sig_of(self.noc, kw, he)
            m = re.match(r"^import\s+([\w.]+)", flat)
            if m:
                self.add("import", m.group(1), m.group(1), kw, s, e, sig, "", depth)
                continue
            m = re.match(r"^(?:\w+\s+)*?(struct|class|interface|enum(?:\s+class)?|namespace|extension)\s+([A-Za-z_]\w*)", flat)
            if m and body:
                kind = {"interface": "trait", "namespace": "mod", "extension": "impl", "enum class": "enum"}.get(m.group(1), m.group(1))
                self.add(kind, m.group(2), prefix + m.group(2), kw, s, e, sig, "", depth)
                self.slang(body[0] + 1, body[1], prefix + m.group(2) + "::", depth + 1)
                continue
            m = re.match(r"^(?:\w+\s+)*?(?:typealias|typedef)\s+([A-Za-z_]\w*)", flat)
            if m:
                self.add("type", m.group(1), prefix + m.group(1), kw, s, e, sig, "", depth)
                continue
            paren = flat.find("(")
            eqs = flat.find("=")
            if body and paren > 0 and (eqs < 0 or eqs > paren):
                names = re.findall(r"([A-Za-z_]\w*)\s*(?:<[^<>()]*>)?\s*$", flat[:paren])
                if names:
                    stage = re.search(r'shader\s*\(\s*"(\w+)"', attrs)
                    kind = "entry" if stage else "fn"
                    flags = stage.group(1) if stage else ""
                    self.add(kind, names[0], prefix + names[0], kw, s, e, sig, flags, depth)
                    continue
            if body is None and re.match(r"^(?:static\s+)?const\b|^static\s", flat):
                m = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*=", flat)
                if m:
                    self.add("const", m.group(1), prefix + m.group(1), kw, s, e, sig, "", depth)
                    continue
            if body is None and depth == 0 and paren < 0:
                m = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?::\s*\w+\s*)?(?:=.*)?$", flat)
                if m and not re.match(r"^(?:return|typedef)\b", flat):
                    self.add("var", m.group(1), prefix + m.group(1), kw, s, e, sig, "", depth)


def module_doc(lines: list[str], lang: str) -> str:
    for t in lines[:40]:
        t = t.strip()
        if lang == "rust" and t.startswith("//!"):
            return t[3:].strip()
        if lang in ("slang", "js") and t.startswith("//"):
            return t[2:].strip()
        if lang == "md" and t.startswith("#"):
            return t.lstrip("#").strip()
    return ""


def parse_brace(text: str, lang: str) -> tuple[list, list, str]:
    sc = Scanner(text, lang)
    if lang == "rust":
        sc.rust(0, len(text), "", 0, False, [])
    else:
        sc.slang(0, len(text), "", 0)
    return sc.syms, sc.mods, module_doc(sc.lines, lang)


def parse_python(text: str) -> tuple[list, list, str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [], [], f"(syntax error at line {e.lineno})"
    lines = text.splitlines()
    syms: list[list] = []

    def sig(node) -> str:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            return f"class {node.name}({bases})" if bases else f"class {node.name}"
        a = ast.unparse(node.args)
        r = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        pre = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{pre} {node.name}({a}){r}"

    def start(node) -> int:
        n = min([node.lineno] + [d.lineno for d in node.decorator_list])
        while n > 1 and lines[n - 2].strip().startswith("#"):
            n -= 1
        return n

    def walk(body, prefix, depth):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "fn"
                syms.append([kind, node.name, prefix + node.name, node.lineno, start(node),
                             node.end_lineno, sig(node)[:SIG_WIDTH * 2], "", depth])
                # Factories such as manager_discord.build_client define a class
                # inside a function; its methods need the same precise lookup.
                walk(node.body, prefix + node.name + ".", depth + 1)
            elif depth == 0 and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        src = WS.sub(" ", lines[node.lineno - 1]).strip()
                        syms.append(["const", t.id, t.id, node.lineno, node.lineno,
                                     node.end_lineno, src[:SIG_WIDTH], "", 0])
            else:
                # Definitions may also sit under if/try/with, including handlers.
                walk(ast.iter_child_nodes(node), prefix, depth)

    walk(tree.body, "", 0)
    doc = ast.get_docstring(tree) or ""
    return syms, [], doc.strip().splitlines()[0] if doc.strip() else ""


MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
ENTRY_HEAD = re.compile(r"^[DVS]\d+\b")  # S: the studio's own log (D329)


def parse_md(text: str) -> tuple[list, list, str]:
    lines = text.splitlines()
    heads = []
    fence = False
    for i, t in enumerate(lines, 1):
        if t.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        m = None if fence else MD_HEAD.match(t)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    syms = []
    stack: list[tuple[int, str]] = []
    # A history entry (`## D46`, `### D64`) is flat whatever its heading level,
    # as fe_docs reads it: it runs to the next entry, and nests under nothing
    # but the document title.
    entry = [bool(ENTRY_HEAD.match(t)) for _, _, t in heads]
    for idx, (ln, lvl, title) in enumerate(heads):
        end = len(lines)
        for k in range(idx + 1, len(heads)):
            ln2, lvl2, _ = heads[k]
            if lvl2 <= lvl or (entry[idx] and entry[k]):
                end = ln2 - 1
                break
        while end > ln and not lines[end - 1].strip():
            end -= 1
        while stack and (stack[-1][0] >= lvl or (entry[idx] and stack[-1][0] > 1)):
            stack.pop()
        qual = " > ".join([t for _, t in stack] + [title])
        syms.append(["section", title, qual, ln, ln, end, "#" * lvl + " " + title,
                     "", len(stack)])
        stack.append((lvl, title))
    return syms, [], module_doc(lines, "md")


TOML_HEAD = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?")


def parse_toml(text: str) -> tuple[list, list, str]:
    lines = text.splitlines()
    heads = [(i, m) for i, t in enumerate(lines, 1) if (m := TOML_HEAD.match(t))]
    syms = []
    seen: dict[str, int] = {}
    for idx, (ln, m) in enumerate(heads):
        end = heads[idx + 1][0] - 1 if idx + 1 < len(heads) else len(lines)
        while end > ln and not lines[end - 1].strip():
            end -= 1
        name = m.group(2)
        if m.group(1) == "[[":
            seen[name] = seen.get(name, 0) + 1
            label = next((re.match(r'\s*name\s*=\s*"([^"]*)"', t).group(1)
                          for t in lines[ln:end] if re.match(r'\s*name\s*=\s*"', t)), "")
            qual = f"{name}#{seen[name]}" + (f" {label}" if label else "")
        else:
            qual = name
        syms.append(["table", qual, qual, ln, ln, end, lines[ln - 1].strip(), "", 0])
    return syms, [], ""


# ---- JavaScript (the board's UI, engine/tools/board_ui: plain JS, no build step)
#
# Masking is a small scanner rather than one regex: a template literal holds
# `${...}` expressions that hold strings and templates again, and a regex
# literal (`/^T\d+$/`) can hold quotes and braces; both must be blanked whole
# for the braces outside them to match.

JS_ID = r"[A-Za-z_$][\w$]*"
JS_REGEX_AFTER = "(,=:[!&|?{};+-*%<>~^"     # a `/` after one of these starts a regex literal
JS_REGEX_WORD = re.compile(r"(?:^|[^\w$.])(?:return|typeof|case|delete|void|throw|in|of|yield|await)\s*$")


class JsMask:
    def __init__(self, text: str):
        self.t, self.n = text, len(text)
        self.strs: list[tuple[int, int]] = []    # strings, templates, regex literals
        self.coms: list[tuple[int, int]] = []    # comments
        self.expr(0, False)

    def expr(self, i: int, inner: bool) -> int:
        """Scan code from i; inside a template's `${` (inner) stop past its `}`."""
        t, n = self.t, self.n
        depth, prev = 0, ""
        while i < n:
            c = t[i]
            if t.startswith("//", i):
                j = t.find("\n", i)
                j = n if j < 0 else j
                self.coms.append((i, j))
                i = j
                continue
            if t.startswith("/*", i):
                j = t.find("*/", i + 2)
                j = n if j < 0 else j + 2
                self.coms.append((i, j))
                i = j
                continue
            if c in "'\"":
                j = i + 1
                while j < n and t[j] != c and t[j] != "\n":
                    j += 2 if t[j] == "\\" else 1
                j = min(n, j + 1)
                self.strs.append((i, j))
                i, prev = j, "a"
                continue
            if c == "`":
                j = self.template(i)
                self.strs.append((i, j))
                i, prev = j, "a"
                continue
            if c == "/" and (prev == "" or prev in JS_REGEX_AFTER
                             or (prev == "a" and JS_REGEX_WORD.search(t[max(0, i - 16):i]))):
                j = self.regex(i)
                if j:
                    self.strs.append((i, j))
                    i, prev = j, "a"
                    continue
            if inner and c == "{":
                depth += 1
            elif inner and c == "}":
                if depth == 0:
                    return i + 1
                depth -= 1
            if not c.isspace():
                prev = "a" if (c.isalnum() or c in "_$") else c
            i += 1
        return n

    def template(self, i: int) -> int:
        t, n = self.t, self.n
        j = i + 1
        while j < n:
            if t[j] == "\\":
                j += 2
            elif t[j] == "`":
                return j + 1
            elif t.startswith("${", j):
                j = self.expr(j + 2, True)
            else:
                j += 1
        return n

    def regex(self, i: int) -> int | None:
        t, n = self.t, self.n
        j, cls = i + 1, False
        while j < n:
            c = t[j]
            if c == "\n":
                return None
            if c == "\\":
                j += 2
                continue
            if cls:
                cls = c != "]"
            elif c == "[":
                cls = True
            elif c == "/":
                j += 1
                while j < n and (t[j].isalpha()):
                    j += 1
                return j
            j += 1
        return None


def mask_js(text: str) -> tuple[str, str]:
    """mask() for JavaScript: (code, nocomment), offsets and newlines kept."""
    m = JsMask(text)
    code, noc = list(text), list(text)
    for a, b in m.strs:
        for k in range(a, min(b, len(text))):
            if text[k] != "\n":
                code[k] = " "
    for a, b in m.coms:
        for k in range(a, b):
            if text[k] != "\n":
                code[k] = noc[k] = " "
    return "".join(code), "".join(noc)


JS_EXPORT = r"(?:export\s+(?:default\s+)?)?"
JS_FUNC = re.compile(rf"^{JS_EXPORT}(async\s+)?function\s*\*?\s*({JS_ID})")
JS_CLASS = re.compile(rf"^{JS_EXPORT}class\s+({JS_ID})")
JS_BIND = re.compile(rf"^{JS_EXPORT}(const|let|var)\s+({JS_ID})\s*=\s*")
JS_FN_VALUE = re.compile(rf"^(?:async\s+)?(?:function\b|\([^()]*\)\s*=>|{JS_ID}\s*=>)")
JS_METHOD = re.compile(rf"^(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?\*?({JS_ID})\s*\(")
JS_LISTEN = re.compile(rf"""^({JS_ID}(?:\.{JS_ID})*)\.addEventListener\(\s*["'`]([\w:-]+)["'`]""")
JS_KEY = re.compile(rf"""^\s*(?:async\s+)?(?:"([^"\n]*)"|'([^'\n]*)'|({JS_ID}))\s*(:|\(|,|$)""")


def parse_js(text: str) -> tuple[list, list, str]:
    """Top-level functions (declared, or a const bound to an arrow or function), classes
    and their methods, top-level const/let/var, and the keys of a top-level object
    literal as its members (`ACTS.send-back`, `S.docs`)."""
    sc = Scanner(text, "js")
    code, noc = sc.code, sc.noc

    def first_line(s: int) -> str:
        e = noc.find("\n", s)
        return WS.sub(" ", noc[s:e if e >= 0 else len(noc)]).strip()[:SIG_WIDTH * 2]

    for s, he, body, e in statements(code, 0, len(code)):
        head = WS.sub(" ", code[s:he]).strip()
        m = JS_FUNC.match(head)
        if m and body:
            sc.add("fn", m.group(2), m.group(2), s, s, e, sig_of(noc, s, he), "async" if m.group(1) else "", 0)
            continue
        m = JS_CLASS.match(head)
        if m and body:
            name = m.group(1)
            sc.add("class", name, name, s, s, e, sig_of(noc, s, he), "", 0)
            for s2, he2, b2, e2 in statements(code, body[0] + 1, body[1]):
                mm = JS_METHOD.match(WS.sub(" ", code[s2:he2]).strip())
                if mm and b2:
                    sc.add("fn", mm.group(1), f"{name}.{mm.group(1)}", s2, s2, e2, sig_of(noc, s2, he2), "", 1)
            continue
        # `document.addEventListener("click", e => {...})`: the UI's dispatch has no name
        # of its own, so it is `document.click` (find click, show document.keydown)
        m = JS_LISTEN.match(WS.sub(" ", noc[s:he]).strip())
        if m:
            sc.add("listener", m.group(2), f"{m.group(1)}.{m.group(2)}", s, s, e, first_line(s), "", 0)
            continue
        m = JS_BIND.match(head)
        if not m:
            continue
        name, rest = m.group(2), head[m.end():]
        if JS_FN_VALUE.match(rest):
            sc.add("fn", name, name, s, s, e, first_line(s), "", 0)
            continue
        sc.add("const" if m.group(1) == "const" else "var", name, name, s, s, e, first_line(s), "", 0)
        if rest.startswith("{"):
            k = code.find("{", s)
            close = match_brace(code, k)
            j, depth, seg = k + 1, 0, k + 1
            while j <= close:
                c = code[j]
                if c in "([{":
                    depth += 1
                elif c in ")]}" and j < close:
                    depth -= 1
                if (c == "," and depth == 0) or j == close:
                    piece = noc[seg:j]
                    lead = len(piece) - len(piece.lstrip())
                    km = JS_KEY.match(piece)
                    if km and piece.strip() and not piece.lstrip().startswith("..."):
                        key = km.group(1) if km.group(1) is not None else km.group(2) if km.group(2) is not None else km.group(3)
                        val = code[seg + km.end():j].strip() if km.group(4) == ":" else ""
                        kind = "fn" if km.group(4) == "(" or JS_FN_VALUE.match(val) else "key"
                        a = seg + lead
                        b = seg + len(piece.rstrip())
                        sc.add(kind, key, f"{name}.{key}", a, a, b, first_line(a), "", 1)
                    seg = j + 1
                j += 1
    return sc.syms, sc.mods, module_doc(sc.lines, "js")


def parse(text: str, lang: str) -> tuple[list, list, str]:
    if lang in ("rust", "slang"):
        return parse_brace(text, lang)
    if lang == "python":
        return parse_python(text)
    if lang == "js":
        return parse_js(text)
    if lang == "md":
        return parse_md(text)
    return parse_toml(text)


# --------------------------------------------------------------------------
# The index.

def listed(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=root, capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return [f for f in out.split("\0") if f]


def tracked() -> list[str]:
    try:
        # Untracked files too (not ignored ones): a file an agent has just
        # written is the one it is working on, and it must be findable before
        # its first commit.
        files = listed(REPO)
        # D329: a submodule (the studio) is one gitlink entry here; its files are listed
        # from inside it.
        for sub in [f for f in files if (REPO / f / ".git").exists()]:
            files.remove(sub)
            files += [f"{sub}/{f}" for f in listed(REPO / sub)]
    except (OSError, subprocess.CalledProcessError):
        files = [p.relative_to(REPO).as_posix() for p in REPO.rglob("*")
                 if p.is_file() and ".git" not in p.parts and "target" not in p.parts]
    return sorted(f for f in files
                  if Path(f).suffix in LANG and not any(x.search(f) for x in EXCLUDE))


def crate_roots(files: set[str]) -> list[str]:
    """main.rs / lib.rs / build.rs of every crate, plus [[bin]]/[lib] paths."""
    roots = []
    for toml in (t for base in CARGO_ROOTS for t in (REPO / base).glob("**/Cargo.toml")):
        if "target" in toml.parts:
            continue
        d = toml.parent.relative_to(REPO).as_posix()
        for c in ("src/main.rs", "src/lib.rs", "build.rs"):
            roots.append(f"{d}/{c}")
        try:
            for m in re.finditer(r'^\s*path\s*=\s*"([^"]+\.rs)"', toml.read_text(encoding="utf-8"), re.M):
                roots.append(f"{d}/{m.group(1)}")
        except OSError:
            pass
        roots += [f for f in files if f.startswith(f"{d}/src/bin/")]
    return [r for r in roots if r in files]


def orphans(entries: dict) -> set[str]:
    rs = {f for f in entries if f.endswith(".rs")}
    reached = set()
    roots = set(crate_roots(rs))
    todo = list(roots)
    while todo:
        f = todo.pop()
        if f in reached:
            continue
        reached.add(f)
        p = Path(f)
        # `mod x;` in a/main.rs, a/lib.rs or a/mod.rs is a/x.rs; in a/y.rs it is a/y/x.rs.
        # A [[bin]] root beside main.rs declares its children beside itself too.
        root = f in roots
        base = p.parent if p.stem in ("main", "lib", "mod") or root else p.parent / p.stem
        for inline, child in entries[f].get("mods", []):
            d = base / inline if inline else base
            for cand in (d / f"{child}.rs", d / child / "mod.rs"):
                c = cand.as_posix()
                if c in rs:
                    todo.append(c)
    return rs - reached


def load_index() -> tuple[dict, dict]:
    t0 = time.perf_counter()
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        if cache.get("version") != VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    old = cache.get("files", {})
    files: dict = {}
    parsed = 0
    for f in tracked():
        p = REPO / f
        try:
            st = p.stat()
        except OSError:
            continue
        key = [st.st_mtime_ns, st.st_size]
        e = old.get(f)
        if e and e.get("key") == key:
            files[f] = e
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lang = LANG[p.suffix]
        syms, mods, doc = parse(text, lang)
        files[f] = {"key": key, "lang": lang, "lines": text.count("\n") + (not text.endswith("\n")),
                    "bytes": st.st_size, "doc": doc, "syms": syms, "mods": mods}
        parsed += 1
    changed = parsed or set(files) != set(old)
    orph = sorted(orphans(files)) if changed or "orphans" not in cache else cache["orphans"]
    if changed:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        # Independent readers can rebuild together; never share a staging file.
        tmp = CACHE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": VERSION, "files": files, "orphans": orph},
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, CACHE)
    stats = {"parsed": parsed, "files": len(files), "ms": (time.perf_counter() - t0) * 1000,
             "orphans": set(orph)}
    return files, stats


# --------------------------------------------------------------------------
# Output helpers.

def span(s) -> str:
    return f"L{s[LINE]}" if s[LINE] == s[END] else f"L{s[LINE]}-{s[END]}"


def label(s) -> str:
    """`fn Renderer::draw`, or an impl's own `impl X for Y`."""
    return s[NAME] if s[K] == "impl" else f"{s[K]}  {s[QUAL]}"


def short(sig: str, width: int = SIG_WIDTH) -> str:
    return sig if len(sig) <= width else sig[:width - 3] + "..."


def resolve_file(files: dict, q: str) -> list[str]:
    q = q.replace("\\", "/").strip()
    if q.startswith("./"):
        q = q[2:]
    try:
        absq = Path(q)
        if absq.is_absolute():
            q = absq.resolve().relative_to(REPO).as_posix()
    except (ValueError, OSError):
        pass
    if q in files:
        return [q]
    return [f for f in files if f.endswith("/" + q) or f == q]


def children(syms: list, i: int) -> list[int]:
    """Indices of the direct members of the container at syms[i]."""
    s = syms[i]
    out = []
    for j in range(i + 1, len(syms)):
        t = syms[j]
        if t[START] > s[END] or t[DEPTH] <= s[DEPTH]:
            if t[LINE] > s[END]:
                break
            if t[DEPTH] <= s[DEPTH]:
                break
        if t[DEPTH] == s[DEPTH] + 1:
            out.append(j)
    return out


def tally(syms: list, idx: list[int]) -> str:
    c: dict[str, int] = {}
    for j in idx:
        c[syms[j][K]] = c.get(syms[j][K], 0) + 1
    return ", ".join(f"{n} {k}" for k, n in sorted(c.items(), key=lambda kv: -kv[1]))


def all_syms(files: dict):
    for f, e in files.items():
        for i, s in enumerate(e["syms"]):
            yield f, i, s


def read_lines(f: str) -> list[str]:
    return (REPO / f).read_text(encoding="utf-8", errors="replace").splitlines()


def flag_text(s, f, orph) -> str:
    bits = []
    if s[FLAGS]:
        bits.append(s[FLAGS])
    if f in orph:
        bits.append("orphan")
    return f"  [{', '.join(bits)}]" if bits else ""


# --------------------------------------------------------------------------
# Commands.

def cmd_find(files, stats, args) -> int:
    kind, tests, regex, cap, in_file = None, False, False, 25, None
    words = []
    it = iter(args)
    for a in it:
        if a == "--kind":
            kind = set(next(it, "").split(","))
        elif a == "--tests":
            tests = True
        elif a == "--regex":
            regex = True
        elif a in ("-n", "--max"):
            cap = int(next(it, "25"))
        elif a == "--in":
            in_file = next(it, None)
        else:
            words.append(a)
    if not words:
        print("usage: fe_index.py find QUERY [QUERY ...] [--kind K] [--in PATH] [--tests] [--regex] [-n N]")
        return 2
    # Each argument is its own query, like show; quote a multi-word heading.
    found = False
    for q in words:
        found |= find_one(files, stats, q, kind, tests, regex, cap, in_file)
    return 0 if found else 1


def in_path(f, where) -> bool:
    """`--in` takes a file (a path suffix) or a directory (a path prefix)."""
    w = where.replace("\\", "/").strip("/")
    return f.endswith(w) or f.startswith(w + "/") or f"/{w}/" in f"/{f}"


def module_split(files, q):
    """`fe_sync.maybe_fetch`: a Python module's stem, then a name in it (the file, the name).
    `enemies::step`, `follow::Follow::step`: a Rust module (a file's stem, or the
    directory of a `mod.rs`), then a name in it, when exactly one file is that
    module and it holds the name; a capitalised head is a type (`Renderer::x`)."""
    stem, dot, rest = q.partition(".")
    if dot and rest and "/" not in stem:
        fs = [f for f in files if files[f]["lang"] == "python" and Path(f).stem == stem]
        return (fs[0], rest) if len(fs) == 1 else None
    head, sep, rest = q.partition("::")
    if not sep or not rest or not head[:1].islower() or "/" in head:
        return None
    def module(f):
        p = Path(f)
        return p.parent.name if p.stem == "mod" else p.stem
    fs = [f for f in files if files[f]["lang"] == "rust" and module(f) == head]
    if len(fs) != 1:
        return None
    rl = rest.lower()
    held = any(s[NAME].lower() == rl or s[QUAL].lower() == rl or s[QUAL].lower().endswith("::" + rl)
               for _, _, s in all_syms({fs[0]: files[fs[0]]}))
    return (fs[0], rest) if held else None


def find_one(files, stats, q, kind, tests, regex, cap, in_file) -> bool:
    shown = q
    mod = None if (in_file or regex) else module_split(files, q)
    if mod:
        in_file, q = mod
    ql = q.lower()
    pat = re.compile(q, re.I) if regex else None
    hits = []
    in_tests = 0
    for f, i, s in all_syms(files):
        if kind and s[K] not in kind:
            continue
        if in_file and not in_path(f, in_file):
            continue
        skip = not tests and "test" in s[FLAGS].split(",")
        name, qual = s[NAME].lower(), s[QUAL].lower()
        if pat:
            if not (pat.search(s[QUAL]) or pat.search(s[NAME])):
                continue
            rank = 2
        elif name == ql or qual == ql:
            rank = 0
        elif qual.endswith(ql) or name.startswith(ql) or qual.endswith("::" + ql):
            rank = 1
        elif ql in qual or ql in name:
            rank = 2
        else:
            continue
        if skip:
            # Counted, so a query that only tests answer says so.
            in_tests += 1
            continue
        code = 0 if files[f]["lang"] in ("rust", "slang", "python", "js") else 1
        hits.append((rank, code, f, s[LINE], s))
    hits.sort(key=lambda h: h[:4])
    print(f"{TAG} find {shown!r} | {len(hits)} match{'es' if len(hits) != 1 else ''}")
    for rank, code, f, _, s in hits[:cap]:
        print(f"  {f}:{span(s)}  {label(s)}{flag_text(s, f, stats['orphans'])}")
        if s[K] not in ("section", "table", "impl") and s[SIG]:
            print(f"      {short(s[SIG])}")
    if len(hits) > cap:
        print(f"  ... {len(hits) - cap} more; narrow with a longer name, --kind, --in, or -n {len(hits)}")
    if not hits and in_tests:
        print(f"  {in_tests} match{'es' if in_tests != 1 else ''} in test code: add --tests")
    return bool(hits)


def pick(files, q: str, in_file: str | None):
    """Symbols a `show` query names: exact qualified, then exact name, then
    a qualified-name suffix, then a heading that starts with it, then (D252:
    `docs/engine-reference.md#ragdolls` finds "Active ragdolls (D136)") a
    heading that contains it."""
    ql = q.lower()
    pool = [(f, i, s) for f, i, s in all_syms(files)
            if not in_file or f in resolve_file(files, in_file)]
    for test in (lambda s: s[QUAL].lower() == ql,
                 lambda s: s[NAME].lower() == ql or (s[K] == "impl" and s[NAME].lower() == ql),
                 lambda s: s[QUAL].lower().endswith("::" + ql) or s[QUAL].lower().endswith("." + ql)
                 or s[QUAL].lower().endswith(" > " + ql),
                 lambda s: s[K] == "section" and s[NAME].lower().startswith(ql),
                 lambda s: s[K] == "section" and ql in s[NAME].lower()):
        hit = [h for h in pool if test(h[2])]
        if hit:
            return hit
    return []


def print_span(f: str, a: int, b: int, cap: int, number: bool, header: str) -> None:
    lines = read_lines(f)
    b = min(b, len(lines))
    print(f"-- {f}:{a}-{b}{header}")
    shown = b - a + 1 if cap == 0 else min(cap, b - a + 1)
    for n in range(a, a + shown):
        t = lines[n - 1]
        print(f"{n:>5}  {t}" if number else t)
    if shown < b - a + 1:
        print(f"... {b - a + 1 - shown} more lines ({f}:{a + shown}-{b}); --max 0 for all")


def cmd_show(files, stats, args) -> int:
    cap, sig_only, number, in_file, full = 400, False, False, None, False
    qs = []
    it = iter(args)
    for a in it:
        if a == "--max":
            cap = int(next(it, "400"))
        elif a == "--sig":
            sig_only = True
        elif a == "-n":
            number = True
        elif a == "--in":
            in_file = next(it, None)
        elif a == "--full":
            full = True
        else:
            qs.append(a)
    if not qs:
        print("usage: fe_index.py show SYMBOL|PATH:L1-L2|PATH#Heading|D64 ... [--sig] [--max N] [--in PATH] [-n]")
        return 2
    rc = 0
    for q in qs:
        if re.fullmatch(r"[DVdv]\d+(\.\.[DVdv]?\d+)?", q):
            sys.path.insert(0, str(HERE))
            import fe_docs
            rc |= fe_docs.cmd_show([q])
            continue
        m = re.fullmatch(r"(.+?):L?(\d+)(?:-L?(\d+))?", q)
        if m:
            fs = resolve_file(files, m.group(1))
            # Explicit spans also work in scripts without a symbol parser (PS1,
            # for example). Keep the fallback confined to this checkout.
            if not fs:
                path = (REPO / m.group(1)).resolve()
                if path.is_relative_to(REPO) and path.is_file():
                    fs = [path.relative_to(REPO).as_posix()]
            if not fs:
                print(f"-- {q}: no such file in this checkout")
                rc = 1
                continue
            if len(fs) > 1:
                print(f"-- {q}: ambiguous file: {', '.join(fs)}")
                rc = 1
                continue
            a = int(m.group(2))
            b = int(m.group(3) or a)
            if a < 1 or b < a:
                print(f"-- {q}: expected a positive, ordered line span")
                rc = 1
                continue
            print_span(fs[0], a, b, cap, number, "")
            continue
        where = in_file
        if "#" in q and resolve_file(files, q.split("#", 1)[0]):
            where, q = q.split("#", 1)
        elif not where and module_split(files, q):
            where, q = module_split(files, q)
        hits = pick(files, q, where)
        if not hits:
            print(f"-- {q}: no such symbol (try: fe_index.py find {q})")
            rc = 1
            continue
        if len(hits) > 1:
            print(f"-- {q}: {len(hits)} symbols; name one with a qualifier or --in PATH")
            for f, i, s in hits[:20]:
                print(f"  {f}:{span(s)}  {label(s)}")
            rc = 1
            continue
        f, i, s = hits[0]
        syms = files[f]["syms"]
        kids = children(syms, i)
        head = f" {label(s)}{flag_text(s, f, stats['orphans'])}"
        if sig_only:
            if s[K] in ("section", "table") or not s[SIG]:
                print_span(f, s[START], s[LINE], 0, number, head)
                continue
            print(f"-- {f}:{s[START]}-{s[END]}{head}")
            if s[START] < s[LINE]:              # its doc comment and attributes
                print("\n".join(read_lines(f)[s[START] - 1:s[LINE] - 1]))
            print(s[SIG])
            continue
        big = s[END] - s[START] + 1 > cap and cap
        if kids and s[K] in ("impl", "trait", "mod", "struct", "section") and big and not full:
            print(f"-- {f}:{s[START]}-{s[END]}{head} | {s[END] - s[START] + 1} lines, "
                  f"{tally(syms, kids)}; members below, show one by name (or --full)")
            for j in kids:
                t = syms[j]
                print(f"  {span(t)}  {t[K]}  {t[NAME]}  {short(t[SIG], 100) if t[K] not in ('section',) else ''}".rstrip())
            continue
        print_span(f, s[START], s[END], cap, number, head)
    return rc


def cmd_outline(files, stats, args) -> int:
    if not args:
        print("usage: fe_index.py outline FILE [SYMBOL] [--all]")
        return 2
    show_all = "--all" in args
    args = [a for a in args if a != "--all"]
    fs = resolve_file(files, args[0])
    if len(fs) != 1:
        print(f"{TAG} outline {args[0]}: " + ("no such indexed file" if not fs else "ambiguous: " + ", ".join(fs)))
        return 1
    f = fs[0]
    e = files[f]
    syms = e["syms"]
    orph = " | orphan: no `mod` reaches it" if f in stats["orphans"] else ""
    root = None
    if len(args) > 1:
        want = args[1].lower()
        cands = [i for i, s in enumerate(syms)
                 if s[QUAL].lower() == want or s[NAME].lower() == want
                 or s[QUAL].lower().endswith("::" + want)]
        cands = [i for i in cands if children(syms, i)] or cands
        # A type's own methods answer `outline FILE Type`; a trait impl
        # (often Default, which comes first) must not hide them. The trait
        # implementation remains selectable by its full `impl ... for ...`.
        inherent = [i for i in cands if syms[i][K] == "impl"
                    and " for " not in syms[i][NAME]]
        cands = inherent or cands
        if not cands:
            print(f"{TAG} outline {f}: no symbol {args[1]!r}")
            return 1
        root = cands[0]
    elif sum(1 for s in syms if s[DEPTH] == 0) == 1:
        root = next(i for i, s in enumerate(syms) if s[DEPTH] == 0)   # a doc's title
    print(f"{TAG} {f} | {e['lines']} lines | {len(syms)} symbols{orph}"
          + (f" | {e['doc']}" if e["doc"] and root is None else ""))

    def emit(i, indent):
        s = syms[i]
        kids = children(syms, i)
        pad = "  " * indent
        fl = f"  [{s[FLAGS]}]" if s[FLAGS] else ""
        if s[K] in ("impl", "section", "table"):
            head = s[NAME]                      # `impl X for Y`, a heading, a table
        else:
            head = f"{s[K]}  {short(s[SIG], 100)}"
        if kids and len(kids) > COLLAPSE and not show_all and i != root:
            what = s[QUAL] if s[K] != "section" else f'"{s[NAME]}"'
            print(f"{pad}{span(s)}  {head}  ({tally(syms, kids)}) -- outline {Path(f).name} {what}{fl}")
            return
        print(f"{pad}{span(s)}  {head}{fl}")
        for j in kids:
            emit(j, indent + 1)

    if root is not None:
        emit(root, 1)
    else:
        for i, s in enumerate(syms):
            if s[DEPTH] == 0:
                emit(i, 1)
    return 0


def enclosing(syms: list, line: int):
    best = None
    for s in syms:
        if s[START] <= line <= s[END] and s[K] not in ("import",):
            if best is None or s[DEPTH] >= best[DEPTH]:
                best = s
    return best


def cmd_where(files, stats, args) -> int:
    rc = 0
    for q in args:
        m = re.fullmatch(r"(.+?):L?(\d+)(?::\d+)?", q)
        fs = resolve_file(files, m.group(1)) if m else []
        if len(fs) != 1:
            print(f"{q}: no such indexed file" if not fs else f"{q}: ambiguous: {', '.join(fs)}")
            rc = 1
            continue
        line = int(m.group(2))
        s = enclosing(files[fs[0]]["syms"], line)
        if not s:
            print(f"{fs[0]}:{line}  (top level, no enclosing symbol)")
            continue
        print(f"{fs[0]}:{line}  in {label(s)} ({span(s)})")
    if not args:
        print("usage: fe_index.py where PATH:LINE ...")
        return 2
    return rc


def cmd_refs(files, stats, args) -> int:
    show_lines = "--lines" in args
    docs = "--docs" in args
    comments = "--comments" in args
    cap = 40
    words, in_file = [], None
    it = iter(args)
    for a in it:
        if a == "--in":
            in_file = next(it, None)
            if not in_file:
                print("refs --in needs a file or directory")
                return 2
        elif not a.startswith("--"):
            words.append(a)
    if not words:
        print("usage: fe_index.py refs NAME [--in PATH] [--lines] [--docs] [--comments]")
        return 2
    name = words[0].split("::")[-1]
    pat = re.compile(rf"\b{re.escape(name)}\b")
    # `refs Type::method` keeps only what can be that item: the path itself,
    # `Self::method` / a bare call inside `impl Type`, and `.method(` calls
    # (a receiver's type is not known here, so those may be another type's).
    owner = words[0].split("::")[-2] if "::" in words[0] else None
    if owner:
        path_pat = re.compile(rf"\b{re.escape(owner)}\s*::\s*{re.escape(name)}\b")
        own_pat = re.compile(rf"(\bSelf\s*::\s*{re.escape(name)}\b|(?<![.\w:]){re.escape(name)}\s*\()")
        call_pat = re.compile(rf"\.\s*{re.escape(name)}\s*(::\s*<[^>]*>\s*)?\(")
    total = 0
    out = []
    for f, e in files.items():
        if in_file and not in_path(f, in_file):
            continue
        lang = e["lang"]
        if lang in ("md", "toml") and not docs:
            continue
        if owner and lang != "rust":            # Type::method is a Rust item
            continue
        text = (REPO / f).read_text(encoding="utf-8", errors="replace")
        body = text
        if lang in ("rust", "slang", "js") and not comments:
            body = mask(text, lang)[1]          # comments blank, strings kept
        lines = body.splitlines()
        orig = text.splitlines()
        groups: dict[str, list[int]] = {}
        order = []
        for n, t in enumerate(lines, 1):
            if pat.search(t):
                s = enclosing(e["syms"], n)
                if owner and lang == "rust" and not (
                        path_pat.search(t) or call_pat.search(t)
                        or (own_pat.search(t) and s and (s[QUAL].startswith(owner + "::") or s[NAME] == owner))):
                    continue
                key = (s[QUAL] if s[K] != "impl" else s[NAME]) if s else "(top level)"
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(n)
        if not groups:
            continue
        cnt = sum(len(v) for v in groups.values())
        total += cnt
        out.append((f, cnt, [(k, groups[k]) for k in order], orig))
    out.sort(key=lambda o: -o[1])
    what = words[0] if owner else name
    print(f"{TAG} refs {what!r} | {total} line{'s' if total != 1 else ''} in {len(out)} file{'s' if len(out) != 1 else ''}"
          + (f" | Rust: {owner}::{name}, Self::{name} in impl {owner}, and .{name}( calls of any receiver" if owner else "")
          + ("" if comments else " | code only (--comments to include them)")
          + ("" if docs else ", --docs for Markdown/TOML"))
    printed = 0
    for f, cnt, groups, orig in out:
        if not show_lines:
            parts = [f"{k} ({len(v)})" for k, v in groups]
            print(f"  {f}  " + "  ".join(parts[:12]) + (f"  ... {len(parts) - 12} more" if len(parts) > 12 else ""))
            continue
        print(f"  {f}")
        for k, v in groups:
            print(f"    {k}")
            for n in v:
                if printed >= cap:
                    break
                print(f"      {n:>5}  {short(orig[n - 1].strip(), 110)}")
                printed += 1
        if printed >= cap:
            print(f"  ... line cap {cap} reached; drop --lines for the grouped counts")
            break
    return 0 if total else 1


def cmd_map(files, stats, args) -> int:
    pre = args[0].replace("\\", "/").rstrip("/") + "/" if args else ""
    sel = [f for f in files if f.startswith(pre)] if pre else list(files)
    print(f"{TAG} map | {len(sel)} files | {sum(len(files[f]['syms']) for f in sel)} symbols"
          f" | parsed {stats['parsed']} in {stats['ms']:.0f} ms")
    last = None
    for f in sel:
        e = files[f]
        d = str(Path(f).parent.as_posix())
        if d != last:
            print(f"  {d}/")
            last = d
        o = "  ORPHAN" if f in stats["orphans"] else ""
        doc = f"  {short(e['doc'], 70)}" if e["doc"] else ""
        print(f"    {Path(f).name:<28} {e['lines']:>5} L {e['bytes'] // 1024:>4}K {len(e['syms']):>4} sym{o}{doc}")
    return 0


# ---- check

RS_FN_LINE = re.compile(
    r"^[ 	]*(?:pub(?:\s*\([^)]*\))?\s+)?(?:default\s+)?(?:(?:const|async|unsafe|extern(?:\s+\"[^\"]*\")?)\s+)*fn\s+([A-Za-z_]\w*)", re.M)
JS_TOP_LINE = re.compile(r"^(?:export\s+(?:default\s+)?)?(?:(?:async\s+)?function\b|class\s|(?:const|let|var)\s"
                         r"|[A-Za-z_$][\w$.]*\.addEventListener\()", re.M)
RS_TYPE_LINE = re.compile(r"^[ 	]*(?:pub(?:\s*\([^)]*\))?\s+)?(?:unsafe\s+)?(struct|enum|trait|impl)\b", re.M)


def cmd_check(files, stats, args) -> int:
    bad: list[str] = []
    counts = {"files": len(files), "symbols": 0, "nested fn": 0}
    for f, e in files.items():
        syms = e["syms"]
        counts["symbols"] += len(syms)
        # spans are ordered, nest, and never overlap a sibling
        stack: list = []
        for s in syms:
            if not (s[START] <= s[LINE] <= s[END]):
                bad.append(f"{f}:{s[LINE]} {s[QUAL]}: span {s[START]}-{s[END]} does not contain its line")
            while stack and stack[-1][DEPTH] >= s[DEPTH]:
                stack.pop()
            if stack and not (stack[-1][START] <= s[LINE] and s[END] <= stack[-1][END]):
                bad.append(f"{f}:{s[LINE]} {s[QUAL]}: escapes its parent {stack[-1][QUAL]}")
            stack.append(s)
        if e["lang"] not in ("rust", "slang", "js"):
            continue
        text = (REPO / f).read_text(encoding="utf-8", errors="replace")
        code, noc = mask(text, e["lang"])
        if code.count("{") != code.count("}"):
            bad.append(f"{f}: unbalanced braces after masking ({code.count('{')} {{ vs {code.count('}')} }})")
        clines = code.splitlines()
        for s in syms:
            if s[K] != "impl" and s[NAME] not in clines[s[LINE] - 1] + noc.splitlines()[s[LINE] - 1]:
                bad.append(f"{f}:{s[LINE]} {s[QUAL]}: its line does not name it")
            # a JS object key ends where its value does (`docs: {},`), not at a `}` or `;`
            js_member = e["lang"] == "js" and s[DEPTH] > 0 and s[QUAL] and "." in s[QUAL]
            if s[K] != "import" and not js_member and not re.search(r"[};]", clines[s[END] - 1]):
                bad.append(f"{f}:{s[END]} {s[QUAL]}: span does not end at a `}}` or `;`")
        if e["lang"] == "js":
            # every top-level declaration a line regex sees is an indexed item on that line
            tops = {s[LINE] for s in syms if s[DEPTH] == 0}
            for m in JS_TOP_LINE.finditer(code):
                ln = code.count("\n", 0, m.start()) + 1
                if ln not in tops:
                    bad.append(f"{f}:{ln} {m.group(0).strip()[:40]}: found by the line regex, missing from the index")
            counts.setdefault("js items", 0)
            counts["js items"] += len(syms)
        elif e["lang"] == "rust":
            indexed = {s[LINE] for s in syms if s[K] == "fn"}
            fns = [s for s in syms if s[K] == "fn"]
            # A macro_rules! body is a template, not items (`struct $name`).
            templates = [(s[START], s[END]) for s in syms if s[K] == "macro"]
            in_template = lambda ln: any(a <= ln <= b for a, b in templates)
            for m in RS_FN_LINE.finditer(code):
                ln = code.count("\n", 0, m.start()) + 1
                if ln in indexed or in_template(ln):
                    continue
                inside = [s for s in fns if s[START] <= ln <= s[END]]
                if inside:
                    counts["nested fn"] += 1
                else:
                    bad.append(f"{f}:{ln} fn {m.group(1)}: found by the line regex, missing from the index")
            items = {s[LINE] for s in syms if s[K] in ("struct", "enum", "trait", "impl")}
            for m in RS_TYPE_LINE.finditer(code):
                ln = code.count("\n", 0, m.start()) + 1
                if ln not in items and not in_template(ln) and not any(s[START] <= ln <= s[END] for s in fns):
                    bad.append(f"{f}:{ln} {m.group(1)}: found by the line regex, missing from the index")
        else:
            entries = {s[LINE] for s in syms if s[K] == "entry"}
            for m in re.finditer(r"\[shader\s*\(", noc):
                ln = noc.count("\n", 0, m.start()) + 1
                if not any(s[START] <= ln <= s[LINE] for s in syms if s[K] == "entry"):
                    bad.append(f"{f}:{ln}: [shader(...)] attribute with no indexed entry point")
            counts.setdefault("entry points", 0)
            counts["entry points"] += len(entries)
    # the history entries agree with fe_docs
    sys.path.insert(0, str(HERE))
    try:
        import fe_docs
        d, v = fe_docs.load()
        for ent in d + v:  # D258: the decisions are one file per category
            path = fe_docs.rel(ent.path)
            if ent.line not in {s[LINE] for s in files.get(path, {}).get("syms", [])}:
                bad.append(f"{path}:{ent.line} {ent.ident}: fe_docs sees an entry the index has no section for")
        counts["history entries"] = len(d) + len(v)
    except Exception as ex:                    # noqa: BLE001 - report, don't die
        bad.append(f"fe_docs cross-check failed: {ex}")
    counts["orphans"] = len(stats["orphans"])
    print(f"{TAG} check {'ok' if not bad else 'FAILED'} | "
          + " | ".join(f"{v} {k}" for k, v in counts.items())
          + f" | parsed {stats['parsed']} in {stats['ms']:.0f} ms")
    for b in bad[:30]:
        print("  " + b)
    if len(bad) > 30:
        print(f"  ... {len(bad) - 30} more")
    if stats["orphans"]:
        print("  orphan files (no `mod` or Cargo root reaches them): " + ", ".join(sorted(stats["orphans"])))
    return 1 if bad else 0


COMMANDS = {"find": cmd_find, "show": cmd_show, "outline": cmd_outline, "refs": cmd_refs,
            "where": cmd_where, "map": cmd_map, "check": cmd_check}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help") or argv[1] not in COMMANDS:
        print(__doc__)
        return 2
    files, stats = load_index()
    return COMMANDS[argv[1]](files, stats, argv[2:])


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
