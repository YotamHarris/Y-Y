# Roadmap

The versions, newest last, append-only. Each is `### Vn title (Dnn)` with a **Status:**
line and, while it ships with known gaps, an **Open:** block.

### V1 Four colour schemes with distinct fog and cavities (D2)

**Status:** superseded by V10 -- only Garden Pop remains

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
seeds into the level table. Yotam: "all levels should have the same amount of
balls", so every level gives 4 (`levelBalls`); levels 6 and 7 get 25 and 20 bounces. The report is
`docs/levels.md`. Each level's card shows the bot's 2,000-game win rate as
EXPECTED WINS N% (Yotam), from `Level::expectedWins` (captures:
`docs/figures/levels/card-02.png`, `card-10.png`).

| Level | Feel | Seed | Balls | Bot wins (2,000 games) | Experience metric |
| ---: | --- | ---: | ---: | ---: | --- |
| 1 | relief | 1659 | 4 | 100% | all wins through a Bomb |
| 2 | build-up | 2920 | 4 | 54.9% | 99.5% of wins through a Bomb |
| 3 | relief | 334 | 4 | 98.4% | 85.6% of wins through lightning |
| 4 | fu | 893 | 4 | 12.9% | 54.4% of losses saw the goal |
| 5 | build-up | 522 | 4 | 54.8% | 78.7% of losses saw the goal |
| 6 | fuck yeah | 948 | 4 | 38.9% | 50.9% of wins on the last ball, 71.3% through a chain |
| 7 | relief | 1751 | 4 | 92.8% | direct hits |
| 8 | build-up | 2669 | 4 | 54.8% | 99.5% of wins through a power-up |
| 9 | fu | 1009 | 4 | 12.5% | 58.3% of losses saw the goal |
| 10 | fuck yeah | 1762 | 4 | 44.2% | 61.8% of wins on the last ball, all chains |

**Tests.** `yy_tests` plays each level 200 times with bot seeds 1 to 200 and
fails when its win rate, last-ball share, chain share or near-loss share leaves
its feel's target, or when a later level of the same feel is easier by more than
10 points; it also plays levels 1 to 3 with every shot pressed, dragged and
released through `tapdemo::Touch` on the fitted camera and requires the same
band within 5 points of the direct shots (100%, 55%, 99%). With level 2's old
placeholder seed the test failed ("level 2: the win rate is outside its feel's
band", 86.5%) before it was replaced; it also checks that every level gives
`levelBalls`.
The test adds about a second.

**Visual evidence.** `TapDemo.exe --smoke 30` with `YY_TAPDEMO_LEVEL` 1 to 10
and `YY_TAPDEMO_SCENE=field`: `docs/figures/levels/level-01.png` to
`level-10.png`. Inspected: each shows LEVEL N in the header with its ball count
(4 on every level), the cavities ringed by the level's own glowing kinds,
and the rest under fog.

**Open:** the bot is not a person; whether relief feels too easy (the bot wins
100%) and whether level 10's losses feel near (level 10's losses rarely see the goal, 5%) are Yotam's
calls on TestFlight. Speed and Ping break nothing, so the numbers cannot show
levels 5 and 7 teach them. Device performance is not applicable.

### V6 Three screen reskin concepts for owner selection (G12)

**Status:** superseded by V7 -- the owner requested stronger themes and visible material damage

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

### V7 Themed reskin concepts with material damage and hit-effect studies (G12)

**Status:** active

T14 revises the static concept package after Yotam's feedback that the first
looks were not interesting or juicy enough. `docs/reskin-concepts.md` now
proposes Garden Pop, Crystal Quarry and Sunken Reef, using original imagegen
artwork composed onto the genuine game captures. The same-day Apple charts
and credited Toon Blast, Royal Match and Candy Crush Saga references remain.
The game itself is unchanged.

Ordinary-brick numbers disappear in the mockups. Grass becomes cut turf and
bare earth; ore develops a wide fissure and becomes sand; coral breaks into
stubs and a sand bed. Each final hit clears the cell, preserving existing hit
points and collision rules. Each look has one small goal-flag twist and a
static contact/burst/settle study. Garden Pop is recommended at an estimated
5-7 engineering days plus 4 art days, including font support and short
cosmetic hit effects.

**Visual evidence.** All nine 1170 by 2532 gameplay, instructions and staged
power-fixture PNGs were read at 390 by 844; all three damage studies were read
at phone width. Every page of the final 18-page, 5.08 MB PDF was inspected
after `fe_plan.py check`, `pdf` and `shots`. All three retain the captured
36-cell aiming geometry. Conservative fog-minus-cavity luminance gaps are
0.369, 0.367 and 0.479, above the 0.08 minimum with independent shape cues.
The six-icon fixture is a debug scene; the goal stays hidden in normal aiming.

**Open:** Yotam chooses a theme. Accepting the revised plan starts Garden
Pop's implementation; another theme needs a named change first. The
180-220 ms hit settle is a proposed target, not a measured animation.
Damage cues during motion, BMP alpha support and iPhone performance need a
playable check after acceptance. Runtime tests, player-path and device checks
are not applicable to this concept round. No game, engine or palette changes.

### V8 Transparent, cropped sprites and baked real-font text (D7)

**Status:** active

T15 gives the renderer what Garden Pop's art and UI need, without reskinning
the game. 32-bit BMPs keep their alpha and draw premultiplied with linear
filtering, a new `sprite` overload draws one cell of a sheet, and `label`
draws Fredoka SemiBold (OFL, `games/tapdemo/assets/fonts/`) from sheets baked
by `scripts/generate-assets.py`, left, centred or right aligned, in any colour.
The debug font is unchanged.

