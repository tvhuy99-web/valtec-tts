#!/usr/bin/env python3
'''Bound acoustic frames for very short Vietnamese text in the Android JNI.

A submitted 2.64 s "Xin chào" WAV contained 33 codec frames even though the
correct first phrase ended before 0.7 s. This patch applies a conservative cap
only to short inputs (<= 24 Unicode code points and <= 5 whitespace words),
preventing long hesitation/repetition tails and proportionally reducing the
most expensive autoregressive acoustic loop. Longer text remains unchanged.
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

    // VieNeu/MOSS emits about 80 ms per acoustic frame. Five frames per word
    // plus four frames of onset/ending allowance is deliberately generous for
    // short Vietnamese phrases while preventing multi-second repeated tails.
    return std::clamp(words * 5 + 4, 12, 29);
}

void redirect_native_console(const std::string& dir) {
''',
    'short text helpers',
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
                   << ",\\\"repetition_penalty\\\":" << params.repetition_penalty
''',
    'short text diagnostics fields',
)

path.write_text(text, encoding='utf-8')
print('Applied automatic short-text acoustic frame cap to Android JNI')
