#pragma once
#include <yy/core.hpp>
#include <algorithm>
#include <cstdint>
#include <vector>

namespace tapdemo {
// The power-up a glowing brick fires when it breaks.
enum class Power : std::uint8_t { None, Bomb, Electricity, Ping, Ghost, Speed };
constexpr int powerKinds=5; // Bomb..Speed
// World units: the grid's top-left corner is (0,0) and each cell is `Model::cell` across.
struct Ball {
  yy::Vec2 position{}, velocity{}; int bounces{};
  float electric{}, zapTimer{}; // seconds of electricity left, and until its next zap
  bool fast{};                  // sped up by a Speed power-up
};
struct Pocket { int column{}, row{}, columns{}, rows{}; };
// A power-up that fired: its brick's cell, and for Ghost the cell its ball reappeared at (else -1).
struct Fired { Power power{}; int cell{-1}, to{-1}; };
// What the last update did, so the game can play sounds and haptics.
struct Hits { int bricksHit{}, bricksBroken{}, bounces{}, ballsSpent{}; std::vector<Fired> fired; bool goalBroken{}; };

// The slingshot brick breaker: a seeded field of bricks with one or two empty pockets and one
// goal brick hidden under the fog. Breaking the goal, however it broke, wins the level.
// Balls are placed only in empty space and fly opposite the pull; every wall or brick
// hit costs one bounce, and a brick loses one hit point per hit. About one brick in
// `glowChance` glows with a power-up that fires when the brick breaks, however it broke.
// Bricks further than `fogReach` straight steps from every empty cell are under fog. Ping shows
// glowing bricks and the goal within `pingRadius` cells of the pinged brick.
class Model {
  std::uint32_t randomState;
  bool paused_{};
  std::vector<int> fog_; // straight steps from each cell to the nearest empty cell
  std::vector<Fired> pending;
  std::vector<int> pingCells_; // cells of the pings still showing
  bool goalBroken_{};
  float random();
  void generate();
  int brickIndexHit(yy::Vec2 p) const;
  bool open(yy::Vec2 p) const;
  void damage(int index, int points);
  void fire(Ball& ball);
  void ghost(Ball& ball, Fired& fired);
  void zap(const Ball& ball);
public:
  static constexpr int columns=24, rows=40;
  static constexpr float cell=32, ballRadius=10, speed=480, minPull=18;
  static constexpr int defaultBalls=10, defaultBounces=15, maxSetting=99;
  static constexpr float glowChance=1.0f/50;     // share of bricks that glow
  static constexpr int fogReach=2;               // straight steps from a cavity that stay visible
  static constexpr int bombSize=3;               // a bomb breaks the bombSize x bombSize around it
  static constexpr float electricSeconds=3;      // how long a ball stays electric
  static constexpr float electricTick=0.25f;     // seconds between zaps
  static constexpr float electricRadius=1.5f;    // cells from the ball to a zapped brick's centre
  static constexpr float pingSeconds=3;          // how long every glowing brick shows through the fog
  static constexpr float speedUp=2;              // a sped-up ball's speed multiplier
  static constexpr int ghostSize=3;              // the ghost's new cavity is ghostSize x ghostSize
  static constexpr int defaultPingRadius=6, minPingRadius=1, maxPingRadius=40; // cells, centre to centre
  std::vector<int> bricks; // hit points per cell, row-major; 0 is empty
  std::vector<Power> powers; // per cell; None on plain bricks and empty cells
  std::vector<Pocket> pockets;
  std::vector<Ball> balls; // in flight
  int ballsLeft{}, ballCount{defaultBalls}, bouncesPerBall{defaultBounces};
  float pingTime{}; // seconds Ping still shows the glowing bricks
  int pingRadius{defaultPingRadius}; // a setting: restart keeps it
  int goal{-1}; // the goal brick's cell
  Hits hits;
  explicit Model(std::uint32_t seed=42);
  // A new grid with a new goal; balls and bounces are clamped to 1..maxSetting.
  void restart(int balls, int bounces);
  void restart() { restart(ballCount, bouncesPerBall); }
  static constexpr float width() { return columns*cell; }
  static constexpr float height() { return rows*cell; }
  int brick(int column, int row) const;
  Power power(int column, int row) const;
  int bricksLeft() const;
  bool isGoal(int column, int row) const { return goal>=0 && column>=0 && row>=0 && column<columns && row<rows && row*columns+column==goal; }
  void setPingRadius(int cells) { pingRadius=std::clamp(cells,minPingRadius,maxPingRadius); }
  // While Ping lasts: the cell lies within pingRadius cells of a pinged brick's cell.
  bool pinged(int column, int row) const;
  const std::vector<int>& pingCells() const { return pingCells_; }
  // Recomputes the fog after `bricks` changed from outside update().
  void refreshFog();
  // Straight (diamond) steps from the cell to the nearest empty cell; walls are not cavities.
  int fogDistance(int column, int row) const;
  bool visible(int column, int row) const { return fogDistance(column,row)<=fogReach; }
  // The ball circle at p lies inside the walls and overlaps no brick; flying balls do not block it.
  bool canPlace(yy::Vec2 p) const { return open(p); }
  // Launches from `at` opposite `pull` (finger minus ball). A pull shorter than minPull,
  // no balls left, a finished round or a spot a brick covers launches nothing.
  bool launch(yy::Vec2 at, yy::Vec2 pull);
  void update(float dt);
  void pause(bool value) { paused_ = value; }
  bool paused() const { return paused_; }
  bool won() const { return goalBroken_; }
  bool lost() const { return !won() && ballsLeft==0 && balls.empty(); }
  bool over() const { return won() || lost(); }
};
}
