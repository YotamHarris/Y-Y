#define SDL_MAIN_USE_CALLBACKS 1
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <yy/runtime.hpp>
#include <cstdlib>
#include <cstring>

namespace tapdemo { std::unique_ptr<yy::Game> createGame(); }
SDL_AppResult SDL_AppInit(void** state, int argc, char** argv) {
  SDL_SetAppMetadata(YY_GAME_TITLE,YY_GAME_VERSION,YY_BUNDLE_IDENTIFIER);
  int smoke=0;
  for(int i=1; i+1<argc; ++i) if(std::strcmp(argv[i],"--smoke")==0) smoke=std::atoi(argv[++i]);
  if(const char* env=std::getenv("YY_SMOKE_FRAMES")) smoke=std::atoi(env);
  auto runtime=std::make_unique<yy::Runtime>(tapdemo::createGame(),smoke,YY_ASSET_DIRECTORY);
  if(!runtime->initialize()) return SDL_APP_FAILURE;
  *state=runtime.release(); return SDL_APP_CONTINUE;
}
SDL_AppResult SDL_AppEvent(void* state, SDL_Event* event) {
  return static_cast<yy::Runtime*>(state)->event(event) ? SDL_APP_CONTINUE : SDL_APP_SUCCESS;
}
SDL_AppResult SDL_AppIterate(void* state) {
  return static_cast<yy::Runtime*>(state)->iterate() ? SDL_APP_CONTINUE : SDL_APP_SUCCESS;
}
void SDL_AppQuit(void* state, SDL_AppResult) { delete static_cast<yy::Runtime*>(state); }
