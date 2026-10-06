# Roadmap

The versions, newest last, append-only. Each is `### Vn title (Dnn)` with a **Status:**
line and, while it ships with known gaps, an **Open:** block.

### V1 Four colour schemes with distinct fog and cavities (D2)

**Status:** active

T5 adds NAVY, EMBER, FOREST and PLUM, selected immediately through DEBUG's
COLORS row. RESTART retains the choice within a session. All rendering colours,
including icons, glows, HUD, panels and fit-zoom margins, come from the palette.
Fog is lighter and stippled; cavities are plain and dark. The red ball has a
dark rim. Existing goal, fog and Ping gameplay and instruction text are unchanged.

**Validation.** `scripts/build.ps1 -Smoke -ToolsRoot 'D:/Source/Y&Y/YYEngine'`
passed the Windows Release build, deterministic CTest (1/1) and 120-frame smoke.
`build/windows/yy_tests.exe` checks both fog shades in every scheme against a
minimum linear sRGB luminance gap of 0.08. The measured minimum gaps are:

| Scheme | Fog minus cavity luminance |
| --- | ---: |
| NAVY | 0.115482 |
| EMBER | 0.124513 |
| FOREST | 0.120002 |
| PLUM | 0.118283 |

The same executable exercises the actual Game pointer handlers and render
output without SDL: DEBUG, opposite edges of COLORS, every scheme and wrap,
RESTART, reopen, CLOSE and reopen. Labels and field/fog colours change together;
scheme and ping radius persist, and pending ball/bounce settings still apply.

**Visual evidence.** For each scheme, set `YY_TAPDEMO_SCHEME` to its uppercase
name, set `YY_TAPDEMO_SCENE` to `palette`, `palette-fit` or `palette-max`, and run
the smoke command above. `palette` uses zoom 1.25; the others cover fit and
maximum zoom 2.5. `YY_TAPDEMO_SCENE=debug` captures the panel. Each run saves
`build/windows/smoke.bmp`; copy it before the next run. The final batch script
`.studio/t5/capture.ps1` and log `.studio/t5/capture.log` record 13 successful
build/test/smoke runs. BMPs were converted losslessly to PNGs and every final
390 by 844 image was inspected:

| Scheme | Fog compared with the cavity |
| --- | --- |
| NAVY | Light slate-blue stipple around a plain dark-navy cavity. |
| EMBER | Light tan stipple around a plain dark-brown cavity. |
| FOREST | Light sage stipple around a plain deep-green cavity. |
| PLUM | Light lavender stipple around a plain dark-violet cavity. |

All four show readable coloured bricks, bright power frames, the cyan Ping
ring and the red ball at fit, middle and maximum zoom. Fit margins match the
scheme. The panel shows COLORS below PING RADIUS, with RESTART, CLOSE and the
footer visible and no clipped controls.

The screenshots are `.studio/t5/<lowercase scheme>-palette.png`,
`<lowercase scheme>-palette-fit.png`, `<lowercase scheme>-palette-max.png` and
`.studio/t5/debug.png`, attached to T5's result. These are debug fixtures:
power-ups and a fogged goal are placed deliberately, a real slingshot activates
Ping, and the model pauses at activation to compare schemes at the same moment.
Normal play does not pause or place these fixtures.

**Open:** Yotam's phone review and preferred default are pending; NAVY remains
the default. Device performance is not applicable to this rendering task.
Desktop smoke metrics do not establish iPhone performance.

### V2 Debug grid size, glow rate and power-up weights (D3)

**Status:** active

T7 adds GRID SIZE, GLOWING and five POWER-UP WEIGHTS steppers to DEBUG, which
now spans 96 to 832 of the 844 logical height with ten 44 by 40 stepper pairs.
RESTART applies them with balls and bounces and refits the camera; the footer
says so.

