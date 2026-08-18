#!/usr/bin/env python3


import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_generation_quality.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
sampler_h = root / 'src/vieneu/v3_native/v3_native_sampler.h'
sampler_cpp = root / 'src/vieneu/v3_native/v3_native_sampler.cpp'
engine_cpp = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    sampler_h,
    '''    V3NativeSampler(uint32_t seed = 42);\n\n    int64_t sample_logits(\n''',
    '''    V3NativeSampler(uint32_t seed = 42);\n    void reset_seed(uint32_t seed);\n\n    int64_t sample_logits(\n''',
    'sampler reset declaration',
)

replace_once(
    sampler_cpp,
    '''V3NativeSampler::V3NativeSampler(uint32_t seed) : rng_(seed) {}\n\nint64_t V3NativeSampler::sample_logits(\n''',
    '''V3NativeSampler::V3NativeSampler(uint32_t seed) : rng_(seed) {}\n\nvoid V3NativeSampler::reset_seed(uint32_t seed) {\n    rng_.seed(seed);\n    sampling_pairs_.clear();\n    sampling_probs_.clear();\n}\n\nint64_t V3NativeSampler::sample_logits(\n''',
    'sampler reset implementation',
)

replace_once(
    engine_cpp,
    '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());\n        ref_diag_ms("reference.resample_48k", t_ref_stage);\n        const int64_t frames = static_cast<int64_t>(mono48.size());\n''',
    '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());\n        ref_diag_ms("reference.resample_48k", t_ref_stage);\n\n        ''',
    'preserve full cleaned reference prompt',
)

replace_once(
    engine_cpp,
    '''    std::lock_guard<std::mutex> lock(run_mutex_);\n    try {\n        std::vector<float> synth_h;\n''',
    '''    std::lock_guard<std::mutex> lock(run_mutex_);\n    try {\n        ''',
    'reset sampler per utterance',
)

replace_once(
    engine_cpp,
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;\n            if (benchmark_enabled) {\n''',
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;\n            if (benchmark_enabled && t < 64) {\n                std::cerr << "[V3NativeCodes] frame=" << t\n                          << " eos=" << (eos ? 1 : 0)\n                          << " codes=";\n                for (size_t code_index = 0; code_index < codes.size(); ++code_index) {\n                    if (code_index > 0) std::cerr << ',';\n                    std::cerr << codes[code_index];\n                }\n                std::cerr << "\\n";\n            }\n            if (benchmark_enabled) {\n''',
    'log acoustic codebooks per frame',
)

replace_once(
    engine_cpp,
    '''        if (!saw_eos) {\n            std::cerr << "[V3NativeEngine] Warning: acoustic generation reached max_new_frames=" << max_frames << " before EOS.\\n";\n        }\n\n        vieneu_report_progress(params.progress, "decode_audio", 0, 1, scaled_progress(0.90f), "Decoding v3 native frames to audio.");\n''',
    '''        if (!saw_eos) {\n            std::ostringstream stop;\n            stop << "VIENEU_NO_EOS generated_frames=" << actual_steps\n                 << " max_new_frames=" << max_frames;\n            error = stop.str();\n            std::cerr << "[V3NativeStop] stop_reason=max_frames"\n                      << " generated_frames=" << actual_steps\n                      << " max_new_frames=" << max_frames << "\\n";\n            out_audio.clear();\n            return false;\n        }\n        if (benchmark_enabled) {\n            std::cerr << "[V3NativeStop] stop_reason=eos"\n                      << " generated_frames=" << actual_steps\n                      << " max_new_frames=" << max_frames << "\\n";\n        }\n\n        vieneu_report_progress(params.progress, "decode_audio", 0, 1, scaled_progress(0.90f), "Decoding v3 native frames to audio.");\n''',
    'reject truncated generation before codec decode',
)

print('Applied per-utterance sampling, full reference prompt and strict EOS completion')
