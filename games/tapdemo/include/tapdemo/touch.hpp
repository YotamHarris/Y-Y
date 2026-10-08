#pragma once
#include <tapdemo/model.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cmath>
#include <optional>
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
    automatic=true; bounds=visibleBounds(model);
    camera.world={model.width(),model.height()}; camera=fitted(camera);
  }
  void update(Camera& camera, const Model& model, float dt) {
    if(!automatic || dt<=0) return;
    const auto visible=visibleBounds(model);
    const float right=std::max(bounds.x+bounds.w,visible.x+visible.w);
    const float bottom=std::max(bounds.y+bounds.h,visible.y+visible.h);
    bounds.x=std::min(bounds.x,visible.x); bounds.y=std::min(bounds.y,visible.y);
    bounds.w=right-bounds.x; bounds.h=bottom-bounds.y;
    const Camera target=fitted(camera,camera.zoom); // never push in during a shot
    const yy::Vec2 screen{camera.view.x+camera.view.w/2,camera.view.y+camera.view.h/2};
    const auto from=camera.toWorld(screen), to=target.toWorld(screen);
    const float k=1-std::exp(-easeRate*dt);
    camera.hold({from.x+(to.x-from.x)*k,from.y+(to.y-from.y)*k},screen,
                camera.zoom+(target.zoom-camera.zoom)*k);
  }
};

