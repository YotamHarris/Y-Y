# Decisions — Design

Append-only. The **Status:** line under each heading says which entry holds.

---

### D2 — Fog has brightness and texture contrast in every colour scheme (2026-10-05)

**Status:** amended by D9 -- only Garden Pop remains; the four other schemes and COLORS are removed

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
Yotam: "all levels should have the same amount of balls": every level gives
`levelBalls` (4), which is the count where both fuck-yeah levels get most wins
on the last ball; difficulty varies by seed, grid, glow and bounces. Within a
feel, a later level may not be easier by more than 10 points.

**Why.** A seed the bot cannot meet a band with is visibly wrong before anyone
plays it, and the test stops a model change from silently moving a level out
of its experience. The bot is not a person: a person will win more often.

**Evidence.** V5, `docs/levels.md`.

---

### D8 — Garden Pop draws damage as materials and keeps hit effects outside the model (2026-10-06)

**Status:** amended by D9 -- Garden Pop is the only look, on every launch

**What happened.** T16 implements the accepted concept 1 of
`docs/reskin-concepts.md`: Garden Pop's material damage, garden art, ladybird
flag, short hit sequence and licensed font support.

**Decision.** Garden Pop is the default look for a fresh installation. Three,
two and one remaining hits select dense grass, clipped turf and bare soil;
cleared cells show dark soil. Ordinary bricks have no digits in Garden.
The four existing palettes retain their appearance and saved indices, remain
selectable in DEBUG / COLORS, and valid saved choices are preserved.

Generated alpha BMPs supply the garden materials, wooden holders, glossy ball,
ladybird pennant, mist and UI. Existing power silhouettes and rules remain.
Fredoka draws the header and instructions inside their existing bounds; three
pictures teach the damage progression. The darkest production mist pixel must
stay at least 0.08 linear-sRGB luminance above the brightest cavity pixel,
with an exposed stepped rim providing a separate shape cue.

A renderer-owned timer observes hit-point decreases and plays a local 200 ms
squash, contact flash and clipping sequence. It never changes the model, grid,
collision areas or input timing. Balls and the current aim draw above it.
Normal levels, seeds, hidden-goal logic and both zoom limits are unchanged.

**Why.** Material coverage teaches damage without numbers, while the short
local pop makes contact visible during continuous aiming and shooting.

**Evidence.** V9 and `docs/garden-pop-validation.md` record production pixel
checks, live touch tests and nine inspected runtime/mockup pairs at 390 wide.

---

### D9 — TapDemo has one look, Garden Pop, and ignores a saved colour scheme (2026-10-06)

**Status:** active

**What happened.** Yotam: "set the default theme to the garden theme. get rid of all other themes but the main one". Read as: Garden Pop is the main one. Garden Pop was the default only on a fresh install; a phone with debug settings saved before it existed reopened in the scheme it remembered, and DEBUG still had a COLORS button.

