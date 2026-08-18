#!/usr/bin/env python3
"""Patch the pinned VieNeu core for persistent speaker cache and dialect choice."""

import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_v092_source.py <vieneu-source-dir>")

root = Path(sys.argv[1]).resolve()
header = root / "src/vieneu/v3_native/vieneu_v3_native.h"
engine = root / "src/vieneu/v3_native/vieneu_v3_native.cpp"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path: Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match in {path}, found {count}")
    path.write_text(updated, encoding="utf-8")


replace_once(
    header,
    '    std::string style = "tu_nhien";\n',
    '    std::string style = "tu_nhien";\n    std::string dialect = "north";\n',
    "native dialect parameter",
)
replace_once(
    header,
    '    std::string phonemize_for_v3(const std::string& text) const;\n',
    '    std::string phonemize_for_v3(const std::string& text, const std::string& dialect) const;\n',
    "dialect-aware phonemizer declaration",
)

replace_once(
    engine,
    '#include <cstring>\n#include <fstream>\n',
    '#include <cstring>\n#include <cstdint>\n#include <cstdio>\n#include <fstream>\n',
    "speaker cache includes",
)
replace_once(
    engine,
    '#include <stdexcept>\n\n#include <nlohmann/json.hpp>\n',
    '#include <stdexcept>\n#include <sys/stat.h>\n\n#include <nlohmann/json.hpp>\n',
    "speaker cache stat include",
)

