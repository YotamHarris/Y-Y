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

void paletteChecks();

static void check(bool condition, const char* label) { if(!condition) { std::cerr<<label<<'\n'; std::exit(1); } }

using tapdemo::Hits;
using tapdemo::Model;
using tapdemo::Power;
static bool near(float a, float b, float within=0.01f) { return std::abs(a-b)<=within; }
static yy::Vec2 pocketCentre(const Model& m, std::size_t i=0) {
  const auto& p=m.pockets[i];
  return {(p.column+p.columns/2.0f)*Model::cell, (p.row+p.rows/2.0f)*Model::cell};
}
static int hitPoints(const Model& m) { return std::accumulate(m.bricks.begin(),m.bricks.end(),0); }
// An empty field but for the given bricks, so a ball's path is known; no goal unless a test sets one.
static void only(Model& m, const std::vector<std::pair<int,int>>& cells, int hp) {
  std::fill(m.bricks.begin(),m.bricks.end(),0);
  std::fill(m.powers.begin(),m.powers.end(),Power::None);
  m.goal=-1;
  for(auto [c,r]: cells) m.bricks[r*Model::defaultColumns+c]=hp;
  m.refreshFog();
}
static void set(Model& m, int c, int r, int hp, Power power=Power::None) {
  m.bricks[r*Model::defaultColumns+c]=hp; m.powers[r*Model::defaultColumns+c]=power; m.refreshFog();
}
static void collect(Hits& total, const Hits& h) {
  total.bricksHit+=h.bricksHit; total.bricksBroken+=h.bricksBroken; total.bounces+=h.bounces; total.ballsSpent+=h.ballsSpent;
  total.fired.insert(total.fired.end(),h.fired.begin(),h.fired.end());
  total.goalBroken|=h.goalBroken;
}
static Hits run(Model& m, float seconds) {
  Hits total;
  for(int i=0; i<static_cast<int>(seconds*60); ++i) { m.update(1.0f/60); collect(total,m.hits); }
  return total;
}
// Updates until a power-up fires (at most `seconds`), returning everything that happened.
static Hits untilFired(Model& m, float seconds=3) {
  Hits total;
  for(int i=0; i<static_cast<int>(seconds*60) && total.fired.empty(); ++i) { m.update(1.0f/60); collect(total,m.hits); }
  return total;
}
static int firedCount(const Hits& h, Power p) {
  return static_cast<int>(std::count_if(h.fired.begin(),h.fired.end(),[&](const tapdemo::Fired& f){ return f.power==p; }));
}
// A ball fired straight up the middle of column 12 from row 20.
static bool launchUp(Model& m) { return m.launch({12.5f*Model::cell,20.5f*Model::cell},{0,30}); }
struct Recorder final: yy::Haptics {
  std::vector<std::string> calls;
  void impact(float) override { calls.push_back("impact"); }
  void humStart(float) override { calls.push_back("hum"); }
  void humStop() override { calls.push_back("stop"); }
  void thump() override { calls.push_back("thump"); }
};