**Tests.** `scripts/build.ps1` runs `yy_tests`. `settingsChecks` restarts 30
seeds at 12 by 20, 42 by 70 and 60 by 100 and checks the size, pockets a cell
in from the walls and clear of fog, a plain fogged goal, the camera fit and pan
limits. A glow rate of 0 and all weights 0 give no glowing bricks; 25% gives
23 to 27%; weights 0:1:2:3:4 over 200 seeds never pick Bomb and keep each kind
within 2 points of its share (37,629 glowing bricks: 0, 3790, 7506, 11411,
14922). A hash of 100 default seeds equals the one the previous code produced,
so the defaults play as before. The palette test drives the real Game pointer
handlers: the steppers change the shown values, 24 by 40 stays until RESTART,
which builds 12 by 20, then 60 by 100, each fitted.

**Visual evidence.** `YY_TAPDEMO_SCENE=debug`, `grid-min` and `grid-max` with
the smoke command. The grid scenes press DEBUG, the size stepper and RESTART.
Inspected 390 by 844 captures: the panel's rows, values (60X100 drawn at 1.75
scale to fit between the buttons) and footer are legible and unclipped; 12 by
20 and 60 by 100 both fill the play-area width at minimum zoom with fog,
pockets and glowing bricks.

**Open:** Yotam's phone review. At 60 by 100 a cell is about 6.5 points at
minimum zoom, so play needs zooming in. Device performance is not applicable.

### V3 Power-up tuning and snapped straight shots (D4)

**Status:** active

T8 turns the bomb size, lightning seconds and reach into Model settings with
the new snap angle, enforces one-hit glowing bricks and wall-free bombs in
generation, and draws the bomb burst, the electric aura and the aim line from
the settings. The instructions card names the bomb's square. DEBUG now covers
12 to 842 of the 844 logical height: fourteen 44 by 40 stepper pairs on a
44-point pitch under three headings (APPLY NOW: ping radius, bomb size, zap
seconds, zap reach, snap angle; APPLY ON RESTART: balls, bounces, grid size,
glowing; the weights), then COLORS (310 by 40), RESTART and CLOSE (150 by 44).
The footer notes gave way to the headings.

**Tests.** `scripts/build.ps1` runs `yy_tests`. A bomb clears exactly the 5
by 5 around it and sets off the Ping and Bomb inside it; size 3 clears a 3 by
3; sizes round to odd and clamp. A still electric ball zaps bricks 2, 2.24
cells away and not 2.83 or 3, is still electric at 5.8 s and stops at 6 s
after 23 to 24 zaps; reach and time clamp and survive restart. Over bomb sizes
3, 5 and 9, grids 12 by 20, 24 by 40 and 42 by 70 and 12 seeds at 25% glow,
every glowing brick has 1 hit point and no bomb lies within half its blast of
a wall. With weights 2:1:1 Bomb keeps half the inner share and the wall band
splits evenly between the other two. Launches 4 degrees off either axis, both
directions, fly exactly straight; 6 degrees keep their angle; snap 0 changes
nothing. Player path: a 3 degree pull through Touch shows a snapped aim and
launches with a vertical velocity of exactly 0. The palette test drives the
real Game: the new rows show 5X5, 6, 2.5 and 5 DEG, step, and survive
RESTART; the moved rows still work. The pinned default-grid hash changed to
8933398709070464508 because glowing bricks and bomb places changed.

**Visual evidence.** `YY_TAPDEMO_SCENE=debug` and the new `snap` scene (a pull
3 degrees off horizontal) with the smoke command. Inspected 390 by 844
captures: every row, heading and button is legible and on screen; the aim dots
run exactly horizontal while the band to the finger keeps its slight tilt.

**Open:** Yotam's phone review of the numbers and of tapping the denser panel.
A bomb near the wall can still exist when the bomb size is raised after the
grid was generated. Device performance is not applicable.

### V4 Ten fixed levels, retries on the same field (D5)

**Status:** active

T10 adds the level table, `Model::play`, saved progress through the new
`yy::Storage` service, the LEVEL header, the next-level and retry overlays, a
level card that names the new power-up and lists only the level's own, and
DEBUG's LEVEL row (FREE, 1 to 10). DEBUG now has fifteen 44 by 38 stepper pairs
on a 42-point pitch (APPLY NOW at 66; APPLY ON RESTART at 298: level, balls,
bounces, grid size, glowing; FREE PLAY WEIGHTS at 530), COLORS at 746 (310 by
38), RESTART and CLOSE at 792.

