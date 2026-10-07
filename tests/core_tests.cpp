#include <yy/core.hpp>
#include <yy/input.hpp>
#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <tapdemo/juice.hpp>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <numeric>
#include <string>
#include <utility>
#include <vector>

void paletteChecks();
void glintChecks();
void levelFlowChecks();
void debugPersistenceChecks();
void levelBandChecks();
void celebrationChecks();
void fontChecks();
void juiceChecks();
void paceChecks();
void framingGameChecks();
void boardRenderingChecks();

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
    {
      // The first fogged ring, which the renderer fades, is exactly the fogged cells touching a visible cell.
      Model e(34); only(e,all,1); set(e,10,10,0); set(e,20,5,0); set(e,0,0,0);
      int ring=0;
      bool exact=true;
      for(int r=0; r<e.rows; ++r) for(int c=0; c<e.columns; ++c) {
        const bool touches=(r>0 && e.visible(c,r-1)) || (r+1<e.rows && e.visible(c,r+1)) || (c>0 && e.visible(c-1,r)) || (c+1<e.columns && e.visible(c+1,r));
        const bool edge=!e.visible(c,r) && touches;
        exact=exact && edge==(e.fogDistance(c,r)==Model::fogReach+1);
        ring+=edge;
      }
      check(exact && ring>0,"the edge ring is the fogged cells touching a visible cell");
      check(e.fogDistance(13,10)==3 && !e.visible(13,10) && e.visible(12,10),"and fog visibility is unchanged at the ring");
    }
    Model g(33);
    check(std::all_of(g.pockets.begin(),g.pockets.end(),[&](const tapdemo::Pocket& p){ return g.visible(p.column-1,p.row) && g.visible(p.column-2,p.row+1); })
      ,"a new grid shows the bricks around its pockets");
    int hidden=0;
    for(int r=0; r<Model::defaultRows; ++r) for(int c=0; c<Model::defaultColumns; ++c) hidden+=!g.visible(c,r);
    check(hidden>cells/2,"and most of the grid is under fog");
  }
  {
    // Bomb: breaks the 5x5 around it outright; a glowing brick it breaks fires too, and chains end.
    // A 7x9 block of 3-point bricks, with a corridor up column 12 to a bomb at (12,8).
    const auto block=[&](Model& m, int size) {
      m.restart(5,99); m.setBombSize(size);
      std::vector<std::pair<int,int>> cells{{0,0}};
      for(int r=4; r<=10; ++r) for(int c=8; c<=16; ++c) if(c!=12 || r<9) cells.push_back({c,r});
      only(m,cells,3); set(m,12,8,1,Power::Bomb);
    };
    Model m(34); block(m,Model::defaultBombSize);
    check(m.bombSize==5,"a bomb is 5x5 by default");
    set(m,14,6,3,Power::Ping); set(m,10,10,3,Power::Bomb);
    check(launchUp(m),"launch at a bomb");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Bomb)==2 && firedCount(h,Power::Ping)==1,"the bomb fires, and fires the glowing bricks it breaks");
    for(int r=6; r<=10; ++r) for(int c=10; c<=14; ++c) check(m.brick(c,r)==0,"a bomb clears the 5x5 square around it");
    for(int r=8; r<=10; ++r) check(m.brick(8,r)==0,"the bomb it set off clears its own 5x5");
    for(int c=8; c<=16; ++c) check(m.brick(c,4)==3 && (c<10 || c>14 || m.brick(c,5)==3),"bricks above the blasts are untouched");
    for(int r=5; r<=10; ++r) check(m.brick(15,r)==3 && m.brick(16,r)==3,"bricks right of the blast are untouched");
    check(m.brick(0,0)==3 && m.brick(8,7)==3,"bricks outside the blasts are untouched");
    check(m.pingTime>0 && m.power(14,6)==Power::None,"a power-up fires once");
    // The size is a setting: odd, clamped, applied at once and kept by restart.
    Model s(34); block(s,3);
    check(launchUp(s) && firedCount(untilFired(s),Power::Bomb)==1,"a 3x3 bomb fires");
    check(s.brick(11,7)==0 && s.brick(13,9)==0 && s.brick(10,8)==3 && s.brick(12,6)==3,"a bomb size of 3 clears only the 3x3");
    s.setBombSize(4); check(s.bombSize==5,"an even bomb size rounds up to odd");
    s.setBombSize(1); check(s.bombSize==Model::minBombSize,"bomb size clamps up to 3");
    s.setBombSize(99); check(s.bombSize==Model::maxBombSize && Model::maxBombSize==11,"bomb size clamps down to 11");
    s.setBombSize(7); s.restart(); check(s.bombSize==7,"restart keeps the bomb size");
  }
  {
    // Electricity: the ball zaps every brick within 2.5 cells each 0.25 s for 6 s, at no bounce.
    Model m(35); m.restart(5,99);
    check(m.electricSeconds==6 && near(m.electricRadius,2.5f),"lightning lasts 6 s and reaches 2.5 cells by default");
    only(m,{{0,0}},3); set(m,12,5,1,Power::Electricity);
    check(launchUp(m) && firedCount(untilFired(m),Power::Electricity)==1,"breaking an electric brick fires it");
    check(m.balls[0].electric>5.9f,"the ball is electric for 6 s");
    // Hold the ball still at (10,10) among bricks to count the zaps: (10,8) is 2 cells away,
    // (12,11) 2.24, (12,12) 2.83 and (13,10) 3.
    Model z(36); z.restart(5,99);
    only(z,{{10,9},{10,8},{12,11},{12,12},{13,10},{0,0}},99);
    check(z.launch({10.5f*Model::cell,10.5f*Model::cell},{0,30}),"a ball among bricks");
    auto& b=z.balls[0]; b.velocity={0,0}; b.electric=static_cast<float>(z.electricSeconds); b.zapTimer=Model::electricTick;
    run(z,0.2f);
    check(z.brick(10,9)==99,"no zap before the first tick");
    run(z,0.1f);
    check(z.brick(10,9)==98 && z.brick(10,8)==98 && z.brick(12,11)==98,"a zap takes a point off each brick within 2.5 cells");
    check(z.brick(12,12)==99 && z.brick(13,10)==99,"bricks past 2.5 cells are not zapped");
    check(z.balls[0].bounces==99,"zaps cost no bounces");
    run(z,5.5f);
    check(z.balls[0].electric>0,"still electric at 5.8 s");
    run(z,0.3f);
    check(z.balls[0].electric==0 && z.brick(10,9)>=99-24 && z.brick(10,9)<=99-23 && z.brick(12,12)==99,"electricity runs out at 6 s, after 24 zaps");
    // The time and reach are settings: clamped, applied at once and kept by restart.
    z.setElectricRadius(3.2f); check(near(z.electricRadius,3),"reach steps by half cells");
    z.setElectricRadius(0); check(near(z.electricRadius,Model::minElectricRadius),"reach clamps up to 1 cell");
    z.setElectricRadius(100); check(near(z.electricRadius,Model::maxElectricRadius),"reach clamps down to 6 cells");
    z.setElectricSeconds(0); check(z.electricSeconds==Model::minElectricSeconds,"lightning lasts at least 1 s");
    z.setElectricSeconds(99); check(z.electricSeconds==Model::maxElectricSeconds,"lightning lasts at most 15 s");
    z.setElectricRadius(3.5f); z.setElectricSeconds(2); z.restart();
    check(near(z.electricRadius,3.5f) && z.electricSeconds==2,"restart keeps the lightning settings");
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
    // Ghost never takes another power-up: most landings have a Ping brick in their 3x3, and it lands clean.
    // A Ping at every fourth column on every second row leaves only columns 2 mod 4 clean.
    Model m(38); m.restart(5,99);
    only(m,all,2);
    for(int r=0; r<Model::defaultRows; r+=2) for(int c=0; c<Model::defaultColumns; c+=4) m.powers[r*Model::defaultColumns+c]=Power::Ping;
    for(int r=6; r<=21; ++r) set(m,12,r,0);
    set(m,12,5,1,Power::Ghost);
    int powersBefore=0; for(Power p: m.powers) powersBefore+=p==Power::Ping;
    check(launchUp(m),"launch up the corridor at a ghost among power-ups");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Ghost)==1 && h.fired.size()==1 && m.pingTime==0,"the ghost fires and no other power-up does");
    const int c=h.fired.front().to%Model::defaultColumns, r=h.fired.front().to/Model::defaultColumns;
    check(h.fired.front().to>=0 && c%4==2,"it lands on a clean spot");
    int powersAfter=0; for(Power p: m.powers) powersAfter+=p==Power::Ping;
    check(powersAfter==powersBefore,"no power-up was taken by the landing");
  }
  {
    // Every landing has a power-up: the cavity clears, nothing else fires, and those power-ups are gone.
    Model m(38); m.restart(5,99);
    only(m,all,2);
    for(int r=0; r<Model::defaultRows; r+=2) for(int c=0; c<Model::defaultColumns; c+=2) m.powers[r*Model::defaultColumns+c]=Power::Ping;
    for(int r=6; r<=21; ++r) set(m,12,r,0);
    set(m,12,5,1,Power::Ghost);
    check(launchUp(m),"launch up the corridor at a ghost in a field of power-ups");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Ghost)==1 && h.fired.size()==1 && m.pingTime==0,"the cavity's power-ups vanish without firing");
    const int c=h.fired.front().to%Model::defaultColumns, r=h.fired.front().to/Model::defaultColumns;
    bool gone=true;
    for(int dr=-1; dr<=1; ++dr) for(int dc=-1; dc<=1; ++dc) gone=gone && m.brick(c+dc,r+dr)==0 && m.power(c+dc,r+dr)==Power::None;
    check(gone,"the 3x3 is clear of bricks and power-ups");
    int left=0; for(Power p: m.powers) left+=p==Power::Ping;
    check(left>0,"power-ups outside the cavity are untouched");
  }
  {
    // A landing whose 3x3 holds the goal wins.
    Model m(38); m.restart(5,99);
    only(m,{{5,25},{6,25}},2);
    set(m,12,5,1,Power::Ghost);
    m.goal=25*Model::defaultColumns+6; m.bricks[m.goal]=1;
    check(launchUp(m),"launch at a ghost with the goal in its only landing");
    const Hits h=untilFired(m);
    check(firedCount(h,Power::Ghost)==1 && h.goalBroken && m.won() && m.brick(6,25)==0,"the ghost's cavity breaks the goal and wins");
  }
  {
    // The goal and every glowing brick break in one hit, on every level and on free-play seeds.
    const auto oneHit=[&](const Model& m, const char* label) {
      check(m.goal>=0 && m.bricks[m.goal]==1,label);
      for(std::size_t i=0; i<m.bricks.size(); ++i) check(m.powers[i]==Power::None || m.bricks[i]==1,"a power-up brick has 1 hit point");
    };
    for(int level=1; level<=tapdemo::levelCount; ++level) { Model m(7); m.play(level); oneHit(m,"the goal has 1 hit point on every level"); }
    for(std::uint32_t seed=1; seed<=60; ++seed) { Model m(seed); oneHit(m,"the goal has 1 hit point on free-play seeds"); m.restart(); oneHit(m,"and on a new grid"); }
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
    auto& b=z.balls[0]; b.velocity={0,0}; b.electric=static_cast<float>(z.electricSeconds); b.zapTimer=Model::electricTick;
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
    tapdemo::Touch t(m); t.camera.fit(); const auto& cam=t.camera; // explicit whole-grid camera math
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
    // The defaults keep the seeded grids: the bricks, glowing bricks, goal and pockets of three
    // grids for each of 100 seeds hash to a pinned value. It last changed when glowing bricks
    // became one-hit and bombs moved off the walls (T8), and again when the goal became one-hit (T19);
    // the random stream itself is unchanged.
    std::uint64_t h=1469598103934665603ull;
    const auto mixIn=[&](std::uint64_t v) { h=(h^v)*1099511628211ull; };
    for(std::uint32_t seed=1; seed<=100; ++seed) {
      Model m(seed); m.restart(); m.restart(m.ballCount,m.bouncesPerBall,Settings{});
      for(std::size_t i=0; i<m.bricks.size(); ++i) { mixIn(static_cast<std::uint64_t>(m.bricks[i])); mixIn(static_cast<std::uint64_t>(m.powers[i])); }
      mixIn(static_cast<std::uint64_t>(m.goal));
      for(const auto& p: m.pockets) { mixIn(p.column); mixIn(p.row); mixIn(p.columns); mixIn(p.rows); }
    }
    check(h==3712109982144219255ull,"the default settings generate the same seeded grids as before");
  }
}
// Generation: every glowing brick breaks in one hit, and no bomb lies within half its blast of a wall.
static void glowChecks() {
  using tapdemo::Settings; using tapdemo::powerKinds;
  int bombs=0, edgeGlows=0;
  for(int size: {3,Model::defaultBombSize,9}) for(int scale: {Settings::minScale,Settings::defaultScale,7}) for(std::uint32_t seed=1; seed<=12; ++seed) {
    Model m(seed); m.setBombSize(size);
    Settings s; s.gridScale=scale; s.glow=Settings::maxGlow; m.restart(5,5,s);
    const int half=size/2;
    for(int r=0; r<m.rows; ++r) for(int c=0; c<m.columns; ++c) {
      const Power p=m.power(c,r);
      if(p==Power::None) continue;
      check(m.brick(c,r)==1,"every glowing brick has 1 hit point");
      const bool edge=c<half || r<half || c>=m.columns-half || r>=m.rows-half;
      edgeGlows+=edge;
      if(p!=Power::Bomb) continue;
      ++bombs;
      check(!edge,"no bomb lies within half its blast of a wall");
    }
  }
  check(bombs>1000 && edgeGlows>1000,"enough bombs and glowing bricks by the walls to judge");
  {
    // By the walls Bomb's share goes to the other kinds in their own proportions; inside it keeps its weight.
    Settings s; s.glow=Settings::maxGlow; s.weights={2,1,1,0,0};
    int inner[powerKinds+1]{}, outer[powerKinds+1]{};
    for(std::uint32_t seed=1; seed<=40; ++seed) {
      Model m(seed); m.restart(5,5,s);
      for(int r=0; r<m.rows; ++r) for(int c=0; c<m.columns; ++c) {
        const bool edge=c<2 || r<2 || c>=m.columns-2 || r>=m.rows-2;
        ++(edge ? outer : inner)[static_cast<int>(m.power(c,r))];
      }
    }
    const int innerGlow=inner[1]+inner[2]+inner[3];
    check(outer[1]==0 && outer[2]>0 && outer[3]>0 && std::abs(outer[2]-outer[3])<(outer[2]+outer[3])/8,"by the walls the other kinds split Bomb's share evenly");
    check(std::abs(static_cast<float>(inner[1])/innerGlow-0.5f)<0.03f,"inside, Bomb keeps its weight");
    Settings only; only.glow=Settings::maxGlow; only.weights={1,0,0,0,0};
    Model m(3); m.restart(5,5,only);
    for(int r=0; r<m.rows; ++r) for(int c=0; c<m.columns; ++c) if(c<2 || r<2 || c>=m.columns-2 || r>=m.rows-2)
      check(m.power(c,r)==Power::None,"with only Bomb weighted, bricks by the walls stay plain");
  }
}
// Aiming: a pull within the snap angle of an axis flies exactly along it.
static void snapChecks() {
  constexpr float degree=0.0174532925f;
  const auto pullAt=[&](float degrees, float length=60) { return yy::Vec2{std::cos(degrees*degree)*length,std::sin(degrees*degree)*length}; };
  check(Model::defaultSnapDegrees==5,"the snap angle is 5 degrees by default");
  const auto velocity=[&](Model& m, yy::Vec2 pull) {
    m.balls.clear(); m.ballsLeft=5;
    check(m.launch(pocketCentre(m),pull),"a launch from the pocket");
    return m.balls.back().velocity;
  };
  Model m(51);
  for(float sign: {1.0f,-1.0f}) {
    const yy::Vec2 h=pullAt(4), v{pullAt(4).y*sign,-pullAt(4).x};
    yy::Vec2 f=velocity(m,{h.x*sign,h.y*sign});
    check(f.y==0 && near(f.x,-sign*Model::speed),"a pull 4 degrees off horizontal flies exactly straight");
    f=velocity(m,v);
    check(f.x==0 && near(f.y,Model::speed),"a pull 4 degrees off vertical flies exactly straight");
    const yy::Vec2 six=pullAt(6);
    f=velocity(m,{six.x*sign,six.y*sign});
    check(near(f.y,-sign*Model::speed*std::sin(6*degree),0.1f),"a pull 6 degrees off horizontal keeps its angle");
    f=velocity(m,{six.y,six.x*sign});
    check(near(f.x,-Model::speed*std::sin(6*degree),0.1f),"a pull 6 degrees off vertical keeps its angle");
  }
  const yy::Vec2 four=pullAt(4);
  const yy::Vec2 snapped=tapdemo::snapPull(four,5);
  check(snapped.y==0 && near(snapped.x,60),"snapPull keeps the pull's length");
  m.setSnapDegrees(0);
  const yy::Vec2 f=velocity(m,four);
  check(near(f.x,-Model::speed*std::cos(4*degree)) && near(f.y,-Model::speed*std::sin(4*degree)),"snap 0 changes nothing");
  check(tapdemo::snapPull(four,0).x==four.x && tapdemo::snapPull(four,0).y==four.y,"snapPull with 0 returns the pull");
  m.setSnapDegrees(-3); check(m.snapDegrees==0,"the snap angle clamps up to 0");
  m.setSnapDegrees(90); check(m.snapDegrees==Model::maxSnapDegrees && Model::maxSnapDegrees==15,"the snap angle clamps down to 15");
  m.setSnapDegrees(10); m.restart();
  check(m.snapDegrees==10,"restart keeps the snap angle");
  check(tapdemo::snapPull(pullAt(9),10).y==0 && tapdemo::snapPull(pullAt(11),10).y!=0,"a wider snap angle snaps wider pulls");
  {
    // The player's path: a near-horizontal pull through Touch launches exactly horizontally.
    Model p(52); tapdemo::Touch t(p); t.instructions=false;
    const auto press=t.camera.toScreen(pocketCentre(p));
    const yy::Vec2 pull=pullAt(3,40);
    const yy::Vec2 finger{press.x+pull.x*t.camera.zoom,press.y+pull.y*t.camera.zoom};
    t.down(0,press); t.move(0,finger);
    check(t.aim && t.aim->pull.y!=0,"the aim holds the pull as pulled");
    const yy::Vec2 shown=tapdemo::snapPull(t.aim->pull,static_cast<float>(p.snapDegrees));
    check(shown.y==0 && shown.x>0,"the aim line shows the snapped direction");
    check(t.up(0,finger) && p.balls.size()==1,"release launches");
    check(p.balls[0].velocity.y==0 && near(p.balls[0].velocity.x,-Model::speed),"the ball flies exactly horizontally");
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
    check(!m.launch(centre,{0,40}) && m.balls.size()==1 && m.ballsLeft==Model::defaultBalls-1,"no ball launches while one flies, even from on top of it");
  }
  powerChecks();
  {
    Model m(9); const auto at=pocketCentre(m);
    check(!m.launch(at,{Model::minPull*0.5f,0}) && m.ballsLeft==Model::defaultBalls && m.balls.empty(),"short pull cancels");
    check(m.launch(at,{30,40}) && m.ballsLeft==Model::defaultBalls-1 && m.balls.size()==1,"launch uses a ball");
    const auto v=m.balls[0].velocity;
    check(near(v.x,-0.6f*Model::speed) && near(v.y,-0.8f*Model::speed),"ball flies opposite the pull at fixed speed");
    check(m.balls[0].bounces==Model::defaultBounces,"ball starts with its bounces");
    check(!m.launch(at,{0,-200}) && m.balls.size()==1,"a second launch waits for the first ball");
    m.balls.push_back({at,{0,Model::speed},Model::defaultBounces}); // the model still steps several balls
    check(m.balls.size()==2 && near(m.balls[1].velocity.y,Model::speed),"several balls can fly at once");
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
    check(cam.zoom>=cam.minZoom() && cam.zoom<=cam.minZoom()*tapdemo::Framing::openingScale+0.01f,"opens within the framing zoom clamp");
    t.camera.fit(); // this legacy gesture regression visits both the cavity and distant fog
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

    m.balls.clear(); // one ball at a time: the next press waits for the field to be still
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
// The ten levels: each loads its own table, a retry is the same field shot for shot, and progress
// moves on a win, stays on a loss and survives a save.
static void levelChecks() {
  using tapdemo::levels; using tapdemo::levelCount; using tapdemo::Settings; using tapdemo::powerKinds;
  // A level's state: every cell, power, the goal, the pockets and the balls left.
  const auto snapshot=[](const Model& m) {
    std::vector<int> s(m.bricks.begin(),m.bricks.end());
    for(Power p: m.powers) s.push_back(static_cast<int>(p));
    s.push_back(m.goal); s.push_back(m.ballsLeft); s.push_back(m.columns);
    for(const auto& p: m.pockets) { s.push_back(p.column); s.push_back(p.row); s.push_back(p.columns); s.push_back(p.rows); }
    return s;
  };
  // The same five shots from the first pocket, each flown out; every power-up fired, with
  // Ghost's landing cell, is recorded.
  const auto play=[&](Model& m) {
    std::vector<int> fired;
    const yy::Vec2 at=pocketCentre(m);
    for(yy::Vec2 pull: {yy::Vec2{20,40},{-40,15},{5,-40},{40,-10},{-25,-30}}) {
      if(m.over() || !m.launch(at,pull)) continue;
      for(int t=0; t<60*40 && !m.balls.empty() && !m.over(); ++t) {
        m.update(1.0f/60);
        for(const auto& f: m.hits.fired) { fired.push_back(static_cast<int>(f.power)); fired.push_back(f.cell); fired.push_back(f.to); }
      }
    }
    auto s=snapshot(m); s.insert(s.end(),fired.begin(),fired.end());
    return s;
  };
  Power seen[powerKinds+1]{}; int order=0;
  const Power introductions[]{Power::Bomb,Power::Electricity,Power::Speed,Power::Ping,Power::Ghost};
  int previousColumns=0;
  for(int n=1; n<=levelCount; ++n) {
    const auto& l=levels[n-1];
    Model a(1), b(777);
    a.play(n); b.play(n);
    check(a.level()==n && a.columns==Settings::shapeColumns*l.grid.gridScale && a.rows==Settings::shapeRows*l.grid.gridScale,"a level builds its own grid size");
    check(a.ballCount==l.balls && a.bouncesPerBall==l.bounces && a.ballsLeft==l.balls,"a level brings its balls and bounces");
    check(a.bombSize==l.bombSize && a.electricSeconds==l.electricSeconds && near(a.electricRadius,l.electricRadius) && a.pingRadius==l.pingRadius,"a level brings its power-up tuning");
    check(a.settings.glow==l.grid.glow && a.settings.weights==l.grid.weights,"a level brings its glow share and weights");
    check(a.columns>=previousColumns,"the grids never shrink from one level to the next");
    previousColumns=a.columns;
    check(a.goal>=0 && a.power(a.goal%a.columns,a.goal/a.columns)==Power::None && !a.visible(a.goal%a.columns,a.goal/a.columns),"every level hides a plain goal in the fog");
    for(Power p: a.powers) check(p==Power::None || l.grid.weights[static_cast<int>(p)-1]>0,"a level glows only with its own power-ups");
    check(snapshot(a)==snapshot(b),"a level's field does not depend on what came before it");
    const auto first=play(a);
    a.restart();
    check(a.level()==n && snapshot(a)==snapshot(b),"a retry restores the level's field");
    check(play(a)==first && play(b)==first,"the same shots on a retry break the same bricks, fire the same power-ups and land Ghost in the same place");
    // Each power-up is introduced by the first level that weights it, and alone.
    if(l.introduces!=Power::None) {
      check(order<5 && l.introduces==introductions[order++],"power-ups arrive Bomb, Electricity, Speed, Ping, Ghost");
      check(seen[static_cast<int>(l.introduces)]==Power::None,"a power-up is introduced once");
      for(int k=1; k<=powerKinds; ++k) check((l.grid.weights[k-1]>0)==(k==static_cast<int>(l.introduces)),"a level introducing a power-up weights only that kind");
    }
    for(int k=1; k<=powerKinds; ++k) if(l.grid.weights[k-1]>0) {
      check(seen[k]!=Power::None || l.introduces==static_cast<Power>(k),"no level uses a power-up before it is introduced");
      seen[k]=static_cast<Power>(k);
    }
  }
  check(order==5,"all five power-ups are introduced");
  check(levels[0].grid.gridScale==Settings::minScale,"level 1 is the smallest grid, 12x20");
  {
    // Ghost on level 9: a Ghost brick above the pocket sends the ball to the same cell on every try.
    std::vector<int> landings;
    for(int attempt=0; attempt<3; ++attempt) {
      Model m(attempt+5); m.play(9);
      const auto& p=m.pockets.front();
      const int column=p.column+p.columns/2;
      m.bricks[(p.row-1)*m.columns+column]=1; m.powers[(p.row-1)*m.columns+column]=Power::Ghost; m.refreshFog();
      m.launch({(column+0.5f)*Model::cell,pocketCentre(m).y},{0,40});
      int to=-1;
      for(int t=0; t<120 && to<0; ++t) { m.update(1.0f/60); for(const auto& f: m.hits.fired) if(f.power==Power::Ghost) to=f.to; }
      check(to>=0,"the ball breaks the Ghost brick");
      landings.push_back(to);
    }
    check(landings[0]==landings[1] && landings[1]==landings[2],"Ghost lands in the same cell on every try");
  }
  {
    // Free play keeps today's advancing random state and its own settings.
    Model m(3); m.play(2);
    Settings s; s.gridScale=3;
    m.restart(4,6,s);
    check(m.level()==0 && m.columns==18 && m.ballCount==4,"free play leaves the level and uses the settings it is given");
    const auto once=snapshot(m);
    m.restart();
    check(m.level()==0 && snapshot(m)!=once,"a free-play restart builds a new field");
  }
  for(int n=1; n<=levelCount; ++n) {
    check(tapdemo::nextLevel(n,false)==n,"a loss keeps the level");
    check(tapdemo::nextLevel(n,true)==(n<levelCount ? n+1 : 0),"a win advances, and the last leads to free play");
    check(tapdemo::loadProgress(tapdemo::saveProgress(n))==n,"saved progress round-trips");
  }
  for(const char* bad: {"","level ","level 0","level 11","level x","lvl 4","level -3"}) check(tapdemo::loadProgress(bad)==1,"a missing or damaged save opens level 1");
  check(tapdemo::loadProgress("level 7\n")==7 && tapdemo::saveProgress(7)=="level 7\n","the save is one plain line");
  std::cout<<"Levels: ten tables load, retries repeat shot for shot, progress round-trips\n";
}
static void debugSettingsChecks() {
  using tapdemo::DebugSettings; using tapdemo::Settings;
  const auto same=[](const DebugSettings& a, const DebugSettings& b) {
    return a.pingRadius==b.pingRadius && a.bombSize==b.bombSize && a.electricSeconds==b.electricSeconds && a.electricHalves==b.electricHalves &&
           a.snapDegrees==b.snapDegrees && a.balls==b.balls && a.bounces==b.bounces &&
           a.grid.gridScale==b.grid.gridScale && a.grid.glow==b.grid.glow && a.grid.weights==b.grid.weights && a.glint==b.glint;
  };
  const DebugSettings defaults;
  check(defaults.pingRadius==Model::defaultPingRadius && defaults.bombSize==Model::defaultBombSize && defaults.electricSeconds==Model::defaultElectricSeconds &&
        defaults.electricHalves==static_cast<int>(Model::defaultElectricRadius*2) && defaults.snapDegrees==Model::defaultSnapDegrees &&
        defaults.balls==Model::defaultBalls && defaults.bounces==Model::defaultBounces,"the debug defaults are the model's defaults");
  check(same(tapdemo::loadDebug(tapdemo::saveDebug(defaults)),defaults),"the defaults round-trip");
  DebugSettings every;
  every.pingRadius=17; every.bombSize=9; every.electricSeconds=11; every.electricHalves=9; every.snapDegrees=0; every.balls=33; every.bounces=44;
  every.grid.gridScale=7; every.grid.glow=21; every.grid.weights={0,9,2,5,3}; every.glint=1;
  check(same(tapdemo::loadDebug(tapdemo::saveDebug(every)),every),"every debug setting round-trips");
  check(tapdemo::saveDebug(every).rfind("debug 1\n",0)==0,"the text starts with its version");
  check(tapdemo::loadDebug("debug 1\nglint 0\n").glint==0 && tapdemo::loadDebug("debug 1\nglint 99\n").glint==3 && tapdemo::loadDebug("debug 1\nglint x\n").glint==defaults.glint,"the glint setting loads, clamps and ignores a damaged line");
  check(tapdemo::saveDebug(defaults).find("glint")==std::string::npos,"a default glint adds no line to the save");
  for(const char* bad: {"","debug","debug 2\nballs 5\n","ping 17\nballs 5\n","\x01 garbage\n\n\n","debug 1 \nballs 5\n"})
    check(same(tapdemo::loadDebug(bad),defaults),"a missing, damaged or newer-version save gives the defaults");
  {
    const auto d=tapdemo::loadDebug("debug 1\nballs 12\nbogus 4\nping x\nbounces\nsnap 3 4\nweights 4 x 2\ngrid 5\n");
    check(d.balls==12 && d.grid.gridScale==5 && d.snapDegrees==3,"a partial save keeps what it has");
    check(d.pingRadius==defaults.pingRadius && d.bounces==defaults.bounces,"unreadable or empty lines leave the default");
    check(d.grid.weights==std::array<int,5>{4,1,2,1,1},"a damaged weight leaves its own default");
  }
  {
    const auto d=tapdemo::loadDebug("debug 1\nping 999\nbomb 4\nzapSeconds -5\nzapHalves 99\nsnap 90\nballs 0\nbounces 1000\ngrid 1\nglow 99\nweights -1 99 3 3 3\n");
    check(d.pingRadius==Model::maxPingRadius && d.bombSize==5 && d.electricSeconds==Model::minElectricSeconds,"out-of-range power-up values clamp");
    check(d.electricHalves==static_cast<int>(Model::maxElectricRadius*2) && d.snapDegrees==Model::maxSnapDegrees,"out-of-range reach and snap clamp");
    check(d.balls==1 && d.bounces==Model::maxSetting && d.grid.gridScale==Settings::minScale && d.grid.glow==Settings::maxGlow,"out-of-range round settings clamp");
    check(d.grid.weights[0]==0 && d.grid.weights[1]==Settings::maxWeight,"weights clamp");
  }
  for(const char* scheme: {"0","3","9","-4","x"}) { // a scheme line from an older save is ignored
    const auto d=tapdemo::loadDebug(std::string("debug 1\nscheme ")+scheme+"\nballs 12\nweights 0 4 2 5 3\n");
    check(d.balls==12 && d.grid.weights==std::array<int,5>{0,4,2,5,3} && d.bounces==defaults.bounces,"a saved scheme line leaves every other setting intact");
  }
  check(tapdemo::saveDebug(every).find("scheme")==std::string::npos,"a save has no scheme line");
  check(tapdemo::DebugSettings{}.pace==7 && tapdemo::loadDebug("debug 1\npace 5\n").pace==5 && tapdemo::loadDebug("debug 1\npace 99\n").pace==10 && tapdemo::loadDebug("debug 1\npace 1\n").pace==4 && tapdemo::loadDebug("debug 1\npace x\n").pace==7,"the pace setting loads, clamps and ignores a damaged line");
  { tapdemo::DebugSettings p; p.pace=5; check(tapdemo::loadDebug(tapdemo::saveDebug(p)).pace==5 && tapdemo::saveDebug(tapdemo::DebugSettings{}).find("pace")==std::string::npos,"the pace round-trips and a default save has no pace line"); }
  check(tapdemo::loadDebug("debug 1\nballs 99999999999\n").balls==Model::defaultBalls,"a number too large to read leaves the default");
  std::cout<<"Debug settings: round-trip, defaults and clamping\n";
}
void gardenChecks();
// The cull and the board's clip share one rectangle: every cell touching the play area is in the range.
static void visibleCellChecks() {
  using tapdemo::Camera;
  const int columns=Model::defaultColumns, rows=Model::defaultRows;
  Camera cam; cam.fit();
  auto all=cam.visibleCells(columns,rows,0);
  check(all.c0==0 && all.c1==columns-1 && all.r0==0 && all.r1==rows-1,"the fitted view sees the whole grid");
  cam.hold({0,0},{cam.view.x,cam.view.y},Camera::maxZoom); // the top-left corner, close up
  auto edge=cam.visibleCells(columns,rows,0);
  check(edge.c0==0 && edge.r0==0,"the top-left edge starts at cell 0");
  const auto coverage=[&](const Camera& shaken) {
    const auto seen=shaken.visibleCells(columns,rows);
    for(int row=0; row<rows; ++row) for(int column=0; column<columns; ++column) {
      const auto a=shaken.toScreen({column*Model::cell,row*Model::cell}), z=shaken.toScreen({(column+1)*Model::cell,(row+1)*Model::cell});
      const bool touches=z.x>shaken.view.x && a.x<shaken.view.x+shaken.view.w && z.y>shaken.view.y && a.y<shaken.view.y+shaken.view.h;
      const bool inside=column>=seen.c0 && column<=seen.c1 && row>=seen.r0 && row<=seen.r1;
      check(!touches || inside,"a cell on screen is never culled");
    }
    check(seen.c0>=0 && seen.c1<columns && seen.r0>=0 && seen.r1<rows,"the range stays on the grid");
  };
  // All four corners and the middle, at fitted, intermediate and maximum zoom, throughout an actual shake.
  for(float zoom: {cam.minZoom(),1.6f,Camera::maxZoom}) for(float x: {0.0f,0.5f,1.0f}) for(float y: {0.0f,0.5f,1.0f}) {
    Camera at=cam;
    at.hold({at.world.x*x,at.world.y*y},{at.view.x+at.view.w*x,at.view.y+at.view.h*y},zoom);
    tapdemo::Shake shake; shake.bump(tapdemo::Shake::limit);
    for(int tick=0; tick<20; ++tick) {
      Camera shaken=at; const auto jolt=shake.offset();
      shaken.offset.x+=jolt.x; shaken.offset.y+=jolt.y;
      coverage(shaken); shake.step(1.0f/60);
    }
  }
  const auto far=cam.visibleCells(columns,rows,0);
  check(far.c1<columns-1 && far.r1<rows-1,"cells beyond the far edge are skipped");
  Camera fractional; fractional.view={12,95,192,256}; fractional.zoom=1; fractional.offset={28,111};
  const auto margin=fractional.visibleCells(columns,rows);
  check(margin.c0==0 && margin.c1==6 && margin.r0==0 && margin.r1==8,"negative fractional world coordinates use floor and one-cell margin");
  fractional.offset={-100000,-100000};
  const auto empty=fractional.visibleCells(columns,rows);
  check(empty.c0>empty.c1 && empty.r0>empty.r1,"a view wholly outside the board draws no cells");
  std::cout<<"Visible cells: all corners and centre, fit/intermediate/max zoom, full shake, fractional bounds, far cells culled\n";
}
static void framingChecks() {
  using tapdemo::Camera; using tapdemo::Framing; using tapdemo::Touch;
  const auto contained=[](const Camera& cam, yy::Rect b) {
    const auto a=cam.toScreen({b.x,b.y}), z=cam.toScreen({b.x+b.w,b.y+b.h});
    return a.x>=cam.view.x-.02f && a.y>=cam.view.y-.02f &&
           z.x<=cam.view.x+cam.view.w+.02f && z.y<=cam.view.y+cam.view.h+.02f;
  };
  Model m; m.play(6); Touch t(m); t.instructions=false;
  const auto b=Framing::visibleBounds(m);
  check(near(b.x,96) && near(b.y,192) && near(b.w,288) && near(b.h,288),"level 6 frames its 3x3 pocket, two cells of visible bricks and one margin cell");
  check(near(t.camera.zoom,390.0f/288) && contained(t.camera,b),"level 6 uses the largest zoom fitting its visible box under the header");
  const float opening=t.camera.zoom;
  m.bricks[25*m.columns+15]=0; m.refreshFog();
  const auto expanded=Framing::visibleBounds(m);
  const float target=std::min(t.camera.view.w/expanded.w,t.camera.view.h/expanded.h);
  t.update(1.0f/60);
  check(t.camera.zoom<opening && t.camera.zoom>target,"revealing distant cells eases out without a jump");
  for(int i=0; i<300; ++i) {
    const float previous=t.camera.zoom; t.update(1.0f/60);
    check(t.camera.zoom<=previous,"the automatic zoom only eases outward");
  }
  check(contained(t.camera,expanded),"the eased frame contains all revealed cells");
  m.bricks[25*m.columns+15]=1; m.refreshFog();
  const float widest=t.camera.zoom; t.update(1);
  check(near(t.camera.zoom,widest),"a shrinking visible region never eases inward");
  // Wheel and pinch ownership persists even when new cells reveal, and retry returns it.
  t.zoom({195,462},2);
  const Camera manual=t.camera;
  m.bricks[0]=0; m.refreshFog(); t.update(1);
  check(!t.framing.automatic && near(t.camera.zoom,manual.zoom) && near(t.camera.offset.x,manual.offset.x) && near(t.camera.offset.y,manual.offset.y),"wheel zoom stops all automatic framing");
  m.restart(); t.refit();
  check(t.framing.automatic && near(t.camera.zoom,opening),"retry resets the opening frame and automatic ownership");
  t.down(1,{150,462}); t.down(2,{240,462}); t.move(2,{285,462});
  check(!t.framing.automatic && t.camera.zoom>opening,"pinch takes over the opening frame");
  t.up(2,{285,462}); t.up(1,{150,462});
  const Camera pinched=t.camera; m.bricks[0]=0; m.refreshFog(); t.update(1);
  check(near(t.camera.zoom,pinched.zoom) && near(t.camera.offset.y,pinched.offset.y),"pinch ownership remains after both fingers lift");
  // Tiny cavity: the cap is relative to the original fitted cell, not absolute zoom.
  std::fill(m.bricks.begin(),m.bricks.end(),1); m.bricks[15*m.columns+9]=0; m.refreshFog(); t.refit();
  check(near(t.camera.zoom,t.camera.minZoom()*2.5f),"a small cavity stops at 2.5 times the original cell size");
  m.bricks.assign(m.bricks.size(),1); m.bricks[0]=0; m.refreshFog(); t.refit();
  const auto edge=Framing::visibleBounds(m);
  check(edge.x==0 && edge.y==0 && contained(t.camera,edge),"margin clips to grid edges and the frame stays within the grid");
  check(near(t.camera.offset.x,0) && near(t.camera.offset.y,t.camera.view.y),"an edge cavity does not pan beyond the grid");
  // Physical safe area -> logical viewport -> camera -> world placement and sling.
  m.play(6); t.refit(); t.instructions=false;
  const auto anchor=pocketCentre(m), press=t.camera.toScreen(anchor);
  yy::Viewport viewport{{12,44,780,1688}}; yy::PointerTracker pointers;
  const auto window=[&](yy::Vec2 p) { return yy::Vec2{12+p.x*2,44+p.y*2}; };
  auto down=pointers.down(71,window(press),viewport); check(down.has_value(),"zoomed press maps through the safe area");
  t.down(down->id,down->position);
  check(t.aim && near(t.aim->anchor.x,anchor.x) && near(t.aim->anchor.y,anchor.y),"zoomed touch holds the ball at the exact world spot");
  m.bricks[0]=0; m.refreshFog(); t.update(.5f);
  const auto held=t.camera.toScreen(anchor);
  check(near(held.x,press.x) && near(held.y,press.y),"revealed-area framing cannot move the anchor beneath a held finger");
  const auto release=t.camera.toScreen({anchor.x,anchor.y+60});
  auto move=pointers.move(71,window(release),viewport); t.move(move->id,move->position);
  check(t.aim && near(t.aim->pull.x,0) && near(t.aim->pull.y,60),"zoomed sling drag maps to its world distance");
  auto up=pointers.up(71,window(release),viewport);
  check(t.up(up->id,up->position) && m.balls.size()==1 && near(m.balls[0].position.x,anchor.x) && near(m.balls[0].position.y,anchor.y) && near(m.balls[0].velocity.x,0) && near(m.balls[0].velocity.y,-Model::speed),"zoomed release fires from the intended world spot opposite the drag");
  const int points=hitPoints(m);
  for(int i=0; i<120 && hitPoints(m)==points; ++i) m.update(1.0f/60);
  check(hitPoints(m)<points,"the zoomed touch shot hits the brick above the cavity");
  for(int level=1; level<=tapdemo::levelCount; ++level) {
    m.play(level); t.refit();
    check(contained(t.camera,Framing::visibleBounds(m)),"every opening fits its visible area");
    std::cout<<"Opening level "<<level<<": cell "<<Model::cell*t.camera.zoom<<", original "<<Model::cell*t.camera.minZoom()<<" logical pixels\n";
  }
  std::cout<<"Framing: known box, margin, clamp, outward easing, manual ownership, retry and zoomed viewport touch path\n";
}
static void shotCameraChecks() {
  using tapdemo::Camera; using tapdemo::Touch;
  const auto same=[](const Camera& a,const Camera& b) {
    return a.zoom==b.zoom && a.offset.x==b.offset.x && a.offset.y==b.offset.y;
  };
  const auto inView=[](const Camera& cam,const Model& m) {
    for(const auto& b: m.balls) {
      const auto lo=cam.toScreen({b.position.x-Model::cell,b.position.y-Model::cell});
      const auto hi=cam.toScreen({b.position.x+Model::cell,b.position.y+Model::cell});
      if(lo.x<cam.view.x-.03f || lo.y<cam.view.y-.03f || hi.x>cam.view.x+cam.view.w+.03f || hi.y>cam.view.y+cam.view.h+.03f) return false;
    }
    return true;
  };
  Model m; m.play(6); Touch t(m); t.instructions=false;
  // The player's two-finger pinch out, then hold / cancel / launch.
  t.down(1,{100,462}); t.down(2,{290,462}); t.move(2,{140,462});
  t.up(2,{140,462}); t.up(1,{100,462});
  const Camera prior=t.camera;
  const auto anchor=pocketCentre(m), press=t.camera.toScreen(anchor);
  check(prior.zoom<t.camera.minZoom()*tapdemo::Framing::openingScale,"touch pinch reaches a wide aiming view");
  t.down(3,press); t.update(1.0f/60);
  check(t.aim && t.camera.zoom>prior.zoom && t.camera.zoom<t.camera.minZoom()*tapdemo::Framing::openingScale,"holding eases into the opening aiming zoom");
  for(int i=0; i<30; ++i) {
    t.update(1.0f/60); const auto at=t.camera.toScreen(anchor);
    check(near(at.x,press.x,.001f) && near(at.y,press.y,.001f),"aim zoom keeps the ball under the pressed finger every frame");
    check(near(t.aim->pull.x,0) && near(t.aim->pull.y,0),"stationary finger never gains pull from the aim zoom");
  }
  check(near(t.camera.zoom,t.camera.minZoom()*tapdemo::Framing::openingScale),"aim reaches the comfortable opening scale");
  check(!t.up(3,press),"release without pull cancels the zoomed aim");
  t.update(.1f); check(!same(t.camera,prior),"cancel restores through an ease, not a cut");
  t.update(.2f); check(same(t.camera,prior),"cancel restores the exact prior zoom and offsets");
  t.update(1); check(same(t.camera,prior),"cancelled manual view stays restored");
  t.down(4,press); t.update(.3f); t.down(5,{press.x+40,press.y});
  check(!t.aim,"second finger cancels an aim");
  t.update(.3f); check(same(t.camera,prior),"second finger eases back to the exact prior camera");
  t.move(5,{press.x+60,press.y});
  check(t.camera.zoom>prior.zoom,"pinch rebases on the restored camera and remains usable");
  t.up(5,{press.x+60,press.y}); t.up(4,press);
  t.camera=prior;
  t.down(6,press); t.update(.1f); m.pause(true); t.cancel(); t.update(.3f);
  check(same(t.camera,prior) && !t.aim,"pause cancellation restores even partway through zoom-in");
  m.pause(false); t.up(6,press);
  t.down(7,press); t.update(.3f);
  const auto release=t.camera.toScreen({anchor.x,anchor.y+60});
  t.move(7,release);
  check(near(t.aim->pull.y,60),"zoomed aim strength stays in world units");
  const float aimed=t.camera.zoom;
  check(t.up(7,release) && t.camera.zoom==aimed,"launch keeps the aiming zoom");
  t.update(.016f,false); check(t.camera.zoom==aimed,"celebration ownership bypasses camera following");
  t.update(.016f); check(t.camera.zoom==aimed,"following a stationary launch preserves the aiming zoom");
  m.balls.clear();
  // An edge hold needs a temporarily unclamped offset to preserve the finger.
  std::fill(m.bricks.begin(),m.bricks.end(),0); m.goal=-1; m.refreshFog(); t.refit(); t.camera.fit(); t.framing.automatic=false;
  const Camera edgePrior=t.camera; const auto edge=t.camera.toScreen({16,16});
  t.down(8,edge); t.update(.3f);
  check(t.aim && near(t.camera.toScreen(t.aim->anchor).x,edge.x) && near(t.camera.toScreen(t.aim->anchor).y,edge.y),"grid-edge aim remains fixed beneath the finger");
  t.up(8,edge); t.update(.3f); check(same(t.camera,edgePrior),"grid-edge cancel restores the original camera exactly");
  // Real one-finger manual pan, followed by a moving shot and a second live ball.
  m.bricks[0]=1; m.refreshFog(); t.refit(); t.instructions=false;
  t.camera.hold({384,640},{195,462},1.3f);
  const auto solid=t.camera.toWorld({60,200});
  const int solidColumn=static_cast<int>(solid.x/Model::cell), solidRow=static_cast<int>(solid.y/Model::cell);
  for(int r=solidRow-2; r<=solidRow+2; ++r) for(int c=solidColumn-2; c<=solidColumn+2; ++c)
    m.bricks[r*m.columns+c]=3;
  m.refreshFog();
  t.down(9,{60,200}); t.move(9,{100,220}); t.up(9,{100,220});
  check(!t.framing.automatic,"manual touch pan takes ownership before the shot");
  const Camera panned=t.camera;
  check(m.launch({384,640},{-40,0}),"a ball launches after the pan");
  m.balls.push_back({{384,640},{Model::speed,0},m.bouncesPerBall}); // and one flying the other way
  check(m.balls.size()==2,"opposing live balls fly");
  for(int i=0; i<100; ++i) { m.update(1.0f/60); t.update(1.0f/60); check(inView(t.camera,m),"every live ball stays inside the play area with a margin after a manual pan"); }
  check(!same(t.camera,panned),"shot follow still moves the manually panned camera");
  t.zoom({195,462},-5); const float widened=t.camera.zoom;
  t.update(1.0f/60);
  check(t.camera.zoom<=widened && inView(t.camera,m),"manual zoom-out during a shot keeps its zoom and live-ball following");
  m.balls.clear(); const Camera ended=t.camera; t.update(1);
  check(same(t.camera,ended),"the camera stays exactly where the shot ended");
  // Production Ghost activation, deterministic far landing, no camera cut.
  Model ghost(38); ghost.restart(5,99);
  std::fill(ghost.bricks.begin(),ghost.bricks.end(),2); std::fill(ghost.powers.begin(),ghost.powers.end(),Power::None); ghost.goal=-1;
  for(int r=6; r<=21; ++r) ghost.bricks[r*ghost.columns+12]=0;
  ghost.bricks[5*ghost.columns+12]=1; ghost.powers[5*ghost.columns+12]=Power::Ghost; ghost.refreshFog();
  Model automaticGhost=ghost;
  Touch follow(ghost); follow.instructions=false; follow.framing.automatic=false;
  const yy::Vec2 start{12.5f*Model::cell,20.5f*Model::cell};
  follow.camera.hold(start,{195,462},1.3f);
  const auto gp=follow.camera.toScreen(start); follow.down(10,gp);
  check(follow.up(10,{gp.x,gp.y+40*follow.camera.zoom}),"touch launches at the real Ghost fixture");
  bool arrived=false; float beforeDistance=0;
  for(int i=0; i<150 && !arrived; ++i) {
    ghost.update(1.0f/60); const Camera before=follow.camera;
    follow.update(1.0f/60); check(inView(follow.camera,ghost),"Ghost shot never loses a live ball, including its arrival frame");
    if(firedCount(ghost.hits,Power::Ghost)>0) {
      arrived=true;
      const auto& f=ghost.hits.fired.front();
      check(f.to>=0,"real Ghost reports the arrival cell");
      const auto ball=ghost.balls.front().position, oldCentre=before.toWorld({195,462}), centre=follow.camera.toWorld({195,462});
      beforeDistance=std::hypot(oldCentre.x-ball.x,oldCentre.y-ball.y);
      const float afterDistance=std::hypot(centre.x-ball.x,centre.y-ball.y);
      check(beforeDistance>Model::cell*3 && afterDistance>Model::cell,"Ghost landing uses a visible swing instead of a cut");
      check(afterDistance<beforeDistance,"Ghost camera starts approaching the arrival immediately");
    }
  }
  check(arrived,"Ghost camera fixture reaches its far landing");
  for(int i=0; i<12; ++i) { ghost.update(1.0f/60); follow.update(1.0f/60); check(inView(follow.camera,ghost),"Ghost ease keeps the arriving ball in view"); }
  const auto centre=follow.camera.toWorld({195,462}), ball=ghost.balls.front().position;
  check(std::hypot(centre.x-ball.x,centre.y-ball.y)<beforeDistance*.4f,"Ghost follow reaches the far side within 200 ms");
  Touch reveal(automaticGhost); const float initial=reveal.camera.zoom;
  check(launchUp(automaticGhost),"automatic Ghost framing fixture launches");
  bool widenedGhost=false;
  for(int i=0; i<150 && !widenedGhost; ++i) {
    automaticGhost.update(1.0f/60); reveal.update(1.0f/60);
    check(inView(reveal.camera,automaticGhost),"automatic reveal framing also keeps the Ghost arrival visible");
    if(firedCount(automaticGhost.hits,Power::Ghost)>0) {
      widenedGhost=true; const int cell=automaticGhost.hits.fired.front().to;
      const yy::Vec2 arrival{(cell%automaticGhost.columns+.5f)*Model::cell,(cell/automaticGhost.columns+.5f)*Model::cell};
      check(arrival.x>=reveal.framing.bounds.x && arrival.y>=reveal.framing.bounds.y &&
            arrival.x<=reveal.framing.bounds.x+reveal.framing.bounds.w && arrival.y<=reveal.framing.bounds.y+reveal.framing.bounds.h &&
            reveal.camera.zoom<initial,"Ghost landing joins accumulated revealed bounds and widens the automatic view");
    }
  }
  check(widenedGhost,"automatic framing fixture fires its Ghost");
  std::cout<<"Shot camera: touch pinch/hold/cancel/launch, exact restore, edge anchor, manual-pan multi-ball follow, real Ghost visible and reached within 200 ms\n";
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
  framingChecks(); visibleCellChecks();
  framingGameChecks();
  boardRenderingChecks();
  shotCameraChecks();
  settingsChecks();
  glowChecks();
  snapChecks();
  levelChecks();
  debugSettingsChecks();
  paletteChecks();
  gardenChecks();
  levelFlowChecks();
  glintChecks();
  debugPersistenceChecks();
  levelBandChecks();
  celebrationChecks();
  juiceChecks();
  paceChecks();
  fontChecks();
  std::cout<<"Engine and TapDemo checks passed\n";
}
