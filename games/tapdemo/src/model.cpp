#include <tapdemo/model.hpp>

namespace tapdemo {
float Model::random() {
  randomState ^= randomState << 13; randomState ^= randomState >> 17; randomState ^= randomState << 5;
  return static_cast<float>(randomState & 0xffff) / 65535.0f;
}
void Model::place(Target& t) {
  t.position = {38 + random()*314, 220 + random()*430};
  t.velocity = {(random()-0.5f)*130, (random()-0.5f)*130};
}
Model::Model(std::uint32_t seed): randomState(seed ? seed : 42) { restart(); }
void Model::restart() { score=0; remaining=30; paused_=false; for(auto& t: targets) place(t); }
void Model::update(float dt) {
  if(paused_ || finished() || !std::isfinite(dt) || dt<=0) return;
  dt = std::min(dt, remaining); remaining=std::max(0.0f, remaining-dt);
  for(auto& t: targets) {
    t.position.x += t.velocity.x*dt; t.position.y += t.velocity.y*dt;
    if(t.position.x<t.radius+16 || t.position.x>374-t.radius) {
      t.velocity.x = -t.velocity.x; t.position.x = std::clamp(t.position.x,t.radius+16,374-t.radius);
    }
    if(t.position.y<190+t.radius || t.position.y>720-t.radius) {
      t.velocity.y = -t.velocity.y; t.position.y = std::clamp(t.position.y,190+t.radius,720-t.radius);
    }
  }
}
bool Model::tap(yy::Vec2 p) {
  if(paused_) return false;
  if(finished()) { restart(); return false; }
  for(auto& t: targets) {
    const float dx=p.x-t.position.x, dy=p.y-t.position.y;
    if(dx*dx+dy*dy<=t.radius*t.radius) { ++score; place(t); return true; }
  }
  return false;
}
}