static void powerChecks() {
  constexpr int cells=Model::defaultColumns*Model::defaultRows;
  std::vector<std::pair<int,int>> all;
  for(int r=0; r<Model::defaultRows; ++r) for(int c=0; c<Model::defaultColumns; ++c) all.push_back({c,r});
  {
    // About one brick in fifty glows, every kind appears, only bricks glow, and a new grid rerolls them.
    int glowing=0, bricks=0; int kinds[tapdemo::powerKinds+1]{};
    for(std::uint32_t seed=1; seed<=60; ++seed) {
      Model m(seed);
      for(int i=0; i<cells; ++i) {
        if(m.bricks[i]>0) ++bricks;
        if(m.powers[i]==Power::None) continue;
        check(m.bricks[i]>0,"only bricks glow");
        ++glowing; ++kinds[static_cast<int>(m.powers[i])];
      }
    }
    const float share=static_cast<float>(glowing)/bricks;
    check(share>0.014f && share<0.028f,"about one brick in fifty glows");
    for(int k=1; k<=tapdemo::powerKinds; ++k) check(kinds[k]>glowing/tapdemo::powerKinds/2,"each power-up is picked about equally");
    Model a(31), b(31);
    check(a.powers==b.powers,"glowing bricks are deterministic per seed");
    const auto before=a.powers; a.restart();
    check(a.powers!=before,"a new grid rerolls the glowing bricks");
  }
  {
    // Fog: visible within two straight steps of an empty cell, a diamond; walls are no cavity.
    Model m(32); only(m,all,1); set(m,10,10,0);
    check(m.fogDistance(10,10)==0 && m.visible(10,10),"an empty cell is clear");
    check(m.visible(12,10) && m.visible(10,8) && m.visible(11,11) && m.visible(9,9),"two straight steps are visible");
    check(!m.visible(13,10) && !m.visible(12,11) && !m.visible(12,12) && m.fogDistance(12,11)==3,"three steps, or two diagonal, are fogged");
    check(!m.visible(0,0) && m.fogDistance(0,0)==20,"a brick by the wall is fogged: walls are not cavities");
    // Hidden bricks still collide, and fog lifts as bricks break.
    set(m,13,10,3);
    check(m.launch({10.5f*Model::cell,10.5f*Model::cell},{-30,0}),"launch right from the cavity");
    Hits total;
    for(int i=0; i<120 && m.brick(11,10)>0; ++i) { m.update(1.0f/60); collect(total,m.hits); }
    check(m.brick(11,10)==0 && total.bricksBroken==1,"the brick beside the cavity breaks");
    check(m.visible(13,10) && m.fogDistance(13,10)==2 && !m.visible(14,10),"fog lifts two steps past the break");
    Model g(33);
    check(std::all_of(g.pockets.begin(),g.pockets.end(),[&](const tapdemo::Pocket& p){ return g.visible(p.column-1,p.row) && g.visible(p.column-2,p.row+1); })
      ,"a new grid shows the bricks around its pockets");
    int hidden=0;
    for(int r=0; r<Model::defaultRows; ++r) for(int c=0; c<Model::defaultColumns; ++c) hidden+=!g.visible(c,r);
    check(hidden>cells/2,"and most of the grid is under fog");
  }
  {
    // Bomb: breaks the 3x3 around it outright; a glowing brick it breaks fires too, and chains end.
    Model m(34); m.restart(5,99);
    only(m,{{0,0},{11,4},{12,4},{13,4},{11,5},{13,5},{11,6},{13,6},{14,5},{15,5}},3);
    set(m,12,5,1,Power::Bomb); set(m,13,4,3,Power::Bomb); set(m,11,4,3,Power::Ping);
    check(launchUp(m),"launch at a bomb");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Bomb)==2 && firedCount(h,Power::Ping)==1,"the bomb fires, and fires the glowing bricks it breaks");
    for(auto [c,r]: std::vector<std::pair<int,int>>{{11,4},{12,4},{13,4},{11,5},{12,5},{13,5},{11,6},{13,6},{14,5}})
      check(m.brick(c,r)==0,"the bombs break their 3x3 outright");
    check(m.brick(15,5)==3 && m.brick(0,0)==3,"bricks outside the blasts are untouched");
    check(m.pingTime>0 && m.powers[4*Model::defaultColumns+11]==Power::None,"a power-up fires once");
  }
  {
    // Electricity: the ball zaps every brick within 1.5 cells each 0.25 s for 3 s, at no bounce.
    Model m(35); m.restart(5,99);
    only(m,{{0,0}},3); set(m,12,5,1,Power::Electricity);
    check(launchUp(m) && firedCount(untilFired(m),Power::Electricity)==1,"breaking an electric brick fires it");
    check(m.balls[0].electric>Model::electricSeconds-0.1f,"the ball is electric");
    // Hold the ball still among bricks to count the zaps.
    Model z(36); z.restart(5,99);
    only(z,{{9,9},{10,9},{11,9},{9,10},{11,10},{9,11},{10,11},{11,11},{12,10},{0,0}},3);
    check(z.launch({10.5f*Model::cell,10.5f*Model::cell},{0,30}),"a ball among bricks");
    auto& b=z.balls[0]; b.velocity={0,0}; b.electric=Model::electricSeconds; b.zapTimer=Model::electricTick;
    run(z,0.2f);
    check(z.brick(10,9)==3,"no zap before the first tick");
    run(z,0.1f);
    check(z.brick(10,9)==2 && z.brick(11,11)==2 && z.brick(12,10)==3,"a zap takes a point off each brick within 1.5 cells");
    check(z.balls[0].bounces==99,"zaps cost no bounces");
    const Hits h=run(z,4);
    check(z.brick(10,9)==0 && z.brick(9,11)==0 && z.brick(12,10)==3 && h.bricksBroken==8,"zaps keep coming while it lasts");
    check(z.balls[0].electric==0,"electricity runs out");
  }
  {
    // Ping: every glowing brick shows through the fog for a while.
    Model m(37); m.restart(5,99);
    only(m,{{0,0}},3); set(m,12,5,1,Power::Ping);
    check(m.pingTime==0,"no ping at the start");
    check(launchUp(m) && firedCount(untilFired(m),Power::Ping)==1,"breaking a ping brick fires it");
    check(m.pingTime>Model::pingSeconds-0.1f,"ping lasts its time");
    run(m,Model::pingSeconds+0.1f);
    check(m.pingTime==0,"ping wears off");
  }
  {
    // Ghost: the ball reappears deep under the fog, velocity and bounces kept, in a new 3x3 cavity.
    Model m(38); m.restart(5,99);
    only(m,all,2);
    for(int r=6; r<=21; ++r) set(m,12,r,0);
    set(m,12,5,1,Power::Ghost);
    check(launchUp(m),"launch up the corridor at a ghost");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Ghost)==1,"breaking a ghost brick fires it");
    const auto& f=h.fired.front(); const int c=f.to%Model::defaultColumns, r=f.to/Model::defaultColumns;
    const auto& ball=m.balls[0];
    // The rest of that frame's sub-steps move it on a few units.
    check(f.to>=0 && near(ball.position.x,(c+0.5f)*Model::cell,Model::cell/2) && near(ball.position.y,(r+0.5f)*Model::cell,Model::cell/2),"the ball reappears at the new cavity's centre");
    check(std::abs(c-12)>Model::fogReach+1 || r<3 || r>23,"it lands far from where it was, under the fog");
    for(int dr=-1; dr<=1; ++dr) for(int dc=-1; dc<=1; ++dc) check(m.brick(c+dc,r+dr)==0,"the ghost clears its 3x3");
    check(near(std::hypot(ball.velocity.x,ball.velocity.y),Model::speed) && ball.bounces==98,"velocity and bounces are kept");
    check(m.bricksLeft()==cells-16-9-1,"only the ghost brick and the new cavity break");
  }
  {
    // Speed up: the ball goes twice as fast and still never tunnels through a brick or a corner.
    Model m(39); m.restart(5,99);
    only(m,{{0,0}},3); set(m,12,5,1,Power::Speed);
    check(launchUp(m) && firedCount(untilFired(m),Power::Speed)==1,"breaking a speed brick fires it");
    check(m.balls[0].fast && near(std::hypot(m.balls[0].velocity.x,m.balls[0].velocity.y),Model::speed*Model::speedUp,0.1f),"the ball moves twice as fast");
    for(yy::Vec2 pull: {yy::Vec2{0,30},{17,30},{30,17},{-23,29},{29,-3}}) {
      Model t(40); t.restart(5,99);
      std::vector<std::pair<int,int>> wall;
      for(int c=0; c<Model::defaultColumns; ++c) wall.push_back({c,10});
      for(int c=0; c<Model::defaultColumns; c+=2) wall.push_back({c,11});
      only(t,wall,99);
      check(t.launch({12.5f*Model::cell,30.5f*Model::cell},pull),"launch at the wall");
      auto& b=t.balls[0]; b.velocity={b.velocity.x*Model::speedUp,b.velocity.y*Model::speedUp}; b.fast=true;
      bool through=false;
      for(int i=0; i<600; ++i) { t.update(1.0f/60); through|=!t.balls.empty() && t.balls[0].position.y<11*Model::cell+Model::ballRadius-0.01f; }
      check(!through,"a fast ball never passes a brick wall or a corner");
    }
  }
  {
    // The player's path: a slingshot launch that breaks a glowing brick fires its power-up.
    Model m(21); tapdemo::Touch t(m); t.instructions=false;
    const auto at=pocketCentre(m); const auto& p=m.pockets[0];
    const int column=static_cast<int>(at.x/Model::cell);
    for(int c: {column-1,column}) set(m,c,p.row-1,1,Power::Bomb);
    const auto press=t.camera.toScreen(at);
    t.down(0,press); t.move(0,{press.x,press.y+40});
    check(t.up(0,{press.x,press.y+40}),"the slingshot launches up at the glowing brick");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Bomb)>=1 && m.brick(column,p.row-2)==0,"its bomb fires and breaks the bricks behind it");
  }
}

