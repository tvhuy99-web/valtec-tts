#!/usr/bin/env python3
'''Reset VieNeu sampling to upstream seed 42 and log acoustic codebooks.

The upstream sampler starts from mt19937 seed 42. A long-lived Android engine
otherwise carries RNG state from previous requests, making the same sentence
produce unrelated lengths. Resetting to 42 preserves fresh-engine behavior.
Per-frame code logging exposes silence collapse or backend divergence directly.
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
    '''    V3NativeSampler(uint32_t seed = 42);

    int64_t sample_logits(
''',
    '''    V3NativeSampler(uint32_t seed = 42);
    void reset_seed(uint32_t seed);

    int64_t sample_logits(
''',
    'sampler reset declaration',
)

replace_once(
    sampler_cpp,
    '''V3NativeSampler::V3NativeSampler(uint32_t seed) : rng_(seed) {}

int64_t V3NativeSampler::sample_logits(
''',
    '''V3NativeSampler::V3NativeSampler(uint32_t seed) : rng_(seed) {}

void V3NativeSampler::reset_seed(uint32_t seed) {
    rng_.seed(seed);
    sampling_pairs_.clear();
    sampling_probs_.clear();
}

int64_t V3NativeSampler::sample_logits(
''',
    'sampler reset implementation',
)

replace_once(
    engine_cpp,
    '''    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        std::vector<float> synth_h;
''',
    '''    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        constexpr uint32_t sampling_seed = 42u;
        sampler_.reset_seed(sampling_seed);
        if (benchmark_enabled) {
            std::cerr << "[V3NativeDiag] stage=sampling.reset"
                      << " seed=" << sampling_seed
                      << " mode=fresh_engine_default"
                      << " phoneme_bytes=" << phonemes.size() << "\\n";
        }
        std::vector<float> synth_h;
''',
    'reset sampler for each utterance',
)

replace_once(
    engine_cpp,
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            if (benchmark_enabled) {
''',
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            if (benchmark_enabled && t < 64) {
                std::cerr << "[V3NativeCodes] frame=" << t
                          << " eos=" << (eos ? 1 : 0)
                          << " codes=";
                for (size_t code_index = 0; code_index < codes.size(); ++code_index) {
                    if (code_index > 0) std::cerr << ',';
                    std::cerr << codes[code_index];
                }
                std::cerr << "\\n";
            }
            if (benchmark_enabled) {
''',
    'log acoustic codebooks per frame',
)

print('Applied upstream seed-42 reset and acoustic codebook diagnostics')
