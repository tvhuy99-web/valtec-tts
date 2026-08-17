#!/usr/bin/env python3
'''Bound short-text generation without cutting normal Vietnamese phrase endings.

The earlier two-word cap of 14 frames stopped "Xin chào" before EOS and left a
non-zero waveform tail. This revision gives short phrases additional EOS grace
while still preventing multi-second repetition, then applies a tiny 10 ms tail
fade so a forced cap cannot produce an audible hard cut.
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

int short_text_frame_cap(const std::string& value) {
    const int codepoints = utf8_codepoint_count(value);
    const int words = whitespace_word_count(value);
    if (codepoints <= 0 || codepoints > 24 || words <= 0 || words > 5) return 0;

    // MOSS emits about 80 ms per frame. Six frames per word plus six frames of
    // onset/EOS grace gives a two-word phrase 18 frames (about 1.44 seconds),
    // while retaining a strict ceiling against long hesitation/repetition.
    return std::clamp(words * 6 + 6, 16, 36);
}

void apply_tail_fade(std::vector<float>& audio, int sample_rate) {
    if (audio.empty() || sample_rate <= 0) return;
    const size_t fade_samples = (std::min)(
        audio.size(),
        static_cast<size_t>((std::max)(1, sample_rate / 100))); // 10 ms
    const size_t start = audio.size() - fade_samples;
    for (size_t i = 0; i < fade_samples; ++i) {
        const float gain = static_cast<float>(fade_samples - i - 1) /
                           static_cast<float>(fade_samples);
        audio[start + i] *= gain;
    }
}

void redirect_native_console(const std::string& dir) {
''',
    'short text and tail-fade helpers',
)

replace_once(
    '''        params.apply_watermark = true;
        params.max_chars = 384;
        params.progress = [](const VieneuProgressEvent& event) { log_progress(event); };

        std::ostringstream start_data;
''',
    '''        params.apply_watermark = true;
        params.max_chars = 384;
        const int auto_frame_cap = short_text_frame_cap(params.text);
        if (auto_frame_cap > 0) {
            params.max_new_frames = (std::min)(params.max_new_frames, auto_frame_cap);
        }
        params.progress = [](const VieneuProgressEvent& event) { log_progress(event); };

        std::ostringstream start_data;
''',
    'apply short text frame cap',
)

replace_once(
    '''                   << ",\\\"max_new_frames\\\":" << params.max_new_frames
                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty
''',
    '''                   << ",\\\"max_new_frames\\\":" << params.max_new_frames
                   << ",\\\"auto_short_text_frame_cap\\\":" << auto_frame_cap
                   << ",\\\"text_codepoints\\\":" << utf8_codepoint_count(params.text)
                   << ",\\\"text_words\\\":" << whitespace_word_count(params.text)
                   << ",\\\"tail_fade_ms\\\":10"
                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty
''',
    'short text diagnostics fields',
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
print('Applied EOS-grace short-text cap and 10 ms tail fade to Android JNI')
