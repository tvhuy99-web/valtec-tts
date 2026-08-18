#!/usr/bin/env python3


import pathlib
import runpy
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_memory.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1]).resolve()
codec_path = root / 'src/vieneu/v3_native/v3_native_moss_codec.cpp'
reference_path = root / 'src/vieneu/v3_native/v3_native_reference.cpp'

codec = codec_path.read_text(encoding='utf-8')
reference = reference_path.read_text(encoding='utf-8')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


codec = replace_once(
    codec,
    '''        auto out = decode_session_->Run(
            Ort::RunOptions{nullptr},
            decode_in_ptrs_.data(),
''',
    '''        Ort::RunOptions decode_run_options;
        decode_run_options.AddConfigEntry("memory.enable_memory_arena_shrinkage", "cpu:0");
        auto out = decode_session_->Run(
            decode_run_options,
            decode_in_ptrs_.data(),
''',
    'MOSS decode arena shrinkage',
)

codec = replace_once(
    codec,
    '''        auto out = encode_session_->Run(
            Ort::RunOptions{nullptr},
            encode_in_ptrs_.data(),
''',
    '''        Ort::RunOptions encode_run_options;
        encode_run_options.AddConfigEntry("memory.enable_memory_arena_shrinkage", "cpu:0");
        auto out = encode_session_->Run(
            encode_run_options,
            encode_in_ptrs_.data(),
''',
    'MOSS encode arena shrinkage',
)

reference = replace_once(
    reference,
    '''        Ort::Value input = Ort::Value::CreateTensor<float>(mem, fbank.data(), fbank.size(), shape.data(), shape.size());
        auto out = session_->Run(Ort::RunOptions{nullptr}, input_ptrs_.data(), &input, 1, output_ptrs_.data(), output_ptrs_.size());
''',
    '''        Ort::Value input = Ort::Value::CreateTensor<float>(mem, fbank.data(), fbank.size(), shape.data(), shape.size());
        Ort::RunOptions run_options;
        run_options.AddConfigEntry("memory.enable_memory_arena_shrinkage", "cpu:0");
        auto out = session_->Run(run_options, input_ptrs_.data(), &input, 1, output_ptrs_.data(), output_ptrs_.size());
''',
    'speaker encoder arena shrinkage',
)

codec_path.write_text(codec, encoding='utf-8')
reference_path.write_text(reference, encoding='utf-8')
print('Applied Android ONNX CPU arena shrinkage to speaker encoder and MOSS codec')

patch = pathlib.Path(__file__).resolve().parent / 'optimize_vieneu_android_v092_source.py'
patch_text = patch.read_text(encoding='utf-8')
old_regex = 'updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)'
new_regex = 'updated, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.DOTALL)'
if patch_text.count(old_regex) != 1:
    raise RuntimeError('0.9.2 source patch regex helper shape changed')
patch_text = patch_text.replace(old_regex, new_regex, 1)




old_stat_patch = '''replace_once(
    engine,
    '#include <stdexcept>\\n\\n#include <nlohmann/json.hpp>\\n',
    '#include <stdexcept>\\n#include <sys/stat.h>\\n\\n#include <nlohmann/json.hpp>\\n',
    "speaker cache stat include",
)
'''
new_stat_check = '''engine_text = engine.read_text(encoding="utf-8")
if "#include <sys/stat.h>" not in engine_text:
    raise RuntimeError(f"{engine}: expected existing sys/stat.h include")
'''
if patch_text.count(old_stat_patch) != 1:
    raise RuntimeError('0.9.2 source stat-include patch shape changed')
patch_text = patch_text.replace(old_stat_patch, new_stat_check, 1)
patch.write_text(patch_text, encoding='utf-8')

saved_argv = sys.argv[:]
try:
    sys.argv = [str(patch), str(root)]
    runpy.run_path(str(patch), run_name='__main__')
finally:
    sys.argv = saved_argv
