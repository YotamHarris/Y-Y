#pragma once
#include <tapdemo/model.hpp>
#include <algorithm>
#include <cmath>

namespace tapdemo {
// The goal celebration is presentation only: nothing here changes what the model does.

// What a look-ahead on a copy of the model saw: whether the goal breaks inside the window, and how
// many game seconds from now.
struct Anticipation { bool hit{}; float seconds{}; };

// Runs a copy of `model` ahead by `window` game seconds in steps of `dt`, the step the real model
// takes, and reports the first update that breaks the goal. Power-up chains and Ghost landings are
// part of the copy, so it sees exactly the hit that will happen (its random state is copied too).
inline Anticipation lookAhead(const Model& model, float dt, float window) {
  Anticipation seen;
  if(model.over() || model.paused() || model.balls.empty() || model.goal<0 || !(dt>0) || !std::isfinite(dt)) return seen;
  const float step=std::max(dt,window/120);
  const int count=static_cast<int>(std::ceil(window/step));
  Model copy=model;
  for(int i=1; i<=count; ++i) {
    copy.update(step);
    if(copy.hits.goalBroken) { seen.hit=true; seen.seconds=i*step; break; }
    if(copy.balls.empty()) break;
  }
  return seen;
}

// The celebration's clock, in real seconds. While a ball flies and the look-ahead sees the goal
// break, time eases down and the camera eases in (Approach); the break starts the Hit, which
// holds on the goal, eases the camera back, shows GOAL! and then gives the win card (Card).
class Celebration {
public:
  enum class Phase { Idle, Approach, Hit, Card };
  static constexpr float window=0.4f;                       // game seconds the look-ahead covers
  static constexpr float slowScale=0.25f;                   // game seconds per real second at the slowest
  static constexpr float slowDown=0.15f, slowUp=0.3f;       // real seconds to reach slowScale, and to leave it
  static constexpr float zoomIn=0.35f, zoomOut=0.8f;        // real seconds for the camera in and back
  static constexpr float holdSeconds=0.55f;                 // after the break the camera stays on the goal
  static constexpr float goalTextAt=0.85f, cardAt=2.0f;     // seconds after the break
  static constexpr float totalSeconds=cardAt;               // the break to the win card

  Phase phase() const { return phase_; }
  float scale() const { return scale_; }
  // How far the camera is toward the goal, 0..1, eased.
  float focus() const { return focus_*focus_*(3-2*focus_); }
  // A hit is coming or has come, and the celebration has not yet reached the card.
  bool playing() const { return phase_==Phase::Hit || (phase_==Phase::Approach && coming_); }
  // The celebration owns the camera and the time scale.
  bool engaged() const { return phase_==Phase::Approach || phase_==Phase::Hit; }
  bool card() const { return phase_==Phase::Card; }
  float time() const { return t_; } // real seconds since the break (Hit), or since the card (Card)
  // 0 before GOAL! shows, then 0..1 as it pops in.
  float goalText() const { return phase_==Phase::Hit ? std::clamp((t_-goalTextAt)/0.3f,0.0f,1.0f) : 0; }

  // One real frame. `coming` is whether the look-ahead sees the goal break; it only matters before the break.
  void step(float dt, bool coming) {
    if(!(dt>0)) return;
    switch(phase_) {
    case Phase::Idle: case Phase::Approach:
      coming_=coming;
      scale_=coming ? std::max(slowScale,scale_-(1-slowScale)/slowDown*dt) : std::min(1.0f,scale_+(1-slowScale)/slowUp*dt);
      focus_=coming ? std::min(1.0f,focus_+dt/zoomIn) : std::max(0.0f,focus_-dt/zoomOut);
      phase_=(coming || scale_<1 || focus_>0) ? Phase::Approach : Phase::Idle;
      break;
    case Phase::Hit:
      t_+=dt;
      focus_=t_<holdSeconds ? std::min(1.0f,focus_+dt/zoomIn) : std::max(0.0f,focus_-dt/zoomOut);
      if(t_>=cardAt) { phase_=Phase::Card; t_=0; focus_=0; }
      break;
    case Phase::Card: t_+=dt; break;
    }
  }
  // The goal broke: normal time from here.
  void hit() { if(phase_==Phase::Card) return; phase_=Phase::Hit; t_=0; scale_=1; coming_=true; }
  // Straight to the card (the model has already won).
  void skip() { phase_=Phase::Card; t_=0; scale_=1; focus_=0; coming_=false; }
  // Back to normal time and framing without a card: an anticipation that did not come true, or a new round.
  void reset() { *this=Celebration(); }

private:
  Phase phase_{Phase::Idle};
  float t_{}, scale_{1}, focus_{};
  bool coming_{};
};
}
