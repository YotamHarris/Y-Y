# The ten levels, measured by simulated play

Each level's seed was chosen because a simulated player's results on it match the
level's intended experience (T11, D6). The numbers below are 2,000 bot games per
level (bot seeds 1 to 2,000); the band test in `yy_tests` replays the first 200
of them on every build and fails if a level leaves its band.

The [level-generation recipe](level-generation.md) gives the ordered steps,
current rules, code functions and one numbered test per rule.

**The bot is not a person.** It plays one ball at a time. Each shot starts at
the centre of a random open cell of a cavity. 60% of shots aim at something it
can see, give or take 4 degrees: the goal once it shows (out of the fog or
pinged), else a glowing brick it can see. The rest go in a random direction.
`launch()` applies the 5-degree snap the player gets. It never plans a bank
shot, never learns the field from a retry, and never fires two balls at once.
A person who studies the field will win more often than it does.

## Targets

| Experience | Target for the bot |
| --- | --- |
| Relief | wins at least 90% |
| Build-up | wins 50 to 60% |
| Fu | wins at most 25%; most losses near: the goal seen, or within 2 cells of an open cell |
| Fuck yeah | wins 35 to 50% (chosen); most wins on the last ball, and most through a power-up chain |

A win counts as a chain when a power-up fired, or an electric ball zapped, in
the frame the goal broke. Closeness on a loss is the straight steps from the
goal to the nearest open cell when the last ball ends; within 2 the goal is out
of the fog, so a loss within 2 always saw the goal. The targets are
`tapdemo::target()` beside the level table in `model.hpp`.

## Results

| # | Experience | Power-ups (glow) | Seed | Balls / bounces | Wins | Experience metric |
| ---: | --- | --- | ---: | --- | ---: | --- |
| 1 | relief | Bomb, new (8%) | 185 | 4 / 15 | 99.15% | goal broken by ball contact, never by a Bomb |
| 2 | build-up | Bomb (12%) | 1783 | 4 / 12 | 55.55% | goal broken by ball contact, never by a Bomb |
| 3 | relief | Electricity, new (8%) | 334 | 4 / 15 | 98.45% | 85.6% of wins through lightning |
| 4 | fu | Electricity (4%) | 893 | 4 / 10 | 12.90% | 54.4% of losses saw the goal (mean 2.4 cells off) |
| 5 | build-up | Speed, new (8%) | 522 | 4 / 15 | 54.80% | 78.7% of losses saw the goal |
| 6 | fuck yeah | Bomb, Electricity (15%) | 1563 | 4 / 16 | 38.75% | 53.3% of wins on the last ball; 84.3% through a chain |
| 7 | relief | Ping, new (8%) | 1751 | 4 / 20 | 92.75% | wins by direct hits (1.6% with same-frame power activity) |
| 8 | build-up | Bomb, Ping (8%) | 2812 | 4 / 15 | 54.70% | goal broken by ball contact; 84.4% of losses saw it |
| 9 | fu | Ghost, new (3%) | 1009 | 4 / 12 | 19.05% | 56.7% of losses saw the goal (mean 2.2 cells off) |
| 10 | fuck yeah | all five (15%) | 2441 | 4 / 22 | 45.35% | 54.6% of wins on the last ball; 99.8% through a chain |

