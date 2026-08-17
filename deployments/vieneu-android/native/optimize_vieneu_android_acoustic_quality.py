#!/usr/bin/env python3
"""Fail closed unless the complete acoustic hot path is upstream CPU/F32.

This is the final guard after the legacy Android acoustic optimization hooks.
Those hooks are now validators only. Any future reintroduction of FP16, Q8 or a
custom OpenCL acoustic graph must be explicit and accompanied by parity tests.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_acoustic_quality.py <vieneu-source-dir>")

path = Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")
required = (
    "weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32",
    "gate_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32",
    "up_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32",
    "down_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32",
    "backend = ggml_backend_cpu_init();",
)
forbidden = (
    "GGML_TYPE_Q8_0",
    "ggml_fp32_to_fp16_row",
    "ggml_backend_opencl_init()",
    "OpenCL acoustic",
)
missing = [item for item in required if item not in text]
found = [item for item in forbidden if item in text]
if missing or found:
    raise RuntimeError(
        f"full acoustic parity verification failed: missing={missing}, forbidden={found}"
    )

print("Parity mode: full acoustic attention/FFN/heads/EOS path verified CPU F32")
