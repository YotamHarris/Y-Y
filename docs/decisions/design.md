# Decisions — Design

Append-only. The **Status:** line under each heading says which entry holds.

---

### D2 — Fog has brightness and texture contrast in every colour scheme (2026-10-05)

**Status:** active

**What happened.** Yotam: "Also give some different color schemes from debug and make sure that what is in the fog has a different contrast to what is outside. Like the cavities kind of look like the fog right now."

**Decision.** TapDemo renders through one palette with four named schemes:
NAVY (the default), EMBER, FOREST and PLUM. Both alternating fog colours must
have linear sRGB luminance at least 0.08 above their scheme's empty field.
Hidden cells also have two light stipple marks that keep a minimum screen size;
open cavities stay plain. Power-up icons have a dark tile and bright frame,
and the red ball has a dark rim.

DEBUG's 310 by 48 logical-unit COLORS row cycles schemes immediately. The
selection is a session rendering preference, independent of the game model,
so RESTART and CLOSE keep it. Ball, bounce and ping-radius controls keep their
existing positions and behavior. Fog reach, visibility and Ping rules remain
unchanged.

**Why.** Brightness plus texture distinguish hidden ground from an open cavity
at both ends of the zoom range. A palette makes every scheme obey the same
contrast rule and lets the owner compare them on the phone.

**Evidence.** V1 records deterministic contrast and actual debug-pointer tests,
and inspected 390 by 844 smoke screenshots for every scheme at fit, middle and
maximum zoom, plus the debug panel.

---
