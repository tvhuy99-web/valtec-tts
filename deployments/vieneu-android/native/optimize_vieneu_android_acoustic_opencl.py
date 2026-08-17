#!/usr/bin/env python3
'''Run VieNeu's acoustic projection, FFN, and sampling-head graphs on OpenCL.

The upstream acoustic implementation accepts a ggml backend but discards it and
allocates every graph in host RAM, so the semantic backbone can be on Adreno
while more than 97% of synthesis remains on CPU. This Android-only patch keeps
all large acoustic weights in OpenCL buffers and performs only small input and
output transfers per autoregressive step. Unsupported GPU graphs fail loudly;
there is no automatic CPU fallback for these hot paths.
'''

import pathlib
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_acoustic_opencl.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
path = root / 'src/vieneu/v3_native/v3_native_acoustic_ggml.cpp'
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)


def replace_regex_once(pattern: str, replacement: str, label: str) -> None:
    global text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one regex match, found {count}')
    text = updated


replace_once(
    '''#include "ggml.h"\n#include "ggml-cpu.h"\n''',
    '''#include "ggml.h"\n#include "ggml-backend.h"\n#include "ggml-opencl.h"\n#include "ggml-cpu.h"\n''',
    'OpenCL acoustic includes',
)

linear_class = r'''class GgmlLinear {
public:
    GgmlLinear() = default;
    ~GgmlLinear() { release(); }

    void initialize(ggml_backend_t backend,
                    const float* weight,
                    int out_dim,
                    int in_dim,
                    int n_threads,
                    bool allow_f16 = false) {
        release();
        (void)n_threads;
        backend_ = backend;
        if (!backend_) {
            throw std::runtime_error("OpenCL acoustic linear received a null backend.");
        }

        in_dim_ = in_dim;
        out_dim_ = out_dim;
        use_f16_ = allow_f16;
        const ggml_type weight_type = use_f16_ ? GGML_TYPE_F16 : GGML_TYPE_F32;

        // With no_alloc=true the context stores only tensor/graph metadata. The
        // actual weights, input, intermediates and output are allocated once in
        // an OpenCL backend buffer and remain resident for the engine lifetime.
        ggml_init_params params = {
            /* .mem_size   = */ 1024 * 1024,
            /* .mem_buffer = */ nullptr,
            /* .no_alloc   = */ true,
        };
        ctx_ = ggml_init(params);
        if (!ctx_) {
            throw std::runtime_error("Failed to initialize metadata context for OpenCL acoustic linear.");
        }

        weight_ = ggml_new_tensor_2d(ctx_, weight_type, in_dim_, out_dim_);
        input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, in_dim_);
        output_ = ggml_mul_mat(ctx_, weight_, input_);
        ggml_set_input(input_);
        ggml_set_output(output_);
        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);

        buffer_ = ggml_backend_alloc_ctx_tensors(ctx_, backend_);
        if (!buffer_) {
            throw std::runtime_error("Failed to allocate OpenCL acoustic linear buffer.");
        }

        if (use_f16_) {
            std::vector<ggml_fp16_t> converted(static_cast<size_t>(in_dim_) * out_dim_);
            ggml_fp32_to_fp16_row(weight, converted.data(), static_cast<int64_t>(converted.size()));
            ggml_backend_tensor_set(weight_, converted.data(), 0, converted.size() * sizeof(ggml_fp16_t));
        } else {
            ggml_backend_tensor_set(
                weight_, weight, 0, static_cast<size_t>(in_dim_) * out_dim_ * sizeof(float));
        }
    }

    void run(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(out_dim_));
        ggml_backend_tensor_set(input_, input, 0, static_cast<size_t>(in_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic linear graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output.data(), 0, static_cast<size_t>(out_dim_) * sizeof(float));
    }

    void shutdown() { release(); }

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
        weight_ = nullptr;
        input_ = nullptr;
        output_ = nullptr;
        graph_ = nullptr;
        in_dim_ = 0;
        out_dim_ = 0;
        use_f16_ = false;
    }

    ggml_backend_t backend_ = nullptr;
    ggml_backend_buffer_t buffer_ = nullptr;
    ggml_context* ctx_ = nullptr;
    ggml_tensor* weight_ = nullptr;
    ggml_tensor* input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
    int in_dim_ = 0;
    int out_dim_ = 0;
    bool use_f16_ = false;
};

class GgmlFfnOp'''

