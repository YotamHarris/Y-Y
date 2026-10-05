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

### D3 — Debug sets the grid size, glow rate and power-up mix, applied on restart (2026-10-06)

**Status:** active

**What happened.** Yotam: "control from debug the size of the grid and ratio of power ups and likelihood of encountering them". In the planning thread he chose one size stepper that keeps the 24 by 40 shape, a control for how many bricks glow, and a separate weight for each power-up kind.

**Decision.** The grid size is a per-Model setting in steps of the 3:5 shape:
6k columns by 10k rows, k from 2 to 10 (12 by 20 to 60 by 100), default 4
(24 by 40). The glow rate is a share of bricks in half percents, 0 to 25%,
default 2%; DEBUG steps it by half a percent up to 5% and by whole percents
above. Bomb, Electricity, Ping, Ghost and Speed each have a weight from 0 to 9,
default 1, and a glowing brick picks its kind in proportion to the weights.
All weights 0 means no brick glows. The goal is always placed. Like balls and
bounces these settings are pending in DEBUG until RESTART, which also refits
the camera; they last for the session only.

With the defaults the random draws are the same as before, so every seed
generates the same grid.

**Why.** One stepper keeps the board's proportions for the phone's play area,
so the camera fit and fog look the same at every size. Weights let the owner
tune each kind's likelihood independently of how many bricks glow.

**Evidence.** V2.

---
