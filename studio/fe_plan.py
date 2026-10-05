#!/usr/bin/env python3
"""Print a plan document as an illustrated PDF you can read on a phone (D241).

    python engine/tools/fe_plan.py pdf docs/NAME.md [--out PATH]
    python engine/tools/fe_plan.py check docs/NAME.md
    python engine/tools/fe_plan.py shots docs/NAME.md [--per N] [--dpi N]   # page images to read
    python engine/tools/fe_plan.py ensure                                   # the private venv, once

`pdf` prints one line: pages, size, figures. The default output is
engine/artifacts/plans/NAME.pdf (regenerated from the markdown, never committed).
`check` reports a figure the markdown names that has no file or no block, a caption with
no source, a research image with no link or date or over 500 KB, and a PDF over the
9 MB cap; it exits 1 on any. `shots` writes the pages as PNGs to read before the PDF is
called pleasant.

Figures. Beside docs/NAME.md sits docs/figures/NAME/ with `figures.toml`, one block per
figure, and the image files:

    [[figure]]
    id = "doom-shadow-atlas"
    file = "doom-shadow-atlas.png"
    kind = "research"          # research | capture | chart
    caption = "What the picture shows, standing on its own."
    source = "Adrian Courreges, DOOM (2016) graphics study"
    link = "https://..."       # required for research
    fetched = "2026-10-01"     # required for research

In the markdown a paragraph that is only `[Figure: id]` places the figure there, and
`[Figure: id]` inside a sentence prints "Figure N" (numbered in order of appearance). Both
stay readable as plain markdown, which is how the board shows the document.

The page is narrow (380 x 680 pt) with 15 pt body type; a table of up to four short
columns stays a table, anything wider prints each row as a card; code lines wrap.
A figure that would leave the page above it a third empty floats down past the
paragraphs after it (a lead-in ending in a colon moves with what it introduces; never past
a heading, a rule or another figure) until it fits, and a heading that would end a page
starts the next.
Relative links to other repository files print as their text. Stdlib in front; the
renderer (PyMuPDF and markdown, plan-requirements.txt) runs from a private venv,
engine/target/plan-tools, installed once, and renders offline.
"""
import argparse
import html as htmllib
import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import studio_config  # noqa: E402  (the checkout and its scratch folder)

REPO = studio_config.repo_root()
TAG = "[fe-plan]"
# The renderer's private venv, per checkout ([plan] venv: somewhere gitignored).
VENV = REPO / studio_config.get("plan.venv", studio_config.rel(studio_config.artifacts_dir() / "plan-tools"))
REQUIREMENTS = HERE / "plan-requirements.txt"
PLANS = studio_config.artifacts_dir() / "plans"
CAP = 9 * 1024 * 1024
RESEARCH_MAX = 500 * 1024
PAGE_W, PAGE_H = 380, 680
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 22, 28, 36
TEXT_W = PAGE_W - 2 * MARGIN_X
FIG_MAX_H = 330
CODE_COLS = 58
FLOAT_GAP = 110
FLOAT_MAX = 12
BLOCK = re.compile(r"^\[Figure: *([A-Za-z0-9_.-]+) *\]\s*$")
INLINE = re.compile(r"\[Figure: *([A-Za-z0-9_.-]+) *\]")
FENCE = re.compile(r"^\s*(```|~~~)")


