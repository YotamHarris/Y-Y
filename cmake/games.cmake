file(READ "${CMAKE_SOURCE_DIR}/config/games.json" YY_GAMES_JSON)
string(JSON YY_GAME_COUNT LENGTH "${YY_GAMES_JSON}")
math(EXPR YY_GAME_LAST "${YY_GAME_COUNT} - 1")
set(YY_GAME_FOUND FALSE)
foreach(index RANGE ${YY_GAME_LAST})
  string(JSON id MEMBER "${YY_GAMES_JSON}" ${index})
  if(YY_GAME AND NOT YY_GAME STREQUAL id)
    continue()
  endif()
  set(YY_GAME_FOUND TRUE)
  foreach(field directory target bundleId version)
    string(JSON ${field} GET "${YY_GAMES_JSON}" "${id}" "${field}")
  endforeach()
  if(YY_GAME AND YY_BUNDLE_ID)
    set(bundleId "${YY_BUNDLE_ID}")
  endif()
  if(YY_GAME AND YY_APP_VERSION)
    set(version "${YY_APP_VERSION}")
  endif()
  file(GLOB sources "${CMAKE_SOURCE_DIR}/${directory}/src/*.cpp")
  add_executable(${target} MACOSX_BUNDLE ${sources})
  target_include_directories(${target} PRIVATE "${directory}/include")
  target_link_libraries(${target} PRIVATE yy_runtime)
  target_compile_definitions(${target} PRIVATE YY_ASSET_DIRECTORY="assets/${id}/" YY_BUNDLE_IDENTIFIER="${bundleId}" YY_GAME_TITLE="${target}" YY_GAME_VERSION="${version}")
  set_target_properties(${target} PROPERTIES
    MACOSX_BUNDLE_GUI_IDENTIFIER "${bundleId}"
    MACOSX_BUNDLE_BUNDLE_NAME "${target}"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${version}"
    MACOSX_BUNDLE_BUNDLE_VERSION "${YY_BUILD_NUMBER}"
    MACOSX_BUNDLE_INFO_PLIST "${CMAKE_SOURCE_DIR}/platform/ios/Info.plist.in"
    XCODE_GENERATE_SCHEME TRUE)
  if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set_target_properties(${target} PROPERTIES
      # CMake's default SKIP_INSTALL leaves apps outside the .xcarchive.
      XCODE_ATTRIBUTE_SKIP_INSTALL "NO"
      XCODE_ATTRIBUTE_INSTALL_PATH "$(LOCAL_APPS_DIR)"
      XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
      XCODE_ATTRIBUTE_CODE_SIGN_STYLE "Manual"
      XCODE_ATTRIBUTE_DEVELOPMENT_TEAM "${YY_APPLE_TEAM_ID}"
      XCODE_ATTRIBUTE_PROVISIONING_PROFILE_SPECIFIER "${YY_PROFILE_NAME}"
      XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY "Apple Distribution"
      XCODE_ATTRIBUTE_DEBUG_INFORMATION_FORMAT "dwarf-with-dsym"
      XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS "YES"
      XCODE_ATTRIBUTE_ASSETCATALOG_COMPILER_APPICON_NAME "AppIcon")
    target_sources(${target} PRIVATE "${CMAKE_SOURCE_DIR}/platform/ios/Assets.xcassets")
    set_source_files_properties("${CMAKE_SOURCE_DIR}/platform/ios/Assets.xcassets" PROPERTIES MACOSX_PACKAGE_LOCATION Resources)
  endif()
  if(EMSCRIPTEN)
    # One folder holds the page and everything it loads: index.html, .js, .wasm and the packaged assets.
    set_target_properties(${target} PROPERTIES OUTPUT_NAME index SUFFIX ".html" RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/site")
    target_link_options(${target} PRIVATE
      --shell-file "${CMAKE_SOURCE_DIR}/cmake/web/shell.html" --pre-js "${CMAKE_SOURCE_DIR}/cmake/web/idbfs.js"
      -lidbfs.js -sFORCE_FILESYSTEM=1 -sALLOW_MEMORY_GROWTH=1 -sEXPORTED_RUNTIME_METHODS=ccall,FS)
    set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS
      "${CMAKE_SOURCE_DIR}/cmake/web/shell.html" "${CMAKE_SOURCE_DIR}/cmake/web/idbfs.js")
  endif()
  if(EXISTS "${CMAKE_SOURCE_DIR}/${directory}/assets" AND EMSCRIPTEN)
    # Packaged where the runtime looks: SDL_GetBasePath is "/" in the browser.
    file(GLOB_RECURSE assets "${CMAKE_SOURCE_DIR}/${directory}/assets/*")
    target_link_options(${target} PRIVATE --preload-file "${CMAKE_SOURCE_DIR}/${directory}/assets@/assets/${id}")
    set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS ${assets})
  elseif(EXISTS "${CMAKE_SOURCE_DIR}/${directory}/assets")
    file(GLOB_RECURSE assets "${CMAKE_SOURCE_DIR}/${directory}/assets/*")
    if(APPLE)
      target_sources(${target} PRIVATE ${assets})
      foreach(asset IN LISTS assets)
        file(RELATIVE_PATH relative "${CMAKE_SOURCE_DIR}/${directory}/assets" "${asset}")
        get_filename_component(folder "${relative}" DIRECTORY)
        set_source_files_properties("${asset}" PROPERTIES MACOSX_PACKAGE_LOCATION "Resources/assets/${id}/${folder}")
      endforeach()
    else()
      add_custom_command(TARGET ${target} POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_SOURCE_DIR}/${directory}/assets" "$<TARGET_FILE_DIR:${target}>/assets/${id}" VERBATIM)
    endif()
  endif()
endforeach()
if(YY_GAME AND NOT YY_GAME_FOUND)
  message(FATAL_ERROR "Unknown selected game: ${YY_GAME}")
endif()
