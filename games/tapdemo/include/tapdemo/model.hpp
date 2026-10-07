#pragma once
#include <yy/core.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
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
// The experience a level is built for, from a breather to a payoff.
enum class Feel : std::uint8_t { Relief, BuildUp, Fu, FuckYeah };
// One of the fixed levels: its seed decides the field, the goal and every Ghost landing, so a
// retry is the same level brick for brick. `introduces` is the power-up it is the first to use.
// `expectedWins` is the bot's measured win rate in percent (docs/levels.md), shown on the level's card.
struct Level {
  std::uint32_t seed{};
  Feel feel{};
  Power introduces{};
  Settings grid;
  int balls{}, bounces{};
  int bombSize{}, electricSeconds{}; float electricRadius{}; int pingRadius{};
  int expectedWins{};
};
constexpr int levelCount=10;
constexpr int levelBalls=4; // every level gives the same number of balls
// What a simulated player's results must show on a level of each feel (T11; docs/levels.md):
// the win rate's band, and the least share of wins on the last ball and through a power-up
// chain, and of losses that came near (the goal seen or within fogReach of an open cell).
struct Target { float minWins{}, maxWins{1}, lastBall{}, chain{}, near{}; };
constexpr Target target(Feel feel) {
  switch(feel) {
  case Feel::Relief: return {.minWins=0.9f};
  case Feel::BuildUp: return {.minWins=0.5f, .maxWins=0.6f};
  case Feel::Fu: return {.maxWins=0.25f, .near=0.5f};
  case Feel::FuckYeah: return {.minWins=0.35f, .maxWins=0.5f, .lastBall=0.5f, .chain=0.5f};
  }
  return {};
}
// levels[0] is level 1.
extern const std::array<Level,levelCount> levels;
// The level an end-of-round tap opens: the next one after a win, the same one after a loss.
// Winning the last level leads to free play (0).
constexpr int nextLevel(int level, bool won) { return !won ? level : level<levelCount ? level+1 : 0; }
// The reached level as saved on the device, and back; a missing or damaged save reads as level 1.
std::string saveProgress(int level);
int loadProgress(std::string_view text);
// Everything the debug panel sets, kept on the device between runs. Zap reach is in half cells.
struct DebugSettings {
  int pingRadius{6}, bombSize{5}, electricSeconds{6}, electricHalves{5}, snapDegrees{5};
  int balls{10}, bounces{15};
  int shake{2}; // screen shake, 0 off to 3 high (juice.hpp; presentation only)
  Settings grid;
  // Every value within its range (bomb size odd).
  DebugSettings clamped() const;
};
// A short versioned text ("debug 1" then one "key value" line each). Loading starts from the
// defaults: a missing, unknown-version or damaged text gives them all, and a missing, unknown or
// unreadable line leaves its own setting at the default (a saved colour `scheme` line from older builds is ignored); numbers are clamped to their ranges.
std::string saveDebug(const DebugSettings& settings);
DebugSettings loadDebug(std::string_view text);
// The straight flight a launch takes up to its first wall or brick contact, from Model::aimPath. It does
// not say what a brick is, so a fogged brick stays unrevealed. `contact` is where the ball circle's centre
// stops, `after` the unit direction it leaves in.
struct AimPath { bool valid{}; yy::Vec2 start{}, contact{}, after{}; bool brick{}; };
// A power-up that fired: its brick's cell, and for Ghost the cell its ball reappeared at (else -1).
struct Fired { Power power{}; int cell{-1}, to{-1}; };
// What the last update did, so the game can play sounds and haptics.
struct Hits { int bricksHit{}, bricksBroken{}, bounces{}, ballsSpent{}; std::vector<Fired> fired; bool goalBroken{}; };

// The slingshot brick breaker: a seeded field of bricks with one or two empty pockets and one
// goal brick hidden under the fog. Breaking the goal, however it broke, wins the level.
// Balls are placed only in empty space and fly opposite the pull; every wall or brick
// hit costs one bounce, and a brick loses one hit point per hit. A `settings.glow` share of the
// bricks glows with a power-up, each kind picked in proportion to its weight, that fires when
// the brick breaks, however it broke. A glowing brick and the goal have 1 hit point, and no Bomb
// sits closer to a wall than half its blast. Ghost lands where its 3x3 holds no power-up brick
// (under fog first); a 3x3 holding the goal is a valid landing and wins. With no such spot it
// still lands and clears the 3x3, and the power-ups in it vanish without firing.
// Bricks further than `fogReach` straight steps from every empty cell are under fog. Ping shows
// glowing bricks and the goal within `pingRadius` cells of the pinged brick.
class Model {
  std::uint32_t randomState;
  int level_{}; // 1..levelCount, or 0 in free play
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
  // Moves the ball one sub-step of h seconds; true when it bounced, with the bricks it struck in `struck` (-1: none).
  bool stepBall(Ball& ball, float h, int (&struck)[2]) const;
  static int substeps(float dt);
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
  // Free play: a new grid with a new goal from the advancing random state; balls and bounces
  // are clamped to 1..maxSetting and the settings to their ranges.
  void restart(int balls, int bounces, const Settings& grid);
  void restart(int balls, int bounces) { restart(balls, bounces, settings); }
  // Plays the level again from its seed, or a new free-play grid with the same settings.
  void restart();
  // Starts level 1..levelCount (clamped) from its seed with its table's settings, including the
  // bomb size, lightning and ping radius; snapDegrees is left alone.
  void play(int level);
  // The same with `table` in place of the level's entry, for trying other seeds or settings;
  // a restart goes back to the real table.
  void play(int level, const Level& table);
  int level() const { return level_; }
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
  // Where a launch from `at` opposite `pull` first touches a wall or brick: the same snap, speed, sub-steps and
  // collision as launch() then update(dt), so the endpoint is the real first contact. Invalid when launch() would
  // refuse the pull or the spot (a finished round or no balls left aside).
  AimPath aimPath(yy::Vec2 at, yy::Vec2 pull, float dt=1.0f/60) const;
  void update(float dt);
  void pause(bool value) { paused_ = value; }
  bool paused() const { return paused_; }
  bool won() const { return goalBroken_; }
  bool lost() const { return !won() && ballsLeft==0 && balls.empty(); }
  bool over() const { return won() || lost(); }
};
}
