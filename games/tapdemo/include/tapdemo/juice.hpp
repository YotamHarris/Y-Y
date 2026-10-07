#pragma once
#include <tapdemo/model.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <vector>

namespace tapdemo {
// Hit feedback is presentation only (T23): it reads the model's Hits and Fired reports and never
// writes the model, its seeds or its clock. Everything here is SDL-free and steps by explicit seconds.

// A short hold on the struck ball, in real seconds. The simulation keeps running; only the drawing waits.
struct HitStop {
  static constexpr float seconds=0.045f;
  float left{};
  void trigger() { left=seconds; }
  void step(float dt) { left=std::max(0.0f,left-dt); }
  bool holding() const { return left>0; }
};

// The break sound climbs a step with every hit or break of one ball's flight, and starts over when a new ball launches.
class PitchLadder {
  int step_{};
public:
  static constexpr float base=660;
  static constexpr int topStep=10;
  // Semitones above the base, a major pentatonic run so a rapid chain stays pleasant.
  static constexpr int semitones[topStep+1]{0,2,4,7,9,12,14,16,19,21,24};
  int step() const { return step_; }
  void reset() { step_=0; }
  // One more hit or break: the step the next sound plays at.
  int climb(int hits=1) { step_=std::min(topStep,step_+std::max(0,hits)); return step_; }
  static float hz(int step) { return base*std::exp2(semitones[std::clamp(step,0,topStep)]/12.0f); }
  float hz() const { return hz(step_); }
};

// A screen shake: a bump of `amount` logical units that fades out over `seconds`.
class Shake {
  float amplitude_{}, left_{}, total_{1}, time_{};
public:
  static constexpr float seconds=0.28f, limit=12;
  // A bump never lowers a shake still going; several together add, up to the limit.
  void bump(float amount) {
    const float current=amplitude();
    amplitude_=std::min(limit,std::max(current,amount)+std::min(current,amount)*0.5f);
    left_=total_=seconds;
  }
  void step(float dt) { time_+=dt; left_=std::max(0.0f,left_-dt); if(left_<=0) amplitude_=0; }
  float amplitude() const { return left_>0 ? amplitude_*left_/total_ : 0; }
  // The offset to draw the board at; zero when still.
  yy::Vec2 offset() const {
    const float a=amplitude();
    return {a*std::sin(time_*83.0f), a*std::cos(time_*97.0f+1.3f)};
  }
};

// How big a break's shake is: a plain break barely nudges, a bomb and lightning grow it, the goal is biggest.
inline float shakeFor(int broken, bool bomb, bool electric, bool goal) {
  float amount=std::min(3.0f,0.8f+0.4f*static_cast<float>(broken));
  if(electric) amount=std::max(amount,4.0f);
  if(bomb) amount=std::max(amount,7.0f);
  if(goal) amount=std::max(amount,10.0f);
  return amount;
}

// A fixed pool of short-lived specks: chips from a broken brick and a ball's fading trail. Nothing
// allocates after construction; a full pool drops the newest.
struct Speck {
  yy::Vec2 at{}, velocity{};
  float age{}, life{}, size{};
  yy::Color color{};
  bool live{}, trail{};
};
class SpeckPool {
  std::vector<Speck> specks_;
  int live_{};
public:
  static constexpr int capacity=320;
  SpeckPool() { specks_.resize(capacity); }
  int live() const { return live_; }
  const std::vector<Speck>& all() const { return specks_; }
  void clear() { for(auto& s: specks_) s.live=false; live_=0; }
  bool add(const Speck& s) {
    for(auto& slot: specks_) if(!slot.live) { slot=s; slot.live=true; ++live_; return true; }
    return false;
  }
  // Moves every speck: chips fall under gravity (screen units per second squared), trail points stay put.
  void step(float dt, float gravity) {
    for(auto& s: specks_) {
      if(!s.live) continue;
      s.age+=dt;
      if(s.age>=s.life) { s.live=false; --live_; continue; }
      if(s.trail) continue;
      s.velocity.y+=gravity*dt;
      s.at.x+=s.velocity.x*dt; s.at.y+=s.velocity.y*dt;
    }
  }
};

// Hit points as cracks (T23): stage 0 is whole (3 hit points or more), 1 cracked (2), 2 badly cracked (1).
inline int crackStage(int hp) { return hp>=3 ? 0 : hp==2 ? 1 : hp==1 ? 2 : 0; }
// A crack's pieces in the brick's unit square, as axis-aligned rectangles that step like a fracture.
struct CrackPiece { float x, y, w, h; };
inline constexpr float crackThickness=0.045f;
inline constexpr std::array<CrackPiece,4> crackedPieces{{
  {0.52f,0.10f,crackThickness,0.26f}, {0.40f,0.36f,0.16f,crackThickness}, {0.40f,0.36f,crackThickness,0.24f}, {0.28f,0.60f,0.16f,crackThickness}}};
inline constexpr std::array<CrackPiece,10> badlyCrackedPieces{{
  {0.52f,0.08f,crackThickness,0.26f}, {0.40f,0.34f,0.16f,crackThickness}, {0.40f,0.34f,crackThickness,0.24f}, {0.26f,0.58f,0.18f,crackThickness}, {0.26f,0.58f,crackThickness,0.30f},
  {0.56f,0.34f,0.20f,crackThickness}, {0.72f,0.34f,crackThickness,0.22f}, {0.72f,0.56f,0.16f,crackThickness},
  {0.12f,0.30f,0.16f,crackThickness}, {0.12f,0.30f,crackThickness,0.18f}}};
// The pieces for a stage; empty for stage 0.
inline const CrackPiece* crackPieces(int stage, int& count) {
  if(stage==1) { count=static_cast<int>(crackedPieces.size()); return crackedPieces.data(); }
  if(stage>=2) { count=static_cast<int>(badlyCrackedPieces.size()); return badlyCrackedPieces.data(); }
  count=0; return nullptr;
}

// The fog peeling off cells a break uncovered. `begin` marks a cell as just uncovered; `progress`
// runs 0 to 1 over `seconds`. A power-up brick or the goal also gets a highlight that outlasts the peel.
class FogLift {
public:
  static constexpr float seconds=0.3f, highlightSeconds=0.7f;
  struct Entry { int cell{}; float age{}; bool special{}; };
  explicit FogLift(int cells=0) { resize(cells); }
  void resize(int cells) { entries_.clear(); entries_.reserve(static_cast<std::size_t>(std::max(cells,0))); }
  void clear() { entries_.clear(); }
  void begin(int cell, bool special) {
    for(auto& e: entries_) if(e.cell==cell) { e.age=0; e.special=e.special||special; return; }
    entries_.push_back({cell,0,special});
  }
  void step(float dt) {
    for(auto& e: entries_) e.age+=dt;
    std::erase_if(entries_,[](const Entry& e){ return e.age>=(e.special ? highlightSeconds : seconds); });
  }
  const std::vector<Entry>& entries() const { return entries_; }
  // 0 while the fog still covers the cell, 1 once it has peeled away; cells not lifting read 1.
  float progress(int cell) const {
    for(const auto& e: entries_) if(e.cell==cell) return std::clamp(e.age/seconds,0.0f,1.0f);
    return 1;
  }
  bool lifting(int cell) const { return progress(cell)<1; }
  // 0..1..0 pop for a special cell, 0 when none.
  float highlight(int cell) const {
    for(const auto& e: entries_) if(e.cell==cell && e.special) return std::sin(std::clamp(e.age/highlightSeconds,0.0f,1.0f)*3.14159265f);
    return 0;
  }
private:
  std::vector<Entry> entries_;
};

// The soft pitch of the fog's lifting: a gentle rising pair by how many cells came free.
inline float fogLiftHz(int cells) { return 392.0f+std::min(cells,8)*12.0f; }
}
