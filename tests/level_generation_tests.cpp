// One named test per rule in docs/level-generation.md (T34).
#include <tapdemo/model.hpp>
#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <numeric>

void levelBandChecks(); // Existing band gate, unchanged.

namespace tapdemo {
struct LevelRecipeTestAccess {
  static void grid(Model& m) { m.buildGridAndHitPoints(); }
  static void glow(Model& m) { m.placeGlowingBricks(); }
  static void goal(Model& m) { m.placeGoal(); }
};
}

namespace {
using namespace tapdemo;
void check(bool ok, const char* message) {
  if(!ok) { std::cerr<<"Level recipe: "<<message<<'\n'; std::exit(1); }
}
template<class Test> void eachField(Test test) {
  for(int level=1; level<=levelCount; ++level) {
    Model m; m.play(level); test(m);
    for(std::uint32_t seed=1; seed<=64; ++seed) {
      Level l=levels[level-1]; l.seed=seed; m.play(level,l); test(m);
    }
  }
}
bool edge(const Model& m, int c, int r) {
  const int half=m.bombSize/2;
  return c<half || r<half || c>=m.columns-half || r>=m.rows-half;
}
std::uint64_t fieldHash(const Model& m) {
  // The exact little-endian bytes emitted by level_recipe_dump.cpp; no padding.
  std::uint64_t hash=14695981039346656037ull;
  const auto word=[&](std::uint32_t value) {
    for(int shift=0; shift<32; shift+=8) hash=(hash^((value>>shift)&255))*1099511628211ull;
  };
  word(m.columns); word(m.rows); word(m.goal); word(m.won()); word(static_cast<std::uint32_t>(m.pockets.size()));
  for(const auto& p: m.pockets) { word(p.column); word(p.row); word(p.columns); word(p.rows); }
  for(std::size_t i=0; i<m.bricks.size(); ++i) {
    word(m.bricks[i]); word(static_cast<std::uint32_t>(m.powers[i]));
    word(m.fogDistance(static_cast<int>(i)%m.columns,static_cast<int>(i)/m.columns));
  }
  return hash;
}
void seededFieldsUnchanged() {
  // Captured with ba80949's model.cpp before extracting the recipe steps.
  constexpr std::array<std::uint64_t,levelCount> before{
    4465901617141586073ull,3495574635567188002ull,10743978662179541491ull,7413345101900285566ull,
    6065245357140630002ull,4964636765703347554ull,3734234545185738262ull,621690589082315072ull,
    16444185347337181642ull,4918079018109933961ull};
  for(int n=1; n<=levelCount; ++n) {
    Model m; m.play(n);
    check(fieldHash(m)==before[n-1],"shipped field differs from pre-refactor bytes");
  }
}

void R1_gridSizeAndHitPoints() {
  std::array<int,4> counts{};
  eachField([&](Model m) {
    LevelRecipeTestAccess::grid(m); // Check before cavities, glow and goal replace hit points.
    check(m.columns==6*m.settings.gridScale && m.rows==10*m.settings.gridScale,"R1 grid shape");
    check(m.bricks.size()==static_cast<std::size_t>(m.columns*m.rows),"R1 cell count");
    for(int hp: m.bricks) { check(hp>=1 && hp<=3,"R1 initial hit point range"); ++counts[hp]; }
  });
  const float total=static_cast<float>(counts[1]+counts[2]+counts[3]);
  for(int hp=1; hp<=3; ++hp) check(std::abs(counts[hp]/total-std::array{0.0f,0.5f,0.3f,0.2f}[hp])<0.01f,"R1 hit point chances");
  for(int scale: {-3,99}) {
    Model m; Settings grid; grid.gridScale=scale; m.restart(4,15,grid);
    check(m.columns==6*(scale<0 ? Settings::minScale : Settings::maxScale),"R1 scale clamps");
  }
}
void R2_levelSeedsFollowBotBands() {
  eachField([](const Model& previous) {
    Model a=previous, b=previous;
    a.play(previous.level()); b.restart();
    check(fieldHash(a)==fieldHash(b),"R2 retry ignores previous random state");
    check(fieldHash(a)==[&] { Model m; m.play(previous.level()); return fieldHash(m); }(),"R2 fixed seed repeats");
  });
  levelBandChecks(); // Test measured outcomes, rather than guessing from seed numbers.
}
void R3_pocketSizeAndCount() {
  int doubles=0, fields=0;
  eachField([&](const Model& m) {
    ++fields; doubles+=m.pockets.size()==2;
    check(m.pockets.size()==1 || m.pockets.size()==2,"R3 one or two pockets");
    std::vector<bool> empty(m.bricks.size());
    for(const auto& p: m.pockets) {
      check(p.columns>=3 && p.columns<=5 && p.rows>=3 && p.rows<=5,"R3 pocket dimensions");
      for(int r=p.row; r<p.row+p.rows; ++r) for(int c=p.column; c<p.column+p.columns; ++c) empty[r*m.columns+c]=true;
    }
    for(std::size_t i=0; i<empty.size(); ++i) check((m.bricks[i]==0)==empty[i],"R3 only pockets are empty");
  });
  check(doubles>fields*0.4f && doubles<fields*0.6f,"R3 pocket count chances");
}
void R4_pocketSpacing() {
  eachField([](const Model& m) {
    for(std::size_t i=0; i<m.pockets.size(); ++i) {
      const auto& p=m.pockets[i];
      check(p.column>=1 && p.row>=1 && p.column+p.columns<m.columns && p.row+p.rows<m.rows,"R4 wall margin");
      for(std::size_t j=0; j<i; ++j) {
        const auto& q=m.pockets[j];
        check(p.column>=q.column+q.columns+1 || q.column>=p.column+p.columns+1 ||
              p.row>=q.row+q.rows+1 || q.row>=p.row+p.rows+1,"R4 separating row or column");
      }
    }
  });
}
void R5_glowChanceAndWeights() {
  int occupied=0; std::array<int,powerKinds+1> counts{};
  eachField([&](Model m) {
    const auto hp=m.bricks;
    m.settings.glow=0; LevelRecipeTestAccess::glow(m);
    check(m.bricks==hp && std::all_of(m.powers.begin(),m.powers.end(),[](Power p) { return p==Power::None; }),"R5 zero glow");
    m.settings.glow=40; m.settings.weights={0,0,0,0,0}; LevelRecipeTestAccess::glow(m);
    check(m.bricks==hp && std::all_of(m.powers.begin(),m.powers.end(),[](Power p) { return p==Power::None; }),"R5 zero weights");
    m.settings.weights={0,1,2,3,4}; LevelRecipeTestAccess::glow(m);
    for(std::size_t i=0; i<m.bricks.size(); ++i) {
      if(hp[i]>0) { ++occupied; ++counts[static_cast<int>(m.powers[i])]; }
      else check(m.powers[i]==Power::None,"R5 empty cells never glow");
    }
  });
  const int glowing=std::accumulate(counts.begin()+1,counts.end(),0);
  check(counts[1]==0 && glowing>20000,"R5 disabled kind and sample size");
  check(std::abs(static_cast<float>(glowing)/occupied-0.2f)<0.01f,"R5 glow half percents");
  for(int kind=2; kind<=powerKinds; ++kind) check(std::abs(static_cast<float>(counts[kind])/glowing-(kind-1)/10.0f)<0.015f,"R5 weighted proportions");
}
void R6_glowingBricksHaveOneHitPoint() {
  int glowing=0;
  eachField([&](const Model& m) {
    for(std::size_t i=0; i<m.bricks.size(); ++i) if(m.powers[i]!=Power::None) {
      ++glowing; check(m.bricks[i]==1,"R6 glowing brick hit points");
    }
  });
  check(glowing>10000,"R6 glowing sample size");
}
void R7_bombsFitInsideWalls() {
  int bombs=0;
  eachField([&](Model m) {
    for(int size: {3,5,7,9,11}) {
      m.setBombSize(size); m.settings.weights={1,1,1,1,1}; m.settings.glow=50;
      LevelRecipeTestAccess::glow(m);
      for(int r=0; r<m.rows; ++r) for(int c=0; c<m.columns; ++c) if(m.power(c,r)==Power::Bomb) {
        ++bombs; check(!edge(m,c,r),"R7 full blast inside walls");
      }
    }
  });
  check(bombs>10000,"R7 Bomb sample size");
}
void R8_bombWeightIsRemovedWhenItDoesNotFit() {
  int repicked=0;
  eachField([&](Model m) {
    m.settings.glow=50; m.settings.weights={9,1,3,0,0};
    Model noBomb=m; noBomb.settings.weights[0]=0;
    LevelRecipeTestAccess::glow(m); LevelRecipeTestAccess::glow(noBomb);
    // Both enabled sums consume identical draws. Edge picks must be identical,
    // despite Bomb's different weight; this catches retaining its share there.
    for(int r=0; r<m.rows; ++r) for(int c=0; c<m.columns; ++c) if(edge(m,c,r)) {
      check(m.power(c,r)==noBomb.power(c,r),"R8 other weights retain their proportions");
      repicked+=m.power(c,r)!=Power::None;
    }
    m.settings.weights={1,0,0,0,0}; LevelRecipeTestAccess::glow(m);
    for(int r=0; r<m.rows; ++r) for(int c=0; c<m.columns; ++c) if(edge(m,c,r)) check(m.power(c,r)==Power::None,"R8 Bomb-only edge stays plain");
  });
  check(repicked>10000,"R8 re-pick sample size");
}
void R9_goalIsHiddenPlainWithFallback() {
  eachField([](Model m) {
    check(m.goal>=0 && m.bricks[m.goal]>0 && m.powers[m.goal]==Power::None,"R9 occupied plain goal");
    check(!m.visible(m.goal%m.columns,m.goal/m.columns),"R9 goal under fog");
    // Expose every plain brick while keeping a fogged power-up block: only a
    // visible plain brick is eligible, even though hidden bricks still exist.
    std::fill(m.bricks.begin(),m.bricks.end(),0); std::fill(m.powers.begin(),m.powers.end(),Power::None);
    for(int r=6; r<=12; ++r) for(int c=1; c<=7; ++c) {
      m.bricks[r*m.columns+c]=1; m.powers[r*m.columns+c]=Power::Ping;
    }
    const int fallback=m.columns+1; m.bricks[fallback]=3;
    LevelRecipeTestAccess::goal(m);
    check(!m.visible(4,9) && m.goal==fallback && m.visible(1,1),"R9 visible plain fallback");
    m.powers[fallback]=Power::Ping; LevelRecipeTestAccess::goal(m);
    check(m.goal==-1 && !m.won(),"R9 no plain brick means no goal");
  });
}
void R10_goalHasOneHitPoint() {
  eachField([](const Model& m) { check(m.goal>=0 && m.bricks[m.goal]==1,"R10 goal hit points"); });
}

// Collision-driven Ghost fixtures use each level's dimensions and random state.
// An upward shot hits (columns/2,2); candidates are snapshotted after that brick
// breaks but before the new cavity, matching the point at which Ghost chooses.
void triggerSetup(Model& m) {
  m.goal=-1; m.balls.clear(); m.ballsLeft=4; m.bouncesPerBall=99;
  const int c=m.columns/2;
  for(int r=3; r<=7; ++r) { m.bricks[r*m.columns+c]=0; m.powers[r*m.columns+c]=Power::None; }
  m.bricks[2*m.columns+c]=1; m.powers[2*m.columns+c]=Power::Ghost; m.refreshFog();
}
struct Choices { std::vector<int> cleanHidden, clean, hidden, any; };
Choices choices(Model m) {
  m.bricks[2*m.columns+m.columns/2]=0; m.powers[2*m.columns+m.columns/2]=Power::None; m.refreshFog();
  Choices result;
  for(int r=1; r<m.rows-1; ++r) for(int c=1; c<m.columns-1; ++c) {
    if(m.brick(c,r)<=0) continue;
    const int i=r*m.columns+c; bool clean=true;
    for(int dr=-1; dr<=1; ++dr) for(int dc=-1; dc<=1; ++dc)
      if(m.brick(c+dc,r+dr)>0 && m.power(c+dc,r+dr)!=Power::None) clean=false;
    result.any.push_back(i);
    if(!m.visible(c,r)) result.hidden.push_back(i);
    if(clean) { result.clean.push_back(i); if(!m.visible(c,r)) result.cleanHidden.push_back(i); }
  }
  return result;
}
Hits fireGhost(Model& m) {
  const int c=m.columns/2;
  check(m.launch({(c+0.5f)*Model::cell,5.5f*Model::cell},{0,40}),"Ghost fixture launches");
  for(int tick=0; tick<120; ++tick) {
    m.update(1.0f/60);
    if(!m.hits.fired.empty()) {
      check(m.hits.fired.front().power==Power::Ghost,"fixture hits Ghost first"); return m.hits;
    }
  }
  check(false,"Ghost fixture fires"); return {};
}
bool contains(const std::vector<int>& cells, int i) { return std::find(cells.begin(),cells.end(),i)!=cells.end(); }
void R11_ghostPrefersPowerFreeCavities() {
  eachField([](Model m) {
    std::fill(m.bricks.begin(),m.bricks.end(),2); std::fill(m.powers.begin(),m.powers.end(),Power::None);
    for(int r=0; r<m.rows; r+=2) for(int c=0; c<m.columns; c+=4) m.powers[r*m.columns+c]=Power::Ping;
    triggerSetup(m); const auto from=choices(m);
    check(!from.clean.empty() && from.clean.size()<from.any.size(),"R11 mixed landing fixture");
    const auto before=m.powers; const Hits h=fireGhost(m);
    check(contains(from.clean,h.fired.front().to),"R11 clean landing chosen");
    check(h.fired.size()==1 && m.pingTime==0,"R11 landing fires no extra power");
    for(std::size_t i=0; i<before.size(); ++i) if(before[i]==Power::Ping) check(m.powers[i]==Power::Ping,"R11 power-up bricks survive");
  });
}
void R12_ghostLandingPreferenceOrder() {
  eachField([](Model initial) {
    for(int branch=0; branch<5; ++branch) {
      Model m=initial;
      std::fill(m.bricks.begin(),m.bricks.end(),branch==4 ? 0 : 2);
      std::fill(m.powers.begin(),m.powers.end(),branch==2 ? Power::Ping : Power::None);
      if(branch==1) {
        // A clean visible island competes with a hidden power-up block: being
        // clean takes priority over fog, rather than merely over other visible cells.
        std::fill(m.bricks.begin(),m.bricks.end(),0);
        for(int r=8; r<=14; ++r) for(int c=1; c<=7; ++c) {
          m.bricks[r*m.columns+c]=2; m.powers[r*m.columns+c]=Power::Ping;
        }
        m.bricks[(m.rows-3)*m.columns+m.columns-3]=2;
      }
      if(branch==3) {
        std::fill(m.bricks.begin(),m.bricks.end(),0);
        for(int r=10; r<=12; ++r) for(int c=2; c<=4; ++c) {
          m.bricks[r*m.columns+c]=2; m.powers[r*m.columns+c]=Power::Ping;
        }
      }
      triggerSetup(m); const auto from=choices(m);
      const std::vector<int>* expected=nullptr;
      if(branch==0) { check(!from.cleanHidden.empty() && from.cleanHidden.size()<from.clean.size(),"R12 hidden and visible clean choices"); expected=&from.cleanHidden; }
      if(branch==1) { check(from.cleanHidden.empty() && !from.clean.empty() && !from.hidden.empty(),"R12 clean visible beats dirty hidden fixture"); expected=&from.clean; }
      if(branch==2) { check(from.clean.empty() && !from.hidden.empty() && from.hidden.size()<from.any.size(),"R12 dirty hidden fallback fixture"); expected=&from.hidden; }
      if(branch==3) { check(from.clean.empty() && from.hidden.empty() && !from.any.empty(),"R12 dirty visible fallback fixture"); expected=&from.any; }
      if(branch==4) check(from.any.empty(),"R12 no landing fixture");
      const auto h=fireGhost(m);
      check(expected ? contains(*expected,h.fired.front().to) : h.fired.front().to==-1,"R12 landing priority or no destination");
    }
  });
}
void R13_ghostCavityCanWin() {
  eachField([](Model m) {
    std::fill(m.bricks.begin(),m.bricks.end(),0); std::fill(m.powers.begin(),m.powers.end(),Power::None);
    m.bricks[12*m.columns+2]=2; m.bricks[12*m.columns+3]=1;
    triggerSetup(m); m.goal=12*m.columns+3;
    const auto h=fireGhost(m);
    check(h.fired.front().to>=0 && m.bricks[m.goal]==0 && h.goalBroken && m.won(),"R13 goal landing stays eligible and wins");
  });
}
void R14_ghostFallbackPowersVanishWithoutFiring() {
  eachField([](Model m) {
    std::fill(m.bricks.begin(),m.bricks.end(),2);
    // Every occupied candidate holds a power, including Bomb, Electricity,
    // Speed and another Ghost: none of their effects may run from the cavity.
    for(std::size_t i=0; i<m.powers.size(); ++i) m.powers[i]=static_cast<Power>(1+i%powerKinds);
    triggerSetup(m); const auto from=choices(m);
    check(from.clean.empty(),"R14 no clean landing fixture");
    const auto before=m.bricks; const auto powers=m.powers;
    const auto h=fireGhost(m); const int to=h.fired.front().to;
    check(to>=0 && h.fired.size()==1 && m.pingTime==0,"R14 only source Ghost fires");
    const int c=to%m.columns, r=to/m.columns, source=2*m.columns+m.columns/2;
    for(int y=0; y<m.rows; ++y) for(int x=0; x<m.columns; ++x) {
      const int i=y*m.columns+x;
      if(std::abs(x-c)<=1 && std::abs(y-r)<=1) check(m.bricks[i]==0 && m.powers[i]==Power::None,"R14 cavity powers vanish");
      else if(i!=source) check(m.bricks[i]==before[i] && m.powers[i]==powers[i],"R14 outside cavity unchanged");
    }
    check(m.balls.size()==1 && !m.balls[0].fast && m.balls[0].electric==0 &&
          std::abs(std::hypot(m.balls[0].velocity.x,m.balls[0].velocity.y)-Model::speed)<0.01f && m.balls[0].bounces==98,"R14 no side effects; velocity and bounces kept");
  });
}
}

void levelGenerationChecks() {
  seededFieldsUnchanged();
  const auto run=[](const char* rule, auto test) { test(); std::cout<<"Level recipe "<<rule<<": passed\n"; };
  run("R1",R1_gridSizeAndHitPoints);
  run("R2",R2_levelSeedsFollowBotBands);
  run("R3",R3_pocketSizeAndCount);
  run("R4",R4_pocketSpacing);
  run("R5",R5_glowChanceAndWeights);
  run("R6",R6_glowingBricksHaveOneHitPoint);
  run("R7",R7_bombsFitInsideWalls);
  run("R8",R8_bombWeightIsRemovedWhenItDoesNotFit);
  run("R9",R9_goalIsHiddenPlainWithFallback);
  run("R10",R10_goalHasOneHitPoint);
  run("R11",R11_ghostPrefersPowerFreeCavities);
  run("R12",R12_ghostLandingPreferenceOrder);
  run("R13",R13_ghostCavityCanWin);
  run("R14",R14_ghostFallbackPowersVanishWithoutFiring);
  std::cout<<"Level recipe: ten shipped field hashes unchanged; rules cover 64 seeds per level\n";
}
