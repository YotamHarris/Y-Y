#include <tapdemo/palette.hpp>
#include <tapdemo/garden.hpp>
#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <yy/runtime.hpp>
#include <algorithm>
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
  check(canvas.has("COLORS") && canvas.has("GARDEN POP"),"debug opens with the default Garden look");
  check(canvas.has("BALLS") && canvas.has("BOUNCES") && canvas.has("PING RADIUS"),"existing controls remain beside COLORS");
  // Every label and value lies on the 390x844 screen (the debug font is 8 units a character at scale 1).
  check(std::all_of(canvas.texts.begin(),canvas.texts.end(),[](const auto& t){ return t.at.x>=0 && t.at.y>=0 && t.at.y+16<=844; }),"every debug row fits the screen");
  tap({335,85}); // Existing ping control still applies immediately.
  check(canvas.valueAt(78,std::to_string(Model::defaultPingRadius+1)),"ping radius plus still works");
  tap({335,359}); tap({335,401}); // Pending ball/bounce settings.
  check(canvas.valueAt(352,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(394,std::to_string(Model::defaultBounces+1)),"existing pending controls work");
  for(std::size_t i=1; i<=lookCount; ++i) {
    const auto& p=lookPalette((gardenScheme+i)%lookCount);
    tap(i%2 ? yy::Vec2{45,750} : yy::Vec2{345,780}); // opposite edges of the 310x38 touch target
    check(canvas.has(p.name) && same(canvas.field,p.field),"COLORS changes the label and field immediately, and wraps");
    check(p.name==garden.name ? canvas.spriteHas(GardenSprite::Mist) : std::find_if(canvas.fills.begin(),canvas.fills.end(),[&](yy::Color c){return same(c,p.fog[0]);})!=canvas.fills.end(),"render uses the selected fog");
    tap({110,812}); // RESTART
    check(!canvas.has("COLORS") && canvas.has("TAP TO START") && same(canvas.field,p.field),"restart closes debug and keeps the selected palette");
    tap({330,40});
    check(canvas.has(p.name),"reopening debug keeps the selected scheme name");
    check(canvas.valueAt(78,std::to_string(Model::defaultPingRadius+1)),"restart also keeps the existing ping radius preference");
    check(canvas.valueAt(352,std::to_string(Model::defaultBalls+1)) && canvas.valueAt(394,std::to_string(Model::defaultBounces+1)),"restart applies the existing pending settings");
  }
  tap({195,762}); tap({195,762}); // Garden -> NAVY -> EMBER
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
  std::vector<float> tones;
  void tone(float hz, float) override { tones.push_back(hz); }
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
    card.tap({330,40}); card.tap({110,812}); check(card.canvas.has("TAP TO START"),"debug restart opens the card again");
    for(int i=0;i<4;++i) {
      Session saved("",nullptr,nullptr,("debug 1\nscheme "+std::to_string(i)+"\n").c_str());
      saved.tap({330,40}); check(saved.canvas.has(palettes[i].name),"all four old saved scheme indices retain their palettes");
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

// The debug panel survives a relaunch: every change is saved as it is made, the next session opens
// with it, and a pinned scheme or scene neither reads nor writes the save.
void debugPersistenceChecks() {
  std::string saved;
  {
    Session first("");
    first.tap({330,40});
    first.tap({335,127}); first.tap({335,359}); first.tap({335,443}); first.tap({335,549}); // bomb, balls, grid, bomb weight
    check(first.storage.writes==4 && first.storage.debug.rfind("debug 1\n",0)==0,"each debug change is saved as it is made");
    first.tap({195,762}); first.tap({195,762});
    check(first.storage.writes==6,"cycling the colours saves too");
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
    check(damaged.canvas.valueAt(352,"12") && damaged.canvas.valueAt(120,"5X5") && damaged.canvas.has("GARDEN POP"),"a damaged save still opens, clamped and defaulted");
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
