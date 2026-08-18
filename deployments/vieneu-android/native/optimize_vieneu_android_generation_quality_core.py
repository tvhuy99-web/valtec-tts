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
}

static size_t v3_fft_factor(size_t n) {
    for (size_t factor = 2; factor * factor <= n; ++factor) {
        if (n % factor == 0) return factor;
    }
    return n;
}

static void v3_fft(std::vector<std::complex<double>>& values, bool inverse) {
    const size_t n = values.size();
    if (n <= 1) return;
    const size_t factor = v3_fft_factor(n);
    const double sign = inverse ? 1.0 : -1.0;
    if (factor == n) {
        std::vector<std::complex<double>> output(n);
        for (size_t k = 0; k < n; ++k) {
            std::complex<double> sum(0.0, 0.0);
            for (size_t j = 0; j < n; ++j) {
                const double angle = sign * 2.0 * kPi *
                    static_cast<double>(j * k) / static_cast<double>(n);
                sum += values[j] * std::complex<double>(std::cos(angle), std::sin(angle));
            }
            output[k] = sum;
        }
        values.swap(output);
        return;
    }

    const size_t width = n / factor;
    std::vector<std::vector<std::complex<double>>> parts(
        factor, std::vector<std::complex<double>>(width));
    for (size_t r = 0; r < factor; ++r) {
        for (size_t j = 0; j < width; ++j) {
            parts[r][j] = values[j * factor + r];
        }
        v3_fft(parts[r], inverse);
    }

    std::vector<std::complex<double>> output(n);
    for (size_t k = 0; k < n; ++k) {
        std::complex<double> sum(0.0, 0.0);
        for (size_t r = 0; r < factor; ++r) {
            const double angle = sign * 2.0 * kPi *
                static_cast<double>(r * k) / static_cast<double>(n);
            sum += parts[r][k % width] *
                std::complex<double>(std::cos(angle), std::sin(angle));
        }
        output[k] = sum;
    }
    values.swap(output);
}

static int64_t v3_reflect_index(int64_t index, int64_t length) {
    if (length <= 1) return 0;
    while (index < 0 || index >= length) {
        if (index < 0) index = -index;
        if (index >= length) index = 2 * length - 2 - index;
    }
    return index;
}

