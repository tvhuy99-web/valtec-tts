#!/usr/bin/env python3
'''Register the pinned GGML OpenCL backend on Android and require full backbone offload.

This experiment deliberately has no whole-model CPU fallback. If an OpenCL device
cannot be exposed to the app, VieNeu initialization fails with a diagnostic error
instead of silently running the semantic backbone on CPU.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_opencl_runtime.py <vieneu-source-dir>')

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
    '''#include "vieneu_v3_native.h"\n#include "vieneu/vieneu.h"\n''',
    '''#include "vieneu_v3_native.h"\n#include "vieneu/vieneu.h"\n#include "ggml-backend.h"\n#include "ggml-opencl.h"\n''',
    'GGML OpenCL includes',
)

replace_once(
    '''bool contains_v3_emotion_token(const std::string& text) {\n''',
    '''int register_and_probe_android_opencl() {\n    ggml_backend_reg_t reg = ggml_backend_opencl_reg();\n    if (!reg) {\n        std::cerr << "[V3NativeDiag] stage=opencl.probe reg=null\\n";\n        return 0;\n    }\n\n    const char* reg_name = ggml_backend_reg_name(reg);\n    ggml_backend_reg_t active = reg_name ? ggml_backend_reg_by_name(reg_name) : nullptr;\n    if (!active) {\n        ggml_backend_register(reg);\n        active = reg;\n    }\n\n    const size_t count = ggml_backend_reg_dev_count(active);\n    std::cerr << "[V3NativeDiag] stage=opencl.probe"\n              << " backend=\\\"" << (reg_name ? reg_name : "unknown") << "\\\""\n              << " device_count=" << count << "\\n";\n\n    for (size_t i = 0; i < count; ++i) {\n        ggml_backend_dev_t dev = ggml_backend_reg_dev_get(active, i);\n        size_t free_bytes = 0;\n        size_t total_bytes = 0;\n        ggml_backend_dev_memory(dev, &free_bytes, &total_bytes);\n        std::cerr << "[V3NativeDiag] stage=opencl.device"\n                  << " index=" << i\n                  << " name=\\\"" << (ggml_backend_dev_name(dev) ? ggml_backend_dev_name(dev) : "") << "\\\""\n                  << " description=\\\"" << (ggml_backend_dev_description(dev) ? ggml_backend_dev_description(dev) : "") << "\\\""\n                  << " type=" << static_cast<int>(ggml_backend_dev_type(dev))\n                  << " free_bytes=" << free_bytes\n                  << " total_bytes=" << total_bytes << "\\n";\n    }\n    return static_cast<int>(count);\n}\n\nbool contains_v3_emotion_token(const std::string& text) {\n''',
    'OpenCL registration and diagnostics helper',
)




replace_once(
    '''        model_dir_ = init.model_dir;\n        auto t_stage = std::chrono::high_resolution_clock::now();\n        if (!assets_.load(init.model_dir, error)) return false;\n''',
    '''        model_dir_ = init.model_dir;\n        const int opencl_device_count = register_and_probe_android_opencl();\n        if (opencl_device_count <= 0) {\n            error = "OpenCL GPU backend unavailable; CPU fallback is disabled for this build";\n            return false;\n        }\n        auto t_stage = std::chrono::high_resolution_clock::now();\n        if (!assets_.load(init.model_dir, error)) return false;\n''',
    'require OpenCL before model initialization',
)

replace_once(
    '''        backbone_params.n_threads = init.n_threads;\n        backbone_params.n_threads_batch = init.n_threads;\n        t_stage = std::chrono::high_resolution_clock::now();\n        if (!backbone_.initialize(backbone_params)) {\n''',
    '''        backbone_params.n_threads = init.n_threads;\n        backbone_params.n_threads_batch = init.n_threads;\n        // Request every semantic-backbone layer on the OpenCL GPU. There is no\n        // n_gpu_layers=0 fallback in this experiment: absence of OpenCL already\n        // failed initialization above.\n        backbone_params.n_gpu_layers = 999;\n        std::cerr << "[V3NativeDiag] stage=opencl.backbone_offload"\n                  << " enabled=1 requested_layers=" << backbone_params.n_gpu_layers << "\\n";\n        t_stage = std::chrono::high_resolution_clock::now();\n        if (!backbone_.initialize(backbone_params)) {\n''',
    'enable OpenCL full backbone offload',
)

path.write_text(text, encoding='utf-8')
print('Applied Android OpenCL runtime probe and full semantic-backbone offload')