helpers = r'''
constexpr uint32_t kSpeakerCacheVersion = 2;
constexpr char kSpeakerCacheMagic[8] = {'V', 'N', 'S', 'P', 'K', '0', '2', '\0'};

struct SpeakerCacheHeader {
    char magic[8];
    uint32_t version;
    uint32_t embedding_dim;
    uint64_t source_size;
    int64_t source_mtime;
    uint32_t denoise_enabled;
    uint32_t reserved;
};

bool source_file_identity(const std::string& path, uint64_t& size, int64_t& mtime) {
    struct stat st {};
    if (stat(path.c_str(), &st) != 0 || st.st_size <= 0) return false;
    size = static_cast<uint64_t>(st.st_size);
    mtime = static_cast<int64_t>(st.st_mtime);
    return true;
}

std::string speaker_cache_path(const std::string& ref_audio_path) {
    return ref_audio_path + ".vieneu-speaker-v2.bin";
}

bool load_speaker_embedding_cache(const std::string& ref_audio_path,
                                  bool denoise_enabled,
                                  size_t expected_dim,
                                  std::vector<float>& speaker_emb) {
    uint64_t source_size = 0;
    int64_t source_mtime = 0;
    if (!source_file_identity(ref_audio_path, source_size, source_mtime)) return false;

    const std::string cache_path = speaker_cache_path(ref_audio_path);
    std::ifstream input(cache_path, std::ios::binary);
    if (!input) return false;

    SpeakerCacheHeader cache_header {};
    input.read(reinterpret_cast<char*>(&cache_header), sizeof(cache_header));
    if (!input ||
        std::memcmp(cache_header.magic, kSpeakerCacheMagic, sizeof(cache_header.magic)) != 0 ||
        cache_header.version != kSpeakerCacheVersion ||
        cache_header.embedding_dim != expected_dim ||
        cache_header.source_size != source_size ||
        cache_header.source_mtime != source_mtime ||
        cache_header.denoise_enabled != static_cast<uint32_t>(denoise_enabled ? 1 : 0)) {
        return false;
    }

    std::vector<float> cached(expected_dim);
    input.read(reinterpret_cast<char*>(cached.data()),
               static_cast<std::streamsize>(cached.size() * sizeof(float)));
    if (!input || input.peek() != std::ifstream::traits_type::eof()) return false;
    for (float value : cached) {
        if (!std::isfinite(value)) return false;
    }
    speaker_emb = std::move(cached);
    std::cout << "[V3NativeCache] speaker_embedding=hit path=\"" << cache_path << "\"\n";
    return true;
}

void save_speaker_embedding_cache(const std::string& ref_audio_path,
                                  bool denoise_enabled,
                                  const std::vector<float>& speaker_emb) {
    uint64_t source_size = 0;
    int64_t source_mtime = 0;
    if (speaker_emb.empty() ||
        !source_file_identity(ref_audio_path, source_size, source_mtime)) return;

    const std::string cache_path = speaker_cache_path(ref_audio_path);
    const std::string temp_path = cache_path + ".tmp";
    SpeakerCacheHeader cache_header {};
    std::memcpy(cache_header.magic, kSpeakerCacheMagic, sizeof(cache_header.magic));
    cache_header.version = kSpeakerCacheVersion;
    cache_header.embedding_dim = static_cast<uint32_t>(speaker_emb.size());
    cache_header.source_size = source_size;
    cache_header.source_mtime = source_mtime;
    cache_header.denoise_enabled = static_cast<uint32_t>(denoise_enabled ? 1 : 0);

    {
        std::ofstream output(temp_path, std::ios::binary | std::ios::trunc);
        if (!output) return;
        output.write(reinterpret_cast<const char*>(&cache_header), sizeof(cache_header));
        output.write(reinterpret_cast<const char*>(speaker_emb.data()),
                     static_cast<std::streamsize>(speaker_emb.size() * sizeof(float)));
        output.flush();
        if (!output) {
            output.close();
            std::remove(temp_path.c_str());
            return;
        }
    }
    std::remove(cache_path.c_str());
    if (std::rename(temp_path.c_str(), cache_path.c_str()) == 0) {
        std::cout << "[V3NativeCache] speaker_embedding=write path=\"" << cache_path << "\"\n";
    } else {
        std::remove(temp_path.c_str());
    }
}

std::string apply_vietnamese_dialect(std::string phonemes, const std::string& dialect) {
    if (dialect != "south") return phonemes;

    // sea-g2p follows the Northern d/gi realization /z/. The Southern option
    // changes only token-initial /z/ to the palatal glide /j/ and leaves tones,
    // vowels and codas untouched.
    bool token_start = true;
    for (size_t i = 0; i < phonemes.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(phonemes[i]);
        if (std::isspace(c)) {
            token_start = true;
            continue;
        }
        if (token_start) {
            if (phonemes[i] == 'z') phonemes[i] = 'j';
            token_start = false;
        }
    }
    return phonemes;
}
'''
replace_once(
    engine,
    '''bool contains_v3_emotion_token(const std::string& text) {\n    return text.find("<|emotion_1|>") != std::string::npos ||\n           text.find("<|emotion_2|>") != std::string::npos ||\n           text.find("<|emotion_3|>") != std::string::npos;\n}\n\n} // namespace\n''',
    '''bool contains_v3_emotion_token(const std::string& text) {\n    return text.find("<|emotion_1|>") != std::string::npos ||\n           text.find("<|emotion_2|>") != std::string::npos ||\n           text.find("<|emotion_3|>") != std::string::npos;\n}\n''' + helpers + '''\n} // namespace\n''',
    "speaker cache and dialect helpers",
)