static void goalChecks() {
  constexpr int cells=Model::defaultColumns*Model::defaultRows;
  std::vector<std::pair<int,int>> all;
  for(int r=0; r<Model::defaultRows; ++r) for(int c=0; c<Model::defaultColumns; ++c) all.push_back({c,r});
  const auto goalAt=[](Model& m, int c, int r, int hp) { set(m,c,r,hp); m.goal=r*Model::defaultColumns+c; };
  {
    // One goal per grid: a plain brick under the fog, set by the seed and rerolled with each grid.
    int moved=0;
    for(std::uint32_t seed=1; seed<=60; ++seed) {
      Model m(seed);
      check(m.goal>=0 && m.goal<cells,"every grid has a goal");
      const int c=m.goal%Model::defaultColumns, r=m.goal/Model::defaultColumns;
      int goals=0;
      for(int rr=0; rr<Model::defaultRows; ++rr) for(int cc=0; cc<Model::defaultColumns; ++cc) goals+=m.isGoal(cc,rr);
      check(goals==1,"exactly one goal");
      check(m.brick(c,r)>0 && m.power(c,r)==Power::None,"the goal is a brick and not a power-up");
      check(!m.visible(c,r),"the goal starts under the fog");
      check(!m.won() && !m.over(),"a new grid is not won");
      const int before=m.goal; m.restart(); moved+=m.goal!=before;
      check(m.goal>=0 && !m.visible(m.goal%Model::defaultColumns,m.goal/Model::defaultColumns),"a new grid hides a new goal");
    }
    check(moved>50,"each grid rerolls the goal");
    check(Model(77).goal==Model(77).goal,"the goal is deterministic per seed");
  }
  {
    // Breaking the goal with a ball hit wins and is reported; the round freezes.
    Model m(41); m.restart(5,99); only(m,{{0,0}},3); goalAt(m,12,5,1);
    check(launchUp(m),"launch at the goal");
    Hits h;
    for(int i=0; i<180 && !m.over(); ++i) { m.update(1.0f/60); collect(h,m.hits); }
    check(m.won() && m.over() && !m.lost() && h.goalBroken,"breaking the goal with a hit wins, and Hits reports it");
    check(m.bricksLeft()==1,"other bricks are left");
    const auto position=m.balls[0].position; m.update(1.0f/60);
    check(position.y==m.balls[0].position.y && !m.launch({100,100},{0,30}),"a won round is frozen");
  }
  {
    // A Bomb that breaks the goal wins.
    Model m(42); m.restart(5,99); only(m,{{0,0}},3); goalAt(m,13,4,3); set(m,12,5,1,Power::Bomb);
    check(launchUp(m),"launch at a bomb beside the goal");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Bomb)==1 && h.goalBroken && m.won(),"a bomb that breaks the goal wins");
  }
  {
    // An electric zap that breaks the goal wins.
    Model z(43); z.restart(5,99); only(z,{{0,0}},3); goalAt(z,10,9,1);
    check(z.launch({10.5f*Model::cell,10.5f*Model::cell},{0,30}),"a ball beside the goal");
    auto& b=z.balls[0]; b.velocity={0,0}; b.electric=Model::electricSeconds; b.zapTimer=Model::electricTick;
    const Hits h=run(z,0.3f);
    check(z.brick(10,9)==0 && h.goalBroken && z.won(),"a zap that breaks the goal wins");
  }
  {
    // Breaking every other brick does not win.
    Model m(44); m.restart(5,99); only(m,{{12,5}},1); goalAt(m,0,39,3);
    check(launchUp(m),"launch at the only other brick");
    const Hits h=run(m,2);
    check(m.brick(12,5)==0 && m.bricksLeft()==1 && !h.goalBroken && !m.won() && !m.over(),"clearing the other bricks does not win");
  }
  {
    // Out of balls with the goal standing loses.
    Model m(45); m.restart(1,2);
    check(m.launch(pocketCentre(m),{0,30}),"the only ball");
    run(m,5);
    check(m.lost() && !m.won() && m.brick(m.goal%Model::defaultColumns,m.goal/Model::defaultColumns)>0,"no balls left and the goal standing: lost");
  }
  {
    // Ping shows the cells within its radius of the pinged brick: a goal inside it, not one outside.
    Model m(46); m.restart(5,99); only(m,all,2);
    for(int r=6; r<=21; ++r) set(m,12,r,0);
    set(m,12,5,1,Power::Ping); goalAt(m,12,0,2);
    check(m.pingRadius==Model::defaultPingRadius && Model::defaultPingRadius==6,"the ping radius starts at 6 cells");
    check(!m.pinged(12,0),"nothing is pinged before a ping");
    check(launchUp(m) && firedCount(untilFired(m),Power::Ping)==1,"breaking a ping brick fires it");
    check(!m.visible(12,0) && m.pinged(12,0),"a fogged goal 5 cells away is inside the ping");
    check(!m.visible(5,5) && !m.pinged(5,5) && !m.pinged(12,12),"7 cells away is outside it");
    check(m.pinged(18,5) && !m.pinged(17,9),"the radius is measured centre to centre in cells");
    m.setPingRadius(8);
    check(m.pinged(5,5),"a larger radius reaches further");
    run(m,Model::pingSeconds+0.1f);
    check(!m.pinged(12,0) && m.pingCells().empty(),"the ping wears off");
    m.setPingRadius(0); check(m.pingRadius==Model::minPingRadius,"the radius clamps up to its minimum");
    m.setPingRadius(1000); check(m.pingRadius==Model::maxPingRadius,"the radius clamps down to its maximum");
    m.setPingRadius(9); m.restart(3,7);
    check(m.pingRadius==9 && m.pingTime==0 && m.pingCells().empty(),"restart keeps the radius and ends the ping");
  }
}

