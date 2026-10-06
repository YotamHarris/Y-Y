#include <tapdemo/palette.hpp>
#include <tapdemo/model.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cmath>
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
  float fieldWidth{}, cellWidth{}; bool afterField{};
  void rectangle(yy::Rect r, yy::Color c) override {
    fills.push_back(c);
    // The first cell drawn after the field is the fogged top-left corner, one whole cell.
    if(afterField) { cellWidth=r.w; afterField=false; }
    constexpr float shape=static_cast<float>(tapdemo::Settings::shapeRows)/tapdemo::Settings::shapeColumns;
    if(r.w>0 && std::abs(r.h/r.w-shape)<0.0001f) { field=c; fieldWidth=r.w; afterField=true; }
  }
  int columns() const { return static_cast<int>(std::lround(fieldWidth/cellWidth)); }
  void circle(yy::Vec2, float, yy::Color) override {}
  void text(yy::Vec2 p, std::string_view v, yy::Color, float) override { texts.push_back({p,std::string(v)}); }
  bool sprite(std::string_view, yy::Rect) override { return false; }
  void read(yy::Game& game) { fills.clear(); texts.clear(); game.render(*this); }
  bool has(std::string_view value) const {
    return std::any_of(texts.begin(),texts.end(),[&](const Text& t){ return t.value==value; });
  }
  bool valueAt(float y, std::string_view value) const {
    return std::any_of(texts.begin(),texts.end(),[&](const Text& t){ return t.at.x>224 && t.at.x<320 && t.at.y==y && t.value==value; });
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
  // Every label and value lies on the 390x844 screen (the debug font is 8 units a character at scale 1).
  check(std::all_of(canvas.texts.begin(),canvas.texts.end(),[](const auto& t){ return t.at.x>=0 && t.at.y>=0 && t.at.y+16<=844; }),"every debug row fits the screen");
  tap({335,92}); // Existing ping control still applies immediately.
  check(canvas.valueAt(84,std::to_string(Model::defaultPingRadius+1)),"ping radius plus still works");
  tap({335,336}); tap({335,380}); // Pending ball/bounce settings.
  check(canvas.valueAt(328,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(372,std::to_string(Model::defaultBounces+1)),"existing pending controls work");
  for(std::size_t i=1; i<=palettes.size(); ++i) {
    const auto& p=palettes[i%palettes.size()];
    tap(i%2 ? yy::Vec2{45,744} : yy::Vec2{345,780}); // opposite edges of the 310x40 touch target
    check(canvas.has(p.name) && same(canvas.field,p.field),"COLORS changes the label and field immediately, and wraps");
    check(std::find_if(canvas.fills.begin(),canvas.fills.end(),[&](yy::Color c){return same(c,p.fog[0]);})!=canvas.fills.end(),"render uses the selected fog colour");
    tap({110,812}); // RESTART
    check(!canvas.has("COLORS") && canvas.has("TAP TO START") && same(canvas.field,p.field),"restart closes debug and keeps the selected palette");
    tap({330,40});
    check(canvas.has(p.name),"reopening debug keeps the selected scheme name");
    check(canvas.valueAt(84,std::to_string(Model::defaultPingRadius+1)),"restart also keeps the existing ping radius preference");
    check(canvas.valueAt(328,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(372,std::to_string(Model::defaultBounces+1)),"restart applies the existing pending settings");
  }
  tap({195,762}); // choose EMBER once more
  tap({275,812}); // CLOSE
  check(!canvas.has("COLORS") && same(canvas.field,palettes[1].field),"CLOSE keeps the scheme");
  tap({330,40}); check(canvas.has("EMBER"),"scheme survives close and reopen");
  std::cout<<"Debug touch path: cycle all schemes, wrap, restart, close, reopen passed\n";

  // The grid settings: the steppers change the pending values and RESTART builds the new board.
  check(canvas.has("GRID SIZE") && canvas.valueAt(416,"24X40") && canvas.valueAt(460,"2%") && canvas.has("POWER-UP WEIGHTS, ON RESTART"),"debug shows the grid size and glow rate");
  for(const char* name: {"BOMB","ELECTRIC","PING","GHOST","SPEED"}) check(canvas.has(name),"debug shows a weight for each power-up");
  check(canvas.columns()==24,"the board starts 24 columns wide");
  tap({205,424}); tap({205,424}); tap({205,424}); // grid size - (stops at its smallest)
  check(canvas.valueAt(416,"12X20"),"the grid size steps down to 12x20");
  tap({205,468}); check(canvas.valueAt(460,"1.5%"),"the glow rate steps by half a percent");
  for(int i=0; i<10; ++i) tap({335,468});
  check(canvas.valueAt(460,"8%"),"above 5% it steps by whole percents");
  tap({205,536}); check(canvas.valueAt(528,"0"),"a weight steps down to 0");
  tap({335,712}); check(canvas.valueAt(704,"2"),"a weight steps up");
  check(canvas.columns()==24,"the pending settings leave the board alone");
  tap({110,812}); // RESTART
  check(!canvas.has("GRID SIZE") && canvas.columns()==12,"restart builds the 12x20 board, fitted to the play area");
  tap({330,40});
  check(canvas.valueAt(416,"12X20") && canvas.valueAt(460,"8%") && canvas.valueAt(528,"0") && canvas.valueAt(704,"2"),"reopening debug shows the applied settings");
  for(int i=0; i<12; ++i) tap({335,424});
  check(canvas.valueAt(416,"60X100"),"the grid size steps up to 60x100");
  tap({110,812});
  check(canvas.columns()==60,"restart builds the 60x100 board");
  std::cout<<"Debug touch path: grid size, glow rate and weights apply on restart passed\n";

  // The power-up tuning: bomb size, lightning time and reach, and the snap angle apply at once.
  auto tuned=createGame(); Canvas panel;
  const auto press=[&](yy::Vec2 p) { tuned->pointerDown(7,p); tuned->pointerUp(7,p); panel.read(*tuned); };
  press({330,40});
  check(panel.has("BOMB SIZE") && panel.has("ZAP SECONDS") && panel.has("ZAP REACH") && panel.has("SNAP ANGLE"),"debug shows the power-up tuning rows");
  check(panel.valueAt(128,"5X5") && panel.valueAt(172,"6") && panel.valueAt(216,"2.5") && panel.valueAt(260,"5 DEG"),"the tuning starts at its defaults");
  press({335,136}); check(panel.valueAt(128,"7X7"),"bomb size steps by 2");
  for(int i=0; i<9; ++i) press({205,136});
  check(panel.valueAt(128,"3X3"),"bomb size stops at 3x3");
  press({335,180}); check(panel.valueAt(172,"7"),"lightning seconds step up");
  press({335,224}); check(panel.valueAt(216,"3"),"lightning reach steps by half a cell");
  for(int i=0; i<5; ++i) press({205,268});
  check(panel.valueAt(260,"OFF"),"a snap angle of 0 shows OFF");
  press({110,812}); press({330,40}); // RESTART, then reopen
  check(panel.valueAt(128,"3X3") && panel.valueAt(172,"7") && panel.valueAt(216,"3") && panel.valueAt(260,"OFF"),"restart keeps the tuning");
  std::cout<<"Debug touch path: bomb size, lightning and snap angle apply at once and survive restart passed\n";
}
