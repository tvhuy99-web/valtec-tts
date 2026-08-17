#!/usr/bin/env python3
"""Generate Android voice UI, EOS-only retry handling and fast quality modes."""

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


run_script(android_root / "patch_android_preset_voices.py", str(android_root))
run_script(native_dir / "optimize_vieneu_android_short_text_base.py", str(jni_path))

text = jni_path.read_text(encoding="utf-8")
text = text.replace(
    "const int frame_caps+] = {300, 450};",
    "const int frame_caps[] = {300, 450};",
)
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

run_script(native_dir / "optimize_vieneu_android_completion_quality.py", str(jni_path))
run_script(native_dir / "optimize_vieneu_android_parity_mode.py", str(android_root))

text = jni_path.read_text(encoding="utf-8")
required = (
    "const int frame_caps[] = {300, 450};",
    "VIENEU_NO_EOS",
    "stable_request_no_eos_retry",
    "rejects_early_eos\\\":false",
    "stop_reason",
    "full_cleaned_reference",
    "params.voice_id",
    "voice_mode",
    "deterministic_mode",
    "opencl_f32",
    "params.use_ref_codes = use_ref_codes == JNI_TRUE",
)
missing = [fragment for fragment in required if fragment not in text]
forbidden = ("VIENEU_EARLY_EOS", "minimum_audio_ms", "upstream_cpu_f32")
found = [fragment for fragment in forbidden if fragment in text]
if missing or found:
    raise RuntimeError(
        f"generated JNI fast policy invalid: missing={missing}, forbidden={found}"
    )

print(
    "Validated generated JNI: preset voices, fast OpenCL F32 modes, immediate EOS acceptance and no-EOS-only retry"
)
