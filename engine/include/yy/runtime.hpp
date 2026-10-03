#pragma once
#include "core.hpp"
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
struct Services { Renderer& renderer; Audio& audio; };
class Game {
public:
  virtual ~Game() = default;
  virtual void initialize(Services&) = 0;
  virtual void update(float seconds) = 0;
  virtual void render(Renderer&) = 0;
  virtual void tap(Vec2 position) = 0;
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
