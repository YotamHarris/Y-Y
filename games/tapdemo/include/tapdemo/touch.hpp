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

// Turns pointer events into slingshot aims, pinch zoom and pans, with the aim's haptics.
// While the instructions show, the first press only dismisses them. Then one finger on open
// space, or within a cell of it, holds a ball there; pulling aims it and letting go launches it opposite the pull.
// One finger elsewhere pans; a second finger cancels any aim and pinches.
class Touch {
  struct Finger { int id; yy::Vec2 position; };
  struct Pinch { yy::Vec2 world; float distance, zoom; };
  std::vector<Finger> fingers;
  std::optional<Pinch> pinch;
  Finger* find(int id) {
    for(auto& f: fingers) if(f.id==id) return &f;
    return nullptr;
  }
  static yy::Vec2 mid(yy::Vec2 a, yy::Vec2 b) { return {(a.x+b.x)/2, (a.y+b.y)/2}; }
  static float distance(yy::Vec2 a, yy::Vec2 b) { return std::hypot(a.x-b.x, a.y-b.y); }
  void startPinch() {
    const yy::Vec2 a=fingers[0].position, b=fingers[1].position;
    pinch=Pinch{camera.toWorld(mid(a,b)), std::max(1.0f,distance(a,b)), camera.zoom};
  }
public:
  struct Aim { int finger; yy::Vec2 anchor, pull; }; // world units; pull is finger minus anchor
  static constexpr float fullPull=120; // world units of pull at the strongest hum
  Model& model;
  yy::Haptics* haptics{};
  Camera camera;
  std::optional<Aim> aim;
  bool instructions{true}; // shown when the game opens and after each restart
  yy::Vec2 rejected{}; float rejectTime{}; // where a press could not hold a ball, while the ring shows
  explicit Touch(Model& m): model(m) { refit(); }
  // Shows the whole grid, sized as the model's grid is now (after a restart).
  void refit() { camera.world={model.width(),model.height()}; camera.fit(); }
  float humLevel() const { return aim ? 0.25f+0.5f*std::min(1.0f, std::hypot(aim->pull.x,aim->pull.y)/fullPull) : 0; }
  void cancel() {
    if(aim && haptics) haptics->humStop();
    aim.reset();
  }
  void down(int id, yy::Vec2 p) {
    if(instructions) { instructions=false; return; } // that finger's moves and release find nothing
    if(find(id) || fingers.size()>=2 || (fingers.empty() && !camera.contains(p))) return;
    fingers.push_back({id,p});
    if(fingers.size()==2) { cancel(); startPinch(); return; }
    const yy::Vec2 world=camera.toWorld(p);
    const auto spot=(!model.over() && model.ballsLeft>0) ? model.placeNear(world) : std::nullopt;
    if(spot) {
      aim=Aim{id,*spot,{}};
      if(haptics) haptics->humStart(humLevel());
    } else { rejected=world; rejectTime=0.45f; }
  }
  void move(int id, yy::Vec2 p) {
    Finger* f=find(id);
    if(!f) return;
    const yy::Vec2 last=f->position; f->position=p;
    if(pinch && fingers.size()==2) {
      const yy::Vec2 a=fingers[0].position, b=fingers[1].position;
      camera.hold(pinch->world, mid(a,b), pinch->zoom*distance(a,b)/pinch->distance);
    } else if(aim && aim->finger==id) {
      const yy::Vec2 world=camera.toWorld(p);
      aim->pull={world.x-aim->anchor.x, world.y-aim->anchor.y};
      if(haptics) haptics->humStart(humLevel());
    } else if(!aim) camera.pan({p.x-last.x, p.y-last.y});
  }
  // Returns true when the release launched a ball.
  bool up(int id, yy::Vec2 p) {
    if(!find(id)) return false;
    bool launched=false;
    if(aim && aim->finger==id) {
      move(id,p);
      if(haptics) haptics->humStop();
      launched=model.launch(aim->anchor, aim->pull);
      if(launched && haptics) haptics->thump();
      aim.reset();
    }
    fingers.erase(std::remove_if(fingers.begin(),fingers.end(),[&](const Finger& f){ return f.id==id; }),fingers.end());
    pinch.reset();
    return launched;
  }
  void update(float dt) { rejectTime=std::max(0.0f, rejectTime-dt); }
};
}
