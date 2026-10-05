#pragma once
// iOS haptics, included by runtime.cpp. The build compiles the runtime as C++ and links no
// haptics framework, so Core Haptics is loaded at run time and both it and UIKit's impact
// generators are driven through the Objective-C runtime. Anything missing leaves the call silent.
#include <yy/runtime.hpp>
#include <dlfcn.h>
#include <objc/message.h>
#include <objc/runtime.h>

namespace yy {
namespace {
template<class R=id,class... A> R send(id target,const char* selector,A... args) {
  if(!target) return R();
  return reinterpret_cast<R(*)(id,SEL,A...)>(objc_msgSend)(target,sel_registerName(selector),args...);
}
id objcClass(const char* name) { return reinterpret_cast<id>(objc_getClass(name)); }
id make(const char* name) { return send(objcClass(name),"alloc"); }
void release(id& object) { send<void>(object,"release"); object=nullptr; }
id array(id* objects,unsigned long count) { return send(objcClass("NSArray"),"arrayWithObjects:count:",objects,count); }
id constant(void* framework,const char* name) {
  auto* address=framework ? static_cast<id*>(dlsym(framework,name)) : nullptr;
  return address ? *address : nullptr;
}
// Scopes the autoreleased objects a call creates, wherever the caller's loop drains.
struct Pool {
  id pool=send(make("NSAutoreleasePool"),"init");
  ~Pool() { send<void>(pool,"drain"); }
};

class AppleHaptics final: public Haptics {
  id engine{}, hum{}, tap{}, heavy{}; // retained: CHHapticEngine, its looping player, UIImpactFeedbackGenerators
  id continuous{}, intensityId{}, sharpnessId{}, intensityControl{};
  bool start(id player) {
    id error=nullptr;
    if(send<BOOL>(player,"startAtTime:error:",0.0,&error)) return true;
    // The system stops the engine in the background or after a reset; restart it once.
    return send<BOOL>(engine,"startAndReturnError:",&error) && send<BOOL>(player,"startAtTime:error:",0.0,&error);
  }
  void intensity(float value) {
    id parameter=send(make("CHHapticDynamicParameter"),"initWithParameterID:value:relativeTime:",intensityControl,value,0.0);
    id error=nullptr;
    if(parameter) send<BOOL>(hum,"sendParameters:atTime:error:",array(&parameter,1),0.0,&error);
    release(parameter);
  }
public:
  AppleHaptics() {
    Pool pool;
    tap=send(make("UIImpactFeedbackGenerator"),"initWithStyle:",1L);   // medium
    heavy=send(make("UIImpactFeedbackGenerator"),"initWithStyle:",2L); // heavy
    send<void>(tap,"prepare"); send<void>(heavy,"prepare");
    void* framework=dlopen("/System/Library/Frameworks/CoreHaptics.framework/CoreHaptics",RTLD_LAZY);
    continuous=constant(framework,"CHHapticEventTypeHapticContinuous");
    intensityId=constant(framework,"CHHapticEventParameterIDHapticIntensity");
    sharpnessId=constant(framework,"CHHapticEventParameterIDHapticSharpness");
    intensityControl=constant(framework,"CHHapticDynamicParameterIDHapticIntensityControl");
    id capabilities=send(objcClass("CHHapticEngine"),"capabilitiesForHardware");
    if(!continuous || !intensityId || !sharpnessId || !intensityControl || !send<BOOL>(capabilities,"supportsHaptics")) return;
    id error=nullptr;
    engine=send(make("CHHapticEngine"),"initAndReturnError:",&error);
    send<void>(engine,"setPlaysHapticsOnly:",static_cast<BOOL>(true));
    if(!send<BOOL>(engine,"startAndReturnError:",&error)) release(engine);
  }
  ~AppleHaptics() override {
    humStop();
    send<void>(engine,"stopWithCompletionHandler:",static_cast<void*>(nullptr));
    release(engine); release(tap); release(heavy);
  }
  void impact(float strength) override {
    if(!(strength>0)) return;
    send<void>(tap,"impactOccurredWithIntensity:",static_cast<double>(std::min(strength,1.0f)));
    send<void>(tap,"prepare");
  }
  void thump() override { send<void>(heavy,"impactOccurredWithIntensity:",1.0); send<void>(heavy,"prepare"); }
  void humStart(float value) override {
    if(!engine) return;
    value=std::clamp(value,0.0f,1.0f);
    if(hum) { intensity(value); return; }
    Pool pool;
    id parameters[]{
      send(make("CHHapticEventParameter"),"initWithParameterID:value:",intensityId,1.0f),
      send(make("CHHapticEventParameter"),"initWithParameterID:value:",sharpnessId,0.2f)};
    id event=parameters[0] && parameters[1]
      ? send(make("CHHapticEvent"),"initWithEventType:parameters:relativeTime:duration:",continuous,array(parameters,2),0.0,30.0) : nullptr;
    id error=nullptr, pattern=event ? send(make("CHHapticPattern"),"initWithEvents:parameters:error:",array(&event,1),array(nullptr,0),&error) : nullptr;
    hum=send(send(engine,"createAdvancedPlayerWithPattern:error:",pattern,&error),"retain");
    send<void>(hum,"setLoopEnabled:",static_cast<BOOL>(true));
    if(hum && start(hum)) intensity(value); else release(hum);
    release(parameters[0]); release(parameters[1]); release(event); release(pattern);
  }
  void humStop() override {
    if(!hum) return;
    id error=nullptr; send<BOOL>(hum,"stopAtTime:error:",0.0,&error); release(hum);
  }
};
}
}
