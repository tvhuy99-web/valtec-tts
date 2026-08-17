#!/usr/bin/env python3
'''Quantize VieNeu Android acoustic QKV/O-projection weights to Q8_0.

This is intentionally separate from the stable memory/reference patches so the
speed experiment can be reverted independently. Audio/text output heads remain
F32 to avoid changing the final logits/sampling path.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_acoustic_q8.py <vieneu-source-dir>')

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
    '''    void initialize(ggml_backend_t backend, const float* weight, int out_dim, int in_dim, int n_threads, bool allow_q8 = false) {
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
        use_q8_ = allow_q8;
        const int64_t q8_block = ggml_blck_size(GGML_TYPE_Q8_0);
        if (use_q8_ && in_dim_ % q8_block != 0) {
            use_q8_ = false;
        }
        use_direct_ = !use_q8_ && use_direct_linear_backend();
        if (use_direct_) {
            return;
        }

        const ggml_type weight_type = use_q8_ ? GGML_TYPE_Q8_0 : GGML_TYPE_F32;
        const size_t weight_bytes = ggml_row_size(weight_type, in_dim_) * static_cast<size_t>(out_dim_);
        const size_t ctx_size = 1024 * 1024 + weight_bytes +
                                static_cast<size_t>(in_dim_ + out_dim_) * sizeof(float);
''',
    'GgmlLinear Q8 mode and context size',
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
    '''        if (use_q8_) {
            ggml_quantize_chunk(
                GGML_TYPE_Q8_0,
                weight,
                weight_->data,
                0,
                out_dim_,
                in_dim_,
                nullptr);
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
        use_q8_ = false;
        use_direct_ = true;
    }
''',
    'GgmlLinear release Q8 state',
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
    bool use_q8_ = false;
    bool use_direct_ = true;
};

class GgmlFfnOp {
''',
    'GgmlLinear Q8 member',
)

replace_once(
    '''            ops.qkv.initialize(backend, wl.qkv.data(), 3 * H, H, n_threads);
            ops.o_proj.initialize(backend, wl.o_proj.data(), H, H, n_threads);
''',
    '''            // QKV and O-projection account for roughly half of Android acoustic
            // runtime. They are internal hidden-state projections, so use the same
            // Q8_0 + ARM dot-product path already used by the fused FFN. Keep the
            // final audio/text heads F32 so sampling logits are not quantized.
            ops.qkv.initialize(backend, wl.qkv.data(), 3 * H, H, n_threads, true);
            ops.o_proj.initialize(backend, wl.o_proj.data(), H, H, n_threads, true);
''',
    'enable Q8 for QKV and O-projection only',
)

replace_once(
    '''        layer_ops.clear();
        layer_ops.resize(w.layers.size());
''',
    '''        if (std::getenv("VIENEU_V3_NATIVE_BENCHMARK")) {
            std::cout << "[V3NativeDiag] stage=acoustic.linear_mode"
                      << " qkv=Q8_0 o_proj=Q8_0 ffn=Q8_0 heads=F32" << std::endl;
        }
        layer_ops.clear();
        layer_ops.resize(w.layers.size());
''',
    'acoustic linear mode diagnostics',
)

path.write_text(text, encoding='utf-8')
print('Applied Android acoustic Q8_0 optimization to QKV/O-projection; heads remain F32')
