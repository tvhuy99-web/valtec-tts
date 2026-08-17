#!/usr/bin/env python3
'''Replace the acoustic OpenCL FFN Q8 path with FP16 weights.

Q8_0 was fast, but autoregressive acoustic errors compound across frames and the
submitted longer phrase became unintelligible. Adreno 732 supports native FP16,
so keeping QKV, O-projection and FFN in FP16 is the best quality/speed balance.
Audio and EOS heads remain F32. No CPU fallback is introduced.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_acoustic_quality.py <vieneu-source-dir>')

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
    '''        use_q8_ = env_flag_enabled("VIENEU_ACOUSTIC_Q8_FFN", true);
        const int64_t q8_block = ggml_blck_size(GGML_TYPE_Q8_0);
        if (use_q8_ && (hidden_dim_ % q8_block != 0 || intermediate_dim_ % q8_block != 0)) {
            use_q8_ = false;
        }

        const ggml_type weight_type = use_q8_ ? GGML_TYPE_Q8_0 : GGML_TYPE_F32;
''',
    '''        // FP16 is intentionally used instead of Q8_0 here. Acoustic FFN
        // quantization errors are fed back autoregressively and caused long
        // phrases to drift into unrelated syllables. Adreno executes FP16
        // natively while preserving substantially more model precision.
        use_f16_ = true;
        const ggml_type weight_type = GGML_TYPE_F16;
''',
    'select FP16 acoustic FFN weights',
)

replace_once(
    '''        auto upload = [this, weight_type](ggml_tensor* tensor,
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
''',
    '''        auto upload = [](ggml_tensor* tensor,
                         const float* source,
                         int64_t rows,
                         int64_t elements_per_row) {
            const size_t element_count = static_cast<size_t>(rows) *
                                         static_cast<size_t>(elements_per_row);
            std::vector<ggml_fp16_t> converted(element_count);
            ggml_fp32_to_fp16_row(
                source,
                converted.data(),
                static_cast<int64_t>(element_count));
            ggml_backend_tensor_set(
                tensor,
                converted.data(),
                0,
                converted.size() * sizeof(ggml_fp16_t));
        };
''',
    'upload FP16 acoustic FFN weights',
)

replace_once(
    '''        use_q8_ = false;
''',
    '''        use_f16_ = false;
''',
    'release FP16 FFN state',
)

replace_once(
    '''    bool use_q8_ = false;
};

} // namespace
''',
    '''    bool use_f16_ = false;
};

} // namespace
''',
    'FP16 FFN state member',
)

replace_once(
    ''' backend=OpenCL qkv=F16 o_proj=F16 ffn=Q8_0 heads=F32 cpu_fallback=0''',
    ''' backend=OpenCL qkv=F16 o_proj=F16 ffn=F16 heads=F32 cpu_fallback=0''',
    'quality diagnostics mode',
)

path.write_text(text, encoding='utf-8')
print('Applied quality-preserving FP16 acoustic FFN on OpenCL')