**Validation.** `scripts/build.ps1 -Smoke` passed the Windows build, CTest
(1/1) and the 120-frame smoke. `yy_tests` checks font parsing, glyph lookup and
the '?' fallback, bake choice, text width and left/centre/right layout, and
that the committed bake has every printable ASCII glyph in every size and
full-height digits for the ball counter.

**Visual evidence.** `YY_TAPDEMO_SCENE=render-test` (a debug scene; normal
play never draws it) puts, over the opening field and over a light card, a
128-pixel flower cutout with an anti-aliased rim and soft shadow at 120, 96,
56 and 40 points, the leaf cell of a 2x2 sheet whose other cells are opaque
magenta at 150 and 64 points, and Fredoka at 40 (header) and 16 (card)
points. The 390 by 844 capture, launched through `fe_manager.py gpu`, shows no
dark or white rim on either background and no magenta pixel anywhere.

**Open:** sprites have no mipmaps, so art shrunk past about 2x aliases; the
40-point flower in the 1x desktop capture shows it. Text has no kerning. The
capture is desktop 1x; the 3x phone draws from the larger bakes, not yet seen
on a device. No game art, look or gameplay changes.

### V9 Garden Pop plays with material damage and a local hit pop (D8)

**Status:** active

T16 implements the accepted Garden Pop concept. Fresh games use dense grass,
cut turf and bare soil for three, two and one hits, with dark cleared ground
and no ordinary-brick digits. Generated phone-resolution alpha BMPs provide
wooden power holders, the ladybird goal pennant, pale dewy mist with a stepped
rim, glossy red ball and wooden/cream UI. Fredoka draws the header and card
within their original bounds; the card teaches the three damage pictures.
DEBUG / COLORS kept all four previous palettes and their saved indices (removed in V10).

Each hit starts a renderer-only 200 ms squash, flash and clipping sequence.
The effect stays local and below the aim, without delaying the next shot.
The deterministic model, seeds, grid, hit points, physics, touch rules, hidden
goal and zoom limits are unchanged. Source artwork, exact imagegen prompt,
crop registration and production contrast are recorded with the assets.

**Validation.** `scripts/build.ps1 -Smoke` passed compilation, CTest 1/1 and
the 120-frame smoke through `fe_manager.py gpu`. `build/windows/yy_tests.exe`
passed existing deterministic/level/font tests and Garden's real Game/Touch
shots, all five powers, goal, simultaneous aim/next shot, 200 ms lifetime,
both pinch limits, card and saved look checks. The actual production BMPs
have a minimum mist/cavity luminance gap of 0.368624 against the 0.08 rule.

**Visual evidence.** `tests/capture_garden.py`, launched through
`fe_manager.py gpu`, captures gameplay, damage, mid-hit aim, powers/goal,
instructions, both zoom limits, a new level 1 and DEBUG. All nine were read
at 390 wide beside the accepted mockups. The staged placements and frozen
75 ms screenshot are explicitly identified in `docs/garden-pop-validation.md`;
independent live touch tests prove timing and activation. The final pictures
show material damage without digits and an unobscured aim during the pop.

**Open:** the captures are desktop SDL at 1x. Phone 3x appearance and the
200 ms pop's tactile feel remain the owner's TestFlight check. This task has
no device performance goal. An installation with a saved legacy palette
kept it until V10, which removed the other palettes.

### V10 TapDemo has one look: Garden Pop (D9)

**Status:** active

Every launch, fresh or with any old save, opens in Garden Pop. The NAVY, EMBER,
FOREST and PLUM palettes, the non-garden brick, fog, header, card and ball
drawing, DEBUG's COLORS button and `YY_TAPDEMO_SCHEME` are gone, and RESTART and
CLOSE moved up into the freed space. `DebugSettings` carries no scheme: a
`scheme N` line in an older save is ignored and the rest of the save loads; a
save written now has none. Garden Pop's art, colours, fog and gameplay are
unchanged.

**Validation.** `scripts/build.ps1`: CTest 1/1. `yy_tests` opens the debug
panel through the real Game from saves with `scheme 0`, `3`, `9` and `-4`: no
COLORS button, balls, bounces, grid, glow and weights intact, RESTART draws the
Garden Pop field, and the next save has no scheme line. Fog/cavity luminance gap
for Garden Pop stays 0.382868 against the 0.08 rule.

**Open:** none known; desktop captures only, no device measurement (not a
performance change).

### V11 Breaking the goal plays a festive, Peggle-style celebration (D10)

**Status:** active

Every goal win slows time and pushes the camera in on the ball as it heads for the goal, bursts the goal with
confetti and a rising tune and a strong haptic, eases the camera back, shows a big "GOAL!" and then the Garden
Pop win card counting the balls left up from 0; about 3.5 s, and one tap skips to the card without advancing.
The model, levels, Ghost landings and touch rules are unchanged.

**Validation.** `scripts/build.ps1 -Smoke`: CTest 1/1, smoke 120 frames. `celebrationChecks` replays bot
rounds through touch: the look-ahead sees the break at exactly the update it comes (a plain win, a win through a
power-up and a win through a Ghost landing) and never on a lost shot; the same winning shot played through the
game with the slow motion ends with the same balls left as on a bare model; a tap during the approach or during
GOAL! shows the card without advancing and the next tap advances; pausing mid-show leaves the camera at the field.
Desktop captures of each beat through `fe_manager.py gpu` (`tests/capture_celebration.py`) are in
`docs/figures/celebration/`.

**Open:** desktop captures and tests only; the feel of the slow motion, the haptic and the tune on a phone is the
owner's TestFlight check. No device measurement: the confetti is under 200 small shapes (not a performance task).
