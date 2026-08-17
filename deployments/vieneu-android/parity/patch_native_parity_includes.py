#!/usr/bin/env python3
import pathlib
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_parity_includes.py <VieNeu-TTS.cpp checkout>")

root = pathlib.Path(sys.argv[1])
path = root / "src/vieneu/v3_native/vieneu_v3_native.cpp"
text = path.read_text(encoding="utf-8")
old = '''#include <iostream>
#include <stdexcept>
'''
new = '''#include <iostream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"parity includes: expected one match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

for script_name in (
    "patch_native_acoustic_internal_dump.py",
    "patch_native_acoustic_trace_once.py",
    "patch_native_exact_output_heads.py",
):
    subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).with_name(script_name)), str(root)],
        check=True,
    )
print("Added parity diagnostics, preserved frame zero, and enabled exact output heads")
