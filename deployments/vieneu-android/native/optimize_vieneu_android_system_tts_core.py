#!/usr/bin/env python3

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_system_tts_core.py <vieneu-source-dir>')

root = Path(sys.argv[1]).resolve()
engine = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'
text = engine.read_text(encoding='utf-8')

old_reset = '''    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        uint32_t sampling_seed = static_cast<uint32_t>(
'''
new_reset = '''    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        ::setenv("VIENEU_V3_CANCEL_REQUESTED", "0", 1);
        uint32_t sampling_seed = static_cast<uint32_t>(
'''
if text.count(old_reset) != 1:
    raise RuntimeError(f'system TTS cancel reset anchor: expected one match, found {text.count(old_reset)}')
text = text.replace(old_reset, new_reset, 1)

old_frame = '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            if (benchmark_enabled && t < 64) {
'''
new_frame = '''            if (const char* cancel = std::getenv("VIENEU_V3_CANCEL_REQUESTED"); cancel && cancel[0] == '1') {
                error = "VIENEU_CANCELLED";
                out_audio.clear();
                std::cerr << "[V3NativeStop] stop_reason=cancelled"
                          << " generated_frames=" << actual_steps << "\\n";
                return false;
            }
            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            if (benchmark_enabled && t < 64) {
'''
if text.count(old_frame) != 1:
    raise RuntimeError(f'system TTS cancel frame anchor: expected one match, found {text.count(old_frame)}')
text = text.replace(old_frame, new_frame, 1)
engine.write_text(text, encoding='utf-8')

final = engine.read_text(encoding='utf-8')
required = (
    'VIENEU_V3_CANCEL_REQUESTED',
    'stop_reason=cancelled',
    'error = "VIENEU_CANCELLED";',
)
missing = [fragment for fragment in required if fragment not in final]
if missing:
    raise RuntimeError(f'missing system TTS cancellation fragments: {missing}')

print('Applied frame-level VieNeu cancellation for interactive system TTS')