def die(message, code=1):
    print(f"{TAG} {message}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---- the document and its figures (stdlib) -----------------------------------------

def resolve_doc(name):
    p = Path(name)
    if not p.is_absolute():
        p = (Path.cwd() / p) if (Path.cwd() / p).exists() else REPO / p
    if not p.is_file():
        die(f"no such document: {name}")
    return p.resolve()


def figures_dir(doc):
    return doc.parent / "figures" / doc.stem


def load_figures(doc):
    """{id: figure dict with 'path'} from docs/figures/<stem>/figures.toml."""
    folder = figures_dir(doc)
    toml = folder / "figures.toml"
    if not toml.is_file():
        return {}
    data = tomllib.loads(toml.read_text(encoding="utf-8"))
    figs = {}
    for f in data.get("figure", []):
        f = dict(f)
        f["path"] = folder / f.get("file", "")
        figs[f.get("id", "")] = f
    return figs


def scan(text):
    """Markers outside code fences: [(kind 'block'|'inline', id, line number)]."""
    found, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = BLOCK.match(line)
        if m:
            found.append(("block", m.group(1), n))
        else:
            found.extend(("inline", i, n) for i in INLINE.findall(line))
    return found


def problems(doc, text, figs):
    out = []
    markers = scan(text)
    used = {i for k, i, _ in markers if k == "block"}
    for kind, fid, n in markers:
        if fid not in figs:
            out.append(f"line {n}: [Figure: {fid}] has no block in figures.toml")
        elif kind == "inline" and fid not in used:
            out.append(f"line {n}: [Figure: {fid}] is referred to but never placed")
    for fid, f in figs.items():
        where = f"figure {fid}"
        if not f["path"].is_file():
            out.append(f"{where}: file missing ({f['path'].name})")
        elif f.get("kind") == "research" and f["path"].stat().st_size > RESEARCH_MAX:
            out.append(f"{where}: {f['path'].stat().st_size // 1024} KB is over the 500 KB limit")
        if not str(f.get("caption", "")).strip():
            out.append(f"{where}: caption is empty")
        if not str(f.get("source", "")).strip():
            out.append(f"{where}: caption has no source")
        if f.get("kind") == "research":
            if not str(f.get("link", "")).strip():
                out.append(f"{where}: research image has no link")
            if not str(f.get("fetched", "")).strip():
                out.append(f"{where}: research image has no date fetched")
        if fid not in used:
            out.append(f"{where}: in figures.toml but not placed in the document")
    return out


# ---- the private venv --------------------------------------------------------------

def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def in_venv():
    return Path(sys.prefix).resolve() == VENV.resolve()


def ensure():
    py = venv_python()
    if not py.exists():
        print(f"{TAG} creating {VENV.relative_to(REPO)} (once per checkout)", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    probe = subprocess.run([str(py), "-c", "import fitz, markdown"], capture_output=True)
    if probe.returncode:
        print(f"{TAG} installing {REQUIREMENTS.name} into the venv", flush=True)
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                               "-r", str(REQUIREMENTS)])
    return py


def run_inner(args):
    """Run this script's renderer inside the venv and return its exit code."""
    if in_venv():
        return inner_main(args)
    py = ensure()
    return subprocess.run([str(py), str(Path(__file__).resolve()), "--inner", *args]).returncode


# ---- markdown to html (inner; needs markdown) --------------------------------------

def prepare(text, figs):
    """Markers to slots and numbers, links to repository files to plain text.

    Returns (markdown text, [figure ids in order of appearance])."""
    order = [i for k, i, _ in scan(text) if k == "block" and i in figs]
    number = {fid: n for n, fid in enumerate(dict.fromkeys(order), 1)}
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        m = BLOCK.match(line)
        if m and m.group(1) in figs:
            out.extend(["", f"FIGSLOT{number[m.group(1)]}X", ""])
            continue
        line = INLINE.sub(lambda mm: f"Figure {number[mm.group(1)]}" if mm.group(1) in number
                          else "Figure ?", line)
        line = re.sub(r"\[([^\]]+)\]\((?!https?:|mailto:|#)[^)]*\)", r"\1", line)
        out.append(line)
    return "\n".join(out) + "\n", list(dict.fromkeys(order))


def plain(fragment):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def cells(row_html):
    return [m.group(0) for m in re.finditer(r"<t[hd][^>]*>.*?</t[hd]>", row_html, re.S)]


def cell_inner(cell):
    return re.sub(r"^<t[hd][^>]*>|</t[hd]>$", "", cell).strip()


def table_html(table):
    """A narrow table stays one; a wide one prints each row as a card."""
    rows = [cells(r) for r in re.findall(r"<tr>(.*?)</tr>", table, re.S)]
    if not rows:
        return table
    heads, body = rows[0], rows[1:]
    ncol = len(heads)
    widths = [min(max([len(plain(cell_inner(r[c]))) for r in rows if c < len(r)] or [0]), 26)
              for c in range(ncol)]
    if ncol <= 4 and sum(widths) + 2 * ncol <= 66:
        return '<div class="tbl">' + table.replace("<table>", '<table class="t">') + "</div>"
    cards = []
    for r in body:
        title = cell_inner(r[0]) if r else ""
        lines = []
        for c in range(1, len(r)):
            label = plain(cell_inner(heads[c])) if c < ncol else ""
            value = cell_inner(r[c])
            lines.append(f'<div class="kv"><span class="k">{label}</span> {value}</div>' if label
                         else f'<div class="kv">{value}</div>')
        cards.append(f'<div class="card"><div class="ct">{title}</div>{"".join(lines)}</div>')
    first = plain(cell_inner(heads[0])) if heads else ""
    lead = f'<div class="cardhead">{first}</div>' if first else ""
    if cards and lead:
        cards[0] = cards[0].replace('<div class="card">', '<div class="card">' + lead, 1)
    return "".join(cards)


