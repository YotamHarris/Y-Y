#include <yy/core.hpp>
#include <yy/input.hpp>
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
  {
    using Phase=yy::PointerEvent::Phase;
    auto at=[](const std::optional<yy::PointerEvent>& e,Phase phase,int id,float x,float y) {
      return e && e->phase==phase && e->id==id && std::abs(e->position.x-x)<0.001f && std::abs(e->position.y-y)<0.001f;
    };
    yy::PointerTracker pointers; const std::uint64_t a=7001,b=42;
    check(at(pointers.down(a,{400,884},v),Phase::Down,0,195,422),"finger down maps to logical point with id 0");
    check(at(pointers.move(a,{420,904},v),Phase::Move,0,205,432),"held finger drag maps to logical point");
    check(at(pointers.down(b,{210,440},v),Phase::Down,1,100,200),"second finger takes id 1");
    check(!pointers.down(a,{400,884},v) && pointers.active()==2,"repeated down for a held finger ignored");
    check(at(pointers.move(b,{230,460},v),Phase::Move,1,110,210),"second finger moves under its own id");
    check(at(pointers.move(a,{0,0},v),Phase::Move,0,0,0),"drag into the letterbox clamps to the edge");
    check(at(pointers.up(a,{5000,5000},v),Phase::Up,0,390,844),"release outside clamps to the far edge");
    check(!pointers.move(a,{400,884},v) && !pointers.up(a,{400,884},v),"released finger sends nothing more");
    check(at(pointers.down(yy::PointerTracker::mouse,{400,884},v),Phase::Down,0,195,422),"mouse reuses the lowest free id");
    check(!pointers.down(99,{0,0},v) && pointers.active()==2,"down in the letterbox ignored");
    const auto ended=pointers.cancel();
    check(ended.size()==2 && pointers.active()==0 && ended[0].phase==Phase::Up && ended[0].id==1 && std::abs(ended[0].position.x-110)<0.001f
      && ended[1].id==0 && std::abs(ended[1].position.y-422)<0.001f,"cancel ends every held pointer where it was");
    check(!pointers.move(yy::PointerTracker::mouse,{400,884},v),"hover without a press sends nothing");
    check(!pointers.down(1,{1,1},yy::Viewport{}) && !pointers.move(1,{1,1},yy::Viewport{}),"zero viewport input ignored");
  }
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
