#!/usr/bin/env python3


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
        (void)allow_f16;
        backend_ = backend;
        if (!backend_) {
            throw std::runtime_error("OpenCL acoustic linear received a null backend.");
        }

        in_dim_ = in_dim;
        out_dim_ = out_dim;

        ggml_init_params params = {
             1024 * 1024,
             nullptr,
             true,
        };
        ctx_ = ggml_init(params);
        if (!ctx_) {
            throw std::runtime_error("Failed to initialize metadata context for OpenCL acoustic linear.");
        }

        weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, in_dim_, out_dim_);
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

        ggml_backend_tensor_set(
            weight_, weight, 0, static_cast<size_t>(in_dim_) * out_dim_ * sizeof(float));
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
};

class GgmlFfnOp'''

replace_regex_once(
    r'class GgmlLinear \{.*?\n\};\n\nclass GgmlFfnOp',
    linear_class,
    'replace CPU acoustic linear with canonical F32 OpenCL linear',
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

        ggml_init_params params = {
             2 * 1024 * 1024,
             nullptr,
             true,
        };
        ctx_ = ggml_init(params);
        if (!ctx_) {
            throw std::runtime_error("Failed to initialize metadata context for OpenCL acoustic FFN.");
        }

        gate_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, hidden_dim_, intermediate_dim_);
        up_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, hidden_dim_, intermediate_dim_);
        down_weight_ = ggml_new_tensor_2d(ctx_, GGML_TYPE_F32, intermediate_dim_, hidden_dim_);
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

        ggml_backend_tensor_set(gate_weight_, gate_weight, 0, ggml_nbytes(gate_weight_));
        ggml_backend_tensor_set(up_weight_, up_weight, 0, ggml_nbytes(up_weight_));
        ggml_backend_tensor_set(down_weight_, down_weight, 0, ggml_nbytes(down_weight_));
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
};

} '''

replace_regex_once(
    r'class GgmlFfnOp \{.*?\n\};\n\n\} // namespace',
    ffn_class,
    'replace CPU acoustic FFN with canonical F32 OpenCL FFN',
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
                  << " precision=F32 qkv=F32 o_proj=F32 ffn=F32 heads=F32 cpu_fallback=0\\n";
''',
    'initialize canonical F32 acoustic OpenCL backend',
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

path.write_text(text, encoding='utf-8')

checks = (
    'GGML_TYPE_F32',
    'precision=F32 qkv=F32 o_proj=F32 ffn=F32 heads=F32',
    'ggml_backend_opencl_init()',
)
final_text = path.read_text(encoding='utf-8')
missing = [fragment for fragment in checks if fragment not in final_text]
if missing:
    raise RuntimeError(f'{path}: missing canonical OpenCL F32 fragments {missing}')

print('Applied canonical OpenCL F32 acoustic runtime without FP16/Q8 detour')
