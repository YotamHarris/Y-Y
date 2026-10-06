#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <tapdemo/palette.hpp>
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
// The debug panel covers the screen: fourteen stepper rows (a label, -, value, +) under three
// headings (what applies now, what RESTART applies, the weights), then COLORS, RESTART and CLOSE.
constexpr yy::Rect panel{20,12,350,830};
enum Stepper { PingRadius, BombSize, ZapSeconds, ZapReach, SnapAngle, Balls, Bounces, GridSize, GlowRate, Weight0, steppers=Weight0+powerKinds };
constexpr float stepperTops[steppers]{72,116,160,204,248, 316,360,404,448, 516,560,604,648,692};
constexpr float headingTops[]{54,298,498};
constexpr yy::Rect minusButton(int row) { return {180,stepperTops[row],44,40}; }
constexpr yy::Rect plusButton(int row) { return {320,stepperTops[row],44,40}; }
constexpr yy::Rect colorsButton{40,742,310,40};
constexpr yy::Rect restartButton{40,790,150,44}, closeButton{200,790,150,44};
constexpr yy::Rect overlay{24,340,342,156};
// Drawing code lets Power::None stand for the goal: its colour, name and flag icon.
constexpr const char* powerNames[]{"GOAL","BOMB","ELECTRICITY","PING","GHOST","SPEED UP"};
constexpr const char* weightNames[]{"","BOMB","ELECTRIC","PING","GHOST","SPEED"};
// Each power-up's line on the instructions card; Bomb's names its size, so render writes it.
constexpr const char* powerLines[]{"","","THE BALL ZAPS BRICKS NEAR IT","SHOWS NEARBY POWER-UPS + GOAL",
  "THE BALL JUMPS DEEP INTO FOG","THE BALL FLIES TWICE AS FAST"};
