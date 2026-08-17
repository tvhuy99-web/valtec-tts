#!/usr/bin/env python3
'''Reset VieNeu sampling, log acoustic codes and compact the reference prompt.

The upstream sampler starts from mt19937 seed 42. A long-lived Android engine
otherwise carries RNG state across requests, so each utterance is reset to the
fresh-engine seed. The full reference remains available to the speaker encoder,
but only the clearest 3.2-second window is encoded as autoregressive prompt
codes to reduce linguistic leakage from a long reference recording.
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
    '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        ref_diag_ms("reference.resample_48k", t_ref_stage);
        const int64_t frames = static_cast<int64_t>(mono48.size());
''',
    '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        ref_diag_ms("reference.resample_48k", t_ref_stage);

        // The complete recording still drives the speaker embedding. Restrict
        // only the autoregressive codec prompt to the strongest 3.2 seconds so
        // a long reference does not steer the beginning toward its own words.
        const size_t target_samples = static_cast<size_t>(sample_rate()) * 16u / 5u;
        size_t selected_start = 0;
        if (mono48.size() > target_samples && target_samples > 0) {
            std::vector<double> prefix(mono48.size() + 1, 0.0);
            for (size_t sample = 0; sample < mono48.size(); ++sample) {
                const double value = static_cast<double>(mono48[sample]);
                prefix[sample + 1] = prefix[sample] + value * value;
            }
            const size_t hop = (std::max)(static_cast<size_t>(1), static_cast<size_t>(sample_rate() / 20));
            double best_energy = -1.0;
            for (size_t start = 0; start + target_samples <= mono48.size(); start += hop) {
                const double energy = prefix[start + target_samples] - prefix[start];
                if (energy > best_energy) {
                    best_energy = energy;
                    selected_start = start;
                }
            }
            const size_t final_start = mono48.size() - target_samples;
            const double final_energy = prefix[mono48.size()] - prefix[final_start];
            if (final_energy > best_energy) selected_start = final_start;
            std::vector<float> selected(
                mono48.begin() + static_cast<std::ptrdiff_t>(selected_start),
                mono48.begin() + static_cast<std::ptrdiff_t>(selected_start + target_samples));
            mono48.swap(selected);
        }
        if (diag) {
            std::cout << "[V3NativeDiag] stage=reference.codec_window"
                      << " source_ms=" << (1000.0 * static_cast<double>(wav.mono.size()) / wav.sample_rate)
                      << " start_ms=" << (1000.0 * static_cast<double>(selected_start) / sample_rate())
                      << " duration_ms=" << (1000.0 * static_cast<double>(mono48.size()) / sample_rate())
                      << "\\n";
        }
        const int64_t frames = static_cast<int64_t>(mono48.size());
''',
    'compact reference codec prompt',
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

print('Applied seed-42 reset, compact reference prompt and codebook diagnostics')
