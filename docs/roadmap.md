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
