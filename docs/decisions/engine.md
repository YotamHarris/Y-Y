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
