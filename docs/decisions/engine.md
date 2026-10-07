# Decisions — Engine

Append-only. The **Status:** line under each heading says which entry holds.

---

### D7 — Sprites blend premultiplied alpha; real text comes from a font baked ahead of time (2026-10-06)

**Status:** active

**What happened.** Garden Pop, accepted in `docs/reskin-concepts.md`, needs
"licensed real-font support" and asks to "prototype BMP transparency and edge
handling before committing to these cutouts". T15 chose the route its plan
named: alpha from 32-bit BMPs and a font baked into glyph sheets, with no new
runtime library.

**Decision.** Every sprite texture holds premultiplied alpha, blends with
`SDL_BLENDMODE_BLEND_PREMULTIPLIED` and filters linearly. A sheet cell is drawn
with `sprite(asset, source, destination)`, which samples half a pixel inside
`source`; sheet cells keep a transparent border. Real text uses
`label(font, position, text, size, colour, align)`: `scripts/generate-assets.py`
bakes the committed OFL font (Fredoka SemiBold) with Pillow into 8-bit coverage
sheets at 16, 24, 36, 54, 80 and 120 pixels plus a `.font` metrics file. The
renderer draws from the smallest bake at least as large as the text on screen,
tinted, on whole device pixels. Layout (`yy/font.hpp`) is SDL-free and unit
tested. The debug font stays for the debug panel.

**Why.** Straight alpha filtered linearly pulls in the black of clear pixels
and leaves a dark rim; premultiplied texels fade to nothing. A baked sheet
needs no font library on iOS, and several sizes keep text sharp from a 1x
desktop capture to a 3x phone without mipmaps, which the SDL renderer lacks.
Sprites have no mipmaps either: art is sized so it is not shrunk past about 2x.

**Evidence.** V8.

### D11 — TapDemo also builds for the browser, to check a change without a TestFlight build (2026-10-07)

**Status:** active

**What happened.** Yotam asked for "a tater way to verify changes without a TestFlight build" and chose "the
playable web build (link per commit, real touch, no haptics)". TestFlight waits on the hosted Mac and Apple
processing.

**Decision.** The `web` CMake preset builds TapDemo with Emscripten, on the same pinned SDL3 commit and the
same runtime. The emsdk version is pinned in `cmake/web/emsdk-version.txt` and CMake refuses another. The
runtime changes only under `__EMSCRIPTEN__`: the browser drives the frame loop (SDL's callbacks), haptics are
`NoHaptics`, the renderer driver is SDL's default (WebGL), a hidden tab pauses like backgrounding, the page's
safe-area insets reach the safe area, and `PreferenceStorage` flushes `/libsdl` (SDL's preference root,
mounted as IDBFS before `main`) to IndexedDB after every write. Assets are packaged at `/assets/tapdemo/`.
`cmake/web/shell.html` is the page: a full-screen canvas that takes every touch (`touch-action: none`, no
pinch zoom, no scroll). `checks.yml` builds it, uploads `tapdemo-web`, and `tests/web_smoke.py` plays it in
headless Chromium at 390x844 with touch.

**Why.** It reuses the game's code, so what Yotam plays in a browser is the build the iPhone gets, minutes
after a push. It cannot say how the game performs on an iPhone: a browser has no Metal, no haptics, other
timing and other memory limits, so the web build is for looks, touch and rules only, never for performance.

**Evidence.** V12.

---
