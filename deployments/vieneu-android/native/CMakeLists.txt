cmake_minimum_required(VERSION 3.22.1)
project(vieneu_android_jni LANGUAGES C CXX)

if(NOT VIENEU_SOURCE_DIR)
    message(FATAL_ERROR "VIENEU_SOURCE_DIR must point to a VieNeu-TTS.cpp checkout")
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

# Convert acoustic QKV/O-projection weights to FP16 before replacing the CPU
# graph wrappers with OpenCL-resident buffers.
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

# The upstream acoustic code accepts a backend but discards it. Replace the
# projection, fused-FFN and sampling-head graphs with OpenCL-resident buffers.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_acoustic_opencl.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_ACOUSTIC_OPENCL_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_ACOUSTIC_OPENCL_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_ACOUSTIC_OPENCL_STDERR
)
if(NOT VIENEU_ANDROID_ACOUSTIC_OPENCL_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android acoustic OpenCL patch failed:\n${VIENEU_ANDROID_ACOUSTIC_OPENCL_STDOUT}\n${VIENEU_ANDROID_ACOUSTIC_OPENCL_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_ACOUSTIC_OPENCL_STDOUT}")

# Replace the acoustic FFN Q8 weights with FP16. Q8 errors compound across
# autoregressive frames and caused longer Vietnamese phrases to become gibberish.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_acoustic_quality.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_ACOUSTIC_QUALITY_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_ACOUSTIC_QUALITY_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_ACOUSTIC_QUALITY_STDERR
)
if(NOT VIENEU_ANDROID_ACOUSTIC_QUALITY_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android acoustic quality patch failed:\n${VIENEU_ANDROID_ACOUSTIC_QUALITY_STDOUT}\n${VIENEU_ANDROID_ACOUSTIC_QUALITY_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_ACOUSTIC_QUALITY_STDOUT}")

# Reset the random sampler from a stable prompt hash for each utterance so the
# same sentence no longer produces unrelated lengths based on prior requests.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_generation_quality.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_GENERATION_QUALITY_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_GENERATION_QUALITY_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_GENERATION_QUALITY_STDERR
)
if(NOT VIENEU_ANDROID_GENERATION_QUALITY_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android generation quality patch failed:\n${VIENEU_ANDROID_GENERATION_QUALITY_STDOUT}\n${VIENEU_ANDROID_GENERATION_QUALITY_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_GENERATION_QUALITY_STDOUT}")

execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_short_text.py" "${CMAKE_CURRENT_LIST_DIR}/vieneu_jni.cpp"
    RESULT_VARIABLE VIENEU_ANDROID_SHORT_TEXT_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_SHORT_TEXT_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_SHORT_TEXT_STDERR
)
if(NOT VIENEU_ANDROID_SHORT_TEXT_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android generation policy patch failed:\n${VIENEU_ANDROID_SHORT_TEXT_STDOUT}\n${VIENEU_ANDROID_SHORT_TEXT_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_SHORT_TEXT_STDOUT}")

execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/optimize_vieneu_android_opencl_runtime.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_OPENCL_RUNTIME_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_OPENCL_RUNTIME_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_OPENCL_RUNTIME_STDERR
)
if(NOT VIENEU_ANDROID_OPENCL_RUNTIME_RESULT EQUAL 0)
    message(FATAL_ERROR "VieNeu Android OpenCL runtime patch failed:\n${VIENEU_ANDROID_OPENCL_RUNTIME_STDOUT}\n${VIENEU_ANDROID_OPENCL_RUNTIME_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_OPENCL_RUNTIME_STDOUT}")

# Fix every embedded kernel for Qualcomm drivers that expose only the
# cl_qcom_subgroup_shuffle builtin names.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/patch_llama_opencl_qcom_shuffle.py" "${VIENEU_SOURCE_DIR}"
    RESULT_VARIABLE VIENEU_ANDROID_QCOM_SHUFFLE_RESULT
    OUTPUT_VARIABLE VIENEU_ANDROID_QCOM_SHUFFLE_STDOUT
    ERROR_VARIABLE VIENEU_ANDROID_QCOM_SHUFFLE_STDERR
)
if(NOT VIENEU_ANDROID_QCOM_SHUFFLE_RESULT EQUAL 0)
    message(FATAL_ERROR "Qualcomm OpenCL shuffle patch failed:\n${VIENEU_ANDROID_QCOM_SHUFFLE_STDOUT}\n${VIENEU_ANDROID_QCOM_SHUFFLE_STDERR}")
endif()
message(STATUS "${VIENEU_ANDROID_QCOM_SHUFFLE_STDOUT}")

# VieNeu upstream does not expose 'opencl' as a VIENEU_NATIVE_BACKEND selector.
# Keep its portable CPU backend compiled only for small scalar/scheduler glue,
# while all dominant semantic and acoustic matrix graphs use OpenCL. There is
# no automatic whole-model or acoustic-hot-path CPU fallback.
set(VIENEU_PORTABLE_CPU ON CACHE BOOL "" FORCE)
set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "" FORCE)
set(VIENEU_NATIVE_BACKEND cpu CACHE STRING "" FORCE)
set(GGML_OPENCL ON CACHE BOOL "" FORCE)
set(GGML_OPENCL_EMBED_KERNELS ON CACHE BOOL "" FORCE)
set(GGML_OPENCL_USE_ADRENO_KERNELS ON CACHE BOOL "" FORCE)
set(GGML_OPENCL_PROFILING OFF CACHE BOOL "" FORCE)
# sea-g2p is cross-compiled explicitly by CI and linked below. Leave the
# upstream host-Cargo build disabled because it cannot target Android correctly.
set(VIENEU_SEA_G2P OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)

message(STATUS "VieNeu Android OpenCL: semantic backbone and FP16 acoustic hot paths on Adreno")
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
    # Rust's Android cdylib has no DT_SONAME by default. Tell CMake to link it
    # with -lsea_g2p_rs instead of embedding the absolute CI workspace path in
    # DT_NEEDED, which Android's linker namespace cannot resolve.
    IMPORTED_NO_SONAME TRUE
    INTERFACE_INCLUDE_DIRECTORIES "${SEA_G2P_INCLUDE_DIR}"
)

# Runtime patches directly call the OpenCL backend API and native sea-g2p.
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

add_library(vieneu_jni SHARED vieneu_jni.cpp)
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
