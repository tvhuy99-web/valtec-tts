#!/usr/bin/env python3


import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: finalize_vieneu_android_direct_wav.py <android-root>")

root = Path(sys.argv[1]).resolve()
activity = root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
native_kt = root / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
jni = root / "native/vieneu_jni.cpp"
gradle = root / "app/build.gradle.kts"
wav_writer = root / "app/src/main/java/com/vieneu/voiceclone/WavWriter.kt"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path: Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match in {path}, found {count}")
    path.write_text(updated, encoding="utf-8")


replace_once(
    native_kt,
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String): FloatArray?",
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String, outputWav: String): String?",
    "replace FloatArray JNI ABI",
)

replace_once(
    jni,
    "#include <chrono>\n#include <cstdio>\n",
    "#include <chrono>\n#include <cerrno>\n#include <cmath>\n#include <cstdint>\n#include <cstdio>\n#include <cstring>\n",
    "native WAV includes",
)

helper = r'''bool write_le16(FILE* file, std::uint16_t value) {
    const unsigned char bytes[2] = {
        static_cast<unsigned char>(value & 0xffu),
        static_cast<unsigned char>((value >> 8u) & 0xffu),
    };
    return std::fwrite(bytes, 1, sizeof(bytes), file) == sizeof(bytes);
}

bool write_le32(FILE* file, std::uint32_t value) {
    const unsigned char bytes[4] = {
        static_cast<unsigned char>(value & 0xffu),
        static_cast<unsigned char>((value >> 8u) & 0xffu),
        static_cast<unsigned char>((value >> 16u) & 0xffu),
        static_cast<unsigned char>((value >> 24u) & 0xffu),
    };
    return std::fwrite(bytes, 1, sizeof(bytes), file) == sizeof(bytes);
}

bool write_pcm16_wav_atomic(
        const std::string& output_path,
        const std::vector<float>& audio,
        int sample_rate,
        std::size_t& output_bytes,
        std::string& error) {
    output_bytes = 0;
    if (output_path.empty()) {
        error = "Output WAV path is empty.";
        return false;
    }
    if (sample_rate <= 0) {
        error = "Invalid sample rate for WAV output.";
        return false;
    }

    constexpr std::uint64_t kMaxDataBytes = 0xffffffffULL - 36ULL;
    const std::uint64_t data_bytes64 = static_cast<std::uint64_t>(audio.size()) * 2ULL;
    if (data_bytes64 > kMaxDataBytes) {
        error = "Generated audio is too large for RIFF/WAV PCM16.";
        return false;
    }

    const std::string temp_path = output_path + ".tmp";
    std::remove(temp_path.c_str());
    FILE* file = std::fopen(temp_path.c_str(), "wb");
    if (!file) {
        error = std::string("Unable to create WAV output: ") + std::strerror(errno);
        return false;
    }

    auto fail = [&](const std::string& message) {
        std::fclose(file);
        std::remove(temp_path.c_str());
        error = message;
        return false;
    };

    const std::uint32_t data_bytes = static_cast<std::uint32_t>(data_bytes64);
    if (std::fwrite("RIFF", 1, 4, file) != 4 ||
        !write_le32(file, 36u + data_bytes) ||
        std::fwrite("WAVE", 1, 4, file) != 4 ||
        std::fwrite("fmt ", 1, 4, file) != 4 ||
        !write_le32(file, 16u) ||
        !write_le16(file, 1u) ||
        !write_le16(file, 1u) ||
        !write_le32(file, static_cast<std::uint32_t>(sample_rate)) ||
        !write_le32(file, static_cast<std::uint32_t>(sample_rate) * 2u) ||
        !write_le16(file, 2u) ||
        !write_le16(file, 16u) ||
        std::fwrite("data", 1, 4, file) != 4 ||
        !write_le32(file, data_bytes)) {
        return fail("Failed while writing WAV header.");
    }

    constexpr std::size_t kChunkSamples = 16384;
    std::vector<unsigned char> pcm(kChunkSamples * 2u);
    std::size_t offset = 0;
    while (offset < audio.size()) {
        const std::size_t count = std::min(kChunkSamples, audio.size() - offset);
        for (std::size_t i = 0; i < count; ++i) {
            const float clipped = std::clamp(audio[offset + i], -1.0f, 1.0f);
            const int rounded = static_cast<int>(std::floor(clipped * 32767.0f + 0.5f));
            const std::int16_t sample = static_cast<std::int16_t>(std::clamp(rounded, -32767, 32767));
            const std::uint16_t bits = static_cast<std::uint16_t>(sample);
            pcm[i * 2u] = static_cast<unsigned char>(bits & 0xffu);
            pcm[i * 2u + 1u] = static_cast<unsigned char>((bits >> 8u) & 0xffu);
        }
        const std::size_t bytes = count * 2u;
        if (std::fwrite(pcm.data(), 1, bytes, file) != bytes) {
            return fail("Failed while writing WAV PCM data.");
        }
        offset += count;
    }

    if (std::fflush(file) != 0) return fail("Failed to flush WAV output.");
    if (::fsync(::fileno(file)) != 0) return fail("Failed to sync WAV output.");
    if (std::fclose(file) != 0) {
        std::remove(temp_path.c_str());
        error = "Failed to close WAV output.";
        return false;
    }
    if (std::rename(temp_path.c_str(), output_path.c_str()) != 0) {
        error = std::string("Unable to publish WAV output: ") + std::strerror(errno);
        std::remove(temp_path.c_str());
        return false;
    }

    output_bytes = static_cast<std::size_t>(44ULL + data_bytes64);
    return true;
}
'''