static std::vector<double> v3_irfft(
        const std::vector<std::complex<double>>& half,
        int fft_size) {
    const int bins = fft_size / 2 + 1;
    if (static_cast<int>(half.size()) != bins) return {};
    std::vector<std::complex<double>> spectrum(static_cast<size_t>(fft_size));
    spectrum[0] = std::complex<double>(half[0].real(), 0.0);
    for (int k = 1; k < fft_size / 2; ++k) {
        spectrum[static_cast<size_t>(k)] = half[static_cast<size_t>(k)];
        spectrum[static_cast<size_t>(fft_size - k)] =
            std::conj(half[static_cast<size_t>(k)]);
    }
    spectrum[static_cast<size_t>(fft_size / 2)] =
        std::complex<double>(half[static_cast<size_t>(fft_size / 2)].real(), 0.0);
    v3_fft(spectrum, true);
    std::vector<double> output(static_cast<size_t>(fft_size));
    const double scale = 1.0 / static_cast<double>(fft_size);
    for (int i = 0; i < fft_size; ++i) {
        output[static_cast<size_t>(i)] = spectrum[static_cast<size_t>(i)].real() * scale;
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

denoiser = r'''bool V3NativeDenoiser::denoise(
        const V3NativeWaveform& input,
        V3NativeWaveform& output,
        std::string& warning) {
    output = V3NativeWaveform{};
    warning.clear();
    if (!session_) {
        warning = "Native denoiser session is not initialized.";
        return false;
    }
    if (input.sample_rate <= 0 || input.mono.empty()) {
        warning = "Reference audio is empty or has an invalid sample rate.";
        return false;
    }

    try {
        constexpr int kDenoiseRate = 44100;
        constexpr int kDenoiseFft = 1680;
        constexpr int kDenoiseHop = 420;
        constexpr int kDenoisePad = 840;
        constexpr int kDenoiseBins = 841;
        constexpr size_t kDenoiseTailPad = 441;

        std::vector<float> wav = input.sample_rate == kDenoiseRate
            ? input.mono
            : v3_resample_sinc(
                input.mono,
                input.sample_rate,
                kDenoiseRate,
                64,
                0.95,
                true,
                14.769656459379492);
        if (wav.empty()) {
            warning = "Reference denoiser resampling failed.";
            return false;
        }

        const size_t output_length = wav.size();
        double abs_max = 1.0e-7;
        for (float value : wav) {
            if (!std::isfinite(value)) {
                warning = "Reference audio contains non-finite samples.";
                return false;
            }
            abs_max = (std::max)(abs_max, std::fabs(static_cast<double>(value)));
        }

        std::vector<double> normalized(output_length + kDenoiseTailPad, 0.0);
        for (size_t i = 0; i < output_length; ++i) {
            normalized[i] = static_cast<double>(wav[i]) / abs_max;
        }
        double inner_max = 0.0;
        for (double value : normalized) inner_max = (std::max)(inner_max, std::fabs(value));
        const double inner_scale = inner_max + 1.0e-7;
        for (double& value : normalized) value /= inner_scale;

        const size_t frames = normalized.size() / static_cast<size_t>(kDenoiseHop);
        if (frames == 0) {
            warning = "Reference audio is too short for denoiser STFT.";
            return false;
        }

        std::vector<double> window(static_cast<size_t>(kDenoiseFft));
        for (int i = 0; i < kDenoiseFft; ++i) {
            window[static_cast<size_t>(i)] =
                0.5 - 0.5 * std::cos(2.0 * kPi * static_cast<double>(i) /
                                     static_cast<double>(kDenoiseFft));
        }

        const size_t spectral_values =
            static_cast<size_t>(kDenoiseBins) * frames;
        std::vector<float> mag(spectral_values);
        std::vector<float> cos_phase(spectral_values);
        std::vector<float> sin_phase(spectral_values);
        std::vector<std::complex<double>> fft_frame(static_cast<size_t>(kDenoiseFft));

        for (size_t frame = 0; frame < frames; ++frame) {
            const int64_t start =
                static_cast<int64_t>(frame * static_cast<size_t>(kDenoiseHop)) -
                static_cast<int64_t>(kDenoisePad);
            for (int i = 0; i < kDenoiseFft; ++i) {
                const int64_t source_index = v3_reflect_index(
                    start + static_cast<int64_t>(i),
                    static_cast<int64_t>(normalized.size()));
                fft_frame[static_cast<size_t>(i)] =
                    std::complex<double>(
                        normalized[static_cast<size_t>(source_index)] *
                            window[static_cast<size_t>(i)],
                        0.0);
            }
            v3_fft(fft_frame, false);
            for (int bin = 0; bin < kDenoiseBins; ++bin) {
                const std::complex<double>& value =
                    fft_frame[static_cast<size_t>(bin)];
                const double magnitude = std::abs(value);
                const size_t index =
                    static_cast<size_t>(bin) * frames + frame;
                mag[index] = static_cast<float>(magnitude);
                if (magnitude > 0.0) {
                    cos_phase[index] =
                        static_cast<float>(value.real() / magnitude);
                    sin_phase[index] =
                        static_cast<float>(value.imag() / magnitude);
                } else {
                    cos_phase[index] = 1.0f;
                    sin_phase[index] = 0.0f;
                }
            }
        }

        Ort::MemoryInfo memory =
            Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
        std::vector<int64_t> shape = {
            1,
            kDenoiseBins,
            static_cast<int64_t>(frames),
        };

        const auto input_names = session_input_names(*session_);
        const auto output_names = session_output_names(*session_);
        if (input_names.size() != 3 || output_names.size() != 3) {
            warning = "Reference denoiser ONNX graph must expose three inputs and three outputs.";
            return false;
        }

        std::vector<Ort::Value> inputs;
        inputs.reserve(input_names.size());
        for (const std::string& name : input_names) {
            float* data = nullptr;
            if (name == "mag") data = mag.data();
            else if (name == "cos") data = cos_phase.data();
            else if (name == "sin") data = sin_phase.data();
            else {
                warning = "Reference denoiser ONNX input names do not match VieNeu v3 Turbo.";
                return false;
            }
            inputs.emplace_back(Ort::Value::CreateTensor<float>(
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

        const size_t synthesis_frames = frames + 1;
        const size_t signal_length =
            static_cast<size_t>(kDenoiseFft) +
            static_cast<size_t>(kDenoiseHop) * (synthesis_frames - 1);
        std::vector<double> signal(signal_length, 0.0);
        std::vector<double> window_sum(signal_length, 0.0);
        std::vector<std::complex<double>> half(
            static_cast<size_t>(kDenoiseBins));

        for (size_t frame = 0; frame < synthesis_frames; ++frame) {
            const size_t source_frame =
                frame < frames ? frame : frames - 1;
            for (int bin = 0; bin < kDenoiseBins; ++bin) {
                const size_t index =
                    static_cast<size_t>(bin) * frames + source_frame;
                const double magnitude = static_cast<double>(sep_mag[index]);
                half[static_cast<size_t>(bin)] =
                    magnitude * std::complex<double>(
                        static_cast<double>(sep_cos[index]),
                        static_cast<double>(sep_sin[index]));
            }
            const std::vector<double> time_frame =
                v3_irfft(half, kDenoiseFft);
            if (time_frame.size() != static_cast<size_t>(kDenoiseFft)) {
                warning = "Reference denoiser iSTFT failed.";
                return false;
            }
            const size_t offset =
                frame * static_cast<size_t>(kDenoiseHop);
            for (int i = 0; i < kDenoiseFft; ++i) {
                const double w = window[static_cast<size_t>(i)];
                signal[offset + static_cast<size_t>(i)] +=
                    time_frame[static_cast<size_t>(i)] * w;
                window_sum[offset + static_cast<size_t>(i)] += w * w;
            }
        }

        for (size_t i = 0; i < signal.size(); ++i) {
            signal[i] /= (std::max)(window_sum[i], 1.0e-11);
        }

        if (signal_length <= static_cast<size_t>(2 * kDenoisePad)) {
            warning = "Reference denoiser iSTFT output is too short.";
            return false;
        }
        const size_t centered_length =
            signal_length - static_cast<size_t>(2 * kDenoisePad);
        output.sample_rate = kDenoiseRate;
        output.mono.assign(output_length, 0.0f);
        const size_t copy_length = (std::min)(output_length, centered_length);
        for (size_t i = 0; i < copy_length; ++i) {
            const double value =
                signal[static_cast<size_t>(kDenoisePad) + i] * abs_max;
            if (!std::isfinite(value)) {
                output = V3NativeWaveform{};
                warning = "Reference denoiser produced non-finite samples.";
                return false;
            }
            output.mono[i] = static_cast<float>(value);
        }
        return true;
    } catch (const std::exception& e) {
        output = V3NativeWaveform{};
        warning = std::string("Reference denoiser failed: ") + e.what();
        return false;
    }
}'''

regex_once(
    reference_cpp,
    r'''bool V3NativeDenoiser::denoise\(const V3NativeWaveform& input, V3NativeWaveform& output, std::string& warning\) \{.*?\n\}''',
    denoiser,
    'native denoiser parity implementation',
)

for path, fragments in {
    reference_h: ('v3_resample_sinc(',),
    reference_cpp: (
        'std::gcd(input_rate, output_rate)',
        'kSpeakerRate, 64, 0.95, true, 14.769656459379492',
        'kDenoiseFft = 1680',
        'kDenoiseHop = 420',
        'kDenoiseTailPad = 441',
        'memory.enable_memory_arena_shrinkage',
        'sep_mag',
        'v3_irfft(',
    ),
    engine_cpp: (
        'sample_rate(), 6, 0.99, false, 0.0',
        'sampler_.reset_seed(sampling_seed)',
    ),
}.items():
    text = path.read_text(encoding='utf-8')
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f'{path}: missing generation quality fragments {missing}')

for path in (reference_h, reference_cpp, engine_cpp):
    if 'v3_resample_linear' in path.read_text(encoding='utf-8'):
        raise RuntimeError(f'{path}: linear resampling remains')

reference_text = reference_cpp.read_text(encoding='utf-8')
forbidden_reference = (
    'STFT/iSTFT denoiser path is not enabled yet',
    'using raw reference audio',
)
found = [fragment for fragment in forbidden_reference if fragment in reference_text]
if found:
    raise RuntimeError(f'{reference_cpp}: stale denoiser fallback remains {found}')

print('Applied generation quality, strict completion, sinc resampling and native reference denoiser parity')
