// Each level plays like its feel for the simulated player (T11, docs/levels.md): over the fixed
// bot seeds 1..runs its results stay inside its feel's target, and the curve stays gradual.
#include "level_bot.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>

namespace {
void check(bool ok, int level, const char* label) { if(!ok) { std::cerr<<"level "<<level<<": "<<label<<'\n'; std::exit(1); } }
}

void levelBandChecks() {
  using namespace tapdemo;
  constexpr int runs=200; // bot seeds 1..200, the first tenth of the report's 2000
  levelbot::Metrics measured[levelCount];
  for(int n=1; n<=levelCount; ++n) {
    const Level& l=levels[n-1];
    const auto m=measured[n-1]=levelbot::measure(n,l,1,runs);
    const auto t=target(l.feel);
    std::cout<<"Level "<<n<<": wins "<<100*m.winRate()<<"%, last ball "<<100*m.lastBallShare()<<"%, chain "<<100*m.chainShare()
      <<"%, near losses "<<100*m.nearShare()<<"%\n";
    check(m.winRate()>=t.minWins && m.winRate()<=t.maxWins,n,"the win rate is outside its feel's band");
    check(m.lastBallShare()>=t.lastBall,n,"too few wins come on the last ball");
    check(m.chainShare()>=t.chain,n,"too few wins come through a power-up");
    check(m.nearShare()>=t.near,n,"too few losses come near the goal");
    // Gradual: no level is easier than an earlier level of the same feel by more than five points.
    for(int k=1; k<n; ++k)
      check(levels[k-1].feel!=l.feel || m.winRate()<=measured[k-1].winRate()+0.05f,n,"a later level of the same feel is clearly easier");
  }
  // The player's path: levels 1 to 3 with every shot pressed, dragged and released on the
  // fitted screen meet the same band. The screen round trip moves a shot by a hair, and a long
  // flight can then end elsewhere, so the counts may differ a little.
  for(int n=1; n<=3; ++n) {
    const auto touched=levelbot::measure(n,levels[n-1],1,runs,{},true);
    const auto t=target(levels[n-1].feel);
    std::cout<<"Level "<<n<<" through touch: wins "<<100*touched.winRate()<<"%\n";
    check(touched.winRate()>=t.minWins && touched.winRate()<=t.maxWins,n,"through touch the win rate is outside its feel's band");
    check(std::abs(touched.winRate()-measured[n-1].winRate())<=0.05f,n,"through touch the win rate differs from the direct shots'");
  }
  std::cout<<"Level bands: ten levels meet their feel over "<<runs<<" bot games each; levels 1-3 also through touch\n";
}
