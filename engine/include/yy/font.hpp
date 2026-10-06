#pragma once
#include "core.hpp"
#include <array>
#include <cstdlib>
#include <string>
#include <string_view>
#include <vector>

namespace yy {
enum class Align { Left, Center, Right };
// A font baked ahead of time by scripts/generate-assets.py: printable ASCII at several pixel sizes,
// each a coverage sheet plus these metrics. SDL-free, so layout is tested without a window.
struct FontBake {
  struct Glyph { Rect source; float left{}, top{}, advance{}; bool present{}; };
  float pixels{}, ascent{}, descent{};
  std::string sheet;
  std::array<Glyph,128> glyphs{};
  // Characters the bake lacks draw as '?'.
  const Glyph& glyph(char c) const {
    const auto code=static_cast<unsigned char>(c);
    return code<glyphs.size() && glyphs[code].present ? glyphs[code] : glyphs['?'];
  }
};
struct GlyphQuad { Rect source, destination; };
struct TextLayout { std::size_t bake{}; std::vector<GlyphQuad> quads; };
struct Font {
  std::vector<FontBake> bakes; // ascending pixel sizes
  // The smallest bake at least as large as the text on screen, else the largest: a sheet is never
  // shrunk more than the gap between two bakes.
  std::size_t pick(float screenPixels) const {
    for(std::size_t i=0; i<bakes.size(); ++i) if(bakes[i].pixels>=screenPixels) return i;
    return bakes.size()-1;
  }
  // In logical units, for text `size` units tall drawn at `pixelsPerUnit` screen pixels per unit.
  float width(std::string_view text, float size, float pixelsPerUnit=1) const {
    if(bakes.empty()) return 0;
    const auto& bake=bakes[pick(size*pixelsPerUnit)];
    float pen=0;
    for(char c: text) pen+=bake.glyph(c).advance;
    return pen*size/bake.pixels;
  }
  // `at` is the top of the line, at its left edge, centre or right edge by `align`.
  TextLayout layout(Vec2 at, std::string_view text, float size, Align align, float pixelsPerUnit=1) const {
    TextLayout out;
    if(bakes.empty() || size<=0) return out;
    out.bake=pick(size*pixelsPerUnit);
    const auto& bake=bakes[out.bake];
    const float k=size/bake.pixels, w=width(text,size,pixelsPerUnit);
    float pen=at.x-(align==Align::Center ? w/2 : align==Align::Right ? w : 0);
    const float baseline=at.y+bake.ascent*k;
    for(char c: text) {
      const auto& g=bake.glyph(c);
      if(g.source.w>0) out.quads.push_back({g.source,{pen+g.left*k,baseline+g.top*k,g.source.w*k,g.source.h*k}});
      pen+=g.advance*k;
    }
    return out;
  }
};
namespace detail {
inline std::string_view word(std::string_view& line) {
  const auto start=line.find_first_not_of(' ');
  if(start==std::string_view::npos) { line={}; return {}; }
  line.remove_prefix(start);
  const auto end=std::min(line.find(' '),line.size());
  const auto w=line.substr(0,end); line.remove_prefix(end); return w;
}
inline bool number(std::string_view& line, float& value) {
  // strtof, not from_chars: Apple's libc++ lacks the floating-point overloads.
  const std::string w(word(line)); char* end{};
  value=std::strtof(w.c_str(),&end);
  return !w.empty() && end==w.c_str()+w.size();
}
}
// Reads the metrics file the bake writes; an empty font when it is malformed.
inline Font parseFont(std::string_view text) {
  Font font;
  while(!text.empty()) {
    const auto end=std::min(text.find('\n'),text.size());
    std::string_view line=text.substr(0,end); text.remove_prefix(std::min(end+1,text.size()));
    if(!line.empty() && line.back()=='\r') line.remove_suffix(1);
    const auto kind=detail::word(line);
    if(kind=="bake") {
      FontBake bake; float pixels{};
      if(!detail::number(line,pixels)) return {};
      bake.pixels=pixels; bake.sheet=std::string(detail::word(line));
      if(bake.sheet.empty() || !detail::number(line,bake.ascent) || !detail::number(line,bake.descent)) return {};
      if(!font.bakes.empty() && font.bakes.back().pixels>=pixels) return {};
      font.bakes.push_back(std::move(bake));
    } else if(kind=="glyph") {
      float v[8];
      for(float& x: v) if(!detail::number(line,x)) return {};
      const int code=static_cast<int>(v[0]);
      if(font.bakes.empty() || code<0 || code>=128) return {};
      font.bakes.back().glyphs[code]={{v[1],v[2],v[3],v[4]},v[5],v[6],v[7],true};
    }
  }
  for(const auto& bake: font.bakes) if(!bake.glyphs['?'].present) return {};
  return font;
}
}
