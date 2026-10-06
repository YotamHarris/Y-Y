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

### D4 — Wider bombs kept off the walls, longer lightning, one-hit power-ups and snapped straight shots (2026-10-06)

**Status:** active

**What happened.** Yotam: "Increase the range of the bomb and give debug option for it. Make the lighting more powerful and longer and add debug option for it. Do not allow the bomb to spawn near the edges of the map. Make straight lines easier to achieve by clamping a straight line horizontal or vertical if the angle is close to it about 5 degrees for now. Power ups should always require one hit, currently it's random if they are under 1 2 or 3." The numbers below are the planner's reading of "increase", "more powerful and longer" (a wider reach) and "about 5 degrees"; Yotam tunes them from DEBUG.

**Decision.** A bomb clears a 5 by 5 square (odd sizes 3 to 11). An electric
ball zaps for 6 s (1 to 15) every brick whose centre is within 2.5 cells (1 to
6, in half cells). A launch within 5 degrees (0 to 15; 0 is off) of horizontal
or vertical flies exactly along that axis, and the aim line shows the snapped
direction: one function, `snapPull`, serves both. These four are Model settings
like the ping radius: DEBUG changes them at once and restart keeps them, for
the session only.

Every glowing brick has 1 hit point. A Bomb is never placed closer to a wall
than half its blast (the bomb size when the grid is generated): there the
brick's kind is picked among the other kinds by their own weights, and with
only Bomb weighted it stays plain. The random draws are unchanged, so a seed
still gives the same pockets and goal search; its bricks' hit points and kinds
differ where these rules apply.

**Why.** A bomb by the wall wastes part of its blast; a glowing brick that
needs three hits hides its power-up behind luck. Snapping only within a small
angle keeps every other aim exact.

**Evidence.** V3.

---

### D5 — Ten fixed levels in order; a level's seed decides it and every retry (2026-10-06)

**Status:** active

**What happened.** Yotam: "level shouldn't randomize so the player will have a more info as he retries the level", and the first levels should focus "on the uniqueness of one powerup and gradually pilling up". Power-up order is Yotam's: Bomb, Electricity, Speed, Ping, Ghost. The feel of each level (relief, build-up, fu, fuck yeah) is the planner's starting plan, which T11 retunes.

**Decision.** TapDemo plays ten fixed levels from a table in the model
(`tapdemo::levels`). Each holds a seed, its feel, the power-up it introduces,
the grid scale, glow share and weights, balls, bounces, bomb size, lightning
seconds and reach, and ping radius. `Model::play(n)` resets the random state
to the seed before building the field, so the bricks, the goal and every Ghost
landing repeat on a retry. A level that introduces a power-up weights only that
kind; later levels stack them. Grids grow from 12 by 20 (levels 1 to 3) to 30
by 50 (level 10).

A new player starts at level 1. A win offers the next level, a loss a retry
(straight back into play, no card); winning level 10 leads to free play. Free
play is the old random field from the advancing random state with the debug
grid settings. The reached level is saved as `level N` in `progress.txt` in the
app's preference folder (a new `yy::Storage` engine service, written through a
temporary file and a rename), and the app opens at it. DEBUG's LEVEL row picks
1 to 10 or FREE; balls, bounces, grid, glow and weights apply to free play only.

**Why.** A field that stays the same lets a retry use what the last try
revealed. One new power-up per level teaches each before they combine.

**Evidence.** V4.

---

### D6 — Level seeds are picked by a simulated player against each level's experience (2026-10-06)

**Status:** active

**What happened.** Yotam asked for four experiences: relief levels you must win, build-up levels with a "Lower success rate 50 - 60%" that teach something new, "Fuck yeah" levels that look lost until "on the last ball some chain reaction is triggered and goal is achieved", and "Fu" levels where the player fails but "sees it as a near miss". He chose "Fixed random seed per level, tuned by picking good seeds". The 35 to 50% win band for fuck-yeah levels is the worker's choice within the task's "~35–50%".

**Decision.** Each feel has a target in `tapdemo::target()` beside the level
table: relief at least 90% wins; build-up 50 to 60%; fu at most 25% with at
least half the losses near (the goal seen, or within 2 cells of an open cell);
fuck yeah 35 to 50% with at least half the wins on the last ball and at least
half through a power-up chain. A headless bot (`tests/level_bot.hpp`) plays a
level one ball at a time from random open cavity cells, 60% of shots aimed (with
up to 4 degrees error) at the goal once it shows, else a visible glowing brick,
the rest at random, with the snap applied. `level_tune` searches thousands of
seeds per level and keeps one whose results over 2,000 and over the first 200
bot games both meet the target. The band test replays the 200 in `yy_tests`.
Levels 6 and 10 drop to 3 balls: with 8 or 10, no seed put most wins on the
last ball. Within a feel, a later level may not be easier by more than 5 points.

**Why.** A seed the bot cannot meet a band with is visibly wrong before anyone
plays it, and the test stops a model change from silently moving a level out
of its experience. The bot is not a person: a person will win more often.

**Evidence.** V5, `docs/levels.md`.
