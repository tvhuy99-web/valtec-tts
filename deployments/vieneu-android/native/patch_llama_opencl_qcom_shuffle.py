#!/usr/bin/env python3


import ast
import pathlib
import re
import subprocess
import sys
import tempfile

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

    insertion = 0
    if text.startswith('#pragma OPENCL EXTENSION cl_khr_fp16 : enable'):
        insertion = text.find('\n') + 1
    text = text[:insertion] + alias + text[insertion:]
    path.write_text(text, encoding='utf-8')
    patched.append(path.relative_to(kernels).as_posix())

if not patched:
    raise RuntimeError('No unpatched sub_group_shuffle_xor OpenCL kernels were found')

print('Patched Qualcomm subgroup shuffle aliases in: ' + ', '.join(patched))


def rawify_replacement_literals(source: str, script_name: str) -> str:

    tree = ast.parse(source, filename=script_name)
    line_offsets = [0]
    for line in source.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    insertions = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != 'replace_once':
            continue
        if len(node.args) < 2:
            raise RuntimeError(f'{script_name}: replace_once call has fewer than two arguments')
        replacement = node.args[1]
        if not isinstance(replacement, ast.Constant) or not isinstance(replacement.value, str):
            raise RuntimeError(f'{script_name}: replace_once replacement is not a string literal')
        if replacement.end_lineno is None or replacement.end_col_offset is None:
            raise RuntimeError(f'{script_name}: replacement literal has no source range')

        start = line_offsets[replacement.lineno - 1] + replacement.col_offset
        end = line_offsets[replacement.end_lineno - 1] + replacement.end_col_offset
        token_source = source[start:end]
        match = re.match(r'(?i)([rubf]*)(\'\'\'|""")', token_source)
        if not match:
            raise RuntimeError(
                f'{script_name}:{replacement.lineno}: replacement must use a triple-quoted string literal')
        prefix = match.group(1).lower()
        delimiter = match.group(2)
        if not token_source.endswith(delimiter):
            raise RuntimeError(f'{script_name}:{replacement.lineno}: malformed replacement literal')
        body = token_source[match.end():-len(delimiter)]



        if 'r' not in prefix and '\n' in body:
            insertions.append(start)

    if not insertions:
        return source

    for offset in sorted(insertions, reverse=True):
        source = source[:offset] + 'r' + source[offset:]
    compile(source, script_name, 'exec')
    return source


def assert_no_multiline_cpp_string(path: pathlib.Path) -> None:

    text = path.read_text(encoding='utf-8')
    state = 'normal'
    escaped = False
    line = 1
    string_start_line = 0
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ''
        if state == 'normal':
            if ch == '/' and nxt == '/':
                state = 'line_comment'
                i += 1
            elif ch == '/' and nxt == '*':
                state = 'block_comment'
                i += 1
            elif ch == '"':
                state = 'string'
                escaped = False
                string_start_line = line
            elif ch == "'":
                state = 'char'
                escaped = False
        elif state == 'line_comment':
            if ch == '\n':
                state = 'normal'
        elif state == 'block_comment':
            if ch == '*' and nxt == '/':
                state = 'normal'
                i += 1
        elif state == 'string':
            if ch == '\n':
                raise RuntimeError(
                    f'{path}: newline inside C++ string literal opened on line {string_start_line}')
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                state = 'normal'
        elif state == 'char':
            if ch == '\n':
                raise RuntimeError(f'{path}: newline inside C++ character literal on line {line}')
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == "'":
                state = 'normal'
        if ch == '\n':
            line += 1
        i += 1

    if state in {'string', 'char', 'block_comment'}:
        raise RuntimeError(f'{path}: unterminated C++ lexical construct at end of file')


scripts_dir = pathlib.Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='vieneu-diag-instrumenters-') as temp_dir:
    temp_root = pathlib.Path(temp_dir)
    for script_name in (
        'instrument_vieneu_deep_diagnostics.py',
        'instrument_vieneu_reference_diagnostics.py',
    ):
        script_path = scripts_dir / script_name
        corrected = rawify_replacement_literals(
            script_path.read_text(encoding='utf-8'), script_name)
        corrected_path = temp_root / script_name
        corrected_path.write_text(corrected, encoding='utf-8')
        subprocess.run([sys.executable, str(corrected_path), str(root)], check=True)

subprocess.run(
    [sys.executable, str(scripts_dir / 'fix_vieneu_deep_diagnostics_config.py'), str(root)],
    check=True,
)

for cpp_path in (
    root / 'src/vieneu/v3_native/vieneu_v3_native.cpp',
    root / 'src/vieneu/v3_native/v3_native_reference.cpp',
):
    assert_no_multiline_cpp_string(cpp_path)

print('Applied final deep synthesis, backbone, acoustic, fbank, and speaker diagnostics')
