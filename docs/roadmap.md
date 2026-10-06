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
