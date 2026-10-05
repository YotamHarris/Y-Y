#include <tapdemo/model.hpp>
#include <yy/runtime.hpp>
#include <string>

namespace tapdemo {
class TapGame final: public yy::Game {
  Model model;
  yy::Audio* audio{};
public:
  void initialize(yy::Services& services) override { audio=&services.audio; }
  void update(float seconds) override { model.update(seconds); }
  void pause(bool value) override { model.pause(value); }
  void shutdown() override { audio=nullptr; }
  void tap(yy::Vec2 p) override { if(model.tap(p) && audio) audio->tone(660.0f); }
  void render(yy::Renderer& r) override {
    using yy::Color;
    const Color muted{132,155,178}, white{231,240,248}, teal{70,235,196};
    const Color targetRed{235,70,70};
    r.text({24,30}, "YY / TAP DEMO", teal, 2);
    r.text({24,74}, "CATCH THE MOMENT", white, 2);
    r.text({24,98}, "VERSION " YY_GAME_VERSION, muted, 1.5f);
    r.rectangle({24,118,342,2}, {40,61,80});
    r.text({24,140}, "SCORE " + std::to_string(model.score), white);
    r.text({218,140}, "TIME " + std::to_string(static_cast<int>(std::ceil(model.remaining))), muted);
    for(const auto& t: model.targets) {
      r.circle(t.position,t.radius+8,{22,60,65});
      r.circle(t.position,t.radius,targetRed);
      if(!r.sprite("spark.bmp",{t.position.x-15,t.position.y-16,16,16}))
        r.circle({t.position.x-7,t.position.y-8},6,{166,255,226});
    }
    r.rectangle({24,755,342,3},{40,61,80});
    r.rectangle({24,755,342*model.remaining/30,3},teal);
    r.text({24,783}, "TAP TARGETS / 30 SECONDS",muted,1.5f);
    if(model.finished()) {
      r.rectangle({24,340,342,156},{15,29,45});
      r.text({58,367}, "ROUND COMPLETE",white);
      r.text({58,405}, "SCORE " + std::to_string(model.score),teal);
      r.text({58,445}, "TAP TO RESTART",muted);
    }
  }
};
std::unique_ptr<yy::Game> createGame() { return std::make_unique<TapGame>(); }
}
