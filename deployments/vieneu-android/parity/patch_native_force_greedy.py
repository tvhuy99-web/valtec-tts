#!/usr/bin/env python3
"""Force deterministic greedy sampling inside the native engine for parity.

The CLI intentionally applies command-line values only when temperature/top-k/
top-p are positive, so `--temperature 0 --top-k 0` leaves the runtime defaults
(0.8/25/0.95) in place. Numerical parity must bypass that CLI policy.
"""

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_force_greedy.py <VieNeu-TTS.cpp checkout>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/vieneu_v3_native.cpp"
text = path.read_text(encoding="utf-8")
old = '''        VieneuV3NativeParams chunk_params = params;
        chunk_params.progress_base = static_cast<float>(i) / static_cast<float>(chunks.size());
'''
new = '''        VieneuV3NativeParams chunk_params = params;
        // Numerical parity requires a true argmax path. Force it in the engine
        // because the CLI treats zero as "not specified" and retains 0.8/25/0.95.
        chunk_params.temperature = 0.0f;
        chunk_params.top_k = 0;
        chunk_params.top_p = 1.0f;
        chunk_params.repetition_penalty = 1.0f;
        chunk_params.progress_base = static_cast<float>(i) / static_cast<float>(chunks.size());
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"force-greedy anchor: expected one match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Forced true temperature-zero greedy sampling inside native parity engine")
