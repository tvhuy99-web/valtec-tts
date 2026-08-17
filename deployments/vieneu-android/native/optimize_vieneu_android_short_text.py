#!/usr/bin/env python3
"""Run the main Android generation patch, then validate and clean generated JNI."""

from pathlib import Path
import runpy
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_short_text.py <vieneu-jni.cpp>")

base_script = Path(__file__).with_name("optimize_vieneu_android_short_text_base.py")
runpy.run_path(str(base_script), run_name="__main__")

jni_path = Path(sys.argv[1])
text = jni_path.read_text(encoding="utf-8")

# Correct a typo from the first EOS-quality patch.
text = text.replace(
    "const int frame_caps+] = {300, 450};",
    "const int frame_caps[] = {300, 450};",
)

# Older Android optimization passes can inject diagnostics for the removed
# word-count frame heuristic. Remove those fields after every patch has run.
stale_fragments = (
    "adaptive_frame_cap",
    "auto_frame_cap",
    "text_codepoints",
    "text_words",
)
text = "".join(
    line
    for line in text.splitlines(keepends=True)
    if not any(fragment in line for fragment in stale_fragments)
)

forbidden = ("frame_caps+]", "auto_frame_cap", "adaptive_frame_cap")
remaining = [fragment for fragment in forbidden if fragment in text]
if remaining:
    raise RuntimeError(f"generated JNI still contains stale fragments: {remaining}")

required = (
    "const int frame_caps[] = {300, 450};",
    "VIENEU_NO_EOS",
    "stop_reason",
    "full_cleaned_reference",
)
missing = [fragment for fragment in required if fragment not in text]
if missing:
    raise RuntimeError(f"generated JNI is missing EOS-quality fragments: {missing}")

jni_path.write_text(text, encoding="utf-8")
print("Validated generated JNI: EOS retry policy enabled; stale frame-cap diagnostics removed")
