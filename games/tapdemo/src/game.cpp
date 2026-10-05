#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>

namespace tapdemo {
namespace {
bool inside(yy::Rect r, yy::Vec2 p) { return p.x>=r.x && p.y>=r.y && p.x<r.x+r.w && p.y<r.y+r.h; }
// The header holds only DEBUG and the ball counter; the board starts below it (Camera::view).
constexpr yy::Rect debugButton{286,22,88,36};
constexpr yy::Rect panel{20,210,350,430};
constexpr yy::Rect ballsMinus{200,270,44,40}, ballsPlus{316,270,44,40};
constexpr yy::Rect bouncesMinus{200,335,44,40}, bouncesPlus{316,335,44,40};
constexpr yy::Rect radiusMinus{200,400,44,40}, radiusPlus{316,400,44,40};
constexpr yy::Rect restartButton{40,475,150,46}, closeButton{200,475,150,46};
constexpr yy::Rect overlay{24,340,342,156};
// Drawing code lets Power::None stand for the goal: its colour, name and flag icon.
constexpr yy::Color glowColors[]{{255,64,200},{255,112,48},{255,232,64},{64,214,255},{214,160,255},{96,255,128}};
constexpr const char* powerNames[]{"GOAL","BOMB","ELECTRICITY","PING","GHOST","SPEED UP"};
constexpr const char* powerLines[]{"","BREAKS THE 3X3 AROUND IT","THE BALL ZAPS BRICKS NEAR IT","SHOWS NEARBY POWER-UPS + GOAL",
  "THE BALL JUMPS DEEP INTO FOG","THE BALL FLIES TWICE AS FAST"};
// Each power-up's sound: pitch and length.
constexpr float powerTones[][2]{{0,0},{110,0.2f},{1320,0.12f},{1760,0.1f},{392,0.18f},{880,0.1f}};
constexpr yy::Color white{231,240,248}, tile{14,22,34}; // an icon's highlights, and the dark tile behind it
yy::Color glow(tapdemo::Power p) { return glowColors[static_cast<int>(p)]; }
yy::Color mix(yy::Color a, yy::Color b, float t) {
  const auto c=[&](unsigned char x, unsigned char y) { return static_cast<unsigned char>(x+(y-x)*t); };
  return {c(a.r,b.r),c(a.g,b.g),c(a.b,b.b)};
}
// A thick line through the given points, as overlapping dots.
void stroke(yy::Renderer& r, std::initializer_list<yy::Vec2> points, float width, yy::Color c) {
  const yy::Vec2* a=nullptr;
  for(const auto& b: points) {
    if(a) {
      const int n=std::max(1,static_cast<int>(std::ceil(std::hypot(b.x-a->x,b.y-a->y)/(width*0.3f))));
      for(int i=0; i<=n; ++i) { const float t=static_cast<float>(i)/n; r.circle({a->x+(b.x-a->x)*t,a->y+(b.y-a->y)*t},width/2,c); }
    }
    a=&b;
  }
}
// A power-up's icon (Power::None: the goal's flag), `size` across, centred at `at` on the dark tile.
void icon(yy::Renderer& r, Power power, yy::Vec2 at, float size) {
  const float u=size/16;
  const auto P=[&](float x, float y) { return yy::Vec2{at.x+x*u,at.y+y*u}; };
  const yy::Color c=glow(power);
  switch(power) {
  case Power::Bomb: // a round bomb, its shine, a fuse and a spark
    r.circle(P(-1,1.5f),6*u,c);
    r.circle(P(-3,-0.5f),1.4f*u,mix(c,white,0.7f));
    stroke(r,{P(2.5f,-3),P(4,-6)},1.8f*u,white);
    r.circle(P(5,-7),2*u,glow(Power::Electricity));
    break;
  case Power::Electricity: stroke(r,{P(3.5f,-8),P(-3,0.5f),P(3,-0.5f),P(-3.5f,8)},2.6f*u,c); break;
  case Power::Ping: // rings
    r.circle(at,7.5f*u,c); r.circle(at,5.8f*u,tile); r.circle(at,4.2f*u,c); r.circle(at,2.5f*u,tile); r.circle(at,1.2f*u,c);
    break;
  case Power::Ghost: // a round head, a wavy hem and two eyes
    r.circle(P(0,-1.5f),5.5f*u,c);
    r.rectangle({at.x-5.5f*u,at.y-1.5f*u,11*u,7.5f*u},c);
    r.circle(P(-2.75f,6.2f),1.5f*u,tile); r.circle(P(2.75f,6.2f),1.5f*u,tile);
    r.circle(P(-2.2f,-2),1.5f*u,tile); r.circle(P(2.2f,-2),1.5f*u,tile);
    break;
  case Power::Speed: // two chevrons
    stroke(r,{P(-6,-6),P(-1,0),P(-6,6)},2.6f*u,c);
    stroke(r,{P(1,-6),P(6,0),P(1,6)},2.6f*u,c);
    break;
  case Power::None: // the goal: a flag on a pole
    stroke(r,{P(-5,-7.5f),P(-5,7.5f)},1.8f*u,white);
    for(float x=-4.5f; x<6.5f; x+=0.5f) {
      const float half=4.5f*(1-(x+4.5f)/11);
      r.rectangle({at.x+x*u,at.y+(-3.5f-half)*u,0.6f*u,2*half*u},c);
    }
    break;
  }
}
// A glowing (or goal) brick: a pulsing frame in its colour around its icon.
void iconBrick(yy::Renderer& r, Power power, yy::Rect cell, float pulse) {
  const float rim=std::max(1.5f,cell.w*0.1f);
  r.rectangle(cell,mix(glow(power),white,pulse*0.35f));
  r.rectangle({cell.x+rim,cell.y+rim,cell.w-2*rim,cell.h-2*rim},tile);
  icon(r,power,{cell.x+cell.w/2,cell.y+cell.h/2},cell.w-rim);
}
}

class TapGame final: public yy::Game {
  Model model;
  Touch touch{model};
  yy::Audio* audio{};
  yy::Haptics* haptics{};
  bool debugOpen{};
  int debugBalls{Model::defaultBalls}, debugBounces{Model::defaultBounces};
  int uiFinger{-1}; // a finger the HUD took, kept from the board
  struct Burst { Fired fired; float age; };
  std::vector<Burst> bursts; // power-ups that just fired, while their rings grow
  float clock{};             // seconds, for glow pulses and sparks