// Each power-up's sound: pitch and length.
constexpr float powerTones[][2]{{0,0},{110,0.2f},{1320,0.12f},{1760,0.1f},{392,0.18f},{880,0.1f}};
// The glow rate steps by half a percent up to 5%, then by whole percents.
int stepGlow(int halfPercents, int by) {
  const bool whole=by>0 ? halfPercents>=10 : halfPercents>10;
  return std::clamp(halfPercents+by*(whole ? 2 : 1),0,Settings::maxGlow);
}
std::string percent(int halfPercents) { return std::to_string(halfPercents/2)+(halfPercents%2 ? ".5%" : "%"); }
std::string halves(float value) { const int h=static_cast<int>(std::lround(value*2)); return std::to_string(h/2)+(h%2 ? ".5" : ""); }
std::string square(int cells) { return std::to_string(cells)+"X"+std::to_string(cells); }
yy::Color glow(const Palette& palette, Power p) { return palette.glows[static_cast<int>(p)]; }
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
void icon(yy::Renderer& r, const Palette& palette, Power power, yy::Vec2 at, float size) {
  const auto white=palette.white, tile=palette.tile;
  const float u=size/16;
  const auto P=[&](float x, float y) { return yy::Vec2{at.x+x*u,at.y+y*u}; };
  const yy::Color c=glow(palette,power);
  switch(power) {
  case Power::Bomb: // a round bomb, its shine, a fuse and a spark
    r.circle(P(-1,1.5f),6*u,c);
    r.circle(P(-3,-0.5f),1.4f*u,mix(c,white,0.7f));
    stroke(r,{P(2.5f,-3),P(4,-6)},1.8f*u,white);
    r.circle(P(5,-7),2*u,glow(palette,Power::Electricity));
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
void iconBrick(yy::Renderer& r, const Palette& palette, Power power, yy::Rect cell, float pulse) {
  const auto white=palette.white, tile=palette.tile;
  const float rim=std::max(1.5f,cell.w*0.1f);
  r.rectangle(cell,mix(glow(palette,power),white,pulse*0.35f));
  r.rectangle({cell.x+rim,cell.y+rim,cell.w-2*rim,cell.h-2*rim},tile);
  icon(r,palette,power,{cell.x+cell.w/2,cell.y+cell.h/2},cell.w-rim);
}
}

class TapGame final: public yy::Game {
  Model model;
  Touch touch{model};
  yy::Audio* audio{};
  yy::Haptics* haptics{};
  bool debugOpen{};
  std::size_t scheme{}; // session preference: restarting a round keeps it
  int debugBalls{Model::defaultBalls}, debugBounces{Model::defaultBounces};
  Settings debugGrid; // pending until RESTART, like balls and bounces
  int uiFinger{-1}; // a finger the HUD took, kept from the board
  struct Burst { Fired fired; float age; };
  std::vector<Burst> bursts; // power-ups that just fired, while their rings grow
  float clock{};             // seconds, for glow pulses and sparks

  void restart(int balls, int bounces, const Settings& grid) {
    touch.cancel(); model.restart(balls,bounces,grid); bursts.clear(); touch.refit(); touch.instructions=true;
  }
  void openDebug() { touch.cancel(); debugBalls=model.ballCount; debugBounces=model.bouncesPerBall; debugGrid=model.settings; debugOpen=true; }
  void step(int row, int by) {
    switch(row) {
    case Balls: debugBalls=std::clamp(debugBalls+by,1,Model::maxSetting); break;
    case Bounces: debugBounces=std::clamp(debugBounces+by,1,Model::maxSetting); break;
    case PingRadius: model.setPingRadius(model.pingRadius+by); break;
    case BombSize: model.setBombSize(model.bombSize+2*by); break;
    case ZapSeconds: model.setElectricSeconds(model.electricSeconds+by); break;
    case ZapReach: model.setElectricRadius(model.electricRadius+0.5f*by); break;
    case SnapAngle: model.setSnapDegrees(model.snapDegrees+by); break;
    case GridSize: debugGrid.gridScale=std::clamp(debugGrid.gridScale+by,Settings::minScale,Settings::maxScale); break;
    case GlowRate: debugGrid.glow=stepGlow(debugGrid.glow,by); break;
    default: { int& w=debugGrid.weights[row-Weight0]; w=std::clamp(w+by,0,Settings::maxWeight); }
    }
  }
  void pressDebug(yy::Vec2 p) {
    for(int row=0; row<steppers; ++row) {
      if(inside(minusButton(row),p)) { step(row,-1); return; }
      if(inside(plusButton(row),p)) { step(row,1); return; }
    }
    if(inside(colorsButton,p)) scheme=(scheme+1)%palettes.size();
    else if(inside(restartButton,p)) { restart(debugBalls,debugBounces,debugGrid); debugOpen=false; }
    else if(inside(closeButton,p) || !inside(panel,p)) debugOpen=false;
  }
  void sling(yy::Vec2 at, yy::Vec2 pull) { pointerDown(0,at); pointerMove(0,{at.x+pull.x,at.y+pull.y}); pointerUp(0,{at.x+pull.x,at.y+pull.y}); }
  // YY_TAPDEMO_SCENE stages a moment for smoke screenshots: instructions (as the game opens),
  // header (one ball flying), aim, snap (an aim 3 degrees off horizontal), debug, play, zoom, icons (one of each power-up and the goal
  // beside the pocket) or glow (the same close up), breaks (four launches), electric (a launch
  // into that power-up), pingin or pingout (a launch into a Ping brick with the goal inside or
  // outside the ping radius), won (a launch into the goal), palette / palette-fit /
  // palette-max (all icons, a real Ping revealing fogged bricks, frozen at activation),
  // grid-min or grid-max (the debug steppers set the smallest or largest grid, then RESTART).
  void stage(const char* scene) {
    if(!scene || model.pockets.empty() || std::strcmp(scene,"instructions")==0) return;
    if(std::strncmp(scene,"grid-",5)==0) {
      const auto press=[&](yy::Rect b) { const yy::Vec2 p{b.x+b.w/2,b.y+b.h/2}; pointerDown(0,p); pointerUp(0,p); };
      press(debugButton);
      const yy::Rect button=std::strcmp(scene,"grid-min")==0 ? minusButton(GridSize) : plusButton(GridSize);
      for(int i=0; i<Settings::maxScale; ++i) press(button);
      press(restartButton);
      touch.instructions=false;
      return;
    }
    touch.instructions=false;
    const auto& pocket=model.pockets.front();
    const yy::Vec2 centre{(pocket.column+pocket.columns/2.0f)*Model::cell, (pocket.row+pocket.rows/2.0f)*Model::cell};
    const int column=pocket.column+pocket.columns/2;
    const yy::Vec2 below{(column+0.5f)*Model::cell,centre.y}; // in the pocket, under the brick at (column, pocket.row-1)
    const auto set=[&](int c, int r, Power power) { model.bricks[r*model.columns+c]=1; model.powers[r*model.columns+c]=power; model.refreshFog(); };
    const auto setGoal=[&](int c, int r) { set(c,r,Power::None); model.goal=r*model.columns+c; };
    if(std::strcmp(scene,"debug")==0) { openDebug(); return; }
    if(std::strcmp(scene,"zoom")==0) { touch.camera.hold(centre,{195,480},2.0f); return; }
    if(std::strcmp(scene,"header")==0) { sling(touch.camera.toScreen(below),{20,40}); return; }
    const bool paletteScene=std::strncmp(scene,"palette",7)==0;
    if(std::strcmp(scene,"icons")==0 || std::strcmp(scene,"glow")==0 || paletteScene) {
      set(pocket.column,pocket.row-1,Power::Bomb); set(pocket.column+1,pocket.row-1,Power::Electricity); set(pocket.column+2,pocket.row-1,Power::Ping);
      set(pocket.column-1,pocket.row,Power::Ghost); set(pocket.column+pocket.columns,pocket.row,Power::Speed);
      setGoal(pocket.column+pocket.columns,pocket.row+1);
      if(paletteScene) {
        // These placements are screenshot fixtures, not changes to fog or Ping rules.
        setGoal(pocket.column+1,pocket.row-4);
        set(pocket.column-1,pocket.row+1,Power::Ping);
        const int fogBomb=pocket.column+pocket.columns+3;
        if(fogBomb<model.columns) set(fogBomb,pocket.row,Power::Bomb);
        const yy::Vec2 at{(pocket.column+2.5f)*Model::cell,centre.y};
        sling(touch.camera.toScreen(at),{0,40});
        for(int i=0; i<180 && model.pingTime<=0; ++i) model.update(1.0f/60);
        model.pause(true); // hold the actual reveal and red ball for every scheme
        if(std::strcmp(scene,"palette-fit")==0) touch.camera.fit();
        else touch.camera.hold(centre,{195,480},std::strcmp(scene,"palette-max")==0 ? Camera::maxZoom : 1.25f);
      }
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
            if(c<0 || r<0 || c>=model.columns || r>=model.rows || model.fogDistance(c,r)<=Model::fogReach+1 || model.isGoal(c,r)) continue;
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
    const yy::Vec2 pull=std::strcmp(scene,"snap")==0 ? yy::Vec2{-100,-5} : yy::Vec2{40,70};
    pointerMove(0,{at.x+pull.x,at.y+pull.y});
    if(std::strcmp(scene,"play")==0) pointerUp(0,{at.x+pull.x,at.y+pull.y});
  }
public: void initialize(yy::Services& services) override {
    audio=&services.audio; haptics=&services.haptics; touch.haptics=haptics;
    if(const char* name=std::getenv("YY_TAPDEMO_SCHEME"))
      for(std::size_t i=0; i<palettes.size(); ++i) if(palettes[i].name==name) { scheme=i; break; }
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
    if(model.over() && inside(overlay,p)) { uiFinger=id; restart(model.ballCount,model.bouncesPerBall,model.settings); return; }
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
    const Palette& palette=palettes[scheme];
    const auto muted=palette.muted, teal=palette.teal, dark=palette.dark;
    const auto ballRed=palette.ballRed, field=palette.field, white=palette.white, tile=palette.tile;
    const auto& brickColors=palette.bricks;
    const Camera& cam=touch.camera;
    const float z=cam.zoom, cellSize=Model::cell*z;
    const auto origin=cam.toScreen({0,0});
    // At fit zoom the grid leaves margins; give those the scheme's backdrop too.
    r.rectangle({0,0,cam.view.w,cam.view.y+cam.view.h},dark);
    r.rectangle({origin.x,origin.y,model.width()*z,model.height()*z},field);

    // Bricks in view, with their hit points once the cells are big enough to read.
    const auto first=cam.toWorld({cam.view.x,cam.view.y}), last=cam.toWorld({cam.view.x+cam.view.w,cam.view.y+cam.view.h});
    const int c0=std::max(0,static_cast<int>(first.x/Model::cell)), c1=std::min(model.columns-1,static_cast<int>(last.x/Model::cell));
    const int r0=std::max(0,static_cast<int>(first.y/Model::cell)), r1=std::min(model.rows-1,static_cast<int>(last.y/Model::cell));
    const float gap=std::max(1.0f,cellSize*0.06f), digit=std::clamp(cellSize/22,0.75f,2.5f);
    // Fogged cells hide their hit points and icons, except glowing bricks and the goal within a ping.
    // Icon bricks hide their hit points so no digit is read as part of the icon.
    const auto& fogColors=palette.fog;
    const float pulse=0.5f+0.5f*std::sin(clock*6);
    for(int row=r0; row<=r1; ++row) for(int column=c0; column<=c1; ++column) {
      const int hp=model.brick(column,row);
      const bool goal=hp>0 && model.isGoal(column,row);
      const Power power=model.power(column,row);
      const auto p=cam.toScreen({column*Model::cell,row*Model::cell});
      const yy::Rect box{p.x,p.y,cellSize,cellSize};
      if(!model.visible(column,row)) {
        r.rectangle(box,fogColors[(column+row)%2]);
        // Two staggered flecks per hidden cell. Screen-sized marks stay legible
        // at fit zoom; plain open cavities never receive this texture.
        const float fleck=std::clamp(cellSize*0.12f,1.5f,4.0f);
        r.rectangle({p.x+cellSize*0.25f,p.y+cellSize*0.3f,fleck,1.5f},palette.fogTexture);
        r.rectangle({p.x+cellSize*0.65f,p.y+cellSize*0.65f,fleck,1.5f},palette.fogTexture);
        if((goal || power!=Power::None) && model.pinged(column,row)) iconBrick(r,palette,power,box,pulse);
        continue;
      }
      if(hp<=0) continue;
      if(goal || power!=Power::None) { iconBrick(r,palette,power,box,pulse); continue; }
      r.rectangle({p.x+gap/2,p.y+gap/2,cellSize-gap,cellSize-gap},brickColors[std::min(hp,3)-1]);
      if(cellSize>=12) r.text({p.x+cellSize/2-4*digit,p.y+cellSize/2-4*digit},std::to_string(hp),dark,digit);
    }
    // Each ping's reach, fading as it wears off.
    for(int index: model.pingCells()) {
      const auto at=cam.toScreen({(index%model.columns+0.5f)*Model::cell,(index/model.columns+0.5f)*Model::cell});
      const float reach=model.pingRadius*Model::cell*z;
      const Color c=mix(field,glow(palette,Power::Ping),std::min(1.0f,model.pingTime/Model::pingSeconds*1.5f));
      for(int i=0; i<64; ++i) {
        const float a=i*6.2831853f/64;
        r.circle({at.x+std::cos(a)*reach,at.y+std::sin(a)*reach},std::max(1.0f,1.5f*z),c);
      }
    }
    // Power-up bursts: a growing ring on the brick that fired, and on a ghost's arrival.
    for(const auto& b: bursts) {
      const float t=b.age/1.2f, reach=(b.fired.power==Power::Bomb ? model.bombSize*0.5f+0.1f : 0.9f)*Model::cell*z*(0.4f+t);
      const Color c=mix(glow(palette,b.fired.power),field,t);
      for(int index: {b.fired.cell,b.fired.to}) {
        if(index<0) continue;
        const auto at=cam.toScreen({(index%model.columns+0.5f)*Model::cell,(index/model.columns+0.5f)*Model::cell});
        for(int i=0; i<20; ++i) {
          const float a=i*6.2831853f/20;
          r.circle({at.x+std::cos(a)*reach,at.y+std::sin(a)*reach},std::max(1.5f,2.5f*z),c);
        }
      }
    }
    const float ballSize=Model::ballRadius*z;
    const Color spark=glow(palette,Power::Electricity), streak=glow(palette,Power::Speed);
    for(const auto& b: model.balls) {
      const auto p=cam.toScreen(b.position);
      if(b.fast) for(int i=3; i>=1; --i)
        r.circle(cam.toScreen({b.position.x-b.velocity.x*0.012f*i,b.position.y-b.velocity.y*0.012f*i}),ballSize*(1-0.2f*i),mix(streak,field,0.25f*i));
      if(b.electric>0) {
        // An aura at the zap radius and flickering arcs to every brick it reaches.
        const float reach=model.electricRadius*Model::cell;
        for(int i=0; i<24; ++i) {
          const float a=i*6.2831853f/24+clock*2, wobble=1+0.06f*std::sin(clock*37+i*5.0f);
          r.circle({p.x+std::cos(a)*reach*z*wobble,p.y+std::sin(a)*reach*z*wobble},std::max(1.2f,1.6f*z),spark);
        }
        const int bc=static_cast<int>(b.position.x/Model::cell), br=static_cast<int>(b.position.y/Model::cell);
        const int span=static_cast<int>(std::ceil(model.electricRadius))+1;
        for(int row=br-span; row<=br+span; ++row) for(int column=bc-span; column<=bc+span; ++column) {
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
      r.circle(p,ballSize+std::max(1.5f,z),tile);
      r.circle(p,ballSize,ballRed);
      r.text({p.x+ballSize+3,p.y-ballSize-6},std::to_string(b.bounces),white,1.25f);
    }
    if(touch.aim) {
      const auto& aim=*touch.aim;
      const float pull=std::hypot(aim.pull.x,aim.pull.y);
      const yy::Vec2 flies=snapPull(aim.pull,static_cast<float>(model.snapDegrees)); // the launch's direction
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
          const yy::Vec2 w{aim.anchor.x-flies.x/pull*d, aim.anchor.y-flies.y/pull*d};
          r.circle(cam.toScreen(w),std::max(2.0f,3.0f*z*(1-d/(length+16))),white);
        }
      }
      r.circle(anchor,ballSize+3,ready ? white : muted);
      r.circle(anchor,ballSize,ballRed);
      r.circle({anchor.x-ballSize*0.3f,anchor.y-ballSize*0.3f},ballSize*0.25f,palette.aimHighlight);
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
    r.rectangle({0,cam.view.y-2,cam.view.w,2},palette.button);
    r.circle({36,40},16,ballRed);
    r.circle({31,35},4.5f,palette.ballHighlight);
    r.text({62,20},std::to_string(model.ballsLeft),white,5);
    r.rectangle(debugButton,palette.button);
    r.text({debugButton.x+14,debugButton.y+12},"DEBUG",white,1.5f);

    if(touch.instructions) {
      const yy::Rect card{12,cam.view.y+10,cam.view.w-24,cam.view.h-20};
      r.rectangle(card,palette.card);
      r.rectangle({card.x,card.y,card.w,2},glow(palette,Power::None));
      float y=card.y+20;
      r.rectangle({24,y,40,40},tile); icon(r,palette,Power::None,{44,y+20},34);
      r.text({76,y+10},"FIND THE GOAL",glow(palette,Power::None),2.5f);
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
        r.rectangle({24,y,40,40},tile); icon(r,palette,power,{44,y+20},34);
        r.text({76,y+4},powerNames[k],glow(palette,power),2);
        r.text({76,y+26},power==Power::Bomb ? "BREAKS THE "+square(model.bombSize)+" AROUND IT" : std::string(powerLines[k]),muted,1.25f);
        y+=54;
      }
      r.text({75,card.y+card.h-50},"TAP TO START",teal,2.5f);
    }
    if(model.over()) {
      r.rectangle(overlay,palette.card);
      r.text({58,367}, model.won() ? "GOAL FOUND" : "OUT OF BALLS",model.won() ? glow(palette,Power::None) : white,2.5f);
      if(model.won()) r.text({58,410}, "BALLS LEFT " + std::to_string(model.ballsLeft),teal);
      else r.text({58,410}, "THE GOAL STAYED HIDDEN",teal,1.5f);
      r.text({58,450}, "TAP TO RESTART",muted);
    }
    if(debugOpen) {
      r.rectangle(panel,palette.card);
      r.rectangle({panel.x,panel.y,panel.w,2},teal);
      r.text({panel.x+20,panel.y+16},"DEBUG",teal);
      r.text({panel.x+150,panel.y+18},"VERSION " YY_GAME_VERSION,muted,1.25f);
      const char* headings[]{"APPLY NOW","APPLY ON RESTART","POWER-UP WEIGHTS, ON RESTART"};
      for(int i=0; i<3; ++i) r.text({panel.x+20,headingTops[i]},headings[i],teal,1.5f);
      const auto row=[&](int index, const std::string& label, Color labelColor, const std::string& value) {
        const yy::Rect minus=minusButton(index), plus=plusButton(index);
        r.text({panel.x+20,minus.y+14},label,labelColor,1.5f);
        r.rectangle(minus,palette.button); r.rectangle(plus,palette.button);
        r.text({minus.x+14,minus.y+12},"-",white); r.text({plus.x+14,plus.y+12},"+",white);
        const float centre=(minus.x+minus.w+plus.x)/2, scale=value.size()>5 ? 1.75f : 2; // 60X100 fits between
        r.text({centre-4*scale*value.size(),minus.y+12},value,teal,scale);
      };
      row(PingRadius,"PING RADIUS",white,std::to_string(model.pingRadius));
      row(BombSize,"BOMB SIZE",white,square(model.bombSize));
      row(ZapSeconds,"ZAP SECONDS",white,std::to_string(model.electricSeconds));
      row(ZapReach,"ZAP REACH",white,halves(model.electricRadius));
      row(SnapAngle,"SNAP ANGLE",white,model.snapDegrees>0 ? std::to_string(model.snapDegrees)+" DEG" : std::string("OFF"));
      row(Balls,"BALLS",white,std::to_string(debugBalls));
      row(Bounces,"BOUNCES",white,std::to_string(debugBounces));
      row(GridSize,"GRID SIZE",white,std::to_string(Settings::shapeColumns*debugGrid.gridScale)+"X"+std::to_string(Settings::shapeRows*debugGrid.gridScale));
      row(GlowRate,"GLOWING",white,percent(debugGrid.glow));
      for(int k=1; k<=powerKinds; ++k) row(Weight0+k-1,weightNames[k],glow(palette,static_cast<Power>(k)),std::to_string(debugGrid.weights[k-1]));
      r.rectangle(colorsButton,palette.button);
      r.text({colorsButton.x+16,colorsButton.y+12},"COLORS",white);
      r.text({colorsButton.x+132,colorsButton.y+12},palette.name,teal);
      r.text({colorsButton.x+278,colorsButton.y+12},">",white);
      r.rectangle(restartButton,palette.teal); r.text({restartButton.x+19,restartButton.y+14},"RESTART",dark);
      r.rectangle(closeButton,palette.button); r.text({closeButton.x+35,closeButton.y+14},"CLOSE",white);
    }
  }
};
std::unique_ptr<yy::Game> createGame() { return std::make_unique<TapGame>(); }
}
