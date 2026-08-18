#!/usr/bin/env python3
'''Use full-F32 weights for every VieNeu acoustic hot-path graph on OpenCL.

FP16 made the Android implementation fast, but the submitted v16 waveform still
mispronounced the beginning of a six-word Vietnamese sentence. This diagnostic
quality build keeps QKV, O-projection, FFN and output heads on the Adreno OpenCL
backend while retaining the original F32 model weights. No CPU fallback is
introduced. The result establishes whether FP16 numerical drift is responsible
for the remaining pronunciation errors.
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
    '''        use_f16_ = allow_f16;
        const ggml_type weight_type = use_f16_ ? GGML_TYPE_F16 : GGML_TYPE_F32;
''',
    '''        (void)allow_f16;
        use_f16_ = false;
        const ggml_type weight_type = GGML_TYPE_F32;
''',
    'force F32 acoustic linear weights',
)


replace_once(
    '''        use_q8_ = env_flag_enabled("VIENEU_ACOUSTIC_Q8_FFN", true);
        const int64_t q8_block = ggml_blck_size(GGML_TYPE_Q8_0);
        if (use_q8_ && (hidden_dim_ % q8_block != 0 || intermediate_dim_ % q8_block != 0)) {
            use_q8_ = false;
        }

        const ggml_type weight_type = use_q8_ ? GGML_TYPE_Q8_0 : GGML_TYPE_F32;
''',
    '''        use_q8_ = false;
        const ggml_type weight_type = GGML_TYPE_F32;
''',
    'select F32 acoustic FFN weights',
)

replace_once(
    ''' backend=OpenCL qkv=F16 o_proj=F16 ffn=Q8_0 heads=F32 cpu_fallback=0''',
    ''' backend=OpenCL qkv=F32 o_proj=F32 ffn=F32 heads=F32 cpu_fallback=0''',
    'full-F32 quality diagnostics mode',
)

path.write_text(text, encoding='utf-8')
print('Applied full-F32 acoustic weights on OpenCL; CPU fallback remains disabled')
