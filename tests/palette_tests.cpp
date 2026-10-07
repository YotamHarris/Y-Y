#include <tapdemo/palette.hpp>
#include <tapdemo/garden.hpp>
#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <tapdemo/celebration.hpp>
#include <tapdemo/juice.hpp>
#include <tapdemo/pace.hpp>
#include "level_bot.hpp"
#include <optional>
#include <yy/runtime.hpp>
#include <algorithm>
#include <array>
#include <utility>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <string_view>
#include <iostream>
#include <string>
#include <vector>
#include <fstream>
#include <iterator>

namespace tapdemo { std::unique_ptr<yy::Game> createGame(); }
namespace {
void check(bool ok, const char* label) { if(!ok) { std::cerr<<label<<'\n'; std::exit(1); } }
bool same(yy::Color a, yy::Color b) { return a.r==b.r && a.g==b.g && a.b==b.b && a.a==b.a; }
// Exercises the real Game pointer handlers and render output, without SDL/a window.
struct Canvas final: yy::Renderer {
  struct Text { yy::Vec2 at; std::string value; };
  std::vector<yy::Color> fills;
  std::vector<Text> texts;
  struct Sprite { std::string asset; yy::Rect source, destination; int order; };
  std::vector<Sprite> sprites;
  struct Frame { std::vector<yy::Color> fills; std::vector<Sprite> sprites; };
  Frame frame() const { return {fills,sprites}; }
  int order{}, lastAimDot{};
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
  void circle(yy::Vec2 p, float, yy::Color c) override { ++order; if(p.y>=80 && same(c,tapdemo::garden.white)) lastAimDot=order; }
  void text(yy::Vec2 p, std::string_view v, yy::Color, float) override { texts.push_back({p,std::string(v)}); }
  bool label(std::string_view, yy::Vec2 p, std::string_view v, float, yy::Color, yy::Align) override { texts.push_back({p,std::string(v)}); return true; }
  bool sprite(std::string_view a, yy::Rect d) override { return sprite(a,{},d); }
  bool sprite(std::string_view a, yy::Rect s, yy::Rect d) override {
    if(afterField && a=="garden/tiles.bmp") { cellWidth=d.w/tapdemo::gardenTextureCells; afterField=false; }
    sprites.push_back({std::string(a),s,d,++order}); return true;
  }
  void read(yy::Game& game) { fills.clear(); texts.clear(); sprites.clear(); order=lastAimDot=0; game.render(*this); }
  bool spriteHas(tapdemo::GardenSprite kind) const {
    const auto rect=tapdemo::gardenSource(kind);
    return std::any_of(sprites.begin(),sprites.end(),[&](const Sprite& s){return s.asset=="garden/tiles.bmp" && s.source.x>=rect.x && s.source.x+s.source.w<=rect.x+rect.w && s.source.y>=rect.y && s.source.y+s.source.h<=rect.y+rect.h;});
  }
  bool spriteAt(tapdemo::GardenSprite kind, yy::Vec2 center) const {
    const auto rect=tapdemo::gardenSource(kind);
    return std::any_of(sprites.begin(),sprites.end(),[&](const Sprite& s){
      return s.asset=="garden/tiles.bmp" && s.source.x==rect.x && s.source.y==rect.y &&
        std::abs(s.destination.x+s.destination.w/2-center.x)<.01f && std::abs(s.destination.y+s.destination.h/2-center.y)<.01f;
    });
  }
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
  float smallest=1;
  for(auto fog: garden.fog) {
    const float gap=luminance(fog)-luminance(garden.field);
    check(gap>=minFogLuminanceGap,"every fog shade is clearly lighter than Garden Pop's cavity");
    smallest=std::min(smallest,gap);
  }
  std::cout<<"Garden Pop minimum fog/cavity luminance gap "<<smallest<<'\n';

