#!/usr/bin/env python3

import pathlib
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_generation_quality.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
sampler_h = root / 'src/vieneu/v3_native/v3_native_sampler.h'
sampler_cpp = root / 'src/vieneu/v3_native/v3_native_sampler.cpp'
engine_cpp = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'
reference_h = root / 'src/vieneu/v3_native/v3_native_reference.h'
reference_cpp = root / 'src/vieneu/v3_native/v3_native_reference.cpp'


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


def regex_once(path: pathlib.Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding='utf-8')
    updated, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one regex match, found {count}')
    path.write_text(updated, encoding='utf-8')


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
    '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());\n        ref_diag_ms("reference.resample_48k", t_ref_stage);\n        if (diag) {\n            std::cout << "[V3NativeDiag] stage=reference.codec_input"\n                      << " source_ms=" << (1000.0 * static_cast<double>(wav.mono.size()) / wav.sample_rate)\n                      << " duration_ms=" << (1000.0 * static_cast<double>(mono48.size()) / sample_rate())\n                      << " policy=full_cleaned_reference\\n";\n        }\n        const int64_t frames = static_cast<int64_t>(mono48.size());\n''',
    'preserve full cleaned reference prompt',
)

replace_once(
    engine_cpp,
    '''    std::lock_guard<std::mutex> lock(run_mutex_);\n    try {\n        std::vector<float> synth_h;\n''',
    '''    std::lock_guard<std::mutex> lock(run_mutex_);\n    try {\n        uint32_t sampling_seed = static_cast<uint32_t>(\n            std::chrono::high_resolution_clock::now().time_since_epoch().count());\n        for (unsigned char c : phonemes) {\n            sampling_seed ^= static_cast<uint32_t>(c);\n            sampling_seed *= 16777619u;\n        }\n        if (const char* forced_seed = std::getenv("VIENEU_V3_SAMPLING_SEED")) {\n            char* end = nullptr;\n            const unsigned long parsed = std::strtoul(forced_seed, &end, 10);\n            if (end != forced_seed && end && *end == '\\0') {\n                sampling_seed = static_cast<uint32_t>(parsed);\n            }\n        }\n        if (sampling_seed == 0u) sampling_seed = 1u;\n        sampler_.reset_seed(sampling_seed);\n        if (benchmark_enabled) {\n            std::cerr << "[V3NativeDiag] stage=sampling.reset"\n                      << " seed=" << sampling_seed\n                      << " mode=" << (std::getenv("VIENEU_V3_SAMPLING_SEED") ? "forced" : "per_utterance")\n                      << " phoneme_bytes=" << phonemes.size() << "\\n";\n        }\n        std::vector<float> synth_h;\n''',
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

replace_once(
    reference_h,
    'std::vector<float> v3_resample_linear(const std::vector<float>& input, int input_rate, int output_rate);',
    'std::vector<float> v3_resample_sinc(const std::vector<float>& input, int input_rate, int output_rate, int lowpass_filter_width, double rolloff, bool kaiser, double beta);',
    'resampler declaration',
)

replace_once(
    reference_cpp,
    '#include <fstream>\n#include <stdexcept>\n',
    '#include <fstream>\n#include <numeric>\n#include <stdexcept>\n',
    'resampler numeric include',
)

resampler = r'''double v3_bessel_i0(double x) {
    const double ax = std::fabs(x);
    if (ax < 3.75) {
        const double y = (x / 3.75) * (x / 3.75);
        return 1.0 + y * (3.5156229 + y * (3.0899424 + y * (1.2067492 +
               y * (0.2659732 + y * (0.0360768 + y * 0.0045813)))));
    }
    const double y = 3.75 / ax;
    return (std::exp(ax) / std::sqrt(ax)) *
           (0.39894228 + y * (0.01328592 + y * (0.00225319 + y * (-0.00157565 +
           y * (0.00916281 + y * (-0.02057706 + y * (0.02635537 +
           y * (-0.01647633 + y * 0.00392377))))))));
}

std::vector<float> v3_resample_sinc(
        const std::vector<float>& input,
        int input_rate,
        int output_rate,
        int lowpass_filter_width,
        double rolloff,
        bool kaiser,
        double beta) {
    if (input.empty() || input_rate <= 0 || output_rate <= 0 || lowpass_filter_width <= 0) return {};
    if (input_rate == output_rate) return input;
    if (!(rolloff > 0.0 && rolloff <= 1.0)) return {};

    const int divisor = std::gcd(input_rate, output_rate);
    const int orig = input_rate / divisor;
    const int next = output_rate / divisor;
    const double base_freq = static_cast<double>((std::min)(orig, next)) * rolloff;
    const int width = static_cast<int>(std::ceil(
        static_cast<double>(lowpass_filter_width) * static_cast<double>(orig) / base_freq));
    const int kernel_size = 2 * width + orig;
    const double scale = base_freq / static_cast<double>(orig);
    const double beta_norm = kaiser ? v3_bessel_i0(beta) : 1.0;

    std::vector<float> kernels(static_cast<size_t>(next) * static_cast<size_t>(kernel_size));
    for (int phase = 0; phase < next; ++phase) {
        for (int k = 0; k < kernel_size; ++k) {
            const double idx = static_cast<double>(k - width) / static_cast<double>(orig);
            double t = (-static_cast<double>(phase) / static_cast<double>(next) + idx) * base_freq;
            t = std::clamp(t, -static_cast<double>(lowpass_filter_width), static_cast<double>(lowpass_filter_width));
            double window = 0.0;
            if (kaiser) {
                const double ratio = t / static_cast<double>(lowpass_filter_width);
                window = v3_bessel_i0(beta * std::sqrt((std::max)(0.0, 1.0 - ratio * ratio))) / beta_norm;
            } else {
                const double angle = t * kPi / static_cast<double>(lowpass_filter_width) / 2.0;
                const double c = std::cos(angle);
                window = c * c;
            }
            const double angle = t * kPi;
            const double sinc = std::fabs(angle) < 1.0e-12 ? 1.0 : std::sin(angle) / angle;
            kernels[static_cast<size_t>(phase) * static_cast<size_t>(kernel_size) + static_cast<size_t>(k)] =
                static_cast<float>(sinc * window * scale);
        }
    }

    const int64_t target_length = static_cast<int64_t>(std::ceil(
        static_cast<double>(next) * static_cast<double>(input.size()) / static_cast<double>(orig)));
    std::vector<float> output(static_cast<size_t>((std::max<int64_t>)(1, target_length)), 0.0f);
    for (int64_t n = 0; n < target_length; ++n) {
        const int64_t frame = n / next;
        const int phase = static_cast<int>(n % next);
        const int64_t input_start = frame * orig - width;
        const float* kernel = kernels.data() + static_cast<size_t>(phase) * static_cast<size_t>(kernel_size);
        double sum = 0.0;
        for (int k = 0; k < kernel_size; ++k) {
            const int64_t index = input_start + k;
            if (index >= 0 && index < static_cast<int64_t>(input.size())) {
                sum += static_cast<double>(input[static_cast<size_t>(index)]) * static_cast<double>(kernel[k]);
            }
        }
        output[static_cast<size_t>(n)] = static_cast<float>(sum);
    }
    return output;
}'''

regex_once(
    reference_cpp,
    r'''std::vector<float> v3_resample_linear\(const std::vector<float>& input, int input_rate, int output_rate\) \{.*?\n\}''',
    resampler,
    'resampler implementation',
)

replace_once(
    reference_cpp,
    'std::vector<float> wav = v3_resample_linear(mono, sample_rate, kSpeakerRate);',
    'std::vector<float> wav = v3_resample_sinc(mono, sample_rate, kSpeakerRate, 64, 0.95, true, 14.769656459379492);',
    'speaker resampler parity',
)

replace_once(
    engine_cpp,
    'std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());',
    'std::vector<float> mono48 = v3_resample_sinc(wav.mono, wav.sample_rate, sample_rate(), 6, 0.99, false, 0.0);',
    'reference code resampler parity',
)

for path, fragments in {
    reference_h: ('v3_resample_sinc(',),
    reference_cpp: ('std::gcd(input_rate, output_rate)', 'kSpeakerRate, 64, 0.95, true, 14.769656459379492'),
    engine_cpp: ('sample_rate(), 6, 0.99, false, 0.0', 'sampler_.reset_seed(sampling_seed)'),
}.items():
    text = path.read_text(encoding='utf-8')
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f'{path}: missing generation quality fragments {missing}')

for path in (reference_h, reference_cpp, engine_cpp):
    if 'v3_resample_linear' in path.read_text(encoding='utf-8'):
        raise RuntimeError(f'{path}: linear resampling remains')

print('Applied generation quality, strict completion and reference resampling parity')
