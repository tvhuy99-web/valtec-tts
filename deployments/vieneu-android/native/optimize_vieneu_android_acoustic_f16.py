#!/usr/bin/env python3
'''Use FP16 for VieNeu Android acoustic QKV/O-projection weights.

Perf4 used Q8_0 for these projections and was faster, but the submitted WAV
showed a clear short-phrase quality regression (hesitation/repetition). FP16 is
chosen as the quality-preserving middle ground on ARMv8.2+fp16 devices. Final
audio/text heads remain F32 and the fused FFN remains Q8_0.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_acoustic_f16.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
path = root / 'src/vieneu/v3_native/v3_native_acoustic_ggml.cpp'
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)


replace_once(
    '''    void initialize(ggml_backend_t backend, const float* weight, int out_dim, int in_dim, int n_threads) {
''',
    '''    void initialize(ggml_backend_t backend, const float* weight, int out_dim, int in_dim, int n_threads, bool allow_f16 = false) {
''',
    'GgmlLinear initialize signature',
)

replace_once(
    '''        direct_weight_ = weight;
        use_direct_ = use_direct_linear_backend();
        if (use_direct_) {
            return;
        }

        const size_t ctx_size = 1024 * 1024 + static_cast<size_t>(in_dim_) * out_dim_ * sizeof(float) +
                                static_cast<size_t>(in_dim_ + out_dim_) * sizeof(float);
''',
    '''        direct_weight_ = weight;
        use_f16_ = allow_f16;
        use_direct_ = !use_f16_ && use_direct_linear_backend();
        if (use_direct_) {
            return;
        }

        const ggml_type weight_type = use_f16_ ? GGML_TYPE_F16 : GGML_TYPE_F32;
        const size_t weight_bytes = ggml_row_size(weight_type, in_dim_) * static_cast<size_t>(out_dim_);
        const size_t ctx_size = 1024 * 1024 + weight_bytes +
                                static_cast<size_t>(in_dim_ + out_dim_) * sizeof(float);
''',
    'GgmlLinear FP16 mode and context size',
)

replace_once(
    '''        weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, in_dim_, out_dim_);
''',
    '''        weight_ = ggml_new_tensor_2d(ctx_, weight_type, in_dim_, out_dim_);
''',
    'GgmlLinear weight tensor type',
)

replace_once(
    '''        std::memcpy(weight_->data, weight, static_cast<size_t>(in_dim_) * out_dim_ * sizeof(float));
        plan_ = ggml_graph_plan(graph_, thread_count_, nullptr);
''',
    '''        if (use_f16_) {
            ggml_fp32_to_fp16_row(
                weight,
                reinterpret_cast<ggml_fp16_t*>(weight_->data),
                static_cast<int64_t>(in_dim_) * static_cast<int64_t>(out_dim_));
        } else {
            std::memcpy(weight_->data, weight, static_cast<size_t>(in_dim_) * out_dim_ * sizeof(float));
        }
        plan_ = ggml_graph_plan(graph_, thread_count_, nullptr);
''',
    'GgmlLinear weight initialization',
)

replace_once(
    '''        direct_weight_ = nullptr;
        use_direct_ = true;
    }
''',
    '''        direct_weight_ = nullptr;
        use_f16_ = false;
        use_direct_ = true;
    }
''',
    'GgmlLinear release FP16 state',
)

replace_once(
    '''    int out_dim_ = 0;
    int thread_count_ = 1;
    bool use_direct_ = true;
};

class GgmlFfnOp {
''',
    '''    int out_dim_ = 0;
    int thread_count_ = 1;
    bool use_f16_ = false;
    bool use_direct_ = true;
};

class GgmlFfnOp {
''',
    'GgmlLinear FP16 member',
)

replace_once(
    '''            ops.qkv.initialize(backend, wl.qkv.data(), 3 * H, H, n_threads);
            ops.o_proj.initialize(backend, wl.o_proj.data(), H, H, n_threads);
''',
    '''            // FP16 preserves substantially more projection precision than the
            // perf4 Q8 experiment while still using ARMv8.2 FP16 vector kernels.
            // Final audio/text heads remain F32; FFN keeps its existing Q8_0 path.
            ops.qkv.initialize(backend, wl.qkv.data(), 3 * H, H, n_threads, true);
            ops.o_proj.initialize(backend, wl.o_proj.data(), H, H, n_threads, true);
''',
    'enable FP16 for QKV and O-projection only',
)

replace_once(
    '''        layer_ops.clear();
        layer_ops.resize(w.layers.size());
''',
    '''        if (std::getenv("VIENEU_V3_NATIVE_BENCHMARK")) {
            std::cout << "[V3NativeDiag] stage=acoustic.linear_mode"
                      << " qkv=F16 o_proj=F16 ffn=Q8_0 heads=F32" << std::endl;
        }
        layer_ops.clear();
        layer_ops.resize(w.layers.size());
''',
    'acoustic linear mode diagnostics',
)

path.write_text(text, encoding='utf-8')
print('Applied Android acoustic FP16 optimization to QKV/O-projection; heads remain F32')
