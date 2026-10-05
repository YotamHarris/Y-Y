#pragma once
#include <yy/core.hpp>
#include <array>
#include <string_view>

namespace tapdemo {
// Rendering only: scheme choice does not belong to the deterministic game model.
struct Palette {
  std::string_view name;
  yy::Color field, dark, card, button, white, muted, teal;
  yy::Color ballRed, ballHighlight, aimHighlight, tile;
  std::array<yy::Color,3> bricks;
  std::array<yy::Color,2> fog;
  yy::Color fogTexture;
  // Goal, Bomb, Electricity, Ping, Ghost, Speed (Power's enum order).
  std::array<yy::Color,6> glows;
};

inline constexpr Palette navy{
  "NAVY", {17,34,52}, {9,20,33}, {15,29,45}, {40,61,80},
  {231,240,248}, {132,155,178}, {70,235,196},
  {235,70,70}, {255,150,150}, {166,255,226}, {14,22,34},
  {{{58,110,168},{196,146,62},{148,96,206}}},
  {{{91,108,126},{86,103,121}}}, {146,164,181},
  {{{255,64,200},{255,112,48},{255,232,64},{64,214,255},{214,160,255},{96,255,128}}}
};

inline constexpr std::array<Palette,4> palettes{{navy,
  {"EMBER", {42,25,19}, {26,14,11}, {38,22,17}, {80,47,32},
   {255,239,218}, {192,161,137}, {255,200,112},
   {255,77,81}, {255,178,156}, {255,238,185}, {24,16,14},
   {{{73,149,161},{212,164,72},{174,111,195}}},
   {{{128,104,88},{122,99,83}}}, {186,161,139},
   {{{255,105,214},{255,159,68},{255,235,96},{80,225,255},{218,176,255},{129,255,149}}}},
  {"FOREST", {15,36,28}, {7,23,17}, {12,31,23}, {34,72,52},
   {234,250,229}, {151,181,160}, {167,243,120},
   {250,75,83}, {255,166,163}, {221,255,185}, {9,22,18},
   {{{68,133,200},{218,170,74},{168,111,209}}},
   {{{94,112,99},{89,107,94}}}, {151,174,152},
   {{{255,98,211},{255,139,65},{255,237,93},{87,221,255},{225,174,255},{126,255,169}}}},
  {"PLUM", {36,22,44}, {22,12,30}, {31,18,40}, {67,43,85},
   {250,234,255}, {179,153,196}, {247,194,255},
   {255,78,86}, {255,176,172}, {255,225,253}, {20,13,27},
   {{{69,151,182},{217,163,69},{170,113,222}}},
   {{{114,101,128},{108,96,122}}}, {176,156,192},
   {{{255,107,207},{255,151,69},{255,239,100},{88,227,255},{226,184,255},{130,255,164}}}}
}};

// Linear sRGB luminance, 0..1. Fog must be clearly lighter than an empty cavity
// even in greyscale; its stipple adds a separate shape cue at every zoom.
inline constexpr float minFogLuminanceGap=0.08f;
inline float luminance(yy::Color c) {
  const auto linear=[](unsigned char channel) {
    const float s=channel/255.0f;
    return s<=0.04045f ? s/12.92f : std::pow((s+0.055f)/1.055f,2.4f);
  };
  return 0.2126f*linear(c.r)+0.7152f*linear(c.g)+0.0722f*linear(c.b);
}
}
