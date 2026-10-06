#pragma once
#include <yy/core.hpp>
#include <array>
#include <string_view>

namespace tapdemo {
// Rendering only: colours do not belong to the deterministic game model.
struct Palette {
  std::string_view name;
  yy::Color field, dark, card, button, white, muted, teal;
  yy::Color ballRed, tile;
  std::array<yy::Color,2> fog;
  // Goal, Bomb, Electricity, Ping, Ghost, Speed (Power's enum order).
  std::array<yy::Color,6> glows;
};

// Linear sRGB luminance, 0..1. Fog must be clearly lighter than an empty cavity
// even in greyscale.
inline constexpr float minFogLuminanceGap=0.08f;
// The first fogged ring fades in over this many strips, from faint (next to a clear cell) to
// nearly solid (next to deeper fog); alpha 0..255.
inline constexpr int fogEdgeStrips=4;
inline constexpr int fogEdgeFaint=90, fogEdgeSolid=240;
inline float luminance(yy::Color c) {
  const auto linear=[](unsigned char channel) {
    const float s=channel/255.0f;
    return s<=0.04045f ? s/12.92f : std::pow((s+0.055f)/1.055f,2.4f);
  };
  return 0.2126f*linear(c.r)+0.7152f*linear(c.g)+0.0722f*linear(c.b);
}
}
