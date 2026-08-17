#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: instrument_vieneu_deep_diagnostics.py <vieneu-source-dir>")

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
    '''#include <iostream>\n#include <stdexcept>\n''',
    '''#include <iostream>\n#include <iomanip>\n#include <limits>\n#include <numeric>\n#include <sstream>\n#include <stdexcept>\n''',
    "diagnostic includes",
)

replace_once(
    '''bool contains_v3_emotion_token(const std::string& text) {\n''',
    r'''uint64_t diag_fnv1a64(const void* data, size_t size) {
    const auto* bytes = static_cast<const unsigned char*>(data);
    uint64_t hash = 1469598103934665603ULL;
    for (size_t i = 0; i < size; ++i) {
        hash ^= static_cast<uint64_t>(bytes[i]);
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string diag_float_summary(const std::vector<float>& values, size_t preview = 8) {
    std::ostringstream out;
    out << std::setprecision(9);
    if (values.empty()) {
        out << "count=0";
        return out.str();
    }
    double sum = 0.0;
    double sum_sq = 0.0;
    double min_value = std::numeric_limits<double>::infinity();
    double max_value = -std::numeric_limits<double>::infinity();
    size_t finite = 0;
    for (float value : values) {
        if (!std::isfinite(value)) continue;
        const double v = static_cast<double>(value);
        sum += v;
        sum_sq += v * v;
        min_value = std::min(min_value, v);
        max_value = std::max(max_value, v);
        ++finite;
    }
    const double mean = finite ? sum / static_cast<double>(finite) : 0.0;
    const double rms = finite ? std::sqrt(sum_sq / static_cast<double>(finite)) : 0.0;
    out << "count=" << values.size()
        << " finite=" << finite
        << " min=" << (finite ? min_value : 0.0)
        << " max=" << (finite ? max_value : 0.0)
        << " mean=" << mean
        << " rms=" << rms
        << " l2=" << std::sqrt(sum_sq)
        << " fnv64=0x" << std::hex << diag_fnv1a64(values.data(), values.size() * sizeof(float)) << std::dec
        << " first=";
    const size_t count = std::min(preview, values.size());
    for (size_t i = 0; i < count; ++i) {
        if (i) out << ',';
        out << values[i];
    }
    return out.str();
}

std::string diag_code_summary(const std::vector<int64_t>& values, size_t preview = 32) {
    std::ostringstream out;
    out << "count=" << values.size()
        << " fnv64=0x" << std::hex
        << (values.empty() ? 0ULL : diag_fnv1a64(values.data(), values.size() * sizeof(int64_t)))
        << std::dec << " first=";
    const size_t count = std::min(preview, values.size());
    for (size_t i = 0; i < count; ++i) {
        if (i) out << ',';
        out << values[i];
    }
    return out.str();
}

bool contains_v3_emotion_token(const std::string& text) {
''',
    "diagnostic helpers",
)

replace_once(
    '''        config_ = assets_.config();\n''',
    '''        config_ = assets_.config();
        std::cerr << "[V3NativeDeep] init.model_dir=\"" << model_dir_ << "\""
                  << " hidden=" << config_.hidden_size
                  << " backbone_layers=" << config_.num_hidden_layers
                  << " acoustic_layers=" << config_.acoustic_num_hidden_layers
                  << " heads=" << config_.num_attention_heads
                  << " kv_heads=" << config_.num_key_value_heads
                  << " n_vq=" << config_.n_vq
                  << " text_vocab=" << config_.text_vocab_size
                  << " audio_vocab=" << config_.audio_vocab_size
                  << " speaker_dim=" << config_.speaker_embedding_dim
                  << " sample_rate=" << config_.sample_rate << "\n";
''',
    "config summary",
)

replace_once(
    '''    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;\n    ref_diag_ms("reference.read_wav", t_ref_stage);\n''',
    '''    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;
    std::cerr << "[V3NativeDeep] reference.loaded path=\"" << ref_audio_path
              << "\" sample_rate=" << wav.sample_rate
              << " " << diag_float_summary(wav.mono) << "\n";
    ref_diag_ms("reference.read_wav", t_ref_stage);
''',
    "reference loaded summary",
)

replace_once(
    '''    v3_trim_seconds(wav, 8.0);\n    ref_diag_ms("reference.trim_8s", t_ref_stage);\n''',
    '''    v3_trim_seconds(wav, 8.0);
    std::cerr << "[V3NativeDeep] reference.trimmed sample_rate=" << wav.sample_rate
              << " duration_ms=" << (wav.sample_rate > 0 ? wav.mono.size() * 1000.0 / wav.sample_rate : 0.0)
              << " " << diag_float_summary(wav.mono) << "\n";
    ref_diag_ms("reference.trim_8s", t_ref_stage);
''',
    "reference trimmed summary",
)

replace_once(
    '''        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;\n        ref_diag_ms("reference.speaker_embedding", t_ref_stage);\n''',
    '''        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;
        std::cerr << "[V3NativeDeep] reference.speaker_embedding " << diag_float_summary(speaker_emb) << "\n";
        ref_diag_ms("reference.speaker_embedding", t_ref_stage);
''',
    "speaker embedding summary",
)

