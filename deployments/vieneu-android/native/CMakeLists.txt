cmake_minimum_required(VERSION 3.22.1)
project(vieneu_android_jni LANGUAGES C CXX)

if(NOT VIENEU_SOURCE_DIR)
    message(FATAL_ERROR "VIENEU_SOURCE_DIR must point to a VieNeu-TTS.cpp checkout")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(VIENEU_PORTABLE_CPU ON CACHE BOOL "" FORCE)
set(VIENEU_NATIVE_BACKEND cpu CACHE STRING "" FORCE)
set(VIENEU_SEA_G2P OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)

add_subdirectory("${VIENEU_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/vieneu-src-build")

add_library(vieneu_jni SHARED vieneu_jni.cpp)
target_include_directories(vieneu_jni PRIVATE "${VIENEU_SOURCE_DIR}/src")

find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(vieneu_jni PRIVATE vieneu-tts ${log-lib} ${android-lib})
