#!/usr/bin/env python3
'''Register the pinned GGML Vulkan backend on Android and offload the semantic backbone.

This PoC intentionally leaves the acoustic generator unchanged. It proves the
real device/driver path first, logs the detected GPU and memory, and requests
all llama.cpp backbone layers on Vulkan only when a Vulkan device is present.
Devices without a working Vulkan backend keep the CPU fallback.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_vulkan_runtime.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
path = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)


replace_once(
    '''#include "vieneu_v3_native.h"
#include "vieneu/vieneu.h"
''',
    '''#include "vieneu_v3_native.h"
#include "vieneu/vieneu.h"
#include "ggml-backend.h"
#include "ggml-vulkan.h"
''',
    'GGML Vulkan includes',
)

replace_once(
    '''bool contains_v3_emotion_token(const std::string& text) {
''',
    '''int register_and_probe_android_vulkan() {
    // Explicit registration is required in the Android packaged-library setup;
    // relying on desktop-style dynamic backend discovery is not portable here.
    if (!ggml_backend_reg_by_name(GGML_VK_NAME)) {
        ggml_backend_register(ggml_backend_vk_reg());
    }

    const int count = ggml_backend_vk_get_device_count();
    std::cerr << "[V3NativeDiag] stage=vulkan.probe device_count=" << count << "\\n";
    for (int i = 0; i < count; ++i) {
        char description[256] = {};
        size_t free_bytes = 0;
        size_t total_bytes = 0;
        ggml_backend_vk_get_device_description(i, description, sizeof(description));
        ggml_backend_vk_get_device_memory(i, &free_bytes, &total_bytes);
        std::cerr << "[V3NativeDiag] stage=vulkan.device"
                  << " index=" << i
                  << " description=\"" << description << "\""
                  << " free_bytes=" << free_bytes
                  << " total_bytes=" << total_bytes << "\\n";
    }
    return count;
}

bool contains_v3_emotion_token(const std::string& text) {
''',
    'Vulkan registration and diagnostics helper',
)

replace_once(
    '''        model_dir_ = init.model_dir;
        if (!assets_.load(init.model_dir, error)) return false;
''',
    '''        model_dir_ = init.model_dir;
        const int vulkan_device_count = register_and_probe_android_vulkan();
        if (!assets_.load(init.model_dir, error)) return false;
''',
    'probe Vulkan before model initialization',
)

replace_once(
    '''        backbone_params.n_threads = init.n_threads;
        backbone_params.n_threads_batch = init.n_threads;
        if (!backbone_.initialize(backbone_params)) {
''',
    '''        backbone_params.n_threads = init.n_threads;
        backbone_params.n_threads_batch = init.n_threads;
        // Request all semantic-backbone layers on the first Vulkan GPU. llama.cpp
        // will keep unsupported operations on CPU. Preserve a complete CPU
        // fallback when the device exposes no usable Vulkan backend.
        backbone_params.n_gpu_layers = vulkan_device_count > 0 ? 999 : 0;
        std::cerr << "[V3NativeDiag] stage=vulkan.backbone_offload"
                  << " enabled=" << (backbone_params.n_gpu_layers > 0 ? 1 : 0)
                  << " requested_layers=" << backbone_params.n_gpu_layers << "\\n";
        if (!backbone_.initialize(backbone_params)) {
''',
    'enable Vulkan backbone offload',
)

path.write_text(text, encoding='utf-8')
print('Applied Android Vulkan runtime probe and semantic-backbone offload')
