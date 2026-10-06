#pragma once
#include "core.hpp"
#include "input.hpp"
#include <memory>
#include <string>
#include <string_view>

namespace yy {
class Renderer {
public:
  virtual ~Renderer() = default;
  virtual void rectangle(Rect r, Color color) = 0;
  virtual void circle(Vec2 center, float radius, Color color) = 0;
  virtual void text(Vec2 position, std::string_view value, Color color, float scale=2) = 0;
  // Assets are cached and owned by the runtime. BMP keeps the initial dependency surface small.
  virtual bool sprite(std::string_view asset, Rect destination) = 0;
};
class Audio {
public:
  virtual ~Audio() = default;
  virtual void tone(float hz, float seconds=0.08f) = 0;
};
// Phone haptics. Desktop and devices without a haptic engine ignore every call.
class Haptics {
public:
  virtual ~Haptics() = default;
  virtual void impact(float strength) = 0;   // short tap, strength 0..1
  virtual void humStart(float intensity) = 0; // continuous hum 0..1; calling again while humming changes its intensity
  virtual void humStop() = 0;
  virtual void thump() = 0;                   // firm release
};
// Small named text files kept on the device between runs, in SDL's preference folder.
class Storage {
public:
  virtual ~Storage() = default;
  virtual std::string read(std::string_view name) = 0; // empty when nothing was written
  virtual bool write(std::string_view name, std::string_view text) = 0; // replaces the whole file
};
struct Services { Renderer& renderer; Audio& audio; Haptics& haptics; Storage& storage; };
class Game {
public:
  virtual ~Game() = default;
  virtual void initialize(Services&) = 0;
  virtual void update(float seconds) = 0;
  virtual void render(Renderer&) = 0;
  virtual void tap(Vec2 position) = 0; // on every pointer down, after pointerDown
  // Per-contact input in logical coordinates; see PointerTracker for ids. Pinch is the game's to recognize.
  virtual void pointerDown(int /*id*/, Vec2 /*position*/) {}
  virtual void pointerMove(int /*id*/, Vec2 /*position*/) {}
  virtual void pointerUp(int /*id*/, Vec2 /*position*/) {}
  // Mouse wheel at a logical point: positive steps zoom in.
  virtual void zoom(Vec2 /*at*/, float /*steps*/) {}
  virtual void pause(bool paused) = 0;
  virtual void shutdown() = 0;
};
class Runtime {
  struct Impl;
  std::unique_ptr<Impl> impl;
public:
  Runtime(std::unique_ptr<Game> game, int smokeFrames=0, std::string assetDirectory="assets/");
  ~Runtime();
  bool initialize();
  bool event(const void* sdlEvent);
  bool iterate();
};
}
