#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <yy/runtime.hpp>
#include <cstdlib>
#include <cstring>
#include <string>

namespace tapdemo {
namespace {
bool inside(yy::Rect r, yy::Vec2 p) { return p.x>=r.x && p.y>=r.y && p.x<r.x+r.w && p.y<r.y+r.h; }
constexpr yy::Rect debugButton{292,22,80,30};
constexpr yy::Rect panel{35,250,320,330};
constexpr yy::Rect ballsMinus{185,320,44,40}, ballsPlus{301,320,44,40};
constexpr yy::Rect bouncesMinus{185,390,44,40}, bouncesPlus{301,390,44,40};
constexpr yy::Rect restartButton{55,470,135,46}, closeButton{200,470,135,46};
constexpr yy::Rect overlay{24,340,342,156};
}

class TapGame final: public yy::Game {
  Model model;
  Touch touch{model};
  yy::Audio* audio{};
  yy::Haptics* haptics{};
  bool debugOpen{};
  int debugBalls{Model::defaultBalls}, debugBounces{Model::defaultBounces};
  int uiFinger{-1}; // a finger the HUD took, kept from the board

  void restart(int balls, int bounces) { touch.cancel(); model.restart(balls,bounces); touch.camera.fit(); }
  void openDebug() { touch.cancel(); debugBalls=model.ballCount; debugBounces=model.bouncesPerBall; debugOpen=true; }
  void pressDebug(yy::Vec2 p) {
    if(inside(ballsMinus,p)) debugBalls=std::max(1,debugBalls-1);
    else if(inside(ballsPlus,p)) debugBalls=std::min(Model::maxSetting,debugBalls+1);
    else if(inside(bouncesMinus,p)) debugBounces=std::max(1,debugBounces-1);
    else if(inside(bouncesPlus,p)) debugBounces=std::min(Model::maxSetting,debugBounces+1);
    else if(inside(restartButton,p)) { restart(debugBalls,debugBounces); debugOpen=false; }
    else if(inside(closeButton,p) || !inside(panel,p)) debugOpen=false;
  }
  // YY_TAPDEMO_SCENE stages a moment for smoke screenshots: aim, debug, play or zoom.
  void stage(const char* scene) {
    if(!scene || model.pockets.empty()) return;
    const auto& pocket=model.pockets.front();
    const yy::Vec2 centre{(pocket.column+pocket.columns/2.0f)*Model::cell, (pocket.row+pocket.rows/2.0f)*Model::cell};
    if(std::strcmp(scene,"debug")==0) { openDebug(); return; }
    if(std::strcmp(scene,"zoom")==0) { touch.camera.hold(centre,{195,480},2.0f); return; }
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
    if(model.hits.bricksBroken>0) {
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
    if(!debugOpen && touch.camera.contains(at)) touch.camera.zoomAt(at,touch.camera.zoom*std::pow(1.15f,steps));
  }
  void render(yy::Renderer& r) override {
    using yy::Color;
    const Color muted{132,155,178}, white{231,240,248}, teal{70,235,196}, dark{9,20,33};
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
    for(int row=r0; row<=r1; ++row) for(int column=c0; column<=c1; ++column) {
      const int hp=model.brick(column,row);
      if(hp<=0) continue;
      const auto p=cam.toScreen({column*Model::cell,row*Model::cell});
      r.rectangle({p.x+gap/2,p.y+gap/2,cellSize-gap,cellSize-gap},brickColors[std::min(hp,3)-1]);
      if(cellSize>=12) r.text({p.x+cellSize/2-4*digit,p.y+cellSize/2-4*digit},std::to_string(hp),dark,digit);
    }
    const float ballSize=Model::ballRadius*z;
    for(const auto& b: model.balls) {
      const auto p=cam.toScreen(b.position);
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

    // The HUD covers anything of the grid drawn above the play area.
    r.rectangle({0,0,cam.view.w,cam.view.y},dark);
    r.rectangle({0,cam.view.y-2,cam.view.w,2},{40,61,80});
    r.text({24,22}, "YY / TAP DEMO", teal, 2);
    r.text({24,46}, "VERSION " YY_GAME_VERSION, muted, 1.5f);
    r.rectangle(debugButton,{40,61,80});
    r.text({debugButton.x+12,debugButton.y+9},"DEBUG",white,1.5f);
    r.text({24,72}, "BALLS " + std::to_string(model.ballsLeft), white);
    r.text({190,72}, "BRICKS " + std::to_string(model.bricksLeft()), muted);
    r.text({24,96}, "HOLD IN A GAP, PULL, LET GO", muted, 1.5f);

    if(model.over()) {
      r.rectangle(overlay,{15,29,45});
      r.text({58,367}, model.won() ? "GRID CLEARED" : "OUT OF BALLS",white);
      r.text({58,405}, "BRICKS LEFT " + std::to_string(model.bricksLeft()),teal);
      r.text({58,445}, "TAP TO RESTART",muted);
    }
    if(debugOpen) {
      r.rectangle(panel,{15,29,45});
      r.rectangle({panel.x,panel.y,panel.w,2},teal);
      r.text({panel.x+20,panel.y+22},"DEBUG",teal);
      const auto row=[&](float y, const char* label, int value, yy::Rect minus, yy::Rect plus) {
        r.text({panel.x+20,y+12},label,white);
        r.rectangle(minus,{40,61,80}); r.rectangle(plus,{40,61,80});
        r.text({minus.x+14,minus.y+12},"-",white); r.text({plus.x+14,plus.y+12},"+",white);
        r.text({minus.x+minus.w+18,y+12},std::to_string(value),teal);
      };
      row(ballsMinus.y,"BALLS",debugBalls,ballsMinus,ballsPlus);
      row(bouncesMinus.y,"BOUNCES",debugBounces,bouncesMinus,bouncesPlus);
      r.rectangle(restartButton,{70,235,196}); r.text({restartButton.x+12,restartButton.y+15},"RESTART",dark);
      r.rectangle(closeButton,{40,61,80}); r.text({closeButton.x+28,closeButton.y+15},"CLOSE",white);
      r.text({panel.x+20,panel.y+292},"RESTART APPLIES BOTH",muted,1.5f);
    }
  }
};
std::unique_ptr<yy::Game> createGame() { return std::make_unique<TapGame>(); }
}
