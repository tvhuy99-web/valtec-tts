#!/usr/bin/env python3
import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_parity_includes.py <VieNeu-TTS.cpp checkout>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/vieneu_v3_native.cpp"
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
print("Added parity diagnostic C++ includes")
