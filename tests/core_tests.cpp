#include <yy/core.hpp>
#include <yy/input.hpp>
#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <numeric>
#include <string>
#include <utility>
#include <vector>

static void check(bool condition, const char* label) { if(!condition) { std::cerr<<label<<'\n'; std::exit(1); } }

using tapdemo::Hits;
using tapdemo::Model;
static bool near(float a, float b, float within=0.01f) { return std::abs(a-b)<=within; }
static yy::Vec2 pocketCentre(const Model& m, std::size_t i=0) {
  const auto& p=m.pockets[i];
  return {(p.column+p.columns/2.0f)*Model::cell, (p.row+p.rows/2.0f)*Model::cell};
}
static int hitPoints(const Model& m) { return std::accumulate(m.bricks.begin(),m.bricks.end(),0); }
// An empty field but for the given bricks, so a ball's path is known.
static void only(Model& m, const std::vector<std::pair<int,int>>& cells, int hp) {
  std::fill(m.bricks.begin(),m.bricks.end(),0);
  for(auto [c,r]: cells) m.bricks[r*Model::columns+c]=hp;
}
static Hits run(Model& m, float seconds) {
  Hits total;
  for(int i=0; i<static_cast<int>(seconds*60); ++i) {
    m.update(1.0f/60);
    total.bricksHit+=m.hits.bricksHit; total.bricksBroken+=m.hits.bricksBroken;
    total.bounces+=m.hits.bounces; total.ballsSpent+=m.hits.ballsSpent;
  }
  return total;
}
struct Recorder final: yy::Haptics {
  std::vector<std::string> calls;
  void impact(float) override { calls.push_back("impact"); }
  void humStart(float) override { calls.push_back("hum"); }
  void humStop() override { calls.push_back("stop"); }
  void thump() override { calls.push_back("thump"); }
};

