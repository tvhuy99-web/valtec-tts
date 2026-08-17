#!/usr/bin/env python3
'''Apply bounded Android generation while preserving VieNeu v3 quality defaults.

The adaptive frame budget prevents runaway generation, but voice cloning and
sampling remain aligned with the original v3 Turbo implementation: reference
codec conditioning enabled, temperature 0.8, top-k 25, top-p 0.95 and
repetition penalty 1.2. These settings are quality-critical for the 16-codebook
acoustic sampler and should not be replaced by low-temperature greedy sampling.
'''

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
    '''void redirect_native_console(const std::string& dir) {
''',
    '''int utf8_codepoint_count(const std::string& value) {
    int count = 0;
    for (unsigned char c : value) {
        if ((c & 0xC0u) != 0x80u) ++count;
    }
    return count;
}

int whitespace_word_count(const std::string& value) {
    int count = 0;
    bool in_word = false;
    for (unsigned char c : value) {
        const bool space = c == ' ' || c == '\\t' || c == '\\n' || c == '\\r';
        if (space) {
            in_word = false;
        } else if (!in_word) {
            in_word = true;
            ++count;
        }
    }
    return count;
}

int adaptive_text_frame_cap(const std::string& value) {
    const int codepoints = utf8_codepoint_count(value);
    const int words = whitespace_word_count(value);
    if (codepoints <= 0 || words <= 0) return 18;

    // A MOSS frame is about 80 ms. Keep enough room for natural onset, pauses
    // and EOS while preventing a short request from wandering to 191/300
    // frames. "Xin chào" gets 18 frames (~1.44 s) and the six-word quality
    // test gets 38 frames (~3.04 s).
    const int by_words = words * 5 + 8;
    const int by_chars = (codepoints * 8 + 9) / 10 + 8;
    return std::clamp((std::max)(by_words, by_chars), 18, 180);
}

void apply_tail_fade(std::vector<float>& audio, int sample_rate) {
    if (audio.empty() || sample_rate <= 0) return;
    const size_t fade_samples = (std::min)(
        audio.size(),
        static_cast<size_t>((std::max)(1, sample_rate / 50))); // 20 ms
    const size_t start = audio.size() - fade_samples;
    for (size_t i = 0; i < fade_samples; ++i) {
        const float gain = static_cast<float>(fade_samples - i - 1) /
                           static_cast<float>(fade_samples);
        audio[start + i] *= gain;
    }
}

void redirect_native_console(const std::string& dir) {
''',
    'adaptive frame and tail-fade helpers',
)

replace_once(
    '''        setenv("VIENEU_V3_NATIVE_BENCHMARK", "1", 1);
        setenv("VIENEU_V3_NATIVE_DEBUG_TAGS", "0", 1);
''',
    '''        setenv("VIENEU_V3_NATIVE_BENCHMARK", "1", 1);
        setenv("VIENEU_V3_NATIVE_DEBUG_TAGS", "1", 1);
        setenv("VIENEU_SEA_G2P_DEBUG", "1", 1);
''',
    'enable phoneme and sea-g2p diagnostics',
)

replace_once(
    '''        const std::string root = from_jstring(env, model_dir);
        if (root.empty()) {
            set_error("Model directory is empty.");
            return to_jstring(env, g_last_error);
        }
''',
    '''        const std::string root = from_jstring(env, model_dir);
        if (root.empty()) {
            set_error("Model directory is empty.");
            return to_jstring(env, g_last_error);
        }
        const std::string sea_g2p_dict = root + "/sea_g2p.bin";
        setenv("VIENEU_SEA_G2P_DICT", sea_g2p_dict.c_str(), 1);
''',
    'configure packaged sea-g2p dictionary',
)

replace_once(
    '''        params.denoise_ref = true;
        params.use_ref_codes = true;
        params.apply_watermark = true;
        params.max_chars = 384;
        params.progress = [](const VieneuProgressEvent& event) { log_progress(event); };

        std::ostringstream start_data;
''',
    '''        params.denoise_ref = true;
        // Keep reference acoustic codes. The v3 Turbo zero-shot path was
        // trained to condition on both the speaker embedding and reference
        // codec prompt; speaker embedding alone collapsed into silence on the
        // submitted six-word sample.
        params.use_ref_codes = true;
        params.apply_watermark = true;
        params.max_chars = 120;
        params.temperature = 0.8f;
        params.top_k = 25;
        params.top_p = 0.95f;
        params.repetition_penalty = 1.2f;
        const int auto_frame_cap = adaptive_text_frame_cap(params.text);
        params.max_new_frames = (std::min)(params.max_new_frames, auto_frame_cap);
        params.progress = [](const VieneuProgressEvent& event) { log_progress(event); };

        std::ostringstream start_data;
''',
    'restore upstream sampling and reference conditioning',
)

replace_once(
    '''                   << ",\\\"max_new_frames\\\":" << params.max_new_frames
                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty
''',
    '''                   << ",\\\"max_new_frames\\\":" << params.max_new_frames
                   << ",\\\"adaptive_frame_cap\\\":" << auto_frame_cap
                   << ",\\\"text_codepoints\\\":" << utf8_codepoint_count(params.text)
                   << ",\\\"text_words\\\":" << whitespace_word_count(params.text)
                   << ",\\\"tail_fade_ms\\\":20"
                   << ",\\\"sampling_profile\\\":\\\"upstream_quality\\\""
                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty
''',
    'quality diagnostics fields',
)

replace_once(
    '''                   << ",\\\"denoise_ref\\\":true"
                   << ",\\\"use_ref_codes\\\":true"
                   << ",\\\"apply_watermark\\\":true}";
''',
    '''                   << ",\\\"denoise_ref\\\":true"
                   << ",\\\"use_ref_codes\\\":true"
                   << ",\\\"apply_watermark\\\":true}";
''',
    'reference-code diagnostics',
)

replace_once(
    '''        if (audio.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) {
''',
    '''        apply_tail_fade(audio, g_engine->sample_rate());
        if (audio.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) {
''',
    'apply output tail fade',
)

path.write_text(text, encoding='utf-8')
print('Applied upstream-quality sampling, reference-code conditioning and bounded frames')
