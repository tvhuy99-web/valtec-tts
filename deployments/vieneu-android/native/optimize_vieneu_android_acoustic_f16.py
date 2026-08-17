#!/usr/bin/env python3
"""Keep the VieNeu acoustic decoder on the upstream CPU/F32 reference path.

This file intentionally performs no conversion. Earlier Android builds changed
QKV and projection weights to FP16 before moving the acoustic decoder to a
custom OpenCL path. That made numerical parity impossible to establish. The
parity build keeps the original upstream F32 tensors and verifies the source
shape before CMake continues.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_acoustic_f16.py <vieneu-source-dir>")

path = Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")
required = (
    "ggml_backend_cpu_init()",
    "GGML_TYPE_F32",
    "std::memcpy(weight_->data, weight",
)
forbidden = (
    "ggml_backend_opencl_init()",
    "use_f16_ = allow_f16",
    "GGML_TYPE_F16, in_dim_, out_dim_",
)
missing = [item for item in required if item not in text]
found = [item for item in forbidden if item in text]
if missing or found:
    raise RuntimeError(
        f"acoustic CPU/F32 parity verification failed: missing={missing}, forbidden={found}"
    )

print("Parity mode: preserved upstream acoustic QKV/projection weights as CPU F32")
