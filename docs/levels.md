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
| 1 | relief | Bomb, new (8%) | 2651 | 8 / 15 | 100% | 100% of wins through a Bomb |
| 2 | build-up | Bomb (12%) | 2913 | 5 / 12 | 55.3% | 99.5% of wins through a Bomb |
| 3 | relief | Electricity, new (8%) | 263 | 8 / 15 | 100% | 72% of wins through lightning |
| 4 | fu | Electricity (4%) | 718 | 5 / 10 | 13.3% | 64.6% of losses saw the goal (mean 2.1 cells off) |
| 5 | build-up | Speed, new (8%) | 547 | 7 / 15 | 55.4% | 93.6% of losses saw the goal |
| 6 | fuck yeah | Bomb, Electricity (15%) | 2877 | 3 / 15 | 41.0% | 58.6% of wins on the last ball, all through a chain |
| 7 | relief | Ping, new (8%) | 1751 | 8 / 15 | 99.9% | wins by direct hits (1.9% through a power-up) |
| 8 | build-up | Bomb, Ping (8%) | 2173 | 7 / 15 | 52.3% | 99.9% of wins through a power-up |
| 9 | fu | Ghost, new (3%) | 1247 | 5 / 12 | 12.8% | 86.4% of losses saw the goal (mean 2.0 cells off) |
| 10 | fuck yeah | all five (15%) | 1440 | 3 / 15 | 44.2% | 58.4% of wins on the last ball, all through a chain |

Grids grow 12x20 (1 to 3), 18x30 (4 to 7), 24x40 (8, 9), 30x50 (10). Only the
seeds and the balls on levels 6 and 10 changed. With 8 or 10 balls no seed put
a fuck-yeah level's wins mostly on the last ball (the three seeds of 5,000
closest to the target: 25 to 31% on level 6, 22 to 24% on level 10); with 3
balls about 58% are.

**The curve.** Wins run 100, 55, 100, 13, 55, 41, 100, 52, 13, 44%. Within each
experience a later level is never easier than an earlier one by more than 5
points (the band test checks it): build-up 55.3, 55.4, 52.3; fu 13.3, 12.8;
fuck yeah 41.0, 44.2.

**What the numbers do not show.** Speed and Ping break nothing, so the chain
share cannot show that levels 5 and 7 teach them; the bot wins level 7 without
needing Ping. The relief levels are won almost every time, so a person will
find them very easy. Level 6's losses almost never see the goal (0.7%): it
looks lost until it is won, as the experience asks, but those losses do not
feel near.

## How it was measured

```
scripts/build.ps1                           # builds yy_tests and level_tune; the band test runs in yy_tests
build/windows/level_tune.exe measure 2000   # this table
build/windows/level_tune.exe search 6 1 3000 300 3 15
```

`search LEVEL FIRST COUNT RUNS [balls bounces]` plays every seed in the range
RUNS times, keeps the forty closest to the target, replays them over the
report's 2,000 and the test's 200 bot games, and lists the ten whose worse
result is closest. Seeds whose goal starts out of the fog are skipped. Levels 1
to 5 and 7 to 9 searched seeds 1 to 5,000 (then 1 to 3,000 for the final pick);
levels 6 and 10 searched 1 to 3,000 with 3, 4 and 5 balls.

## Opening fields

`YY_TAPDEMO_LEVEL=N YY_TAPDEMO_SCENE=field TapDemo.exe --smoke 30` with
`YY_SCREENSHOT_PATH` set captures each level's field as it opens, the card
dismissed: `docs/figures/levels/level-01.png` to `level-10.png`.
