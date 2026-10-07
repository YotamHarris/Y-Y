#pragma once
#include <tapdemo/model.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cmath>
#include <optional>
#include <span>
#include <vector>

namespace tapdemo {
// Shows the grid in the play area: screen = world * zoom + offset.
struct Camera {
  yy::Rect view{0,80,390,764}; // below the header
  yy::Vec2 world{Model::defaultColumns*Model::cell, Model::defaultRows*Model::cell}; // the grid's size
  float zoom{1};
  yy::Vec2 offset{};
  static constexpr float maxZoom=2.5f;
  float minZoom() const { return std::min(view.w/world.x, view.h/world.y); }
  yy::Vec2 toScreen(yy::Vec2 w) const { return {w.x*zoom+offset.x, w.y*zoom+offset.y}; }
  yy::Vec2 toWorld(yy::Vec2 s) const { return {(s.x-offset.x)/zoom, (s.y-offset.y)/zoom}; }
  // The cells whose boxes meet `view`, inclusive, with `margin` cells more on every side, kept on the grid.
  struct Cells { int c0, c1, r0, r1; };
  Cells visibleCells(int columns, int rows, int margin=1) const {
    const auto first=toWorld({view.x,view.y}), last=toWorld({view.x+view.w,view.y+view.h});
    const auto cell=[](float world) { return static_cast<int>(std::floor(world/Model::cell)); };
    return {std::max(0,cell(first.x)-margin), std::min(columns-1,cell(last.x)+margin),
            std::max(0,cell(first.y)-margin), std::min(rows-1,cell(last.y)+margin)};
  }
  bool contains(yy::Vec2 s) const { return s.x>=view.x && s.y>=view.y && s.x<view.x+view.w && s.y<view.y+view.h; }
  // Centres the grid on an axis that fits; otherwise keeps the play area covered.
  void clamp() {
    const auto axis=[](float& o, float start, float size, float span) {
      o = span<=size ? start+(size-span)/2 : std::clamp(o, start+size-span, start);
    };
    axis(offset.x, view.x, view.w, world.x*zoom);
    axis(offset.y, view.y, view.h, world.y*zoom);
  }
  void fit() { zoom=minZoom(); clamp(); }
  // Puts world point `world` under screen point `screen` at the given (clamped) zoom.
  void hold(yy::Vec2 world, yy::Vec2 screen, float newZoom) {
    zoom=std::clamp(newZoom, minZoom(), maxZoom);
    offset={screen.x-world.x*zoom, screen.y-world.y*zoom};
    clamp();
  }
  void zoomAt(yy::Vec2 screen, float newZoom) { hold(toWorld(screen), screen, newZoom); }
  void pan(yy::Vec2 by) { offset.x+=by.x; offset.y+=by.y; clamp(); }
};

// Presentation only: frame the cavity and the bricks the player can see, with a cell of
// breathing room. Keep every area revealed this level, even after a Ping expires.
struct Framing {
  static constexpr float openingScale=2.5f, easeRate=4;
  yy::Rect bounds{};
  bool automatic{true};
  bool shotWasLive{};
  float followLimit{}, ghostTime{};
  void manual() { automatic=false; followLimit=0; }
  static yy::Rect visibleBounds(const Model& model) {
    int left=model.columns, top=model.rows, right=0, bottom=0;
    for(int r=0; r<model.rows; ++r) for(int c=0; c<model.columns; ++c) {
      const bool ping=(model.isGoal(c,r) || model.power(c,r)!=Power::None) && model.pinged(c,r);
      if(model.brick(c,r)>0 && !model.visible(c,r) && !ping) continue;
      left=std::min(left,c); top=std::min(top,r);
      right=std::max(right,c+1); bottom=std::max(bottom,r+1);
    }
    if(right<=left || bottom<=top) return {0,0,model.width(),model.height()};
    left=std::max(0,left-1); top=std::max(0,top-1);
    right=std::min(model.columns,right+1); bottom=std::min(model.rows,bottom+1);
    return {left*Model::cell,top*Model::cell,(right-left)*Model::cell,(bottom-top)*Model::cell};
  }
  Camera fitted(const Camera& camera, float limit=Camera::maxZoom) const {
    Camera target=camera;
    const float zoom=std::min({camera.view.w/bounds.w,camera.view.h/bounds.h,
                               camera.minZoom()*openingScale,limit});
    target.hold({bounds.x+bounds.w/2,bounds.y+bounds.h/2},
                {camera.view.x+camera.view.w/2,camera.view.y+camera.view.h/2},zoom);
    return target;
  }
  void reset(Camera& camera, const Model& model) {
    automatic=true; shotWasLive=false; followLimit=0; ghostTime=0; bounds=visibleBounds(model);
    camera.world={model.width(),model.height()}; camera=fitted(camera);
  }
  void update(Camera& camera, const Model& model, float dt, std::span<const yy::Vec2> shown={}) {
    if(dt<=0) return;
    const bool live=!model.balls.empty();
    if(!live && (shotWasLive || !automatic)) { followLimit=0; return; }
    const auto previous=bounds;
    const auto visible=visibleBounds(model);
    const float right=std::max(bounds.x+bounds.w,visible.x+visible.w);
    const float bottom=std::max(bounds.y+bounds.h,visible.y+visible.h);
    bounds.x=std::min(bounds.x,visible.x); bounds.y=std::min(bounds.y,visible.y);
    bounds.w=right-bounds.x; bounds.h=bottom-bounds.y;
    const bool revealed=bounds.x!=previous.x || bounds.y!=previous.y || bounds.w!=previous.w || bounds.h!=previous.h;
    Camera target=fitted(camera,camera.zoom);
    yy::Rect flight{};
    if(live) {
      if(followLimit==0) followLimit=camera.zoom;
      shotWasLive=true;
      float left=model.width(), top=model.height(), right=0, bottom=0;
      const auto include=[&](yy::Vec2 at) {
        left=std::min(left,at.x-Model::cell); top=std::min(top,at.y-Model::cell);
        right=std::max(right,at.x+Model::cell); bottom=std::max(bottom,at.y+Model::cell);
      };
      for(const auto& ball: model.balls) include(ball.position);
      for(const auto at: shown) include(at); // interpolation and hit-stop can draw behind the model
      flight={left,top,right-left,bottom-top};
      followLimit=std::min({followLimit,camera.view.w/flight.w,camera.view.h/flight.h,
                           automatic && revealed ? fitted(camera).zoom : Camera::maxZoom});
      for(const auto& fired: model.hits.fired)
        if(fired.power==Power::Ghost && fired.to>=0) ghostTime=0.35f;
      // A fast swing to a Ghost's new cavity. Other shots drift only as far as
      // needed to bring the live bounds inside the view, regardless of manual ownership.
      target=camera;
      target.zoom=followLimit;
      const yy::Vec2 centre=ghostTime>0 || !automatic ? yy::Vec2{left+flight.w/2,top+flight.h/2} :
                                                                      yy::Vec2{bounds.x+bounds.w/2,bounds.y+bounds.h/2};
      target.offset={camera.view.x+camera.view.w/2-centre.x*followLimit,
                     camera.view.y+camera.view.h/2-centre.y*followLimit};
    }
    const yy::Vec2 screen{camera.view.x+camera.view.w/2,camera.view.y+camera.view.h/2};
    const auto from=camera.toWorld(screen), to=target.toWorld(screen);
    const float k=1-std::exp(-(ghostTime>0 ? 24.0f : easeRate)*dt);
    const yy::Vec2 centre{from.x+(to.x-from.x)*k,from.y+(to.y-from.y)*k};
    float zoom=camera.zoom+(target.zoom-camera.zoom)*k;
    if(live) {
      // Widen immediately when a teleport or a fast ball outruns the ease.
      // This preserves a visible swing instead of cutting to the landing.
      const float dx=std::max(std::abs(centre.x-flight.x),std::abs(centre.x-flight.x-flight.w));
      const float dy=std::max(std::abs(centre.y-flight.y),std::abs(centre.y-flight.y-flight.h));
      zoom=std::min({zoom,camera.view.w/(2*dx),camera.view.h/(2*dy)});
    }
    if(live) {
      // A ball at a wall still needs breathing room. Following may show a
      // little backdrop outside the grid; manual camera limits remain intact.
      camera.zoom=zoom;
      camera.offset={screen.x-centre.x*zoom,screen.y-centre.y*zoom};
    } else camera.hold(centre,screen,zoom);
    ghostTime=std::max(0.0f,ghostTime-dt);
  }
};

// Turns pointer events into slingshot aims, pinch zoom and pans, with the aim's haptics.
// While the instructions show, the first press only dismisses them. Then one finger on open
// space, or within a cell of it, holds a ball there once nothing flies (a press while a ball flies only pans); pulling aims it and letting go launches it opposite the pull.
// One finger elsewhere pans; a second finger cancels any aim and pinches.
class Touch {
  struct Finger { int id; yy::Vec2 position; };
  struct Pinch { yy::Vec2 world; float distance, zoom; };
  std::vector<Finger> fingers;
  std::optional<Pinch> pinch;
  struct AimZoom { Camera prior; yy::Vec2 screen; float elapsed{}; };
  struct Restore { Camera from, to; float elapsed{}; };
  std::optional<AimZoom> aimZoom;
  std::optional<Restore> restore;
  static constexpr float zoomSeconds=0.28f;
  Finger* find(int id) {
    for(auto& f: fingers) if(f.id==id) return &f;
    return nullptr;
  }
  static yy::Vec2 mid(yy::Vec2 a, yy::Vec2 b) { return {(a.x+b.x)/2, (a.y+b.y)/2}; }
  static float distance(yy::Vec2 a, yy::Vec2 b) { return std::hypot(a.x-b.x, a.y-b.y); }
  void startPinch() {
    framing.manual();
    const yy::Vec2 a=fingers[0].position, b=fingers[1].position;
    pinch=Pinch{camera.toWorld(mid(a,b)), std::max(1.0f,distance(a,b)), camera.zoom};
  }
public:
  struct Aim { int finger; yy::Vec2 anchor, pull; }; // world units; pull is finger minus anchor
  static constexpr float fullPull=120; // world units of pull at the strongest hum
  Model& model;
  yy::Haptics* haptics{};
  Camera camera;
  Framing framing;
  std::optional<Aim> aim;
  bool instructions{true}; // shown when the game opens and a level or free play starts; a retry skips it
  yy::Vec2 rejected{}; float rejectTime{}; // where a press could not hold a ball, while the ring shows
  explicit Touch(Model& m): model(m) { refit(); }
  // A new level or retry returns control to the cavity frame and clears old gestures.
  void refit() { cancel(); restore.reset(); fingers.clear(); pinch.reset(); framing.reset(camera,model); }
  void zoom(yy::Vec2 at, float steps) {
    if(steps==0) return;
    restore.reset();
    framing.manual();
    camera.zoomAt(at,camera.zoom*std::pow(1.15f,steps));
  }
  // The aim's hum grows with the pull; the last ball's is tighter: it starts higher and ends at full strength.
  float humLevel() const {
    if(!aim) return 0;
    const float pulled=std::min(1.0f, std::hypot(aim->pull.x,aim->pull.y)/fullPull);
    return model.ballsLeft==1 ? 0.5f+0.5f*pulled : 0.25f+0.5f*pulled;
  }
  void cancel() {
    if(aim && haptics) haptics->humStop();
    if(aimZoom) restore=Restore{camera,aimZoom->prior};
    aimZoom.reset();
    aim.reset();
  }
  void down(int id, yy::Vec2 p) {
    if(instructions) { instructions=false; return; } // that finger's moves and release find nothing
    if(find(id) || fingers.size()>=2 || (fingers.empty() && !camera.contains(p))) return;
    fingers.push_back({id,p});
    if(fingers.size()==2) { cancel(); startPinch(); return; }
    const yy::Vec2 world=camera.toWorld(p);
    const auto spot=(!model.over() && model.ballsLeft>0 && !model.flying()) ? model.placeNear(world) : std::nullopt; // one ball at a time: a press while one flies places nothing and pans
    if(spot) {
      restore.reset();
      aim=Aim{id,*spot,{}};
      if(camera.zoom<std::min(Camera::maxZoom,camera.minZoom()*Framing::openingScale))
        aimZoom=AimZoom{camera,camera.toScreen(*spot)};
      if(haptics) haptics->humStart(humLevel());
    } else { rejected=world; rejectTime=0.45f; }
  }
  void move(int id, yy::Vec2 p) {
    Finger* f=find(id);
    if(!f) return;
    const yy::Vec2 last=f->position;
    if(restore && (p.x!=last.x || p.y!=last.y)) {
      restore.reset();
      if(pinch) startPinch();
    }
    f->position=p;
    if(pinch && fingers.size()==2) {
      const yy::Vec2 a=fingers[0].position, b=fingers[1].position;
      camera.hold(pinch->world, mid(a,b), pinch->zoom*distance(a,b)/pinch->distance);
    } else if(aim && aim->finger==id) {
      const yy::Vec2 world=camera.toWorld(p);
      aim->pull={world.x-aim->anchor.x, world.y-aim->anchor.y};
      if(haptics) haptics->humStart(humLevel());
    } else if(!aim && (p.x!=last.x || p.y!=last.y)) {
      framing.manual();
      camera.pan({p.x-last.x, p.y-last.y});
    }
  }
  // Returns true when the release launched a ball.
  bool up(int id, yy::Vec2 p) {
    if(!find(id)) return false;
    bool launched=false;
    if(aim && aim->finger==id) {
      move(id,p);
      launched=model.launch(aim->anchor, aim->pull);
      if(launched) framing.followLimit=0;
      if(launched && haptics) { haptics->humStop(); haptics->thump(); }
      if(!launched) cancel();
      aimZoom.reset();
      aim.reset();
    }
    fingers.erase(std::remove_if(fingers.begin(),fingers.end(),[&](const Finger& f){ return f.id==id; }),fingers.end());
    pinch.reset();
    return launched;
  }
  void update(float dt, bool follow=true, std::span<const yy::Vec2> shown={}) {
    rejectTime=std::max(0.0f, rejectTime-dt);
    if(!follow || dt<=0) return; // the celebration borrows the entire camera
    if(restore) {
      restore->elapsed=std::min(zoomSeconds,restore->elapsed+dt);
      const float t=restore->elapsed/zoomSeconds, k=t*t*(3-2*t);
      const auto& a=restore->from; const auto& b=restore->to;
      camera.zoom=a.zoom+(b.zoom-a.zoom)*k;
      camera.offset={a.offset.x+(b.offset.x-a.offset.x)*k,a.offset.y+(b.offset.y-a.offset.y)*k};
      if(t==1) { camera=b; restore.reset(); if(pinch) startPinch(); }
      return;
    }
    if(aimZoom && aim) {
      aimZoom->elapsed=std::min(zoomSeconds,aimZoom->elapsed+dt);
      const float t=aimZoom->elapsed/zoomSeconds, k=t*t*(3-2*t);
      const float target=std::min(Camera::maxZoom,camera.minZoom()*Framing::openingScale);
      camera.zoom=aimZoom->prior.zoom+(target-aimZoom->prior.zoom)*k;
      // Grid-edge clamping must not slide a held ball away from its finger.
      camera.offset={aimZoom->screen.x-aim->anchor.x*camera.zoom,aimZoom->screen.y-aim->anchor.y*camera.zoom};
      if(const auto* finger=find(aim->finger)) {
        const auto world=camera.toWorld(finger->position);
        aim->pull={world.x-aim->anchor.x,world.y-aim->anchor.y};
      }
    } else if(!aim && (fingers.empty() || !model.balls.empty())) framing.update(camera,model,dt,shown);
  }
};
}