replace_regex_once(
    r'class GgmlLinear \{.*?\n\};\n\nclass GgmlFfnOp',
    linear_class,
    'replace CPU acoustic linear with OpenCL linear',
)

ffn_class = r'''class GgmlFfnOp {
public:
    GgmlFfnOp() = default;
    ~GgmlFfnOp() { release(); }

    void initialize(ggml_backend_t backend,
                    const float* gate_weight,
                    const float* up_weight,
                    const float* down_weight,
                    int hidden_dim,
                    int intermediate_dim,
                    int n_threads) {
        release();
        (void)n_threads;
        backend_ = backend;
        if (!backend_) {
            throw std::runtime_error("OpenCL acoustic FFN received a null backend.");
        }
        hidden_dim_ = hidden_dim;
        intermediate_dim_ = intermediate_dim;
        use_q8_ = env_flag_enabled("VIENEU_ACOUSTIC_Q8_FFN", true);
        const int64_t q8_block = ggml_blck_size(GGML_TYPE_Q8_0);
        if (use_q8_ && (hidden_dim_ % q8_block != 0 || intermediate_dim_ % q8_block != 0)) {
            use_q8_ = false;
        }

        const ggml_type weight_type = use_q8_ ? GGML_TYPE_Q8_0 : GGML_TYPE_F32;
        ggml_init_params params = {
            /* .mem_size   = */ 2 * 1024 * 1024,
            /* .mem_buffer = */ nullptr,
            /* .no_alloc   = */ true,
        };
        ctx_ = ggml_init(params);
        if (!ctx_) {
            throw std::runtime_error("Failed to initialize metadata context for OpenCL acoustic FFN.");
        }

        gate_weight_ = ggml_new_tensor_2d(ctx_, weight_type, hidden_dim_, intermediate_dim_);
        up_weight_ = ggml_new_tensor_2d(ctx_, weight_type, hidden_dim_, intermediate_dim_);
        down_weight_ = ggml_new_tensor_2d(ctx_, weight_type, intermediate_dim_, hidden_dim_);
        input_ = ggml_new_tensor_1d(ctx_, GGML_TYPE_F32, hidden_dim_);

        ggml_tensor* gate = ggml_mul_mat(ctx_, gate_weight_, input_);
        ggml_tensor* up = ggml_mul_mat(ctx_, up_weight_, input_);
        ggml_tensor* activated = ggml_silu(ctx_, gate);
        ggml_tensor* fused = ggml_mul(ctx_, activated, up);
        output_ = ggml_mul_mat(ctx_, down_weight_, fused);
        ggml_set_input(input_);
        ggml_set_output(output_);

        graph_ = ggml_new_graph(ctx_);
        ggml_build_forward_expand(graph_, output_);
        buffer_ = ggml_backend_alloc_ctx_tensors(ctx_, backend_);
        if (!buffer_) {
            throw std::runtime_error("Failed to allocate OpenCL acoustic FFN buffer.");
        }

        auto upload = [this, weight_type](ggml_tensor* tensor,
                                          const float* source,
                                          int64_t rows,
                                          int64_t elements_per_row) {
            if (weight_type == GGML_TYPE_Q8_0) {
                std::vector<uint8_t> quantized(ggml_nbytes(tensor));
                ggml_quantize_chunk(
                    GGML_TYPE_Q8_0,
                    source,
                    quantized.data(),
                    0,
                    rows,
                    elements_per_row,
                    nullptr);
                ggml_backend_tensor_set(tensor, quantized.data(), 0, quantized.size());
            } else {
                ggml_backend_tensor_set(tensor, source, 0, ggml_nbytes(tensor));
            }
        };
        upload(gate_weight_, gate_weight, intermediate_dim_, hidden_dim_);
        upload(up_weight_, up_weight, intermediate_dim_, hidden_dim_);
        upload(down_weight_, down_weight, hidden_dim_, intermediate_dim_);
    }

    void run(const float* input, std::vector<float>& output) {
        output.resize(static_cast<size_t>(hidden_dim_));
        ggml_backend_tensor_set(input_, input, 0, static_cast<size_t>(hidden_dim_) * sizeof(float));
        const ggml_status status = ggml_backend_graph_compute(backend_, graph_);
        if (status != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("OpenCL acoustic FFN graph compute failed.");
        }
        ggml_backend_tensor_get(output_, output.data(), 0, static_cast<size_t>(hidden_dim_) * sizeof(float));
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
        gate_weight_ = nullptr;
        up_weight_ = nullptr;
        down_weight_ = nullptr;
        input_ = nullptr;
        output_ = nullptr;
        graph_ = nullptr;
        hidden_dim_ = 0;
        intermediate_dim_ = 0;
        use_q8_ = false;
    }

    ggml_backend_t backend_ = nullptr;
    ggml_backend_buffer_t buffer_ = nullptr;
    ggml_context* ctx_ = nullptr;
    ggml_tensor* gate_weight_ = nullptr;
    ggml_tensor* up_weight_ = nullptr;
    ggml_tensor* down_weight_ = nullptr;
    ggml_tensor* input_ = nullptr;
    ggml_tensor* output_ = nullptr;
    ggml_cgraph* graph_ = nullptr;
    int hidden_dim_ = 0;
    int intermediate_dim_ = 0;
    bool use_q8_ = false;
};

} // namespace'''

