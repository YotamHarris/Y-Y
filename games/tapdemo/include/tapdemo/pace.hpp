#pragma once
#include <tapdemo/model.hpp>
#include <algorithm>
#include <cmath>

namespace tapdemo {
// How fast the game clock runs (T25). Presentation only: the game takes more or fewer fixed model steps per
// frame, each step the same dt, so a ball's path, the electricity seconds and every outcome are what they would
// be at 1x. Nothing here writes the model.

// The last ball is the one being placed with none behind it, and then the one in flight with none left to place;
// any ball still flying counts once no balls are left. `aiming` is whether the player is holding a ball.
inline bool lastBall(const Model& m, bool aiming) {
  if(m.over()) return false;
  return (m.ballsLeft==1 && aiming) || (m.ballsLeft==0 && !m.balls.empty());
}

// The goal is still standing, visible or pinged, and a flying ball is within `reach` cells of it. Only the last
// ball (or any flying ball once none are left) counts.
inline bool nearGoal(const Model& m, float reach=2) {
  if(m.ballsLeft>0 || m.over() || m.goal<0 || m.bricks[m.goal]<=0) return false;
  const int column=m.goal%m.columns, row=m.goal/m.columns;
  if(!m.visible(column,row) && !m.pinged(column,row)) return false;
  const yy::Vec2 goal{(column+0.5f)*Model::cell,(row+0.5f)*Model::cell};
  return std::any_of(m.balls.begin(),m.balls.end(),[&](const Ball& b) {
    return std::hypot(b.position.x-goal.x,b.position.y-goal.y)<=reach*Model::cell;
  });
}

// Earns `seconds*scale` of game time into `debt` and returns the whole model steps of `seconds` it now pays for,
// at most `cap`: none while a slow scale has not yet earned one, several at a fast one.
inline int takeSteps(float& debt, float seconds, float scale, int cap) {
  debt+=seconds*scale;
  int steps=0;
  while(steps<cap && debt>=seconds) { debt-=seconds; ++steps; }
  return steps;
}

// Game seconds per real second: 1x with nothing flying, rising with the oldest flying ball's bounces used to
// topSpeed on its last, and dropping to dramaSpeed (overriding the rise) near the goal on the last ball.
class Pace {
  float speed_{1};
public:
  static constexpr float topSpeed=2.5f, dramaSpeed=0.35f;
  static constexpr float dropRate=14, easeRate=4; // per real second: into slow motion, and back out of it
  float speed() const { return speed_; }
  void reset() { speed_=1; }
  // The speed the clock is heading for.
  static float target(const Model& m) {
    if(m.balls.empty() || m.over()) return 1;
    if(nearGoal(m)) return dramaSpeed;
    const int used=std::max(0,m.bouncesPerBall-m.balls.front().bounces); // the oldest ball is first
    const float done=m.bouncesPerBall>1 ? std::min(1.0f,static_cast<float>(used)/(m.bouncesPerBall-1)) : 0;
    return 1+(topSpeed-1)*done;
  }
  // One real frame; returns the speed to run it at.
  float step(const Model& m, float dt) {
    const float to=target(m);
    if(m.balls.empty() || m.over()) speed_=1; // nothing flies: nothing to hurry or to ease
    else {
      speed_+=(to-speed_)*(1-std::exp(-(to<speed_ ? dropRate : easeRate)*std::max(0.0f,dt)));
      if(std::abs(speed_-to)<1e-3f) speed_=to;
    }
    return speed_;
  }
};
}
