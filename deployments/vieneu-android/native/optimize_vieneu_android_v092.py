#!/usr/bin/env python3
"""VieNeu Android 0.9.2 quality-of-life patch.

Adds:
- persistent speaker-embedding cache beside the app-private reference WAV,
- Northern/Southern pronunciation selection,
- optional clear-speech punctuation,
- bounded one-pass deterministic generation that remains usable for long text
  through native sentence/chunk splitting.

The patch runs after the existing Android voice/parity patch and fails closed if
any expected source fragment changes.
"""

from pathlib import Path
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: optimize_vieneu_android_v092.py <android-root> <vieneu-source-dir>"
    )

android_root = Path(sys.argv[1]).resolve()
source_root = Path(sys.argv[2]).resolve()

activity = android_root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
native_kt = android_root / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
layout = android_root / "app/src/main/res/layout/activity_main.xml"
jni = android_root / "native/vieneu_jni.cpp"
gradle = android_root / "app/build.gradle.kts"
header = source_root / "src/vieneu/v3_native/vieneu_v3_native.h"
engine = source_root / "src/vieneu/v3_native/vieneu_v3_native.cpp"


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


# ---------------------------------------------------------------------------
# Native engine: dialect selection and persistent speaker-embedding cache.
# ---------------------------------------------------------------------------
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
    '''#include <cstdlib>\n#include <cstring>\n#include <fstream>\n''',
    '''#include <cstdlib>\n#include <cstring>\n#include <cstdint>\n#include <cstdio>\n#include <fstream>\n''',
    "speaker cache includes",
)
replace_once(
    engine,
    '''#include <stdexcept>\n\n#include <nlohmann/json.hpp>\n''',
    '''#include <stdexcept>\n#include <sys/stat.h>\n\n#include <nlohmann/json.hpp>\n''',
    "speaker cache stat include",
)

cache_helpers = r'''
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

    SpeakerCacheHeader header {};
    input.read(reinterpret_cast<char*>(&header), sizeof(header));
    if (!input || std::memcmp(header.magic, kSpeakerCacheMagic, sizeof(header.magic)) != 0 ||
        header.version != kSpeakerCacheVersion ||
        header.embedding_dim != expected_dim ||
        header.source_size != source_size ||
        header.source_mtime != source_mtime ||
        header.denoise_enabled != static_cast<uint32_t>(denoise_enabled ? 1 : 0)) {
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
    SpeakerCacheHeader header {};
    std::memcpy(header.magic, kSpeakerCacheMagic, sizeof(header.magic));
    header.version = kSpeakerCacheVersion;
    header.embedding_dim = static_cast<uint32_t>(speaker_emb.size());
    header.source_size = source_size;
    header.source_mtime = source_mtime;
    header.denoise_enabled = static_cast<uint32_t>(denoise_enabled ? 1 : 0);

    {
        std::ofstream output(temp_path, std::ios::binary | std::ios::trunc);
        if (!output) return;
        output.write(reinterpret_cast<const char*>(&header), sizeof(header));
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

    // sea-g2p's Vietnamese profile follows the Northern d/gi realization /z/.
    // The Southern option changes token-initial /z/ to the palatal glide /j/.
    // This intentionally leaves vowels, codas and lexical tones untouched so
    // the model receives only phonemes already present in its tokenizer.
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
    '''bool contains_v3_emotion_token(const std::string& text) {\n    return text.find("<|emotion_1|>") != std::string::npos ||\n           text.find("<|emotion_2|>") != std::string::npos ||\n           text.find("<|emotion_3|>") != std::string::npos;\n}\n''' + cache_helpers + '''\n} // namespace\n''',
    "speaker cache and dialect helpers",
)

