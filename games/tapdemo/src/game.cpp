#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <tapdemo/palette.hpp>
#include <tapdemo/garden.hpp>
#include <tapdemo/celebration.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#include <optional>
#include <string>
#include <vector>

namespace tapdemo {
namespace {
bool inside(yy::Rect r, yy::Vec2 p) { return p.x>=r.x && p.y>=r.y && p.x<r.x+r.w && p.y<r.y+r.h; }
// The header holds the ball counter, the level and DEBUG; the board starts below it (Camera::view).
constexpr yy::Rect debugButton{286,22,88,36};
// The debug panel covers the screen: fifteen stepper rows (a label, -, value, +) under three
// headings (what applies now; what RESTART applies: the level, or free play's settings; free
// play's weights), then RESTART and CLOSE.
constexpr yy::Rect panel{20,12,350,784};
enum Stepper { PingRadius, BombSize, ZapSeconds, ZapReach, SnapAngle, LevelPick, Balls, Bounces, GridSize, GlowRate, Weight0, steppers=Weight0+powerKinds };
constexpr float stepperTops[steppers]{66,108,150,192,234, 298,340,382,424,466, 530,572,614,656,698};
constexpr float headingTops[]{50,282,514};
constexpr yy::Rect minusButton(int row) { return {180,stepperTops[row],44,38}; }
constexpr yy::Rect plusButton(int row) { return {320,stepperTops[row],44,38}; }
constexpr yy::Rect restartButton{40,746,150,44}, closeButton{200,746,150,44};
constexpr const char* progressFile="progress.txt"; // the reached level, in yy::Storage
constexpr const char* debugFile="debug.txt";       // every debug panel setting, in yy::Storage
constexpr yy::Rect overlay{24,340,342,156};          // the OUT OF BALLS card
constexpr yy::Rect winCard{24,250,342,348};          // the win card: the instructions card's art, cut short
constexpr float cardArtWidth=1098, cardArtHeight=2232; // garden/card.bmp, in pixels
// The goal's tune: four short notes rising and a long last one. The audio queues them one after another.
constexpr float goalTune[][2]{{523.25f,0.1f},{659.25f,0.1f},{783.99f,0.1f},{1046.5f,0.1f},{1318.5f,0.4f}};
constexpr float petalGravity=420, petalDrag=1.6f;      // confetti, in logical units per second
constexpr float focusZoom=2.2f;                        // the camera's zoom on the ball and the goal
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
// A glowing (or goal) brick: its icon in a holder above a bar in its colour.
void iconBrick(yy::Renderer& r, const Palette& palette, Power power, yy::Rect cell) {
  const float inset=cell.w*0.045f;
  r.rectangle({cell.x+inset,cell.y+cell.h*0.82f,cell.w-2*inset,cell.h*0.14f},glow(palette,power));
  gardenSprite(r,GardenSprite::Holder,cell);
  const yy::Rect inner{cell.x+cell.w*0.20f,cell.y+cell.h*0.18f,cell.w*0.60f,cell.h*0.62f};
  if(power==Power::None) gardenSprite(r,GardenSprite::Flag,inner);
  else icon(r,palette,power,{inner.x+inner.w/2,inner.y+inner.h/2},inner.w);
}
}

class TapGame final: public yy::Game {
  Model model;
  Touch touch{model};
  yy::Audio* audio{};
  yy::Haptics* haptics{};
  bool debugOpen{};
  yy::Storage* storage{};
  bool saveDebugSettings{true}; // off while a smoke run pins a level or scene
  int freeBalls{Model::defaultBalls}, freeBounces{Model::defaultBounces};
  Settings freeGrid; // free play's settings; levels bring their own
  int debugLevel{}; // the picker: 1..levelCount, or 0 for free play
  int debugBalls{Model::defaultBalls}, debugBounces{Model::defaultBounces};
  Settings debugGrid; // pending until RESTART, like balls and bounces
  int uiFinger{-1}; // a finger the HUD took, kept from the board
  struct Burst { Fired fired; float age; };
  std::vector<Burst> bursts; // power-ups that just fired, while their rings grow
  struct Pop { int cell; float age; };
  std::vector<Pop> pops; // cosmetic only: never part of Model, input or the shot clock
  std::vector<int> lastBricks;
  float clock{};             // seconds, for glow pulses and sparks
  // The goal celebration (celebration.hpp): presentation only. The model always steps by the real frame's dt,
  // on the frames the scaled clock crosses a step, so slow motion cannot change where a ball goes.
  Celebration celebration;
  std::optional<Camera> framing; // the camera as the player left it, while the celebration has it
  yy::Vec2 focusWorld{}; bool focused{};
  float timeDebt{};          // game time earned by the scaled clock but not yet stepped
  float frameSeconds{1.0f/60};
  struct Petal { yy::Vec2 at, velocity; float size, spin, age, life; yy::Color color; bool leaf; };
  std::vector<Petal> petals; // confetti and falling petals, in screen units
  std::uint32_t petalState{20251006};
  float rainDebt{};          // petals still to fall from the top
  int countedBalls{};        // the win card's count-up
  bool cheered{};            // GOAL!'s haptic has played
  bool renderTest{};         // YY_TAPDEMO_SCENE=render-test: the renderer's own checks over the field
  bool frozenScene{}; // only an explicitly staged screenshot (mid-hit, a celebration beat), never ordinary play

