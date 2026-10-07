# The ten levels, measured by simulated play

Each level's seed was chosen because a simulated player's results on it match the
level's intended experience (T11, D6). The numbers below are 2,000 bot games per
level (bot seeds 1 to 2,000); the band test in `yy_tests` replays the first 200
of them on every build and fails if a level leaves its band.

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
| 1 | relief | Bomb, new (8%) | 1659 | 4 / 15 | 100% | 99.9% of wins through a Bomb |
| 2 | build-up | Bomb (12%) | 2920 | 4 / 12 | 55.0% | 99.2% of wins through a Bomb |
| 3 | relief | Electricity, new (8%) | 334 | 4 / 15 | 98.4% | 85.6% of wins through lightning |
| 4 | fu | Electricity (4%) | 893 | 4 / 10 | 12.9% | 54.4% of losses saw the goal (mean 2.4 cells off) |
| 5 | build-up | Speed, new (8%) | 522 | 4 / 15 | 54.8% | 78.7% of losses saw the goal |
| 6 | fuck yeah | Bomb, Electricity (15%) | 696 | 4 / 18 | 38.0% | 56.9% of wins on the last ball; 90.8% through a chain |
| 7 | relief | Ping, new (8%) | 1751 | 4 / 20 | 92.8% | wins by direct hits (1.6% through a power-up) |
| 8 | build-up | Bomb, Ping (8%) | 2669 | 4 / 15 | 55.7% | 97.8% of wins through a power-up |
| 9 | fu | Ghost, new (3%) | 1009 | 4 / 12 | 19.1% | 56.7% of losses saw the goal (mean 2.2 cells off) |
| 10 | fuck yeah | all five (15%) | 2104 | 4 / 17 | 45.8% | 52.1% of wins on the last ball, all through a chain |

Grids grow 12x20 (1 to 3), 18x30 (4 to 7), 24x40 (8, 9), 30x50 (10). **Every level
gives 4 balls** (Yotam's call: all levels have the same number). Difficulty comes
from the seed, the grid, the glow and the bounces: levels 6 and 7 got more bounces
(25 and 20) so the bot clears the relief band and level 6 reaches its win band
at 4 balls; with 4 balls on every level, 4 of the 10 levels needed no change
beyond a new seed. At 5 balls the fuck-yeah levels reached at most 45% of wins on
the last ball (best seeds of 3,000); at 4 balls level 6 reaches 51% and level 10
62%.

**The curve.** Wins run 100, 55, 98, 13, 55, 38, 93, 56, 19, 46%. Within each
experience a later level is never easier than an earlier one by more than 10
points (the band test checks it; 200 games carry about 3.5 points of noise).
Level 10 (46%) is 8 points easier than level 6 (38%) over 2,000 games, a bigger
field with the same bands.

**What the numbers do not show.** Speed and Ping break nothing, so the chain
share cannot show that levels 5 and 7 teach them; the bot wins level 7 without
needing Ping. The relief levels are won almost every time, so a person will
find them very easy. Level 10's losses rarely see the goal (5%): it
looks lost until it is won, as the experience asks, but those losses do not
feel near.

## T19: Ghost and the goal

Ghost lands only where its 3x3 holds no power-up brick (under fog first); with no such
spot it still lands and the power-ups in the cavity vanish without firing. The goal
brick now breaks in one hit, like every power-up brick. This moved the seeded grids
and the bot's results, so levels 6 and 10 left their bands and were re-searched
(`level_tune search 6 1 3000 300 4 18`, `search 10 1 3000 200 4 17`): level 6 is seed 696
with 18 bounces, level 10 is seed 2104 with 17 bounces. The other levels kept their
seeds; the table above is the new measurement. The band test also plays level 9 through touch for its 200
games and fails if any power-up fires from a Ghost landing's 3x3 (140 landings, none).

## How it was measured

```
scripts/build.ps1                           # builds yy_tests and level_tune; the band test runs in yy_tests
build/windows/level_tune.exe measure 2000   # this table
build/windows/level_tune.exe search 6 1 3000 300 3 15
```

`search LEVEL FIRST COUNT RUNS [balls bounces]` plays every seed in the range
RUNS times, keeps the forty closest to the target, replays them over the
report's 2,000 and the test's 200 bot games, and lists the ten whose worse
result is closest. Seeds whose goal starts out of the fog are skipped. Every level
searched seeds 1 to 3,000 with 4 balls (levels 6 and 7 also with 20 and 25
bounces, level 10 also 3,001 to 10,000).

## Opening fields

`YY_TAPDEMO_LEVEL=N YY_TAPDEMO_SCENE=field TapDemo.exe --smoke 30` with
`YY_SCREENSHOT_PATH` set captures each level's field as it opens, the card
dismissed: `docs/figures/levels/level-01.png` to `level-10.png`.

## On the level's card

Yotam asked for each level's expected win rate in the game. Each level's card
shows EXPECTED WINS N% beside LEVEL N. N is the bot's win rate over 2,000
games, rounded (`Level::expectedWins` in the level table; the Wins column
above). The band test fails if the figure leaves the level's feel band, or if it
differs from the 200 test games by more than 7 points (two widths of their
noise). It is the bot's rate, not a person's. `YY_TAPDEMO_SCENE=instructions`
captures the card: `docs/figures/levels/card-02.png` (55%) and `card-10.png`
(now 46%).

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
