#!/usr/bin/env python3

import runpy
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_generation_quality.py <vieneu-source-dir>')

native_dir = Path(__file__).resolve().parent
core = native_dir / 'optimize_vieneu_android_generation_quality_core.py'
runpy.run_path(str(core), run_name='__main__')

reference_cpp = Path(sys.argv[1]).resolve() / 'src/vieneu/v3_native/v3_native_reference.cpp'
text = reference_cpp.read_text(encoding='utf-8')
old = '''        const auto input_ptrs = ptrs(input_names);
        const auto output_ptrs = ptrs(output_names);
        Ort::RunOptions run_options;
        run_options.AddConfigEntry("memory.enable_memory_arena_shrinkage", "cpu:0");
        auto outputs = session_->Run(
            run_options,
            input_ptrs.data(),
            inputs.data(),
            inputs.size(),
            output_ptrs.data(),
            output_ptrs.size());

        const float* sep_mag = nullptr;
        const float* sep_cos = nullptr;
        const float* sep_sin = nullptr;
        for (size_t i = 0; i < output_names.size(); ++i) {
            const auto info = outputs[i].GetTensorTypeAndShapeInfo();
            size_t count = 1;
            for (int64_t dim : info.GetShape()) {
                if (dim <= 0) {
                    warning = "Reference denoiser returned an invalid tensor shape.";
                    return false;
                }
                count *= static_cast<size_t>(dim);
            }
            if (count != spectral_values) {
                warning = "Reference denoiser returned an unexpected tensor size.";
                return false;
            }
            const float* data = outputs[i].GetTensorData<float>();
            if (output_names[i] == "sep_mag") sep_mag = data;
            else if (output_names[i] == "sep_cos") sep_cos = data;
            else if (output_names[i] == "sep_sin") sep_sin = data;
            else {
                warning = "Reference denoiser ONNX output names do not match VieNeu v3 Turbo.";
                return false;
            }
        }
        if (!sep_mag || !sep_cos || !sep_sin) {
            warning = "Reference denoiser ONNX outputs are incomplete.";
            return false;
        }
'''
new = '''        std::vector<float> sep_mag_storage(spectral_values);
        std::vector<float> sep_cos_storage(spectral_values);
        std::vector<float> sep_sin_storage(spectral_values);
        std::vector<Ort::Value> outputs;
        outputs.reserve(output_names.size());
        for (const std::string& name : output_names) {
            float* data = nullptr;
            if (name == "sep_mag") data = sep_mag_storage.data();
            else if (name == "sep_cos") data = sep_cos_storage.data();
            else if (name == "sep_sin") data = sep_sin_storage.data();
            else {
                warning = "Reference denoiser ONNX output names do not match VieNeu v3 Turbo.";
                return false;
            }
            outputs.emplace_back(Ort::Value::CreateTensor<float>(
                memory,
                data,
                spectral_values,
                shape.data(),
                shape.size()));
        }

        const auto input_ptrs = ptrs(input_names);
        const auto output_ptrs = ptrs(output_names);
        Ort::RunOptions run_options;
        run_options.AddConfigEntry("memory.enable_memory_arena_shrinkage", "cpu:0");
        session_->Run(
            run_options,
            input_ptrs.data(),
            inputs.data(),
            inputs.size(),
            output_ptrs.data(),
            outputs.data(),
            outputs.size());

        const float* sep_mag = sep_mag_storage.data();
        const float* sep_cos = sep_cos_storage.data();
        const float* sep_sin = sep_sin_storage.data();
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f'denoiser ORT output block: expected exactly one match, found {count}')
text = text.replace(old, new, 1)
reference_cpp.write_text(text, encoding='utf-8')

final = reference_cpp.read_text(encoding='utf-8')
required = ('outputs.data()', 'sep_mag_storage.data()', 'session_->Run(')
missing = [fragment for fragment in required if fragment not in final]
if missing:
    raise RuntimeError(f'denoiser ORT output fix missing fragments {missing}')

system_tts_core = native_dir / 'optimize_vieneu_android_system_tts_core.py'
runpy.run_path(str(system_tts_core), run_name='__main__')

system_tts_ui = native_dir / 'patch_vieneu_android_system_tts_ui.py'
runpy.run_path(str(system_tts_ui), run_name='__main__')

print('Applied VieNeu generation quality core, preallocated ONNX denoiser outputs, system-TTS cancellation and UI hooks')
