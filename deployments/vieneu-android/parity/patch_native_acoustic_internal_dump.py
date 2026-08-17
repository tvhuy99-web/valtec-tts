#!/usr/bin/env python3
"""Dump native acoustic decoder hidden states around its incremental KV cache."""

from __future__ import annotations

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_acoustic_internal_dump.py <VieNeu-TTS.cpp checkout>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''#include <iostream>
#if defined(_OPENMP)
''',
    '''#include <iostream>
#include <fstream>
#include <iomanip>
#include <sstream>
#if defined(_OPENMP)
''',
    "acoustic dump includes",
)

replace_once(
    '''bool use_direct_linear_backend() {
    if (env_flag_enabled("VIENEU_ACOUSTIC_DIRECT_LINEAR", false)) {
        return true;
    }
    return !env_flag_enabled("VIENEU_ACOUSTIC_GGML_LINEAR", true);
}

using BenchClock = std::chrono::steady_clock;
''',
    '''bool use_direct_linear_backend() {
    if (env_flag_enabled("VIENEU_ACOUSTIC_DIRECT_LINEAR", false)) {
        return true;
    }
    return !env_flag_enabled("VIENEU_ACOUSTIC_GGML_LINEAR", true);
}

void dump_acoustic_f32(const std::string& name, const float* values, size_t count) {
    const char* directory = std::getenv("VIENEU_PARITY_DIR");
    if (!directory || !*directory || !values) return;
    std::string path(directory);
    if (!path.empty() && path.back() != '/' && path.back() != '\\\\') path.push_back('/');
    path += name;
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(values),
              static_cast<std::streamsize>(count * sizeof(float)));
}

using BenchClock = std::chrono::steady_clock;
''',
    "acoustic dump helper",
)

replace_once(
    '''        const int initial_positions[2] = {0, 1};
        impl_->cached_step(impl_->token, initial_positions, 2, impl_->hidden);
        std::copy(impl_->hidden.begin(), impl_->hidden.begin() + H, impl_->slot0.begin());
''',
    '''        const int initial_positions[2] = {0, 1};
        impl_->cached_step(impl_->token, initial_positions, 2, impl_->hidden);
        dump_acoustic_f32("native_acoustic_initial.f32", impl_->hidden.data(), impl_->hidden.size());
        std::copy(impl_->hidden.begin(), impl_->hidden.begin() + H, impl_->slot0.begin());
''',
    "initial acoustic hidden dump",
)

replace_once(
    '''            const int step_position = ch + 1;
            impl_->cached_step(impl_->token, &step_position, 1, impl_->hidden);
            codes.push_back(impl_->sample_channel(ch, impl_->hidden.data(), temperature, top_k, top_p, repetition_penalty, history));
''',
    '''            const int step_position = ch + 1;
            impl_->cached_step(impl_->token, &step_position, 1, impl_->hidden);
            {
                std::ostringstream name;
                name << "native_acoustic_step_" << std::setw(3) << std::setfill('0') << ch << ".f32";
                dump_acoustic_f32(name.str(), impl_->hidden.data(), impl_->hidden.size());
            }
            codes.push_back(impl_->sample_channel(ch, impl_->hidden.data(), temperature, top_k, top_p, repetition_penalty, history));
''',
    "incremental acoustic hidden dump",
)

path.write_text(text, encoding="utf-8")
print("Instrumented native acoustic initial and incremental hidden-state dumps")
