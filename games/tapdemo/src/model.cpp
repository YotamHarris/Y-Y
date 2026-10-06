#include <tapdemo/model.hpp>
#include <algorithm>
#include <cmath>
#include <charconv>
#include <deque>

namespace tapdemo {
float Model::random() {
  randomState ^= randomState << 13; randomState ^= randomState >> 17; randomState ^= randomState << 5;
  return static_cast<float>(randomState & 0xffff) / 65536.0f;
}
Settings Settings::clamped() const {
  Settings s=*this;
  s.gridScale=std::clamp(gridScale,minScale,maxScale);
  s.glow=std::clamp(glow,0,maxGlow);
  for(int& w: s.weights) w=std::clamp(w,0,maxWeight);
  return s;
}
yy::Vec2 snapPull(yy::Vec2 pull, float degrees) {
  const float length=std::hypot(pull.x,pull.y);
  if(!(degrees>0) || !(length>0)) return pull;
  // Within `degrees` of an axis means the other component is at most sin(degrees) of the pull.
  const float limit=length*std::sin(degrees*0.0174532925f);
  if(std::abs(pull.y)<=limit) return {std::copysign(length,pull.x),0};
  if(std::abs(pull.x)<=limit) return {0,std::copysign(length,pull.y)};
  return pull;
}
Model::Model(std::uint32_t seed): randomState(seed ? seed : 42) { restart(); }

// Bomb, Electricity, Speed, Ping, then Ghost, each brought in alone and then stacked. The seeds
// are hand-picked placeholders that a random player wins (one ball at a time, from a random open
// cell in a random direction: 99, 66, 86, 19, 64, 100, 78, 59, 14 and 99% of 200 tries); task T11
// tunes them and the numbers.
// Weights run Bomb, Electricity, Ping, Ghost, Speed.
const std::array<Level,levelCount> levels{{
  {.seed=901188, .feel=Feel::Relief, .introduces=Power::Bomb, .grid={.gridScale=2,.glow=16,.weights={1,0,0,0,0}},
   .balls=8, .bounces=15, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=6},
  {.seed=201838, .feel=Feel::BuildUp, .introduces=Power::None, .grid={.gridScale=2,.glow=24,.weights={1,0,0,0,0}},
   .balls=5, .bounces=12, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=6},
  {.seed=979312, .feel=Feel::Relief, .introduces=Power::Electricity, .grid={.gridScale=2,.glow=16,.weights={0,1,0,0,0}},
   .balls=8, .bounces=15, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=6},
  {.seed=201838, .feel=Feel::Fu, .introduces=Power::None, .grid={.gridScale=3,.glow=8,.weights={0,1,0,0,0}},
   .balls=5, .bounces=10, .bombSize=5, .electricSeconds=5, .electricRadius=2, .pingRadius=6},
  {.seed=901188, .feel=Feel::BuildUp, .introduces=Power::Speed, .grid={.gridScale=3,.glow=16,.weights={0,0,0,0,1}},
   .balls=7, .bounces=15, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=6},
  {.seed=328984, .feel=Feel::FuckYeah, .introduces=Power::None, .grid={.gridScale=3,.glow=30,.weights={1,1,0,0,0}},
   .balls=8, .bounces=15, .bombSize=7, .electricSeconds=8, .electricRadius=3, .pingRadius=6},
  {.seed=901188, .feel=Feel::Relief, .introduces=Power::Ping, .grid={.gridScale=3,.glow=16,.weights={0,0,1,0,0}},
   .balls=8, .bounces=15, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=7},
  {.seed=604740, .feel=Feel::BuildUp, .introduces=Power::None, .grid={.gridScale=4,.glow=16,.weights={1,0,1,0,0}},
   .balls=7, .bounces=15, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=6},
  {.seed=176941, .feel=Feel::Fu, .introduces=Power::Ghost, .grid={.gridScale=4,.glow=6,.weights={0,0,0,1,0}},
   .balls=5, .bounces=12, .bombSize=5, .electricSeconds=6, .electricRadius=2.5f, .pingRadius=6},
  {.seed=103022, .feel=Feel::FuckYeah, .introduces=Power::None, .grid={.gridScale=5,.glow=30,.weights={1,1,1,1,1}},
   .balls=10, .bounces=15, .bombSize=7, .electricSeconds=8, .electricRadius=3, .pingRadius=8},
}};
std::string saveProgress(int level) { return "level "+std::to_string(std::clamp(level,1,levelCount))+'\n'; }
int loadProgress(std::string_view text) {
  constexpr std::string_view key="level ";
  if(text.substr(0,key.size())!=key) return 1;
  int level=0;
  const char* first=text.data()+key.size();
  const auto [end,error]=std::from_chars(first,text.data()+text.size(),level);
  return error==std::errc{} && end!=first && level>=1 && level<=levelCount ? level : 1;
}

void Model::generate() {
  bricks.assign(columns*rows, 0);
  for(auto& b: bricks) { const float r=random(); b = r<0.5f ? 1 : r<0.8f ? 2 : 3; }
  // One or two pockets of 3 to 5 cells a side, a cell in from the walls and apart from each other.
  pockets.clear();
  const int count = random()<0.5f ? 1 : 2;
  while(static_cast<int>(pockets.size())<count) {
    Pocket p;
    p.columns=3+static_cast<int>(random()*3); p.rows=3+static_cast<int>(random()*3);
    p.column=1+static_cast<int>(random()*(columns-1-p.columns)); p.row=1+static_cast<int>(random()*(rows-1-p.rows));
    const bool apart=std::all_of(pockets.begin(),pockets.end(),[&](const Pocket& o) {
      return p.column>o.column+o.columns || o.column>p.column+p.columns || p.row>o.row+o.rows || o.row>p.row+p.rows;
    });
    if(!apart) continue;
    pockets.push_back(p);
    for(int r=p.row; r<p.row+p.rows; ++r) for(int c=p.column; c<p.column+p.columns; ++c) bricks[r*columns+c]=0;
  }
  // Glowing bricks, each power-up picked in proportion to its weight; all weights 0 means none glow.
  // Where a bomb's blast would pass a wall, the pick is among the other kinds, so Bomb's weight
  // leaves their proportions alone; with only Bomb weighted that brick stays plain.
  // A glowing brick breaks in one hit.
  powers.assign(columns*rows, Power::None);
  int total=0;
  for(int w: settings.weights) total+=w;
  const float glowChance=settings.glow/200.0f;
  const int half=bombSize/2;
  if(total>0) for(std::size_t i=0; i<bricks.size(); ++i) {
    if(bricks[i]<=0 || !(random()<glowChance)) continue;
    const int c=static_cast<int>(i)%columns, r=static_cast<int>(i)/columns;
    const bool bombFits=c>=half && r>=half && c<columns-half && r<rows-half;
    auto weights=settings.weights;
    if(!bombFits) weights[0]=0;
    const int sum=total-settings.weights[0]+weights[0];
    const float pick=random()*sum;
    if(sum<=0) continue;
    int kind=0;
    for(int s=weights[0]; kind<powerKinds-1 && !(pick<s); s+=weights[++kind]) {}
    powers[i]=static_cast<Power>(1+kind);
    bricks[i]=1;
  }
  refreshFog();
  // The goal: a plain brick under the fog (any plain brick if none is fogged).
  std::vector<int> hidden, plain;
  for(int i=0; i<columns*rows; ++i) if(bricks[i]>0 && powers[i]==Power::None) { plain.push_back(i); if(fog_[i]>fogReach) hidden.push_back(i); }
  if(hidden.empty()) hidden=plain;
  goal=hidden.empty() ? -1 : hidden[std::min(hidden.size()-1,static_cast<std::size_t>(random()*hidden.size()))];
  goalBroken_=false;
}
void Model::restart() {
  if(level_>0) play(level_);
  else restart(ballCount,bouncesPerBall);
}
void Model::play(int level) {
  level=std::clamp(level,1,levelCount);
  const Level& l=levels[level-1];
  randomState=l.seed ? l.seed : 42;
  setBombSize(l.bombSize); setElectricSeconds(l.electricSeconds); setElectricRadius(l.electricRadius); setPingRadius(l.pingRadius);
  restart(l.balls,l.bounces,l.grid);
  level_=level;
}
void Model::restart(int balls_, int bounces, const Settings& grid) {
  level_=0;
  settings=grid.clamped();
  columns=Settings::shapeColumns*settings.gridScale; rows=Settings::shapeRows*settings.gridScale;
  ballCount=std::clamp(balls_,1,maxSetting); bouncesPerBall=std::clamp(bounces,1,maxSetting);
  ballsLeft=ballCount; balls.clear(); hits={}; pending.clear(); pingCells_.clear(); pingTime=0; paused_=false;
  generate();
}
int Model::brick(int column, int row) const {
  if(column<0 || row<0 || column>=columns || row>=rows) return 0;
  return bricks[row*columns+column];
}
Power Model::power(int column, int row) const {
  if(column<0 || row<0 || column>=columns || row>=rows) return Power::None;
  return powers[row*columns+column];
}
bool Model::pinged(int column, int row) const {
  if(pingTime<=0) return false;
  return std::any_of(pingCells_.begin(),pingCells_.end(),[&](int cell) {
    const int dc=cell%columns-column, dr=cell/columns-row;
    return dc*dc+dr*dr<=pingRadius*pingRadius;
  });
}
int Model::fogDistance(int column, int row) const {
  if(column<0 || row<0 || column>=columns || row>=rows) return 0;
  return fog_[row*columns+column];
}
// A breadth-first walk out from every empty cell, in straight steps inside the grid.
void Model::refreshFog() {
  const int far=columns+rows;
  fog_.assign(columns*rows, far);
  std::deque<int> queue;
  for(int i=0; i<columns*rows; ++i) if(bricks[i]<=0) { fog_[i]=0; queue.push_back(i); }
  while(!queue.empty()) {
    const int i=queue.front(); queue.pop_front();
    const int c=i%columns, r=i/columns;
    const int next[4][2]{{c-1,r},{c+1,r},{c,r-1},{c,r+1}};
    for(const auto& n: next) {
      const int j=n[1]*columns+n[0];
      if(n[0]<0 || n[1]<0 || n[0]>=columns || n[1]>=rows || fog_[j]<=fog_[i]+1) continue;
      fog_[j]=fog_[i]+1; queue.push_back(j);
    }
  }
}
int Model::bricksLeft() const {
  return static_cast<int>(std::count_if(bricks.begin(),bricks.end(),[](int b){ return b>0; }));
}
// The brick the ball circle at p overlaps whose centre is nearest, or -1.
int Model::brickIndexHit(yy::Vec2 p) const {
  const int c0=std::max(0,static_cast<int>(std::floor((p.x-ballRadius)/cell))), c1=std::min(columns-1,static_cast<int>(std::floor((p.x+ballRadius)/cell)));
  const int r0=std::max(0,static_cast<int>(std::floor((p.y-ballRadius)/cell))), r1=std::min(rows-1,static_cast<int>(std::floor((p.y+ballRadius)/cell)));
  int best=-1; float bestDistance=0;
  for(int r=r0; r<=r1; ++r) for(int c=c0; c<=c1; ++c) {
    if(bricks[r*columns+c]<=0) continue;
    const float dx=p.x-std::clamp(p.x,c*cell,(c+1)*cell), dy=p.y-std::clamp(p.y,r*cell,(r+1)*cell);
    if(dx*dx+dy*dy>=ballRadius*ballRadius) continue;
    const float cx=p.x-(c+0.5f)*cell, cy=p.y-(r+0.5f)*cell, distance=cx*cx+cy*cy;
    if(best<0 || distance<bestDistance) { best=r*columns+c; bestDistance=distance; }
  }
  return best;
}
bool Model::open(yy::Vec2 p) const {
  if(!std::isfinite(p.x) || !std::isfinite(p.y)) return false;
  if(p.x<ballRadius || p.y<ballRadius || p.x>width()-ballRadius || p.y>height()-ballRadius) return false;
  return brickIndexHit(p)<0;
}
// Rings of growing radius around the tap; the first open sample is the closest, and the
// angular order (from the +x direction, clockwise on screen) settles ties.
std::optional<yy::Vec2> Model::placeNear(yy::Vec2 tap) const {
  if(canPlace(tap)) return tap;
  constexpr float ringStep=0.25f;
  constexpr float twoPi=6.28318530718f;
  const int rings=static_cast<int>(cell/ringStep);
  for(int k=1; k<=rings; ++k) {
    const float radius=k*ringStep;
    const int steps=std::max(16,static_cast<int>(std::ceil(twoPi*radius/ringStep)));
    for(int s=0; s<steps; ++s) {
      const float angle=twoPi*s/steps;
      const yy::Vec2 candidate{tap.x+radius*std::cos(angle), tap.y+radius*std::sin(angle)};
      if(open(candidate)) return candidate;
    }
  }
  return std::nullopt;
}
// Takes hit points off a brick; a glowing brick that breaks queues its power-up for fire().
void Model::damage(int index, int points) {
  if(bricks[index]<=0) return;
  bricks[index]=std::max(0,bricks[index]-points);
  if(bricks[index]>0) return;
  ++hits.bricksBroken;
  if(index==goal) { goalBroken_=true; hits.goalBroken=true; }
  if(powers[index]!=Power::None) { pending.push_back({powers[index],index}); powers[index]=Power::None; }
}
// Fires every queued power-up for the ball whose hit broke its brick, including the ones
// those power-ups break in turn. Each brick breaks once, so a chain ends.
void Model::fire(Ball& ball) {
  for(std::size_t i=0; i<pending.size(); ++i) {
    Fired f=pending[i];
    const int column=f.cell%columns, row=f.cell/columns;
    switch(f.power) {
    case Power::Bomb:
      for(int r=row-bombSize/2; r<=row+bombSize/2; ++r) for(int c=column-bombSize/2; c<=column+bombSize/2; ++c)
        if(brick(c,r)>0) damage(r*columns+c,bricks[r*columns+c]);
      break;
    case Power::Electricity:
      if(ball.electric<=0) ball.zapTimer=electricTick;
      ball.electric=static_cast<float>(electricSeconds);
      break;
    case Power::Ping: pingTime=pingSeconds; pingCells_.push_back(f.cell); break;
    case Power::Ghost: ghost(ball,f); break;
    case Power::Speed:
      if(!ball.fast) { ball.velocity={ball.velocity.x*speedUp,ball.velocity.y*speedUp}; ball.fast=true; }
      break;
    case Power::None: break;
    }
    hits.fired.push_back(f);
  }
  pending.clear();
}
// Moves the ball, velocity and bounces kept, to the centre of a random brick deep in the
// field (under fog when any is) and clears the ghostSize square there into a new cavity.
void Model::ghost(Ball& ball, Fired& fired) {
  refreshFog();
  constexpr int half=ghostSize/2;
  std::vector<int> hidden, any;
  for(int r=half; r<rows-half; ++r) for(int c=half; c<columns-half; ++c) {
    if(bricks[r*columns+c]<=0) continue;
    any.push_back(r*columns+c);
    if(!visible(c,r)) hidden.push_back(r*columns+c);
  }
  const auto& from=hidden.empty() ? any : hidden;
  if(from.empty()) return;
  const int target=from[std::min(from.size()-1,static_cast<std::size_t>(random()*from.size()))];
  const int column=target%columns, row=target/columns;
  for(int r=row-half; r<=row+half; ++r) for(int c=column-half; c<=column+half; ++c) damage(r*columns+c,bricks[r*columns+c]);
  ball.position={(column+0.5f)*cell,(row+0.5f)*cell};
  fired.to=target;
}
// One electric pulse: a hit point off every brick whose centre is within electricRadius cells.
void Model::zap(const Ball& ball) {
  const float reach=electricRadius*cell;
  const int c0=std::max(0,static_cast<int>(std::floor((ball.position.x-reach)/cell))), c1=std::min(columns-1,static_cast<int>(std::floor((ball.position.x+reach)/cell)));
  const int r0=std::max(0,static_cast<int>(std::floor((ball.position.y-reach)/cell))), r1=std::min(rows-1,static_cast<int>(std::floor((ball.position.y+reach)/cell)));
  for(int r=r0; r<=r1; ++r) for(int c=c0; c<=c1; ++c) {
    const float dx=ball.position.x-(c+0.5f)*cell, dy=ball.position.y-(r+0.5f)*cell;
    if(bricks[r*columns+c]<=0 || dx*dx+dy*dy>reach*reach) continue;
    ++hits.bricksHit; damage(r*columns+c,1);
  }
}
bool Model::launch(yy::Vec2 at, yy::Vec2 pull) {
  pull=snapPull(pull,static_cast<float>(snapDegrees));
  const float length=std::hypot(pull.x,pull.y);
  if(paused_ || over() || ballsLeft<=0 || !(length>=minPull) || !open(at)) return false;
  balls.push_back({at,{-pull.x/length*speed,-pull.y/length*speed},bouncesPerBall});
  --ballsLeft;
  return true;
}
void Model::update(float dt) {
  hits={};
  if(paused_ || over() || !std::isfinite(dt) || dt<=0) return;
  pingTime=std::max(0.0f,pingTime-dt);
  if(pingTime<=0) pingCells_.clear();
  // Sub-steps of at most a few units for a sped-up ball keep any ball from passing a cell corner.
  const int steps=std::max(1,static_cast<int>(std::ceil(speed*speedUp*dt/4)));
  const float h=dt/steps;
  for(std::size_t i=0; i<balls.size(); ) {
    Ball& b=balls[i]; bool spent=false;
    for(int s=0; s<steps && !spent; ++s) {
      int struck[2]{-1,-1}; bool bounced=false;
      // Each axis moves alone; a blocked axis steps back and reverses.
      b.position.x+=b.velocity.x*h;
      if(b.position.x<ballRadius || b.position.x>width()-ballRadius || (struck[0]=brickIndexHit(b.position))>=0) {
        b.position.x-=b.velocity.x*h; b.velocity.x=-b.velocity.x; bounced=true;
      }
      b.position.y+=b.velocity.y*h;
      if(b.position.y<ballRadius || b.position.y>height()-ballRadius || (struck[1]=brickIndexHit(b.position))>=0) {
        b.position.y-=b.velocity.y*h; b.velocity.y=-b.velocity.y; bounced=true;
      }
      // Electric zaps cost no bounces.
      if(b.electric>0) {
        b.zapTimer-=h;
        if(b.zapTimer<=1e-5f) { b.zapTimer+=electricTick; zap(b); fire(b); }
        b.electric=std::max(0.0f,b.electric-h);
      }
      if(!bounced) continue;
      if(struck[1]==struck[0]) struck[1]=-1;
      for(int index: struck) if(index>=0) { ++hits.bricksHit; damage(index,1); }
      fire(b);
      ++hits.bounces;
      spent=--b.bounces<=0;
    }
    if(spent) { ++hits.ballsSpent; balls.erase(balls.begin()+static_cast<std::ptrdiff_t>(i)); }
    else ++i;
  }
  if(hits.bricksBroken>0) refreshFog();
}
}