new_enroll = r'''bool VieneuV3NativeEngine::enroll_reference(const std::string& ref_audio_path,
                                            bool denoise_ref,
                                            bool use_ref_codes,
                                            std::vector<float>& speaker_emb,
                                            std::vector<int64_t>& ref_codes,
                                            std::string& error) {
    const bool diag = env_flag_enabled_local("VIENEU_V3_NATIVE_BENCHMARK");
    const auto t_ref_total = std::chrono::high_resolution_clock::now();
    auto ref_diag_ms = [diag](const char* stage, const std::chrono::high_resolution_clock::time_point& started) {
        if (!diag) return;
        const auto ended = std::chrono::high_resolution_clock::now();
        std::cout << "[V3NativeDiag] stage=" << stage
                  << " wall_ms=" << std::chrono::duration<double, std::milli>(ended - started).count() << "\n";
    };

    speaker_emb.clear();
    ref_codes.clear();
    const bool effective_denoise = denoise_ref && has_denoiser_;
    auto t_ref_stage = std::chrono::high_resolution_clock::now();
    const bool embedding_cache_hit = config_.use_speaker_embedding &&
        load_speaker_embedding_cache(
            ref_audio_path,
            effective_denoise,
            static_cast<size_t>(config_.speaker_embedding_dim),
            speaker_emb);
    ref_diag_ms(embedding_cache_hit ? "reference.speaker_cache_hit" : "reference.speaker_cache_miss", t_ref_stage);
    if (embedding_cache_hit && !use_ref_codes) {
        ref_diag_ms("reference.total", t_ref_total);
        return true;
    }

    V3NativeWaveform wav;
    t_ref_stage = std::chrono::high_resolution_clock::now();
    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;
    ref_diag_ms("reference.read_wav", t_ref_stage);
    t_ref_stage = std::chrono::high_resolution_clock::now();
    v3_trim_seconds(wav, 8.0);
    ref_diag_ms("reference.trim_8s", t_ref_stage);

    if (effective_denoise) {
        V3NativeWaveform clean;
        std::string warning;
        t_ref_stage = std::chrono::high_resolution_clock::now();
        if (denoiser_.denoise(wav, clean, warning)) {
            wav = std::move(clean);
        } else if (!warning.empty()) {
            std::cerr << "[V3NativeEngine] Warning: " << warning << "\n";
        }
        ref_diag_ms("reference.denoise", t_ref_stage);
    }

    if (config_.use_speaker_embedding && !embedding_cache_hit) {
        std::cout << "[V3NativeCache] speaker_embedding=miss path=\""
                  << speaker_cache_path(ref_audio_path) << "\"\n";
        t_ref_stage = std::chrono::high_resolution_clock::now();
        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;
        ref_diag_ms("reference.speaker_embedding", t_ref_stage);
        save_speaker_embedding_cache(ref_audio_path, effective_denoise, speaker_emb);
    }

    if (use_ref_codes) {
        t_ref_stage = std::chrono::high_resolution_clock::now();
        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        ref_diag_ms("reference.resample_48k", t_ref_stage);
        const int64_t frames = static_cast<int64_t>(mono48.size());
        std::vector<float> stereo(static_cast<size_t>(2 * frames), 0.0f);
        std::copy(mono48.begin(), mono48.end(), stereo.begin());
        std::copy(mono48.begin(), mono48.end(), stereo.begin() + frames);
        t_ref_stage = std::chrono::high_resolution_clock::now();
        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
        ref_diag_ms("reference.codec_encode", t_ref_stage);
    }
    ref_diag_ms("reference.total", t_ref_total);
    return true;
}

bool VieneuV3NativeEngine::encode_reference'''
regex_once(
    engine,
    r'''bool VieneuV3NativeEngine::enroll_reference\(.*?\n\}\n\nbool VieneuV3NativeEngine::encode_reference''',
    new_enroll,
    "persistent speaker embedding cache",
)

replace_once(
    engine,
    '        const std::string phonemes = phonemize_for_v3(chunks[i]);\n',
    '        const std::string phonemes = phonemize_for_v3(chunks[i], params.dialect);\n',
    "pass dialect to phonemizer",
)
replace_once(
    engine,
    '''std::string VieneuV3NativeEngine::phonemize_for_v3(const std::string& text) const {\n    return VieneuProfile::phonemize(text);\n}\n''',
    '''std::string VieneuV3NativeEngine::phonemize_for_v3(\n        const std::string& text, const std::string& dialect) const {\n    return apply_vietnamese_dialect(VieneuProfile::phonemize(text), dialect);\n}\n''',
    "dialect-aware phonemizer implementation",
)

checks = {
    header: ("std::string dialect = \"north\";", "phonemize_for_v3(const std::string& text, const std::string& dialect)"),
    engine: ("speaker_embedding=hit", "speaker_embedding=write", "apply_vietnamese_dialect", "params.dialect"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing 0.9.2 core fragments {missing}")

print("Applied persistent speaker embedding cache and Bắc/Nam phoneme selection")
