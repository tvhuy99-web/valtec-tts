#!/usr/bin/env python3
'''Limit voice-cloning reference codes to the clearest 3.2-second speech window.

The speaker encoder continues to see the full (up to eight second) reference so
voice identity is preserved. Only the codec-code prompt is shortened. Feeding a
full 6+ second utterance into every prompt can leak its linguistic trajectory
into the beginning of newly generated speech and also increases prefill work.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_reference_prompt.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
path = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'
text = path.read_text(encoding='utf-8')

old = '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        ref_diag_ms("reference.resample_48k", t_ref_stage);
        const int64_t frames = static_cast<int64_t>(mono48.size());
'''
new = '''        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        ref_diag_ms("reference.resample_48k", t_ref_stage);

        // Keep the complete reference for speaker embedding, but condition the
        // autoregressive prompt on only the strongest contiguous 3.2 seconds.
        // This reduces linguistic/content leakage from long reference clips.
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
'''

count = text.count(old)
if count != 1:
    raise RuntimeError(f'reference codec window anchor: expected one match, found {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('Limited reference codec prompt to the highest-energy 3.2-second window')
