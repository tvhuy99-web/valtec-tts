#!/usr/bin/env python3
'''Backport Qualcomm subgroup shuffle aliases across all embedded OpenCL kernels.

Adreno 732 exposes cl_qcom_subgroup_shuffle but not the KHR builtin names. The
previous patch covered only one flash-attention source, while another embedded
kernel still failed to compile. Patch every .cl source that calls
sub_group_shuffle_xor and lacks the Qualcomm alias.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_llama_opencl_qcom_shuffle.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
kernels = root / 'third_party/llama.cpp/ggml/src/ggml-opencl/kernels'
if not kernels.is_dir():
    raise RuntimeError(f'OpenCL kernel directory not found: {kernels}')

alias = '''
#if defined(cl_qcom_subgroup_shuffle) && !defined(cl_khr_subgroup_shuffle)
#pragma OPENCL EXTENSION cl_qcom_subgroup_shuffle : enable
#ifndef sub_group_shuffle_xor
#define sub_group_shuffle_xor(val, mask) qcom_sub_group_shuffle_xor((val), (mask), CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.0f)
#endif
#endif
'''

patched = []
for path in sorted(kernels.rglob('*.cl')):
    text = path.read_text(encoding='utf-8')
    if 'sub_group_shuffle_xor' not in text:
        continue
    if 'qcom_sub_group_shuffle_xor((val), (mask)' in text:
        continue

    # Put the compatibility preamble before kernel code. It is safe whether or
    # not the source already enables the KHR extension: the guard activates only
    # on Qualcomm-only drivers.
    insertion = 0
    if text.startswith('#pragma OPENCL EXTENSION cl_khr_fp16 : enable'):
        insertion = text.find('\n') + 1
    text = text[:insertion] + alias + text[insertion:]
    path.write_text(text, encoding='utf-8')
    patched.append(path.relative_to(kernels).as_posix())

if not patched:
    raise RuntimeError('No unpatched sub_group_shuffle_xor OpenCL kernels were found')

print('Patched Qualcomm subgroup shuffle aliases in: ' + ', '.join(patched))