replace_once(
    '''        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;\n        ref_diag_ms("reference.codec_encode", t_ref_stage);\n''',
    '''        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
        std::cerr << "[V3NativeDeep] reference.codes frames="
                  << (config_.n_vq > 0 ? ref_codes.size() / static_cast<size_t>(config_.n_vq) : 0)
                  << " " << diag_code_summary(ref_codes) << "\n";
        ref_diag_ms("reference.codec_encode", t_ref_stage);
''',
    "reference codes summary",
)

replace_once(
    '''    const std::vector<std::string> chunks = chunk_text_v3(params.text, params.max_chars);\n''',
    '''    std::cerr << "[V3NativeDeep] synth.params text_bytes=" << params.text.size()
              << " voice=\"" << params.voice_id << "\""
              << " ref=\"" << params.ref_audio_path << "\""
              << " style=\"" << params.style << "\""
              << " temperature=" << params.temperature
              << " top_k=" << params.top_k
              << " top_p=" << params.top_p
              << " repetition_penalty=" << params.repetition_penalty
              << " max_new_frames=" << params.max_new_frames
              << " max_chars=" << params.max_chars
              << " denoise_ref=" << (params.denoise_ref ? 1 : 0)
              << " use_ref_codes=" << (params.use_ref_codes ? 1 : 0)
              << " watermark=" << (params.apply_watermark ? 1 : 0) << "\n";
    const std::vector<std::string> chunks = chunk_text_v3(params.text, params.max_chars);
''',
    "effective synthesis params",
)

replace_once(
    '''    if (!assets_.compute_speaker_anchor(speaker_emb, speaker_anchor, error)) return false;\n    const int style_token_id = resolve_style_token(params.style);\n''',
    '''    if (!assets_.compute_speaker_anchor(speaker_emb, speaker_anchor, error)) return false;
    std::cerr << "[V3NativeDeep] speaker.anchor " << diag_float_summary(speaker_anchor)
              << " ref_code_count=" << ref_codes.size() << "\n";
    const int style_token_id = resolve_style_token(params.style);
    std::cerr << "[V3NativeDeep] style.token_id=" << style_token_id
              << " chunks=" << chunks.size() << "\n";
''',
    "speaker anchor summary",
)

replace_once(
    '''    const V3PromptRows rows = prompt_builder_->build_rows(phonemes, ref_codes, style_token_id);\n    prompt_builder_->embed_rows_into(rows, speaker_anchor, prompt_embeds_);\n''',
    '''    const V3PromptRows rows = prompt_builder_->build_rows(phonemes, ref_codes, style_token_id);
    prompt_builder_->embed_rows_into(rows, speaker_anchor, prompt_embeds_);
    std::cerr << "[V3NativeDeep] prompt rows=" << rows.rows
              << " cols=" << rows.cols
              << " row_codes=" << diag_code_summary(rows.data)
              << " embeds=" << diag_float_summary(prompt_embeds_) << "\n";
''',
    "prompt summary",
)

replace_once(
    '''        if (!backbone_.prefill(prompt_embeds_, synth_h)) {\n            error = "Semantic backbone prefill failed.";\n            return false;\n        }\n''',
    '''        if (!backbone_.prefill(prompt_embeds_, synth_h)) {
            error = "Semantic backbone prefill failed.";
            return false;
        }
        std::cerr << "[V3NativeDeep] backbone.prefill_hidden " << diag_float_summary(synth_h) << "\n";
''',
    "backbone prefill summary",
)

replace_once(
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;\n''',
    '''            if (!acoustic_->generate_frame(synth_h, params.temperature, params.top_k, params.top_p, params.repetition_penalty, history, codes, eos, error)) return false;
            std::cerr << "[V3NativeDeep] acoustic.frame index=" << t
                      << " eos=" << (eos ? 1 : 0)
                      << " input_hidden=" << diag_float_summary(synth_h, 4)
                      << " codes=" << diag_code_summary(codes, static_cast<size_t>(config_.n_vq)) << "\n";
''',
    "acoustic frame summary",
)

replace_once(
    '''            if (!backbone_.decode_step(synth_se, synth_h)) {\n                error = "Semantic backbone decode step failed.";\n                return false;\n            }\n''',
    '''            if (!backbone_.decode_step(synth_se, synth_h)) {
                error = "Semantic backbone decode step failed.";
                return false;
            }
            std::cerr << "[V3NativeDeep] backbone.decode_hidden frame=" << t
                      << " slot=" << diag_float_summary(synth_se, 4)
                      << " hidden=" << diag_float_summary(synth_h, 4) << "\n";
''',
    "backbone decode summary",
)

replace_once(
    '''        bool ok = codec_.decode(frames, static_cast<int64_t>(frames.size() / config_.n_vq), out_audio, error);\n''',
    '''        bool ok = codec_.decode(frames, static_cast<int64_t>(frames.size() / config_.n_vq), out_audio, error);
        std::cerr << "[V3NativeDeep] codec.output ok=" << (ok ? 1 : 0)
                  << " generated_frames=" << (config_.n_vq > 0 ? frames.size() / static_cast<size_t>(config_.n_vq) : 0)
                  << " audio=" << diag_float_summary(out_audio) << "\n";
''',
    "codec output summary",
)

path.write_text(text, encoding="utf-8")
print(f"Instrumented deep native diagnostics in {path}")
