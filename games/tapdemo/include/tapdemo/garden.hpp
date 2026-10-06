#pragma once
#include <tapdemo/palette.hpp>
#include <yy/runtime.hpp>

namespace tapdemo {
// Garden Pop is the game's only look.
inline constexpr Palette garden{
  "GARDEN POP", {24,43,29}, {25,57,34}, {25,57,34}, {63,91,55},
  {255,248,224}, {166,192,158}, {162,224,126},
  {237,35,51}, {58,32,18},
  {{{154,177,143},{184,207,170}}},
  {{{244,111,175},{255,174,122},{255,234,122},{128,225,250},{219,181,250},{162,245,179}}}
};
inline constexpr float popSeconds=0.200f;
inline constexpr int gardenTextureCells=8; // large dewy/soil patches, independent of brick strength
enum class GardenSprite { Grass, Cut, Soil, Clear, Holder, Mist, Ball, Flag,
  Flash, Burst, Clippings, Header, Card, RimH, RimV, RimCorner };
inline yy::Rect gardenSource(GardenSprite sprite) {
  const int i=static_cast<int>(sprite);
  return {static_cast<float>((i%4)*260+2),static_cast<float>((i/4)*260+2),256,256};
}
inline GardenSprite gardenDamage(int hp) {
  return hp>=3 ? GardenSprite::Grass : hp==2 ? GardenSprite::Cut : hp==1 ? GardenSprite::Soil : GardenSprite::Clear;
}
inline void gardenSprite(yy::Renderer& r, GardenSprite sprite, yy::Rect box) {
  r.sprite("garden/tiles.bmp",gardenSource(sprite),box);
}
inline void gardenCellTexture(yy::Renderer& r, GardenSprite sprite, int column, int row, yy::Rect box) {
  auto source=gardenSource(sprite);
  source.w/=gardenTextureCells; source.h/=gardenTextureCells;
  source.x+=(column%gardenTextureCells)*source.w; source.y+=(row%gardenTextureCells)*source.h;
  r.sprite("garden/tiles.bmp",source,box);
}
inline void gardenLabel(yy::Renderer& r, yy::Vec2 at, std::string_view text, float size,
                        yy::Color color, yy::Align align=yy::Align::Left) {
  r.label("fonts/fredoka",at,text,size,color,align);
}
}