// The debug grid settings: size, glow rate and power-up weights apply on restart.
static void settingsChecks() {
  using tapdemo::Settings; using tapdemo::powerKinds;
  {
    Model d(5);
    check(d.columns==24 && d.rows==40 && d.settings.gridScale==Settings::defaultScale && d.settings.glow==4,"the defaults are today's 24x40 grid and 2% glow");
  }
  for(int scale: {Settings::minScale,7,Settings::maxScale}) {
    const int columns=Settings::shapeColumns*scale, rows=Settings::shapeRows*scale;
    for(std::uint32_t seed=1; seed<=30; ++seed) {
      Model m(seed); Settings s; s.gridScale=scale;
      m.restart(m.ballCount,m.bouncesPerBall,s);
      check(m.columns==columns && m.rows==rows && m.bricks.size()==static_cast<std::size_t>(columns*rows) && m.powers.size()==m.bricks.size(),"a restart builds the chosen size");
      check(near(m.width(),columns*Model::cell) && near(m.height(),rows*Model::cell),"the world size follows the grid");
      int pocketCells=0;
      for(const auto& p: m.pockets) {
        check(p.columns>=3 && p.columns<=5 && p.rows>=3 && p.rows<=5 && p.column>=1 && p.row>=1 && p.column+p.columns<=columns-1 && p.row+p.rows<=rows-1,"pockets fit a cell in from the walls");
        for(int r=p.row; r<p.row+p.rows; ++r) for(int c=p.column; c<p.column+p.columns; ++c) { check(m.brick(c,r)==0,"pocket is empty"); ++pocketCells; }
        check(m.visible(p.column,p.row) && m.fogDistance(p.column,p.row)==0,"a pocket is clear of fog");
      }
      check(m.bricksLeft()==columns*rows-pocketCells,"everything else is brick");
      check(m.goal>=0 && m.goal<columns*rows && m.brick(m.goal%columns,m.goal/columns)>0 && m.power(m.goal%columns,m.goal/columns)==Power::None,"the goal is a plain brick");
      check(!m.visible(m.goal%columns,m.goal/columns),"the goal is under the fog");
      check(!m.visible(0,0) && m.fogDistance(columns-1,rows-1)>Model::fogReach,"the corners are fogged");
      m.restart();
      check(m.columns==columns && m.rows==rows,"a plain restart keeps the size");
    }
    Model m(9); Settings s; s.gridScale=scale; m.restart(5,5,s);
    tapdemo::Touch t(m); const auto& cam=t.camera;
    check(near(cam.zoom,cam.minZoom()) && near(std::max(m.width()*cam.zoom/cam.view.w,m.height()*cam.zoom/cam.view.h),1),"the camera fits the whole grid");
    const auto corner=cam.toScreen({m.width(),m.height()});
    check(corner.x<=cam.view.x+cam.view.w+0.01f && corner.y<=cam.view.y+cam.view.h+0.01f && cam.toScreen({0,0}).x>=cam.view.x-0.01f,"the grid sits inside the play area");
    t.camera.zoomAt({195,400},10); t.camera.pan({-100000,-100000});
    const auto far=t.camera.toScreen({m.width(),m.height()});
    check(near(far.x,cam.view.x+cam.view.w,0.05f) && near(far.y,cam.view.y+cam.view.h,0.05f),"panning stops at the far corner of the grid");
  }
  {
    Settings wild; wild.gridScale=99; wild.glow=-3; wild.weights={-1,20,1,1,1};
    Model m(3); m.restart(5,5,wild);
    check(m.settings.gridScale==Settings::maxScale && m.settings.glow==0 && m.settings.weights[0]==0 && m.settings.weights[1]==Settings::maxWeight,"restart clamps the settings");
  }
  const auto tally=[](const Settings& s, int seeds, int counts[powerKinds+1], int& bricks) {
    for(std::uint32_t seed=1; seed<=static_cast<std::uint32_t>(seeds); ++seed) {
      Model m(seed); m.restart(m.ballCount,m.bouncesPerBall,s);
      check(m.goal>=0 && m.power(m.goal%m.columns,m.goal/m.columns)==Power::None,"the goal is always placed");
      for(std::size_t i=0; i<m.bricks.size(); ++i) { bricks+=m.bricks[i]>0; ++counts[static_cast<int>(m.powers[i])]; }
    }
  };
  {
    Settings none; none.glow=0; int counts[powerKinds+1]{}, bricks=0;
    tally(none,40,counts,bricks);
    check(std::accumulate(counts+1,counts+powerKinds+1,0)==0,"a glow rate of 0 makes no glowing bricks");
    Settings zero; zero.weights={0,0,0,0,0}; int zc[powerKinds+1]{}; bricks=0;
    tally(zero,40,zc,bricks);
    check(std::accumulate(zc+1,zc+powerKinds+1,0)==0,"all weights 0 makes no glowing bricks");
  }
  {
    Settings high; high.glow=Settings::maxGlow; int counts[powerKinds+1]{}, bricks=0;
    tally(high,20,counts,bricks);
    const float share=static_cast<float>(std::accumulate(counts+1,counts+powerKinds+1,0))/bricks;
    check(share>0.23f && share<0.27f,"a 25% glow rate makes about a quarter of the bricks glow");
  }
  {
    // Bomb 0, Electricity 1, Ping 2, Ghost 3, Speed 4: Bomb never appears and the rest follow the weights.
    Settings mix; mix.glow=40; mix.weights={0,1,2,3,4}; int counts[powerKinds+1]{}, bricks=0;
    tally(mix,200,counts,bricks);
    const int glowing=std::accumulate(counts+1,counts+powerKinds+1,0);
    check(counts[static_cast<int>(Power::Bomb)]==0,"a kind weighted 0 never appears");
    check(glowing>10000,"enough glowing bricks to judge the mix");
    for(int k=2; k<=powerKinds; ++k) {
      const float share=static_cast<float>(counts[k])/glowing, expected=(k-1)/10.0f;
      check(std::abs(share-expected)<0.02f,"each kind appears in proportion to its weight");
    }
    std::cout<<"Power-up mix 0:1:2:3:4 over "<<glowing<<" glowing bricks:";
    for(int k=1; k<=powerKinds; ++k) std::cout<<' '<<counts[k];
    std::cout<<'\n';
  }
  {
    // A single kind: Ghost only.
    Settings ghosts; ghosts.weights={0,0,0,1,0}; int counts[powerKinds+1]{}, bricks=0;
    tally(ghosts,60,counts,bricks);
    check(counts[static_cast<int>(Power::Ghost)]>0 && std::accumulate(counts+1,counts+powerKinds+1,0)==counts[static_cast<int>(Power::Ghost)],"only the weighted kind glows");
  }
  {
    // The defaults keep the seeded grids from before these settings: the bricks, glowing bricks,
    // goal and pockets of three grids for each of 100 seeds hash to the value the old code gave.
    std::uint64_t h=1469598103934665603ull;
    const auto mixIn=[&](std::uint64_t v) { h=(h^v)*1099511628211ull; };
    for(std::uint32_t seed=1; seed<=100; ++seed) {
      Model m(seed); m.restart(); m.restart(m.ballCount,m.bouncesPerBall,Settings{});
      for(std::size_t i=0; i<m.bricks.size(); ++i) { mixIn(static_cast<std::uint64_t>(m.bricks[i])); mixIn(static_cast<std::uint64_t>(m.powers[i])); }
      mixIn(static_cast<std::uint64_t>(m.goal));
      for(const auto& p: m.pockets) { mixIn(p.column); mixIn(p.row); mixIn(p.columns); mixIn(p.rows); }
    }
    check(h==12729722871215073676ull,"the default settings generate the same seeded grids as before");
  }
}
static void tapdemoChecks() {
  {
    Model a(123), b(123), c(124);
    check(a.bricks==b.bricks && a.pockets.size()==b.pockets.size() && a.pockets[0].column==b.pockets[0].column && a.pockets[0].row==b.pockets[0].row,"grid deterministic per seed");
    check(a.bricks!=c.bricks,"another seed makes another grid");
    bool sawOne=false, sawTwo=false;
    for(std::uint32_t seed=1; seed<=40; ++seed) {
      Model m(seed);
      check(m.bricks.size()==Model::defaultColumns*Model::defaultRows && Model::defaultColumns>=20 && Model::defaultRows>=36,"grid is large");
      check(m.pockets.size()==1 || m.pockets.size()==2,"one or two pockets");
      sawOne|=m.pockets.size()==1; sawTwo|=m.pockets.size()==2;
      int pocketCells=0;
      for(std::size_t i=0; i<m.pockets.size(); ++i) {
        const auto& p=m.pockets[i];
        check(p.columns>=3 && p.columns<=5 && p.rows>=3 && p.rows<=5,"pocket 3 to 5 cells a side");
        for(int r=p.row; r<p.row+p.rows; ++r) for(int col=p.column; col<p.column+p.columns; ++col) { check(m.brick(col,r)==0,"pocket is empty"); ++pocketCells; }
        check(m.canPlace(pocketCentre(m,i)),"a ball fits in every pocket");
      }
      check(m.bricksLeft()==Model::defaultColumns*Model::defaultRows-pocketCells,"everything else is brick");
      check(std::all_of(m.bricks.begin(),m.bricks.end(),[](int hp){ return hp>=0 && hp<=3; }),"bricks have 1 to 3 hit points");
    }
    check(sawOne && sawTwo,"pocket count varies by seed");
  }
  {
    Model m(5); const auto centre=pocketCentre(m); const auto& p=m.pockets[0];
    check(m.canPlace(centre),"pocket centre is open");
    check(!m.canPlace({(p.column-0.5f)*Model::cell,centre.y}),"a brick cell is not open");
    check(!m.canPlace({p.column*Model::cell+Model::ballRadius*0.5f,centre.y}),"a ball overlapping the pocket edge is not open");
    check(!m.canPlace({-50,-50}) && !m.canPlace({m.width()+5,10}),"outside the grid is not open");
    check(m.launch(centre,{0,40}),"launch from the pocket");
    check(m.canPlace(centre),"a flying ball does not block its spot");
    check(m.launch(centre,{0,40}) && m.balls.size()==2,"a ball launches from on top of a flying ball");
  }
  powerChecks();
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
    check(m.launch({m.width()/2,m.height()/2},{-30,0}),"launch toward the right wall");
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
  goalChecks();
  {
    Model m(14); m.restart(1,1);
    check(m.launch(pocketCentre(m),{0,30}) && !m.lost(),"last ball in flight: not lost");
    run(m,5);
    check(m.balls.empty() && m.ballsLeft==0 && m.lost() && !m.won(),"no balls left with the goal standing: lost");
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
    // A press that touches a brick holds the ball at the closest open spot within one cell.
    Model m(30); const float c=Model::cell;
    only(m,{},1); std::fill(m.bricks.begin(),m.bricks.end(),1);
    for(int r=20; r<23; ++r) for(int col=10; col<13; ++col) m.bricks[r*Model::defaultColumns+col]=0; // a 3x3 cavity
    m.refreshFog();
    const yy::Vec2 inside{11.5f*c,21.5f*c};
    auto spot=m.placeNear(inside);
    check(spot && spot->x==inside.x && spot->y==inside.y,"an open tap is unchanged");
    const yy::Vec2 beside{10*c-4,21.5f*c}; // 4 units into the brick left of the cavity
    spot=m.placeNear(beside);
    check(spot && m.canPlace(*spot),"a tap onto a brick snaps to an open spot");
    check(spot && near(spot->x,10*c+Model::ballRadius,0.3f) && near(spot->y,beside.y,0.3f),"it is the closest open spot");
    check(spot && std::hypot(spot->x-beside.x,spot->y-beside.y)<=c,"within one cell of the tap");
    // Just past a corner of the cavity the closest spot is the corner of the open square.
    spot=m.placeNear({10*c-3,20*c-3});
    check(spot && m.canPlace(*spot) && near(spot->x,10*c+Model::ballRadius,0.3f) && near(spot->y,20*c+Model::ballRadius,0.3f),"a tap past a cavity corner snaps to the corner");
    check(!m.placeNear({10*c-30,21.5f*c}),"nothing open within one cell: rejected");
    check(!m.placeNear({3.5f*c,3.5f*c}),"deep in solid bricks: rejected");
    check(!m.placeNear({std::nanf(""),10}),"a non-finite tap is rejected");
    only(m,{},1);
    spot=m.placeNear({4,400});
    check(spot && near(spot->x,Model::ballRadius,0.3f) && near(spot->y,400,0.3f),"a tap near the wall snaps inward");
    spot=m.placeNear({m.width()+5,m.height()-3});
    check(spot && m.canPlace(*spot) && near(spot->x,m.width()-Model::ballRadius,0.3f) && near(spot->y,m.height()-Model::ballRadius,0.3f),"a tap past the corner of the grid snaps to the corner");
  }
  {
    // The touch path: down beside a cavity anchors the aim on the snapped spot, and up launches from it.
    Model m(31); tapdemo::Touch t(m); t.instructions=false; const float c=Model::cell;
    std::fill(m.bricks.begin(),m.bricks.end(),1); std::fill(m.powers.begin(),m.powers.end(),Power::None); m.goal=-1;
    for(int r=20; r<23; ++r) for(int col=10; col<13; ++col) m.bricks[r*Model::defaultColumns+col]=0;
    m.refreshFog();
    const yy::Vec2 tap{10*c-4,21.5f*c};
    t.down(0,t.camera.toScreen(tap));
    check(t.aim && near(t.aim->anchor.x,10*c+Model::ballRadius,0.3f) && near(t.aim->anchor.y,tap.y,0.3f) && t.rejectTime==0,"down on a brick beside a cavity anchors the aim at the snapped spot");
    const yy::Vec2 anchor=t.aim->anchor;
    const yy::Vec2 finger=t.camera.toScreen({anchor.x,anchor.y+30});
    t.move(0,finger);
    check(near(t.aim->pull.x,0,0.1f) && near(t.aim->pull.y,30,0.1f),"pull is measured from the snapped spot");
    check(t.up(0,finger) && m.balls.size()==1 && near(m.balls[0].position.x,anchor.x,0.1f) && near(m.balls[0].position.y,anchor.y,0.1f),"release launches from the snapped spot");
    t.down(0,t.camera.toScreen({3.5f*c,3.5f*c}));
    check(!t.aim && t.rejectTime>0,"down deep in the bricks is still rejected");
  }
  {
    // The player's path: press in a pocket, pull, release, and a brick loses a hit point.
    Model m(21); tapdemo::Touch t(m); Recorder haptics; t.haptics=&haptics;
    const auto& cam=t.camera;
    check(near(cam.zoom,cam.minZoom()) && m.width()*cam.zoom<=cam.view.w+0.01f && m.height()*cam.zoom<=cam.view.h+0.01f,"opens on the whole grid");
    const auto press=cam.toScreen(pocketCentre(m));
    check(t.instructions,"the instructions show when the game opens");
    t.down(0,press);
    check(!t.instructions && !t.aim && haptics.calls.empty(),"the first press only dismisses the instructions");
    t.move(0,{press.x,press.y+40});
    check(!t.up(0,{press.x,press.y+40}) && m.ballsLeft==Model::defaultBalls && m.balls.empty(),"and its pull and release place no ball");
    t.down(0,press);
    check(t.aim && haptics.calls==std::vector<std::string>{"hum"},"then a press in a pocket holds a ball and hums");
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
  settingsChecks();
  paletteChecks();
  std::cout<<"Engine and TapDemo checks passed\n";
}
