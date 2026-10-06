#include <yy/font.hpp>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

namespace {
void check(bool ok, const char* label) { if(!ok) { std::cerr<<label<<'\n'; std::exit(1); } }
bool near(float a, float b) { return std::abs(a-b)<0.001f; }
// Two bakes of three glyphs: 'A', '?' and a space, in the format scripts/generate-assets.py writes.
constexpr const char* sample=
  "# test\r\n"
  "bake 10 small.bmp 8 2\n"
  "glyph 32 0 0 0 0 -2 -2 3\n"
  "glyph 65 0 0 10 12 -2 -9 6\n"
  "glyph 63 10 0 8 12 -2 -9 5\n"
  "bake 20 large.bmp 16 4\n"
  "glyph 32 0 0 0 0 -2 -2 6.5\n"
  "glyph 65 0 0 16 20 -2 -18 12.5\n"
  "glyph 63 16 0 14 20 -2 -18 10\n";
}

void fontChecks() {
  const yy::Font font=yy::parseFont(sample);
  check(font.bakes.size()==2 && font.bakes[1].sheet=="large.bmp" && near(font.bakes[1].ascent,16),"font metrics parse every bake");
  check(font.bakes[0].glyph('A').present && near(font.bakes[0].glyph('A').source.w,10),"a glyph is found by its character");
  check(&font.bakes[0].glyph('z')==&font.bakes[0].glyph('?') && &font.bakes[0].glyph('\xe9')==&font.bakes[0].glyph('?'),"a missing or non-ASCII character draws as '?'");
  check(font.pick(8)==0 && font.pick(10)==0 && font.pick(11)==1 && font.pick(60)==1,"the smallest bake at least as large as the text, else the largest");
  check(near(font.width("A A",10),15) && near(font.width("A A",20),31.5f),"width sums advances in the chosen bake's scale");
  check(near(font.width("A",40),25) && near(font.width("A",10,2),12.5f/2),"width scales a bake to the asked size and screen density");
  const auto left=font.layout({100,50},"A A",10,yy::Align::Left);
  check(left.bake==0 && left.quads.size()==2,"spaces advance without a quad");
  check(near(left.quads[0].destination.x,98) && near(left.quads[0].destination.y,50+8-9) && near(left.quads[1].destination.x,100+9-2),
        "left-aligned glyphs sit on the baseline from the line top");
  const auto centre=font.layout({100,50},"A A",10,yy::Align::Center), right=font.layout({100,50},"A A",10,yy::Align::Right);
  check(near(centre.quads[0].destination.x,98-7.5f) && near(right.quads[0].destination.x,98-15),"centre and right alignment shift by half and all of the width");
  const auto big=font.layout({0,0},"A",30,yy::Align::Left,1);
  check(big.bake==1 && near(big.quads[0].destination.w,24) && near(big.quads[0].source.w,16),"a larger size draws from the larger bake, scaled");
  check(yy::parseFont("bake 10 a.bmp 8\n").bakes.empty() && yy::parseFont("glyph 65 0 0 1 1 0 0 1\n").bakes.empty()
        && yy::parseFont("bake 20 a.bmp 8 2\nglyph 63 0 0 1 1 0 0 1\nbake 10 b.bmp 8 2\nglyph 63 0 0 1 1 0 0 1\n").bakes.empty(),
        "malformed metrics give an empty font");
  check(yy::Font{}.layout({0,0},"A",10,yy::Align::Left).quads.empty() && near(yy::Font{}.width("A",10),0),"an empty font lays out nothing");

  // The committed bake: every printable character in every size, and big digits for the ball counter.
  std::ifstream in(YY_SOURCE_DIR "/games/tapdemo/assets/fonts/fredoka.font",std::ios::binary);
  const yy::Font fredoka=yy::parseFont(std::string(std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()));
  check(fredoka.bakes.size()>=3 && fredoka.bakes.back().pixels>=96,"the committed font has bakes up to header size on a 3x phone");
  for(const auto& bake: fredoka.bakes) for(int c=33; c<127; ++c) check(bake.glyphs[c].present && bake.glyphs[c].source.w>0,"every printable ASCII glyph is baked with ink");
  const auto& largest=fredoka.bakes.back();
  for(char c='0'; c<='9'; ++c) check(largest.glyph(c).source.h>=0.6f*largest.pixels,"the largest bake's digits are full height, for the ball counter");
}
