#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: instrument_vieneu_reference_diagnostics.py <vieneu-source-dir>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_reference.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''#include <fstream>\n#include <stdexcept>\n''',
    '''#include <fstream>\n#include <iomanip>\n#include <iostream>\n#include <limits>\n#include <sstream>\n#include <stdexcept>\n''',
    "reference diagnostic includes",
)

replace_once(
    '''double hz_to_mel(double hz) {\n''',
    r'''uint64_t reference_diag_hash(const float* values, size_t count) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(values);
    const size_t bytes_count = count * sizeof(float);
    uint64_t hash = 1469598103934665603ULL;
    for (size_t i = 0; i < bytes_count; ++i) {
        hash ^= static_cast<uint64_t>(bytes[i]);
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string reference_diag_summary(const std::vector<float>& values, size_t preview = 8) {
    std::ostringstream out;
    out << std::setprecision(9);
    double sum = 0.0;
    double sum_sq = 0.0;
    double min_value = std::numeric_limits<double>::infinity();
    double max_value = -std::numeric_limits<double>::infinity();
    size_t finite = 0;
    for (float value : values) {
        if (!std::isfinite(value)) continue;
        const double v = static_cast<double>(value);
        sum += v;
        sum_sq += v * v;
        min_value = std::min(min_value, v);
        max_value = std::max(max_value, v);
        ++finite;
    }
    out << "count=" << values.size()
        << " finite=" << finite
        << " min=" << (finite ? min_value : 0.0)
        << " max=" << (finite ? max_value : 0.0)
        << " mean=" << (finite ? sum / static_cast<double>(finite) : 0.0)
        << " rms=" << (finite ? std::sqrt(sum_sq / static_cast<double>(finite)) : 0.0)
        << " hash=0x" << std::hex
        << (values.empty() ? 0ULL : reference_diag_hash(values.data(), values.size()))
        << std::dec << " first=";
    for (size_t i = 0; i < std::min(preview, values.size()); ++i) {
        if (i) out << ',';
        out << values[i];
    }
    return out.str();
}

double hz_to_mel(double hz) {
''',
    "reference diagnostic helpers",
)

replace_once(
    '''    std::vector<float> wav = v3_resample_linear(mono, sample_rate, kSpeakerRate);\n    if (wav.size() < kFrameLength) {\n''',
    '''    std::vector<float> wav = v3_resample_linear(mono, sample_rate, kSpeakerRate);
    std::cerr << "[V3NativeRefDeep] resample input_rate=" << sample_rate
              << " output_rate=" << kSpeakerRate
              << " input=" << reference_diag_summary(mono, 4)
              << " output=" << reference_diag_summary(wav, 4) << "\n";
    if (wav.size() < kFrameLength) {
''',
    "reference resample summary",
)

replace_once(
    '''    for (int64_t f = 0; f < frames; ++f) {\n        for (int m = 0; m < kMelBins; ++m) {\n            features[static_cast<size_t>(f * kMelBins + m)] -= static_cast<float>(mean[static_cast<size_t>(m)]);\n        }\n    }\n    return features;\n}\n''',
    '''    for (int64_t f = 0; f < frames; ++f) {
        for (int m = 0; m < kMelBins; ++m) {
            features[static_cast<size_t>(f * kMelBins + m)] -= static_cast<float>(mean[static_cast<size_t>(m)]);
        }
    }
    std::cerr << "[V3NativeRefDeep] fbank frames=" << frames
              << " bins=" << kMelBins
              << " frame_length=" << kFrameLength
              << " frame_shift=" << kFrameShift
              << " fft=" << kFftSize
              << " " << reference_diag_summary(features) << "\n";
    return features;
}
''',
    "fbank summary",
)

replace_once(
    '''        out_embedding.assign(p, p + count);\n        if (out_embedding.size() == 192) return true;\n''',
    '''        out_embedding.assign(p, p + count);
        std::cerr << "[V3NativeRefDeep] speaker_encoder input_shape=1x" << frames << "x" << kMelBins
                  << " output_shape=";
        for (int64_t dim : info.GetShape()) std::cerr << dim << 'x';
        std::cerr << " " << reference_diag_summary(out_embedding) << "\n";
        if (out_embedding.size() == 192) return true;
''',
    "speaker encoder output summary",
)

path.write_text(text, encoding="utf-8")
print("Instrumented native reference fbank and speaker encoder diagnostics")
