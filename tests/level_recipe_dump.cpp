// Canonical bytes for comparing a recipe refactor with an earlier model.cpp.
// Compile this file with either revision's model.cpp; no SDL or native padding.
#include <tapdemo/model.hpp>
#include <cstdlib>
#include <fstream>
#include <iostream>

namespace {
void word(std::ostream& out, std::uint32_t value) {
  for(int shift=0; shift<32; shift+=8) out.put(static_cast<char>((value>>shift)&255));
}
void field(std::ostream& out, const tapdemo::Model& m) {
  word(out,m.columns); word(out,m.rows); word(out,m.goal); word(out,m.won());
  word(out,static_cast<std::uint32_t>(m.pockets.size()));
  for(const auto& p: m.pockets) { word(out,p.column); word(out,p.row); word(out,p.columns); word(out,p.rows); }
  for(std::size_t i=0; i<m.bricks.size(); ++i) {
    word(out,m.bricks[i]); word(out,static_cast<std::uint32_t>(m.powers[i]));
    word(out,m.fogDistance(static_cast<int>(i)%m.columns,static_cast<int>(i)/m.columns));
  }
}
void landing(std::ostream& out, tapdemo::Model m) {
  // A controlled trigger uses the random state left by generation, so a shifted
  // draw cannot hide behind an identical opening field.
  const int c=m.columns/2, trigger=2*m.columns+c;
  m.goal=-1;
  for(int r=3; r<=7; ++r) { m.bricks[r*m.columns+c]=0; m.powers[r*m.columns+c]=tapdemo::Power::None; }
  m.bricks[trigger]=1; m.powers[trigger]=tapdemo::Power::Ghost; m.refreshFog();
  if(!m.launch({(c+0.5f)*m.cell,5.5f*m.cell},{0,40})) std::exit(1);
  for(int tick=0; tick<120; ++tick) {
    m.update(1.0f/60);
    for(const auto& f: m.hits.fired) if(f.power==tapdemo::Power::Ghost) {
      word(out,f.to); field(out,m); return;
    }
  }
  std::cerr<<"Ghost trigger did not fire\n"; std::exit(1);
}
}

int main(int argc, char** argv) {
  if(argc!=2) { std::cerr<<"usage: level_recipe_dump OUTPUT\n"; return 1; }
  std::ofstream out(argv[1],std::ios::binary);
  for(int level=1; level<=tapdemo::levelCount; ++level) {
    tapdemo::Model m; m.play(level); field(out,m); landing(out,m);
    for(std::uint32_t seed=1; seed<=128; ++seed) {
      auto table=tapdemo::levels[level-1]; table.seed=seed;
      m.play(level,table); field(out,m); landing(out,m);
      m.restart(table.balls,table.bounces,table.grid); field(out,m); landing(out,m);
    }
  }
  out.close();
  return out ? 0 : 1;
}