  auto game=createGame(); Canvas canvas;
  const auto tap=[&](yy::Vec2 p) { game->pointerDown(7,p); game->pointerUp(7,p); canvas.read(*game); };
  tap({330,40}); // DEBUG works even over the initial instructions.
  check(canvas.has("DEBUG") && same(canvas.field,garden.field),"debug opens over the Garden Pop field");
  check(!canvas.has("COLORS") && !canvas.has("NAVY") && !canvas.has("EMBER") && !canvas.has("FOREST") && !canvas.has("PLUM") && !canvas.has("GARDEN POP"),"the debug panel has no COLORS button or scheme name");
  check(canvas.has("BALLS") && canvas.has("BOUNCES") && canvas.has("PING RADIUS") && canvas.has("RESTART") && canvas.has("CLOSE"),"the other controls remain");
  // Every label and value lies on the 390x844 screen (the debug font is 8 units a character at scale 1).
  check(std::all_of(canvas.texts.begin(),canvas.texts.end(),[](const auto& t){ return t.at.x>=0 && t.at.y>=0 && t.at.y+16<=844; }),"every debug row fits the screen");
  tap({335,85}); // Existing ping control still applies immediately.
  check(canvas.valueAt(78,std::to_string(Model::defaultPingRadius+1)),"ping radius plus still works");
  tap({335,359}); tap({335,401}); // Pending ball/bounce settings.
  check(canvas.valueAt(352,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(394,std::to_string(Model::defaultBounces+1)),"existing pending controls work");
  tap({110,768}); // RESTART, where COLORS used to sit above it
  check(!canvas.has("CLOSE") && canvas.has("TAP TO START") && same(canvas.field,garden.field) && canvas.spriteHas(GardenSprite::Mist),"restart closes debug and the field is Garden Pop");
  tap({330,40});
  check(canvas.valueAt(78,std::to_string(Model::defaultPingRadius+1)),"restart also keeps the existing ping radius preference");
  check(canvas.valueAt(352,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(394,std::to_string(Model::defaultBounces+1)),"restart applies the existing pending settings");
  tap({195,725}); // between the last weight row and RESTART: inside the panel, no button
  check(canvas.has("CLOSE"),"an empty spot in the panel does nothing");
  tap({275,768}); // CLOSE
  check(!canvas.has("CLOSE") && same(canvas.field,garden.field),"CLOSE keeps Garden Pop");
  tap({330,40});
  std::cout<<"Debug touch path: no COLORS button, restart, close, reopen passed\n";

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
  tap({110,768}); // RESTART
  check(!canvas.has("GRID SIZE") && canvas.columns()==12,"restart builds the 12x20 board, fitted to the play area");
  tap({330,40});
  check(canvas.valueAt(436,"12X20") && canvas.valueAt(478,"8%") && canvas.valueAt(542,"0") && canvas.valueAt(710,"2"),"reopening debug shows the applied settings");
  for(int i=0; i<12; ++i) tap({335,443});
  check(canvas.valueAt(436,"60X100"),"the grid size steps up to 60x100");
  tap({110,768});
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
  press({110,768}); press({330,40}); // RESTART, then reopen
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
  std::vector<float> tones, impacts; int thumps{};
  void tone(float hz, float) override { tones.push_back(hz); }
  void impact(float strength) override { impacts.push_back(strength); }
  void humStart(float) override {}
  void humStop() override {}
  void thump() override { ++thumps; }
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
  Session(const char* save, const char* level=nullptr, const char* scene=nullptr, const char* debug="") {
    storage.saved=save; storage.debug=debug;
    setVariable("YY_TAPDEMO_LEVEL",level); setVariable("YY_TAPDEMO_SCENE",scene);
    yy::Services services{canvas,quiet,quiet,storage};
    game->initialize(services);
    setVariable("YY_TAPDEMO_LEVEL",nullptr); setVariable("YY_TAPDEMO_SCENE",nullptr);
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
  Canvas::Frame field() { if(canvas.has("TAP TO START")) tap({195,600}); return canvas.frame(); }
};
bool sameFills(const Canvas::Frame& a, const Canvas::Frame& b) {
  const auto rect=[](yy::Rect x,yy::Rect y){ return x.x==y.x && x.y==y.y && x.w==y.w && x.h==y.h; };
  return a.fills.size()==b.fills.size() && std::equal(a.fills.begin(),a.fills.end(),b.fills.begin(),same) &&
    a.sprites.size()==b.sprites.size() && std::equal(a.sprites.begin(),a.sprites.end(),b.sprites.begin(),[&](const auto& x,const auto& y){
      return x.asset==y.asset && rect(x.source,y.source) && rect(x.destination,y.destination);
    });
}
}

void gardenChecks() {
  using namespace tapdemo;
  // Read actual production BMP texels, without SDL, including the exposed rim pieces.
  std::ifstream in(YY_SOURCE_DIR "/games/tapdemo/assets/garden/tiles.bmp",std::ios::binary);
  const std::vector<unsigned char> bytes{std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
  const auto u32=[&](int at) { return static_cast<unsigned>(bytes.at(at)) | static_cast<unsigned>(bytes.at(at+1))<<8 |
    static_cast<unsigned>(bytes.at(at+2))<<16 | static_cast<unsigned>(bytes.at(at+3))<<24; };
  check(bytes.size()>122 && bytes[0]=='B' && bytes[1]=='M' && u32(14)==108 && u32(66)==0xff000000,"garden sprites use explicit BMP V4 alpha");
  const auto width=u32(18),height=u32(22),offset=u32(10);
  check(width==1040 && height==1040 && bytes.size()==offset+width*height*4,"production sheet dimensions and byte count are valid");
  const auto pixel=[&](int x,int y) {
    const auto at=offset+((height-1-y)*width+x)*4;
    return yy::Color{bytes[at+2],bytes[at+1],bytes[at],bytes[at+3]};
  };
  float minMist=1,maxClear=luminance(garden.field);
  for(auto kind: {GardenSprite::Mist,GardenSprite::RimH,GardenSprite::RimV,GardenSprite::RimCorner,GardenSprite::Clear}) {
    const auto source=gardenSource(kind);
    for(int y=static_cast<int>(source.y);y<source.y+source.h;++y) for(int x=static_cast<int>(source.x);x<source.x+source.w;++x) {
      const auto c=pixel(x,y); check(c.a==255,"mist, rim and cleared-soil samples are opaque at every interior texel");
      if(kind==GardenSprite::Clear) maxClear=std::max(maxClear,luminance(c));
      else minMist=std::min(minMist,luminance(c));
    }
  }
  check(minMist-maxClear>=minFogLuminanceGap,"darkest production mist/rim is at least 0.08 above brightest cavity");
  check(pixel(0,0).a==0 && pixel(259,259).a==0,"atlas has transparent gutters against filtering bleed");
  std::cout<<"Garden production BMP minimum mist/cavity gap "<<minMist-maxClear<<'\n';
  {
    Session card("","10");
    check(card.canvas.has("EACH HIT STRIPS A LAYER") && card.canvas.has("LAST HIT OPENS THE PATH"),"garden card teaches material damage");
    for(auto kind: {GardenSprite::Grass,GardenSprite::Cut,GardenSprite::Soil,GardenSprite::Flag}) check(card.canvas.spriteHas(kind),"card shows progression pictures and ladybird pennant");
    card.tap({195,600}); check(!card.canvas.has("TAP TO START"),"touch dismisses the card without shooting");
    card.tap({330,40}); card.tap({110,768}); check(card.canvas.has("TAP TO START"),"debug restart opens the card again");
  }
  {
    // Saves from before Garden Pop was the only look carry a scheme line: any value, in or out of range,
    // is ignored; everything else in the save is kept and the game opens in Garden Pop.
    for(const char* scheme: {"0","3","9","-4"}) {
      const std::string text=std::string("debug 1\nballs 12\nbounces 9\ngrid 3\nglow 12\nscheme ")+scheme+"\nweights 0 4 2 5 3\n";
      Session old("",nullptr,nullptr,text.c_str());
      check(same(old.canvas.field,garden.field) || old.canvas.has("TAP TO START"),"an old save opens in Garden Pop");
      old.tap({330,40});
      check(!old.canvas.has("COLORS") && !old.canvas.has("NAVY") && !old.canvas.has("GARDEN POP"),"the debug panel of an old save has no COLORS button");
      check(old.canvas.valueAt(352,"12") && old.canvas.valueAt(394,"9") && old.canvas.valueAt(436,"18X30") && old.canvas.valueAt(478,"6%"),"an old save keeps its balls, bounces, grid and glow");
      check(old.canvas.valueAt(542,"0") && old.canvas.valueAt(584,"4") && old.canvas.valueAt(626,"2") && old.canvas.valueAt(668,"5") && old.canvas.valueAt(710,"3"),"an old save keeps its weights");
      check(old.storage.debug==text,"opening the debug panel on an old save rewrites nothing until a setting changes");
      old.tap({205,317}); // LEVEL -: free play, which uses the saved grid
      old.tap({110,768}); // RESTART
      check(same(old.canvas.field,garden.field) && old.canvas.spriteHas(GardenSprite::Mist) && old.canvas.spriteHas(GardenSprite::Grass) && old.canvas.columns()==18,"an old save restarts into a Garden Pop field of its own grid");
      old.tap({330,40}); old.tap({335,127});
      check(old.storage.debug.find("scheme")==std::string::npos && old.storage.debug.find("balls 12\n")!=std::string::npos,"the next save has no scheme line and keeps the other settings");
    }
  }
  {
    Session damage("","1","garden-damage");
    Model reference; reference.play(1); const auto& pocket=reference.pockets.front();
    const yy::Vec2 center{(pocket.column+pocket.columns/2.0f)*Model::cell,(pocket.row+pocket.rows/2.0f)*Model::cell};
    Camera camera; camera.world={reference.width(),reference.height()}; camera.hold(center,{195,480},1.4f);
    const GardenSprite states[]{GardenSprite::Grass,GardenSprite::Cut,GardenSprite::Soil};
    for(int i=0;i<3;++i) check(damage.canvas.spriteAt(states[i],camera.toScreen({(pocket.column+i+.5f)*Model::cell,(pocket.row-.5f)*Model::cell})),"3/2/1 hit-point fixture cells select dense/cut/bare sprites in their exact unchanged positions");
    for(auto kind: {GardenSprite::Grass,GardenSprite::Cut,GardenSprite::Soil,GardenSprite::Clear,GardenSprite::Mist}) check(damage.canvas.spriteHas(kind),"actual Garden board renders each material and cavity");
    check(std::none_of(damage.canvas.texts.begin(),damage.canvas.texts.end(),[](const auto& t){return t.at.y>=80 && t.value.size()==1 && t.value[0]>='0' && t.value[0]<='9';}),"ordinary Garden bricks draw no digits");
    damage.game->pointerDown(1,{145,440}); damage.game->pointerDown(2,{245,440});
    damage.game->pointerMove(1,{-1000,440}); damage.game->pointerMove(2,{1000,440}); damage.canvas.read(*damage.game);
    check(std::abs(damage.canvas.fieldWidth-12*Model::cell*Camera::maxZoom)<.01f,"two-finger touch reaches maximum zoom without changing the grid");
    damage.game->pointerMove(1,{195,440}); damage.game->pointerMove(2,{196,440}); damage.canvas.read(*damage.game);
    check(std::abs(damage.canvas.fieldWidth-390)<.01f,"two-finger touch reaches fit zoom without changing the grid");
    damage.game->pointerUp(1,{195,440}); damage.game->pointerUp(2,{196,440});
  }
  {
    Session shot("","1","garden-shot");
    for(int i=0;i<120 && !shot.canvas.spriteHas(GardenSprite::Flash);++i) shot.play(1);
    check(shot.canvas.spriteHas(GardenSprite::Flash) && shot.canvas.spriteHas(GardenSprite::Cut),"ordinary pointer shot removes one grass layer and starts contact flash");
    Model reference; reference.play(1); const auto& p=reference.pockets.front();
    const yy::Vec2 centre{(p.column+p.columns/2.0f)*Model::cell,(p.row+p.rows/2.0f)*Model::cell};
    Camera camera; camera.world={reference.width(),reference.height()}; camera.hold(centre,{195,480},1.4f);
    const auto at=camera.toScreen({(p.column+p.columns/2+0.5f)*Model::cell,centre.y});
    const auto struck=camera.toScreen({(p.column+p.columns/2+.5f)*Model::cell,(p.row-.5f)*Model::cell});
    check(shot.canvas.spriteAt(GardenSprite::Cut,struck),"the struck three-hit cell is now the two-hit sprite at its unchanged center");
    shot.game->pointerDown(8,at); shot.game->pointerMove(8,{at.x+30,at.y+50}); shot.canvas.read(*shot.game);
    int effectOrder=0;
    for(const auto& s: shot.canvas.sprites) if(s.source.x==gardenSource(GardenSprite::Flash).x && s.source.y==gardenSource(GardenSprite::Flash).y) effectOrder=std::max(effectOrder,s.order);
    check(effectOrder>0 && shot.canvas.lastAimDot>effectOrder,"aim draws above the local hit effect while next shot is held");
    shot.game->pointerUp(8,{at.x+30,at.y+50}); shot.canvas.read(*shot.game);
    check(std::any_of(shot.canvas.texts.begin(),shot.canvas.texts.end(),[](const auto& t){return t.at.x==62 && t.at.y==13 && t.value==std::to_string(levels[0].balls-2);}),"next pointer release spends another ball during the first hit's flash");
    // A separate single-shot run isolates the complete visual lifetime from the next collision.
    Session timing("","1","garden-shot");
    for(int i=0;i<120 && !timing.canvas.spriteHas(GardenSprite::Flash);++i) timing.play(1);
    timing.play(4); check(timing.canvas.spriteHas(GardenSprite::Burst),"hit reaches clipping burst at 67 ms");
    timing.play(4); check(timing.canvas.spriteHas(GardenSprite::Clippings),"hit reaches clear-centred settle at 133 ms");
    timing.play(5); check(!timing.canvas.spriteHas(GardenSprite::Flash) && !timing.canvas.spriteHas(GardenSprite::Burst) && !timing.canvas.spriteHas(GardenSprite::Clippings),"hit is gone by 217 ms, with stable cut-grass damage");
    check(timing.canvas.spriteAt(GardenSprite::Cut,struck),"the cosmetic sequence settles to the real damage at the same grid position");
  }
  const char* scenes[]{"garden-BOMB","garden-ELECTRIC","garden-PING","garden-GHOST","garden-SPEED"};
  const float tones[]{110,1320,1760,392,880};
  for(int k=0;k<5;++k) {
    Session power("","0",scenes[k]);
    for(int i=0;i<120 && power.quiet.tones.empty();++i) power.play(1);
    check(std::find(power.quiet.tones.begin(),power.quiet.tones.end(),tones[k])!=power.quiet.tones.end(),"real Game pointer shot triggers each of the five powers");
  }
  Session goal("","1","won"); check(goal.until("GOAL FOUND"),"pointer shot finds and breaks staged goal through normal win logic");
  std::cout<<"Garden touch path: ordinary hit, simultaneous aim/shot, five powers, goal, both pinch limits and card passed\n";
}

void framingGameChecks() {
  using namespace tapdemo;
  for(int level: {1,6,10}) {
    const std::string pin=std::to_string(level);
    Session play("",pin.c_str());
    Model reference; reference.play(level); Touch touch(reference);
    const float opening=reference.width()*touch.camera.zoom;
    check(std::abs(play.canvas.fieldWidth-opening)<.02f,"the real game opens on the computed cavity frame");
    play.tap({195,600}); // instructions, no placement
    play.game->zoom({195,462},2); play.canvas.read(*play.game);
    const float manual=play.canvas.fieldWidth;
    check(manual>opening,"the game's wheel handler zooms the opening frame");
    play.play(60);
    check(std::abs(play.canvas.fieldWidth-manual)<.02f,"the game keeps the player's wheel frame across updates");
    play.tap({330,40}); play.tap({110,768}); // debug RESTART at the same level
    check(std::abs(play.canvas.fieldWidth-opening)<.02f && play.canvas.has("TAP TO START"),"the game's restart returns to the opening frame");
    play.tap({195,600});
    const auto anchor=reference.pockets.front();
    const yy::Vec2 world{(anchor.column+anchor.columns/2.0f)*Model::cell,(anchor.row+anchor.rows/2.0f)*Model::cell};
    const auto press=touch.camera.toScreen(world), release=touch.camera.toScreen({world.x,world.y+60});
    play.game->pointerDown(3,press); play.game->pointerMove(3,release); play.game->pointerUp(3,release);
    play.canvas.read(*play.game);
    check(std::any_of(play.canvas.texts.begin(),play.canvas.texts.end(),[&](const auto& t){return t.at.x==62 && t.at.y==13 && t.value==std::to_string(levelBalls-1);}),"the real game fires through touch at its opening zoom");
  }
  std::cout<<"Game framing: levels 1/6/10 open framed, wheel holds, restart resets, zoomed touch fires\n";
}

// The debug panel survives a relaunch: every change is saved as it is made, the next session opens
// with it, and a pinned level or scene neither reads nor writes the save.
void debugPersistenceChecks() {
  std::string saved;
  {
    Session first("");
    first.tap({330,40});
    first.tap({335,127}); first.tap({335,359}); first.tap({335,443}); first.tap({335,549}); // bomb, balls, grid, bomb weight
    check(first.storage.writes==4 && first.storage.debug.rfind("debug 1\n",0)==0,"each debug change is saved as it is made");
    check(first.storage.debug.find("scheme")==std::string::npos,"a save has no scheme line");
    saved=first.storage.debug;
  }
  {
    Session second("",nullptr,nullptr,saved.c_str());
    check(second.storage.writes==0,"opening the app does not rewrite the settings");
    second.tap({330,40});
    check(second.canvas.valueAt(120,"7X7") && second.canvas.valueAt(352,"11") && second.canvas.valueAt(436,"30X50") && second.canvas.valueAt(542,"2"),
          "a relaunch brings the debug settings back");
  }
  {
    Session pinned("","1",nullptr,saved.c_str());
    pinned.tap({330,40}); pinned.tap({335,127});
    check(pinned.canvas.valueAt(120,"7X7") && pinned.storage.writes==0 && pinned.storage.debug==saved,"a pinned level uses the defaults and saves nothing");
    Session scene("",nullptr,"debug",saved.c_str());
    scene.tap({335,127});
    check(scene.canvas.valueAt(120,"7X7") && scene.storage.writes==0 && scene.storage.debug==saved,"a scene uses the defaults and saves nothing");
  }
  {
    Session damaged("",nullptr,nullptr,"debug 1\nballs 12\nscheme 99\nbomb\n\x01");
    damaged.tap({330,40});
    check(damaged.canvas.valueAt(352,"12") && damaged.canvas.valueAt(120,"5X5") && !damaged.canvas.has("COLORS") && same(damaged.canvas.field,tapdemo::garden.field),"a damaged save still opens, clamped and defaulted");
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
    check(fresh.canvas.has("EXPECTED WINS 100%"),"level 1's card gives its expected win rate");
    check(fresh.storage.writes==0,"opening the app saves nothing");
  }
  {
    Session third("level 3\n");
    check(third.canvas.has("LEVEL 3") && third.canvas.has("NEW POWER-UP") && third.canvas.has("ELECTRICITY") && !third.canvas.has("BOMB"),"the app opens at the saved level, and its card names only Electricity");
    check(third.canvas.has("EXPECTED WINS 98%"),"level 3's card gives its own expected win rate");
    const auto before=third.field();
    third.tap({330,40});
    check(third.canvas.valueAt(310,"3"),"the debug level picker shows the current level");
    third.tap({110,768}); // RESTART on level 3
    check(third.canvas.has("LEVEL 3") && sameFills(third.field(),before),"restarting a level from debug rebuilds the identical field");
    third.tap({330,40}); third.tap({335,317}); // LEVEL +
    check(third.canvas.valueAt(310,"4"),"the picker steps to the next level");
    third.tap({110,768});
    check(third.canvas.has("LEVEL 4") && third.storage.saved=="level 4\n","a picked level opens and is saved");
    third.tap({330,40});
    for(int i=0; i<levelCount; ++i) third.tap({205,317});
    check(third.canvas.valueAt(310,"FREE"),"the picker's lowest entry is free play");
    third.tap({110,768});
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
    check(sameFills(lose.canvas.frame(),again.field()),"the retry is the identical field, brick for brick");
  }
  {
    Session last("","10","won");
    check(last.until("TAP FOR FREE PLAY"),"winning level 10 leads to free play");
    last.tap({195,420});
    check(last.canvas.has("FREE PLAY"),"after the last level the game plays free");
  }
  std::cout<<"Level flow: opens at the save, win opens and saves the next, loss retries the same field, debug picks levels\n";
}

namespace {
// One round of a level played by the bot through touch, kept as its shots: replayed on a bare Model it
// shows where the goal broke; replayed on the game it must end the same way.
struct Round {
  int level{}; std::vector<levelbot::Shot> shots;
  std::vector<int> flights;      // updates each shot's flight takes, up to its end or the break
  int ballsLeft{}; bool chain{}, ghost{};
};
constexpr float step=1.0f/60;
// Replays `shots` on a bare model with a look-ahead before every update, and checks the look-ahead against what
// really happens: no hit seen on a shot that does not break the goal, and on the shot that does, a hit seen at
// exactly the number of updates the break then takes, from the window's edge on.
Round replayRound(int level, const std::vector<levelbot::Shot>& shots) {
  using namespace tapdemo;
  Round round; round.level=level; round.shots=shots;
  Model m; m.play(level,levels[level-1]);
  Touch finger(m); finger.instructions=false;
  finger.camera.fit(); finger.framing.automatic=false; // recorded whole-grid manual shots
  for(const auto& shot: shots) {
    finger.down(1,shot.press); finger.move(1,shot.release);
    check(finger.up(1,shot.release),"a recorded shot launches again");
    std::vector<Anticipation> seen; int updates=0;
    while(!m.balls.empty() && !m.over() && updates<60*120) {
      seen.push_back(lookAhead(m,step,Celebration::window));
      bool electric=false;
      for(const auto& b: m.balls) electric|=b.electric>0;
      m.update(step); ++updates;
      if(m.hits.goalBroken) {
        round.chain=electric || !m.hits.fired.empty();
        for(const auto& f: m.hits.fired) round.ghost|=f.power==Power::Ghost && f.to>=0;
      }
    }
    round.flights.push_back(updates);
    const int count=static_cast<int>(seen.size());
    for(int i=0; i<count; ++i) {
      const float left=(count-i)*step; // game seconds from this look-ahead to the update that ended the flight
      if(m.won()) {
        if(left<=Celebration::window-step) check(seen[i].hit && std::abs(seen[i].seconds-left)<step/2,"the look-ahead sees the goal break at the very update it breaks");
        else if(left>Celebration::window+step) check(!seen[i].hit,"the look-ahead sees nothing beyond its window");
      } else check(!seen[i].hit,"the look-ahead sees no goal break on a shot that does not break it");
    }
    m.balls.clear();
    if(m.won()) break;
  }
  check(m.won(),"the replayed round wins");
  round.ballsLeft=m.ballsLeft;
  return round;
}
// A round the bot wins through touch, in the first level and bot seed that has `want` (any win when null).
std::optional<Round> findRound(bool Round::*want, int minShots=1) {
  using namespace tapdemo;
  for(int level=1; level<=levelCount; ++level) for(std::uint32_t seed=1; seed<=400; ++seed) {
    std::vector<levelbot::Shot> shots;
    const auto result=levelbot::play(level,levels[level-1],seed,{},true,&shots);
    if(!result.won || static_cast<int>(shots.size())<minShots) continue;
    Round round=replayRound(level,shots);
    if(!want || round.*want) return round;
  }
  return std::nullopt;
}
bool hasNumber(const Canvas& canvas, int value) {
  return std::any_of(canvas.texts.begin(),canvas.texts.end(),[&](const auto& t){ return t.at.y>250 && t.at.y<600 && t.value==std::to_string(value); });
}
bool hasTune(const std::vector<float>& tones) {
  const float tune[]{523.25f,659.25f,783.99f,1046.5f,1318.5f};
  return std::search(tones.begin(),tones.end(),std::begin(tune),std::end(tune))!=tones.end();
}
// The game on `round`'s level with every shot but the winning one played out; the winning shot is pressed,
// dragged and released, and no frame has followed it yet.
struct Play {
  Session session; const Round& round; float fit{}; std::size_t impactsBefore{};
  explicit Play(const Round& r): session("",std::to_string(r.level).c_str()), round(r) {
    session.tap({195,600}); // the instructions card
    session.game->zoom({195,462},-100); // replay in the recorded whole-grid manual view
    session.canvas.read(*session.game);
    fit=session.canvas.fieldWidth;
    for(std::size_t i=0; i<round.shots.size(); ++i) {
      if(i+1==round.shots.size()) impactsBefore=session.quiet.impacts.size();
      const auto& shot=round.shots[i];
      session.game->pointerDown(1,shot.press); session.game->pointerMove(1,shot.release); session.game->pointerUp(1,shot.release);
      session.canvas.read(*session.game);
      if(i+1<round.shots.size()) session.play(round.flights[i]);
    }
  }
  bool zoomed() const { return session.canvas.fieldWidth>fit*1.1f; }
  bool atFit() const { return std::abs(session.canvas.fieldWidth-fit)<0.01f; }
  bool cardShows() const { return session.canvas.has("TAP FOR NEXT LEVEL") || session.canvas.has("TAP FOR FREE PLAY"); }
  // Frames until `done`, one at a time; false when 20 s pass first.
  template<class F> bool until(F done) { for(int i=0; i<1200; ++i) { if(done()) return true; session.play(1); } return done(); }
};
// Reads the next level's field the way the game fits it, and presses in its first pocket.
yy::Vec2 pocketPress(int level) {
  using namespace tapdemo;
  Model m; m.play(level); Touch touch(m); const Camera& camera=touch.camera;
  const auto& p=m.pockets.front();
  return camera.toScreen({(p.column+p.columns/2+0.5f)*Model::cell,(p.row+p.rows/2.0f)*Model::cell});
}
}

// The goal celebration (T20): the look-ahead, the clock, and the whole show through the game's touch path.
void celebrationChecks() {
  using namespace tapdemo;
  {
    // The clock alone: a coming hit slows time to a quarter and pushes the camera in, a hit that does not come eases both back
    // with nothing broken, and a real hit runs on to the card in the time it promises.
    Celebration c;
    check(c.phase()==Celebration::Phase::Idle && c.scale()==1 && c.focus()==0 && !c.engaged(),"the celebration starts idle");
    for(int i=0; i<60; ++i) c.step(step,true);
    check(c.playing() && std::abs(c.scale()-Celebration::slowScale)<1e-4f && c.focus()==1,"a coming hit slows time to a quarter and pushes the camera all the way in");
    for(int i=0; i<90; ++i) c.step(step,false);
    check(c.phase()==Celebration::Phase::Idle && c.scale()==1 && c.focus()==0 && !c.engaged(),"a hit that does not come eases time and the camera back to normal");
    for(int i=0; i<40; ++i) c.step(step,true);
    c.hit();
    check(c.phase()==Celebration::Phase::Hit && c.scale()==1 && c.playing(),"the hit returns time to normal");
    int frames=0;
    while(c.phase()==Celebration::Phase::Hit && frames<600) {
      c.step(step,true); ++frames;
      if(frames*step<Celebration::goalTextAt-step) check(c.goalText()==0,"GOAL! waits for the camera to start back");
    }
    check(std::abs(frames*step-Celebration::totalSeconds)<=2*step && c.card() && c.focus()==0,"the break reaches the card in the promised time with the camera back");
    c.reset(); c.step(step,true); c.skip();
    check(c.card() && c.scale()==1 && c.focus()==0 && !c.engaged(),"a skip lands on the card with time and camera normal");
  }
  {
    // The won scene's own shot: the look-ahead sees the goal hit before it happens, and at the very update it comes.
    Model m; m.play(1);
    const auto& p=m.pockets.front(); const int column=p.column+p.columns/2;
    m.bricks[(p.row-1)*m.columns+column]=1; m.powers[(p.row-1)*m.columns+column]=Power::None; m.goal=(p.row-1)*m.columns+column; m.refreshFog();
    const yy::Vec2 below{(column+0.5f)*Model::cell,(p.row+p.rows/2.0f)*Model::cell};
    check(m.launch(below,{0,40}),"the won scene's shot launches");
    const auto seen=lookAhead(m,step,Celebration::window);
    check(seen.hit && seen.seconds>0 && seen.seconds<0.3f,"the look-ahead predicts the won scene's goal hit");
    int updates=0; while(!m.won() && updates<120) { m.update(step); ++updates; }
    check(std::abs(updates*step-seen.seconds)<step/2,"and at the very update it comes");
    check(!lookAhead(m,step,Celebration::window).hit,"nothing is predicted once the round is won");
  }
  // The look-ahead against real rounds, in a level where the goal breaks plainly and in ones where a power-up or a Ghost landing breaks it.
  const auto plain=findRound(nullptr,2);
  const auto chain=findRound(&Round::chain);
  const auto ghost=findRound(&Round::ghost);
  check(plain && chain,"the bot wins a plain round and a round through a power-up");
  std::cout<<"Look-ahead: plain win on level "<<plain->level<<" ("<<plain->shots.size()<<" shots), through a power-up on level "<<chain->level
    <<", through a Ghost landing "<<(ghost ? "on level "+std::to_string(ghost->level) : std::string("not found"))<<'\n';
  std::vector<const Round*> rounds{&*plain,&*chain};
  if(ghost) rounds.push_back(&*ghost);
  for(const Round* round: rounds) {
    {
      // The whole show: the same shot wins with the slow motion as without it, and the card ends it.
      Play play(*round);
      const int breakFrames=round->flights.back();
      int frames=0, firstZoom=-1; float widest=play.fit; bool goalShown=false, cardWithGoal=false;
      while(!play.cardShows() && frames<1200) {
        play.session.play(1); ++frames;
        if(play.zoomed() && firstZoom<0) firstZoom=frames;
        widest=std::max(widest,play.session.canvas.fieldWidth);
        if(play.session.canvas.has("GOAL!")) { goalShown=true; cardWithGoal|=play.session.canvas.has("GOAL FOUND"); }
      }
      check(frames<1200 && widest>play.fit*1.5f,"the camera pushes in on the way to the goal");
      check(goalShown && !cardWithGoal,"GOAL! shows before the win card, never with it");
      check(breakFrames<6 || frames>breakFrames+Celebration::totalSeconds*60+5,"the slow motion makes the winning flight last longer than it does at full speed");
      check(firstZoom>=0 && (frames-firstZoom)*step>=1.0f && (frames-firstZoom)*step<=4.2f,"the celebration takes a few seconds from the push-in to the card");
      check(play.atFit(),"the camera is back on the field at the card");
      check(play.session.canvas.has("GOAL FOUND") && play.session.canvas.has("BALLS LEFT"),"the win card shows");
      check(round->ballsLeft==0 || hasNumber(play.session.canvas,0),"the card opens counting from 0");
      play.session.play(90);
      check(hasNumber(play.session.canvas,round->ballsLeft),"the card counts up to the balls left the bare model ends with");
      check(hasTune(play.session.quiet.tones),"the goal plays its rising tune");
      check(std::count(play.session.quiet.impacts.begin()+static_cast<std::ptrdiff_t>(play.impactsBefore),play.session.quiet.impacts.end(),1.0f)>=1,"and a strong haptic");
      play.session.tap({195,420});
      check(play.session.canvas.has("TAP TO START") && !play.session.canvas.has("GOAL FOUND"),"the next tap advances to the next level");
      if(round->level<levelCount) {
        play.session.tap({195,600}); // the next card
        const auto press=pocketPress(round->level+1);
        play.session.game->pointerDown(3,press); play.session.game->pointerMove(3,{press.x,press.y+40}); play.session.game->pointerUp(3,{press.x,press.y+40});
        play.session.canvas.read(*play.session.game);
        const std::string left=std::to_string(levels[round->level].balls-1);
        check(std::any_of(play.session.canvas.texts.begin(),play.session.canvas.texts.end(),[&](const auto& t){ return t.at.x==62 && t.at.y==13 && t.value==left; }),"aiming and shooting work as usual on the next level");
      }
    }
    {
      // One tap in the middle of the slow-motion approach jumps to the card without advancing past it.
      Play play(*round);
      check(play.until([&]{ return play.zoomed(); }) || round->flights.back()<6,"the approach starts");
      play.session.tap({195,420});
      check(play.cardShows() && play.session.canvas.has("GOAL FOUND") && !play.session.canvas.has("TAP TO START"),"a tap during the approach shows the card and does not advance");
      check(play.atFit(),"and the camera is back on the field");
      play.session.play(90);
      check(hasNumber(play.session.canvas,round->ballsLeft),"the skipped shot ended in the same win");
      // The camera works at once: two fingers spread to zoom.
      play.session.game->pointerDown(4,{145,650}); play.session.game->pointerDown(5,{245,650});
      play.session.game->pointerMove(4,{95,650}); play.session.game->pointerMove(5,{295,650}); play.session.canvas.read(*play.session.game);
      check(play.zoomed(),"pinch zoom works right after a skip");
      play.session.game->pointerUp(4,{95,650}); play.session.game->pointerUp(5,{295,650});
      play.session.tap({195,420});
      check(play.session.canvas.has("TAP TO START"),"the second tap advances");
    }
    {
      // A tap after the break, while GOAL! shows, does the same.
      Play play(*round);
      check(play.until([&]{ return play.session.canvas.has("GOAL!"); }),"GOAL! shows");
      play.session.tap({195,420});
      check(play.cardShows() && play.atFit() && !play.session.canvas.has("TAP TO START"),"a tap during GOAL! shows the card without advancing");
      play.session.tap({195,420});
      check(play.session.canvas.has("TAP TO START"),"and the next tap advances");
    }
    {
      // Pausing the app mid-approach or mid-celebration leaves no zoom or time scale behind.
      Play play(*round);
      check(play.until([&]{ return play.zoomed(); }) || round->flights.back()<6,"the approach starts");
      play.session.game->pause(true); play.session.canvas.read(*play.session.game);
      check(play.atFit(),"pausing during the approach puts the camera back");
      play.session.game->pause(false);
      check(play.until([&]{ return play.cardShows(); }) && play.atFit(),"the shot still wins after the pause");
      play.session.play(90);
      check(hasNumber(play.session.canvas,round->ballsLeft),"the same win, the same balls left");
    }
    {
      Play play(*round);
      check(play.until([&]{ return play.session.canvas.has("GOAL!"); }),"GOAL! shows again");
      play.session.game->pause(true); play.session.canvas.read(*play.session.game);
      check(play.cardShows() && play.atFit(),"pausing during GOAL! settles on the card with the camera back");
      play.session.game->pause(false);
      play.session.play(60);
      check(play.cardShows() && play.atFit() && hasNumber(play.session.canvas,round->ballsLeft),"and it stays there");
    }
  }
  std::cout<<"Goal celebration: look-ahead exact, same win with slow motion, skip, pause and next tap passed\n";
}

// Hit feedback (T23): effect timing, pitch stepping, shake, the particle pool, crack stages and the fog lift, all without
// a window; then the game's own sounds through a real shot.
void juiceChecks() {
  using namespace tapdemo;
  {
    HitStop stop;
    check(!stop.holding(),"no hit-stop before a hit");
    stop.trigger(); stop.step(0.02f);
    check(stop.holding() && HitStop::seconds>=0.03f && HitStop::seconds<=0.06f,"a hit-stop of 30 to 60 ms still holds at 20 ms");
    stop.step(0.04f);
    check(!stop.holding(),"the hit-stop is over by 60 ms");
  }
  {
    PitchLadder ladder;
    check(std::abs(ladder.hz()-660)<1e-3f,"the first break plays the base pitch");
    float last=ladder.hz();
    for(int i=0; i<PitchLadder::topStep; ++i) {
      ladder.climb();
      check(ladder.hz()>last,"every further hit or break raises the pitch a step");
      last=ladder.hz();
    }
    ladder.climb(); ladder.climb();
    check(ladder.step()==PitchLadder::topStep && ladder.hz()==last,"the ladder stops at its top step");
    check(std::abs(PitchLadder::hz(PitchLadder::topStep)-660*4)<1,"the top step is two octaves up");
    ladder.reset();
    check(ladder.step()==0 && std::abs(ladder.hz()-660)<1e-3f,"a new ball starts the ladder over");
  }
  {
    check(shakeFor(1,false,false,false)<shakeFor(4,false,false,false) && shakeFor(4,false,false,false)<shakeFor(4,false,true,false)
          && shakeFor(4,false,true,false)<shakeFor(25,true,false,false) && shakeFor(25,true,false,false)<shakeFor(1,false,false,true),
          "the shake grows from a break to lightning, a bomb and the goal");
    Shake shake;
    check(shake.amplitude()==0 && shake.offset().x==0 && shake.offset().y==0,"a still screen has no offset");
    shake.bump(7);
    check(std::abs(shake.amplitude()-7)<1e-4f,"a bump starts at its size");
    shake.step(Shake::seconds/2);
    check(shake.amplitude()>0 && shake.amplitude()<7,"the shake fades");
    shake.step(Shake::seconds);
    check(shake.amplitude()==0 && shake.offset().x==0,"the shake is over after its time");
    for(int i=0; i<20; ++i) shake.bump(10);
    check(shake.amplitude()<=Shake::limit,"stacked bumps stay within the limit");
  }
  {
    SpeckPool pool;
    const auto capacity=pool.all().capacity();
    bool refused=false;
    for(int i=0; i<SpeckPool::capacity+50; ++i) refused=!pool.add({{},{0,0},0,0.5f,3,{},false,false}) || refused;
    check(refused && pool.live()==SpeckPool::capacity && pool.all().size()==SpeckPool::capacity && pool.all().capacity()==capacity,"the pool caps its specks and never grows");
    pool.step(0.4f,100);
    check(pool.live()==SpeckPool::capacity && pool.all().front().velocity.y>30 && pool.all().front().at.y>0,"chips fall under gravity");
    pool.step(0.2f,100);
    check(pool.live()==0,"specks end with their life");
    pool.add({{5,5},{9,9},0,1,3,{},true,true}); pool.step(0.5f,100);
    check(pool.all().front().at.x==5 && pool.all().front().at.y==5,"a trail point stays where it was dropped");
  }
  {
    check(crackStage(3)==0 && crackStage(2)==1 && crackStage(1)==2 && crackStage(5)==0,"hit points map to whole, cracked and badly cracked");
    int whole=-1, cracked=0, bad=0;
    check(crackPieces(0,whole)==nullptr && whole==0,"a whole brick has no cracks");
    const auto* a=crackPieces(1,cracked); const auto* b=crackPieces(2,bad);
    check(a && b && cracked>0 && bad>cracked,"a badly cracked brick shows more cracks than a cracked one");
    for(int i=0; i<bad; ++i) check(b[i].x>=0 && b[i].y>=0 && b[i].x+b[i].w<=1 && b[i].y+b[i].h<=1,"every crack lies inside its brick");
  }
  {
    FogLift lift(100);
    check(lift.progress(7)==1 && !lift.lifting(7) && lift.highlight(7)==0,"a cell that is not lifting reads as clear");
    lift.begin(7,false); lift.begin(8,true);
    check(lift.progress(7)==0 && lift.lifting(7) && lift.highlight(7)==0,"the fog covers a cell at the start of its lift");
    lift.step(FogLift::seconds/2);
    check(std::abs(lift.progress(7)-0.5f)<1e-4f,"half the lift is half peeled");
    check(lift.highlight(8)>0.1f && lift.highlight(7)==0,"only a power-up brick or the goal is highlighted");
    lift.step(FogLift::seconds/2+1e-4f);
    check(lift.progress(7)==1 && lift.entries().size()==1 && lift.highlight(8)>0.5f,"the peel ends after 0.3 s and the highlight outlasts it");
    lift.step(FogLift::highlightSeconds);
    check(lift.entries().empty(),"the highlight ends too");
    check(FogLift::seconds==0.3f,"the fog lifts in about 0.3 s");
  }
  {
    // The model is untouched: the same level and shots give the same field with or without the game's effects watching.
    Model a, b; a.play(3); b.play(3);
    check(a.bricks==b.bricks && a.goal==b.goal,"effects do not touch the field");
  }
  {
    // Through the game: a real flight of four shots climbs the break sound's pitch and every tone is a ladder step or a power-up's.
    Session breaks("","1","breaks");
    breaks.play(300);
    std::vector<float> ladderTones;
    for(float hz: breaks.quiet.tones) for(int k=0; k<=PitchLadder::topStep; ++k) if(std::abs(hz-PitchLadder::hz(k))<0.01f) { ladderTones.push_back(hz); break; }
    check(ladderTones.size()>=3 && ladderTones.front()==PitchLadder::hz(0),"a flight's first hit plays the base pitch and the flight keeps hitting");
    check(std::is_sorted(ladderTones.begin(),ladderTones.end()) && ladderTones.back()>ladderTones.front(),"within one flight each further hit or break plays higher");
    check(!breaks.quiet.impacts.empty(),"hits and breaks give haptics");
  }
  {
    // A new ball starts the pitch over: the first sound of each single-ball flight is the base pitch.
    Session shots("","1","garden-shot");
    shots.play(150);
    check(!shots.quiet.tones.empty() && std::abs(shots.quiet.tones.front()-PitchLadder::hz(0))<0.01f,"the first hit of a ball plays the base pitch");
  }
  {
    // Cracks: the staged fixture's 2 and 1 hit-point bricks draw cracks, in the one ink colour.
    Session damage("","10","garden-damage");
    const auto inks=std::count_if(damage.canvas.fills.begin(),damage.canvas.fills.end(),[](yy::Color c){ return c.r==38 && c.g==22 && c.b==12; });
    int cracked=0, bad=0; crackPieces(1,cracked); crackPieces(2,bad);
    check(inks>=cracked+bad,"cracked and badly cracked bricks draw their cracks over the sprite");
    check(!damage.canvas.has("1") && !damage.canvas.has("2") && !damage.canvas.has("3"),"no digit is drawn on a brick");
  }
  {
    // The SHAKE control: off, low, medium, high; it saves with the debug settings and a default save stays as it was.
    check(shakeScale(0)==0 && shakeScale(1)<shakeScale(2) && shakeScale(2)<shakeScale(3) && shakeScale(2)==1 && shakeScale(9)==shakeScale(3),"the shake levels run from off to high");
    check(shakeFor(25,true,false,false)*shakeScale(3)<=Shake::limit && shakeFor(1,false,false,true)*shakeScale(3)<=Shake::limit*1.5f,"even high keeps the goal's shake modest");
    Session panel("");
    panel.tap({330,40});
    check(panel.canvas.has("SHAKE MEDIUM"),"the debug panel shows the shake, at medium by default");
    panel.tap({280,52}); check(panel.canvas.has("SHAKE HIGH") && panel.storage.debug.find("shake 3\n")!=std::string::npos,"a tap steps the shake and saves it");
    panel.tap({280,52}); check(panel.canvas.has("SHAKE OFF"),"the shake steps round to off");
    panel.tap({280,52}); panel.tap({280,52}); check(panel.canvas.has("SHAKE MEDIUM") && panel.storage.debug.find("shake")==std::string::npos,"back at medium the save has no shake line");
    Session kept("",nullptr,nullptr,"debug 1\nshake 1\n");
    kept.tap({330,40}); check(kept.canvas.has("SHAKE LOW"),"a saved shake is kept");
    DebugSettings d; d.shake=3;
    check(loadDebug(saveDebug(d)).shake==3 && loadDebug("debug 1\nshake 9\n").shake==3 && DebugSettings{}.shake==defaultShakeLevel,"the shake round-trips and is clamped");
  }
  std::cout<<"Hit feedback: hit-stop, pitch ladder, shake, specks, cracks and fog lift passed\n";
}

namespace {
using tapdemo::Model;
using tapdemo::Power;
bool close(float a, float b, float within=0.01f) { return std::abs(a-b)<=within; }
// An empty field but for the given bricks, so a ball's path is known; no goal unless a test sets one.
void clearField(Model& m, const std::vector<std::pair<int,int>>& cells, int hp) {
  std::fill(m.bricks.begin(),m.bricks.end(),0);
  std::fill(m.powers.begin(),m.powers.end(),Power::None);
  m.goal=-1;
  for(auto [c,r]: cells) m.bricks[r*Model::defaultColumns+c]=hp;
  m.refreshFog();
}
yy::Vec2 pocketSpot(const Model& m) {
  const auto& p=m.pockets.front();
  return {(p.column+p.columns/2+0.5f)*Model::cell,(p.row+p.rows/2+0.5f)*Model::cell};
}
// What a played level left behind, to compare runs that differ only in game speed.
struct Outcome {
  std::vector<int> bricks; std::vector<Power> powers; int goal, ballsLeft; bool won;
  std::vector<std::array<int,3>> fired; int bounces;
  bool operator==(const Outcome& o) const { return bricks==o.bricks && powers==o.powers && goal==o.goal && ballsLeft==o.ballsLeft && won==o.won && fired==o.fired && bounces==o.bounces; }
};
// Plays `level` with six pulls in turn from the first pocket's centre, one ball at a time, each launched the moment
// nothing flies, whatever the frame. `scale` is the clock's speed; 0 means the game's own pace (tapdemo::Pace).
Outcome playPaced(int level, float scale, float& fastest) {
  using namespace tapdemo;
  Model m; m.play(level);
  const yy::Vec2 at=pocketSpot(m);
  const yy::Vec2 pulls[]{{20,40},{-40,15},{5,-40},{40,-10},{-30,-30},{0,50}};
  Pace pace; float debt=0; int shot=0; Outcome out{}; fastest=1;
  const float dt=1.0f/60;
  for(int frame=0; frame<60*600 && !m.over(); ++frame) {
    if(m.balls.empty() && m.ballsLeft>0 && !m.launch(at,pulls[shot++%6])) break;
    const float speed=scale>0 ? scale : pace.step(m,dt);
    fastest=std::max(fastest,speed);
    for(int n=takeSteps(debt,dt,speed,4); n>0; --n) {
      m.update(dt);
      for(const auto& f: m.hits.fired) out.fired.push_back({static_cast<int>(f.power),f.cell,f.to});
      out.bounces+=m.hits.bounces;
    }
  }
  out.bricks=m.bricks; out.powers=m.powers; out.goal=m.goal; out.ballsLeft=m.ballsLeft; out.won=m.won();
  return out;
}
}

void paceChecks() {
  using namespace tapdemo;
  // The aim line ends where a real launch first touches something: several shots on several levels, snapped or not.
  int shots=0, onBrick=0, onWall=0;
  const auto compare=[&](const Model& m, yy::Vec2 at, yy::Vec2 pull) {
    constexpr float dt=1.0f/240;
    const AimPath path=m.aimPath(at,pull,dt);
    check(path.valid && path.start.x==at.x && path.start.y==at.y,"the aim line starts at the held ball");
    Model real=m;
    check(real.launch(at,pull),"the shot launches");
    for(int i=0; i<4000 && real.hits.bounces==0; ++i) real.update(dt);
    check(real.hits.bounces>0,"the real ball touches something");
    const Ball& b=real.balls.front();
    check(close(b.position.x,path.contact.x,1e-3f) && close(b.position.y,path.contact.y,1e-3f),"the aim line ends at the real first contact");
    const float speed=std::hypot(b.velocity.x,b.velocity.y);
    check(close(b.velocity.x/speed,path.after.x,1e-4f) && close(b.velocity.y/speed,path.after.y,1e-4f),"the stub leaves in the real reflected direction");
    check(path.brick==(real.hits.bricksHit>0),"the line knows a brick from a wall as the real hit does");
    ++shots; (path.brick ? onBrick : onWall)++;
  };
  const yy::Vec2 pulls[]{{20,40},{-40,15},{5,-40},{40,-10},{-100,-5},{3,60},{-30,-30},{0,50}};
  for(int level: {1,3,5,8,10}) for(yy::Vec2 pull: pulls) { Model m; m.play(level); compare(m,pocketSpot(m),pull); }
  for(yy::Vec2 pull: pulls) { Model m; clearField(m,{},1); compare(m,{9.5f*Model::cell,20.5f*Model::cell},pull); } // an empty field: walls
  check(shots==48 && onBrick>0 && onWall>=8,"the aim checks ran, on bricks and on walls");
  {
    // After the snap: a pull 3 degrees off horizontal is drawn level, and as pulled once the snap is off.
    Model m; m.play(1); m.setSnapDegrees(5);
    const yy::Vec2 at=pocketSpot(m);
    const AimPath snapped=m.aimPath(at,{-100,-5});
    check(snapped.valid && close(snapped.contact.y,at.y,1e-3f) && snapped.contact.x>at.x,"the line is drawn from the snapped direction");
    m.setSnapDegrees(0);
    const AimPath loose=m.aimPath(at,{-100,-5});
    check(loose.valid && !close(loose.contact.y,at.y,1.0f),"without the snap the same pull is drawn as pulled");
    check(!m.aimPath(at,{3,3}).valid && !m.aimPath({0,0},{40,40}).valid,"a short pull or a covered spot draws nothing");
  }

  // Game speed changes how many fixed steps a frame takes, never the steps: every level plays out the same.
  float fastest=1;
  for(int level=1; level<=levelCount; ++level) {
    float f1,fp,ff,fs;
    const Outcome flat=playPaced(level,1,f1), paced=playPaced(level,0,fp), fast=playPaced(level,2.5f,ff), slow=playPaced(level,0.35f,fs);
    check(flat==paced && flat==fast && flat==slow,"a level plays out the same at 1x, at the game's varying pace, fast and slow");
    check(flat.bounces>0,"the compared run flew");
    fastest=std::max(fastest,fp);
  }
  check(fastest>2,"the varying pace really ran fast on some level");
  std::cout<<"Aim line: "<<shots<<" shots matched their first contact ("<<onBrick<<" brick, "<<onWall<<" wall); game speed left every level's outcome unchanged (fastest "<<fastest<<"x)\n";

  {
    // The speed rises with the oldest flying ball's bounces, 1x to about 2.5x, and is 1x with nothing flying.
    Model m; m.play(1);
    check(Pace::target(m)==1,"nothing flying runs at 1x");
    const yy::Vec2 at=pocketSpot(m);
    check(m.launch(at,{20,40}) && m.launch(at,{-20,40}),"two balls fly");
    float last=0;
    for(int left=m.bouncesPerBall; left>=1; --left) {
      m.balls[0].bounces=left; m.balls[1].bounces=m.bouncesPerBall;
      const float t=Pace::target(m);
      check(t>=last && t>=1 && t<=Pace::topSpeed+1e-4f,"the speed rises as the oldest ball uses its bounces");
      last=t;
    }
    check(close(Pace::target(m),Pace::topSpeed,1e-4f),"the oldest ball's last bounce runs at the top speed");
    m.balls[0].bounces=m.bouncesPerBall; m.balls[1].bounces=1;
    check(Pace::target(m)==1,"a fresh oldest ball runs at 1x whatever the newer ball has used");
    Pace pace; m.balls[0].bounces=1;
    float s=1; for(int i=0; i<120; ++i) s=pace.step(m,1.0f/60);
    check(close(s,Pace::topSpeed,0.01f),"the pace eases up to the target");
    m.balls.clear();
    check(pace.step(m,1.0f/60)==1,"the pace returns to 1x the moment nothing flies");
  }
  {
    // Last ball: banner state and the tighter hum.
    Model m; m.play(1);
    const yy::Vec2 at=pocketSpot(m);
    check(!lastBall(m,false) && !lastBall(m,true),"no banner with all balls in hand");
    m.ballsLeft=1;
    check(lastBall(m,true) && !lastBall(m,false),"the banner shows while the last ball is held");
    check(m.launch(at,{20,40}) && m.ballsLeft==0 && lastBall(m,false) && lastBall(m,true),"and while it flies");
    m.balls.clear();
    check(!lastBall(m,false) && !lastBall(m,true),"and goes when it is spent");
    Model hum; hum.play(1); Touch t(hum); t.instructions=false;
    t.aim=Touch::Aim{0,at,{0,60}};
    const float calm=t.humLevel(); hum.ballsLeft=1;
    check(t.humLevel()>calm && t.humLevel()<=1,"the last ball's hum is tighter");
    t.aim->pull={0,Touch::fullPull*2};
    check(close(t.humLevel(),1),"and tops out at full strength");
  }
  {
    // Slow motion: the last ball within about 2 cells of a goal that is visible or pinged.
    Model m;
    std::vector<std::pair<int,int>> mass;
    for(int c=10; c<=14; ++c) for(int r=5; r<=12; ++r) mass.push_back({c,r});
    clearField(m,mass,3);
    const int columns=Model::defaultColumns;
    m.bricks[13*columns+12]=1; m.powers[13*columns+12]=Power::Ping;
    m.goal=8*columns+12; m.bricks[m.goal]=1; m.refreshFog();
    const yy::Vec2 goal{12.5f*Model::cell,8.5f*Model::cell};
    check(!m.visible(12,8),"the goal starts hidden in the fog");
    m.ballsLeft=0; m.balls.push_back({{goal.x,goal.y+1.5f*Model::cell},{0,-Model::speed},5});
    check(!nearGoal(m) && Pace::target(m)>=1,"near a hidden, unpinged goal nothing slows");
    // A real Ping: launch up the column into the Ping brick, then put the ball by the goal.
    Model p=m; p.balls.clear(); p.ballsLeft=1;
    check(p.launch({12.5f*Model::cell,16.5f*Model::cell},{0,40}),"the Ping shot launches");
    for(int i=0; i<600 && p.pingTime<=0; ++i) p.update(1.0f/60);
    check(p.pinged(12,8) && !p.visible(12,8),"the goal is pinged though fogged");
    p.balls.front().position={goal.x,goal.y+1.5f*Model::cell};
    check(nearGoal(p) && Pace::target(p)==Pace::dramaSpeed,"the last ball within 2 cells of a pinged goal drops to slow motion");
    p.balls.front().position={goal.x,goal.y+3*Model::cell};
    check(!nearGoal(p),"3 cells away is not near");
    p.balls.front().position={goal.x,goal.y+1.5f*Model::cell}; p.ballsLeft=1;
    check(!nearGoal(p),"with balls still to place it is not the last ball");
    p.ballsLeft=0; p.pingTime=0;
    check(!nearGoal(p),"once the ping has gone the fogged goal is hidden again");
    // A visible goal needs no ping, and the slow-down overrides the speed-up.
    m.bricks[10*columns+12]=0; m.bricks[9*columns+12]=0; m.refreshFog();
    check(m.visible(12,8),"the goal is now visible");
    m.balls.front().bounces=1; m.bouncesPerBall=15;
    check(nearGoal(m) && Pace::target(m)==Pace::dramaSpeed,"the slow-down overrides the speed-up");
    Pace pace; pace.step(m,1.0f/60);
    check(pace.speed()<1,"the pace starts dropping at once");
    for(int i=0; i<60; ++i) pace.step(m,1.0f/60);
    check(close(pace.speed(),Pace::dramaSpeed,0.01f),"and reaches slow motion");
    m.balls.front().position={goal.x,goal.y+5*Model::cell};
    pace.step(m,1.0f/60);
    check(pace.speed()>Pace::dramaSpeed && pace.speed()<1,"leaving the zone eases back rather than snapping");
    for(int i=0; i<240; ++i) pace.step(m,1.0f/60);
    check(close(pace.speed(),Pace::target(m),0.01f),"and arrives at the speed the ball's bounces call for");
  }
  {
    // On screen: the banner shows with the last ball held and not before.
    Session held("",nullptr,"lastball");
    check(held.canvas.has("LAST BALL"),"the LAST BALL banner shows while the last ball is held");
    Session many("",nullptr,"aim-brick");
    check(!many.canvas.has("LAST BALL"),"no banner with balls to spare");
    Session wall("",nullptr,"aim-wall");
    check(!wall.canvas.has("LAST BALL"),"no banner on the wall-line scene");
  }
  std::cout<<"Game speed: bounce speed-up, slow motion near the goal, LAST BALL banner and hum passed\n";
}
