#pragma once
#include <yy/core.hpp>
#include <array>
#include <cstdint>

namespace tapdemo {
struct Target { yy::Vec2 position{}, velocity{}; float radius{26}; };
class Model {
  std::uint32_t randomState;
  bool paused_{};
  float random();
  void place(Target& target);
public:
  std::array<Target, 5> targets{};
  int score{};
  float remaining{30};
  explicit Model(std::uint32_t seed=42);
  void restart();
  void update(float dt);
  bool tap(yy::Vec2 point);
  void pause(bool value) { paused_ = value; }
  bool paused() const { return paused_; }
  bool finished() const { return remaining <= 0; }
};
}
