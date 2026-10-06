// Picks level seeds by simulated play (T11). Not part of the test run.
//   level_tune measure [runs]                       every level in the table, bot seeds 1..runs
//   level_tune search LEVEL FIRST COUNT RUNS [balls bounces]
//       tries level seeds FIRST..FIRST+COUNT-1 with RUNS bot seeds each, optionally with other
//       balls and bounces, and prints the ten that best meet the level's target over both the
//       report's and the band test's bot seeds
#include "level_bot.hpp"
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

using tapdemo::Feel; using tapdemo::Level; using tapdemo::levels; using tapdemo::levelCount;

// The report's runs, and the band test's (bot seeds 1..testRuns, a prefix of the report's).
constexpr int reportRuns=2000, testRuns=200;
static const char* feelName(Feel f) {
  switch(f) { case Feel::Relief: return "relief"; case Feel::BuildUp: return "build-up"; case Feel::Fu: return "fu"; case Feel::FuckYeah: return "fuck-yeah"; }
  return "?";
}
// How far the metrics sit from the target: 0 inside every band, plus a pull toward the middle
// of the win band so a seed keeps its margin, and for a build-up level toward power-up wins.
static float miss(Feel feel, const levelbot::Metrics& m) {
  const auto t=tapdemo::target(feel);
  const float w=m.winRate();
  float d=std::max(0.0f,t.minWins-w)+std::max(0.0f,w-t.maxWins);
  d+=std::max(0.0f,t.lastBall-m.lastBallShare())+std::max(0.0f,t.chain-m.chainShare())+std::max(0.0f,t.near-m.nearShare());
  const float middle=feel==Feel::Relief ? 1.0f : feel==Feel::Fu ? 0.12f : (t.minWins+t.maxWins)/2;
  // A build-up level teaches: its wins should come through its power-ups.
  const float lesson=feel==Feel::BuildUp ? 0.1f*(1-m.chainShare()) : 0.0f;
  return 4*d+std::abs(w-middle)*0.2f+lesson;
}
static void print(int level, const Level& l, const levelbot::Metrics& m) {
  std::printf("L%-2d %-9s seed %-8u balls %2d bounces %2d  wins %5.1f%%  last %5.1f%%  chain %5.1f%%  last+chain %5.1f%%  near %5.1f%%  seen %5.1f%%  dist %.2f  miss %.3f\n",
    level,feelName(l.feel),l.seed,l.balls,l.bounces,100*m.winRate(),100*m.lastBallShare(),100*m.chainShare(),
    m.wins ? 100.0f*m.lastChainWins/m.wins : 0.0f,100*m.nearShare(),m.runs>m.wins ? 100.0f*m.seenLosses/(m.runs-m.wins) : 0.0f,
    m.meanLossDistance(),miss(l.feel,m));
}
// Runs fn(i) for i in 0..count-1 across the machine's threads.
template<class F> static void parallel(int count, F fn) {
  std::atomic<int> next{0};
  std::vector<std::thread> threads;
  for(unsigned t=0; t<std::max(1u,std::thread::hardware_concurrency()); ++t)
    threads.emplace_back([&] { for(int i; (i=next++)<count; ) fn(i); });
  for(auto& t: threads) t.join();
}
int main(int argc, char** argv) {
  if(argc>=2 && std::strcmp(argv[1],"measure")==0) {
    const int runs=argc>=3 ? std::atoi(argv[2]) : 1000;
    std::vector<levelbot::Metrics> results(levelCount);
    parallel(levelCount,[&](int i) { results[i]=levelbot::measure(i+1,levels[i],1,runs); });
    for(int i=0; i<levelCount; ++i) print(i+1,levels[i],results[i]);
    return 0;
  }
  if(argc>=6 && std::strcmp(argv[1],"search")==0) {
    const int level=std::clamp(std::atoi(argv[2]),1,levelCount);
    const auto first=static_cast<std::uint32_t>(std::strtoul(argv[3],nullptr,10));
    const int count=std::atoi(argv[4]), runs=std::atoi(argv[5]);
    Level base=levels[level-1];
    if(argc>=8) { base.balls=std::atoi(argv[6]); base.bounces=std::atoi(argv[7]); }
    struct Candidate { Level level; levelbot::Metrics metrics; float miss; };
    std::vector<Candidate> found(count);
    parallel(count,[&](int i) {
      Level l=base; l.seed=first+static_cast<std::uint32_t>(i);
      // The goal must start in the fog, as every level's does.
      tapdemo::Model m; m.play(level,l);
      if(m.goal<0 || m.visible(m.goal%m.columns,m.goal/m.columns)) { found[i]={l,{},1e9f}; return; }
      const auto metrics=levelbot::measure(level,l,1,runs);
      found[i]={l,metrics,miss(l.feel,metrics)};
    });
    std::sort(found.begin(),found.end(),[](const Candidate& a, const Candidate& b) { return a.miss<b.miss; });
    // The best forty again over the report's runs and the test's, ranked by the worse of the two.
    found.resize(std::min(40,count));
    std::vector<Candidate> test(found.size());
    parallel(static_cast<int>(found.size()),[&](int i) {
      Candidate& c=found[i];
      c.metrics=levelbot::measure(level,c.level,1,reportRuns); c.miss=miss(c.level.feel,c.metrics);
      test[i]=c; test[i].metrics=levelbot::measure(level,c.level,1,testRuns); test[i].miss=miss(c.level.feel,test[i].metrics);
      c.miss=std::max(c.miss,test[i].miss);
    });
    std::vector<int> order(found.size());
    for(int i=0; i<static_cast<int>(order.size()); ++i) order[i]=i;
    std::sort(order.begin(),order.end(),[&](int a, int b) { return found[a].miss<found[b].miss; });
    for(int i=0; i<std::min<int>(10,static_cast<int>(order.size())); ++i) {
      print(level,found[order[i]].level,found[order[i]].metrics);
      std::printf("    over the test's %d runs: ",testRuns); print(level,test[order[i]].level,test[order[i]].metrics);
    }
    return 0;
  }
  std::fprintf(stderr,"usage: level_tune measure [runs] | search LEVEL FIRST COUNT RUNS [balls bounces]\n");
  return 2;
}
