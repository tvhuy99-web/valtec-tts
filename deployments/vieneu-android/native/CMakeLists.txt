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

# Keep ORT's CPU arena enabled during inference for speed, but request shrinkage
# after the speaker-encoder and MOSS codec runs so large mobile workspaces are
# not retained for the lifetime of the process.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_memory.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_MEMORY_OPT_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_MEMORY_OPT_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_MEMORY_OPT_STDERR
)
if(NOT VIENEU_ANDROID_MEMORY_OPT_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android memory patch failed:\n${VIENEU_ANDROID_MEMORY_OPT_STDOUT}\n${VIENEU_ANDROID_MEMORY_OPT_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_MEMORY_OPT_STDOUT}")

# Keep the quality-preserving CPU weights while the Vulkan PoC is being brought
# up. This first gate verifies that the Android APK can package and load the
# ggml Vulkan backend before acoustic tensors are migrated to the GPU.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_acoustic_f16.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_ACOUSTIC_F16_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_ACOUSTIC_F16_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_ACOUSTIC_F16_STDERR
)
if(NOT VIENEU_ANDROID_ACOUSTIC_F16_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android acoustic FP16 patch failed:\n${VIENEU_ANDROID_ACOUSTIC_F16_STDOUT}\n${VIENEU_ANDROID_ACOUSTIC_F16_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_ACOUSTIC_F16_STDOUT}")

execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_short_text.py" "${CMAKE_CURRENT_LIST_DIR}/vieneu_jni.cpp"
    RESULT_VARIABLE VIENEU_ANDROID_SHORT_TEXT_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_SHORT_TEXT_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_SHORT_TEXT_STDERR
)
if(NOT VIENEU_ANDROID_SHORT_TEXT_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android short-text patch failed:\n${VIENEU_ANDROID_SHORT_TEXT_STDOUT}\n${VIENEU_ANDROID_SHORT_TEXT_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_SHORT_TEXT_STDOUT}")

set(VIENEU_PORTABLE_CPU ON CACHE BOOL "" FORCE)
set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "" FORCE)
set(VIENEU_NATIVE_BACKEND vulkan CACHE STRING "" FORCE)
set(GGML_VULKAN ON CACHE BOOL "" FORCE)
set(GGML_VULKAN_DEBUG OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN_VALIDATE OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN_CHECK_RESULTS OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN_RUN_TESTS OFF CACHE BOOL "" FORCE)
set(VIENEU_SEA_G2P OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)

message(STATUS "VieNeu Android Vulkan PoC: compiling ggml-vulkan for arm64-v8a")
add_subdirectory("${VIENEU_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/vieneu-src-build")

add_library(vieneu_jni SHARED vieneu_jni.cpp)
target_include_directories(vieneu_jni PRIVATE
    "${VIENEU_SOURCE_DIR}/src"
    "${ONNXRUNTIME_ROOT}/include"
)

find_library(log-lib log)
find_library(android-lib android)
find_library(vulkan-lib vulkan)
if(NOT vulkan-lib)
    message(FATAL_ERROR "Android system Vulkan loader was not found")
endif()

target_link_libraries(vieneu_jni PRIVATE vieneu-tts ${log-lib} ${android-lib} ${vulkan-lib})
