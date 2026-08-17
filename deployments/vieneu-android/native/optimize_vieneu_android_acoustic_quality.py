#!/usr/bin/env python3
"""Force the upstream CPU acoustic decoder to use full F32 weights.

Upstream runs acoustic attention and projections on CPU, but its fused FFN still
enables Q8_0 by default. For a behavioral parity build, that quantization must
also be disabled because autoregressive numerical drift compounds frame by
frame. This patch changes only the upstream CPU implementation: no custom graph
or backend is introduced.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_acoustic_quality.py <vieneu-source-dir>")

path = Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''        use_q8_ = env_flag_enabled("VIENEU_ACOUSTIC_Q8_FFN", true);
        const int64_t q8_block = ggml_blck_size(GGML_TYPE_Q8_0);
        if (use_q8_ && (hidden_dim_ % q8_block != 0 || intermediate_dim_ % q8_block != 0)) {
            use_q8_ = false;
        }

        use_direct_ = !use_q8_ && use_direct_linear_backend();
''',
    '''        // Parity build: keep original F32 FFN tensors. Q8 errors can change
        // later autoregressive frames even when the backend itself is correct.
        use_q8_ = false;
        use_direct_ = use_direct_linear_backend();
''',
    "disable upstream Q8 acoustic FFN",
)
replace_once(
    '''        const ggml_type weight_type = use_q8_ ? GGML_TYPE_Q8_0 : GGML_TYPE_F32;
''',
    '''        const ggml_type weight_type = GGML_TYPE_F32;
''',
    "select F32 FFN tensor type",
)
replace_once(
    '''        if (use_q8_) {
            ggml_quantize_chunk(GGML_TYPE_Q8_0, gate_weight, gate_weight_->data, 0, intermediate_dim_, hidden_dim_, nullptr);
            ggml_quantize_chunk(GGML_TYPE_Q8_0, up_weight, up_weight_->data, 0, intermediate_dim_, hidden_dim_, nullptr);
            ggml_quantize_chunk(GGML_TYPE_Q8_0, down_weight, down_weight_->data, 0, hidden_dim_, intermediate_dim_, nullptr);
        } else {
            std::memcpy(gate_weight_->data, gate_weight, static_cast<size_t>(intermediate_dim_) * hidden_dim_ * sizeof(float));
            std::memcpy(up_weight_->data, up_weight, static_cast<size_t>(intermediate_dim_) * hidden_dim_ * sizeof(float));
            std::memcpy(down_weight_->data, down_weight, static_cast<size_t>(hidden_dim_) * intermediate_dim_ * sizeof(float));
        }
''',
    '''        std::memcpy(gate_weight_->data, gate_weight, static_cast<size_t>(intermediate_dim_) * hidden_dim_ * sizeof(float));
        std::memcpy(up_weight_->data, up_weight, static_cast<size_t>(intermediate_dim_) * hidden_dim_ * sizeof(float));
        std::memcpy(down_weight_->data, down_weight, static_cast<size_t>(hidden_dim_) * intermediate_dim_ * sizeof(float));
''',
    "upload original F32 FFN weights",
)

required = (
    "backend = ggml_backend_cpu_init();",
    "const ggml_type weight_type = GGML_TYPE_F32;",
    "use_q8_ = false;",
    "std::memcpy(gate_weight_->data, gate_weight",
    "std::memcpy(up_weight_->data, up_weight",
    "std::memcpy(down_weight_->data, down_weight",
)
forbidden = (
    "ggml_backend_opencl_init()",
    "env_flag_enabled(\"VIENEU_ACOUSTIC_Q8_FFN\"",
    "ggml_quantize_chunk(GGML_TYPE_Q8_0",
    "OpenCL acoustic",
)
missing = [item for item in required if item not in text]
found = [item for item in forbidden if item in text]
if missing or found:
    raise RuntimeError(
        f"full acoustic CPU/F32 verification failed: missing={missing}, forbidden={found}"
    )

path.write_text(text, encoding="utf-8")
print("Parity mode: upstream CPU acoustic attention, FFN, heads and EOS use original F32 weights")
