#!/usr/bin/env python3
"""Repair and run the generated preset-voice patch."""

from pathlib import Path
import runpy

base = Path(__file__).with_name("patch_android_preset_voices_base.py")
source = base.read_text(encoding="utf-8")
broken = "                    statusText.text'')"
fixed = "                    statusText.text''')"
count = source.count(broken)
if count != 1:
    raise RuntimeError(f"preset voice patch repair: expected one delimiter, found {count}")
base.write_text(source.replace(broken, fixed, 1), encoding="utf-8")
runpy.run_path(str(base), run_name="__main__")