Grids grow 12x20 (1 to 3), 18x30 (4 to 7), 24x40 (8, 9), 30x50 (10). **Every level
gives 4 balls** (Yotam's call: all levels have the same number). Difficulty comes
from the seed, the grid, the glow and the bounces. T35 changes five seeds and
the bounces on levels 6 and 10; the other five levels keep their settings.
The grid, power-up mix, ball count and target bands are unchanged.

**The curve.** Rounded wins run 99, 56, 98, 13, 55, 39, 93, 55, 19, 45%. Within each
experience a later level is never easier than an earlier one by more than 10
points (the band test checks it; 200 games carry about 3.5 points of noise).
Level 10 (45.35%) is 6.6 points easier than level 6 (38.75%) over 2,000 games, a bigger
field with the same bands.

**What the numbers do not show.** Speed and Ping break nothing, so the chain
share cannot show that levels 5 and 7 teach them; the bot wins level 7 without
needing Ping. The relief levels are won almost every time, so a person will
find them very easy. Level 10's losses rarely see the goal (1.3%): it
looks lost until it is won, as the experience asks, but those losses do not
feel near.

## T19: Ghost and the goal

Ghost lands only where its 3x3 holds no power-up brick (under fog first); with no such
spot it still lands and the power-ups in the cavity vanish without firing. The goal
brick now breaks in one hit, like every power-up brick. This moved the seeded grids
and the bot's results, so levels 6 and 10 left their bands and were re-searched
(`level_tune search 6 1 3000 300 4 18`, `search 10 1 3000 200 4 17`): at T19, level 6 became seed 696
with 18 bounces and level 10 became seed 2104 with 17 bounces. The other levels kept their
seeds then; T35 below replaces that measurement. The band test also plays level 9 through touch for its 200
games and fails if any power-up fires from a Ghost landing's 3x3 (140 landings, none).

## T35: Bombs clear paths without breaking the goal or firing Ghost

Recipe **R15** excludes the goal and Ghost bricks from every generated Bomb
square, including its corners, at the size used to generate the field.
The power-up step removes invalid kinds from the weighted pick; the goal
step chooses a safe fogged plain brick. With none, it chooses a safe visible
plain brick; with no safe plain brick, it leaves the goal absent. Every shipped
level and sampled free-play field has a goal. Other power-ups and Bombs can
still chain. Ghost's landing and all power-up effects keep their existing rules.

With the original seeds, the 2,000-game win rates fell to 60.0% on level 1,
25.7% on level 2, 26.0% on level 8 and 0% on level 10. Level 6 still won 41.9%,
but its 200-game last-ball share fell below 50%, so it also needed re-tuning.

| Level | Seed before → after | Bounces before → after | Card before → after |
| ---: | --- | --- | --- |
| 1 | 1659 → 185 | 15 → 15 | 100% → 99% |
| 2 | 2920 → 1783 | 12 → 12 | 55% → 56% |
| 6 | 696 → 1563 | 18 → 16 | 38% → 39% |
| 8 | 2669 → 2812 | 15 → 15 | 56% → 55% |
| 10 | 2104 → 2441 | 17 → 22 | 46% → 45% |

The band test plays levels 1, 2 and 10 through `Touch::down/move/up` with
bot seeds 1–200, on the fitted manual camera. It counts every Bomb firing,
including chained Bombs, and checks goal breaks and Ghost firings inside
each blast square. This is automated touch-handler evidence, not a human
play session or a device measurement.

| Level | Touch wins | Bomb firings | Goal breaks / Ghost firings inside blasts |
| ---: | ---: | ---: | --- |
| 1 | 199 / 200 (99.5%) | 1,231 | 0 / 0 |
| 2 | 109 / 200 (54.5%) | 2,527 | 0 / 0 |
| 10 | 82 / 200 (41%) | 494 | 0 / 0 |

**How levels 1 and 2 are won now.** Bombs open space and expose a route;
the ball then hits the goal itself. All 199 and 109 touch wins respectively
fired Bombs earlier in the round, but none was won by a blast. The measured
win rates stay in their bands without a surprise win from a Bomb.

R15's test checks all ten levels at sizes 3, 5, 7, 9 and 11, plus 256 free-play
seeds at each size and grid scales 2, 4 and 10: 279,252 Bomb squares with no
goal or Ghost inside. It also checks the safe visible/no-goal fallbacks and
the remaining kinds' weight proportions.

## How it was measured

```
scripts/build.ps1                           # builds yy_tests and level_tune; the band test runs in yy_tests
build/windows/level_tune.exe measure 2000   # this table
build/windows/level_tune.exe search 1 1 3000 200 4 15
build/windows/level_tune.exe search 2 1 3000 200 4 12
build/windows/level_tune.exe search 6 1 3000 300 4 16
build/windows/level_tune.exe search 8 1 3000 200 4 15
build/windows/level_tune.exe search 10 1 3000 300 4 20
build/windows/level_tune.exe search 10 2441 1 300 4 22
```

`search LEVEL FIRST COUNT RUNS [balls bounces]` plays every seed in the range
RUNS times, keeps the forty closest to the target, replays them over the
report's 2,000 and the test's 200 bot games, and lists the ten whose worse
result is closest. Seeds whose goal starts out of the fog are skipped.
For T35, levels 1, 2, 6 and 8 searched seeds 1–3,000; level 6 first tried
18 bounces, then 16. Level 10 searched 1–3,000 and 3,001–6,000 at 17 bounces,
then 1–3,000 at 20, and seed 2441 at 21 and 22. Twenty-two meets the full
and test samples with more margin. Every search kept four balls.
`level_tune` prints exact win counts and the rounded card figure alongside
the one-decimal display; the Results table uses the exact counts out of 2,000.

## Opening fields

`YY_TAPDEMO_LEVEL=N YY_TAPDEMO_SCENE=field TapDemo.exe --smoke 30` with
`YY_SCREENSHOT_PATH` set captures each level's field as it opens, the card
dismissed: `docs/figures/levels/level-01.png` to `level-10.png`.

T35 refreshes all ten fields with
`python studio/fe_manager.py gpu -- python tests/capture_levels.py`.
The changed seeds' fields (1, 2, 6, 8, 10) and their cards were read at
390×844: the header shows four balls, the cavities and visible powers are
clear, and every card fits. These are staged opening scenes from the SDL app;
the touch replays above provide the gameplay evidence.

## On the level's card

Yotam asked for each level's expected win rate in the game. Each level's card
shows EXPECTED WINS N% beside LEVEL N. N is the bot's win rate over 2,000
games, rounded (`Level::expectedWins` in the level table; the Wins column
above). The band test fails if the figure leaves the level's feel band, or if it
differs from the 200 test games by more than 7 points (two widths of their
noise). It is the bot's rate, not a person's. `YY_TAPDEMO_SCENE=instructions`
captures the card. T35's cards in `docs/figures/levels/` are `card-01.png`
(99%), `card-02.png` (56%), `card-06.png` (39%), `card-08.png` (55%) and
`card-10.png` (45%). All ten current figures are 99, 56, 98, 13, 55, 39, 93,
55, 19 and 45%.

## T26: the goal's glint and the near miss

Two presentation changes help a person find the goal and plan the retry; neither
touches the model, so the seeds, the fields and every number in this document are
unchanged.

- **Glint.** Fogged cells within 4 straight steps of the goal shimmer warmly, as
  strongly as the debug GLINT setting (OFF, LOW, MEDIUM, HIGH) allows: the goal's
  cell and its four neighbours the same, then falling in even steps to a quarter at
  4 steps and nothing beyond. It says "over here", not "this brick". It stops once
  the goal is out of the fog. Level 1's card says "THE GOAL GLOWS FAINTLY THROUGH IT."
  Captures: `docs/figures/glint/` (`tests/capture_glint.py`).
- **Near miss.** On any loss the fog lifts around the goal and along the straight
  run to the nearest open cell, the goal is ringed and the card says "N BRICKS
  AWAY": N is the closeness measure above (`nearMissBricks`, checked against the
  bot's own `goalDistance` and a count over every open cell). A tap during the
  reveal finishes it; the next tap retries the identical field.

**The card's EXPECTED WINS is the bot's rate without the glint.** The bot never
sees the glint, so a person who reads it will win more often than the figure says.