old_enroll = '''bool VieneuV3NativeEngine::enroll_reference(const std::string& ref_audio_path,
                                            bool denoise_ref,
                                            bool use_ref_codes,
                                            std::vector<float>& speaker_emb,
                                            std::vector<int64_t>& ref_codes,
                                            std::string& error) {
    speaker_emb.clear();
    ref_codes.clear();
    V3NativeWaveform wav;
    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;
    v3_trim_seconds(wav, 8.0);

    if (denoise_ref && has_denoiser_) {
        V3NativeWaveform clean;
        std::string warning;
        if (denoiser_.denoise(wav, clean, warning)) {
            wav = std::move(clean);
        } else if (!warning.empty()) {
            std::cerr << "[V3NativeEngine] Warning: " << warning << "\\n";
        }
    }

    if (config_.use_speaker_embedding) {
        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;
    }

    if (use_ref_codes) {
        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        const int64_t frames = static_cast<int64_t>(mono48.size());
        std::vector<float> stereo(static_cast<size_t>(2 * frames), 0.0f);
        std::copy(mono48.begin(), mono48.end(), stereo.begin());
        std::copy(mono48.begin(), mono48.end(), stereo.begin() + frames);
        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
    }
    return true;
}
'''
new_enroll = '''bool VieneuV3NativeEngine::enroll_reference(const std::string& ref_audio_path,
                                            bool denoise_ref,
                                            bool use_ref_codes,
                                            std::vector<float>& speaker_emb,
                                            std::vector<int64_t>& ref_codes,
                                            std::string& error) {
    speaker_emb.clear();
    ref_codes.clear();

    const bool effective_denoise = denoise_ref && has_denoiser_;
    const bool embedding_cache_hit = config_.use_speaker_embedding &&
        load_speaker_embedding_cache(
            ref_audio_path,
            effective_denoise,
            static_cast<size_t>(config_.speaker_embedding_dim),
            speaker_emb);
    if (embedding_cache_hit && !use_ref_codes) {
        return true;
    }

    V3NativeWaveform wav;
    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;
    v3_trim_seconds(wav, 8.0);

    if (effective_denoise) {
        V3NativeWaveform clean;
        std::string warning;
        if (denoiser_.denoise(wav, clean, warning)) {
            wav = std::move(clean);
        } else if (!warning.empty()) {
            std::cerr << "[V3NativeEngine] Warning: " << warning << "\\n";
        }
    }

    if (config_.use_speaker_embedding && !embedding_cache_hit) {
        std::cout << "[V3NativeCache] speaker_embedding=miss path=\""
                  << speaker_cache_path(ref_audio_path) << "\"\\n";
        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;
        save_speaker_embedding_cache(ref_audio_path, effective_denoise, speaker_emb);
    }

    if (use_ref_codes) {
        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        const int64_t frames = static_cast<int64_t>(mono48.size());
        std::vector<float> stereo(static_cast<size_t>(2 * frames), 0.0f);
        std::copy(mono48.begin(), mono48.end(), stereo.begin());
        std::copy(mono48.begin(), mono48.end(), stereo.begin() + frames);
        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
    }
    return true;
}
'''
replace_once(engine, old_enroll, new_enroll, "persistent speaker embedding cache")

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

