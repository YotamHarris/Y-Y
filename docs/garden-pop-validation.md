# Garden Pop playable validation (T16)

Garden Pop now draws dense grass, clipped turf, bare soil and the cleared dark
path from the existing hit points. Ordinary bricks have no digits in this look.
Wooden holders carry the existing five power silhouettes and the ladybird
pennant; the ball is glossy red. Fredoka SemiBold (OFL) draws the wooden header
and cream instructions card within their original bounds. The card teaches the
three material states with pictures.

Garden is the default for a fresh game. A saved choice of NAVY, EMBER, FOREST or
PLUM keeps its original index and appearance. DEBUG / COLORS cycles those four
choices and GARDEN POP; restart keeps the selected look.

## Automated evidence

`python studio/fe_manager.py gpu -- python build/t16-build.py` ran
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1
-ToolsRoot D:/Source/Y&Y/YYEngine -Smoke`: compilation, CTest 1/1 and the
120-frame SDL smoke passed. The helper and full output are ignored local files
`build/t16-build.py` and `build/t16-build.log`. The installed toolchain needed
approved unsandboxed access; sandboxed Ninja stalled before starting a compiler.

`build/windows/yy_tests.exe` passed the deterministic regression, ten unchanged
level bands (200 bot games per level), font tests and real Game/Touch handlers.
The Garden checks read the committed BMP V4 alpha mask and every mist, rim and
cavity texel. The darkest mist is RGB (154,177,143), luminance 0.402975; the
brightest cavity is (37,57,34), 0.034351. Their minimum gap is **0.368624**,
above palette.hpp's 0.08 rule. Bright stepped rims provide the independent
shape cue.

The touch checks verify exact hp3/hp2/hp1 sprite centers, ordinary shots,
all five actual power activations, a goal win, both two-finger zoom limits,
card dismissal and reopening through restart, debug cycling and persistence.
All fixtures still fire through the real pointer/collision/power paths.

The renderer compares hit points after simulation and keeps its own **200 ms**
per-cell pop. Its three frames are contact flash, clipping burst and sparse
settle. Tests see burst at 67 ms, settle at 133 ms and no effect by 217 ms.
They also hold a second aim above the effect and release another shot during
the first flash, spending the next ball immediately. The squashed drawing and
effects stay within the struck cell, at its unchanged center. No animation
state, timing, grid movement or input lock is added to the model.

## Captures read at 390 pixels wide

Command: `python studio/fe_manager.py gpu -- python tests/capture_garden.py`.
The trusted manager release's equivalent command also passed. Each launch is
`TapDemo.exe --smoke 45`, pinned with `YY_TAPDEMO_LEVEL` and
`YY_TAPDEMO_SCENE`. `tests/capture_garden.py` synchronizes candidate assets for
artwork-only iterations, records their SHA256 hashes and preserves the actual
390x844 SDL pixels. Each `*-compare.png` places those pixels beside the named
accepted mockup at 390 wide. All nine final pairs were read; their manifest
hashes were checked against the current production BMPs.

Files below are in `docs/figures/garden-pop/`.

| Capture and readback | Matching mockup | What it showed |
| --- | --- | --- |
| gameplay.png / gameplay-compare.png | garden-gameplay.png | Level 10's unchanged pocket and aim; dense/cut/bare materials read without numbers, glossy red held ball, wooden counter and level, pale dewy mist with bright stepped boundary above dark soil. The goal remains hidden. |
| damage.png / damage-compare.png | garden-damage.png | Staged adjacent hp3, hp2 and hp1 cells show dense grass, sparse cut grass and bare earth, with the cleared dark pocket beneath. Strength is visible through coverage, without ordinary digits. |
| mid-hit.png / mid-hit-compare.png | garden-damage.png | A real ordinary hit, frozen 75 ms into its local burst, with a second held aim. The hit tile is cut turf and squashed; clippings/flash stay local. White aim dots, band and red ball remain visible above the effect. |
| powers-goal.png / powers-goal-compare.png | garden-powers.png | All five distinct glyphs in warm wooden holders, their colored bases and the pink goal pennant with its small ladybird; dark soil beneath the pocket and pale mist outside it. |
| instructions.png / instructions-compare.png | garden-instructions.png | Level 10's actual ball/bounce limits and five powers in smooth Fredoka; cream/wood card, goal picture, three damage pictures and arrows, last-hit teaching and tap-to-start, all inside the existing card. |
| fit.png / fit-compare.png | garden-gameplay.png | Whole level at the existing minimum zoom, with the actual staged Ping reveal. The small material tiles remain separate from the pale mist and dark cleared pocket; header remains fixed. |
| max.png / max-compare.png | garden-powers.png | Existing maximum zoom (2.5): enlarged grass/soil, sharp alpha edges on wood and ball, stepped fog rim and uncluttered dark cavity. Crop/pan follows the original camera rules. |
| new-game.png / new-game-compare.png | garden-instructions.png | A fresh level 1 opens in Garden with its existing eight balls, fifteen bounces and Bomb-only introduction; the material teaching and start prompt remain present. The concept's level 10 values are deliberately not substituted. |
| debug.png / debug-compare.png | garden-plate.jpg | Existing debug controls and bounds, with a fitting GARDEN POP choice label. The legacy block font remains on the debug controls. |

The damage, mid-hit and six-holder scenes deliberately place fixtures; the
mid-hit screenshot deliberately freezes that moment. Fit/max deliberately
place powers and hold a real Ping activation. Those captures prove the
rendering; the independent live touch tests prove activation, timing and the
next shot. None changes a normal level seed or ordinary scene.

Readback prompted two production fixes: mist and cleared-soil artwork now
span eight cells, rather than repeating visibly within every brick; atlas
crops exclude neighboring soil, card and strip fragments. The final paired
captures show soft dewy patches and clean cutouts, with no horizontal texture
seams. Damage and the mid-hit aim read clearly beside the accepted studies.

## Artwork and remaining owner check

`docs/figures/garden-pop/source-atlas.png` is original built-in imagegen output,
styled from the accepted garden atlas. Its exact prompt is `prompt.txt`.
`games/tapdemo/art/pack-garden.py` packages it as 32-bit straight-alpha BMPs:
256-pixel padded sprite cells, 1146x216 header and 1098x2232 card (3x their
logical bounds). Cropping, scaling, conservative texture normalization and
offline nine-slice preserve the generated painted artwork.
`games/tapdemo/assets/garden/sources.json` records source/reference, crop
registration, hashes, layout, font license and pixel contrast.

**Open:** these are desktop SDL captures at 1x. Phone 3x rendering, tactile
feel and whether the 200 ms pop feels right remain the owner's TestFlight
check. Device performance is not applicable to this task and was not measured.
This report makes no TestFlight delivery/readiness claim.
