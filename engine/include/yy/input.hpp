#pragma once
#include "core.hpp"
#include <cstdint>
#include <vector>

namespace yy {
// One finger (or the left mouse button) from down to up. Ids are small and stable while
// the contact lasts: each new contact takes the lowest free id, so the mouse alone and the
// first finger are 0 and a second finger is 1.
struct PointerEvent {
  enum class Phase { Down, Move, Up };
  Phase phase{};
  int id{};
  Vec2 position{};
};
// Turns window-space contacts into logical pointer events without SDL. A contact must start
// inside the content area; once down, its moves and up are clamped to the content edge so a
// drag that leaves the play area still ends.
class PointerTracker {
  struct Contact { std::uint64_t source; int id; Vec2 position; };
  std::vector<Contact> contacts;
  Contact* find(std::uint64_t source) {
    for(auto& c: contacts) if(c.source==source) return &c;
    return nullptr;
  }
  static std::optional<Vec2> clamped(const Viewport& v, Vec2 windowPoint) {
    const auto r=v.content(); const float s=v.scale();
    if(s<=0) return {};
    return Vec2{std::clamp((windowPoint.x-r.x)/s,0.0f,v.logicalWidth), std::clamp((windowPoint.y-r.y)/s,0.0f,v.logicalHeight)};
  }
public:
  static constexpr std::uint64_t mouse = ~std::uint64_t{0};
  std::optional<PointerEvent> down(std::uint64_t source, Vec2 windowPoint, const Viewport& v) {
    if(find(source)) return {};
    const auto p=v.map(windowPoint);
    if(!p) return {};
    int id=0;
    for(bool taken=true; taken; ) { taken=false; for(const auto& c: contacts) if(c.id==id) { taken=true; ++id; break; } }
    contacts.push_back({source,id,*p});
    return PointerEvent{PointerEvent::Phase::Down,id,*p};
  }
  std::optional<PointerEvent> move(std::uint64_t source, Vec2 windowPoint, const Viewport& v) {
    auto* c=find(source); const auto p=clamped(v,windowPoint);
    if(!c || !p) return {};
    c->position=*p;
    return PointerEvent{PointerEvent::Phase::Move,c->id,*p};
  }
  std::optional<PointerEvent> up(std::uint64_t source, Vec2 windowPoint, const Viewport& v) {
    auto* c=find(source);
    if(!c) return {};
    const PointerEvent e{PointerEvent::Phase::Up,c->id,clamped(v,windowPoint).value_or(c->position)};
    contacts.erase(contacts.begin()+(c-contacts.data()));
    return e;
  }
  // Ends every contact where it last was, for a pause or a lost window.
  std::vector<PointerEvent> cancel() {
    std::vector<PointerEvent> ended;
    for(const auto& c: contacts) ended.push_back({PointerEvent::Phase::Up,c.id,c.position});
    contacts.clear();
    return ended;
  }
  std::size_t active() const { return contacts.size(); }
};
}