# ---------------------------------------------------------------------------
# JNI: pass dialect and constrain deterministic mode to one 96-frame attempt.
# The engine still chunks long input through max_chars=96, so long passages run
# as multiple bounded deterministic chunks instead of one unbounded loop.
# ---------------------------------------------------------------------------
replace_once(
    native_kt,
    '''external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean): FloatArray?''',
    '''external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String): FloatArray?''',
    "extend Kotlin native API with dialect",
)
replace_once(
    jni,
    '''jstring text, jstring reference_wav, jstring voice_id, jstring style, jboolean use_ref_codes, jboolean deterministic) {''',
    '''jstring text, jstring reference_wav, jstring voice_id, jstring style, jboolean use_ref_codes, jboolean deterministic, jstring dialect) {''',
    "extend JNI API with dialect",
)
replace_once(
    jni,
    '''        params.style = from_jstring(env, style);\n''',
    '''        params.style = from_jstring(env, style);\n        params.dialect = from_jstring(env, dialect);\n        if (params.dialect != "south") params.dialect = "north";\n''',
    "read and validate dialect",
)
replace_once(
    jni,
    '''        if (deterministic_mode) {\n            params.temperature = 0.0f;\n            params.top_k = 1;\n            params.top_p = 1.0f;\n            params.repetition_penalty = 1.0f;\n        }\n''',
    '''        if (deterministic_mode) {\n            params.temperature = 0.0f;\n            params.top_k = 1;\n            params.top_p = 1.0f;\n            params.repetition_penalty = 1.2f;\n            params.max_chars = 96;\n        }\n''',
    "bounded deterministic sampling",
)
replace_once(
    jni,
    '''                   << ",\\\"deterministic_mode\\\":" << (deterministic_mode ? "true" : "false")\n                   << ",\\\"acoustic_backend\\\":\\\"opencl_f32\\\""\n''',
    '''                   << ",\\\"deterministic_mode\\\":" << (deterministic_mode ? "true" : "false")\n                   << ",\\\"dialect\\\":" << quote(params.dialect)\n                   << ",\\\"deterministic_frame_cap\\\":96"\n                   << ",\\\"deterministic_chunk_chars\\\":96"\n                   << ",\\\"speaker_embedding_cache\\\":\\\"persistent_v2\\\""\n                   << ",\\\"acoustic_backend\\\":\\\"opencl_f32\\\""\n''',
    "log dialect cache and deterministic policy",
)
replace_once(
    jni,
    '''        const int frame_caps[] = {300, 450};\n        const uint32_t request_seed = stable_request_seed(params);\n        for (int attempt = 0; attempt < 2; ++attempt) {\n            params.max_new_frames = frame_caps[attempt];\n''',
    '''        const int normal_frame_caps[] = {300, 450};\n        const int max_attempts = deterministic_mode ? 1 : 2;\n        const int deterministic_frame_cap = 96;\n        const uint32_t request_seed = stable_request_seed(params);\n        for (int attempt = 0; attempt < max_attempts; ++attempt) {\n            params.max_new_frames = deterministic_mode\n                ? deterministic_frame_cap\n                : normal_frame_caps[attempt];\n''',
    "one-pass deterministic loop",
)
replace_once(
    jni,
    '''                ",\\\"max_attempts\\\":2,\\\"max_new_frames\\\":" +\n                std::to_string(params.max_new_frames) +\n''',
    '''                ",\\\"max_attempts\\\":" + std::to_string(max_attempts) +\n                ",\\\"max_new_frames\\\":" + std::to_string(params.max_new_frames) +\n''',
    "dynamic attempt diagnostics",
)
replace_once(
    jni,
    '''            if (!retryable) break;\n''',
    '''            if (!retryable || attempt + 1 >= max_attempts) break;\n''',
    "stop deterministic after one attempt",
)
replace_once(
    jni,
    '''            const std::string final_error = is_no_eos_error(error)\n                ? "Model không phát tín hiệu kết thúc sau 2 lần thử; không lưu âm thanh bị cắt."\n                : (error.empty() ? "VieNeu synthesis failed." : error);\n''',
    '''            const std::string final_error = is_no_eos_error(error)\n                ? (deterministic_mode\n                    ? "Deterministic không phát EOS trong 96 khung của một đoạn; hãy rút ngắn câu đang báo lỗi."\n                    : "Model không phát tín hiệu kết thúc sau 2 lần thử; không lưu âm thanh bị cắt.")\n                : (error.empty() ? "VieNeu synthesis failed." : error);\n''',
    "deterministic no-EOS error",
)
replace_once(
    jni,
    '''                 << ",\\\"attempts\\\":2"\n''',
    '''                 << ",\\\"attempts\\\":" << max_attempts\n''',
    "dynamic failure attempts",
)

# ---------------------------------------------------------------------------
# Android UI: dialect and clear-speech selectors.
# ---------------------------------------------------------------------------
replace_once(
    activity,
    '''    private lateinit var generationModeSpinner: Spinner\n    private lateinit var styleSpinner: Spinner''',
    '''    private lateinit var generationModeSpinner: Spinner\n    private lateinit var dialectSpinner: Spinner\n    private lateinit var claritySpinner: Spinner\n    private lateinit var styleSpinner: Spinner''',
    "declare pronunciation controls",
)
replace_once(
    activity,
    '''        generationModeSpinner = findViewById(R.id.generationModeSpinner)\n        styleSpinner = findViewById(R.id.styleSpinner)''',
    '''        generationModeSpinner = findViewById(R.id.generationModeSpinner)\n        dialectSpinner = findViewById(R.id.dialectSpinner)\n        claritySpinner = findViewById(R.id.claritySpinner)\n        styleSpinner = findViewById(R.id.styleSpinner)''',
    "bind pronunciation controls",
)
replace_once(
    activity,
    '''        generationModeSpinner.adapter = ArrayAdapter(\n            this,\n            android.R.layout.simple_spinner_dropdown_item,\n            arrayOf(\n                "Ổn định (khuyến nghị)",\n                "Bám sát mẫu (mã tham chiếu)",\n                "Kiểm chuẩn deterministic"\n            )\n        )\n        voiceSpinner.onItemSelectedListener''',
    '''        generationModeSpinner.adapter = ArrayAdapter(\n            this,\n            android.R.layout.simple_spinner_dropdown_item,\n            arrayOf(\n                "Ổn định (khuyến nghị)",\n                "Bám sát mẫu (mã tham chiếu)",\n                "Kiểm chuẩn deterministic"\n            )\n        )\n        dialectSpinner.adapter = ArrayAdapter(\n            this,\n            android.R.layout.simple_spinner_dropdown_item,\n            arrayOf("Miền Bắc", "Miền Nam")\n        )\n        claritySpinner.adapter = ArrayAdapter(\n            this,\n            android.R.layout.simple_spinner_dropdown_item,\n            arrayOf("Tự nhiên", "Rõ chữ")\n        )\n        voiceSpinner.onItemSelectedListener''',
    "configure pronunciation controls",
)