| Level | Feel | Power-ups | Grid | Balls / bounces | Random player wins |
| --- | --- | --- | --- | --- | ---: |
| 1 | relief | Bomb (new) | 12x20 | 8 / 15 | 99% |
| 2 | build-up | Bomb, 12% glow | 12x20 | 5 / 12 | 66% |
| 3 | relief | Electricity (new) | 12x20 | 8 / 15 | 86% |
| 4 | fu | Electricity, 4% glow | 18x30 | 5 / 10 | 19% |
| 5 | build-up | Speed (new) | 18x30 | 7 / 15 | 64% |
| 6 | fuck yeah | Bomb + Electricity, 15% | 18x30 | 8 / 15 | 100% |
| 7 | relief | Ping (new) | 18x30 | 8 / 15 | 78% |
| 8 | build-up | Ping + Bomb | 24x40 | 7 / 15 | 59% |
| 9 | fu | Ghost (new), 3% glow | 24x40 | 5 / 12 | 14% |
| 10 | fuck yeah | all five, 15% | 30x50 | 10 / 15 | 99% |

The seeds are placeholders picked by hand with a throwaway bot (one ball at a
time from a random open cell in a random direction, 200 tries per seed) so
that every level is winnable; T11 tunes them.

**Tests.** `scripts/build.ps1` runs `yy_tests`. For every level: the grid,
balls, bounces, tuning, glow and weights match the table; the goal is plain
and fogged; only the level's kinds glow; the field is the same from two models
with different histories; five shots flown out, a restart, and the same five
shots give identical bricks, powers, goal, balls left and fired power-ups with
Ghost landings. A Ghost brick placed above level 9's pocket lands the ball on
the same cell on three tries. Power-ups are introduced Bomb, Electricity,
Speed, Ping, Ghost, each alone and before any use. A win advances (10 leads to
free play), a loss keeps the level, and `level N` saves round-trip; damaged
saves open level 1. Through the real Game with an in-memory storage: a new
player opens level 1 with the Bomb card; a save of level 3 opens level 3 with
only Electricity listed; DEBUG RESTART of level 3 draws the identical field;
the picker steps to 4 (opened and saved), to FREE (a 24x40 field, save kept)
and stops at 10; the `won` scene on level 1 shows TAP FOR NEXT LEVEL and the
tap opens level 2 on its card and saves it; the new `lost` scene on level 2
shows TAP TO RETRY and the tap redraws level 2's field colour for colour, with
no card and no save; winning level 10 leads to FREE PLAY. Making `play` skip
the seed reset fails the tests.

**Visual evidence.** `scripts/build.ps1 -Smoke` then `TapDemo.exe --smoke 150`
with `YY_TAPDEMO_LEVEL` (new: pins a level without saving) and
`YY_TAPDEMO_SCENE`: level 3 card, `header`, `debug`; level 1 `won`; level 2
`lost`; level 10 `won`; free play `grid-min`. Inspected 390 by 844 captures:
LEVEL 3 and LEVEL 10 fit between the ball counter and DEBUG; the card names
ELECTRICITY under NEW POWER-UP; the overlays read TAP FOR NEXT LEVEL, TAP TO
RETRY and TAP FOR FREE PLAY; every debug row is on screen with the free-play
rows muted while a level is picked.

**Open:** the player path on a phone or simulator (play level 1 to a win, see
level 2, lose, retry) is Yotam's TestFlight check; the Windows tests drive the
same Game handlers. Seeds and numbers are T11's. Debug settings other than the
level are still session-only (T9). Device performance is not applicable.

### V5 Level seeds tuned by simulated play to their experiences (D6)

**Status:** active

T11 adds a headless bot (`tests/level_bot.hpp`), a seed search tool
(`level_tune`, built beside the tests and not run by them), per-feel targets in
`tapdemo::target()`, `Model::play(level, table)` for trying another seed, and a
`field` smoke scene (the opening field, card dismissed). It writes ten new
seeds into the level table and drops levels 6 and 10 to 3 balls. The report is
`docs/levels.md`.

