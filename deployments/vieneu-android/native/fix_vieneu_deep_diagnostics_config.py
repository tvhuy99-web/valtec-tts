#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: fix_vieneu_deep_diagnostics_config.py <vieneu-source-dir>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/vieneu_v3_native.cpp"
text = path.read_text(encoding="utf-8")
replacements = {
    "config_.acoustic_num_hidden_layers": "config_.local_num_hidden_layers",
    "config_.sample_rate": "sample_rate()",
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one occurrence of {old!r}, found {count}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("Corrected deep diagnostic config field names")
