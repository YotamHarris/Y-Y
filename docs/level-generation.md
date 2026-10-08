# The level-generation recipe

Build a field in this order; `Model::generate()` calls steps 1–4.
Step 5 runs when a Ghost fires during play.
The code is in `games/tapdemo/src/model.cpp`, independent of SDL.
Each R rule has a named test in `tests/level_generation_tests.cpp`.

## 1. Grid size and hit points

`Model::buildGridAndHitPoints()` fills the grid before anything is carved.
`Model::play()` first loads the level's seed and settings.

- **R1:** The clamped scale gives 6×scale columns and 10×scale rows, filled
  with 1, 2 or 3 hit points at chances of 50%, 30% and 20%.
- **R2:** Fixed level seeds are chosen by simulated play to meet the bot
  bands in [levels.md](levels.md), and retries start from the same seed.

## 2. Cavities (pockets)

`Model::carvePockets()` empties rectangles for placing balls.

- **R3:** There are one or two pockets with equal chance, each 3–5 cells on
  each side, and only their cells start empty.
- **R4:** Each pocket leaves at least one cell to every wall and at least
  one separating row or column from another pocket.

## 3. Glowing power-up bricks

`Model::placeGlowingBricks()` visits occupied cells in row order.

- **R5:** Each brick has a glow chance of glow/200 and each power is picked
  in proportion to its enabled weight, with no glow when all weights are zero.
- **R6:** A glowing brick has 1 hit point.
- **R7:** A Bomb's cell is at least half its blast size from every wall.
- **R8:** Where a Bomb cannot fit, pick with its weight removed, preserving
  the other weights' proportions, or leave the brick plain if only Bomb is enabled.

Keep the existing draw even when the eligible weight sum is zero.

## 4. The goal

`Model::placeGoal()` refreshes the fog and chooses the goal.
Fog distance is the shortest number of horizontal or vertical steps to an
empty cell; distances above `Model::fogReach` are hidden.

- **R9:** Pick a plain occupied brick under fog, falling back to any plain
  occupied brick if none is hidden, or no goal if none exists.
- **R10:** The goal has 1 hit point.

## 5. During play: Ghost landing

`Model::ghost()` refreshes fog, picks an occupied centre one cell inside
the walls, moves the ball there and empties its 3×3 cavity.
It keeps the ball's velocity and remaining bounces.

- **R11:** Prefer a landing whose 3×3 contains no occupied power-up brick.
- **R12:** Pick under fog first among clean landings, then any clean landing,
  then hidden occupied landings, then any occupied landing, doing nothing if none exist.
- **R13:** A 3×3 holding the goal remains eligible and breaking it wins.
- **R14:** With no clean spot, power-ups in the cleared 3×3 vanish without firing.

## Adding a rule

Put the rule in the step that owns it; add a step only if it has a separate job.
Give it the next unused R number and a plain sentence here, then add its
`R<number>_...` test in `tests/level_generation_tests.cpp` to `yy_tests`.
Exercise all ten levels and many seeds, including a fallback fixture when needed.
If the rule moves fields, re-check the bot bands and update the measurements in
[levels.md](levels.md); seed or tuning changes require their own approved scope.
For a refactor, preserve every random draw and its order and run
`fe_land.py --refactor` through the manager.