def wrap_code(code):
    out = []
    for line in code.split("\n"):
        if len(line) <= CODE_COLS:
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        out.extend(textwrap.wrap(line, CODE_COLS, subsequent_indent=" " * (indent + 4),
                                 break_long_words=True, break_on_hyphens=False,
                                 drop_whitespace=False, replace_whitespace=False) or [""])
    return "\n".join(out)


def code_html(m):
    code = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
    return '<div class="code"><pre>' + htmllib.escape(wrap_code(code.rstrip("\n"))) + "</pre></div>"


def figure_html(n, f, fitz, apart=False):
    pix = fitz.Pixmap(str(f["path"]))
    aspect = pix.width / pix.height
    w = min(TEXT_W, FIG_MAX_H * aspect)
    h = w / aspect
    source = htmllib.escape(str(f.get("source", "")))
    tail = f'<span class="src">Source: {source}'
    if f.get("link"):
        link = htmllib.escape(str(f["link"]), quote=True)
        tail += f'. <a href="{link}">{link}</a>'
    if f.get("fetched"):
        tail += f" (fetched {htmllib.escape(str(f['fetched']))})"
    tail += "</span>"
    brk = " page-break-before: always;" if apart else ""
    return (f'<div class="fig" id="fig{n}" style="margin-top: 0;{brk}"><img src="{htmllib.escape(f["path"].name, quote=True)}" '
            f'width="{w:.0f}" height="{h:.0f}"/>'
            f'<p class="cap"><b>Figure {n}.</b> {htmllib.escape(str(f.get("caption", "")))} {tail}</p></div>')


CSS = f"""
body {{ font-family: sans-serif; font-size: 15pt; line-height: 1.38; color: #1b1f24; }}
h1 {{ font-size: 27pt; line-height: 1.12; color: #0f2a44; margin: 0 0 8pt 0; }}
h2 {{ font-size: 20pt; line-height: 1.15; color: #0b4f8a; border-top: 3pt solid #0b4f8a;
     padding-top: 7pt; margin: 24pt 0 8pt 0; page-break-after: avoid; }}
h3 {{ font-size: 16.5pt; line-height: 1.2; color: #14202e; border-left: 5pt solid #7aa7d2;
     padding-left: 7pt; margin: 18pt 0 6pt 0; page-break-after: avoid; }}
h4 {{ font-size: 15pt; color: #3a4a5c; margin: 12pt 0 4pt 0; page-break-after: avoid; }}
p {{ margin: 0 0 9pt 0; }}
ul, ol {{ margin: 0 0 9pt 0; padding-left: 20pt; }}
li {{ margin-bottom: 5pt; }}
a {{ color: #0b57b0; }}
code {{ font-family: monospace; font-size: 12pt; color: #8a3b00; }}
strong {{ color: #0d1620; }}
p.status {{ background-color: #fff4d6; border-left: 5pt solid #e0a100; border-top: 6pt solid #fff4d6;
            border-bottom: 6pt solid #fff4d6; padding: 0 8pt; font-size: 13.5pt; }}
blockquote {{ background-color: #eaf2fb; border-left: 5pt solid #0b4f8a; margin: 4pt 0 10pt 0;
             border-top: 6pt solid #eaf2fb; border-bottom: 6pt solid #eaf2fb; padding: 0 9pt; }}
blockquote p {{ margin: 0; }}
.rule {{ border-top: 0.75pt solid #c3ccd6; margin: 12pt 0; }}
.code {{ background-color: #f0f3f6; border-left: 3pt solid #9fb3c8; border-top: 5pt solid #f0f3f6;
         border-bottom: 5pt solid #f0f3f6; padding: 0 6pt; margin: 4pt 0 10pt 0; }}
.code pre {{ font-family: monospace; font-size: 8.5pt; line-height: 1.3; color: #1d2733; margin: 0; }}
.tbl {{ margin: 4pt 0 10pt 0; }}
table.t {{ border-collapse: collapse; width: 100%; font-size: 11pt; }}
table.t th {{ color: #0b4f8a; text-align: left; border-bottom: 1.5pt solid #0b4f8a; padding: 4pt; }}
table.t td {{ border-bottom: 0.5pt solid #c3ccd6; padding: 4pt; vertical-align: top; }}
.cardhead {{ font-size: 10.5pt; color: #5a6b7d; margin: 6pt 0 3pt 0; }}
.card {{ background-color: #f3f7fb; border-left: 4pt solid #7aa7d2; border-top: 5pt solid #f3f7fb;
         border-bottom: 5pt solid #f3f7fb; padding: 0 8pt; margin: 0 0 7pt 0; }}
.ct {{ font-weight: bold; font-size: 13.5pt; color: #0f2a44; margin-bottom: 2pt; }}
.kv {{ font-size: 12pt; line-height: 1.3; margin-bottom: 1pt; }}
.k {{ color: #5a6b7d; }}
.fig {{ text-align: center; margin: 8pt 0 12pt 0; }}
.fig img {{ margin: 0; }}
p.cap {{ text-align: left; font-size: 11.5pt; line-height: 1.3; color: #28323d; margin: 4pt 0 0 0; }}
.src {{ font-size: 9.5pt; color: #5a6b7d; }}
.src a {{ font-size: 9.5pt; }}
"""


