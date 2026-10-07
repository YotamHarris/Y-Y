# T27 rendering validation

The field and mist previously spilled beyond the 390x844 logical frame into
letterbox strips, while brick drawing stopped at the play area. The fix clips
every board layer to that play area and retains a one-cell culling margin.
The strips now show the engine's plain backdrop, including on phone safe areas
whose aspect ratio differs from the logical frame.

## Matched captures

These are SDL framebuffer captures. The 393x759 images are read at phone size;
that window represents a phone safe-area shape, without claiming a particular
device measurement. Before and after use the same level 6 fixture, pinch,
pan path and drawing transform. The shake cases freeze the same maximum bump
25 ms into its decay.

| View | Before | After |
|---|---|---|
| Phone, touch | [393x759](figures/letterbox/before-touch-393x759.png) | [393x759](figures/letterbox/after-touch-393x759.png) |
| Phone, shake | [393x759](figures/letterbox/before-shake-393x759.png) | [393x759](figures/letterbox/after-shake-393x759.png) |
| Wide, touch | [600x844](figures/letterbox/before-touch-600x844.png) | [600x844](figures/letterbox/after-touch-600x844.png) |
| Wide, shake | [600x844](figures/letterbox/before-shake-600x844.png) | [600x844](figures/letterbox/after-shake-600x844.png) |
| Tall, touch | [390x1000](figures/letterbox/before-touch-390x1000.png) | [390x1000](figures/letterbox/after-touch-390x1000.png) |
| Tall, shake | [390x1000](figures/letterbox/before-shake-390x1000.png) | [390x1000](figures/letterbox/after-shake-390x1000.png) |
| Landscape, touch | [800x600](figures/letterbox/before-touch-800x600.png) | [800x600](figures/letterbox/after-touch-800x600.png) |
| Landscape, shake | [800x600](figures/letterbox/before-shake-800x600.png) | [800x600](figures/letterbox/after-shake-800x600.png) |

All sixteen images were inspected. Before, mist fills the left strip in wider
windows and the top strip in taller ones; those areas lack their bricks.
After, every strip contains only backdrop, with no missing brick bands in the
field. The cropped brick faces meet the visible frame boundary.

The fixtures intentionally clear every third row and the outer columns to
make the field boundary readable. Those empty corridors are part of the model.
`edge-touch` starts at the opposite corner, pinches from zoom 1 to 1.6 and pans
to the bottom-right through the game's pointer handlers. `edge-shake` adds the
frozen shake. These debug fixtures neither change ordinary play nor save
preferences.

For the before images, only the rendering changes were temporarily reverted:
no board clip, and the original `c0/c1/r0/r1` equations from pre-T27 commit
`0ac6438`. The new fixture and window-size hooks remained for an identical
comparison. The new board clipping test failed against that renderer as
expected. Final source was restored byte for byte and rebuilt before after
captures. The before images are instrumented reproductions of the original
renderer, rather than captures from an untouched older executable.

## Commands and results

```powershell
./scripts/build.ps1 -ToolsRoot 'D:/Source/Y&Y/YYEngine' -Smoke
```

Final Release build, CTest 1/1 and the 120-frame SDL smoke passed. Deterministic
`visibleCellChecks` covers all corners and the centre at fitted, intermediate
and maximum zoom throughout a full shake, negative fractional world coordinates,
the one-cell margin and skipping cells beyond the view.

`boardRenderingChecks` delivers finger events through `PointerTracker`, the
safe-area `Viewport` and the real Game handlers. At every gesture step it checks
that every visible model cell draws its ground and every visible brick draws
its face. It traverses all four corners, both pinch limits, phone/wide/tall/
landscape safe areas and a doubled safe area with a nonzero origin. It also
checks off-screen skipping, the common clip for mist, fog, rims, pops and fog
lifts, and releasing the clip before UI and the next frame. This is automated
touch-path evidence, rather than a physical iPhone playtest.

```powershell
python studio/fe_manager.py gpu -- python tests/capture_letterbox.py --tag before-touch --scene edge-touch --sizes 393x759,600x844,390x1000,800x600
python studio/fe_manager.py gpu -- python tests/capture_letterbox.py --tag before-shake --scene edge-shake --sizes 393x759,600x844,390x1000,800x600
python studio/fe_manager.py gpu -- python tests/capture_letterbox.py --tag after-touch --scene edge-touch --sizes 393x759,600x844,390x1000,800x600 --verify
python studio/fe_manager.py gpu -- python tests/capture_letterbox.py --tag after-shake --scene edge-shake --sizes 393x759,600x844,390x1000,800x600 --verify
```

The first two commands used the instrumented original renderer; the last two
used the rebuilt final renderer. `--exe` can select a separate baseline build.
All commands passed. `--verify` checks every strip pixel outside the logical
frame, allowing one pixel for clip rounding, against backdrop RGB (9,20,33).
All eight final captures passed. Pixel comparison of each before/after board
interior, excluding two border pixels for rounding, found zero changed pixels
in all eight pairs. This confirms identical staged views; it is specific to
these fixtures.

Local capture drivers and detailed logs are in ignored `.studio/t27-before.ps1`,
`.studio/t27-before-build.log`, `.studio/t27-after.ps1`, `.studio/t27-after-build.log`
and `.studio/t27-compare.txt`. No device performance measurement is required by
T27, and no desktop result is used as iPhone performance evidence.
