#!/usr/bin/env python3
"""Verify that acoustic generation remains on the upstream CPU backend.

The semantic Qwen backbone may still use Adreno OpenCL. The autoregressive
acoustic decoder, its FFN, output heads, attention and EOS head deliberately stay
on the upstream CPU/F32 implementation until tensor-level parity is proven.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_acoustic_opencl.py <vieneu-source-dir>")

path = Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")
required = (
    "backend = ggml_backend_cpu_init();",
    "ggml_backend_cpu_set_n_threads",
    "class GgmlLinear",
    "class GgmlFfnOp",
)
forbidden = (
    "ggml_backend_opencl_init()",
    "OpenCL acoustic linear",
    "OpenCL acoustic FFN",
    "mode=OpenCL-only",
)
missing = [item for item in required if item not in text]
found = [item for item in forbidden if item in text]
if missing or found:
    raise RuntimeError(
        f"acoustic backend parity verification failed: missing={missing}, forbidden={found}"
    )

print("Parity mode: acoustic decoder verified on upstream GGML CPU backend")