def top_blocks(html):
    blocks, depth, start = [], 0, 0
    for m in re.finditer(r"<(/?)([a-zA-Z0-9]+)([^>]*)>", html):
        close, tag, rest = m.group(1), m.group(2).lower(), m.group(3)
        if tag in ("hr", "br", "img") or rest.rstrip().endswith("/"):
            if depth == 0:
                blocks.append(m.group(0))
        elif not close:
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                blocks.append(html[start:m.end()])
    return blocks


def float_figures(html, shifts, stuck=None):
    """Move each figure slot `shifts[n]` blocks later. A block that ends in a colon moves
    together with what it introduces, a run of cards as one; never past a heading, a rule or
    another slot."""
    blocks = top_blocks(html)

    def hard(b):
        return (not b or b.startswith(("<h1", "<h2", "<h3", "<h4", "<h5", "<h6"))
                or "FIGSLOT" in b or 'class="rule"' in b)

    def cardlike(b):
        return b.startswith(('<div class="card"', '<div class="tbl"'))

    for n, k in sorted(shifts.items(), reverse=True):
        slot = f"<p>FIGSLOT{n}X</p>"
        if slot not in blocks:
            continue
        i = blocks.index(slot)
        for _ in range(k):
            nxt = blocks[i + 1] if i + 1 < len(blocks) else ""
            j = i + 2
            if not hard(nxt) and nxt.endswith(":</p>"):
                if j >= len(blocks) or hard(blocks[j]):
                    nxt = ""
                else:
                    j += 1
            if hard(nxt):
                if stuck is not None:
                    stuck.add(n)
                break
            while j < len(blocks) and cardlike(blocks[j]) and cardlike(blocks[j - 1]):
                j += 1
            blocks.insert(j - 1, blocks.pop(i))
            i = j - 1
    return "\n".join(blocks)


def heading_key(s):
    """A heading's identity: its text without whitespace (the engine's position text loses the
    space where a heading wraps)."""
    return "".join(s.split())


def break_headings(html, breaks):
    def one(m):
        return f'<h{m.group(1)} style="page-break-before: always">{m.group(2)}</h{m.group(1)}>' \
            if heading_key(plain(m.group(2))) in breaks else m.group(0)
    return re.sub(r"<h([1-6])>(.*?)</h\1>", one, html, flags=re.S)


def build_html(text, figs, fitz, apart=(), shifts=None, breaks=(), stuck=None):
    import markdown
    body_md, order = prepare(text, figs)
    html = markdown.markdown(body_md, extensions=["tables", "fenced_code", "sane_lists"])
    html = re.sub(r"<table>.*?</table>", lambda m: table_html(m.group(0)), html, flags=re.S)
    html = re.sub(r"<pre><code[^>]*>(.*?)</code></pre>", code_html, html, flags=re.S)
    html = html.replace("<hr />", '<div class="rule"></div>')
    if shifts:
        html = float_figures(html, shifts, stuck)
    if breaks:
        html = break_headings(html, breaks)
    for n, fid in enumerate(order, 1):
        html = html.replace(f"<p>FIGSLOT{n}X</p>", figure_html(n, figs[fid], fitz, n in apart))
    html = re.sub(r"(</h1>\s*)<p>", r'\1<p class="status">', html, count=1)
    title = plain(re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)) if "<h1>" in html else "Plan"
    return html, title, len(order)


