#include <yy/runtime.hpp>
#include <SDL3/SDL.h>
#include <array>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <unordered_map>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#elif defined(__APPLE__)
#include <mach/mach.h>
#include <TargetConditionals.h>
#endif
#if defined(__APPLE__) && TARGET_OS_IOS
#include "apple_haptics.hpp"
#endif

namespace yy {
namespace {
std::uint64_t memoryBytes() {
#ifdef _WIN32
  PROCESS_MEMORY_COUNTERS info{};
  if(GetProcessMemoryInfo(GetCurrentProcess(),&info,sizeof(info))) return info.WorkingSetSize;
#elif defined(__APPLE__)
  mach_task_basic_info_data_t info{}; mach_msg_type_number_t count=MACH_TASK_BASIC_INFO_COUNT;
  if(task_info(mach_task_self(),MACH_TASK_BASIC_INFO,reinterpret_cast<task_info_t>(&info),&count)==KERN_SUCCESS) return info.resident_size;
#endif
  return 0;
}
class SDLRenderer final: public Renderer {
public:
  SDL_Renderer* handle{};
  Viewport viewport;
  float pixelScale{1};
  std::unordered_map<std::string,SDL_Texture*> textures;
  std::string assetDirectory;
  ~SDLRenderer() override {
    for(auto& [_,t]:textures) if(t) SDL_DestroyTexture(t);
    for(auto& [_,f]:fonts) for(auto* t: f.sheets) SDL_DestroyTexture(t);
    if(handle) SDL_DestroyRenderer(handle);
  }
  Vec2 screen(Vec2 p) const { const auto r=viewport.content(); return {(r.x+p.x*viewport.scale())*pixelScale,(r.y+p.y*viewport.scale())*pixelScale}; }
  void color(Color c) { SDL_SetRenderDrawColor(handle,c.r,c.g,c.b,c.a); }
  void rectangle(Rect r,Color c) override {
    const auto p=screen({r.x,r.y}); const float s=viewport.scale()*pixelScale;
    SDL_FRect rect{p.x,p.y,r.w*s,r.h*s}; color(c); SDL_RenderFillRect(handle,&rect);
  }
  void circle(Vec2 center,float radius,Color c) override {
    constexpr int segments=40; std::array<SDL_Vertex,segments*3> vertices{};
    const auto p=screen(center); const float rad=radius*viewport.scale()*pixelScale;
    const SDL_FColor fc{c.r/255.0f,c.g/255.0f,c.b/255.0f,c.a/255.0f};
    for(int i=0;i<segments;++i) {
      const float a=static_cast<float>(i)*6.2831853f/segments,b=static_cast<float>(i+1)*6.2831853f/segments;
      vertices[i*3]={{p.x,p.y},fc,{}};
      vertices[i*3+1]={{p.x+std::cos(a)*rad,p.y+std::sin(a)*rad},fc,{}};
      vertices[i*3+2]={{p.x+std::cos(b)*rad,p.y+std::sin(b)*rad},fc,{}};
    }
    SDL_RenderGeometry(handle,nullptr,vertices.data(),static_cast<int>(vertices.size()),nullptr,0);
  }
  void text(Vec2 p,std::string_view value,Color c,float fontScale) override {
    const auto point=screen(p); const float s=viewport.scale()*pixelScale*fontScale;
    if(s<=0) return;
    SDL_SetRenderScale(handle,s,s); color(c);
    SDL_RenderDebugText(handle,point.x/s,point.y/s,std::string(value).c_str());
    SDL_SetRenderScale(handle,1,1);
  }
  // Asset names are relative to the bundled assets directory.
  std::string assetPath(std::string_view name) const {
    if(name.empty() || name.find("..")!=std::string_view::npos || name.find(':')!=std::string_view::npos || name.front()=='/' || name.front()=='\\') return {};
    return std::string(SDL_GetBasePath())+assetDirectory+std::string(name);
  }
  // Every texture holds premultiplied alpha and draws with linear filtering: blending premultiplied
  // texels, a scaled edge fades to transparent instead of towards the black of the clear pixels.
  SDL_Texture* upload(SDL_Surface* premultiplied) {
    if(!premultiplied) return nullptr;
    SDL_Texture* texture=SDL_CreateTextureFromSurface(handle,premultiplied); SDL_DestroySurface(premultiplied);
    if(texture) { SDL_SetTextureBlendMode(texture,SDL_BLENDMODE_BLEND_PREMULTIPLIED); SDL_SetTextureScaleMode(texture,SDL_SCALEMODE_LINEAR); }
    return texture;
  }
  // A failed load is cached too, so a missing asset is looked for once.
  SDL_Texture* texture(std::string_view asset) {
    const std::string key(asset);
    if(auto it=textures.find(key); it!=textures.end()) return it->second;
    const std::string path=assetPath(key);
    SDL_Surface* image=path.empty() ? nullptr : SDL_LoadBMP(path.c_str());
    if(image && SDL_ISPIXELFORMAT_ALPHA(image->format) && !SDL_PremultiplySurfaceAlpha(image,false)) { SDL_DestroySurface(image); image=nullptr; }
    return textures.emplace(key,upload(image)).first->second;
  }
  SDL_FRect device(Rect r) const {
    const auto p=screen({r.x,r.y}); const float s=viewport.scale()*pixelScale;
    return {p.x,p.y,r.w*s,r.h*s};
  }
  bool sprite(std::string_view asset,Rect dst) override {
    SDL_Texture* t=texture(asset); const SDL_FRect rect=device(dst);
    return t && SDL_RenderTexture(handle,t,nullptr,&rect);
  }
  bool sprite(std::string_view asset,Rect src,Rect dst) override {
    SDL_Texture* t=texture(asset); const SDL_FRect rect=device(dst);
    const float inX=std::min(0.5f,src.w/2), inY=std::min(0.5f,src.h/2);
    const SDL_FRect from{src.x+inX,src.y+inY,src.w-2*inX,src.h-2*inY};
    return t && SDL_RenderTexture(handle,t,&from,&rect);
  }
  // A glyph sheet is 8-bit greyscale coverage: white text, premultiplied, tinted at draw time.
  SDL_Texture* coverageSheet(const std::string& path) {
    SDL_Surface* sheet=SDL_LoadBMP(path.c_str());
    if(!sheet) return nullptr;
    SDL_Palette* palette=SDL_GetSurfacePalette(sheet);
    SDL_Surface* rgba=sheet->format==SDL_PIXELFORMAT_INDEX8 && palette ? SDL_CreateSurface(sheet->w,sheet->h,SDL_PIXELFORMAT_RGBA32) : nullptr;
    if(rgba) for(int y=0; y<sheet->h; ++y) {
      const auto* in=static_cast<const Uint8*>(sheet->pixels)+y*sheet->pitch;
      auto* out=static_cast<Uint8*>(rgba->pixels)+y*rgba->pitch;
      for(int x=0; x<sheet->w; ++x) { const Uint8 a=palette->colors[in[x]].r; out[x*4]=out[x*4+1]=out[x*4+2]=out[x*4+3]=a; }
    }
    SDL_DestroySurface(sheet);
    return upload(rgba);
  }
  struct LoadedFont { Font font; std::vector<SDL_Texture*> sheets; };
  std::unordered_map<std::string,LoadedFont> fonts;
  const LoadedFont* font(std::string_view name) {
    const std::string key(name);
    if(auto it=fonts.find(key); it!=fonts.end()) return it->second.sheets.empty() ? nullptr : &it->second;
    LoadedFont loaded;
    const std::string path=assetPath(key+".font");
    std::size_t size=0; void* data=path.empty() ? nullptr : SDL_LoadFile(path.c_str(),&size);
    if(data) { loaded.font=parseFont({static_cast<const char*>(data),size}); SDL_free(data); }
    const std::string folder=key.substr(0,key.find_last_of('/')+1);
    for(const auto& bake: loaded.font.bakes) {
      SDL_Texture* sheet=coverageSheet(assetPath(folder+bake.sheet));
      if(!sheet) { for(auto* t: loaded.sheets) SDL_DestroyTexture(t); loaded.sheets.clear(); break; }
      loaded.sheets.push_back(sheet);
    }
    auto& entry=fonts.emplace(key,std::move(loaded)).first->second;
    return entry.sheets.empty() ? nullptr : &entry;
  }
  bool label(std::string_view name,Vec2 p,std::string_view value,float size,Color c,Align align) override {
    const LoadedFont* f=font(name);
    if(!f) return false;
    const float ppu=viewport.scale()*pixelScale;
    const auto layout=f->font.layout(p,value,size,align,ppu);
    SDL_Texture* sheet=f->sheets[layout.bake];
    // Premultiplied: the tint's colour is scaled by its own alpha.
    const auto mod=[&](unsigned char v){ return static_cast<Uint8>(v*c.a/255); };
    SDL_SetTextureColorMod(sheet,mod(c.r),mod(c.g),mod(c.b)); SDL_SetTextureAlphaMod(sheet,c.a);
    for(const auto& q: layout.quads) {
      // Whole device pixels: at a bake's own size every texel lands on one pixel, sharp.
      SDL_FRect rect=device(q.destination); rect.x=std::round(rect.x); rect.y=std::round(rect.y);
      const SDL_FRect from{q.source.x,q.source.y,q.source.w,q.source.h};
      SDL_RenderTexture(handle,sheet,&from,&rect);
    }
    return true;
  }
};
class SDLAudio final: public Audio {
public:
  SDL_AudioStream* stream{};
  ~SDLAudio() override { if(stream) SDL_DestroyAudioStream(stream); }
  void initialize() {
    const SDL_AudioSpec spec{SDL_AUDIO_F32,1,48000};
    stream=SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,&spec,nullptr,nullptr);
    if(stream) SDL_ResumeAudioStreamDevice(stream);
  }
  void tone(float hz,float seconds) override {
    if(!stream || hz<=0 || seconds<=0) return;
    const int count=static_cast<int>(48000*std::min(seconds,1.0f)); std::vector<float> samples(count);
    for(int i=0;i<count;++i) samples[i]=0.15f*std::sin(6.2831853f*hz*i/48000)*(1-static_cast<float>(i)/count);
    SDL_PutAudioStreamData(stream,samples.data(),count*sizeof(float));
  }
};
class NoHaptics final: public Haptics {
public:
  void impact(float) override {}
  void humStart(float) override {}
  void humStop() override {}
  void thump() override {}
};
// Each name is a file in the app's preference folder; a write goes to a temporary file first and
// replaces the old one by rename, so a crash mid-write leaves the previous text.
class PreferenceStorage final: public Storage {
  std::string folder;
public:
  PreferenceStorage() {
    const char* appName=SDL_GetAppMetadataProperty(SDL_PROP_APP_METADATA_NAME_STRING);
    char* path=SDL_GetPrefPath("YYEngine",appName ? appName : "Game");
    if(path) { folder=path; SDL_free(path); }
  }
  std::string read(std::string_view name) override {
    if(folder.empty()) return {};
    std::ifstream in(folder+std::string(name),std::ios::binary);
    return {std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
  }
  bool write(std::string_view name, std::string_view text) override {
    if(folder.empty()) return false;
    const std::string path=folder+std::string(name), temporary=path+".tmp";
    {
      std::ofstream out(temporary,std::ios::binary|std::ios::trunc);
      out.write(text.data(),static_cast<std::streamsize>(text.size()));
      if(!out.flush()) return false;
    }
    return SDL_RenamePath(temporary.c_str(),path.c_str());
  }
};
#if defined(__APPLE__) && TARGET_OS_IOS
using PlatformHaptics=AppleHaptics;
#else
using PlatformHaptics=NoHaptics;
#endif
}
struct Runtime::Impl {
  std::unique_ptr<Game> game;
  SDL_Window* window{};
  SDLRenderer renderer;
  SDLAudio audio;
  std::unique_ptr<Haptics> haptics;
  std::unique_ptr<Storage> storage;
  PointerTracker pointers;
  FixedClock clock;
  std::uint64_t previous{}, frames{};
  double totalMs{}, worstMs{};
  int smokeFrames{};
  bool paused{}, initialized{};
  Impl(std::unique_ptr<Game> g,int smoke,std::string assets):game(std::move(g)),smokeFrames(smoke) {renderer.assetDirectory=std::move(assets);}
  void viewport() {
    int w=0,h=0,pw=0,ph=0; SDL_GetWindowSize(window,&w,&h); SDL_GetWindowSizeInPixels(window,&pw,&ph);
    SDL_Rect safe{0,0,w,h}; SDL_GetWindowSafeArea(window,&safe);
    renderer.viewport.safe={static_cast<float>(safe.x),static_cast<float>(safe.y),static_cast<float>(safe.w),static_cast<float>(safe.h)};
    renderer.pixelScale=w>0 ? static_cast<float>(pw)/w : 1;
  }
  void deliver(const std::optional<PointerEvent>& e) {
    if(!e) return;
    if(e->phase==PointerEvent::Phase::Down) { game->pointerDown(e->id,e->position); game->tap(e->position); }
    else if(e->phase==PointerEvent::Phase::Move) game->pointerMove(e->id,e->position);
    else game->pointerUp(e->id,e->position);
  }
  void pointer(const SDL_Event& event) {
    viewport(); const auto& v=renderer.viewport;
    switch(event.type) {
    case SDL_EVENT_FINGER_DOWN: case SDL_EVENT_FINGER_MOTION: case SDL_EVENT_FINGER_UP: case SDL_EVENT_FINGER_CANCELED: {
      int w=0,h=0; SDL_GetWindowSize(window,&w,&h);
      const Vec2 p{event.tfinger.x*w,event.tfinger.y*h}; const auto finger=static_cast<std::uint64_t>(event.tfinger.fingerID);
      if(event.type==SDL_EVENT_FINGER_DOWN) deliver(pointers.down(finger,p,v));
      else if(event.type==SDL_EVENT_FINGER_MOTION) deliver(pointers.move(finger,p,v));
      else deliver(pointers.up(finger,p,v));
      break;
    }
    case SDL_EVENT_MOUSE_BUTTON_DOWN: case SDL_EVENT_MOUSE_BUTTON_UP:
      if(event.button.button!=SDL_BUTTON_LEFT || event.button.which==SDL_TOUCH_MOUSEID) break;
      if(event.type==SDL_EVENT_MOUSE_BUTTON_DOWN) deliver(pointers.down(PointerTracker::mouse,{event.button.x,event.button.y},v));
      else deliver(pointers.up(PointerTracker::mouse,{event.button.x,event.button.y},v));
      break;
    case SDL_EVENT_MOUSE_MOTION:
      if(event.motion.which!=SDL_TOUCH_MOUSEID) deliver(pointers.move(PointerTracker::mouse,{event.motion.x,event.motion.y},v));
      break;
    case SDL_EVENT_MOUSE_WHEEL:
      if(event.wheel.which==SDL_TOUCH_MOUSEID) break;
      if(auto at=v.map({event.wheel.mouse_x,event.wheel.mouse_y}))
        game->zoom(*at,event.wheel.direction==SDL_MOUSEWHEEL_FLIPPED ? -event.wheel.y : event.wheel.y);
      break;
    default: break;
    }
  }
  void metrics() {
    if(!frames) return;
    const char* configured=std::getenv("YY_METRICS_PATH");
    const char* appName=SDL_GetAppMetadataProperty(SDL_PROP_APP_METADATA_NAME_STRING);
    char* preference=SDL_GetPrefPath("YYEngine",appName ? appName : "Game");
    const std::string path=configured ? configured : std::string(preference ? preference : "")+"metrics.json";
    SDL_free(preference);
    std::ofstream out(path);
    out<<"{\"frames\":"<<frames<<",\"mean_frame_ms\":"<<totalMs/frames<<",\"worst_frame_ms\":"<<worstMs
       <<",\"resident_memory_bytes\":"<<memoryBytes()<<",\"renderer\":\""<<SDL_GetRendererName(renderer.handle)<<"\"}\n";
    SDL_Log("frames=%llu mean=%.2fms worst=%.2fms memory=%llu bytes",static_cast<unsigned long long>(frames),totalMs/frames,worstMs,static_cast<unsigned long long>(memoryBytes()));
  }
};
Runtime::Runtime(std::unique_ptr<Game> game,int smoke,std::string assets):impl(std::make_unique<Impl>(std::move(game),smoke,std::move(assets))) {}
Runtime::~Runtime() {
  if(impl->initialized) { impl->metrics(); impl->game->shutdown(); }
  SDL_Window* window=impl->window;
  impl.reset(); // renderer and audio before their window and SDL
  if(window) SDL_DestroyWindow(window);
  SDL_Quit();
}
bool Runtime::initialize() {
  SDL_SetHint(SDL_HINT_MOUSE_TOUCH_EVENTS,"0"); SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS,"0");
  if(!SDL_Init(SDL_INIT_VIDEO|SDL_INIT_AUDIO)) { SDL_Log("SDL_Init: %s",SDL_GetError()); return false; }
  impl->window=SDL_CreateWindow(SDL_GetAppMetadataProperty(SDL_PROP_APP_METADATA_NAME_STRING),390,844,SDL_WINDOW_RESIZABLE|SDL_WINDOW_HIGH_PIXEL_DENSITY);
  if(!impl->window) return false;
#ifdef _WIN32
  const char* driver="direct3d11";
#elif defined(__APPLE__)
  const char* driver="metal";
#else
  const char* driver=nullptr;
#endif
  impl->renderer.handle=SDL_CreateRenderer(impl->window,driver);
  if(!impl->renderer.handle) { SDL_Log("Renderer: %s",SDL_GetError()); return false; }
  SDL_SetRenderVSync(impl->renderer.handle,1);
  SDL_SetRenderDrawBlendMode(impl->renderer.handle,SDL_BLENDMODE_BLEND); // colours with alpha below 255 blend; opaque draws are unchanged
  impl->audio.initialize(); impl->viewport(); impl->haptics=std::make_unique<PlatformHaptics>();
  impl->storage=std::make_unique<PreferenceStorage>();
  Services services{impl->renderer,impl->audio,*impl->haptics,*impl->storage}; impl->game->initialize(services);
  impl->previous=SDL_GetTicksNS(); impl->initialized=true; return true;
}
bool Runtime::event(const void* raw) {
  const auto& event=*static_cast<const SDL_Event*>(raw);
  if(event.type==SDL_EVENT_QUIT) return false;
  if(event.type==SDL_EVENT_WILL_ENTER_BACKGROUND || event.type==SDL_EVENT_WINDOW_MINIMIZED) {
    // Pause first so a game can tell these forced releases from a player letting go.
    impl->paused=true; impl->clock.reset(); impl->game->pause(true);
    for(const auto& ended: impl->pointers.cancel()) impl->deliver(ended);
    if(impl->haptics) impl->haptics->humStop();
    if(impl->audio.stream) SDL_PauseAudioStreamDevice(impl->audio.stream);
  }
  if(event.type==SDL_EVENT_DID_ENTER_FOREGROUND || event.type==SDL_EVENT_WINDOW_RESTORED) {
    impl->paused=false; impl->clock.reset(); impl->previous=SDL_GetTicksNS(); impl->game->pause(false);
    if(impl->audio.stream) SDL_ResumeAudioStreamDevice(impl->audio.stream);
  }
  if(!impl->paused) impl->pointer(event);
  return true;
}
bool Runtime::iterate() {
  const auto now=SDL_GetTicksNS(); const double elapsed=static_cast<double>(now-impl->previous)/1e9; impl->previous=now;
  if(impl->paused) { SDL_Delay(20); return true; }
  impl->clock.advance(elapsed,[&](float step){ impl->game->update(step); }); impl->viewport();
  impl->renderer.color({9,20,33}); SDL_RenderClear(impl->renderer.handle); impl->game->render(impl->renderer);
  ++impl->frames; const double ms=elapsed*1000; impl->totalMs+=ms; impl->worstMs=std::max(impl->worstMs,ms);
  const bool done=impl->smokeFrames>0 && impl->frames>=static_cast<std::uint64_t>(impl->smokeFrames);
  if(done) {
    if(const char* path=std::getenv("YY_SCREENSHOT_PATH")) {
      SDL_Surface* surface=SDL_RenderReadPixels(impl->renderer.handle,nullptr);
      if(surface) { SDL_SaveBMP(surface,path); SDL_DestroySurface(surface); }
    }
  }
  SDL_RenderPresent(impl->renderer.handle); return !done;
}
}
