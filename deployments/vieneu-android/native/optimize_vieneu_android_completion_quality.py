#!/usr/bin/env python3
"""Make VieNeu Android generation stable and retry implausibly early EOS output."""

from pathlib import Path
import sys

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


# The JNI source has already been expanded by optimize_vieneu_android_short_text.py.
replace_once(
    jni_path,
    '''#include <cstdlib>\n#include <ctime>\n''',
    '''#include <cstdlib>\n#include <cctype>\n#include <cstdint>\n#include <ctime>\n''',
    "completion quality includes",
)
replace_once(
    jni_path,
    '''bool is_no_eos_error(const std::string& value) {\n    return value.find("VIENEU_NO_EOS") != std::string::npos;\n}\n\n''',
    '''bool is_no_eos_error(const std::string& value) {\n    return value.find("VIENEU_NO_EOS") != std::string::npos;\n}\n\nbool is_early_eos_error(const std::string& value) {\n    return value.find("VIENEU_EARLY_EOS") != std::string::npos;\n}\n\nbool is_retryable_generation_error(const std::string& value) {\n    return is_no_eos_error(value) || is_early_eos_error(value);\n}\n\nint count_text_words(const std::string& text) {\n    int words = 0;\n    bool inside = false;\n    for (unsigned char c : text) {\n        const bool separator = std::isspace(c) || c == ',' || c == '.' || c == ';' ||\n                               c == ':' || c == '!' || c == '?' || c == '/' || c == '|';\n        if (separator) {\n            inside = false;\n        } else if (!inside) {\n            ++words;\n            inside = true;\n        }\n    }\n    return (std::max)(1, words);\n}\n\nuint32_t fnv1a_append(uint32_t hash, const char* data, size_t size) {\n    for (size_t i = 0; i < size; ++i) {\n        hash ^= static_cast<unsigned char>(data[i]);\n        hash *= 16777619u;\n    }\n    return hash;\n}\n\nuint32_t stable_request_seed(const VieneuV3NativeParams& params) {\n    uint32_t hash = 2166136261u;\n    const std::string fields[] = {params.text, params.voice_id, params.style, params.ref_audio_path};\n    for (const auto& field : fields) {\n        hash = fnv1a_append(hash, field.data(), field.size());\n        const char separator = '\\0';\n        hash = fnv1a_append(hash, &separator, 1);\n    }\n    if (!params.ref_audio_path.empty()) {\n        std::ifstream input(params.ref_audio_path, std::ios::binary);\n        char buffer[8192];\n        while (input) {\n            input.read(buffer, sizeof(buffer));\n            const std::streamsize count = input.gcount();\n            if (count > 0) hash = fnv1a_append(hash, buffer, static_cast<size_t>(count));\n        }\n    }\n    return hash == 0u ? 1u : hash;\n}\n\n''',
    "early EOS and stable seed helpers",
)

replace_once(
    jni_path,
    '''                   << ",\\\"retry_frame_cap\\\":450"\n                   << ",\\\"max_attempts\\\":2"\n                   << ",\\\"requires_eos\\\":true"\n                   << ",\\\"reference_prompt_policy\\\":\\\"full_cleaned_reference\\\""\n                   << ",\\\"sampling_seed_policy\\\":\\\"per_utterance\\\""\n''',
    '''                   << ",\\\"retry_frame_caps\\\":[300,300,450]"\n                   << ",\\\"max_attempts\\\":3"\n                   << ",\\\"requires_eos\\\":true"\n                   << ",\\\"rejects_early_eos\\\":true"\n                   << ",\\\"reference_prompt_policy\\\":\\\"full_cleaned_reference\\\""\n                   << ",\\\"sampling_seed_policy\\\":\\\"stable_request_retry_sequence\\\""\n''',
    "completion policy diagnostics",
)

