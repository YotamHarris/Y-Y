#pragma once
#include <yy/core.hpp>
#include <algorithm>
#include <array>
#include <cmath>
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
// The pull turned onto the nearest axis when it lies within `degrees` of it; 0 snaps nothing.
// launch() and the aim line both use it, so the line shows what flies.
yy::Vec2 snapPull(yy::Vec2 pull, float degrees);
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
// the brick breaks, however it broke. A glowing brick has 1 hit point, and no Bomb sits closer
// to a wall than half its blast.
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
  static constexpr float electricTick=0.25f;     // seconds between zaps
  static constexpr float pingSeconds=3;          // how long a ping shows what lies within pingRadius
  static constexpr float speedUp=2;              // a sped-up ball's speed multiplier
  static constexpr int ghostSize=3;              // the ghost's new cavity is ghostSize x ghostSize
  static constexpr int defaultPingRadius=6, minPingRadius=1, maxPingRadius=40; // cells, centre to centre
  static constexpr int defaultBombSize=5, minBombSize=3, maxBombSize=11;      // odd: the blast square's side in cells
  static constexpr int defaultElectricSeconds=6, minElectricSeconds=1, maxElectricSeconds=15;
  static constexpr float defaultElectricRadius=2.5f, minElectricRadius=1, maxElectricRadius=6; // cells, steps of 0.5
  static constexpr int defaultSnapDegrees=5, maxSnapDegrees=15;               // 0 turns snapping off
  std::vector<int> bricks; // hit points per cell, row-major; 0 is empty
  std::vector<Power> powers; // per cell; None on plain bricks and empty cells
  std::vector<Pocket> pockets;
  std::vector<Ball> balls; // in flight
  int columns{defaultColumns}, rows{defaultRows}; // set by restart from settings.gridScale
  Settings settings; // the grid settings the current grid was built with
  int ballsLeft{}, ballCount{defaultBalls}, bouncesPerBall{defaultBounces};
  float pingTime{}; // seconds the pings still show
  // Settings that apply at once and that restart keeps.
  int pingRadius{defaultPingRadius};
  int bombSize{defaultBombSize};             // a bomb breaks the bombSize x bombSize around it
  int electricSeconds{defaultElectricSeconds}; // how long a ball stays electric
  float electricRadius{defaultElectricRadius}; // cells from the ball to a zapped brick's centre
  int snapDegrees{defaultSnapDegrees};       // a launch this close to an axis flies along it
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
  // An even size rounds up to the next odd one.
  void setBombSize(int cells) { bombSize=std::clamp(cells|1,minBombSize,maxBombSize); }
  void setElectricSeconds(int seconds) { electricSeconds=std::clamp(seconds,minElectricSeconds,maxElectricSeconds); }
  void setElectricRadius(float cells) { electricRadius=std::clamp(std::round(cells*2)/2,minElectricRadius,maxElectricRadius); }
  void setSnapDegrees(int degrees) { snapDegrees=std::clamp(degrees,0,maxSnapDegrees); }
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
  // Launches from `at` opposite `pull` (finger minus ball), snapped by snapPull. A pull shorter than minPull,
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
