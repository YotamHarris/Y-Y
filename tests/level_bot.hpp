#pragma once
// A headless player for the ten levels (T11). It plays a level to the end, one ball at a time:
// each shot is placed at the centre of a random open cell of a cavity, and aimed either at
// something it can see (the goal once it shows, else a glowing brick) or in a random direction.
// launch() applies the snap the player gets. The bot is a simple stand-in, not a person.
// With `touch` each shot goes through the player's touch path instead: a press on the fitted
// screen, a drag and a release, as a finger makes them.
#include <tapdemo/model.hpp>
#include <tapdemo/touch.hpp>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <vector>

namespace levelbot {
using tapdemo::Model; using tapdemo::Power; using tapdemo::Feel;

// One played level: how it ended, and on a win, how; on a loss, how close it came.
struct Result {
  bool won{};
  bool lastBall{};  // the goal broke on the level's last ball
  bool chain{};     // a power-up fired, or an electric ball zapped, in the update the goal broke
  bool goalSeen{};  // the goal showed at any time (out of the fog, or pinged)
  int goalDistance{}; // straight steps from the goal to the nearest open cell when the level ended
  int ghostLandings{}; // Ghost power-ups that moved the ball
  int ghostTakes{};    // power-ups that fired from inside a Ghost's landing cavity in the update it landed
};

// A small generator of the bot's own, apart from the model's.
struct Random {
  std::uint64_t state;
  explicit Random(std::uint64_t seed): state(seed*0x9e3779b97f4a7c15ull+0x632be59bd9b4e019ull) {}
  std::uint32_t next() {
    std::uint64_t z=(state+=0x9e3779b97f4a7c15ull);
    z=(z^(z>>30))*0xbf58476d1ce4e5b9ull; z=(z^(z>>27))*0x94d049bb133111ebull;
    return static_cast<std::uint32_t>((z^(z>>31))>>32);
  }
  float unit() { return (next()>>8)*(1.0f/16777216.0f); }
  int below(int n) { return static_cast<int>(next()%static_cast<std::uint32_t>(n)); }
};

// How the bot aims: the chance a shot goes at something it can see, and how far off it lands.
struct Style {
  float aimed=0.6f;      // otherwise a uniformly random direction
  float spreadDegrees=4; // an aimed shot is off by up to this much either way
};

inline bool goalShows(const Model& m) {
  if(m.goal<0) return false;
  const int c=m.goal%m.columns, r=m.goal/m.columns;
  return m.visible(c,r) || m.pinged(c,r);
}

// Plays level `level` as `table` sets it up with bot seed `botSeed` and returns how it went.
// With `shots` (and `touch`) each launched shot's press and release, in screen units, is appended to it:
// the same shots replayed on a game reproduce the round. With `end` the finished model is copied to it.
struct Shot { yy::Vec2 press, release; };
inline Result play(int level, const tapdemo::Level& table, std::uint32_t botSeed, Style style={}, bool touch=false, std::vector<Shot>* shots=nullptr, Model* end=nullptr) {
  Model m; m.play(level,table);
  tapdemo::Touch finger(m); finger.instructions=false;
  // Difficulty/recorded-flight regressions use the player's whole-grid manual view.
  // The opening camera and its moving touch mapping have their own framing checks.
  finger.camera.fit(); finger.framing.automatic=false;
  Random random(botSeed);
  Result result;
  constexpr float dt=1.0f/60, twoPi=6.28318530718f;
  std::vector<int> open, targets;
  while(!m.over()) {
    open.clear();
    for(int i=0; i<m.columns*m.rows; ++i) {
      if(m.bricks[i]>0) continue;
      const yy::Vec2 at{(i%m.columns+0.5f)*Model::cell,(i/m.columns+0.5f)*Model::cell};
      if(m.canPlace(at)) open.push_back(i);
    }
    if(open.empty()) break;
    const int from=open[random.below(static_cast<int>(open.size()))];
    const yy::Vec2 at{(from%m.columns+0.5f)*Model::cell,(from/m.columns+0.5f)*Model::cell};
    float angle=random.unit()*twoPi;
    if(random.unit()<style.aimed) {
      targets.clear();
      if(goalShows(m)) targets.push_back(m.goal);
      else for(int i=0; i<m.columns*m.rows; ++i)
        if(m.powers[i]!=Power::None && (m.visible(i%m.columns,i/m.columns) || m.pinged(i%m.columns,i/m.columns))) targets.push_back(i);
      if(!targets.empty()) {
        const int t=targets[random.below(static_cast<int>(targets.size()))];
        const float dx=(t%m.columns+0.5f)*Model::cell-at.x, dy=(t/m.columns+0.5f)*Model::cell-at.y;
        angle=std::atan2(dy,dx)+(random.unit()*2-1)*style.spreadDegrees*0.0174532925f;
      }
    }
    // The ball flies opposite the pull.
    const yy::Vec2 pull{-60*std::cos(angle),-60*std::sin(angle)};
    bool launched;
    if(touch) {
      const yy::Vec2 press=finger.camera.toScreen(at), release=finger.camera.toScreen({at.x+pull.x,at.y+pull.y});
      finger.down(1,press); finger.move(1,release); launched=finger.up(1,release);
      if(launched && shots) shots->push_back({press,release});
    } else launched=m.launch(at,pull);
    if(!launched) break;
    const bool last=m.ballsLeft==0;
    for(int t=0; t<60*120 && !m.balls.empty() && !m.over(); ++t) {
      bool electric=false;
      for(const auto& b: m.balls) electric|=b.electric>0;
      m.update(dt);
      result.goalSeen|=goalShows(m);
      // A power-up fired after a Ghost in the same update, from a brick in its 3x3, was taken for free.
      const auto& fired=m.hits.fired;
      for(std::size_t g=0; g<fired.size(); ++g) {
        if(fired[g].power!=Power::Ghost || fired[g].to<0) continue;
        ++result.ghostLandings;
        for(std::size_t k=g+1; k<fired.size(); ++k)
          result.ghostTakes+=std::abs(fired[k].cell%m.columns-fired[g].to%m.columns)<=Model::ghostSize/2 &&
                             std::abs(fired[k].cell/m.columns-fired[g].to/m.columns)<=Model::ghostSize/2;
      }
      if(m.won()) { result.won=true; result.lastBall=last; result.chain=electric || !m.hits.fired.empty(); }
    }
    m.balls.clear(); // a ball still flying after two minutes is spent
  }
  if(m.goal>=0) result.goalDistance=m.fogDistance(m.goal%m.columns,m.goal/m.columns);
  if(end) *end=m; // the field as the round ended
  return result;
}

// A level's results over many bot seeds.
struct Metrics {
  int runs{}, wins{}, lastBallWins{}, chainWins{}, lastChainWins{}, nearLosses{}, seenLosses{};
  long long lossDistance{};
  int ghostLandings{}, ghostTakes{};
  void add(const Result& r) {
    ++runs; ghostLandings+=r.ghostLandings; ghostTakes+=r.ghostTakes;
    if(r.won) { ++wins; lastBallWins+=r.lastBall; chainWins+=r.chain; lastChainWins+=r.lastBall && r.chain; return; }
    nearLosses+=r.goalSeen || r.goalDistance<=2; seenLosses+=r.goalSeen; lossDistance+=r.goalDistance;
  }
  float winRate() const { return runs ? static_cast<float>(wins)/runs : 0; }
  float lastBallShare() const { return wins ? static_cast<float>(lastBallWins)/wins : 0; }
  float chainShare() const { return wins ? static_cast<float>(chainWins)/wins : 0; }
  float nearShare() const { return runs>wins ? static_cast<float>(nearLosses)/(runs-wins) : 0; }
  float meanLossDistance() const { return runs>wins ? static_cast<float>(lossDistance)/(runs-wins) : 0; }
};

// Bot seeds first..first+runs-1 on one level.
inline Metrics measure(int level, const tapdemo::Level& table, std::uint32_t first, int runs, Style style={}, bool touch=false) {
  Metrics m;
  for(int i=0; i<runs; ++i) m.add(play(level,table,first+static_cast<std::uint32_t>(i),style,touch));
  return m;
}
}
