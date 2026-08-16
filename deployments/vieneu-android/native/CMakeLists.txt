cmake_minimum_required(VERSION 3.22.1)
project(vieneu_android_jni LANGUAGES C CXX)

if(NOT VIENEU_SOURCE_DIR)
    message(FATAL_ERROR "VIENEU_SOURCE_DIR must point to a VieNeu-TTS.cpp checkout")
endif()

if(NOT ONNXRUNTIME_ROOT)
    message(FATAL_ERROR "ONNXRUNTIME_ROOT must point to the Android ONNX Runtime C++ SDK")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Apply Android-only hot-path patches to the pinned upstream checkout after
# diagnostics instrumentation and before VieNeu targets are configured.
find_package(Python3 COMPONENTS Interpreter REQUIRED)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_OPT_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_OPT_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_OPT_STDERR
)
if(NOT VIENEU_ANDROID_OPT_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android optimization patch failed:\n${VIENEU_ANDROID_OPT_STDOUT}\n${VIENEU_ANDROID_OPT_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_OPT_STDOUT}")

# Keep a known Android ABI baseline while enabling the ARMv8.2 dot-product and
# FP16 vector instructions that are available on the profiled SM7675 device.
# This avoids cross-compiling with -mcpu=native while allowing GGML's Q8 path to
# use materially better ARM kernels than the plain armv8-a portable baseline.
set(VIENEU_PORTABLE_CPU ON CACHE BOOL "" FORCE)
set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "" FORCE)
set(VIENEU_NATIVE_BACKEND cpu CACHE STRING "" FORCE)
set(VIENEU_SEA_G2P OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)

add_subdirectory("${VIENEU_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/vieneu-src-build")

add_library(vieneu_jni SHARED vieneu_jni.cpp)
target_include_directories(vieneu_jni PRIVATE
    "${VIENEU_SOURCE_DIR}/src"
    "${ONNXRUNTIME_ROOT}/include"
)

find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(vieneu_jni PRIVATE vieneu-tts ${log-lib} ${android-lib})
