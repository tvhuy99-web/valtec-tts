#!/usr/bin/env python3

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_acoustic_fused_post_attn.py <vieneu-source-dir>")

root = pathlib.Path(sys.argv[1]).resolve()
path = root / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


# The canonical OpenCL implementation currently performs O-projection and FFN
# as two independent backend graphs. Between them it reads O-proj to the host,
# applies residual + RMSNorm on CPU, uploads the normalized vector again, runs
# FFN, then reads FFN back to the host. This fused graph preserves F32/model
# math but keeps that complete post-attention chain on the OpenCL backend:
#
#   attn -> O-proj -> residual -> RMSNorm2 -> FFN -> residual
#
# QKV, Q/K normalization, KV cache, attention and sampling intentionally stay
# untouched in this first persistent-graph step.
post_attn_class = r'''class GgmlPostAttentionOp {
public:
    GgmlPostAttentionOp() = default;
    ~GgmlPostAttentionOp() { release(); }

    void initialize(ggml_backend_t backend,
                    const float* o_weight,
                    const float* norm2_weight,
                    const float* gate_weight,
                    const float* up_weight,
                    const float* down_weight,
                    int hidden_dim,
                    int intermediate_dim,
                    float rms_norm_eps) {
        release();
        backend_ = backend;
        if (!backend_) {
            throw std::runtime_error("Fused OpenCL post-attention block received a null backend.");
        }
        hidden_dim_ = hidden_dim;
        intermediate_dim_ = intermediate_dim;

        ggml_init_params params = {
            4 * 1024 * 1024,
            nullptr,
            true,
        };
        ctx_ = ggml_init(params);
        if (!ctx_) {
            throw std::runtime_error("Failed to initialize fused OpenCL post-attention metadata context.");
        }

        o_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, hidden_dim_, hidden_dim_);
        norm2_weight_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, hidden_dim_);
        gate_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, hidden_dim_, intermediate_dim_);
        up_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, hidden_dim_, intermediate_dim_);
        down_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, intermediate_dim_, hidden_dim_);
        residual_input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, hidden_dim_);
        attention_input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, hidden_dim_);

        ggml_tensor* projected = ggml_mul_mat(ctx_, o_weight_, attention_input_);
        ggml_tensor* residual = ggml_add(ctx_, residual_input_, projected);
        ggml_tensor* normalized = ggml_rms_norm(ctx_, residual, rms_norm_eps);
        ggml_tensor* weighted_norm = ggml_mul(ctx_, normalized, norm2_weight_);
        ggml_tensor* gate = ggml_mul_mat(ctx_, gate_weight_, weighted_norm);
        ggml_tensor* up = ggml_mul_mat(ctx_, up_weight_, weighted_norm);
        ggml_tensor* activated = ggml_silu(ctx_, gate);
        ggml_tensor* gated = ggml_mul(ctx_, activated, up);
        ggml_tensor* down = ggml_mul_mat(ctx_, down_weight_, gated);
        output_ = ggml_add(ctx_, residual, down);

        ggml_set_input(residual_input_);
        ggml_set_input(attention_input_);
        ggml_set_output(output_);
        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);

        buffer_ = ggml_backend_alloc_ctx_tensors(ctx_, backend_);
        if (!buffer_) {
            throw std::runtime_error("Failed to allocate fused OpenCL post-attention buffer.");
        }

        ggml_backend_tensor_set(o_weight_, o_weight, 0, ggml_nbytes(o_weight_));
        ggml_backend_tensor_set(norm2_weight_, norm2_weight, 0, ggml_nbytes(norm2_weight_));
        ggml_backend_tensor_set(gate_weight_, gate_weight, 0, ggml_nbytes(gate_weight_));
        ggml_backend_tensor_set(up_weight_, up_weight, 0, ggml_nbytes(up_weight_));
        ggml_backend_tensor_set(down_weight_, down_weight, 0, ggml_nbytes(down_weight_));
    }

    void run(const float* residual_input,
             const float* attention_input,
             float* output) {
        const size_t bytes = static_cast<size_t>(hidden_dim_) * sizeof(float);
        ggml_backend_tensor_set(residual_input_, residual_input, 0, bytes);
        ggml_backend_tensor_set(attention_input_, attention_input, 0, bytes);
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL fused post-attention graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output, 0, bytes);
    }

private:
    void release() {
        if (buffer_) {
            ggml_backend_buffer_free(buffer_);
            buffer_ = nullptr;
        }
        if (ctx_) {
            ggml_free(ctx_);
            ctx_ = nullptr;
        }
        backend_ = nullptr;
        o_weight_ = nullptr;
        norm2_weight_ = nullptr;
        gate_weight_ = nullptr;
        up_weight_ = nullptr;
        down_weight_ = nullptr;
        residual_input_ = nullptr;
        attention_input_ = nullptr;
        output_ = nullptr;
        graph_ = nullptr;
        hidden_dim_ = 0;
        intermediate_dim_ = 0;
    }

    ggml_backend_t backend_ = nullptr;
    ggml_backend_buffer_t buffer_ = nullptr;
    ggml_context* ctx_ = nullptr;
    ggml_tensor* o_weight_ = nullptr;
    ggml_tensor* norm2_weight_ = nullptr;
    ggml_tensor* gate_weight_ = nullptr;
    ggml_tensor* up_weight_ = nullptr;
    ggml_tensor* down_weight_ = nullptr;
    ggml_tensor* residual_input_ = nullptr;
    ggml_tensor* attention_input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
    int hidden_dim_ = 0;
    int intermediate_dim_ = 0;
};
'''

