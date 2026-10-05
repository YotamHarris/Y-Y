#include <tapdemo/palette.hpp>
#include <tapdemo/model.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

namespace tapdemo { std::unique_ptr<yy::Game> createGame(); }
namespace {
void check(bool ok, const char* label) { if(!ok) { std::cerr<<label<<'\n'; std::exit(1); } }
bool same(yy::Color a, yy::Color b) { return a.r==b.r && a.g==b.g && a.b==b.b && a.a==b.a; }
// Exercises the real Game pointer handlers and render output, without SDL/a window.
struct Canvas final: yy::Renderer {
  struct Text { yy::Vec2 at; std::string value; };
  std::vector<yy::Color> fills;
  std::vector<Text> texts;
  yy::Color field;
  void rectangle(yy::Rect r, yy::Color c) override {
    fills.push_back(c);
    if(r.w>0 && std::abs(r.h/r.w-tapdemo::Model::height()/tapdemo::Model::width())<0.0001f) field=c;
  }
  void circle(yy::Vec2, float, yy::Color) override {}
  void text(yy::Vec2 p, std::string_view v, yy::Color, float) override { texts.push_back({p,std::string(v)}); }
  bool sprite(std::string_view, yy::Rect) override { return false; }
  void read(yy::Game& game) { fills.clear(); texts.clear(); game.render(*this); }
  bool has(std::string_view value) const {
    return std::any_of(texts.begin(),texts.end(),[&](const Text& t){ return t.value==value; });
  }
  bool valueAt(float y, std::string_view value) const {
    return std::any_of(texts.begin(),texts.end(),[&](const Text& t){ return t.at.x>260 && t.at.y==y && t.value==value; });
  }
};
}

void paletteChecks() {
  using namespace tapdemo;
  for(const auto& p: palettes) {
    float smallest=1;
    for(auto fog: p.fog) {
      const float gap=luminance(fog)-luminance(p.field);
      check(gap>=minFogLuminanceGap,"every fog shade is clearly lighter than its scheme's cavity");
      smallest=std::min(smallest,gap);
    }
    check(luminance(p.fogTexture)>luminance(p.fog[0]),"fog texture remains visible");
    std::cout<<p.name<<" minimum fog/cavity luminance gap "<<smallest<<'\n';
  }

  auto game=createGame(); Canvas canvas;
  const auto tap=[&](yy::Vec2 p) { game->pointerDown(7,p); game->pointerUp(7,p); canvas.read(*game); };
  tap({330,40}); // DEBUG works even over the initial instructions.
  check(canvas.has("COLORS") && canvas.has("NAVY"),"debug opens with the default named palette");
  check(canvas.has("BALLS") && canvas.has("BOUNCES") && canvas.has("PING RADIUS"),"existing controls remain beside COLORS");
  tap({335,420}); // Existing ping control still applies immediately.
  check(canvas.valueAt(412,std::to_string(Model::defaultPingRadius+1)),"ping radius plus still works");
  tap({335,290}); tap({335,355}); // Pending ball/bounce settings.
  check(canvas.valueAt(282,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(347,std::to_string(Model::defaultBounces+1)),"existing pending controls work");
  for(std::size_t i=1; i<=palettes.size(); ++i) {
    const auto& p=palettes[i%palettes.size()];
    tap(i%2 ? yy::Vec2{45,470} : yy::Vec2{345,508}); // opposite edges of the 310x48 touch target
    check(canvas.has(p.name) && same(canvas.field,p.field),"COLORS changes the label and field immediately, and wraps");
    check(std::find_if(canvas.fills.begin(),canvas.fills.end(),[&](yy::Color c){return same(c,p.fog[0]);})!=canvas.fills.end(),"render uses the selected fog colour");
    tap({110,560}); // RESTART
    check(!canvas.has("COLORS") && canvas.has("TAP TO START") && same(canvas.field,p.field),"restart closes debug and keeps the selected palette");
    tap({330,40});
    check(canvas.has(p.name),"reopening debug keeps the selected scheme name");
    check(canvas.valueAt(412,std::to_string(Model::defaultPingRadius+1)),"restart also keeps the existing ping radius preference");
    check(canvas.valueAt(282,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(347,std::to_string(Model::defaultBounces+1)),"restart applies the existing pending settings");
  }
  tap({195,490}); // choose EMBER once more
  tap({275,560}); // CLOSE
  check(!canvas.has("COLORS") && same(canvas.field,palettes[1].field),"CLOSE keeps the scheme");
  tap({330,40}); check(canvas.has("EMBER"),"scheme survives close and reopen");
  std::cout<<"Debug touch path: cycle all schemes, wrap, restart, close, reopen passed\n";
}