static void tapdemoChecks() {
  {
    Model a(123), b(123), c(124);
    check(a.bricks==b.bricks && a.pockets.size()==b.pockets.size() && a.pockets[0].column==b.pockets[0].column && a.pockets[0].row==b.pockets[0].row,"grid deterministic per seed");
    check(a.bricks!=c.bricks,"another seed makes another grid");
    bool sawOne=false, sawTwo=false;
    for(std::uint32_t seed=1; seed<=40; ++seed) {
      Model m(seed);
      check(m.bricks.size()==Model::columns*Model::rows && Model::columns>=20 && Model::rows>=36,"grid is large");
      check(m.pockets.size()==1 || m.pockets.size()==2,"one or two pockets");
      sawOne|=m.pockets.size()==1; sawTwo|=m.pockets.size()==2;
      int pocketCells=0;
      for(std::size_t i=0; i<m.pockets.size(); ++i) {
        const auto& p=m.pockets[i];
        check(p.columns>=3 && p.columns<=5 && p.rows>=3 && p.rows<=5,"pocket 3 to 5 cells a side");
        for(int r=p.row; r<p.row+p.rows; ++r) for(int col=p.column; col<p.column+p.columns; ++col) { check(m.brick(col,r)==0,"pocket is empty"); ++pocketCells; }
        check(m.canPlace(pocketCentre(m,i)),"a ball fits in every pocket");
      }
      check(m.bricksLeft()==Model::columns*Model::rows-pocketCells,"everything else is brick");
      check(std::all_of(m.bricks.begin(),m.bricks.end(),[](int hp){ return hp>=0 && hp<=3; }),"bricks have 1 to 3 hit points");
    }
    check(sawOne && sawTwo,"pocket count varies by seed");
  }
  {
    Model m(5); const auto centre=pocketCentre(m); const auto& p=m.pockets[0];
    check(m.canPlace(centre),"pocket centre is open");
    check(!m.canPlace({(p.column-0.5f)*Model::cell,centre.y}),"a brick cell is not open");
    check(!m.canPlace({p.column*Model::cell+Model::ballRadius*0.5f,centre.y}),"a ball overlapping the pocket edge is not open");
    check(!m.canPlace({-50,-50}) && !m.canPlace({Model::width()+5,10}),"outside the grid is not open");
    check(m.launch(centre,{0,40}),"launch from the pocket");
    check(!m.canPlace(centre),"a flying ball's spot is not open");
  }
  {
    Model m(9); const auto at=pocketCentre(m);
    check(!m.launch(at,{Model::minPull*0.5f,0}) && m.ballsLeft==Model::defaultBalls && m.balls.empty(),"short pull cancels");
    check(m.launch(at,{30,40}) && m.ballsLeft==Model::defaultBalls-1 && m.balls.size()==1,"launch uses a ball");
    const auto v=m.balls[0].velocity;
    check(near(v.x,-0.6f*Model::speed) && near(v.y,-0.8f*Model::speed),"ball flies opposite the pull at fixed speed");
    check(m.balls[0].bounces==Model::defaultBounces,"ball starts with its bounces");
    check(m.launch(at,{0,-200}) && m.balls.size()==2 && near(m.balls[1].velocity.y,Model::speed),"several balls fly at once");
    m.pause(true); const auto held=m.balls[0].position; m.update(1);
    check(m.balls[0].position.x==held.x && m.balls[0].position.y==held.y && !m.launch(at,{0,30}),"pause freezes balls and launches");
  }
  {
    Model m(11); m.restart(5,3); only(m,{{0,0}},1);
    check(m.launch({Model::width()/2,Model::height()/2},{-30,0}),"launch toward the right wall");
    const Hits total=run(m,10);
    check(total.bounces==3 && total.bricksHit==0 && total.ballsSpent==1 && m.balls.empty(),"each wall hit uses a bounce; spent ball removed");
  }
  {
    Model m(12); m.restart(5,99); only(m,{{12,5}},3); m.bricks[0]=1; // a far brick keeps the round going
    check(m.launch({12.5f*Model::cell,20*Model::cell},{0,30}),"launch up at a brick");
    Hits total; int first=-1;
    for(int i=0; i<1200 && m.brick(12,5)>0; ++i) {
      m.update(1.0f/60); total.bricksHit+=m.hits.bricksHit; total.bricksBroken+=m.hits.bricksBroken; total.bounces+=m.hits.bounces;
      if(first<0 && m.hits.bricksHit) { first=m.brick(12,5); check(m.hits.bounces==1 && m.balls[0].bounces==98,"a brick hit uses one bounce"); }
    }
    check(first==2,"a hit takes one hit point");
    check(m.brick(12,5)==0 && total.bricksHit==3 && total.bricksBroken==1,"three-point brick breaks on the third hit and reports it");
    check(total.bounces==5 && m.balls.size()==1,"walls and bricks both count bounces");
    check(!m.won(),"a brick left: not won");
  }
  {
    Model m(13); only(m,{{12,5}},1);
    check(m.launch({12.5f*Model::cell,20*Model::cell},{0,30}),"launch at the last brick");
    run(m,3);
    check(m.won() && m.over() && !m.lost() && m.bricksLeft()==0,"breaking the last brick wins");
    const auto position=m.balls[0].position; m.update(1.0f/60);
    check(position.y==m.balls[0].position.y && !m.launch({100,100},{0,30}),"a won round is frozen");
  }
  {
    Model m(14); m.restart(1,1);
    check(m.launch(pocketCentre(m),{0,30}) && !m.lost(),"last ball in flight: not lost");
    run(m,5);
    check(m.balls.empty() && m.ballsLeft==0 && m.lost() && !m.won(),"no balls left with bricks: lost");
    check(!m.launch(pocketCentre(m),{0,30}),"no launch after losing");
  }
  {
    Model m(15); const auto before=m.bricks;
    m.restart(3,7);
    check(m.ballCount==3 && m.ballsLeft==3 && m.bouncesPerBall==7 && m.balls.empty(),"debug restart sets balls and bounces");
    check(m.launch(pocketCentre(m),{0,30}) && m.balls[0].bounces==7,"restarted ball carries the new bounces");
    check(m.bricks!=before,"restart deals a new grid");
    m.restart(0,500); check(m.ballCount==1 && m.bouncesPerBall==Model::maxSetting,"debug values clamp");
  }
  {
    // The player's path: press in a pocket, pull, release, and a brick loses a hit point.
    Model m(21); tapdemo::Touch t(m); Recorder haptics; t.haptics=&haptics;
    const auto& cam=t.camera;
    check(near(cam.zoom,cam.minZoom()) && Model::width()*cam.zoom<=cam.view.w+0.01f && Model::height()*cam.zoom<=cam.view.h+0.01f,"opens on the whole grid");
    const auto press=cam.toScreen(pocketCentre(m));
    t.down(0,press);
    check(t.aim && haptics.calls==std::vector<std::string>{"hum"},"press in a pocket holds a ball and hums");
    t.move(0,{press.x,press.y+40});
    check(t.aim && t.aim->pull.y>0 && haptics.calls.back()=="hum","pulling keeps the hum and aims");
    const int points=hitPoints(m);
    check(t.up(0,{press.x,press.y+40}) && !t.aim,"release launches");
    check(haptics.calls.size()>=3 && haptics.calls[haptics.calls.size()-2]=="stop" && haptics.calls.back()=="thump","release stops the hum then thumps");
    check(m.balls.size()==1 && m.balls[0].velocity.y<0 && m.ballsLeft==Model::defaultBalls-1,"ball flies opposite the pull");
    for(int i=0; i<120 && hitPoints(m)==points; ++i) m.update(1.0f/60);
    check(hitPoints(m)==points-1,"a brick loses a hit point");

    haptics.calls.clear();
    t.down(0,press); t.up(0,{press.x+2,press.y+2});
    check(!t.aim && haptics.calls==std::vector<std::string>{"hum","hum","stop"} && m.ballsLeft==Model::defaultBalls-1,"a short pull cancels without a thump");

    haptics.calls.clear();
    t.down(0,cam.toScreen({0.5f*Model::cell,0.5f*Model::cell}));
    check(!t.aim && t.rejectTime>0 && haptics.calls.empty(),"press on a brick shows the red ring, no hum");
    t.up(0,{0,0});

    haptics.calls.clear();
    t.down(0,press); t.down(1,{press.x+60,press.y});
    check(!t.aim && haptics.calls.back()=="stop","a second finger cancels the aim and the hum");
    const float zoom=t.camera.zoom;
    t.move(1,{press.x+120,press.y});
    check(t.camera.zoom>zoom*1.5f,"spreading two fingers zooms in");
    t.move(1,{press.x+5000,press.y});
    check(near(t.camera.zoom,tapdemo::Camera::maxZoom),"zoom stops at close-up");
    t.up(1,{0,0}); t.up(0,{0,0});
    check(m.ballsLeft==Model::defaultBalls-1,"a cancelled aim launches nothing");
    t.down(0,press); t.move(0,{press.x,press.y+40}); m.pause(true);
    check(!t.up(0,{press.x,press.y+40}) && m.ballsLeft==Model::defaultBalls-1,"the release a pause forces launches nothing");
    m.pause(false);
    t.camera.zoomAt({195,400},0.01f);
    check(near(t.camera.zoom,t.camera.minZoom()),"zoom stops at the whole grid");
  }
}
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
  tapdemoChecks();
  std::cout<<"Engine and TapDemo checks passed\n";
}
