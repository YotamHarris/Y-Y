# Opening camera validation (T24)

Levels now open on their visible cavity instead of the whole fogged grid.
The camera follows revealed cells outward smoothly, with a one-cell margin
and a cap of 2.5 times the previous fitted cell size. Grid-edge clamping may
keep a cavity off-center. Manual pinch, wheel and pan take over until retry
or the next level. A held sling keeps the camera still beneath the finger.

## Automated evidence

`scripts/build.ps1 -Smoke -ToolsRoot 'D:/Source/Y&Y/YYEngine'` passed the
Release build, CTest 1/1 and 120-frame SDL smoke. The final local output is
`build/t24-build.log`. The sandboxed Ninja invocation stalled before compiler
launch; the authorized build passed outside the restricted sandbox.

`framingChecks` in `tests/core_tests.cpp` checks level 6's exact world box
(96,192,288,288), zoom limits, margin and grid edges, smooth outward-only
easing, manual takeover and retry. All ten levels' opening boxes fit.
Physical pointer events through a safe area at twice logical size and an
offset map into the automatically zoomed camera: the ball anchors at the
correct world point, a 60-unit pull remains 60 world units, and release fires
upward from that anchor and hits the brick above it. Framing pauses while
the finger is held.

`framingGameChecks` in `tests/palette_tests.cpp` checks levels 1, 6 and 10
through the real Game: computed opening zoom, wheel ownership across updates,
restart restoring that zoom, and pointer drag/release launching a ball.
Existing garden, juice, celebration and level-band checks pass. Difficulty
and recorded celebration regressions explicitly use the player's manual
whole-grid view, preserving their previous screen-coordinate recordings;
the new framing and touch checks exercise automatic opening views.

## Inspected opening captures

The previous worker captured the three before images at the parent commit.
The after command was:

```
python <manager-tools>/fe_manager.py gpu -- python tests/capture_framing.py --tag after
```

The capture script launches the real SDL game with `YY_TAPDEMO_LEVEL`, the
`field` scene and `--smoke 30`. This scene dismisses instructions and leaves
the production camera active; no bricks, placements or shots are staged.
All six images are 390x844. Cell widths below come from deterministic camera
math, in logical screen pixels.

| Level | Before | After | Growth |
| --- | ---: | ---: | ---: |
| 1 | 32.50 | 43.33 | 1.33x |
| 6 | 21.67 | 43.33 | 2.00x |
| 10 | 13.00 | 32.50 | 2.50x |

Level 1's bricks are larger and its lower cavity remains near the grid edge.
Level 6's cavity is centered below the header with clearly larger bricks.
Level 10 reaches the size cap and its formerly tiny cavity is much easier to
read. Visible cells fit; fog still fills space left by the screen's aspect
ratio. Level 2 already has a broad opening and remains at whole-grid fit.

| Level | Before capture | After capture |
| --- | --- | --- |
| 1 | [Before](figures/framing/before-level-1.png) | [After](figures/framing/after-level-1.png) |
| 6 | [Before](figures/framing/before-level-6.png) | [After](figures/framing/after-level-6.png) |
| 10 | [Before](figures/framing/before-level-10.png) | [After](figures/framing/after-level-10.png) |

Device measurements are not applicable: this task is camera framing, not
iPhone performance. The owner's phone playtest decides how the framing feels.
