#include <yy/core.hpp>
#include <tapdemo/model.hpp>
#include <cstdlib>
#include <iostream>

static void check(bool condition, const char* label) { if(!condition) { std::cerr<<label<<'\n'; std::exit(1); } }
int main() {
  yy::Viewport v{{10,40,780,1688}};
  auto point=v.map({400,884});
  check(point && std::abs(point->x-195)<0.001f && std::abs(point->y-422)<0.001f,"safe-area input maps correctly");
  check(!v.map({0,0}),"letterbox taps ignored");
  yy::Viewport wide{{0,0,1200,600}};
  check(!wide.map({1,300}),"wide safe-area letterbox ignored");
  auto center=wide.map({600,300}); check(center && std::abs(center->x-195)<0.001f && std::abs(center->y-422)<0.001f,"wide viewport center maps correctly");
  check(!yy::Viewport{}.map({0,0}),"zero viewport safe");
  yy::FixedClock clock; int ticks=0;
  clock.advance(1.0/30,[&](float){++ticks;}); check(ticks==2,"fixed updates");
  clock.reset(); clock.advance(100,[&](float){++ticks;}); check(ticks==8,"resume catch-up capped");
  tapdemo::Model a(123),b(123);
  for(int i=0;i<600;++i) { a.update(1.0f/60); b.update(1.0f/60); }
  check(a.targets[0].position.x==b.targets[0].position.x,"seeded gameplay deterministic");
  const float time=a.remaining; const auto position=a.targets[0].position;
  a.pause(true); a.update(5); check(a.remaining==time && a.targets[0].position.x==position.x,"pause freezes timer and targets");
  check(!a.tap(position),"paused taps ignored"); a.pause(false);
  check(a.tap(position) && a.score==1,"tap awards point");
  check(!a.tap({-100,-100}) && a.score==1,"miss does not score");
  a.update(100); check(a.finished() && a.remaining==0,"round ends without negative timer");
  a.tap({0,0}); check(a.score==0 && a.remaining==30,"tap restarts round");
  std::cout<<"Engine and TapDemo checks passed\n";
}