# ---- the pdf (inner; needs fitz) ----------------------------------------------------

def ascii_only(s):
    s = s.replace("—", "-").replace("–", "-").replace("−", "-")
    return s.encode("ascii", "ignore").decode()


def content_bottom(page):
    ys = [b[3] for b in page.get_text("blocks")] + [i["bbox"][3] for i in page.get_image_info()]
    ys += [d["rect"].y1 for d in page.get_drawings()]
    return max(ys, default=0)


def stranded(doc, where):
    """Figures (numbered by their image's order) that start a page whose predecessor ends
    more than FLOAT_GAP short of the bottom."""
    out, n = set(), 0
    for page in doc:
        for info in sorted(page.get_image_info(), key=lambda i: i["bbox"][1]):
            n += 1
            if (page.number and info["bbox"][1] < where.y0 + 24
                    and where.y1 - content_bottom(doc[page.number - 1]) > FLOAT_GAP):
                out.add(n)
    return out


def orphan_headings(doc, toc):
    """Headings that are the last thing on their page (the engine ignores page-break-after)."""
    out = set()
    for _level, name, page in toc:
        blocks = [b for b in doc[page - 1].get_text("blocks") if b[6] == 0 and b[4].strip()]
        if not blocks:
            continue
        last = heading_key(max(blocks, key=lambda b: b[3])[4])
        key = heading_key(name)
        if len(last) >= 3 and last in key:
            out.add(key)
    return out


def caption_tail(f):
    if f.get("fetched"):
        return f"(fetched {f['fetched']})"
    return " ".join(str(f.get("link") or f.get("source", "")).split())[-24:]


def split_figures(doc, tails):
    """Figures whose caption runs onto the next page (its last words are not on the image's page)."""
    out, n = set(), 0
    for page in doc:
        flat = " ".join(page.get_text().split())
        for _ in page.get_image_info():
            n += 1
            if n <= len(tails) and tails[n - 1] not in flat:
                out.add(n)
    return out


def render(doc_path, out):
    import fitz
    text = doc_path.read_text(encoding="utf-8")
    figs = load_figures(doc_path)
    folder = figures_dir(doc_path)
    media = fitz.Rect(0, 0, PAGE_W, PAGE_H)
    where = fitz.Rect(MARGIN_X, MARGIN_TOP, PAGE_W - MARGIN_X, PAGE_H - MARGIN_BOTTOM)
    tails = [caption_tail(figs[i]) for i in prepare(text, figs)[1]]
    apart, shifts, breaks, stuck = set(), {}, set(), set()

    def layout():
        html, title, nfig = build_html(text, figs, fitz, apart, shifts, breaks, stuck)
        story = fitz.Story(html, user_css=CSS, em=15,
                           archive=fitz.Archive(str(folder)) if folder.is_dir() else None)
        toc = []

        def on_position(pos):
            if pos.heading and pos.open_close & 1:
                toc.append([pos.heading, pos.text.strip() or "-", pos.page_num])

        doc = story.write_with_links(lambda n, filled: (media, where, None), positionfn=on_position)
        return doc, toc, title, nfig

    for attempt in range(60):
        doc, toc, title, nfig = layout()
        split = split_figures(doc, tails) - apart
        lifted = {n for n in stranded(doc, where) - split - stuck if shifts.get(n, 0) < FLOAT_MAX}
        orphans = orphan_headings(doc, toc) - breaks
        if (not split and not lifted and not orphans) or attempt == 59:
            break
        apart |= split
        breaks |= orphans
        apart -= lifted - split
        for n in lifted:
            shifts[n] = shifts.get(n, 0) + 1
        doc.close()
    # a break set for a layout that has since moved can leave a page nearly empty: drop it if
    # the page does without
    for key in sorted(breaks):
        breaks.discard(key)
        trial, ttoc, ttitle, tfig = layout()
        if (trial.page_count <= doc.page_count and not split_figures(trial, tails) - apart
                and not orphan_headings(trial, ttoc) - breaks):
            doc.close()
            doc, toc, title, nfig = trial, ttoc, ttitle, tfig
        else:
            breaks.add(key)
            trial.close()
    clean, last = [], 0
    for level, name, page in toc:
        level = min(level, last + 1)
        clean.append([level, name, page])
        last = level
    if clean:
        doc.set_toc(clean)
    short = ascii_only(title)[:64]
    for i, page in enumerate(doc, 1):
        label = f"{short}  |  page {i} of {doc.page_count}"
        width = fitz.get_text_length(label, fontname="helv", fontsize=8)
        page.insert_text(((PAGE_W - width) / 2, PAGE_H - 16), label, fontname="helv",
                         fontsize=8, color=(0.4, 0.45, 0.52))
    doc.set_metadata({"title": title, "author": studio_config.name(), "creator": "fe_plan.py (D241)"})
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out), garbage=4, deflate=True)
    pages = doc.page_count
    doc.close()
    return pages, out.stat().st_size, nfig


