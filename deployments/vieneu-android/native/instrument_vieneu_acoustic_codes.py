#!/usr/bin/env python3
'''Log generated acoustic codebooks for Android quality diagnostics.'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: instrument_vieneu_acoustic_codes.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
path = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'
text = path.read_text(encoding='utf-8')

old = '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            if (benchmark_enabled) {
'''
new = '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
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
'''

count = text.count(old)
if count != 1:
    raise RuntimeError(f'acoustic frame diagnostics anchor: expected one match, found {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('Instrumented per-frame VieNeu acoustic codebook diagnostics')