regex_once(
    activity,
    r'''    private fun normalizeTextForTts\(raw: String\): String \{.*?\n    \}\n\n    private fun loadVoices''',
    r'''    private fun normalizeTextForTts(raw: String, clearSpeech: Boolean): String {
        var text = raw.trim().replace(Regex("\\s+"), " ")
        if (text.isBlank()) return text
        if (clearSpeech) text = applyClearSpeechPunctuation(text)
        if (text.last() in ".!?…") return text
        val lower = text.lowercase()
        val questionEndings = listOf(" không", " chưa", " à", " ư", " hả", " sao", " thế nào")
        return text + if (questionEndings.any { lower.endsWith(it) }) "?" else "."
    }

    private fun applyClearSpeechPunctuation(raw: String): String {
        val introductory = Regex(
            "(?iu)\\b(dạo này|hôm nay|bây giờ|trước tiên|sau đó|tuy nhiên|vì vậy|do đó|nói chung)\\b(?![,.;:!?])"
        )
        val withIntroPauses = raw.replace(introductory, "\$1,")
        val words = withIntroPauses.split(Regex("\\s+")).filter { it.isNotBlank() }
        if (words.size <= 14) return withIntroPauses

        val result = ArrayList<String>(words.size)
        var wordsSincePause = 0
        for (word in words) {
            result += word
            wordsSincePause++
            val hasPause = word.lastOrNull()?.let { it in charArrayOf(',', '.', ';', ':', '!', '?', '…') } == true
            if (hasPause) {
                wordsSincePause = 0
            } else if (wordsSincePause >= 14) {
                result[result.lastIndex] = word + ","
                wordsSincePause = 0
            }
        }
        return result.joinToString(" ")
    }

    private fun loadVoices''',
    "clear-speech text normalization",
)
replace_once(
    activity,
    '''        refreshModelStatus()\n    }''',
    '''        refreshModelStatus()\n        restoreReferenceIfPresent()\n    }''',
    "restore persisted reference on startup",
)
replace_once(
    activity,
    '''    private fun importReference(uri: Uri) {''',
    '''    private fun restoreReferenceIfPresent() {
        val saved = File(filesDir, "references/reference.wav")
        if (!saved.isFile || !looksLikeWav(saved)) return
        referenceFile = saved
        referenceStatus.text = "Giọng mẫu: reference.wav đã lưu (${saved.length() / 1024} KB)"
        Diagnostics.log(
            "reference",
            "reference.restore",
            data = mapOf(
                "wav" to Diagnostics.wavInfo(saved),
                "speaker_cache_present" to File(saved.absolutePath + ".vieneu-speaker-v2.bin").isFile
            )
        )
    }

    private fun importReference(uri: Uri) {''',
    "restore persisted reference helper",
)
replace_once(
    activity,
    '''                val target = File(dir, "reference.wav")
                val copyStart = SystemClock.elapsedRealtimeNanos()''',
    '''                val target = File(dir, "reference.wav")
                File(target.absolutePath + ".vieneu-speaker-v2.bin").delete()
                val copyStart = SystemClock.elapsedRealtimeNanos()''',
    "invalidate speaker cache before replacing reference",
)

replace_once(
    activity,
    '''        val rawText = textInput.text.toString().trim()\n        val text = normalizeTextForTts(rawText)\n        val ref = referenceFile''',
    '''        val rawText = textInput.text.toString().trim()\n        val clearSpeech = claritySpinner.selectedItemPosition == 1\n        val text = normalizeTextForTts(rawText, clearSpeech)\n        val ref = referenceFile''',
    "apply clear speech",
)
replace_once(
    activity,
    '''        val generationMode = generationModeSpinner.selectedItemPosition\n        val useRefCodes = generationMode == 1''',
    '''        val generationMode = generationModeSpinner.selectedItemPosition\n        val dialect = if (dialectSpinner.selectedItemPosition == 1) "south" else "north"\n        val useRefCodes = generationMode == 1''',
    "resolve dialect",
)
replace_once(
    activity,
    '''                "generation_profile" to generationProfile,\n                "use_ref_codes" to useRefCodes,''',
    '''                "generation_profile" to generationProfile,\n                "dialect" to dialect,\n                "clear_speech" to clearSpeech,\n                "speaker_embedding_cache" to "persistent_v2",\n                "use_ref_codes" to useRefCodes,''',
    "log UI pronunciation settings",
)
replace_once(
    activity,
    '''mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "generation_profile" to generationProfile, "use_ref_codes" to useRefCodes, "deterministic" to deterministic, "text_chars" to text.length)''',
    '''mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "generation_profile" to generationProfile, "dialect" to dialect, "clear_speech" to clearSpeech, "use_ref_codes" to useRefCodes, "deterministic" to deterministic, "text_chars" to text.length)''',
    "log native pronunciation settings",
)
replace_once(
    activity,
    '''VieNeuNative.synthesize(text, referencePath, voiceId, style, useRefCodes, deterministic)''',
    '''VieNeuNative.synthesize(text, referencePath, voiceId, style, useRefCodes, deterministic, dialect)''',
    "pass dialect to native",
)

