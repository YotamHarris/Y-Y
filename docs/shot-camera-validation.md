# Aim zoom and shot follow validation (T28)

Holding a ball while zoomed out eases in over 0.28 seconds around its pressed
position. The anchor remains fixed on screen, including at a grid edge; pull
still uses world units. Cancelling by a short release, second finger or pause
returns to the exact remembered zoom and offset over 0.28 seconds. Launching
keeps the zoom. A deliberate pinch movement can take over the cancel return.

Each live flight follows even after manual pan, pinch or wheel zoom. The
camera gives every ball a cell of margin and accounts for interpolation and
hit-stop drawing positions. Spread and new reveals widen the settled zoom;
temporary widening covers motion that outruns the center ease. A Ghost arrival
starts a fast swing at rate 24 for 0.35 seconds, while ordinary easing uses
rate 4. Flight can expose the existing backdrop beyond a world edge to retain
ball margin. Manual camera limits and opening framing remain unchanged. Held
aims keep their gesture priority, and the goal celebration retains its camera.
At the end of a shot the view stays exactly where it is.

## Commands and deterministic evidence

```powershell
./scripts/build.ps1 -ToolsRoot 'D:/Source/Y&Y/YYEngine' -Smoke
```

Passed the Release build, CTest 1/1 (9.41 seconds) and 120-frame SDL smoke.
The local logs are `build/t28-build.log` and
`build/windows/Testing/Temporary/LastTest.log`; build outputs are not committed.
The restricted Ninja launch initially stalled before compilation. The
authorized escalated build completed successfully.

After rebasing onto T27's play-area clipping fix, the same command passed
the combined deterministic suite (CTest 1/1, 9.38 seconds) and 120-frame SDL
smoke. Its log is `build/t28-rebase-build.log`. Both `boardRenderingChecks`
and `shotCameraChecks` run: clipping, visible-cell coverage, touch and shake
checks coexist with the new camera behavior. The test renderer uses T27's
existing field rectangle to map stable world damage positions. The camera
history entries are D16/V17, following T27's D15/V16.

`shotCameraChecks` in `tests/core_tests.cpp` follows actual touch events:

- Pinch out, hold a ball, check its fixed screen anchor on every easing frame
  and zero pull for a stationary finger, then cancel and compare zoom and both
  offsets with exact float equality against the prior camera.
- Cancel through a second finger and pause, including partway through zoom;
  exercise a rebased pinch and a grid-edge anchor.
- Drag 60 world units and launch, keep the aiming zoom, and bypass camera
  updates while the celebration owns it.
- Pan by touch, launch two opposing balls, and keep both inside the play area
  with a full cell of margin each frame. Zoom out during flight and retain that
  choice. Clear the finished flight and check exact camera equality thereafter.
- Launch by touch at a real Ghost brick in a seeded corridor, assert a valid
  arrival cell, keep the ball in view on the arrival frame and throughout the
  swing, and reduce center-to-ball distance below 40% of its former distance
  within 12 frames (200 ms). Check automatic reveal widening separately.

`framingGameChecks` in `tests/palette_tests.cpp` reads the actual Game render
commands at the Ghost beats. The drawn ball sprite lies inside x=5..385 and
y=85..839, including interpolation, hit-stop and shake. Opening framing,
level bands, touch shots, glint/loss, celebration, pace/aim-line and feedback
regressions also pass. Damage remains at the same world cell through the
moving camera. Recorded loss and celebration shots explicitly restore their
recorded whole-grid view before each launch.

## Phone-size capture sequence

```powershell
python 'C:/Users/conz3/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/YYEngine/board/manager/releases/a62b93eddb60/studio/fe_manager.py' gpu -- python tests/capture_camera.py
```

Exit 0. All nine files are actual SDL render readbacks at 390x844, captured
from `TapDemo.exe --smoke 10` with `YY_TAPDEMO_LEVEL=6` and a named
`YY_TAPDEMO_SCENE=camera-*`. The manifest is
[captures.json](figures/shot-camera/captures.json). Every final image was
inspected at its original phone dimensions.
The sequence was recaptured and inspected after integrating T27's clipping.

The aim sequence uses level 6 and the Game's pinch, hold and cancel pointer
handlers. The Ghost sequence explicitly stages a seed-38 free-play corridor:
two-hit bricks, open column 12 at rows 6..21, a one-hit Ghost at (12,5), no
other powers or goal, five balls and 99 bounces. A real touch pan precedes a
real touch sling; production collision, Ghost selection and camera updates
run until each frozen beat. This is a debug evidence fixture, not a new level.

| Capture | Inspected result |
| --- | --- |
| [1 Wide](figures/shot-camera/1-wide.png) | Small cavity in the pinched-out view. |
| [2 Aim at 100 ms](figures/shot-camera/2-aim-early.png) | Cavity grows; held ball remains near (162,364). |
| [3 Aim settled](figures/shot-camera/3-aim.png) | Comfortable large bricks; same ball center. |
| [4 Cancel at 100 ms](figures/shot-camera/4-cancel-early.png) | Ball removed; view easing outward. |
| [5 Cancel settled](figures/shot-camera/5-cancel.png) | Original cavity size, location and fitted margins restored. |
| [6 Before Ghost](figures/shot-camera/6-ghost-before.png) | Ball moving up the corridor just below the Ghost. |
| [7 Ghost arrival](figures/shot-camera/7-ghost-arrival.png) | Immediate widening includes the distant bottom-right landing and ball. |
| [8 Ghost +4 frames](figures/shot-camera/8-ghost-4.png) | At 67 ms the camera has swung toward the new cavity with ball margin. |
| [9 Ghost +12 frames](figures/shot-camera/9-ghost-12.png) | At 200 ms the landing is near screen center; the ball is readable with room. |

Exact camera restoration is proven by deterministic snapshot equality.
Whole images need not match bytes: decorative glows keep animating during the
gesture. The first inspection identified insufficient bottom-edge breathing
room; the final captures above include the corrected wall and drawn-position
framing.

## Owner acceptance

In level 6, pinch out, hold a ball in the cavity and look for a smooth zoom-in
with the ball under the finger. Release without pulling and check the original
view returns. Then pan or pinch, launch at a Ghost (also present in level 9),
and watch the landing appear immediately followed by a quick camera swing.
When the shot finishes, its view should stay put.

Player-path evidence here is actual pointer handlers exercised by deterministic
tests and SDL scenes. The feel on an iPhone remains the owner's playtest.
Device measurements are not applicable: T28 changes camera behavior, not
iPhone performance. Desktop smoke timings do not establish device performance.
