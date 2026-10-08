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
    check(l.balls==levelBalls,n,"every level gives the same number of balls");
    const auto m=measured[n-1]=levelbot::measure(n,l,1,runs);
    const auto t=target(l.feel);
    std::cout<<"Level "<<n<<": wins "<<100*m.winRate()<<"%, last ball "<<100*m.lastBallShare()<<"%, chain "<<100*m.chainShare()
      <<"%, near losses "<<100*m.nearShare()<<"%\n";
    check(m.winRate()>=t.minWins && m.winRate()<=t.maxWins,n,"the win rate is outside its feel's band");
    check(l.expectedWins>=std::lround(100*t.minWins) && l.expectedWins<=std::lround(100*t.maxWins),n,"the card's expected win rate is outside its feel's band");
    // The card's figure comes from 2000 games; these 200 carry about 3.5 points of noise, so 7 points is two of it.
    check(std::abs(m.winRate()-l.expectedWins/100.0f)<=0.07f,n,"the card's expected win rate differs from the measured one");
    check(m.lastBallShare()>=t.lastBall,n,"too few wins come on the last ball");
    check(m.chainShare()>=t.chain,n,"too few wins come through a power-up");
    check(m.nearShare()>=t.near,n,"too few losses come near the goal");
    // Gradual: no level is easier than an earlier level of the same feel by more than ten points (200 games carry about 3.5 points of noise).
    for(int k=1; k<n; ++k)
      check(levels[k-1].feel!=l.feel || m.winRate()<=measured[k-1].winRate()+0.10f,n,"a later level of the same feel is clearly easier");
  }
  // The player's path: levels 1 to 3 and 10 with every shot pressed, dragged and released on the
  // fitted screen meet the same band. The screen round trip moves a shot by a hair, and a long
  // flight can then end elsewhere, so the counts may differ a little.
  for(int n: {1,2,3,10}) {
    const auto touched=levelbot::measure(n,levels[n-1],1,runs,{},true);
    const auto t=target(levels[n-1].feel);
    std::cout<<"Level "<<n<<" through touch: wins "<<100*touched.winRate()<<"%\n";
    check(touched.winRate()>=t.minWins && touched.winRate()<=t.maxWins,n,"through touch the win rate is outside its feel's band");
    check(std::abs(touched.winRate()-measured[n-1].winRate())<=0.05f,n,"through touch the win rate differs from the direct shots'");
    if(n==1 || n==2 || n==10) {
      std::cout<<"Level "<<n<<" Bombs through touch: "<<touched.bombFirings<<" firings, "
        <<touched.bombGoalBreaks<<" goal breaks, "<<touched.bombGhostFires<<" Ghost firings\n";
      check(touched.bombFirings>0,n,"touch path fires no Bombs");
      check(touched.bombGoalBreaks==0,n,"a Bomb blast broke the goal through touch");
      check(touched.bombGhostFires==0,n,"a Bomb blast fired Ghost through touch");
      if(n<=2) std::cout<<"Level "<<n<<": all "<<touched.wins<<" wins are ball hits; "<<touched.winsAfterBombs<<" winning rounds fired Bombs earlier\n";
    }
  }
  // Ghost on level 9 through the player's touch path: every landing takes no power-up from its cavity.
  {
    constexpr int ghostLevel=9;
    check(levels[ghostLevel-1].introduces==Power::Ghost,ghostLevel,"level 9 introduces Ghost");
    const auto touched=levelbot::measure(ghostLevel,levels[ghostLevel-1],1,runs,{},true);
    std::cout<<"Level "<<ghostLevel<<" through touch: wins "<<100*touched.winRate()<<"%, "<<touched.ghostLandings
      <<" Ghost landings, "<<touched.ghostTakes<<" power-ups fired from a landing\n";
    check(touched.ghostLandings>0,ghostLevel,"no Ghost landed through touch");
    check(touched.ghostTakes==0,ghostLevel,"a Ghost landing fired another power-up");
  }
  std::cout<<"Level bands: ten levels meet their feel over "<<runs<<" bot games each; levels 1-3, 9 and 10 also through touch\n";
}