replace_once(
    layout,
    '''        <TextView\n            android:layout_width="match_parent"\n            android:layout_height="wrap_content"\n            android:layout_marginTop="5dp"\n            android:text="Ổn định chỉ dùng dấu giọng speaker embedding. Bám sát mẫu thêm mã âm thanh tham chiếu. Deterministic dùng greedy để tạo kết quả lặp lại."\n            android:textSize="12sp" />\n\n        <Button\n            android:id="@+id/generateButton"''',
    '''        <TextView\n            android:layout_width="match_parent"\n            android:layout_height="wrap_content"\n            android:layout_marginTop="5dp"\n            android:text="Ổn định dùng speaker embedding có cache. Deterministic chia đoạn, tối đa 96 khung mỗi đoạn và không chạy lại."\n            android:textSize="12sp" />\n\n        <TextView\n            android:layout_width="match_parent"\n            android:layout_height="wrap_content"\n            android:layout_marginTop="16dp"\n            android:text="Phát âm"\n            android:textStyle="bold" />\n\n        <Spinner\n            android:id="@+id/dialectSpinner"\n            android:layout_width="match_parent"\n            android:layout_height="wrap_content"\n            android:layout_marginTop="6dp" />\n\n        <Spinner\n            android:id="@+id/claritySpinner"\n            android:layout_width="match_parent"\n            android:layout_height="wrap_content"\n            android:layout_marginTop="6dp" />\n\n        <TextView\n            android:layout_width="match_parent"\n            android:layout_height="wrap_content"\n            android:layout_marginTop="5dp"\n            android:text="Miền Nam đổi âm đầu d/gi sang âm y. Rõ chữ thêm các nhịp nghỉ nhẹ cho câu dài."\n            android:textSize="12sp" />\n\n        <Button\n            android:id="@+id/generateButton"''',
    "add dialect and clarity UI",
)
replace_once(
    layout,
    'android:text="Bản nhanh: semantic và acoustic OpenCL F32; dùng giọng có sẵn hoặc clone offline"',
    'android:text="OpenCL F32 nhanh, cache giọng mẫu, phát âm Bắc/Nam và chế độ Rõ chữ"',
    "update app description",
)
replace_once(
    gradle,
    'versionCode = 18\n        versionName = "0.9.1-fast-opencl-eos"',
    'versionCode = 19\n        versionName = "0.9.2-cache-dialect-clear"',
    "bump Android 0.9.2 version",
)

checks = {
    header: ("std::string dialect = \"north\";", "phonemize_for_v3(const std::string& text, const std::string& dialect)"),
    engine: ("speaker_embedding=hit", "speaker_embedding=write", "apply_vietnamese_dialect", "params.dialect"),
    jni: ("deterministic_frame_cap = 96", "max_attempts = deterministic_mode ? 1 : 2", "persistent_v2", "params.dialect"),
    native_kt: ("dialect: String",),
    activity: ("dialectSpinner", "claritySpinner", "applyClearSpeechPunctuation", "restoreReferenceIfPresent", "persistent_v2"),
    layout: ("@+id/dialectSpinner", "@+id/claritySpinner", "Rõ chữ"),
    gradle: ("versionCode = 19", "0.9.2-cache-dialect-clear"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing 0.9.2 fragments {missing}")

forbidden = {
    jni: ("params.repetition_penalty = 1.0f", "const int frame_caps[] = {300, 450};"),
}
for path, fragments in forbidden.items():
    text = path.read_text(encoding="utf-8")
    found = [fragment for fragment in fragments if fragment in text]
    if found:
        raise RuntimeError(f"{path}: stale unsafe fragments remain {found}")

print(
    "Applied VieNeu Android 0.9.2: persistent speaker cache, Bắc/Nam pronunciation, "
    "clear speech, and one-pass 96-frame deterministic chunks"
)