replace_once(
    jni,
    '}\n\nextern "C" JNIEXPORT jstring JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_configureDiagnostics',
    helper + '}\n\nextern "C" JNIEXPORT jstring JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_configureDiagnostics',
    "insert native WAV writer",
)
replace_once(
    jni,
    'extern "C" JNIEXPORT jfloatArray JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_synthesize(',
    'extern "C" JNIEXPORT jstring JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_synthesize(',
    "change synthesis JNI return type",
)
replace_once(
    jni,
    'jstring dialect) {',
    'jstring dialect, jstring output_wav) {',
    "add output WAV argument",
)

success_tail = r'''        const std::string output_path = from_jstring(env, output_wav);
        const int sample_rate = g_engine->sample_rate();
        std::size_t output_bytes = 0;
        std::string wav_error;
        const auto wav_write_start = std::chrono::steady_clock::now();
        if (!write_pcm16_wav_atomic(output_path, audio, sample_rate, output_bytes, wav_error)) {
            set_error(wav_error.empty() ? "Failed to write native WAV output." : wav_error);
            return nullptr;
        }
        const double wav_write_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - wav_write_start).count();
        set_error("");

        const auto wall_end = std::chrono::steady_clock::now();
        const double wall_ms = std::chrono::duration<double, std::milli>(wall_end - wall_start).count();
        const double audio_ms = sample_rate > 0
            ? static_cast<double>(audio.size()) * 1000.0 / static_cast<double>(sample_rate)
            : 0.0;
        std::ostringstream data;
        data << "{\"success\":true"
             << ",\"wall_ms\":" << wall_ms
             << ",\"process_cpu_ms\":" << (process_cpu_ms() - cpu_start)
             << ",\"rss_delta_bytes\":" << (rss_bytes() - rss_start)
             << ",\"samples\":" << audio.size()
             << ",\"sample_rate_hz\":" << sample_rate
             << ",\"audio_duration_ms\":" << audio_ms
             << ",\"rtf\":" << (audio_ms > 0.0 ? wall_ms / audio_ms : -1.0)
             << ",\"wav_write_ms\":" << wav_write_ms
             << ",\"output_bytes\":" << output_bytes
             << ",\"java_audio_buffer_bytes\":0"
             << ",\"audio_transport\":\"native_wav_pcm16\""
             << "}";
        native_event("synthesis.end", data.str());
        return to_jstring(env, output_path);
'''

regex_once(
    jni,
    r'''        if \(audio\.size\(\) > static_cast<size_t>\(std::numeric_limits<jsize>::max\(\)\)\) \{.*?        return result;\n''',
    success_tail,
    "replace FloatArray allocation/copy with native WAV write",
)

