#!/usr/bin/env python3
'''Reset VieNeu v3 sampling to the upstream fresh-engine seed per utterance.

The upstream sampler starts from mt19937 seed 42. A long-lived Android engine
otherwise carries RNG state from previous requests, making the same sentence
produce unrelated lengths. Resetting to 42 preserves upstream sampling behavior
while making every utterance independent and reproducible.
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

print('Applied upstream seed-42 reset for independent utterance sampling')
