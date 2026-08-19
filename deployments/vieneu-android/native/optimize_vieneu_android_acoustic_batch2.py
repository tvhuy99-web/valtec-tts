#!/usr/bin/env python3

import pathlib
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_acoustic_batch2.py <vieneu-source-dir>")

root = pathlib.Path(sys.argv[1]).resolve()
path = root / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


def regex_once(pattern: str, replacement: str, label: str) -> None:
    global text
    updated, count = re.subn(
        pattern,
        lambda _m: replacement,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    text = updated


# Each acoustic frame starts with S=2 (semantic hidden + speech-generation-start).
# The canonical OpenCL path previously launched one matvec graph per token. Build
# a second fixed F32 graph with batch=2 so that initial QKV/O-proj/FFN work uses
# one device submission and one host/device transfer per op instead of two.
replace_once(
    '''        weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, in_dim_, out_dim_);
        input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, in_dim_);
        output_ = ggml_mul_mat(ctx_, weight_, input_);
        ggml_set_input(input_);
        ggml_set_output(output_);
        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);
''',
    '''        weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, in_dim_, out_dim_);
        input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, in_dim_);
        output_ = ggml_mul_mat(ctx_, weight_, input_);
        input2_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, in_dim_, 2);
        output2_ = ggml_mul_mat(ctx_, weight_, input2_);
        ggml_set_input(input_);
        ggml_set_output(output_);
        ggml_set_input(input2_);
        ggml_set_output(output2_);
        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);
        graph2_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph2_, output2_);
''',
    "linear batch2 graph",
)

replace_once(
    '''    void run(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(out_dim_));
        ggml_backend_tensor_set(input_, input, 0, static_cast<size_t>(in_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic linear graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output.data(), 0, static_cast<size_t>(out_dim_) * sizeof(float));
    }

    void shutdown() { release(); }
''',
    '''    void run(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(out_dim_));
        ggml_backend_tensor_set(input_, input, 0, static_cast<size_t>(in_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic linear graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output.data(), 0, static_cast<size_t>(out_dim_) * sizeof(float));
    }

    void run_batch2(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(2 * out_dim_));
        ggml_backend_tensor_set(
            input2_, input, 0, static_cast<size_t>(2 * in_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph2_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic linear batch2 graph compute failed.");
        }
        ggml_backend_tensor_get(
            output2_, output.data(), 0, static_cast<size_t>(2 * out_dim_) * sizeof(float));
    }

    void shutdown() { release(); }
''',
    "linear batch2 run",
)

replace_once(
    '''        input_ = nullptr;
        output_ = nullptr;
        graph_ = nullptr;
''',
    '''        input_ = nullptr;
        output_ = nullptr;
        input2_ = nullptr;
        output2_ = nullptr;
        graph_ = nullptr;
        graph2_ = nullptr;
''',
    "linear batch2 release",
)

replace_once(
    '''    ggml_tensor* input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
''',
    '''    ggml_tensor* input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_tensor* input2_ = nullptr;
    ggml_tensor* output2_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
    ggml_cgraph* graph2_ = nullptr;
''',
    "linear batch2 members",
)

replace_once(
    '''        input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, hidden_dim_);

        ggml_tensor* gate = ggml_mul_mat(ctx_, gate_weight_, input_);
        ggml_tensor* up = ggml_mul_mat(ctx_, up_weight_, input_);
        ggml_tensor* activated = ggml_silu(ctx_, gate);
        ggml_tensor* fused = ggml_mul(ctx_, activated, up);
        output_ = ggml_mul_mat(ctx_, down_weight_, fused);
        ggml_set_input(input_);
        ggml_set_output(output_);

        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);
''',
    '''        input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, hidden_dim_);
        input2_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, hidden_dim_, 2);

        ggml_tensor* gate = ggml_mul_mat(ctx_, gate_weight_, input_);
        ggml_tensor* up = ggml_mul_mat(ctx_, up_weight_, input_);
        ggml_tensor* activated = ggml_silu(ctx_, gate);
        ggml_tensor* fused = ggml_mul(ctx_, activated, up);
        output_ = ggml_mul_mat(ctx_, down_weight_, fused);

        ggml_tensor* gate2 = ggml_mul_mat(ctx_, gate_weight_, input2_);
        ggml_tensor* up2 = ggml_mul_mat(ctx_, up_weight_, input2_);
        ggml_tensor* activated2 = ggml_silu(ctx_, gate2);
        ggml_tensor* fused2 = ggml_mul(ctx_, activated2, up2);
        output2_ = ggml_mul_mat(ctx_, down_weight_, fused2);
        ggml_set_input(input_);
        ggml_set_output(output_);
        ggml_set_input(input2_);
        ggml_set_output(output2_);

        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);
        graph2_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph2_, output2_);
''',
    "ffn batch2 graph",
)

replace_once(
    '''    void run(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(hidden_dim_));
        ggml_backend_tensor_set(input_, input, 0, static_cast<size_t>(hidden_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic FFN graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output.data(), 0, static_cast<size_t>(hidden_dim_) * sizeof(float));
    }

private:
''',
    '''    void run(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(hidden_dim_));
        ggml_backend_tensor_set(input_, input, 0, static_cast<size_t>(hidden_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic FFN graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output.data(), 0, static_cast<size_t>(hidden_dim_) * sizeof(float));
    }

    void run_batch2(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(2 * hidden_dim_));
        ggml_backend_tensor_set(
            input2_, input, 0, static_cast<size_t>(2 * hidden_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph2_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic FFN batch2 graph compute failed.");
        }
        ggml_backend_tensor_get(
            output2_, output.data(), 0, static_cast<size_t>(2 * hidden_dim_) * sizeof(float));
    }

private:
''',
    "ffn batch2 run",
)

# This is the second occurrence of the release/member blocks, belonging to FFN.
old_release = '''        input_ = nullptr;
        output_ = nullptr;
        graph_ = nullptr;
        hidden_dim_ = 0;
'''
new_release = '''        input_ = nullptr;
        output_ = nullptr;
        input2_ = nullptr;
        output2_ = nullptr;
        graph_ = nullptr;
        graph2_ = nullptr;
        hidden_dim_ = 0;
'''
replace_once(old_release, new_release, "ffn batch2 release")

old_members = '''    ggml_tensor* input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
    int hidden_dim_ = 0;
'''
new_members = '''    ggml_tensor* input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_tensor* input2_ = nullptr;
    ggml_tensor* output2_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
    ggml_cgraph* graph2_ = nullptr;
    int hidden_dim_ = 0;
'''
replace_once(old_members, new_members, "ffn batch2 members")

# Batch the S=2 QKV projection once per layer. Q/K normalization and attention
# remain on the CPU exactly as before; only the F32 matmul submission is batched.
regex_once(
    r'''            for \(int s = 0; s < S; \+\+s\) \{\n                \{\n                    ScopedBenchTimer timer\(benchmark_enabled, bench\.norm1_ms\);.*?\n                \}\n            \}\n\n            const int new_used = past \+ S;''',
    '''            for (int s = 0; s < S; ++s) {
                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.norm1_ms);
                    rms_norm(
                        x.data() + static_cast<size_t>(s) * H,
                        wl.norm1.data(),
                        H,
                        config.rms_norm_eps,
                        normed.data() + static_cast<size_t>(s) * H);
                }
            }

            {
                ScopedBenchTimer timer(benchmark_enabled, bench.qkv_ms);
                if (S == 2) {
                    ops.qkv.run_batch2(normed.data(), qkv_val);
                } else {
                    ops.qkv.run(normed.data(), qkv_val);
                }
            }
            for (int s = 0; s < S; ++s) {
                const size_t qkv_base = static_cast<size_t>(s) * static_cast<size_t>(3 * H);
                std::copy(
                    qkv_val.begin() + static_cast<std::ptrdiff_t>(qkv_base),
                    qkv_val.begin() + static_cast<std::ptrdiff_t>(qkv_base + H),
                    q.begin() + static_cast<size_t>(s) * H);
                std::copy(
                    qkv_val.begin() + static_cast<std::ptrdiff_t>(qkv_base + H),
                    qkv_val.begin() + static_cast<std::ptrdiff_t>(qkv_base + 2 * H),
                    new_k.begin() + static_cast<size_t>(s) * H);
                std::copy(
                    qkv_val.begin() + static_cast<std::ptrdiff_t>(qkv_base + 2 * H),
                    qkv_val.begin() + static_cast<std::ptrdiff_t>(qkv_base + 3 * H),
                    new_v.begin() + static_cast<size_t>(s) * H);

                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.qk_norm_ms);
                    for (int head = 0; head < nH; ++head) {
                        rms_norm(
                            q.data() + static_cast<size_t>(s * H + head * hd),
                            wl.q_norm.data(),
                            hd,
                            config.rms_norm_eps,
                            q.data() + static_cast<size_t>(s * H + head * hd));
                        rms_norm(
                            new_k.data() + static_cast<size_t>(s * H + head * hd),
                            wl.k_norm.data(),
                            hd,
                            config.rms_norm_eps,
                            new_k.data() + static_cast<size_t>(s * H + head * hd));
                    }
                }
            }

            const int new_used = past + S;''',
    "batch S=2 QKV projection",
)

# Batch O-proj and fused FFN for the same S=2 initial step. Residuals and RMS
# norms stay in their original F32 CPU implementation and operation order.
regex_once(
    r'''            for \(int s = 0; s < S; \+\+s\) \{\n                \{\n                    ScopedBenchTimer timer\(benchmark_enabled, bench\.o_proj_ms\);.*?\n                \}\n            \}\n        \}\n\n        output\.resize''',
    '''            {
                ScopedBenchTimer timer(benchmark_enabled, bench.o_proj_ms);
                if (S == 2) {
                    ops.o_proj.run_batch2(attn_out.data(), proj);
                } else {
                    ops.o_proj.run(attn_out.data(), proj);
                }
            }
            for (int s = 0; s < S; ++s) {
                float* x_ptr = x.data() + static_cast<size_t>(s) * H;
                const float* proj_ptr = proj.data() + static_cast<size_t>(s) * H;
                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.residual_ms);
                    for (int i = 0; i < H; ++i) {
                        x_ptr[i] += proj_ptr[i];
                    }
                }
                {
                    ScopedBenchTimer timer(benchmark_enabled, bench.norm2_ms);
                    rms_norm(
                        x_ptr,
                        wl.norm2.data(),
                        H,
                        config.rms_norm_eps,
                        normed.data() + static_cast<size_t>(s) * H);
                }
            }

            if (ops.fuse_ffn) {
                ScopedBenchTimer timer(benchmark_enabled, bench.ffn_ms);
                if (S == 2) {
                    ops.ffn.run_batch2(normed.data(), down);
                } else {
                    ops.ffn.run(normed.data(), down);
                }
            } else {
                ScopedBenchTimer timer(benchmark_enabled, bench.ffn_ms);
                if (S == 2) {
                    ops.ff_gate.run_batch2(normed.data(), gate);
                    ops.ff_up.run_batch2(normed.data(), up);
                    for (int s = 0; s < S; ++s) {
                        const size_t base = static_cast<size_t>(s) * I;
                        for (int i = 0; i < I; ++i) {
                            up[base + static_cast<size_t>(i)] *= silu(gate[base + static_cast<size_t>(i)]);
                        }
                    }
                    ops.ff_down.run_batch2(up.data(), down);
                } else {
                    ops.ff_gate.run(normed.data(), gate);
                    ops.ff_up.run(normed.data(), up);
                    for (int i = 0; i < I; ++i) {
                        up[static_cast<size_t>(i)] *= silu(gate[static_cast<size_t>(i)]);
                    }
                    ops.ff_down.run(up.data(), down);
                }
            }
            for (int s = 0; s < S; ++s) {
                float* x_ptr = x.data() + static_cast<size_t>(s) * H;
                const float* down_ptr = down.data() + static_cast<size_t>(s) * H;
                ScopedBenchTimer timer(benchmark_enabled, bench.residual_ms);
                for (int i = 0; i < H; ++i) {
                    x_ptr[i] += down_ptr[i];
                }
            }
        }

        output.resize''',
    "batch S=2 O-proj and FFN",
)

path.write_text(text, encoding="utf-8")

final = path.read_text(encoding="utf-8")
required = (
    "run_batch2(const float* input",
    "ops.qkv.run_batch2",
    "ops.o_proj.run_batch2",
    "ops.ffn.run_batch2",
    "OpenCL acoustic linear batch2 graph compute failed",
    "OpenCL acoustic FFN batch2 graph compute failed",
)
missing = [fragment for fragment in required if fragment not in final]
if missing:
    raise RuntimeError(f"{path}: batch2 acoustic fragments missing: {missing}")

print("Applied F32 OpenCL batch=2 acoustic graphs for the initial two-token step")