def rel(p):
    try:
        return str(Path(p).resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p)


def report(doc, out, pages, size, nfig):
    over = "  OVER THE 9 MB CAP" if size > CAP else ""
    print(f"{TAG} {rel(doc)} -> {rel(out)}: {pages} pages, {size / 1e6:.2f} MB, {nfig} figures{over}",
          flush=True)


def inner_main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("doc")
    ap.add_argument("--out")
    ap.add_argument("--per", type=int, default=1)
    ap.add_argument("--dpi", type=int, default=110)
    a = ap.parse_args(argv)
    doc = resolve_doc(a.doc)
    out = Path(a.out) if a.out else PLANS / f"{doc.stem}.pdf"
    if a.cmd == "shots":
        out = PLANS / f"{doc.stem}.pdf" if not a.out else out
    pages, size, nfig = render(doc, out if a.cmd != "check" else PLANS / f"{doc.stem}.check.pdf")
    if a.cmd == "check":
        tmp = PLANS / f"{doc.stem}.check.pdf"
        tmp.unlink(missing_ok=True)
        print(f"{TAG} {rel(doc)}: {pages} pages, {size / 1e6:.2f} MB, {nfig} figures")
        if size > CAP:
            print(f"{TAG} problem: the PDF is {size / 1e6:.1f} MB, over the 9 MB cap")
            return 1
        return 0
    report(doc, out, pages, size, nfig)
    if a.cmd == "shots":
        import fitz
        folder = PLANS / f"{doc.stem}-pages"
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.png"):
            old.unlink()
        pdf = fitz.open(str(out))
        pix = [p.get_pixmap(dpi=a.dpi) for p in pdf]
        per = max(1, a.per)
        for k in range(0, len(pix), per):
            group = pix[k:k + per]
            if len(group) == 1:
                group[0].save(str(folder / f"page-{k + 1:02d}.png"))
                continue
            w = sum(p.width for p in group) + 8 * (len(group) - 1)
            sheet = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, group[0].height), False)
            sheet.set_rect(sheet.irect, (150, 150, 150))
            x = 0
            for p in group:
                p.set_origin(x, 0)
                sheet.copy(p, p.irect)
                x += p.width + 8
            sheet.save(str(folder / f"pages-{k + 1:02d}-{k + len(group):02d}.png"))
        print(f"{TAG} page images in {rel(folder)}")
    return 0


# ---- front --------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--inner":
        sys.exit(inner_main(sys.argv[2:]))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pdf", help="print the PDF and report pages, size and figures")
    p.add_argument("doc")
    p.add_argument("--out")
    p = sub.add_parser("check", help="figures, captions, sources and the size cap")
    p.add_argument("doc")
    p = sub.add_parser("shots", help="render the PDF and write its pages as PNGs")
    p.add_argument("doc")
    p.add_argument("--out")
    p.add_argument("--per", type=int, default=1, help="pages side by side in one image")
    p.add_argument("--dpi", type=int, default=110)
    sub.add_parser("ensure", help="install the renderer into its private venv")
    a = ap.parse_args()
    if a.cmd == "ensure":
        ensure()
        print(f"{TAG} renderer ready in {rel(VENV)}")
        return 0
    doc = resolve_doc(a.doc)
    if a.cmd == "check":
        found = problems(doc, doc.read_text(encoding="utf-8"), load_figures(doc))
        for line in found:
            print(f"{TAG} problem: {line}")
        if found:
            print(f"{TAG} {len(found)} problem(s); the PDF was not rendered")
            return 1
    args = [a.cmd, str(doc)]
    for flag in ("out", "per", "dpi"):
        if getattr(a, flag, None) is not None:
            args += [f"--{flag}", str(getattr(a, flag))]
    code = run_inner(args)
    if a.cmd == "check" and code == 0:
        print(f"{TAG} check clean")
    return code


if __name__ == "__main__":
    sys.exit(main())
