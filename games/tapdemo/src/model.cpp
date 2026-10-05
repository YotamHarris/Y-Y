#include <tapdemo/model.hpp>
#include <algorithm>
#include <cmath>

namespace tapdemo {
float Model::random() {
  randomState ^= randomState << 13; randomState ^= randomState >> 17; randomState ^= randomState << 5;
  return static_cast<float>(randomState & 0xffff) / 65536.0f;
}
Model::Model(std::uint32_t seed): randomState(seed ? seed : 42) { restart(); }

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
}
void Model::restart(int balls_, int bounces) {
  ballCount=std::clamp(balls_,1,maxSetting); bouncesPerBall=std::clamp(bounces,1,maxSetting);
  ballsLeft=ballCount; balls.clear(); hits={}; paused_=false;
  generate();
}
int Model::brick(int column, int row) const {
  if(column<0 || row<0 || column>=columns || row>=rows) return 0;
  return bricks[row*columns+column];
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
bool Model::open(yy::Vec2 p, bool checkBalls) const {
  if(!std::isfinite(p.x) || !std::isfinite(p.y)) return false;
  if(p.x<ballRadius || p.y<ballRadius || p.x>width()-ballRadius || p.y>height()-ballRadius) return false;
  if(brickIndexHit(p)>=0) return false;
  if(checkBalls) for(const auto& b: balls) {
    const float dx=p.x-b.position.x, dy=p.y-b.position.y;
    if(dx*dx+dy*dy<4*ballRadius*ballRadius) return false;
  }
  return true;
}
bool Model::launch(yy::Vec2 at, yy::Vec2 pull) {
  const float length=std::hypot(pull.x,pull.y);
  // Flying balls may have crossed the spot since it was chosen; balls pass through each other.
  if(paused_ || over() || ballsLeft<=0 || !(length>=minPull) || !open(at,false)) return false;
  balls.push_back({at,{-pull.x/length*speed,-pull.y/length*speed},bouncesPerBall});
  --ballsLeft;
  return true;
}
void Model::update(float dt) {
  hits={};
  if(paused_ || over() || !std::isfinite(dt) || dt<=0) return;
  // Sub-steps of at most a few units keep a ball from passing a cell corner.
  const int steps=std::max(1,static_cast<int>(std::ceil(speed*dt/4)));
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
      if(!bounced) continue;
      if(struck[1]==struck[0]) struck[1]=-1;
      for(int index: struck) if(index>=0) { ++hits.bricksHit; if(--bricks[index]==0) ++hits.bricksBroken; }
      ++hits.bounces;
      spent=--b.bounces<=0;
    }
    if(spent) { ++hits.ballsSpent; balls.erase(balls.begin()+static_cast<std::ptrdiff_t>(i)); }
    else ++i;
  }
}
}
