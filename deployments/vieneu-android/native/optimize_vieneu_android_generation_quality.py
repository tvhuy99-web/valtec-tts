#!/usr/bin/env python3
'''Make VieNeu v3 sampling deterministic per utterance and independently reproducible.

The upstream sampler owns one long-lived mt19937. Every generation consumes its
state, so the same sentence can terminate after 64 frames or wander for 191
frames depending on what was synthesized before it. Resetting from a stable hash
of the phoneme prompt makes output, EOS behavior and performance repeatable.
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
    '''bool contains_v3_emotion_token(const std::string& text) {
''',
    '''uint32_t stable_utterance_seed(const std::string& phonemes, int style_token_id) {
    // FNV-1a gives a small deterministic seed without depending on the unstable
    // implementation-defined result of std::hash across platforms/builds.
    uint32_t hash = 2166136261u;
    for (unsigned char c : phonemes) {
        hash ^= static_cast<uint32_t>(c);
        hash *= 16777619u;
    }
    hash ^= static_cast<uint32_t>(style_token_id);
    hash *= 16777619u;
    return hash == 0u ? 42u : hash;
}

bool contains_v3_emotion_token(const std::string& text) {
''',
    'stable utterance seed helper',
)

replace_once(
    engine_cpp,
    '''    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        std::vector<float> synth_h;
''',
    '''    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        const uint32_t sampling_seed = stable_utterance_seed(phonemes, style_token_id);
        sampler_.reset_seed(sampling_seed);
        if (benchmark_enabled) {
            std::cerr << "[V3NativeDiag] stage=sampling.reset"
                      << " seed=" << sampling_seed
                      << " phoneme_bytes=" << phonemes.size() << "\\n";
        }
        std::vector<float> synth_h;
''',
    'reset sampler for each utterance',
)

print('Applied deterministic per-utterance acoustic sampling seed')