  void reset(bool instructions) {
    bursts.clear(); pops.clear(); petals.clear(); frozenScene=false; lastBricks=model.bricks; touch.refit(); touch.instructions=instructions;
    celebration.reset(); framing.reset(); focused=false; timeDebt=0; rainDebt=0; countedBalls=0; cheered=false;
  }
  float unit() { petalState=petalState*1664525u+1013904223u; return static_cast<float>(petalState>>8)*(1.0f/16777216.0f); }
  yy::Vec2 goalCentre() const { return {(model.goal%model.columns+0.5f)*Model::cell,(model.goal/model.columns+0.5f)*Model::cell}; }
  // Where a flying ball is drawn: the model's position, plus the slow clock's share of the next step.
  yy::Vec2 shown(const Ball& b) const {
    if(timeDebt<=0) return b.position;
    const yy::Vec2 next{b.position.x+b.velocity.x*timeDebt,b.position.y+b.velocity.y*timeDebt};
    return model.canPlace(next) ? next : b.position;
  }
  // One frame of the model at the celebration's time scale. A frame that earns less than a step steps nothing.
  void stepModel(float seconds) {
    model.hits={};
    timeDebt+=seconds*celebration.scale();
    if(timeDebt>=seconds) { timeDebt-=seconds; model.update(seconds); }
  }
  void releaseCamera() {
    if(framing) { touch.camera=*framing; framing.reset(); }
    focused=false;
  }
  // Takes the camera while the celebration is engaged: the player's framing blended toward the ball, then the goal.
  void steer(const Anticipation& ahead, float seconds) {
    if(!celebration.engaged()) { releaseCamera(); return; }
    Camera& cam=touch.camera;
    if(!framing) framing=cam;
    const yy::Vec2 goalAt=goalCentre();
    yy::Vec2 target=focused ? focusWorld : goalAt;
    if(celebration.phase()==Celebration::Phase::Hit) target=goalAt;
    else if(ahead.hit && !model.balls.empty()) {
      const Ball* near=&model.balls.front(); float best=-1;
      for(const auto& b: model.balls) {
        const float d=std::hypot(b.position.x-goalAt.x,b.position.y-goalAt.y);
        if(best<0 || d<best) { near=&b; best=d; }
      }
      const yy::Vec2 ball=shown(*near);
      const float close=std::clamp(1-ahead.seconds/Celebration::window,0.0f,1.0f), mixed=close*close;
      target={ball.x+(goalAt.x-ball.x)*mixed,ball.y+(goalAt.y-ball.y)*mixed};
    }
    const float follow=focused ? 1-std::exp(-12*seconds) : 1;
    focusWorld={focusWorld.x+(target.x-focusWorld.x)*follow,focusWorld.y+(target.y-focusWorld.y)*follow};
    focused=true;
    const float k=celebration.focus();
    const yy::Vec2 centre{cam.view.x+cam.view.w/2,cam.view.y+cam.view.h/2}, from=framing->toWorld(centre);
    cam.hold({from.x+(focusWorld.x-from.x)*k,from.y+(focusWorld.y-from.y)*k},centre,
             framing->zoom*std::pow(std::max(1.0f,focusZoom/framing->zoom),k));
  }
  // The break: a bigger burst on the goal, confetti, the tune and a strong haptic.
  void celebrate() {
    bursts.push_back({{Power::None,model.goal,-1},0});
    timeDebt=0; cheered=false;
    const auto at=touch.camera.toScreen(goalCentre());
    const yy::Color white=garden.white;
    for(int i=0; i<80; ++i) {
      const float angle=unit()*6.2831853f, speed=140+unit()*460;
      const int pick=static_cast<int>(unit()*7);
      petals.push_back({at,{std::cos(angle)*speed,std::sin(angle)*speed-140},3+unit()*3.5f,unit()*6.2831853f,0,1.5f+unit()*1.0f,
                        pick<6 ? garden.glows[pick] : white,unit()<0.45f});
    }
    if(haptics) { haptics->thump(); haptics->impact(1); }
    if(audio) for(const auto& note: goalTune) audio->tone(note[0],note[1]);
  }
  // Petals fall across the screen for a while after the break.
  void rain(float seconds) {
    if(celebration.phase()!=Celebration::Phase::Hit || celebration.time()>1.4f) return;
    rainDebt+=seconds*55;
    const auto& view=touch.camera.view;
    for(; rainDebt>=1; --rainDebt) {
      const int pick=static_cast<int>(unit()*7);
      petals.push_back({{unit()*view.w,view.y-8},{(unit()-0.5f)*60,20+unit()*80},3+unit()*3.5f,unit()*6.2831853f,0,2.4f+unit()*1.2f,
                        pick<6 ? garden.glows[pick] : garden.white,unit()<0.6f});
    }
  }
  // A tap during the celebration: the shot still in flight finishes unseen, by the steps it would have taken, and the card shows.
  void skipCelebration() {
    if(!model.over()) {
      for(int i=0, n=static_cast<int>(Celebration::window*2/frameSeconds)+1; i<n && !model.over(); ++i) model.update(frameSeconds);
      lastBricks=model.bricks; pops.clear(); bursts.clear();
      if(!model.won()) { celebration.reset(); timeDebt=0; releaseCamera(); return; }
    }
    celebration.skip(); petals.clear(); timeDebt=0; countedBalls=0; releaseCamera();
  }
  // The app is pausing: leave no time scale or zoom behind. A celebration past the break goes to the card.
  void settle() {
    if(celebration.phase()==Celebration::Phase::Hit) { celebration.skip(); petals.clear(); countedBalls=0; }
    else celebration.reset();
    timeDebt=0; releaseCamera();
  }
  // The win card shows once the celebration has reached it; the loss card shows at once.
  bool cardShows() const { return model.over() && celebration.phase()!=Celebration::Phase::Hit; }
  yy::Rect cardRect() const { return model.won() ? winCard : overlay; }
  // Balls counted so far on the win card: after a short beat, one per step, a whole count within a second.
  int counted() const {
    if(!celebration.card()) return model.ballsLeft;
    const float step=std::min(0.2f,1.0f/std::max(1,model.ballsLeft));
    return std::min(model.ballsLeft,static_cast<int>(std::max(0.0f,celebration.time()-0.25f)/step));
  }
  // Opens a level on its instructions card; `save` records it as the reached level.
  void enter(int level, bool save=true) {
    touch.cancel(); model.play(level); reset(true);
    if(save && storage) storage->write(progressFile,saveProgress(model.level()));
  }
  // The same level again, straight into play: the same field, goal and Ghost landings.
  void retry() { touch.cancel(); model.restart(); reset(false); }
  void freePlay() { touch.cancel(); model.restart(freeBalls,freeBounces,freeGrid); reset(true); }
  // The end overlay's tap: the next level after a win, a retry after a loss, free play after
  // the last level or a free-play round.
  void advance() {
    const int next=model.level()>0 ? nextLevel(model.level(),model.won()) : 0;
    if(next==0) freePlay();
    else if(next==model.level()) retry();
    else enter(next);
  }
  // Writes every panel value as it stands, pending ones included, so a relaunch shows the panel as left.
  void persist() {
    if(!storage || !saveDebugSettings) return;
    DebugSettings d;
    d.pingRadius=model.pingRadius; d.bombSize=model.bombSize; d.electricSeconds=model.electricSeconds;
    d.electricHalves=static_cast<int>(std::lround(model.electricRadius*2)); d.snapDegrees=model.snapDegrees;
    d.balls=debugBalls; d.bounces=debugBounces; d.grid=debugGrid;
    storage->write(debugFile,saveDebug(d));
  }
  void openDebug() { touch.cancel(); debugLevel=model.level(); debugBalls=freeBalls; debugBounces=freeBounces; debugGrid=freeGrid; debugOpen=true; }
  void step(int row, int by) {
    switch(row) {
    case LevelPick: debugLevel=std::clamp(debugLevel+by,0,levelCount); break;
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
    persist();
  }
  void pressDebug(yy::Vec2 p) {
    for(int row=0; row<steppers; ++row) {
      if(inside(minusButton(row),p)) { step(row,-1); return; }
      if(inside(plusButton(row),p)) { step(row,1); return; }
    }
    if(inside(restartButton,p)) {
      freeBalls=debugBalls; freeBounces=debugBounces; freeGrid=debugGrid;
      if(debugLevel>0) enter(debugLevel); else freePlay();
      debugOpen=false;
    }
    else if(inside(closeButton,p) || !inside(panel,p)) debugOpen=false;
  }
  void sling(yy::Vec2 at, yy::Vec2 pull) { pointerDown(0,at); pointerMove(0,{at.x+pull.x,at.y+pull.y}); pointerUp(0,{at.x+pull.x,at.y+pull.y}); }
  // YY_TAPDEMO_SCENE stages a moment for smoke screenshots: instructions (as the game opens),
  // field (the opening field with the card dismissed, untouched), header (one ball flying), aim, snap (an aim 3 degrees off horizontal), debug, play, zoom, icons (one of each power-up and the goal
  // beside the pocket) or glow (the same close up), breaks (four launches), electric (a launch
  // into that power-up), pingin or pingout (a launch into a Ping brick with the goal inside or
  // outside the ping radius), won (a launch into the goal; won-approach, won-burst, won-goal and won-card freeze its celebration at a beat), lost (the last ball, spent on a brick), palette / palette-fit /
  // palette-max (all icons, a real Ping revealing fogged bricks, frozen at activation),
  // grid-min or grid-max (the debug steppers pick free play and the smallest or largest grid, then RESTART),
  // render-test (the opening field under soft-edged cutouts, a cropped sheet cell and baked-font text).
  // YY_TAPDEMO_LEVEL (1..10, or 0 for free play) opens that level instead of the saved one, and saves nothing.
  void stage(const char* scene) {
    if(!scene || model.pockets.empty() || std::strcmp(scene,"instructions")==0) return;
    if(std::strncmp(scene,"grid-",5)==0) {
      const auto press=[&](yy::Rect b) { const yy::Vec2 p{b.x+b.w/2,b.y+b.h/2}; pointerDown(0,p); pointerUp(0,p); };
      press(debugButton);
      for(int i=0; i<levelCount; ++i) press(minusButton(LevelPick));
      const yy::Rect button=std::strcmp(scene,"grid-min")==0 ? minusButton(GridSize) : plusButton(GridSize);
      for(int i=0; i<Settings::maxScale; ++i) press(button);
      press(restartButton);
      touch.instructions=false;
      return;
    }
    touch.instructions=false;
    if(std::strcmp(scene,"field")==0) return;
    if(std::strcmp(scene,"render-test")==0) { renderTest=true; return; }
    const auto& pocket=model.pockets.front();
    const yy::Vec2 centre{(pocket.column+pocket.columns/2.0f)*Model::cell, (pocket.row+pocket.rows/2.0f)*Model::cell};
    const int column=pocket.column+pocket.columns/2;
    const yy::Vec2 below{(column+0.5f)*Model::cell,centre.y}; // in the pocket, under the brick at (column, pocket.row-1)
    const auto set=[&](int c, int r, Power power) { model.bricks[r*model.columns+c]=1; model.powers[r*model.columns+c]=power; model.refreshFog(); };
    const auto setGoal=[&](int c, int r) { set(c,r,Power::None); model.goal=r*model.columns+c; };
    // Garden evidence fixtures alter only explicitly pinned smoke scenes. Shots still use
    // the real pointer handlers, collision and power activation paths.
    if(std::strncmp(scene,"garden-",7)==0) {
      touch.camera.hold(centre,{195,480},1.4f);
      if(std::strcmp(scene,"garden-damage")==0) {
        for(int i=0; i<3; ++i) {
          const int c=pocket.column+i;
          model.bricks[(pocket.row-1)*model.columns+c]=3-i;
          model.powers[(pocket.row-1)*model.columns+c]=Power::None;
        }
        model.refreshFog(); return;
      }
      Power power=Power::None;
      for(int k=1; k<=powerKinds; ++k) {
        const std::string name="garden-"+std::string(weightNames[k]);
        if(name==scene) power=static_cast<Power>(k);
      }
      set(column,pocket.row-1,power);
      if(power==Power::None) model.bricks[(pocket.row-1)*model.columns+column]=3;
      lastBricks=model.bricks;
      sling(touch.camera.toScreen(below),{0,40});
      if(!model.balls.empty()) model.balls.back().bounces=1;
      if(std::strcmp(scene,"garden-hit")==0) {
        for(int i=0; i<120 && pops.empty(); ++i) update(1.0f/60);
        update(0.075f);
        const auto at=touch.camera.toScreen(below);
        pointerDown(9,at); pointerMove(9,{at.x+40,at.y+70});
        frozenScene=true;
      }
      return;
    }
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
        model.pause(true); // hold the actual reveal and red ball
        if(std::strcmp(scene,"palette-fit")==0) touch.camera.fit();
        else touch.camera.hold(centre,{195,480},std::strcmp(scene,"palette-max")==0 ? Camera::maxZoom : 1.25f);
      }
      if(scene[0]=='g') touch.camera.hold(centre,{195,480},2.0f);
      return;
    }
    if(std::strncmp(scene,"fogedge",7)==0) {
      // Screenshot fixture for the faded fog ring: hit-point bricks, power-ups and the goal three steps from the
      // pocket, so they sit in the first fogged ring. Fog and Ping rules are untouched.
      const int row=pocket.row-3;
      const auto hit=[&](int c,int hp) { set(c,row,Power::None); model.bricks[row*model.columns+c]=hp; };
      hit(column-2,3); hit(column-1,2);
      set(column,row,Power::Bomb); set(column+1,row,Power::Ping);
      setGoal(column+2,row);
      set(pocket.column+pocket.columns+2,pocket.row,Power::Speed);
      lastBricks=model.bricks;
      if(std::strcmp(scene,"fogedge-fit")==0) touch.camera.fit();
      else touch.camera.hold({centre.x,(row+1.0f)*Model::cell},{195,480},std::strcmp(scene,"fogedge-near")==0 ? 2.0f : 3.0f);
      return;
    }
    if(std::strcmp(scene,"breaks")==0) {
      const yy::Vec2 at=touch.camera.toScreen(centre);
      for(yy::Vec2 pull: {yy::Vec2{20,40},{-40,15},{5,-40},{40,-10}}) sling(at,pull);
      return;
    }
    if(std::strcmp(scene,"lost")==0) {
      const int above=(pocket.row-1)*model.columns+column;
      model.bricks[above]=3; model.powers[above]=Power::None; // takes the hit without breaking
      model.ballsLeft=1; sling(touch.camera.toScreen(below),{0,40});
      if(!model.balls.empty()) model.balls.back().bounces=1; // spent on the brick above the pocket
      return;
    }
    if(std::strcmp(scene,"won")==0) { setGoal(column,pocket.row-1); sling(touch.camera.toScreen(below),{0,40}); return; }
    // The celebration's beats, each frozen where the screenshot is taken. The goal is eight cells up the pocket's column with
    // the cells between cleared, so the ball is seen for the whole look-ahead before the hit; the shot is a real sling.
    if(std::strncmp(scene,"won-",4)==0) {
      const int goalRow=std::max(1,pocket.row-9);
      for(int row=goalRow+1; row<pocket.row; ++row) model.bricks[row*model.columns+column]=0;
      setGoal(column,goalRow); lastBricks=model.bricks;
      sling(touch.camera.toScreen(below),{0,40});
      const std::string beat=scene+4;
      const auto runTo=[&](auto reached) { for(int i=0; i<1800 && !reached(); ++i) update(1.0f/60); frozenScene=true; };
      using Phase=Celebration::Phase;
      if(beat=="approach") runTo([&]{ return celebration.focus()>0.8f && celebration.scale()<=0.26f; });
      else if(beat=="burst") runTo([&]{ return celebration.phase()==Phase::Hit && celebration.time()>=0.25f; });
      else if(beat=="goal") runTo([&]{ return celebration.phase()==Phase::Hit && celebration.time()>=1.5f; });
      else if(beat=="card") runTo([&]{ return celebration.card() && celebration.time()>=1.2f; });
      return;
    }
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
    audio=&services.audio; haptics=&services.haptics; touch.haptics=haptics; storage=&services.storage;
    // The app opens at the reached level with the debug settings it was left with. A smoke run can
    // pin a level or a scene: both use the defaults and neither touches a save.
    const char* pinned=std::getenv("YY_TAPDEMO_LEVEL"), *scene=std::getenv("YY_TAPDEMO_SCENE");
    saveDebugSettings=!pinned && !scene;
    const bool saved=!pinned && !scene;
    // Before the first grid: free play's grid, balls and bounces come from the save.
    const std::string debugText=saved ? storage->read(debugFile) : std::string();
    const DebugSettings d=saved ? loadDebug(debugText) : DebugSettings{};
    freeBalls=debugBalls=d.balls; freeBounces=debugBounces=d.bounces; freeGrid=debugGrid=d.grid;
    if(pinned) { if(std::atoi(pinned)>0) enter(std::atoi(pinned),false); }
    else enter(loadProgress(storage->read(progressFile)),false);
    // A level's table sets the power-up values, so the saved ones go on after it.
    if(saved) { model.setPingRadius(d.pingRadius); model.setBombSize(d.bombSize); model.setElectricSeconds(d.electricSeconds);
                model.setElectricRadius(d.electricHalves/2.0f); }
    model.setSnapDegrees(d.snapDegrees);
    stage(scene);
    lastBricks=model.bricks;
  }
  void update(float seconds) override {
    if(frozenScene) return;
    frameSeconds=seconds;
    // A copy of the model runs ahead: when it sees the goal break, time slows and the camera pushes in.
    const Anticipation ahead=model.over() ? Anticipation{} : lookAhead(model,seconds,Celebration::window);
    const bool playing=celebration.playing();
    celebration.step(seconds,ahead.hit);
    if(!playing && celebration.playing()) touch.cancel(); // a held aim gives way to the show
    stepModel(seconds); touch.update(seconds);
    for(auto& p: pops) p.age+=seconds;
    std::erase_if(pops,[](const Pop& p){ return p.age>=popSeconds; });
    if(lastBricks.size()==model.bricks.size()) for(std::size_t i=0; i<lastBricks.size(); ++i) {
      if(model.bricks[i]<lastBricks[i]) {
        const auto found=std::find_if(pops.begin(),pops.end(),[&](const Pop& p){ return p.cell==static_cast<int>(i); });
        if(found!=pops.end()) found->age=0;
        else pops.push_back({static_cast<int>(i),0});
      }
    }
    lastBricks=model.bricks;
    clock+=seconds;
    for(auto& b: bursts) b.age+=seconds;
    bursts.erase(std::remove_if(bursts.begin(),bursts.end(),[](const Burst& b){ return b.age>1.2f; }),bursts.end());
    // The goal's celebration, or a power-up's thump and tone, stands in for the plain break's tap that frame.
    for(const auto& f: model.hits.fired) bursts.push_back({f,0});
    if(model.hits.goalBroken) { celebration.hit(); celebrate(); }
    else if(!model.hits.fired.empty()) {
      const auto& tone=powerTones[static_cast<int>(model.hits.fired.front().power)];
      if(haptics) haptics->thump();
      if(audio) audio->tone(tone[0],tone[1]);
    } else if(model.hits.bricksBroken>0) {
      if(haptics) haptics->impact(std::min(1.0f,0.55f+0.15f*(model.hits.bricksBroken-1)));
      if(audio) audio->tone(660.0f,0.05f);
    }
    rain(seconds);
    for(auto& p: petals) {
      p.age+=seconds; p.velocity.y+=petalGravity*seconds;
      const float drag=std::max(0.0f,1-petalDrag*seconds);
      p.velocity.x*=drag; p.velocity.y*=drag;
      p.at.x+=(p.velocity.x+std::sin(p.age*4+p.spin)*28)*seconds; p.at.y+=p.velocity.y*seconds;
    }
    std::erase_if(petals,[](const Petal& p){ return p.age>=p.life; });
    if(celebration.phase()==Celebration::Phase::Hit && !cheered && celebration.time()>=Celebration::goalTextAt) {
      cheered=true;
      if(haptics) haptics->impact(0.8f);
    }
    if(celebration.card()) {
      const int count=counted();
      if(count>countedBalls) {
        countedBalls=count;
        if(audio) audio->tone(660+60.0f*count,0.05f);
        if(haptics) haptics->impact(0.4f);
      }
    }
    steer(ahead,seconds);
  }
  void pause(bool value) override { model.pause(value); if(value) { touch.cancel(); settle(); } }
  void shutdown() override { touch.cancel(); touch.haptics=nullptr; audio=nullptr; haptics=nullptr; }
  void tap(yy::Vec2) override {}
  void pointerDown(int id, yy::Vec2 p) override {
    if(debugOpen) { uiFinger=id; pressDebug(p); return; }
    if(inside(debugButton,p)) { uiFinger=id; openDebug(); return; }
    // One tap anywhere skips the celebration to the card; that finger does nothing more.
    if(celebration.playing()) { uiFinger=id; skipCelebration(); return; }
    // An anticipation that came to nothing, still easing back, gives way to the player.
    if(celebration.engaged()) { celebration.reset(); timeDebt=0; releaseCamera(); }
    if(cardShows() && inside(cardRect(),p)) { uiFinger=id; advance(); return; }
    touch.down(id,p);
  }
  void pointerMove(int id, yy::Vec2 p) override { if(id!=uiFinger && !celebration.engaged()) touch.move(id,p); }
  void pointerUp(int id, yy::Vec2 p) override {
    if(id==uiFinger) { uiFinger=-1; return; }
    touch.up(id,p);
  }
  void zoom(yy::Vec2 at, float steps) override {
    if(!debugOpen && !touch.instructions && !celebration.engaged() && touch.camera.contains(at)) touch.camera.zoomAt(at,touch.camera.zoom*std::pow(1.15f,steps));
  }
  void render(yy::Renderer& r) override {
    using yy::Color;
    const Palette& palette=garden;
    const auto muted=palette.muted, teal=palette.teal, dark=palette.dark;
    const auto ballRed=palette.ballRed, field=palette.field, white=palette.white, tile=palette.tile;
    const Camera& cam=touch.camera;
    const float z=cam.zoom, cellSize=Model::cell*z;
    const auto origin=cam.toScreen({0,0});
    // At fit zoom the grid leaves margins; give those the backdrop too.
    r.rectangle({0,0,cam.view.w,cam.view.y+cam.view.h},dark);
    r.rectangle({origin.x,origin.y,model.width()*z,model.height()*z},field);
    // A mist patch spans eight cells, as in the concept: dew stays soft instead of
    // becoming a small repeated pattern. Visible cells cover it with dark soil below.
    for(int row=0; row<model.rows; row+=gardenTextureCells) for(int column=0; column<model.columns; column+=gardenTextureCells) {
      const int cols=std::min(gardenTextureCells,model.columns-column), rows=std::min(gardenTextureCells,model.rows-row);
      const auto at=cam.toScreen({column*Model::cell,row*Model::cell});
      auto source=gardenSource(GardenSprite::Mist);
      source.w*=static_cast<float>(cols)/gardenTextureCells; source.h*=static_cast<float>(rows)/gardenTextureCells;
      r.sprite("garden/tiles.bmp",source,{at.x,at.y,cols*cellSize,rows*cellSize});
    }

    // Bricks in view, with their hit points once the cells are big enough to read.
    const auto first=cam.toWorld({cam.view.x,cam.view.y}), last=cam.toWorld({cam.view.x+cam.view.w,cam.view.y+cam.view.h});
    const int c0=std::max(0,static_cast<int>(first.x/Model::cell)), c1=std::min(model.columns-1,static_cast<int>(last.x/Model::cell));
    const int r0=std::max(0,static_cast<int>(first.y/Model::cell)), r1=std::min(model.rows-1,static_cast<int>(last.y/Model::cell));
    const float gap=std::max(1.0f,cellSize*0.06f);
    // Fogged cells hide their bricks and icons, except glowing bricks and the goal within a ping.
    const auto& fogColors=palette.fog;
    // A brick with its hit points, or the icon brick of a power-up or the goal.
    const auto cellContents=[&](int column,int row,yy::Vec2 p,yy::Rect box,int hp,bool goal,Power power) {
      if(goal || power!=Power::None) { iconBrick(r,palette,power,box); return; }
      float squash=0;
      for(const auto& pop: pops) if(pop.cell==row*model.columns+column) {
        squash=std::sin(std::min(1.0f,pop.age/0.12f)*3.14159265f); break;
      }
      const float w=cellSize-gap-cellSize*0.06f*squash, h=cellSize-gap-cellSize*0.16f*squash;
      gardenSprite(r,gardenDamage(hp),{p.x+(cellSize-w)/2,p.y+(cellSize-h)/2,w,h});
    };
    for(int row=r0; row<=r1; ++row) for(int column=c0; column<=c1; ++column) {
      const int hp=model.brick(column,row);
      const bool goal=hp>0 && model.isGoal(column,row);
      const Power power=model.power(column,row);
      const auto p=cam.toScreen({column*Model::cell,row*Model::cell});
      const yy::Rect box{p.x,p.y,cellSize,cellSize};
      if(!model.visible(column,row)) {
        if(model.fogDistance(column,row)==Model::fogReach+1) {
          // The first fogged ring fades in: contents first, then fog in thin strips, faint on
          // the side facing a clear cell and nearly solid on the side facing deeper fog.
          if(hp>0) cellContents(column,row,p,box,hp,goal,power);
          const auto clear=[&](int c,int rr) { return c>=0 && rr>=0 && c<model.columns && rr<model.rows && model.visible(c,rr); };
          const bool up=clear(column,row-1), down=clear(column,row+1), left=clear(column-1,row), right=clear(column+1,row);
          const auto base=fogColors[(column+row)%2];
          const auto tint=[&](float proximity) { auto c=base; c.a=static_cast<unsigned char>(fogEdgeFaint+(fogEdgeSolid-fogEdgeFaint)*(1-proximity)); return c; };
          // Proximity of a point in the cell to its nearest clear side: 1 on it, 0 a cell away.
          const auto closeness=[&](float u,float v) {
            float best=0;
            if(up) best=std::max(best,1-v);
            if(down) best=std::max(best,v);
            if(left) best=std::max(best,1-u);
            if(right) best=std::max(best,u);
            return best;
          };
          const bool vertical=up||down, horizontal=left||right;
          const float step=1.0f/fogEdgeStrips;
          // One axis: strips across the cell. Two axes (a corner, or a dead end): a small tile grid.
          for(int j=0; j<(vertical ? fogEdgeStrips : 1); ++j) for(int i=0; i<(horizontal ? fogEdgeStrips : 1); ++i) {
            const float u0=horizontal ? i*step : 0, v0=vertical ? j*step : 0, w=horizontal ? step : 1, h=vertical ? step : 1;
            r.rectangle({p.x+u0*cellSize,p.y+v0*cellSize,w*cellSize,h*cellSize},tint(closeness(u0+w/2,v0+h/2)));
          }
          if((goal || power!=Power::None) && model.pinged(column,row)) iconBrick(r,palette,power,box);
          continue;
        }
        const float rim=std::clamp(cellSize*0.045f,1.0f,3.0f);
        const auto exposed=[&](int c,int rr) { return c>=0 && rr>=0 && c<model.columns && rr<model.rows && model.visible(c,rr); };
        if(exposed(column,row-1)) gardenSprite(r,GardenSprite::RimH,{p.x,p.y,cellSize,rim});
        if(exposed(column,row+1)) gardenSprite(r,GardenSprite::RimH,{p.x,p.y+cellSize-rim,cellSize,rim});
        if(exposed(column-1,row)) gardenSprite(r,GardenSprite::RimV,{p.x,p.y,rim,cellSize});
        if(exposed(column+1,row)) gardenSprite(r,GardenSprite::RimV,{p.x+cellSize-rim,p.y,rim,cellSize});
        if((goal || power!=Power::None) && model.pinged(column,row)) iconBrick(r,palette,power,box);
        continue;
      }
      gardenCellTexture(r,GardenSprite::Clear,column,row,box);
      if(hp>0) cellContents(column,row,p,box,hp,goal,power);
    }
    // The entire 200 ms flash/clipping sequence stays inside the struck cell. Aim and balls
    // draw after it, so a second shot can be held and released while the previous hit settles.
    for(const auto& pop: pops) {
      const int c=pop.cell%model.columns, row=pop.cell/model.columns;
      if(c<c0 || c>c1 || row<r0 || row>r1 || !model.visible(c,row)) continue;
      const auto p=cam.toScreen({c*Model::cell,row*Model::cell});
      const auto frame=pop.age<0.05f ? GardenSprite::Flash : pop.age<0.12f ? GardenSprite::Burst : GardenSprite::Clippings;
      gardenSprite(r,frame,{p.x,p.y,cellSize,cellSize});
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
      const float t=b.age/1.2f;
      if(b.fired.power==Power::None) { drawGoalBurst(r,palette,b.age); continue; }
      const float reach=(b.fired.power==Power::Bomb ? model.bombSize*0.5f+0.1f : 0.9f)*Model::cell*z*(0.4f+t);
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
      const auto at=shown(b);
      const auto p=cam.toScreen(at);
      if(b.fast) for(int i=3; i>=1; --i)
        r.circle(cam.toScreen({at.x-b.velocity.x*0.012f*i,at.y-b.velocity.y*0.012f*i}),ballSize*(1-0.2f*i),mix(streak,field,0.25f*i));
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
      gardenSprite(r,GardenSprite::Ball,{p.x-ballSize,p.y-ballSize,2*ballSize,2*ballSize});
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
      gardenSprite(r,GardenSprite::Ball,{anchor.x-ballSize,anchor.y-ballSize,2*ballSize,2*ballSize});
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
    r.sprite("garden/header.bmp",{4,4,382,72});
    r.circle({36,40},17,white);
    gardenSprite(r,GardenSprite::Ball,{20,24,32,32});
    gardenLabel(r,{62,13},std::to_string(model.ballsLeft),48,white);
    gardenLabel(r,{150,29},model.level()>0 ? "LEVEL "+std::to_string(model.level()) : "FREE PLAY",20,white);
    r.rectangle(debugButton,palette.button);
    gardenLabel(r,{330,30},"DEBUG",17,white,yy::Align::Center);

    if(touch.instructions) {
      const yy::Rect card{12,cam.view.y+10,cam.view.w-24,cam.view.h-20};
      drawGardenCard(r,card);
    }
    if(cardShows()) {
      if(model.won()) drawWinCard(r);
      else {
        r.rectangle(overlay,palette.card);
        r.text({58,367},"OUT OF BALLS",white,2.5f);
        r.text({58,410},"THE GOAL STAYED HIDDEN",teal,1.5f);
        r.text({58,450},model.level()==0 ? "TAP TO RESTART" : "TAP TO RETRY",muted);
      }
    }
    drawPetals(r);
    if(celebration.goalText()>0) drawGoalText(r,cam.view);
    if(debugOpen) {
      r.rectangle(panel,palette.card);
      r.rectangle({panel.x,panel.y,panel.w,2},teal);
      r.text({panel.x+20,panel.y+16},"DEBUG",teal);
      r.text({panel.x+150,panel.y+18},"VERSION " YY_GAME_VERSION,muted,1.25f);
      const char* headings[]{"APPLY NOW","APPLY ON RESTART","FREE PLAY WEIGHTS"};
      for(int i=0; i<3; ++i) r.text({panel.x+20,headingTops[i]},headings[i],teal,1.5f);
      const auto row=[&](int index, const std::string& label, Color labelColor, const std::string& value) {
        const yy::Rect minus=minusButton(index), plus=plusButton(index);
        r.text({panel.x+20,minus.y+14},label,labelColor,1.5f);
        r.rectangle(minus,palette.button); r.rectangle(plus,palette.button);
        r.text({minus.x+14,minus.y+11},"-",white); r.text({plus.x+14,plus.y+11},"+",white);
        const float centre=(minus.x+minus.w+plus.x)/2, scale=value.size()>5 ? 1.75f : 2; // 60X100 fits between
        r.text({centre-4*scale*value.size(),minus.y+12},value,teal,scale);
      };
      row(PingRadius,"PING RADIUS",white,std::to_string(model.pingRadius));
      row(BombSize,"BOMB SIZE",white,square(model.bombSize));
      row(ZapSeconds,"ZAP SECONDS",white,std::to_string(model.electricSeconds));
      row(ZapReach,"ZAP REACH",white,halves(model.electricRadius));
      row(SnapAngle,"SNAP ANGLE",white,model.snapDegrees>0 ? std::to_string(model.snapDegrees)+" DEG" : std::string("OFF"));
      row(LevelPick,"LEVEL",white,debugLevel>0 ? std::to_string(debugLevel) : std::string("FREE"));
      // Free play's settings; with a level picked they wait, muted, for the next free play.
      const Color freeLabel=debugLevel>0 ? muted : white;
      row(Balls,"BALLS",freeLabel,std::to_string(debugBalls));
      row(Bounces,"BOUNCES",freeLabel,std::to_string(debugBounces));
      row(GridSize,"GRID SIZE",freeLabel,std::to_string(Settings::shapeColumns*debugGrid.gridScale)+"X"+std::to_string(Settings::shapeRows*debugGrid.gridScale));
      row(GlowRate,"GLOWING",freeLabel,percent(debugGrid.glow));
      for(int k=1; k<=powerKinds; ++k) row(Weight0+k-1,weightNames[k],debugLevel>0 ? muted : glow(palette,static_cast<Power>(k)),std::to_string(debugGrid.weights[k-1]));
      r.rectangle(restartButton,palette.teal); r.text({restartButton.x+19,restartButton.y+14},"RESTART",dark);
      r.rectangle(closeButton,palette.button); r.text({closeButton.x+35,closeButton.y+14},"CLOSE",white);
    }
    if(renderTest) drawRenderTest(r);
  }
  // The goal brick's burst, bigger than a power-up's: a flash, a gold glow and three rings.
  void drawGoalBurst(yy::Renderer& r, const Palette& palette, float age) const {
    const Camera& cam=touch.camera;
    const auto at=cam.toScreen(goalCentre());
    const float cell=Model::cell*cam.zoom;
    const yy::Color gold=glow(palette,Power::Electricity), pink=glow(palette,Power::None);
    if(age<0.45f) {
      const float fade=1-age/0.45f;
      yy::Color halo=gold; halo.a=static_cast<unsigned char>(150*fade);
      r.circle(at,cell*(1.2f+3.2f*age/0.45f),halo);
      gardenSprite(r,age<0.1f ? GardenSprite::Flash : GardenSprite::Burst,{at.x-cell*2,at.y-cell*2,cell*4,cell*4});
    }
    for(int ring=0; ring<3; ++ring) {
      const float lag=ring*0.12f, u=std::clamp((age-lag)/(1.2f-lag),0.0f,1.0f);
      if(u<=0 || u>=1) continue;
      yy::Color c=mix(ring==1 ? pink : gold,palette.white,0.25f); c.a=static_cast<unsigned char>(255*(1-u));
      const float reach=cell*(0.8f+4.5f*u), dot=std::max(2.0f,cell*0.14f*(1-u));
      for(int i=0; i<28; ++i) {
        const float a=i*6.2831853f/28+ring*0.2f;
        r.circle({at.x+std::cos(a)*reach,at.y+std::sin(a)*reach},dot,c);
      }
    }
  }
  // Confetti is little rectangles that flip as they fall and rounded petals, fading out at the end of their life.
  void drawPetals(yy::Renderer& r) const {
    for(const auto& p: petals) {
      yy::Color c=p.color; c.a=static_cast<unsigned char>(255*std::clamp((p.life-p.age)/0.4f,0.0f,1.0f));
      if(p.leaf) {
        const float s=p.size*(0.7f+0.3f*std::fabs(std::sin(p.spin+p.age*5)));
        r.circle(p.at,s,c);
        r.circle({p.at.x+std::cos(p.spin+p.age*3)*s*0.8f,p.at.y+std::sin(p.spin+p.age*3)*s*0.8f},s*0.75f,c);
      } else {
        const float w=std::max(1.0f,p.size*2*std::fabs(std::cos(p.spin+p.age*8))), h=p.size*1.3f;
        r.rectangle({p.at.x-w/2,p.at.y-h/2,w,h},c);
      }
    }
  }
  // A big GOAL! that pops in over the field once the camera starts back.
  void drawGoalText(yy::Renderer& r, yy::Rect view) const {
    const float p=celebration.goalText(), back=1.70158f;
    const float pop=1+(back+1)*std::pow(p-1,3)+back*std::pow(p-1,2); // ease out with a little overshoot
    const float size=100*pop*(1+0.02f*std::sin(celebration.time()*9));
    const yy::Vec2 at{195,view.y+view.h*0.36f-size*0.62f};
    const auto& palette=garden;
    gardenLabel(r,{at.x,at.y+9},"GOAL!",size,palette.dark,yy::Align::Center);
    gardenLabel(r,{at.x,at.y+5},"GOAL!",size,glow(palette,Power::None),yy::Align::Center);
    gardenLabel(r,at,"GOAL!",size,glow(palette,Power::Electricity),yy::Align::Center);
  }
  // The win card, in the instructions card's art cut to a short card at the same scale: the title, the
  // balls left counting up from 0, and the action.
  void drawWinCard(yy::Renderer& r) const {
    constexpr yy::Color green{46,122,48}, mutedInk{88,105,66};
    const float scale=winCard.w/cardArtWidth, slice=winCard.h/2, source=slice/scale;
    r.sprite("garden/card.bmp",{0,0,cardArtWidth,source},{winCard.x,winCard.y,winCard.w,slice});
    r.sprite("garden/card.bmp",{0,cardArtHeight-source,cardArtWidth,source},{winCard.x,winCard.y+slice,winCard.w,slice});
    const float cx=winCard.x+winCard.w/2, y=winCard.y;
    gardenLabel(r,{cx,y+34},"GOAL FOUND",36,green,yy::Align::Center);
    gardenLabel(r,{cx,y+80},model.level()>0 ? "LEVEL "+std::to_string(model.level()) : "FREE PLAY",17,mutedInk,yy::Align::Center);
    gardenSprite(r,GardenSprite::Ball,{cx-104,y+122,64,64});
    gardenLabel(r,{cx+22,y+104},std::to_string(counted()),88,green,yy::Align::Center);
    gardenLabel(r,{cx,y+206},"BALLS LEFT",18,mutedInk,yy::Align::Center);
    const int level=model.level(), next=level>0 ? nextLevel(level,true) : 0;
    gardenLabel(r,{cx,y+248},level==0 ? "TAP TO RESTART" : next==0 ? "TAP FOR FREE PLAY" : "TAP FOR NEXT LEVEL",24,green,yy::Align::Center);
  }
  // The level's expected win rate, as the simulated player measured it (T11, docs/levels.md).
  std::string expectedWins() const { return "EXPECTED WINS "+std::to_string(levels[model.level()-1].expectedWins)+"%"; }
  void drawGardenCard(yy::Renderer& r, yy::Rect card) {
    constexpr yy::Color ink{47,75,35}, green{46,122,48}, mutedInk{88,105,66};
    r.sprite("garden/card.bmp",card);
    float y=card.y+16;
    gardenLabel(r,{26,y},model.level()>0 ? "LEVEL "+std::to_string(model.level()) : "FREE PLAY",21,green);
    if(model.level()>0) gardenLabel(r,{140,y+3},expectedWins(),15,mutedInk); // left of the corner leaf
    y+=34;
    iconBrick(r,garden,Power::None,{26,y,40,40});
    gardenLabel(r,{78,y+4},"FIND THE GOAL",25,green);
    y+=53;
    const std::string limits=std::to_string(model.ballCount)+" BALLS, "+std::to_string(model.bouncesPerBall)+" BOUNCES EACH.";
    for(const std::string& line: {std::string("BREAK THE GOAL BRICK TO WIN."),std::string("IT HIDES IN THE FOG."),std::string(),
                                 std::string("HOLD IN A GAP, PULL, LET GO."),limits,std::string("NO BALLS LEFT: YOU LOSE.")}) {
      gardenLabel(r,{26,y},line,15,ink); y+=19;
    }
    y+=10;
    const auto powerRow=[&](Power power) {
      const int k=static_cast<int>(power);
      iconBrick(r,garden,power,{26,y,36,36});
      gardenLabel(r,{76,y},powerNames[k],18,ink);
      gardenLabel(r,{76,y+21},power==Power::Bomb ? "BREAKS THE "+square(model.bombSize)+" AROUND IT" : std::string(powerLines[k]),11.5f,mutedInk);
      y+=44;
    };
    const Power introduced=model.level()>0 ? levels[model.level()-1].introduces : Power::None;
    if(introduced!=Power::None) { gardenLabel(r,{26,y},"NEW POWER-UP",17,green); y+=24; powerRow(introduced); y+=4; }
    bool heading=false;
    for(int k=1; k<=powerKinds; ++k) {
      const Power power=static_cast<Power>(k);
      if(power==introduced || (model.level()>0 && model.settings.weights[k-1]==0)) continue;
      if(!heading) { gardenLabel(r,{26,y},introduced!=Power::None ? "ALSO HERE" : "POWER-UPS",17,green); y+=24; heading=true; }
      powerRow(power);
    }
    const float teaching=card.y+card.h-192;
    gardenLabel(r,{195,teaching},"EACH HIT STRIPS A LAYER",18,green,yy::Align::Center);
    for(int i=0; i<3; ++i) {
      gardenSprite(r,gardenDamage(3-i),{78.0f+i*86,teaching+31,58,58});
      if(i<2) gardenLabel(r,{144.0f+i*86,teaching+46},">",20,green);
    }
    gardenLabel(r,{195,teaching+102},"LAST HIT OPENS THE PATH",13,mutedInk,yy::Align::Center);
    gardenLabel(r,{195,card.y+card.h-51},"TAP TO START",26,green,yy::Align::Center);
  }
  // Not game art: the alpha edges, sheet cropping and font sizes a reskin depends on, at the header's
  // size (40) and the card's (16), over the dark field and over a light card.
  static void drawRenderTest(yy::Renderer& r) {
    constexpr const char* font="fonts/fredoka";
    constexpr yy::Rect leaf{0,0,64,64}; // the sheet's top-left cell; the others are magenta
    const yy::Color white{255,255,255}, sun{255,214,92}, ink{40,84,48}, cream{250,244,226};
    r.label(font,{195,92},"Garden Pop",40,white,yy::Align::Center);
    r.label(font,{24,142},"0123456789",40,sun);
    r.sprite("render-test/cutout.bmp",{20,200,120,120});
    r.sprite("render-test/cutout.bmp",{150,232,56,56});
    r.sprite("render-test/sheet.bmp",leaf,{220,200,150,150});
    const yy::Rect card{12,360,366,270};
    r.rectangle(card,cream);
    r.label(font,{28,372},"Find the goal",40,ink);
    r.label(font,{28,428},"Break the goal brick to win. It hides",16,ink);
    r.label(font,{28,450},"in the fog: 3 balls, 5 bounces each.",16,ink);
    r.label(font,{195,480},"Tap to start",16,{30,150,130},yy::Align::Center);
    r.sprite("render-test/cutout.bmp",{28,512,96,96});
    r.sprite("render-test/sheet.bmp",leaf,{136,528,64,64});
    r.sprite("render-test/cutout.bmp",{214,540,40,40});
    r.label(font,{360,560},"12",40,ink,yy::Align::Right);
  }
};
std::unique_ptr<yy::Game> createGame() { return std::make_unique<TapGame>(); }
}
