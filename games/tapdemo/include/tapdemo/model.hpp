#pragma once
#include <yy/core.hpp>
#include <algorithm>
#include <array>
#include <cstdint>
#include <optional>
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
// The grid's debug settings; restart applies them. The grid keeps the 3:5 shape:
// gridScale k gives 6k columns and 10k rows.
struct Settings {
  static constexpr int shapeColumns=6, shapeRows=10;
  static constexpr int defaultScale=4, minScale=2, maxScale=10;      // 24x40; 12x20 to 60x100
  static constexpr int defaultGlow=4, maxGlow=50;                   // half percents: 2%; 0 to 25%
  static constexpr int defaultWeight=1, maxWeight=9;
  int gridScale{defaultScale};
  int glow{defaultGlow}; // share of bricks that glow, in half percents
  std::array<int,powerKinds> weights{defaultWeight,defaultWeight,defaultWeight,defaultWeight,defaultWeight}; // Bomb..Speed
  // The settings within their ranges.
  Settings clamped() const;
};
// A power-up that fired: its brick's cell, and for Ghost the cell its ball reappeared at (else -1).
struct Fired { Power power{}; int cell{-1}, to{-1}; };
// What the last update did, so the game can play sounds and haptics.
struct Hits { int bricksHit{}, bricksBroken{}, bounces{}, ballsSpent{}; std::vector<Fired> fired; bool goalBroken{}; };

// The slingshot brick breaker: a seeded field of bricks with one or two empty pockets and one
// goal brick hidden under the fog. Breaking the goal, however it broke, wins the level.
// Balls are placed only in empty space and fly opposite the pull; every wall or brick
// hit costs one bounce, and a brick loses one hit point per hit. A `settings.glow` share of the
// bricks glows with a power-up, each kind picked in proportion to its weight, that fires when
// the brick breaks, however it broke.
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
  static constexpr int defaultColumns=Settings::shapeColumns*Settings::defaultScale, defaultRows=Settings::shapeRows*Settings::defaultScale;
  static constexpr float cell=32, ballRadius=10, speed=480, minPull=18;
  static constexpr int defaultBalls=10, defaultBounces=15, maxSetting=99;
  static constexpr int fogReach=2;               // straight steps from a cavity that stay visible
  static constexpr int bombSize=3;               // a bomb breaks the bombSize x bombSize around it
  static constexpr float electricSeconds=3;      // how long a ball stays electric
  static constexpr float electricTick=0.25f;     // seconds between zaps
  static constexpr float electricRadius=1.5f;    // cells from the ball to a zapped brick's centre
  static constexpr float pingSeconds=3;          // how long a ping shows what lies within pingRadius
  static constexpr float speedUp=2;              // a sped-up ball's speed multiplier
  static constexpr int ghostSize=3;              // the ghost's new cavity is ghostSize x ghostSize
  static constexpr int defaultPingRadius=6, minPingRadius=1, maxPingRadius=40; // cells, centre to centre
  std::vector<int> bricks; // hit points per cell, row-major; 0 is empty
  std::vector<Power> powers; // per cell; None on plain bricks and empty cells
  std::vector<Pocket> pockets;
  std::vector<Ball> balls; // in flight
  int columns{defaultColumns}, rows{defaultRows}; // set by restart from settings.gridScale
  Settings settings; // the grid settings the current grid was built with
  int ballsLeft{}, ballCount{defaultBalls}, bouncesPerBall{defaultBounces};
  float pingTime{}; // seconds the pings still show
  int pingRadius{defaultPingRadius}; // a setting: restart keeps it
  int goal{-1}; // the goal brick's cell
  Hits hits;
  explicit Model(std::uint32_t seed=42);
  // A new grid with a new goal; balls and bounces are clamped to 1..maxSetting and the
  // settings to their ranges.
  void restart(int balls, int bounces, const Settings& grid);
  void restart(int balls, int bounces) { restart(balls, bounces, settings); }
  void restart() { restart(ballCount, bouncesPerBall); }
  float width() const { return columns*cell; }
  float height() const { return rows*cell; }
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
  // Where a press at `tap` holds a ball: the tap itself when open, else the closest open spot
  // within one cell of it, else nothing.
  std::optional<yy::Vec2> placeNear(yy::Vec2 tap) const;
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
