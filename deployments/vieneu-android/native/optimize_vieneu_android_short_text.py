#!/usr/bin/env python3
"""Apply Android voice UI, EOS policy, stable retries, then validate generated JNI."""

from pathlib import Path
import runpy
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_short_text.py <vieneu-jni.cpp>")

jni_path = Path(sys.argv[1]).resolve()
native_dir = Path(__file__).resolve().parent
android_root = native_dir.parent


def run_script(path: Path, *arguments: str) -> None:
    saved_argv = sys.argv[:]
    try:
        sys.argv = [str(path), *arguments]
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = saved_argv


# Expose all preset voices in the downloaded model and pass voice_id through JNI.
run_script(
    android_root / "patch_android_preset_voices.py",
    str(android_root),
)

# Apply the main safety-budget and no-EOS retry patch.
run_script(
    native_dir / "optimize_vieneu_android_short_text_base.py",
    str(jni_path),
)

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

jni_path.write_text(text, encoding="utf-8")

# Stabilize repeated requests and reject EOS that arrives implausibly early.
run_script(
    native_dir / "optimize_vieneu_android_completion_quality.py",
    str(jni_path),
)

text = jni_path.read_text(encoding="utf-8")
required = (
    "const int frame_caps[] = {300, 300, 450};",
    "VIENEU_NO_EOS",
    "VIENEU_EARLY_EOS",
    "stable_request_retry_sequence",
    "stop_reason",
    "full_cleaned_reference",
    "params.voice_id",
    "voice_mode",
)
missing = [fragment for fragment in required if fragment not in text]
if missing:
    raise RuntimeError(f"generated JNI is missing quality/voice fragments: {missing}")

print(
    "Validated generated JNI: preset voices enabled; stable three-attempt EOS quality policy enabled"
)
