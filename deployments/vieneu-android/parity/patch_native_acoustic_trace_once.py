#!/usr/bin/env python3
"""Prevent acoustic parity trace files from being overwritten by later frames."""

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_acoustic_trace_once.py <VieNeu-TTS.cpp checkout>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")
old = '''    path += name;
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
'''
new = '''    path += name;
    {
        std::ifstream existing(path, std::ios::binary);
        if (existing.good()) return;
    }
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"trace-once helper anchor: expected one match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Configured acoustic parity dumps to preserve the first generated frame")