new_generation_block = r'''                val out = File(filesDir, "outputs/vieneu_${System.currentTimeMillis()}.wav")
                out.parentFile?.mkdirs()
                val synthSpan = Diagnostics.span(
                    "generation",
                    "native.synthesize",
                    mapOf(
                        "generation_id" to generationId,
                        "style" to style,
                        "voice_id" to voiceId,
                        "voice_mode" to voiceMode,
                        "generation_profile" to generationProfile,
                        "dialect" to dialect,
                        "clear_speech" to clearSpeech,
                        "use_ref_codes" to useRefCodes,
                        "deterministic" to deterministic,
                        "text_chars" to text.length,
                        "audio_transport" to "native_wav_pcm16"
                    )
                )
                val synthStart = SystemClock.elapsedRealtimeNanos()
                val writtenPath = VieNeuNative.synthesize(
                    text,
                    referencePath,
                    voiceId,
                    style,
                    useRefCodes,
                    deterministic,
                    dialect,
                    out.absolutePath
                ) ?: run {
                    val nativeError = VieNeuNative.lastError().ifBlank { "Native synthesis trả về null." }
                    synthSpan.end(false, mapOf("generation_id" to generationId, "error" to nativeError))
                    throw IllegalStateException(nativeError)
                }
                val synthWallMs = (SystemClock.elapsedRealtimeNanos() - synthStart) / 1_000_000.0
                if (writtenPath != out.absolutePath || !out.isFile || out.length() <= 44L) {
                    synthSpan.end(false, mapOf(
                        "generation_id" to generationId,
                        "error" to "native_wav_missing_or_invalid",
                        "returned_path" to writtenPath,
                        "bytes" to if (out.isFile) out.length() else 0L
                    ))
                    throw IllegalStateException("Native không tạo được WAV đầu ra hợp lệ.")
                }

                val wav = Diagnostics.wavInfo(out)
                val audioDurationMs = (wav["duration_ms"] as? Number)?.toDouble() ?: 0.0
                synthSpan.end(true, mapOf(
                    "generation_id" to generationId,
                    "wall_ms_direct" to synthWallMs,
                    "rtf" to if (audioDurationMs > 0.0) synthWallMs / audioDurationMs else null,
                    "output" to wav,
                    "audio_transport" to "native_wav_pcm16",
                    "java_audio_buffer_bytes" to 0
                ))
                outputFile = out
'''

regex_once(
    activity,
    r'''                val synthSpan = Diagnostics\.span\(.*?                outputFile = out\n''',
    new_generation_block,
    "replace Kotlin FloatArray and WAV writer pass",
)
replace_once(
    activity,
    '                        "audio" to audioStats,\n',
    '                        "audio_transport" to "native_wav_pcm16",\n                        "java_audio_buffer_bytes" to 0,\n',
    "remove remaining app audioStats reference",
)
replace_once(
    gradle,
    'versionCode = 21\n        versionName = "0.9.4-content-addressed-cache"',
    'versionCode = 22\n        versionName = "0.9.5-native-wav-canonical-f32"',
    "bump direct WAV build version",
)

if wav_writer.exists():
    wav_writer.unlink()

checks = {
    native_kt: ("outputWav: String): String?",),
    jni: ("jstring output_wav", "write_pcm16_wav_atomic", "native_wav_pcm16", "java_audio_buffer_bytes\\\":0"),
    activity: ('"audio_transport" to "native_wav_pcm16"', '"java_audio_buffer_bytes" to 0', "out.parentFile?.mkdirs()"),
    gradle: ("versionCode = 22", "0.9.5-native-wav-canonical-f32"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing direct-WAV fragments {missing}")

for path in (native_kt, activity):
    text = path.read_text(encoding="utf-8")
    forbidden = [fragment for fragment in ("FloatArray?", "WavWriter.writeMonoFloat", "audioStats") if fragment in text]
    if forbidden:
        raise RuntimeError(f"{path}: stale Java audio-buffer fragments remain {forbidden}")
if wav_writer.exists():
    raise RuntimeError("WavWriter.kt must be removed after native WAV transport is materialized")

print("Finalized Android audio transport: native PCM16 WAV output, zero Java FloatArray copies")