| Level | Feel | Seed | Balls | Bot wins (2,000 games) | Experience metric |
| ---: | --- | ---: | ---: | ---: | --- |
| 1 | relief | 2651 | 8 | 100% | all wins through a Bomb |
| 2 | build-up | 2913 | 5 | 55.3% | 99.5% of wins through a Bomb |
| 3 | relief | 263 | 8 | 100% | 72% of wins through lightning |
| 4 | fu | 718 | 5 | 13.3% | 64.6% of losses saw the goal |
| 5 | build-up | 547 | 7 | 55.4% | 93.6% of losses saw the goal |
| 6 | fuck yeah | 2877 | 3 | 41.0% | 58.6% of wins on the last ball, all chains |
| 7 | relief | 1751 | 8 | 99.9% | direct hits |
| 8 | build-up | 2173 | 7 | 52.3% | 99.9% of wins through a power-up |
| 9 | fu | 1247 | 5 | 12.8% | 86.4% of losses saw the goal |
| 10 | fuck yeah | 1440 | 3 | 44.2% | 58.4% of wins on the last ball, all chains |

**Tests.** `yy_tests` plays each level 200 times with bot seeds 1 to 200 and
fails when its win rate, last-ball share, chain share or near-loss share leaves
its feel's target, or when a later level of the same feel is easier by more than
5 points; it also plays levels 1 to 3 with every shot pressed, dragged and
released through `tapdemo::Touch` on the fitted camera and requires the same
band within 5 points of the direct shots (100%, 55.5%, 100%). Restoring level
2's old seed fails it ("level 2: the win rate is outside its feel's band", 86.5%).
The test adds about a second.

**Visual evidence.** `TapDemo.exe --smoke 30` with `YY_TAPDEMO_LEVEL` 1 to 10
and `YY_TAPDEMO_SCENE=field`: `docs/figures/levels/level-01.png` to
`level-10.png`. Inspected: each shows LEVEL N in the header with its ball count
(3 on levels 6 and 10), the cavities ringed by the level's own glowing kinds,
and the rest under fog.

**Open:** the bot is not a person; whether relief feels too easy (the bot wins
100%) and whether level 6's losses feel near (0.7% see the goal) are Yotam's
calls on TestFlight. Speed and Ping break nothing, so the numbers cannot show
levels 5 and 7 teach them. Device performance is not applicable.

### V6 Three screen reskin concepts for owner selection (G12)

**Status:** active

T14 proposes three static looks in `docs/reskin-concepts.md`: Toy Workshop
(Toon Blast), Jewel Vault (Royal Match) and Sugar Mist (Candy Crush Saga).
The 6 October 2026 Apple US games free and grossing chart snapshots and
credited publisher screenshots are stored beside the original artwork in
`docs/figures/reskin-concepts/`. Each look keeps the captured grid, hit points,
fog boundary, cavity, ball, aim line, header and instructions card, with one
small named twist. Toy Workshop is recommended at an estimated 3-4
engineering days plus 2 art days, including real font support.

**Visual evidence.** The plan PDF compares the current aiming screen with all
three looks. Each has full 1170 by 2532 aiming, instructions and six-icon
fixture PNGs, reviewed at 390 by 844. Every page of the final 14-page PDF was
read at phone size after `fe_plan.py check`, `pdf` and `shots`. The six-icon
fixture is a debug scene, not a normal level. Calculated fog-minus-cavity
luminance gaps are 0.424, 0.388 and 0.470, all above the palette rule's 0.08
minimum and accompanied by a shape cue. No game, engine or palette changes.

**Open:** Yotam chooses a look. Accepting the plan starts Toy Workshop's
implementation; another look needs a named change first. Static images do
not establish animated juice, touch feel, BMP alpha support or iPhone
performance. Those checks belong to the accepted implementation. Runtime
tests, player-path and device checks are not applicable to this concept round.
