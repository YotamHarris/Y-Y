#include <tapdemo/palette.hpp>
#include <tapdemo/model.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <string_view>
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
  bool label(std::string_view, yy::Vec2, std::string_view, float, yy::Color, yy::Align) override { return false; }
  bool sprite(std::string_view, yy::Rect) override { return false; }
  bool sprite(std::string_view, yy::Rect, yy::Rect) override { return false; }
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
  tap({335,85}); // Existing ping control still applies immediately.
  check(canvas.valueAt(78,std::to_string(Model::defaultPingRadius+1)),"ping radius plus still works");
  tap({335,359}); tap({335,401}); // Pending ball/bounce settings.
  check(canvas.valueAt(352,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(394,std::to_string(Model::defaultBounces+1)),"existing pending controls work");
  for(std::size_t i=1; i<=palettes.size(); ++i) {
    const auto& p=palettes[i%palettes.size()];
    tap(i%2 ? yy::Vec2{45,750} : yy::Vec2{345,780}); // opposite edges of the 310x38 touch target
    check(canvas.has(p.name) && same(canvas.field,p.field),"COLORS changes the label and field immediately, and wraps");
    check(std::find_if(canvas.fills.begin(),canvas.fills.end(),[&](yy::Color c){return same(c,p.fog[0]);})!=canvas.fills.end(),"render uses the selected fog colour");
    tap({110,812}); // RESTART
    check(!canvas.has("COLORS") && canvas.has("TAP TO START") && same(canvas.field,p.field),"restart closes debug and keeps the selected palette");
    tap({330,40});
    check(canvas.has(p.name),"reopening debug keeps the selected scheme name");
    check(canvas.valueAt(78,std::to_string(Model::defaultPingRadius+1)),"restart also keeps the existing ping radius preference");
    check(canvas.valueAt(352,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(394,std::to_string(Model::defaultBounces+1)),"restart applies the existing pending settings");
  }
  tap({195,762}); // choose EMBER once more
  tap({275,812}); // CLOSE
  check(!canvas.has("COLORS") && same(canvas.field,palettes[1].field),"CLOSE keeps the scheme");
  tap({330,40}); check(canvas.has("EMBER"),"scheme survives close and reopen");
  std::cout<<"Debug touch path: cycle all schemes, wrap, restart, close, reopen passed\n";

  // The grid settings: the steppers change the pending values and RESTART builds the new board.
  check(canvas.has("GRID SIZE") && canvas.valueAt(436,"24X40") && canvas.valueAt(478,"2%") && canvas.has("FREE PLAY WEIGHTS"),"debug shows the grid size and glow rate");
  for(const char* name: {"BOMB","ELECTRIC","PING","GHOST","SPEED"}) check(canvas.has(name),"debug shows a weight for each power-up");
  check(canvas.columns()==24,"the board starts 24 columns wide");
  tap({205,443}); tap({205,443}); tap({205,443}); // grid size - (stops at its smallest)
  check(canvas.valueAt(436,"12X20"),"the grid size steps down to 12x20");
  tap({205,485}); check(canvas.valueAt(478,"1.5%"),"the glow rate steps by half a percent");
  for(int i=0; i<10; ++i) tap({335,485});
  check(canvas.valueAt(478,"8%"),"above 5% it steps by whole percents");
  tap({205,549}); check(canvas.valueAt(542,"0"),"a weight steps down to 0");
  tap({335,717}); check(canvas.valueAt(710,"2"),"a weight steps up");
  check(canvas.columns()==24,"the pending settings leave the board alone");
  tap({110,812}); // RESTART
  check(!canvas.has("GRID SIZE") && canvas.columns()==12,"restart builds the 12x20 board, fitted to the play area");
  tap({330,40});
  check(canvas.valueAt(436,"12X20") && canvas.valueAt(478,"8%") && canvas.valueAt(542,"0") && canvas.valueAt(710,"2"),"reopening debug shows the applied settings");
  for(int i=0; i<12; ++i) tap({335,443});
  check(canvas.valueAt(436,"60X100"),"the grid size steps up to 60x100");
  tap({110,812});
  check(canvas.columns()==60,"restart builds the 60x100 board");
  std::cout<<"Debug touch path: grid size, glow rate and weights apply on restart passed\n";

  // The power-up tuning: bomb size, lightning time and reach, and the snap angle apply at once.
  auto tuned=createGame(); Canvas panel;
  const auto press=[&](yy::Vec2 p) { tuned->pointerDown(7,p); tuned->pointerUp(7,p); panel.read(*tuned); };
  press({330,40});
  check(panel.has("BOMB SIZE") && panel.has("ZAP SECONDS") && panel.has("ZAP REACH") && panel.has("SNAP ANGLE"),"debug shows the power-up tuning rows");
  check(panel.valueAt(120,"5X5") && panel.valueAt(162,"6") && panel.valueAt(204,"2.5") && panel.valueAt(246,"5 DEG"),"the tuning starts at its defaults");
  press({335,127}); check(panel.valueAt(120,"7X7"),"bomb size steps by 2");
  for(int i=0; i<9; ++i) press({205,127});
  check(panel.valueAt(120,"3X3"),"bomb size stops at 3x3");
  press({335,169}); check(panel.valueAt(162,"7"),"lightning seconds step up");
  press({335,211}); check(panel.valueAt(204,"3"),"lightning reach steps by half a cell");
  for(int i=0; i<5; ++i) press({205,253});
  check(panel.valueAt(246,"OFF"),"a snap angle of 0 shows OFF");
  press({110,812}); press({330,40}); // RESTART, then reopen
  check(panel.valueAt(120,"3X3") && panel.valueAt(162,"7") && panel.valueAt(204,"3") && panel.valueAt(246,"OFF"),"restart keeps the tuning");
  std::cout<<"Debug touch path: bomb size, lightning and snap angle apply at once and survive restart passed\n";
}

namespace {
struct MemoryStorage final: yy::Storage {
  std::string saved, debug; int writes{}; // the reached level, and the debug settings
  std::string& file(std::string_view name) { return name=="debug.txt" ? debug : saved; }
  std::string read(std::string_view name) override { return file(name); }
  bool write(std::string_view name, std::string_view text) override { file(name)=text; ++writes; return true; }
};
struct Quiet final: yy::Audio, yy::Haptics {
  void tone(float, float) override {}
  void impact(float) override {}
  void humStart(float) override {}
  void humStop() override {}
  void thump() override {}
};
void setVariable(const char* name, const char* value) {
#ifdef _WIN32
  _putenv_s(name,value ? value : "");
#else
  if(value) setenv(name,value,1); else unsetenv(name);
#endif
}
// A game opened the way the app opens it, on the given save, level pin and scene.
struct Session {
  Canvas canvas; Quiet quiet; MemoryStorage storage;
  std::unique_ptr<yy::Game> game=tapdemo::createGame();
  int frames{}; // played so far; the glow pulse follows the clock
  Session(const char* save, const char* level=nullptr, const char* scene=nullptr, const char* debug="", const char* scheme=nullptr) {
    storage.saved=save; storage.debug=debug;
    setVariable("YY_TAPDEMO_LEVEL",level); setVariable("YY_TAPDEMO_SCENE",scene); setVariable("YY_TAPDEMO_SCHEME",scheme);
    yy::Services services{canvas,quiet,quiet,storage};
    game->initialize(services);
    setVariable("YY_TAPDEMO_LEVEL",nullptr); setVariable("YY_TAPDEMO_SCENE",nullptr); setVariable("YY_TAPDEMO_SCHEME",nullptr);
    canvas.read(*game);
  }
  void tap(yy::Vec2 p) { game->pointerDown(7,p); game->pointerUp(7,p); canvas.read(*game); }
  // Plays frames until the end overlay shows `action`, at most ten seconds.
  bool until(std::string_view action) {
    for(int i=0; i<600; ++i) { play(1); if(canvas.has(action)) return true; }
    return false;
  }
  void play(int count) { for(int i=0; i<count; ++i) { game->update(1.0f/60); ++frames; } canvas.read(*game); }
  // The field's colours, with the instructions card dismissed by a tap on the board.
  std::vector<yy::Color> field() { if(canvas.has("TAP TO START")) tap({195,600}); return canvas.fills; }
};
bool sameFills(const std::vector<yy::Color>& a, const std::vector<yy::Color>& b) {
  return a.size()==b.size() && std::equal(a.begin(),a.end(),b.begin(),same);
}
}

// The debug panel survives a relaunch: every change is saved as it is made, the next session opens
// with it, and a pinned scheme or scene neither reads nor writes the save.
void debugPersistenceChecks() {
  std::string saved;
  {
    Session first("");
    first.tap({330,40});
    first.tap({335,127}); first.tap({335,359}); first.tap({335,443}); first.tap({335,549}); // bomb, balls, grid, bomb weight
    check(first.storage.writes==4 && first.storage.debug.rfind("debug 1\n",0)==0,"each debug change is saved as it is made");
    first.tap({195,762});
    check(first.storage.writes==5,"cycling the colours saves too");
    saved=first.storage.debug;
  }
  {
    Session second("",nullptr,nullptr,saved.c_str());
    check(second.storage.writes==0,"opening the app does not rewrite the settings");
    second.tap({330,40});
    check(second.canvas.has("EMBER") && second.canvas.valueAt(120,"7X7") && second.canvas.valueAt(352,"11") && second.canvas.valueAt(436,"30X50") && second.canvas.valueAt(542,"2"),
          "a relaunch brings the debug settings back");
  }
  {
    Session pinned("",nullptr,nullptr,saved.c_str(),"PLUM");
    pinned.tap({330,40}); pinned.tap({335,127});
    check(pinned.canvas.has("PLUM") && pinned.canvas.valueAt(120,"9X9") && pinned.storage.writes==0 && pinned.storage.debug==saved,"a pinned scheme overrides the saved one and saves nothing");
    Session scene("",nullptr,"debug",saved.c_str());
    scene.tap({335,127});
    check(scene.canvas.valueAt(120,"7X7") && scene.storage.writes==0 && scene.storage.debug==saved,"a scene uses the defaults and saves nothing");
  }
  {
    Session damaged("",nullptr,nullptr,"debug 1\nballs 12\nscheme 99\nbomb\n\x01");
    damaged.tap({330,40});
    check(damaged.canvas.valueAt(352,"12") && damaged.canvas.valueAt(120,"5X5") && damaged.canvas.has("PLUM"),"a damaged save still opens, clamped and defaulted");
  }
  std::cout<<"Debug touch path: settings survive a relaunch, pins neither read nor write them passed\n";
}

// The level flow through the real Game: the app opens at the saved level, a win offers and opens
// the next level and saves it, a loss retries the identical field, and DEBUG picks any level or
// free play.
void levelFlowChecks() {
  using namespace tapdemo;
  {
    Session fresh("");
    check(fresh.canvas.has("LEVEL 1") && fresh.canvas.has("NEW POWER-UP") && fresh.canvas.has("BOMB") && !fresh.canvas.has("GHOST"),"a new player opens level 1, whose card names the Bomb");
    check(fresh.storage.writes==0,"opening the app saves nothing");
  }
  {
    Session third("level 3\n");
    check(third.canvas.has("LEVEL 3") && third.canvas.has("NEW POWER-UP") && third.canvas.has("ELECTRICITY") && !third.canvas.has("BOMB"),"the app opens at the saved level, and its card names only Electricity");
    const auto before=third.field();
    third.tap({330,40});
    check(third.canvas.valueAt(310,"3"),"the debug level picker shows the current level");
    third.tap({110,812}); // RESTART on level 3
    check(third.canvas.has("LEVEL 3") && sameFills(third.field(),before),"restarting a level from debug rebuilds the identical field");
    third.tap({330,40}); third.tap({335,317}); // LEVEL +
    check(third.canvas.valueAt(310,"4"),"the picker steps to the next level");
    third.tap({110,812});
    check(third.canvas.has("LEVEL 4") && third.storage.saved=="level 4\n","a picked level opens and is saved");
    third.tap({330,40});
    for(int i=0; i<levelCount; ++i) third.tap({205,317});
    check(third.canvas.valueAt(310,"FREE"),"the picker's lowest entry is free play");
    third.tap({110,812});
    check(third.canvas.has("FREE PLAY") && third.canvas.columns()==24 && third.storage.saved=="level 4\n","free play opens today's random 24x40 field and keeps the saved level");
    third.tap({330,40});
    for(int i=0; i<levelCount; ++i) third.tap({335,317});
    check(third.canvas.valueAt(310,"10"),"the picker stops at level 10");
  }
  {
    Session win("level 5\n","1","won");
    check(win.canvas.has("LEVEL 1"),"a pinned level opens instead of the saved one");
    check(win.until("TAP FOR NEXT LEVEL") && win.canvas.has("GOAL FOUND"),"breaking the goal offers the next level");
    win.tap({195,420});
    check(win.canvas.has("LEVEL 2") && win.canvas.has("TAP TO START") && win.storage.saved=="level 2\n","the next level opens on its card and is saved");
  }
  {
    Session lose("","2","lost");
    check(lose.until("TAP TO RETRY") && lose.canvas.has("OUT OF BALLS"),"running out of balls offers a retry");
    lose.tap({195,420});
    check(lose.canvas.has("LEVEL 2") && !lose.canvas.has("TAP TO START") && lose.storage.writes==0,"a retry goes straight back into the same level");
    Session again("","2");
    again.play(lose.frames);
    check(sameFills(lose.canvas.fills,again.field()),"the retry is the identical field, brick for brick");
  }
  {
    Session last("","10","won");
    check(last.until("TAP FOR FREE PLAY"),"winning level 10 leads to free play");
    last.tap({195,420});
    check(last.canvas.has("FREE PLAY"),"after the last level the game plays free");
  }
  std::cout<<"Level flow: opens at the save, win opens and saves the next, loss retries the same field, debug picks levels\n";
}
