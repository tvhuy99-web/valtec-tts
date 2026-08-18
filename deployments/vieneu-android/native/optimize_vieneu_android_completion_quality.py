#!/usr/bin/env python3
"""Use stable sampling while accepting every real EOS immediately.

The previous duration heuristic rejected valid 18–21-frame Vietnamese sentences,
then repeated the whole expensive synthesis three times. Duration cannot prove
whether speech content is complete. This patch accepts any output that stopped
through the model's EOS token and retries only when generation reaches the safety
cap without EOS.
"""

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(
        "usage: optimize_vieneu_android_completion_quality.py <vieneu-jni.cpp>"
    )

jni_path = Path(sys.argv[1])


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    jni_path,
    '''#include <cstdlib>\n#include <ctime>\n''',
    '''#include <cstdlib>\n#include <cstdint>\n#include <ctime>\n''',
    "stable sampling include",
)
replace_once(
    jni_path,
    '''bool is_no_eos_error(const std::string& value) {\n    return value.find("VIENEU_NO_EOS") != std::string::npos;\n}\n\n''',
    '''bool is_no_eos_error(const std::string& value) {\n    return value.find("VIENEU_NO_EOS") != std::string::npos;\n}\n\nuint32_t fnv1a_append(uint32_t hash, const char* data, size_t size) {\n    for (size_t i = 0; i < size; ++i) {\n        hash ^= static_cast<unsigned char>(data[i]);\n        hash *= 16777619u;\n    }\n    return hash;\n}\n\nuint32_t stable_request_seed(const VieneuV3NativeParams& params) {\n    uint32_t hash = 2166136261u;\n    const std::string fields[] = {params.text, params.voice_id, params.style, params.ref_audio_path};\n    for (const auto& field : fields) {\n        hash = fnv1a_append(hash, field.data(), field.size());\n        const char separator = '\\0';\n        hash = fnv1a_append(hash, &separator, 1);\n    }\n    if (!params.ref_audio_path.empty()) {\n        std::ifstream input(params.ref_audio_path, std::ios::binary);\n        char buffer[8192];\n        while (input) {\n            input.read(buffer, sizeof(buffer));\n            const std::streamsize count = input.gcount();\n            if (count > 0) hash = fnv1a_append(hash, buffer, static_cast<size_t>(count));\n        }\n    }\n    return hash == 0u ? 1u : hash;\n}\n\n''',
    "stable request seed helper",
)
replace_once(
    jni_path,
    '''                   << ",\\\"retry_frame_cap\\\":450"\n                   << ",\\\"max_attempts\\\":2"\n                   << ",\\\"requires_eos\\\":true"\n                   << ",\\\"reference_prompt_policy\\\":\\\"full_cleaned_reference\\\""\n                   << ",\\\"sampling_seed_policy\\\":\\\"per_utterance\\\""\n''',
    '''                   << ",\\\"retry_frame_cap\\\":450"\n                   << ",\\\"max_attempts\\\":2"\n                   << ",\\\"requires_eos\\\":true"\n                   << ",\\\"rejects_early_eos\\\":false"\n                   << ",\\\"reference_prompt_policy\\\":\\\"full_cleaned_reference\\\""\n                   << ",\\\"sampling_seed_policy\\\":\\\"stable_request_no_eos_retry\\\""\n''',
    "completion policy diagnostics",
)
replace_once(
    jni_path,
    '''        const int frame_caps[] = {300, 450};\n        for (int attempt = 0; attempt < 2; ++attempt) {\n            params.max_new_frames = frame_caps[attempt];\n            audio.clear();\n            error.clear();\n            native_event(\n                "synthesis.attempt.begin",\n                std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                ",\\\"max_attempts\\\":2,\\\"max_new_frames\\\":" +\n                std::to_string(params.max_new_frames) + "}");\n\n            if (g_engine->synthesize(params, audio, error)) {\n                synthesized = true;\n                successful_attempt = attempt + 1;\n                native_event(\n                    "synthesis.attempt.end",\n                    std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                    ",\\\"success\\\":true,\\\"stop_reason\\\":\\\"eos\\\"}");\n                break;\n            }\n\n            const bool retryable = is_no_eos_error(error);\n''',
    '''        const int frame_caps[] = {300, 450};\n        const uint32_t request_seed = stable_request_seed(params);\n        for (int attempt = 0; attempt < 2; ++attempt) {\n            params.max_new_frames = frame_caps[attempt];\n            audio.clear();\n            error.clear();\n            const uint32_t attempt_seed = request_seed +\n                0x9e3779b9u * static_cast<uint32_t>(attempt + 1);\n            const std::string attempt_seed_text =\n                std::to_string(attempt_seed == 0u ? 1u : attempt_seed);\n            setenv("VIENEU_V3_SAMPLING_SEED", attempt_seed_text.c_str(), 1);\n            native_event(\n                "synthesis.attempt.begin",\n                std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                ",\\\"max_attempts\\\":2,\\\"max_new_frames\\\":" +\n                std::to_string(params.max_new_frames) +\n                ",\\\"sampling_seed_base\\\":" + attempt_seed_text + "}");\n\n            if (g_engine->synthesize(params, audio, error)) {\n                synthesized = true;\n                successful_attempt = attempt + 1;\n                native_event(\n                    "synthesis.attempt.end",\n                    std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                    ",\\\"success\\\":true,\\\"stop_reason\\\":\\\"eos\\\"}");\n                break;\n            }\n\n            const bool retryable = is_no_eos_error(error);\n''',
    "stable sampling and no-EOS-only retry",
)
replace_once(
    jni_path,
    '''        if (!synthesized) {\n''',
    '''        unsetenv("VIENEU_V3_SAMPLING_SEED");\n        if (!synthesized) {\n''',
    "clear forced sampling seed",
)

required = (
    "const int frame_caps[] = {300, 450};",
    "stable_request_no_eos_retry",
    "rejects_early_eos\\\":false",
    "sampling_seed_base",
    "unsetenv(\"VIENEU_V3_SAMPLING_SEED\")",
)
text = jni_path.read_text(encoding="utf-8")
missing = [fragment for fragment in required if fragment not in text]
forbidden = ("VIENEU_EARLY_EOS", "minimum_audio_ms", "word_count * 3")
found = [fragment for fragment in forbidden if fragment in text]
if missing or found:
    raise RuntimeError(
        f"generated JNI completion policy invalid: missing={missing}, forbidden={found}"
    )

print("Applied stable request seeds; real EOS accepted immediately; retry only on missing EOS")
