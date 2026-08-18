#!/usr/bin/env python3


import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android_short_text.py <vieneu-jni.cpp>')

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)


replace_once(
    '''void redirect_native_console(const std::string& dir) {\n''',
    '''bool is_no_eos_error(const std::string& value) {\n    return value.find("VIENEU_NO_EOS") != std::string::npos;\n}\n\nvoid apply_tail_fade(std::vector<float>& audio, int sample_rate) {\n    if (audio.empty() || sample_rate <= 0) return;\n    const size_t fade_samples = (std::min)(\n        audio.size(),\n        static_cast<size_t>((std::max)(1, sample_rate / 50))); ''',
    'EOS and tail-fade helpers',
)

replace_once(
    '''        setenv("VIENEU_V3_NATIVE_BENCHMARK", "1", 1);\n        setenv("VIENEU_V3_NATIVE_DEBUG_TAGS", "0", 1);\n''',
    '''        setenv("VIENEU_V3_NATIVE_BENCHMARK", "1", 1);\n        setenv("VIENEU_V3_NATIVE_DEBUG_TAGS", "1", 1);\n        setenv("VIENEU_SEA_G2P_DEBUG", "1", 1);\n''',
    'enable phoneme and sea-g2p diagnostics',
)

replace_once(
    '''        const std::string root = from_jstring(env, model_dir);\n        if (root.empty()) {\n            set_error("Model directory is empty.");\n            return to_jstring(env, g_last_error);\n        }\n''',
    '''        const std::string root = from_jstring(env, model_dir);\n        if (root.empty()) {\n            set_error("Model directory is empty.");\n            return to_jstring(env, g_last_error);\n        }\n        const std::string sea_g2p_dict = root + "/sea_g2p.bin";\n        setenv("VIENEU_SEA_G2P_DICT", sea_g2p_dict.c_str(), 1);\n''',
    'configure packaged sea-g2p dictionary',
)

replace_once(
    '''        params.denoise_ref = true;\n        params.use_ref_codes = true;\n        params.apply_watermark = true;\n        params.max_chars = 384;\n        params.progress = [](const VieneuProgressEvent& event) { log_progress(event); };\n\n        std::ostringstream start_data;\n''',
    '''        params.denoise_ref = true;\n        params.use_ref_codes = true;\n        params.apply_watermark = true;\n        params.max_chars = 120;\n        params.temperature = 0.8f;\n        params.top_k = 25;\n        params.top_p = 0.95f;\n        params.repetition_penalty = 1.2f;\n        ''',
    'restore upstream generation budget and quality settings',
)

replace_once(
    '''                   << ",\\\"max_new_frames\\\":" << params.max_new_frames\n                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty\n''',
    '''                   << ",\\\"max_new_frames\\\":" << params.max_new_frames\n                   << ",\\\"retry_frame_cap\\\":450"\n                   << ",\\\"max_attempts\\\":2"\n                   << ",\\\"requires_eos\\\":true"\n                   << ",\\\"reference_prompt_policy\\\":\\\"full_cleaned_reference\\\""\n                   << ",\\\"sampling_seed_policy\\\":\\\"per_utterance\\\""\n                   << ",\\\"tail_fade_ms\\\":20"\n                   << ",\\\"sampling_profile\\\":\\\"upstream_quality\\\""\n                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty\n''',
    'quality diagnostics fields',
)

replace_once(
    '''        std::vector<float> audio;\n        std::string error;\n        if (!g_engine->synthesize(params, audio, error)) {\n            const auto wall_end = std::chrono::steady_clock::now();\n            std::ostringstream data;\n            data << "{\\\"success\\\":false"\n                 << ",\\\"wall_ms\\\":" << std::chrono::duration<double, std::milli>(wall_end - wall_start).count()\n                 << ",\\\"process_cpu_ms\\\":" << (process_cpu_ms() - cpu_start)\n                 << ",\\\"rss_delta_bytes\\\":" << (rss_bytes() - rss_start)\n                 << ",\\\"error\\\":" << quote(error) << "}";\n            native_event("synthesis.end", data.str());\n            set_error(error.empty() ? "VieNeu synthesis failed." : error);\n            return nullptr;\n        }\n''',
    '''        std::vector<float> audio;\n        std::string error;\n        bool synthesized = false;\n        int successful_attempt = 0;\n        const int frame_caps+] = {300, 450};\n        for (int attempt = 0; attempt < 2; ++attempt) {\n            params.max_new_frames = frame_caps[attempt];\n            audio.clear();\n            error.clear();\n            native_event(\n                "synthesis.attempt.begin",\n                std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                ",\\\"max_attempts\\\":2,\\\"max_new_frames\\\":" +\n                std::to_string(params.max_new_frames) + "}");\n\n            if (g_engine->synthesize(params, audio, error)) {\n                synthesized = true;\n                successful_attempt = attempt + 1;\n                native_event(\n                    "synthesis.attempt.end",\n                    std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                    ",\\\"success\\\":true,\\\"stop_reason\\\":\\\"eos\\\"}");\n                break;\n            }\n\n            const bool retryable = is_no_eos_error(error);\n            native_event(\n                "synthesis.attempt.end",\n                std::string("{\\\"attempt\\\":") + std::to_string(attempt + 1) +\n                ",\\\"success\\\":false,\\\"retryable\\\":" +\n                (retryable ? "true" : "false") +\n                ",\\\"stop_reason\\\":" + quote(retryable ? "max_frames" : "error") +\n                ",\\\"error\\\":" + quote(error) + "}");\n            if (!retryable) break;\n        }\n\n        if (!synthesized) {\n            const auto wall_end = std::chrono::steady_clock::now();\n            const std::string final_error = is_no_eos_error(error)\n                ? "Model không phát tín hiệu kết thúc sau 2 lần thử; không lưu âm thanh bị cắt."\n                : (error.empty() ? "VieNeu synthesis failed." : error);\n            std::ostringstream data;\n            data << "{\\\"success\\\":false"\n                 << ",\\\"wall_ms\\\":" << std::chrono::duration<double, std::milli>(wall_end - wall_start).count()\n                 << ",\\\"process_cpu_ms\\\":" << (process_cpu_ms() - cpu_start)\n                 << ",\\\"rss_delta_bytes\\\":" << (rss_bytes() - rss_start)\n                 << ",\\\"attempts\\\":2"\n                 << ",\\\"error\\\":" << quote(final_error) << "}";\n            native_event("synthesis.end", data.str());\n            set_error(final_error);\n            return nullptr;\n        }\n''',
    'retry no-EOS generation and reject truncated output',
)

replace_once(
    '''        if (audio.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) {\n''',
    '''        apply_tail_fade(audio, g_engine->sample_rate());\n        if (audio.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) {\n''',
    'apply output tail fade',
)

replace_once(
    '''             << ",\\\"audio_duration_ms\\\":" << audio_ms\n             << ",\\\"rtf\\\":" << (audio_ms > 0.0 ? wall_ms / audio_ms : -1.0)\n''',
    '''             << ",\\\"audio_duration_ms\\\":" << audio_ms\n             << ",\\\"attempts\\\":" << successful_attempt\n             << ",\\\"stop_reason\\\":\\\"eos\\\""\n             << ",\\\"rtf\\\":" << (audio_ms > 0.0 ? wall_ms / audio_ms : -1.0)\n''',
    'successful stop diagnostics',
)

path.write_text(text, encoding='utf-8')
print('Applied strict EOS completion, retry policy and upstream frame budget')
