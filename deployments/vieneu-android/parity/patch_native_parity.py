#!/usr/bin/env python3
"""Instrument a clean VieNeu-TTS.cpp checkout for numerical parity auditing.

The patch is diagnostic only. It forces an exact phoneme string supplied through
VIENEU_PARITY_PHONEMES and dumps prompt rows, speaker anchor, prompt embeddings,
backbone hidden states, generated acoustic codes and EOS decisions. No model
math is modified.
"""

from __future__ import annotations

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_parity.py <VieNeu-TTS.cpp checkout>")

root = pathlib.Path(sys.argv[1])
path = root / "src/vieneu/v3_native/vieneu_v3_native.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''bool contains_v3_emotion_token(const std::string& text) {
    return text.find("<|emotion_1|>") != std::string::npos ||
           text.find("<|emotion_2|>") != std::string::npos ||
           text.find("<|emotion_3|>") != std::string::npos;
}

} // namespace
''',
    '''bool contains_v3_emotion_token(const std::string& text) {
    return text.find("<|emotion_1|>") != std::string::npos ||
           text.find("<|emotion_2|>") != std::string::npos ||
           text.find("<|emotion_3|>") != std::string::npos;
}

std::string parity_path(const std::string& name) {
    const char* dir = std::getenv("VIENEU_PARITY_DIR");
    if (!dir || !*dir) return {};
    return join_paths(dir, name);
}

void dump_f32(const std::string& name, const float* values, size_t count) {
    const std::string path = parity_path(name);
    if (path.empty() || !values) return;
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(values),
              static_cast<std::streamsize>(count * sizeof(float)));
}

void dump_i64(const std::string& name, const int64_t* values, size_t count) {
    const std::string path = parity_path(name);
    if (path.empty() || !values) return;
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(values),
              static_cast<std::streamsize>(count * sizeof(int64_t)));
}

void dump_text(const std::string& name, const std::string& value, bool append = false) {
    const std::string path = parity_path(name);
    if (path.empty()) return;
    std::ofstream out(path, append ? std::ios::app : std::ios::trunc);
    out << value;
}

} // namespace
''',
    "parity dump helpers",
)

replace_once(
    '''    std::vector<float> speaker_anchor;
    if (!assets_.compute_speaker_anchor(speaker_emb, speaker_anchor, error)) return false;
    const int style_token_id = resolve_style_token(params.style);
''',
    '''    std::vector<float> speaker_anchor;
    if (!assets_.compute_speaker_anchor(speaker_emb, speaker_anchor, error)) return false;
    if (!speaker_anchor.empty()) {
        dump_f32("native_anchor.f32", speaker_anchor.data(), speaker_anchor.size());
    }
    const int style_token_id = resolve_style_token(params.style);
''',
    "speaker anchor dump",
)

replace_once(
    '''    out_audio.clear();
    const V3PromptRows rows = prompt_builder_->build_rows(phonemes, ref_codes, style_token_id);
    prompt_builder_->embed_rows_into(rows, speaker_anchor, prompt_embeds_);

    std::lock_guard<std::mutex> lock(run_mutex_);
''',
    '''    out_audio.clear();
    const V3PromptRows rows = prompt_builder_->build_rows(phonemes, ref_codes, style_token_id);
    prompt_builder_->embed_rows_into(rows, speaker_anchor, prompt_embeds_);
    dump_i64("native_rows.i64", rows.data.data(), rows.data.size());
    dump_f32("native_prompt.f32", prompt_embeds_.data(), prompt_embeds_.size());
    {
        std::ostringstream meta;
        meta << "{\\n"
             << "  \\\"rows\\\": " << rows.rows << ",\\n"
             << "  \\\"cols\\\": " << rows.cols << ",\\n"
             << "  \\\"hidden\\\": " << config_.hidden_size << ",\\n"
             << "  \\\"phonemes\\\": \\\"" << phonemes << "\\\"\\n"
             << "}\\n";
        dump_text("native_meta.json", meta.str());
        dump_text("native_codes.csv", "frame,eos,codes\\n");
    }

    std::lock_guard<std::mutex> lock(run_mutex_);
''',
    "prompt parity dumps",
)

replace_once(
    '''        if (!backbone_.prefill(prompt_embeds_, synth_h)) {
            error = "Semantic backbone prefill failed.";
            return false;
        }
        double prefill_ms = 0.0;
''',
    '''        if (!backbone_.prefill(prompt_embeds_, synth_h)) {
            error = "Semantic backbone prefill failed.";
            return false;
        }
        dump_f32("native_prefill_h.f32", synth_h.data(), synth_h.size());
        double prefill_ms = 0.0;
''',
    "prefill hidden dump",
)

replace_once(
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            if (benchmark_enabled) {
''',
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            {
                std::ostringstream line;
                line << t << ',' << (eos ? 1 : 0) << ',';
                for (size_t ch = 0; ch < codes.size(); ++ch) {
                    if (ch) line << ':';
                    line << codes[ch];
                }
                line << '\\n';
                dump_text("native_codes.csv", line.str(), true);
            }
            if (benchmark_enabled) {
''',
    "acoustic code dump",
)

replace_once(
    '''            if (!backbone_.decode_step(synth_se, synth_h)) {
                error = "Semantic backbone decode step failed.";
                return false;
            }
            if (benchmark_enabled) {
''',
    '''            if (!backbone_.decode_step(synth_se, synth_h)) {
                error = "Semantic backbone decode step failed.";
                return false;
            }
            {
                std::ostringstream name;
                name << "native_decode_h_" << std::setw(3) << std::setfill('0') << t << ".f32";
                dump_f32(name.str(), synth_h.data(), synth_h.size());
            }
            if (benchmark_enabled) {
''',
    "decode hidden dump",
)

replace_once(
    '''std::string VieneuV3NativeEngine::phonemize_for_v3(const std::string& text) const {
    return VieneuProfile::phonemize(text);
}
''',
    '''std::string VieneuV3NativeEngine::phonemize_for_v3(const std::string& text) const {
    const char* forced = std::getenv("VIENEU_PARITY_PHONEMES");
    if (forced && *forced) return std::string(forced);
    return VieneuProfile::phonemize(text);
}
''',
    "forced parity phonemes",
)

path.write_text(text, encoding="utf-8")
print("Instrumented native VieNeu prompt/backbone/acoustic parity dumps")
