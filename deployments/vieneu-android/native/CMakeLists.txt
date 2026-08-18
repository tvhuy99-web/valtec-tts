cmake_minimum_required(VERSION 3.22.1)
project(vieneu_android_jni LANGUAGES C CXX)

if(NOT VIENEU_SOURCE_DIR)
    message(FATAL_ERROR "VIENEU_SOURCE_DIR must point to a prepared VieNeu-TTS.cpp checkout")
endif()

if(NOT EXISTS "${VIENEU_SOURCE_DIR}/.vieneu-android-prepared.json")
    message(FATAL_ERROR "VieNeu Android source was not materialized before CMake configure")
endif()

if(NOT ONNXRUNTIME_ROOT)
    message(FATAL_ERROR "ONNXRUNTIME_ROOT must point to the Android ONNX Runtime C++ SDK")
endif()

if(NOT OpenCL_INCLUDE_DIR OR NOT EXISTS "${OpenCL_INCLUDE_DIR}/CL/cl.h")
    message(FATAL_ERROR "OpenCL headers are required for the Android Adreno build")
endif()
if(NOT OpenCL_LIBRARY OR NOT EXISTS "${OpenCL_LIBRARY}")
    message(FATAL_ERROR "OpenCL link library is required for the Android Adreno build")
endif()
if(NOT SEA_G2P_INCLUDE_DIR OR NOT EXISTS "${SEA_G2P_INCLUDE_DIR}/sea_g2p.h")
    message(FATAL_ERROR "sea-g2p C ABI headers are required for Vietnamese pronunciation")
endif()
if(NOT SEA_G2P_LIBRARY OR NOT EXISTS "${SEA_G2P_LIBRARY}")
    message(FATAL_ERROR "Android sea-g2p shared library is required")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python3 COMPONENTS Interpreter REQUIRED)

set(VIENEU_PORTABLE_CPU ON CACHE BOOL "" FORCE)
set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "" FORCE)
set(VIENEU_NATIVE_BACKEND cpu CACHE STRING "" FORCE)
set(GGML_OPENCL ON CACHE BOOL "" FORCE)
set(GGML_OPENCL_EMBED_KERNELS ON CACHE BOOL "" FORCE)
set(GGML_OPENCL_USE_ADRENO_KERNELS ON CACHE BOOL "" FORCE)
set(GGML_OPENCL_PROFILING OFF CACHE BOOL "" FORCE)
set(VIENEU_SEA_G2P OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)

message(STATUS "VieNeu Android OpenCL: prepared semantic backbone and full-F32 acoustic hot paths on Adreno")
message(STATUS "OpenCL headers: ${OpenCL_INCLUDE_DIR}")
message(STATUS "OpenCL link library: ${OpenCL_LIBRARY}")
message(STATUS "sea-g2p Android library: ${SEA_G2P_LIBRARY}")
add_subdirectory("${VIENEU_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/vieneu-src-build")

if(NOT TARGET ggml-opencl)
    message(FATAL_ERROR "GGML OpenCL target was not created")
endif()

add_library(vieneu-sea-g2p-android SHARED IMPORTED GLOBAL)
set_target_properties(vieneu-sea-g2p-android PROPERTIES
    IMPORTED_LOCATION "${SEA_G2P_LIBRARY}"
    IMPORTED_NO_SONAME TRUE
    INTERFACE_INCLUDE_DIRECTORIES "${SEA_G2P_INCLUDE_DIR}"
)

target_include_directories(vieneu-tts-core PRIVATE
    "${VIENEU_SOURCE_DIR}/third_party/llama.cpp/ggml/include"
    "${SEA_G2P_INCLUDE_DIR}"
)
target_link_libraries(vieneu-tts-core PUBLIC ggml-opencl vieneu-sea-g2p-android)
target_compile_definitions(vieneu-tts-core PRIVATE
    VIENEU_ANDROID_OPENCL_POC=1
    VIENEU_ANDROID_ACOUSTIC_OPENCL=1
    VIENEU_USE_SEA_G2P=1
)

add_library(vieneu_jni SHARED vieneu_jni.cpp vieneu_cancel_jni.cpp)
target_include_directories(vieneu_jni PRIVATE
    "${VIENEU_SOURCE_DIR}/src"
    "${ONNXRUNTIME_ROOT}/include"
)

find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(vieneu_jni PRIVATE vieneu-tts ggml-opencl ${log-lib} ${android-lib})

if(NOT CMAKE_READELF)
    find_program(CMAKE_READELF NAMES llvm-readelf readelf REQUIRED)
endif()
add_custom_command(TARGET vieneu_jni POST_BUILD
    COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_LIST_DIR}/verify_android_elf_needed.py"
            "${CMAKE_READELF}"
            "$<TARGET_FILE:vieneu-tts>"
            "$<TARGET_FILE:vieneu_jni>"
    COMMENT "Verifying portable Android DT_NEEDED library names"
    VERBATIM
)
