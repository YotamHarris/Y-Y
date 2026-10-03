#pragma once
#include <algorithm>
#include <cmath>
#include <optional>

namespace yy {
struct Vec2 { float x{}, y{}; };
struct Rect { float x{}, y{}, w{}, h{}; };
struct Color { unsigned char r{}, g{}, b{}, a{255}; };
// Coordinates always refer to the safe area in window units, not framebuffer pixels.
struct Viewport {
  Rect safe{};
  float logicalWidth{390}, logicalHeight{844};
  float scale() const { return std::min(safe.w / logicalWidth, safe.h / logicalHeight); }
  Rect content() const {
    const float s = scale();
    return {safe.x + (safe.w - logicalWidth*s)/2, safe.y + (safe.h-logicalHeight*s)/2,
            logicalWidth*s, logicalHeight*s};
  }
  std::optional<Vec2> map(Vec2 windowPoint) const {
    const auto r = content(); const float s = scale();
    if(s<=0 || windowPoint.x<r.x || windowPoint.y<r.y || windowPoint.x>=r.x+r.w || windowPoint.y>=r.y+r.h) return {};
    return Vec2{(windowPoint.x-r.x)/s, (windowPoint.y-r.y)/s};
  }
};
class FixedClock {
  double accumulator{};
public:
  static constexpr double step = 1.0/60.0;
  void reset() { accumulator = 0; }
  template<class F> void advance(double elapsed, F update) {
    if(!std::isfinite(elapsed)) return;
    accumulator += std::clamp(elapsed, 0.0, 0.1);
    while(accumulator + 1e-9 >= step) { update(static_cast<float>(step)); accumulator -= step; }
  }
};
}