**Decision.** Garden Pop is the only palette and the only drawing path: the NAVY, EMBER, FOREST and PLUM palettes, their rectangle bricks, stipple fog, plain header, plain instructions card and circle ball, the COLORS button and `YY_TAPDEMO_SCHEME` are removed. `DebugSettings` has no scheme; an older `debug 1` save with a `scheme N` line (any N) loads with every other setting intact and the line is ignored, and a save written now has none. The fog must still be at least 0.08 linear-sRGB luminance above the cavity (D2's rule, now for Garden Pop alone). Garden Pop's art, colours, fog and gameplay are unchanged.

**Why.** One look is what the owner asked for, and it removes the drawing paths nobody can reach, which had to be kept in step with every rendering change.

**Evidence.** V10, `tests/palette_tests.cpp` (old saves with `scheme 0` and `scheme 3` through the real Game, no COLORS button) and `tests/core_tests.cpp`.

### D10 — Breaking the goal plays a celebration, presentation only (2026-10-06)

**Status:** active

**What happened.** Yotam: "add a festive goal celebration maybe a zoomed in as in peggle game". His choices: slow motion and zoom just before the ball hits the goal, like Peggle; about 3–4 seconds before the win card, and a tap skips it; the same full celebration on every goal. Breaking the goal gave a small burst, three beeps and the plain "GOAL FOUND" card at once.

**Decision.** Every goal win, in a level or in free play, plays the same celebration, in the game's render and update code only (`celebration.hpp`, `game.cpp`); the model, levels, Ghost landings and touch rules are unchanged. While a ball flies, a copy of the `Model` runs 0.4 s of game time ahead by the real frame's step (its random state copied, so a power-up chain or a Ghost landing is part of the copy). If the goal breaks inside that window, time eases to 0.25x and the camera eases to 2.2x onto the ball, then the goal. The model always steps by the real frame's dt, on the frames the scaled clock earns a step, so slow motion never changes where a ball goes (the balls are drawn a little ahead of the model between steps). The break plays a bigger burst on the goal, confetti and petals in the Garden Pop palette, a five-note rising tune and a strong haptic; 0.55 s later the camera eases back to the framing the player left, "GOAL!" pops in, and at 2.0 s after the break the win card shows (Garden Pop card art) with the balls left counting up from 0; the whole show is about 3.5 s. One tap at any point from the push-in to the card jumps to the card (a shot still in flight finishes unseen by the same steps) and does not also advance; the next tap advances. A look-ahead that sees a hit which does not come eases back with nothing broken. Pausing the app settles the show: the camera and time scale are put back, and a celebration past the break goes to the card. The OUT OF BALLS card is unchanged.

**Why.** The moment the goal breaks is the game's payoff; the slow push-in makes the last half second readable and the card no longer cuts it off. Keeping the model's steps identical is what lets the slow motion promise the same shot wins.

**Evidence.** V11, `tests/palette_tests.cpp` (`celebrationChecks`: the look-ahead exact on a plain win, a win through a power-up and a win through a Ghost landing, none on lost shots; the same winning shot through the game's touch path with the slow motion ends with the balls left the bare model ends with; skip, pause and next tap) and `docs/figures/celebration/`.

---

### D12 — Every hit has feedback, bricks show cracks, and the fog peels away (2026-10-07)

**Status:** active
**What happened.** Yotam: "Something feels off with the gameplay it's not fun enough", and from the review he chose a hit that registers, bricks that read as damage instead of a spreadsheet, and a reward for uncovering the field.

**Decision.** Presentation only (`juice.hpp`, `game.cpp`); the model, seeds and levels are unchanged. A brick hit holds the drawn balls for 45 ms (the simulation keeps running). A break throws chips in the brick's colour (a power-up's glow, the goal's pink) from a fixed pool of 320 specks, and shakes the board a little, more for lightning (2.5), a bomb (4) and the goal (6), within 8 (owner, after trying it: "the screen shake is too aggressive. give us controls on the amount of screen shake and tone it down a bit"). The debug panel has a SHAKE button (OFF, LOW 0.5x, MEDIUM 1x, HIGH 1.5x; MEDIUM by default) that applies at once and is saved with the debug settings. Balls leave a fading trail from the same pool. Each hit or break of a flight raises the break sound one pentatonic step (660 Hz up to two octaves); a launch resets it. A bomb adds a full-strength haptic. Hit points show as cracks over the brick (whole at 3, cracked at 2, badly cracked at 1) on top of the existing materials, which keep the colours distinct; glowing bricks and the goal breathe a soft halo under their unchanged icons. Cells a break uncovers keep their fog for 0.3 s while it peels away with a soft sound; a power-up brick or the goal found that way also gets a ring of sparkles for 0.7 s.

**Why.** A hit should be felt, damage should be seen without reading a number, and finding something in the fog should be a moment. Effects only read `Hits` and the brick changes, so no shot or level result can move.

**Evidence.** V13, `tests/palette_tests.cpp` (`juiceChecks`), the level band test unchanged, and `docs/figures/juice/`.

---

### D13 — Levels open framed on the visible cavity and ease outward as it grows (2026-10-07)

**Status:** active

**What happened.** T24 records Yotam choosing item 2 of the gameplay review.
The opening cavity in level 6 occupied about a fifth of a 390x844 screen.

**Decision.** A game-layer camera frames all open cells and visible bricks,
including special bricks shown by Ping, with one cell of margin clipped to the
grid. It opens at the largest zoom fitting that box below the header, capped
at 2.5 times the whole-grid fitted cell size. Existing grid-edge clamping and
the manual zoom limits remain. Revealed bounds accumulate for the level; the
camera eases toward the enlarged frame with an exponential rate of 4 per
second, never zooming inward again. A held finger, debug panel or goal
celebration pauses automatic following. Pinch, wheel or pan takes ownership
until a new level or retry restores the opening frame. Placement and sling
drag keep using the existing safe-area viewport and camera world mapping.

**Why.** Larger bricks make the opening action readable, and following the
revealed area keeps new cavities in view without mid-shot jumps or fighting
the player's camera. This changes presentation, not the simulation or levels.

**Evidence.** V14, `docs/framing-validation.md`, deterministic framing tests,
zoomed viewport touch tests and six inspected 390x844 opening captures.

---

### D14 — The aim line shows the first bounce, and game speed follows the shot (2026-10-07)

**Status:** active

**What happened.** T25 records Yotam choosing items 4, 5 and 9 of the gameplay
review: shots should be plans rather than hopes, watching 15 bounces takes too
long, and the last ball should carry tension.

**Decision.** While the player pulls, the line is the launch's real path up to its
first wall or brick contact, plus a short stub of the way it leaves.
`Model::aimPath` runs the same snap, speed, sub-steps and collision
(`Model::stepBall`) that `update()` uses, so the line cannot lie. A brick draws as
a brick or a wall alike, so a fogged brick is blocking but never revealed.
The game clock takes more fixed steps per frame (never a faster ball, never a
different dt): 1x with nothing flying, rising with the oldest flying ball's
bounces used to 2.5x on its last. The last ball shows a LAST BALL banner from the
moment it is held until it is spent, with a tighter hum (0.5 to 1.0 where other
balls hum 0.25 to 0.75). When the last ball, or any ball once none are left, is
within 2 cells of a goal that is visible or pinged, the speed eases down to 0.35x
(overriding the speed-up) and eases back afterwards.

**Why.** The line turns a hope into a plan, the speed-up cuts the wait without
touching any outcome, and the slowed last ball lets the player watch the moment
that decides the level. The model and its seeds are untouched, so the level band
test holds unchanged.

**Evidence.** V15, `tests/palette_tests.cpp` (`paceChecks`) and `docs/figures/aimline/`.

---

### D15 — The board and its cull share the play area (2026-10-07)

**Status:** active

**What happened.** T27: Yotam saw bricks not drawing when the screen moved. The cull used the 390x764 play area, but
the field and its mist drew unclipped, so in a window that is not 390x844 (the engine letterboxes the frame) they spilled
into the strips beside it, where no brick was ever drawn.

**Decision.** The field, mist and all board effects draw inside the play-area clip (`Camera::view`, below the header).
`Renderer::clip` is required of every renderer; SDL maps it through the safe-area viewport to framebuffer pixels.
`Camera::visibleCells` uses the same rectangle with one extra cell on every side, including the drawing camera's shake.
Cells beyond that margin stay skipped. The clip is lifted before drawing the header and other UI; letterbox strips keep
the engine's backdrop.

**Why.** A clean backdrop beside the game keeps the board's visible boundary consistent across phone safe-area shapes,
wider windows and taller windows. A phone's safe area can have a different aspect ratio from the logical frame too.

**Evidence.** V16, `visibleCellChecks`, `boardRenderingChecks` and [the matched phone and shake captures](../letterbox-validation.md).

---