replace_once(
    '''    int hidden_dim_ = 0;
    int intermediate_dim_ = 0;
};

} ''',
    '''    int hidden_dim_ = 0;
    int intermediate_dim_ = 0;
};

''' + post_attn_class + '''
} ''',
    "insert fused post-attention OpenCL op",
)

replace_once(
    '''    double ffn_ms = 0.0;
    double residual_ms = 0.0;
    double final_norm_ms = 0.0;
''',
    '''    double ffn_ms = 0.0;
    double residual_ms = 0.0;
    double fused_post_attn_ms = 0.0;
    double final_norm_ms = 0.0;
''',
    "add fused post-attention benchmark counter",
)

replace_once(
    '''    struct LayerOps {
        GgmlLinear qkv;
        GgmlLinear o_proj;
        GgmlLinear ff_gate;
        GgmlLinear ff_up;
        GgmlLinear ff_down;
        GgmlFfnOp ffn;
        bool fuse_ffn = false;
    };
''',
    '''    struct LayerOps {
        GgmlLinear qkv;
        GgmlPostAttentionOp post_attn;
    };
''',
    "replace separate O-proj/FFN layer ops with fused post-attention op",
)

# optimize_vieneu_android.py runs before this patch and intentionally forces
# GGML heads on Android, so anchor the already-materialized `use_ggml_heads = true`
# form rather than the pristine upstream environment-controlled line.
replace_once(
    '''        const int H = config.hidden_size;
        const int I = config.local_intermediate_size;
        const bool fuse = env_flag_enabled("VIENEU_GGML_FUSE_FFN", true);
        use_ggml_heads = true;
        layer_ops.clear();
        layer_ops.resize(w.layers.size());
        for (size_t i = 0; i < w.layers.size(); ++i) {
            const auto& wl = w.layers[i];
            LayerOps& ops = layer_ops[i];
            ops.fuse_ffn = fuse;
            ops.qkv.initialize(backend, wl.qkv.data(), 3 * H, H, n_threads);
            ops.o_proj.initialize(backend, wl.o_proj.data(), H, H, n_threads);
            if (fuse) {
                ops.ffn.initialize(backend, wl.ff_gate.data(), wl.ff_up.data(), wl.ff_down.data(), H, I, n_threads);
            } else {
                ops.ff_gate.initialize(backend, wl.ff_gate.data(), I, H, n_threads);
                ops.ff_up.initialize(backend, wl.ff_up.data(), I, H, n_threads);
                ops.ff_down.initialize(backend, wl.ff_down.data(), H, I, n_threads);
            }
        }
''',
    '''        const int H = config.hidden_size;
        const int I = config.local_intermediate_size;
        use_ggml_heads = true;
        layer_ops.clear();
        layer_ops.resize(w.layers.size());
        for (size_t i = 0; i < w.layers.size(); ++i) {
            const auto& wl = w.layers[i];
            LayerOps& ops = layer_ops[i];
            ops.qkv.initialize(backend, wl.qkv.data(), 3 * H, H, n_threads);
            ops.post_attn.initialize(
                backend,
                wl.o_proj.data(),
                wl.norm2.data(),
                wl.ff_gate.data(),
                wl.ff_up.data(),
                wl.ff_down.data(),
                H,
                I,
                config.rms_norm_eps);
        }
        std::cerr << "[V3NativeDiag] stage=acoustic.fused_post_attn"
                  << " enabled=1 precision=F32"
                  << " graph=\\\"o_proj+residual+rmsnorm2+ffn+residual\\\""
                  << " host_roundtrip_between_o_and_ffn=0\\n";
''',
    "initialize fused post-attention layer graphs",
)

