#!/usr/bin/env python3
'''Backport the Qualcomm subgroup shuffle alias required by Adreno OpenCL.

The pinned llama.cpp revision enables cl_qcom_subgroup_shuffle but calls the KHR
builtin name. Adreno 732 exposes only the Qualcomm builtin, causing the flash
attention kernel to fail compilation. This is the exact compatibility mapping
used by newer llama.cpp revisions.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_llama_opencl_qcom_shuffle.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
path = root / 'third_party/llama.cpp/ggml/src/ggml-opencl/kernels/flash_attn_f32_q8_0.cl'
text = path.read_text(encoding='utf-8')
old = '''#elif defined(cl_qcom_subgroup_shuffle)
#pragma OPENCL EXTENSION cl_qcom_subgroup_shuffle : enable
#define HAS_SUBGROUP_SHUFFLE 1
#endif
'''
new = '''#elif defined(cl_qcom_subgroup_shuffle)
#pragma OPENCL EXTENSION cl_qcom_subgroup_shuffle : enable
#define HAS_SUBGROUP_SHUFFLE 1
// Adreno compilers that expose only cl_qcom_subgroup_shuffle do not declare the KHR
// name. Route the KHR-style call to the Qualcomm builtin used by this driver.
#define sub_group_shuffle_xor(val, mask) qcom_sub_group_shuffle_xor((val), (mask), CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.0f)
#endif
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f'Qualcomm subgroup shuffle anchor: expected one match, found {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('Backported Qualcomm subgroup shuffle alias for Adreno OpenCL')