// Turns pointer events into slingshot aims, pinch zoom and pans, with the aim's haptics.
// While the instructions show, the first press only dismisses them. Then one finger on open
// space, or within a cell of it, holds a ball there; pulling aims it and letting go launches it opposite the pull.
// One finger elsewhere pans; a second finger cancels any aim and pinches.
class Touch {
  struct Finger { int id; yy::Vec2 position; };
  struct Pinch { yy::Vec2 world; float distance, zoom; };
  std::vector<Finger> fingers;
  std::optional<Pinch> pinch;
  struct AimZoom { Camera prior; yy::Vec2 screen; float elapsed{}; };
  // The zoom a held aim may not exceed (where it started), and the eased zoom that keeps its first contact in view.
  struct AimFit { Camera prior; yy::Vec2 screen; float cap, zoom; };
  struct Restore { Camera from, to; float elapsed{}; };
  std::optional<AimFit> aimFit;
  std::optional<AimZoom> aimZoom;
  std::optional<Restore> restore;
  static constexpr float zoomSeconds=0.28f, fitRate=8, fitStubCells=1.6f;
  Finger* find(int id) {
    for(auto& f: fingers) if(f.id==id) return &f;
    return nullptr;
  }
  static yy::Vec2 mid(yy::Vec2 a, yy::Vec2 b) { return {(a.x+b.x)/2, (a.y+b.y)/2}; }
  static float distance(yy::Vec2 a, yy::Vec2 b) { return std::hypot(a.x-b.x, a.y-b.y); }
  // The largest zoom about screen point `at` (capped, never below the grid fit) that keeps world point
  // `to` (seen from `from`) inside the view with `margin` world units around it.
  float zoomToFit(yy::Vec2 at, yy::Vec2 from, yy::Vec2 to, float margin, float cap) const {
    float z=cap;
    const auto axis=[&](float s, float d, float lo, float hi) {
      if(hi-s<=0 || s-lo<=0) return; // the held ball is already at the edge: nothing to gain
      if(d+margin>0) z=std::min(z,(hi-s)/(d+margin));
      if(margin-d>0) z=std::min(z,(s-lo)/(margin-d));
    };
    axis(at.x,to.x-from.x,camera.view.x,camera.view.x+camera.view.w);
    axis(at.y,to.y-from.y,camera.view.y,camera.view.y+camera.view.h);
    return std::max(z,camera.minZoom());
  }
  // Ends the aim: the camera eases back to the view held before it (the aim zoom and the fit both undo).
  void endAim() {
    if(aimFit && (aimZoom || camera.zoom!=aimFit->prior.zoom)) restore=Restore{camera,aimFit->prior};
    aimZoom.reset();
    aimFit.reset();
    aim.reset();
  }
  void startPinch() {
    framing.automatic=false;
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
    framing.automatic=false;
    camera.zoomAt(at,camera.zoom*std::pow(1.15f,steps));
  }
  // The aim's hum grows with the pull; the last ball's is tighter: it starts higher and ends at full strength.
  float humLevel() const {
    if(!aim) return 0;
    // Zooming out to fit the aim stretches the pull in world units; the hum follows the finger's screen distance.
    const float scale=aimFit ? aimFit->zoom/aimFit->cap : 1;
    const float pulled=std::min(1.0f, std::hypot(aim->pull.x,aim->pull.y)*scale/fullPull);
    return model.ballsLeft==1 ? 0.5f+0.5f*pulled : 0.25f+0.5f*pulled;
  }
  void cancel() {
    if(aim && haptics) haptics->humStop();
    endAim();
  }
  void down(int id, yy::Vec2 p) {
    if(instructions) { instructions=false; return; } // that finger's moves and release find nothing
    if(find(id) || fingers.size()>=2 || (fingers.empty() && !camera.contains(p))) return;
    fingers.push_back({id,p});
    if(fingers.size()==2) { cancel(); startPinch(); return; }
    const yy::Vec2 world=camera.toWorld(p);
    const auto spot=(!model.over() && model.ballsLeft>0) ? model.placeNear(world) : std::nullopt;
    if(spot) {
      restore.reset();
      aim=Aim{id,*spot,{}};
      const float opening=std::min(Camera::maxZoom,camera.minZoom()*Framing::openingScale);
      if(camera.zoom<opening) aimZoom=AimZoom{camera,camera.toScreen(*spot)};
      const float cap=aimZoom ? opening : camera.zoom;
      aimFit=AimFit{camera,camera.toScreen(*spot),cap,cap};
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
      framing.automatic=false;
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
      if(launched && haptics) { haptics->humStop(); haptics->thump(); }
      if(launched) { // the aim zoom eases back to the view held before the ball was placed
        endAim();
      } else cancel();
    }
    fingers.erase(std::remove_if(fingers.begin(),fingers.end(),[&](const Finger& f){ return f.id==id; }),fingers.end());
    pinch.reset();
    return launched;
  }
  void update(float dt, bool follow=true) {
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
    if(aim && aimFit) {
      float base=aimFit->cap;
      if(aimZoom) {
        aimZoom->elapsed=std::min(zoomSeconds,aimZoom->elapsed+dt);
        const float t=aimZoom->elapsed/zoomSeconds, k=t*t*(3-2*t);
        base=aimZoom->prior.zoom+(aimFit->cap-aimZoom->prior.zoom)*k;
      }
      // Zoom out about the held ball just far enough to keep the line's first contact, the whole block it meets (a cell and a ball radius around the contact) and its stub in view;
      // an invalid path leaves the fit where it is.
      if(std::hypot(aim->pull.x,aim->pull.y)>=Model::minPull) {
        const AimPath path=model.aimPath(aim->anchor,aim->pull);
        if(path.valid) {
          const float stub=fitStubCells*Model::cell;
          const yy::Vec2 end{path.contact.x+path.after.x*stub,path.contact.y+path.after.y*stub};
          const float need=std::min(zoomToFit(aimFit->screen,aim->anchor,path.contact,Model::cell+Model::ballRadius,aimFit->cap),
                                    zoomToFit(aimFit->screen,aim->anchor,end,0,aimFit->cap));
          aimFit->zoom+=(need-aimFit->zoom)*(1-std::exp(-fitRate*dt));
        }
      }
      camera.zoom=std::clamp(std::min(base,aimFit->zoom),camera.minZoom(),Camera::maxZoom);
      // Grid-edge clamping must not slide a held ball away from its finger.
      camera.offset={aimFit->screen.x-aim->anchor.x*camera.zoom,aimFit->screen.y-aim->anchor.y*camera.zoom};
      if(const auto* finger=find(aim->finger)) {
        const auto world=camera.toWorld(finger->position);
        aim->pull={world.x-aim->anchor.x,world.y-aim->anchor.y};
      }
    } else if(!aim && (fingers.empty() || !model.balls.empty())) framing.update(camera,model,dt);
  }
};
}
