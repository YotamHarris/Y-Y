#pragma once
#include <yy/core.hpp>
#include <cstdint>
#include <vector>

namespace tapdemo {
// World units: the grid's top-left corner is (0,0) and each cell is `Model::cell` across.
struct Ball { yy::Vec2 position{}, velocity{}; int bounces{}; };
struct Pocket { int column{}, row{}, columns{}, rows{}; };
// What the last update did, so the game can play sounds and haptics.
struct Hits { int bricksHit{}, bricksBroken{}, bounces{}, ballsSpent{}; };

// The slingshot brick breaker: a seeded field of bricks with one or two empty pockets.
// Balls are placed only in empty space and fly opposite the pull; every wall or brick
// hit costs one bounce, and a brick loses one hit point per hit.
class Model {
  std::uint32_t randomState;
  bool paused_{};
  float random();
  void generate();
  int brickIndexHit(yy::Vec2 p) const;
  bool open(yy::Vec2 p, bool checkBalls) const;
public:
  static constexpr int columns=24, rows=40;
  static constexpr float cell=32, ballRadius=10, speed=480, minPull=18;
  static constexpr int defaultBalls=10, defaultBounces=15, maxSetting=99;
  std::vector<int> bricks; // hit points per cell, row-major; 0 is empty
  std::vector<Pocket> pockets;
  std::vector<Ball> balls; // in flight
  int ballsLeft{}, ballCount{defaultBalls}, bouncesPerBall{defaultBounces};
  Hits hits;
  explicit Model(std::uint32_t seed=42);
  // A new grid; balls and bounces are clamped to 1..maxSetting.
  void restart(int balls, int bounces);
  void restart() { restart(ballCount, bouncesPerBall); }
  static constexpr float width() { return columns*cell; }
  static constexpr float height() { return rows*cell; }
  int brick(int column, int row) const;
  int bricksLeft() const;
  // The ball circle at p lies inside the walls and overlaps no brick and no flying ball.
  bool canPlace(yy::Vec2 p) const { return open(p, true); }
  // Launches from `at` opposite `pull` (finger minus ball). A pull shorter than minPull,
  // no balls left, a finished round or a spot a brick covers launches nothing.
  bool launch(yy::Vec2 at, yy::Vec2 pull);
  void update(float dt);
  void pause(bool value) { paused_ = value; }
  bool paused() const { return paused_; }
  bool won() const { return bricksLeft()==0; }
  bool lost() const { return !won() && ballsLeft==0 && balls.empty(); }
  bool over() const { return won() || lost(); }
};
}
