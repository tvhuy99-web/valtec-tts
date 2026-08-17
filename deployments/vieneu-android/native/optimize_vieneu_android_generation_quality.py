#!/usr/bin/env python3
'''Improve VieNeu Android generation quality without diverging from upstream behavior.

The Android runtime keeps the official v3 Turbo sampling defaults and reference-code
conditioning, but it must not reuse one fixed seed forever or crop the reference
prompt to an arbitrary high-energy window. Each utterance gets a fresh seed (unless
VIENEU_V3_SAMPLING_SEED is explicitly set for deterministic diagnostics), the full
cleaned reference clip is encoded, and generation that reaches max_new_frames before
EOS is treated as a failed attempt instead of decoding and saving truncated speech.
'''

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
    '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());\n        ref_diag_ms("reference.resample_48k", t_ref_stage);\n\n        // Match the official path: encode the complete cleaned reference clip.\n        // Selecting an arbitrary high-energy 3.2-second window can retain noise or\n        // leak the wrong prosody/content into the autoregressive reference prompt.\n        if (diag) {\n            std::cout << "[V3NativeDiag] stage=reference.codec_input"\n                      << " source_ms=" << (1000.0 * static_cast<double>(wav.mono.size()) / wav.sample_rate)\n                      << " duration_ms=" << (1000.0 * static_cast<double>(mono48.size()) / sample_rate())\n                      << " policy=full_cleaned_reference\\n";\n        }\n        const int64_t frames = static_cast<int64_t>(mono48.size());\n''',
    'preserve full cleaned reference prompt',
)

replace_once(
    engine_cpp,
    '''    std::lock_guard<std::mutex> lock(run_mutex_);\n    try {\n        std::vector<float> synth_h;\n''',
    '''    std::lock_guard<std::mutex> lock(run_mutex_);\n    try {\n        // Production requests must not be locked to seed 42. A fixed bad sample\n        // otherwise repeats forever. Tests can still force a deterministic seed\n        // with VIENEU_V3_SAMPLING_SEED.\n        uint32_t sampling_seed = static_cast<uint32_t>(\n            std::chrono::high_resolution_clock::now().time_since_epoch().count());\n        for (unsigned char c : phonemes) {\n            sampling_seed ^= static_cast<uint32_t>(c);\n            sampling_seed *= 16777619u;\n        }\n        if (const char* forced_seed = std::getenv("VIENEU_V3_SAMPLING_SEED")) {\n            char* end = nullptr;\n            const unsigned long parsed = std::strtoul(forced_seed, &end, 10);\n            if (end != forced_seed && end && *end == '\\0') {\n                sampling_seed = static_cast<uint32_t>(parsed);\n            }\n        }\n        if (sampling_seed == 0u) sampling_seed = 1u;\n        sampler_.reset_seed(sampling_seed);\n        if (benchmark_enabled) {\n            std::cerr << "[V3NativeDiag] stage=sampling.reset"\n                      << " seed=" << sampling_seed\n                      << " mode=" << (std::getenv("VIENEU_V3_SAMPLING_SEED") ? "forced" : "per_utterance")\n                      << " phoneme_bytes=" << phonemes.size() << "\\n";\n        }\n        std::vector<float> synth_h;\n''',
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