  void restart(int balls, int bounces) { touch.cancel(); model.restart(balls,bounces); bursts.clear(); touch.camera.fit(); touch.instructions=true; }
  void openDebug() { touch.cancel(); debugBalls=model.ballCount; debugBounces=model.bouncesPerBall; debugOpen=true; }
  void pressDebug(yy::Vec2 p) {
    if(inside(ballsMinus,p)) debugBalls=std::max(1,debugBalls-1);
    else if(inside(ballsPlus,p)) debugBalls=std::min(Model::maxSetting,debugBalls+1);
    else if(inside(bouncesMinus,p)) debugBounces=std::max(1,debugBounces-1);
    else if(inside(bouncesPlus,p)) debugBounces=std::min(Model::maxSetting,debugBounces+1);
    else if(inside(radiusMinus,p)) model.setPingRadius(model.pingRadius-1);
    else if(inside(radiusPlus,p)) model.setPingRadius(model.pingRadius+1);
    else if(inside(restartButton,p)) { restart(debugBalls,debugBounces); debugOpen=false; }
    else if(inside(closeButton,p) || !inside(panel,p)) debugOpen=false;
  }
  void sling(yy::Vec2 at, yy::Vec2 pull) { pointerDown(0,at); pointerMove(0,{at.x+pull.x,at.y+pull.y}); pointerUp(0,{at.x+pull.x,at.y+pull.y}); }
  // YY_TAPDEMO_SCENE stages a moment for smoke screenshots: instructions (as the game opens),
  // header (one ball flying), aim, debug, play, zoom, icons (one of each power-up and the goal
  // beside the pocket) or glow (the same close up), breaks (four launches), electric (a launch
  // into that power-up), pingin or pingout (a launch into a Ping brick with the goal inside or
  // outside the ping radius), won (a launch into the goal).
  void stage(const char* scene) {
    if(!scene || model.pockets.empty() || std::strcmp(scene,"instructions")==0) return;
    touch.instructions=false;
    const auto& pocket=model.pockets.front();
    const yy::Vec2 centre{(pocket.column+pocket.columns/2.0f)*Model::cell, (pocket.row+pocket.rows/2.0f)*Model::cell};
    const int column=pocket.column+pocket.columns/2;
    const yy::Vec2 below{(column+0.5f)*Model::cell,centre.y}; // in the pocket, under the brick at (column, pocket.row-1)
    const auto set=[&](int c, int r, Power power) { model.bricks[r*Model::columns+c]=1; model.powers[r*Model::columns+c]=power; model.refreshFog(); };
    const auto setGoal=[&](int c, int r) { set(c,r,Power::None); model.goal=r*Model::columns+c; };
    if(std::strcmp(scene,"debug")==0) { openDebug(); return; }
    if(std::strcmp(scene,"zoom")==0) { touch.camera.hold(centre,{195,480},2.0f); return; }
    if(std::strcmp(scene,"header")==0) { sling(touch.camera.toScreen(below),{20,40}); return; }
    if(std::strcmp(scene,"icons")==0 || std::strcmp(scene,"glow")==0) {
      set(pocket.column,pocket.row-1,Power::Bomb); set(pocket.column+1,pocket.row-1,Power::Electricity); set(pocket.column+2,pocket.row-1,Power::Ping);
      set(pocket.column-1,pocket.row,Power::Ghost); set(pocket.column+pocket.columns,pocket.row,Power::Speed);
      setGoal(pocket.column+pocket.columns,pocket.row+1);
      if(scene[0]=='g') touch.camera.hold(centre,{195,480},2.0f);
      return;
    }
    if(std::strcmp(scene,"breaks")==0) {
      const yy::Vec2 at=touch.camera.toScreen(centre);
      for(yy::Vec2 pull: {yy::Vec2{20,40},{-40,15},{5,-40},{40,-10}}) sling(at,pull);
      return;
    }
    if(std::strcmp(scene,"won")==0) { setGoal(column,pocket.row-1); sling(touch.camera.toScreen(below),{0,40}); return; }
    const bool pingIn=std::strcmp(scene,"pingin")==0, pingOut=std::strcmp(scene,"pingout")==0;
    if(pingIn || pingOut || std::strcmp(scene,"electric")==0) {
      const int row=pocket.row-1;
      set(column,row,pingIn || pingOut ? Power::Ping : Power::Electricity);
      if(pingIn || pingOut) {
        // The goal and a Bomb deep in the fog, one inside the ping radius and one outside it.
        const auto place=[&](int distance, Power power) {
          for(auto [dc,dr]: {std::pair{0,-1},{1,0},{-1,0},{0,1},{1,-1},{-1,-1},{1,1},{-1,1}}) {
            const float scale=distance/std::hypot(static_cast<float>(dc),static_cast<float>(dr));
            const int c=column+static_cast<int>(std::lround(dc*scale)), r=row+static_cast<int>(std::lround(dr*scale));
            if(c<0 || r<0 || c>=Model::columns || r>=Model::rows || model.fogDistance(c,r)<=Model::fogReach+1 || model.isGoal(c,r)) continue;
            if(power==Power::None) setGoal(c,r); else set(c,r,power);
            return;
          }
        };
        place(pingIn ? 4 : 9,Power::None);
        place(pingIn ? 9 : 4,Power::Bomb);
        touch.camera.fit();
      } else touch.camera.hold(centre,{195,480},1.6f);
      sling(touch.camera.toScreen(below),{0,40});
      if((pingIn || pingOut) && !model.balls.empty()) model.balls.back().bounces=1; // spent on the Ping brick
      return;
    }
    touch.camera.hold(centre,{195,480},1.4f);
    const yy::Vec2 at=touch.camera.toScreen(centre);
    pointerDown(0,at);
    pointerMove(0,{at.x+40,at.y+70});
    if(std::strcmp(scene,"play")==0) pointerUp(0,{at.x+40,at.y+70});
  }
public:
  void initialize(yy::Services& services) override {
    audio=&services.audio; haptics=&services.haptics; touch.haptics=haptics;
    stage(std::getenv("YY_TAPDEMO_SCENE"));
  }
  void update(float seconds) override {
    model.update(seconds); touch.update(seconds);
    clock+=seconds;
    for(auto& b: bursts) b.age+=seconds;
    bursts.erase(std::remove_if(bursts.begin(),bursts.end(),[](const Burst& b){ return b.age>1.2f; }),bursts.end());
    // The goal's fanfare, or a power-up's thump and tone, stands in for the plain break's tap that frame.
    for(const auto& f: model.hits.fired) bursts.push_back({f,0});
    if(model.hits.goalBroken) {
      bursts.push_back({{Power::None,model.goal,-1},0});
      if(haptics) { haptics->thump(); haptics->impact(1); }
      if(audio) for(float hz: {660.0f,880.0f,1320.0f}) audio->tone(hz,0.12f);
    } else if(!model.hits.fired.empty()) {
      const auto& tone=powerTones[static_cast<int>(model.hits.fired.front().power)];
      if(haptics) haptics->thump();
      if(audio) audio->tone(tone[0],tone[1]);
    } else if(model.hits.bricksBroken>0) {
      if(haptics) haptics->impact(std::min(1.0f,0.55f+0.15f*(model.hits.bricksBroken-1)));
      if(audio) audio->tone(660.0f,0.05f);
    }
  }
  void pause(bool value) override { model.pause(value); if(value) touch.cancel(); }
  void shutdown() override { touch.cancel(); touch.haptics=nullptr; audio=nullptr; haptics=nullptr; }
  void tap(yy::Vec2) override {}
  void pointerDown(int id, yy::Vec2 p) override {
    if(debugOpen) { uiFinger=id; pressDebug(p); return; }
    if(inside(debugButton,p)) { uiFinger=id; openDebug(); return; }
    if(model.over() && inside(overlay,p)) { uiFinger=id; restart(model.ballCount,model.bouncesPerBall); return; }
    touch.down(id,p);
  }
  void pointerMove(int id, yy::Vec2 p) override { if(id!=uiFinger) touch.move(id,p); }
  void pointerUp(int id, yy::Vec2 p) override {
    if(id==uiFinger) { uiFinger=-1; return; }
    touch.up(id,p);
  }
  void zoom(yy::Vec2 at, float steps) override {
    if(!debugOpen && !touch.instructions && touch.camera.contains(at)) touch.camera.zoomAt(at,touch.camera.zoom*std::pow(1.15f,steps));
  }
  void render(yy::Renderer& r) override {
    using yy::Color;
    const Color muted{132,155,178}, teal{70,235,196}, dark{9,20,33};
    const Color ballRed{235,70,70}, field{17,34,52};
    const Color brickColors[]{{58,110,168},{196,146,62},{148,96,206}};
    const Camera& cam=touch.camera;
    const float z=cam.zoom, cellSize=Model::cell*z;
    const auto origin=cam.toScreen({0,0});
    r.rectangle({origin.x,origin.y,Model::width()*z,Model::height()*z},field);

    // Bricks in view, with their hit points once the cells are big enough to read.
    const auto first=cam.toWorld({cam.view.x,cam.view.y}), last=cam.toWorld({cam.view.x+cam.view.w,cam.view.y+cam.view.h});
    const int c0=std::max(0,static_cast<int>(first.x/Model::cell)), c1=std::min(Model::columns-1,static_cast<int>(last.x/Model::cell));
    const int r0=std::max(0,static_cast<int>(first.y/Model::cell)), r1=std::min(Model::rows-1,static_cast<int>(last.y/Model::cell));
    const float gap=std::max(1.0f,cellSize*0.06f), digit=std::clamp(cellSize/22,0.75f,2.5f);
    // Fogged cells hide their hit points and icons, except glowing bricks and the goal within a ping.
    // Icon bricks hide their hit points so no digit is read as part of the icon.
    const Color fogColors[]{{30,42,56},{27,38,51}};
    const float pulse=0.5f+0.5f*std::sin(clock*6);
    for(int row=r0; row<=r1; ++row) for(int column=c0; column<=c1; ++column) {
      const int hp=model.brick(column,row);
      const bool goal=hp>0 && model.isGoal(column,row);
      const Power power=model.power(column,row);
      const auto p=cam.toScreen({column*Model::cell,row*Model::cell});
      const yy::Rect box{p.x,p.y,cellSize,cellSize};
      if(!model.visible(column,row)) {
        r.rectangle(box,fogColors[(column+row)%2]);
        if((goal || power!=Power::None) && model.pinged(column,row)) iconBrick(r,power,box,pulse);
        continue;
      }
      if(hp<=0) continue;
      if(goal || power!=Power::None) { iconBrick(r,power,box,pulse); continue; }
      r.rectangle({p.x+gap/2,p.y+gap/2,cellSize-gap,cellSize-gap},brickColors[std::min(hp,3)-1]);
      if(cellSize>=12) r.text({p.x+cellSize/2-4*digit,p.y+cellSize/2-4*digit},std::to_string(hp),dark,digit);
    }
    // Each ping's reach, fading as it wears off.
    for(int index: model.pingCells()) {
      const auto at=cam.toScreen({(index%Model::columns+0.5f)*Model::cell,(index/Model::columns+0.5f)*Model::cell});
      const float reach=model.pingRadius*Model::cell*z;
      const Color c=mix(field,glow(Power::Ping),std::min(1.0f,model.pingTime/Model::pingSeconds*1.5f));
      for(int i=0; i<64; ++i) {
        const float a=i*6.2831853f/64;
        r.circle({at.x+std::cos(a)*reach,at.y+std::sin(a)*reach},std::max(1.0f,1.5f*z),c);
      }
    }
    // Power-up bursts: a growing ring on the brick that fired, and on a ghost's arrival.
    for(const auto& b: bursts) {
      const float t=b.age/1.2f, reach=(b.fired.power==Power::Bomb ? 1.6f : 0.9f)*Model::cell*z*(0.4f+t);
      const Color c=mix(glow(b.fired.power),field,t);
      for(int index: {b.fired.cell,b.fired.to}) {
        if(index<0) continue;
        const auto at=cam.toScreen({(index%Model::columns+0.5f)*Model::cell,(index/Model::columns+0.5f)*Model::cell});
        for(int i=0; i<20; ++i) {
          const float a=i*6.2831853f/20;
          r.circle({at.x+std::cos(a)*reach,at.y+std::sin(a)*reach},std::max(1.5f,2.5f*z),c);
        }
      }
    }
    const float ballSize=Model::ballRadius*z;
    const Color spark=glow(Power::Electricity), streak=glow(Power::Speed);
    for(const auto& b: model.balls) {
      const auto p=cam.toScreen(b.position);
      if(b.fast) for(int i=3; i>=1; --i)
        r.circle(cam.toScreen({b.position.x-b.velocity.x*0.012f*i,b.position.y-b.velocity.y*0.012f*i}),ballSize*(1-0.2f*i),mix(streak,field,0.25f*i));
      if(b.electric>0) {
        // An aura at the zap radius and flickering arcs to every brick it reaches.
        const float reach=Model::electricRadius*Model::cell;
        for(int i=0; i<24; ++i) {
          const float a=i*6.2831853f/24+clock*2, wobble=1+0.06f*std::sin(clock*37+i*5.0f);
          r.circle({p.x+std::cos(a)*reach*z*wobble,p.y+std::sin(a)*reach*z*wobble},std::max(1.2f,1.6f*z),spark);
        }
        const int bc=static_cast<int>(b.position.x/Model::cell), br=static_cast<int>(b.position.y/Model::cell);
        for(int row=br-2; row<=br+2; ++row) for(int column=bc-2; column<=bc+2; ++column) {
          const float dx=(column+0.5f)*Model::cell-b.position.x, dy=(row+0.5f)*Model::cell-b.position.y;
          if(model.brick(column,row)<=0 || dx*dx+dy*dy>reach*reach) continue;
          const float length=std::max(1.0f,std::hypot(dx,dy)), nx=-dy/length, ny=dx/length;
          for(int k=1; k<=7; ++k) {
            const float t=k/8.0f, jag=std::sin(clock*53+k*2.7f+column*1.3f+row*0.7f)*5;
            r.circle(cam.toScreen({b.position.x+dx*t+nx*jag,b.position.y+dy*t+ny*jag}),std::max(1.0f,1.4f*z),mix(spark,white,0.4f));
          }
        }
        r.circle(p,ballSize+2*z,spark);
      }
      r.circle(p,ballSize,ballRed);
      r.text({p.x+ballSize+3,p.y-ballSize-6},std::to_string(b.bounces),white,1.25f);
    }
    if(touch.aim) {
      const auto& aim=*touch.aim;
      const float pull=std::hypot(aim.pull.x,aim.pull.y);
      const bool ready=pull>=Model::minPull;
      const auto anchor=cam.toScreen(aim.anchor);
      // The band back to the finger, then dots along the launch line.
      for(int i=1; i<=6; ++i) {
        const float t=i/6.0f;
        r.circle(cam.toScreen({aim.anchor.x+aim.pull.x*t,aim.anchor.y+aim.pull.y*t}),1.5f,muted);
      }
      if(ready) {
        const float length=std::min(pull,Touch::fullPull)*2.5f;
        for(float d=Model::ballRadius+14; d<=length; d+=16) {
          const yy::Vec2 w{aim.anchor.x-aim.pull.x/pull*d, aim.anchor.y-aim.pull.y/pull*d};
          r.circle(cam.toScreen(w),std::max(2.0f,3.0f*z*(1-d/(length+16))),white);
        }
      }
      r.circle(anchor,ballSize+3,ready ? white : muted);
      r.circle(anchor,ballSize,ballRed);
      if(!r.sprite("spark.bmp",{anchor.x-ballSize*0.6f,anchor.y-ballSize*0.65f,ballSize*0.6f,ballSize*0.6f}))
        r.circle({anchor.x-ballSize*0.3f,anchor.y-ballSize*0.3f},ballSize*0.25f,{166,255,226});
    }
    if(touch.rejectTime>0) {
      const auto p=cam.toScreen(touch.rejected); const float ring=(Model::ballRadius+6)*z;
      for(int i=0; i<16; ++i) {
        const float a=i*6.2831853f/16;
        r.circle({p.x+std::cos(a)*ring,p.y+std::sin(a)*ring},2.5f,ballRed);
      }
    }

    // The header covers anything of the grid drawn above the play area: the ball counter and DEBUG.
    r.rectangle({0,0,cam.view.w,cam.view.y},dark);
    r.rectangle({0,cam.view.y-2,cam.view.w,2},{40,61,80});
    r.circle({36,40},16,ballRed);
    r.circle({31,35},4.5f,{255,150,150});
    r.text({62,20},std::to_string(model.ballsLeft),white,5);
    r.rectangle(debugButton,{40,61,80});
    r.text({debugButton.x+14,debugButton.y+12},"DEBUG",white,1.5f);

    if(touch.instructions) {
      const yy::Rect card{12,cam.view.y+10,cam.view.w-24,cam.view.h-20};
      r.rectangle(card,{15,29,45});
      r.rectangle({card.x,card.y,card.w,2},glow(Power::None));
      float y=card.y+20;
      r.rectangle({24,y,40,40},tile); icon(r,Power::None,{44,y+20},34);
      r.text({76,y+10},"FIND THE GOAL",glow(Power::None),2.5f);
      y+=58;
      const std::string limits=std::to_string(model.ballCount)+" BALLS, "+std::to_string(model.bouncesPerBall)+" BOUNCES EACH.";
      for(const std::string& line: {std::string("BREAK THE GOAL BRICK TO WIN."),std::string("IT HIDES IN THE FOG."),std::string(),
                                    std::string("HOLD IN A GAP, PULL, LET GO."),limits,std::string("NO BALLS LEFT: YOU LOSE.")}) {
        r.text({24,y},line,white,1.5f); y+=20;
      }
      y+=14;
      r.text({24,y},"POWER-UPS",teal,2); y+=28;
      for(int k=1; k<=powerKinds; ++k) {
        const Power power=static_cast<Power>(k);
        r.rectangle({24,y,40,40},tile); icon(r,power,{44,y+20},34);
        r.text({76,y+4},powerNames[k],glow(power),2);
        r.text({76,y+26},powerLines[k],muted,1.25f);
        y+=54;
      }
      r.text({75,card.y+card.h-50},"TAP TO START",teal,2.5f);
    }
    if(model.over()) {
      r.rectangle(overlay,{15,29,45});
      r.text({58,367}, model.won() ? "GOAL FOUND" : "OUT OF BALLS",model.won() ? glow(Power::None) : white,2.5f);
      if(model.won()) r.text({58,410}, "BALLS LEFT " + std::to_string(model.ballsLeft),teal);
      else r.text({58,410}, "THE GOAL STAYED HIDDEN",teal,1.5f);
      r.text({58,450}, "TAP TO RESTART",muted);
    }
    if(debugOpen) {
      r.rectangle(panel,{15,29,45});
      r.rectangle({panel.x,panel.y,panel.w,2},teal);
      r.text({panel.x+20,panel.y+22},"DEBUG",teal);
      const auto row=[&](const char* label, int value, yy::Rect minus, yy::Rect plus) {
        r.text({panel.x+20,minus.y+14},label,white,1.5f);
        r.rectangle(minus,{40,61,80}); r.rectangle(plus,{40,61,80});
        r.text({minus.x+14,minus.y+12},"-",white); r.text({plus.x+14,plus.y+12},"+",white);
        r.text({minus.x+minus.w+18,minus.y+12},std::to_string(value),teal);
      };
      row("BALLS",debugBalls,ballsMinus,ballsPlus);
      row("BOUNCES",debugBounces,bouncesMinus,bouncesPlus);
      row("PING RADIUS",model.pingRadius,radiusMinus,radiusPlus);
      r.rectangle(restartButton,{70,235,196}); r.text({restartButton.x+19,restartButton.y+15},"RESTART",dark);
      r.rectangle(closeButton,{40,61,80}); r.text({closeButton.x+35,closeButton.y+15},"CLOSE",white);
      r.text({panel.x+20,panel.y+330},"RESTART APPLIES BALLS+BOUNCES",muted,1.25f);
      r.text({panel.x+20,panel.y+350},"PING RADIUS APPLIES NOW",muted,1.25f);
      r.text({panel.x+20,panel.y+390},"VERSION " YY_GAME_VERSION,muted,1.5f);
    }
  }
};
std::unique_ptr<yy::Game> createGame() { return std::make_unique<TapGame>(); }
}