replace_once(
    '''            for (int s = 0; s < S; ++s) {
                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.o_proj_ms);
                    ops.o_proj.run(attn_out.data() + static_cast<size_t>(s) * H, proj);
                }
                float* x_ptr = x.data() + static_cast<size_t>(s) * H;
                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.residual_ms);
                    for (int i = 0; i < H; ++i) {
                        x_ptr[i] += proj[static_cast<size_t>(i)];
                    }
                }

                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.norm2_ms);
                    rms_norm(x_ptr, wl.norm2.data(), H, config.rms_norm_eps, normed.data() + static_cast<size_t>(s) * H);
                }
                if (ops.fuse_ffn) {
                    ScopedBenchTimer timer(benchmark_enabled, bench.ffn_ms);
                    ops.ffn.run(normed.data() + static_cast<size_t>(s) * H, down);
                } else {
                    ScopedBenchTimer timer(benchmark_enabled, bench.ffn_ms);
                    ops.ff_gate.run(normed.data() + static_cast<size_t>(s) * H, gate);
                    ops.ff_up.run(normed.data() + static_cast<size_t>(s) * H, up);
                    for (int i = 0; i < I; ++i) {
                        up[static_cast<size_t>(i)] *= silu(gate[static_cast<size_t>(i)]);
                    }
                    ops.ff_down.run(up.data(), down);
                }
                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.residual_ms);
                    for (int i = 0; i < H; ++i) {
                        x_ptr[i] += down[static_cast<size_t>(i)];
                    }
                }
            }
''',
    '''            for (int s = 0; s < S; ++s) {
                float* x_ptr = x.data() + static_cast<size_t>(s) * H;
                ScopedBenchTimer timer(benchmark_enabled, bench.fused_post_attn_ms);
                ops.post_attn.run(
                    x_ptr,
                    attn_out.data() + static_cast<size_t>(s) * H,
                    x_ptr);
            }
''',
    "fuse O-proj residual RMSNorm2 FFN residual execution",
)

replace_once(
    '''              << "    ffn=" << b.ffn_ms << " ms"
              << ", residual=" << b.residual_ms << " ms"
              << ", final_norm=" << b.final_norm_ms << " ms\\n"
''',
    '''              << "    ffn=" << b.ffn_ms << " ms"
              << ", residual=" << b.residual_ms << " ms"
              << ", fused_post_attn=" << b.fused_post_attn_ms << " ms"
              << ", final_norm=" << b.final_norm_ms << " ms\\n"
''',
    "report fused post-attention benchmark",
)

replace_once(
    '''        b.reset_cache_ms + b.cached_step_ms + b.sample_head_matvec_ms +
        b.sample_head_ggml_ms + b.sample_select_ms + b.eos_head_matvec_ms +
        b.eos_head_ggml_ms;
''',
    '''        b.reset_cache_ms + b.cached_step_ms + b.sample_head_matvec_ms +
        b.sample_head_ggml_ms + b.sample_select_ms + b.eos_head_matvec_ms +
        b.eos_head_ggml_ms;
''',
    "keep frame-level benchmark accounting stable",
)

path.write_text(text, encoding="utf-8")

final = path.read_text(encoding="utf-8")
required = (
    "class GgmlPostAttentionOp",
    "ops.post_attn.initialize(",
    "ops.post_attn.run(",
    "ggml_rms_norm(ctx_, residual, rms_norm_eps)",
    "OpenCL fused post-attention graph compute failed.",
    "host_roundtrip_between_o_and_ffn=0",
    "fused_post_attn_ms",
    "use_ggml_heads = true;",
)
missing = [fragment for fragment in required if fragment not in final]
if missing:
    raise RuntimeError(f"Fused post-attention F32 patch missing fragments: {missing}")

forbidden = (
    "ops.o_proj.run(",
    "ops.ffn.run(",
    "ops.ff_gate.run(",
    "ops.ff_up.run(",
    "ops.ff_down.run(",
)
found = [fragment for fragment in forbidden if fragment in final]
if found:
    raise RuntimeError(f"Separate host-roundtrip post-attention path survived fusion: {found}")

print("Applied fused F32 OpenCL post-attention graph without changing model, sampling or KV attention")