replace_regex_once(
    r'class GgmlFfnOp \{.*?\n\};\n\n\} // namespace',
    ffn_class,
    'replace CPU acoustic FFN with OpenCL FFN',
)

replace_once(
    '''        backend = ggml_backend_cpu_init();
        if (!backend) {
            throw std::runtime_error("Failed to initialize GGML CPU backend.");
        }
        ggml_backend_cpu_set_n_threads(backend, get_ggml_threads(n_threads));
''',
    '''        backend = ggml_backend_opencl_init();
        if (!backend) {
            throw std::runtime_error("Failed to initialize GGML OpenCL backend for acoustic generation; CPU fallback is disabled.");
        }
        ggml_backend_dev_t device = ggml_backend_get_device(backend);
        std::cerr << "[V3NativeDiag] stage=acoustic.backend"
                  << " backend=\\\"" << (ggml_backend_name(backend) ? ggml_backend_name(backend) : "") << "\\\""
                  << " device=\\\"" << (device && ggml_backend_dev_description(device) ? ggml_backend_dev_description(device) : "") << "\\\""
                  << " mode=OpenCL-only\\n";
''',
    'initialize acoustic OpenCL backend',
)

replace_once(
    '''    ~Impl() {
        layer_ops.clear();
        if (backend) {
            ggml_backend_free(backend);
            backend = nullptr;
        }
    }
''',
    '''    ~Impl() {
        layer_ops.clear();
        audio_head_ops.clear();
        text_head_op.shutdown();
        if (backend) {
            ggml_backend_free(backend);
            backend = nullptr;
        }
    }
''',
    'release acoustic buffers before OpenCL backend',
)

replace_once(
    '''            std::cout << "[V3NativeDiag] stage=acoustic.linear_mode"
                      << " qkv=F16 o_proj=F16 ffn=Q8_0 heads=F32" << std::endl;
''',
    '''            std::cout << "[V3NativeDiag] stage=acoustic.linear_mode"
                      << " backend=OpenCL qkv=F16 o_proj=F16 ffn=Q8_0 heads=F32 cpu_fallback=0" << std::endl;
''',
    'OpenCL acoustic diagnostics mode',
)

path.write_text(text, encoding='utf-8')
print('Applied OpenCL-resident acoustic projection, FFN and sampling-head graphs')