replace_once(
    jni_path,
    '''        const int frame_caps[] = {300, 450};\n        for (int attempt = 0; attempt < 2; ++attempt) {\n            params.max_new_frames = frame_caps[attempt];\n            audio.clear();\n            error.clear();\n            native_event(\n                "synthesis.attempt.begin",\n                std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                ",\\\"max_attempts\\\":2,\\\"max_new_frames\\\":" +\n                std::to_string(params.max_new_frames) + "}");\n\n            if (g_engine->synthesize(params, audio, error)) {\n                synthesized = true;\n                successful_attempt = attempt + 1;\n                native_event(\n                    "synthesis.attempt.end",\n                    std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                    ",\\\"success\\\":true,\\\"stop_reason\\\":\\\"eos\\\"}");\n                break;\n            }\n\n            const bool retryable = is_no_eos_error(error);\n''',
    '''        const int frame_caps[] = {300, 300, 450};\n        const int word_count = count_text_words(params.text);\n        const int minimum_frames = (std::max)(8, word_count * 3 + 5);\n        const double minimum_audio_ms = static_cast<double>(minimum_frames) * 80.0;\n        const uint32_t request_seed = stable_request_seed(params);\n        for (int attempt = 0; attempt < 3; ++attempt) {\n            params.max_new_frames = frame_caps[attempt];\n            audio.clear();\n            error.clear();\n            const uint32_t attempt_seed = request_seed + 0x9e3779b9u * static_cast<uint32_t>(attempt + 1);\n            const std::string attempt_seed_text = std::to_string(attempt_seed == 0u ? 1u : attempt_seed);\n            setenv("VIENEU_V3_SAMPLING_SEED", attempt_seed_text.c_str(), 1);\n            native_event(\n                "synthesis.attempt.begin",\n                std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                ",\\\"max_attempts\\\":3,\\\"max_new_frames\\\":" +\n                std::to_string(params.max_new_frames) +\n                ",\\\"sampling_seed_base\\\":" + attempt_seed_text +\n                ",\\\"word_count\\\":" + std::to_string(word_count) +\n                ",\\\"minimum_audio_ms\\\":" + std::to_string(minimum_audio_ms) + "}");\n\n            if (g_engine->synthesize(params, audio, error)) {\n                const int sample_rate = g_engine->sample_rate();\n                const double candidate_audio_ms = sample_rate > 0\n                    ? static_cast<double>(audio.size()) * 1000.0 / static_cast<double>(sample_rate)\n                    : 0.0;\n                if (candidate_audio_ms + 0.5 < minimum_audio_ms) {\n                    std::ostringstream early;\n                    early << "VIENEU_EARLY_EOS audio_ms=" << candidate_audio_ms\n                          << " minimum_audio_ms=" << minimum_audio_ms\n                          << " words=" << word_count;\n                    error = early.str();\n                    audio.clear();\n                    native_event(\n                        "synthesis.attempt.end",\n                        std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                        ",\\\"success\\\":false,\\\"retryable\\\":true"\n                        ",\\\"stop_reason\\\":\\\"early_eos\\\""\n                        ",\\\"audio_duration_ms\\\":" + std::to_string(candidate_audio_ms) +\n                        ",\\\"minimum_audio_ms\\\":" + std::to_string(minimum_audio_ms) +\n                        ",\\\"error\\\":" + quote(error) + "}");\n                    continue;\n                }\n                synthesized = true;\n                successful_attempt = attempt + 1;\n                native_event(\n                    "synthesis.attempt.end",\n                    std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                    ",\\\"success\\\":true,\\\"stop_reason\\\":\\\"eos\\\""\n                    ",\\\"audio_duration_ms\\\":" + std::to_string(candidate_audio_ms) +\n                    ",\\\"minimum_audio_ms\\\":" + std::to_string(minimum_audio_ms) + "}");\n                break;\n            }\n\n            const bool retryable = is_retryable_generation_error(error);\n''',
    "stable retries and early EOS rejection",
)
replace_once(
    jni_path,
    '''        if (!synthesized) {\n            const auto wall_end = std::chrono::steady_clock::now();\n            const std::string final_error = is_no_eos_error(error)\n                ? "Model không phát tín hiệu kết thúc sau 2 lần thử; không lưu âm thanh bị cắt."\n                : (error.empty() ? "VieNeu synthesis failed." : error);\n''',
    '''        unsetenv("VIENEU_V3_SAMPLING_SEED");\n        if (!synthesized) {\n            const auto wall_end = std::chrono::steady_clock::now();\n            const std::string final_error = is_early_eos_error(error)\n                ? "Model kết thúc quá sớm sau 3 lần thử; không lưu âm thanh có nguy cơ nói sai hoặc thiếu câu."\n                : (is_no_eos_error(error)\n                    ? "Model không phát tín hiệu kết thúc sau 3 lần thử; không lưu âm thanh bị cắt."\n                    : (error.empty() ? "VieNeu synthesis failed." : error));\n''',
    "final completion error",
)
replace_once(
    jni_path,
    '''                 << ",\\\"attempts\\\":2"\n''',
    '''                 << ",\\\"attempts\\\":3"\n''',
    "failure attempt count",
)

required = {
    jni_path: (
        "VIENEU_EARLY_EOS",
        "stable_request_retry_sequence",
        "const int frame_caps[] = {300, 300, 450};",
        "is_retryable_generation_error",
        "minimum_audio_ms",
    ),
}
for path, fragments in required.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing completion-quality fragments {missing}")

print("Applied stable request seeds, three-stage retries and early-EOS rejection")
