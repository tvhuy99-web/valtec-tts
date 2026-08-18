#!/usr/bin/env python3

import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: optimize_vieneu_android_cache_v4.py <vieneu-source-dir> <android-root>"
    )

source_root = Path(sys.argv[1]).resolve()
android_root = Path(sys.argv[2]).resolve()
engine = source_root / "src/vieneu/v3_native/vieneu_v3_native.cpp"
activity = android_root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
jni = android_root / "native/vieneu_jni.cpp"
gradle = android_root / "app/build.gradle.kts"
model_manager = android_root / "app/src/main/java/com/vieneu/voiceclone/ModelManager.kt"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path: Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        pattern,
        lambda _match: replacement,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match in {path}, found {count}")
    path.write_text(updated, encoding="utf-8")


model_text = model_manager.read_text(encoding="utf-8")
package_match = re.search(r'EXPECTED_PACKAGE_ID\s*=\s*"([^"]+)"', model_text)
manifest_match = re.search(
    r'EXPECTED_MANIFEST_SHA256\s*=\s*"([0-9a-fA-F]{64})"',
    model_text,
)
if not package_match or not manifest_match:
    raise RuntimeError("Unable to derive model identity from ModelManager.kt")

package_id = package_match.group(1)
manifest_sha = manifest_match.group(1).lower()
cache_namespace = f"{package_id}-refv4-{manifest_sha}"
legacy_cache_namespace = f"{package_id}-refv3-{manifest_sha}"

replace_once(
    engine,
    "constexpr uint32_t kSpeakerCacheVersion = 2;",
    "constexpr uint32_t kSpeakerCacheVersion = 4;",
    "speaker cache version",
)
replace_once(
    engine,
    "constexpr char kSpeakerCacheMagic[8] = {'V', 'N', 'S', 'P', 'K', '0', '2', '\\0'};",
    "constexpr char kSpeakerCacheMagic[8] = {'V', 'N', 'S', 'P', 'K', '0', '4', '\\0'};",
    "speaker cache magic",
)
replace_once(
    engine,
    '    return ref_audio_path + ".vieneu-speaker-v2.bin";\n',
    '    return ref_audio_path + ".vieneu-speaker-v4.bin";\n',
    "speaker cache filename",
)

reference_code_helpers = r'''
constexpr uint32_t kReferenceCodesCacheVersion = 4;
constexpr char kReferenceCodesCacheMagic[8] = {'V', 'N', 'R', 'E', 'F', '0', '4', '\0'};

struct ReferenceCodesCacheHeader {
    char magic[8];
    uint32_t version;
    uint32_t n_vq;
    uint64_t source_size;
    int64_t source_mtime;
    uint32_t denoise_enabled;
    uint32_t reserved;
    uint64_t code_count;
};

std::string reference_codes_cache_path(const std::string& ref_audio_path) {
    return ref_audio_path + ".vieneu-refcodes-v4.bin";
}

bool load_reference_codes_cache(const std::string& ref_audio_path,
                                bool denoise_enabled,
                                int expected_n_vq,
                                std::vector<int64_t>& ref_codes) {
    uint64_t source_size = 0;
    int64_t source_mtime = 0;
    if (expected_n_vq <= 0 ||
        !source_file_identity(ref_audio_path, source_size, source_mtime)) return false;

    const std::string cache_path = reference_codes_cache_path(ref_audio_path);
    std::ifstream input(cache_path, std::ios::binary);
    if (!input) return false;

    ReferenceCodesCacheHeader cache_header {};
    input.read(reinterpret_cast<char*>(&cache_header), sizeof(cache_header));
    if (!input ||
        std::memcmp(cache_header.magic, kReferenceCodesCacheMagic, sizeof(cache_header.magic)) != 0 ||
        cache_header.version != kReferenceCodesCacheVersion ||
        cache_header.n_vq != static_cast<uint32_t>(expected_n_vq) ||
        cache_header.source_size != source_size ||
        cache_header.source_mtime != source_mtime ||
        cache_header.denoise_enabled != static_cast<uint32_t>(denoise_enabled ? 1 : 0) ||
        cache_header.code_count == 0 ||
        cache_header.code_count > 10000000ULL ||
        cache_header.code_count % static_cast<uint64_t>(expected_n_vq) != 0) {
        return false;
    }

    std::vector<int64_t> cached(static_cast<size_t>(cache_header.code_count));
    input.read(
        reinterpret_cast<char*>(cached.data()),
        static_cast<std::streamsize>(cached.size() * sizeof(int64_t)));
    if (!input || input.peek() != std::ifstream::traits_type::eof()) return false;
    ref_codes = std::move(cached);
    std::cout << "[V3NativeCache] reference_codes=hit path=\"" << cache_path << "\""
              << " values=" << ref_codes.size() << "\n";
    return true;
}

void save_reference_codes_cache(const std::string& ref_audio_path,
                                bool denoise_enabled,
                                int n_vq,
                                const std::vector<int64_t>& ref_codes) {
    uint64_t source_size = 0;
    int64_t source_mtime = 0;
    if (n_vq <= 0 ||
        ref_codes.empty() ||
        ref_codes.size() % static_cast<size_t>(n_vq) != 0 ||
        !source_file_identity(ref_audio_path, source_size, source_mtime)) return;

    const std::string cache_path = reference_codes_cache_path(ref_audio_path);
    const std::string temp_path = cache_path + ".tmp";
    ReferenceCodesCacheHeader cache_header {};
    std::memcpy(cache_header.magic, kReferenceCodesCacheMagic, sizeof(cache_header.magic));
    cache_header.version = kReferenceCodesCacheVersion;
    cache_header.n_vq = static_cast<uint32_t>(n_vq);
    cache_header.source_size = source_size;
    cache_header.source_mtime = source_mtime;
    cache_header.denoise_enabled = static_cast<uint32_t>(denoise_enabled ? 1 : 0);
    cache_header.code_count = static_cast<uint64_t>(ref_codes.size());

    {
        std::ofstream output(temp_path, std::ios::binary | std::ios::trunc);
        if (!output) return;
        output.write(reinterpret_cast<const char*>(&cache_header), sizeof(cache_header));
        output.write(
            reinterpret_cast<const char*>(ref_codes.data()),
            static_cast<std::streamsize>(ref_codes.size() * sizeof(int64_t)));
        output.flush();
        if (!output) {
            output.close();
            std::remove(temp_path.c_str());
            return;
        }
    }
    std::remove(cache_path.c_str());
    if (std::rename(temp_path.c_str(), cache_path.c_str()) == 0) {
        std::cout << "[V3NativeCache] reference_codes=write path=\"" << cache_path << "\""
                  << " values=" << ref_codes.size() << "\n";
    } else {
        std::remove(temp_path.c_str());
    }
}

'''
replace_once(
    engine,
    "std::string apply_vietnamese_dialect(std::string phonemes, const std::string& dialect) {",
    reference_code_helpers
    + "std::string apply_vietnamese_dialect(std::string phonemes, const std::string& dialect) {",
    "reference codes cache helpers",
)

replace_once(
    engine,
    '''    const bool embedding_cache_hit = config_.use_speaker_embedding &&
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
''',
    '''    const bool embedding_cache_hit = config_.use_speaker_embedding &&
        load_speaker_embedding_cache(
            ref_audio_path,
            effective_denoise,
            static_cast<size_t>(config_.speaker_embedding_dim),
            speaker_emb);
    ref_diag_ms(embedding_cache_hit ? "reference.speaker_cache_hit" : "reference.speaker_cache_miss", t_ref_stage);
    t_ref_stage = std::chrono::high_resolution_clock::now();
    const bool reference_codes_cache_hit = use_ref_codes &&
        load_reference_codes_cache(
            ref_audio_path,
            effective_denoise,
            config_.n_vq,
            ref_codes);
    if (use_ref_codes) {
        ref_diag_ms(
            reference_codes_cache_hit
                ? "reference.codes_cache_hit"
                : "reference.codes_cache_miss",
            t_ref_stage);
    }
    if ((!config_.use_speaker_embedding || embedding_cache_hit) &&
        (!use_ref_codes || reference_codes_cache_hit)) {
        ref_diag_ms("reference.total", t_ref_total);
        return true;
    }

    V3NativeWaveform wav;
''',
    "reference codes cache lookup",
)

replace_once(
    engine,
    '''    if (use_ref_codes) {
        t_ref_stage = std::chrono::high_resolution_clock::now();
        std::vector<float> mono48 = v3_resample_sinc(wav.mono, wav.sample_rate, sample_rate(), 6, 0.99, false, 0.0);
''',
    '''    if (use_ref_codes && !reference_codes_cache_hit) {
        std::cout << "[V3NativeCache] reference_codes=miss path=\""
                  << reference_codes_cache_path(ref_audio_path) << "\"\n";
        t_ref_stage = std::chrono::high_resolution_clock::now();
        std::vector<float> mono48 = v3_resample_sinc(wav.mono, wav.sample_rate, sample_rate(), 6, 0.99, false, 0.0);
''',
    "reference codes cache miss path",
)

replace_once(
    engine,
    '''        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
        ref_diag_ms("reference.codec_encode", t_ref_stage);
    }
    ref_diag_ms("reference.total", t_ref_total);
''',
    '''        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
        ref_diag_ms("reference.codec_encode", t_ref_stage);
        save_reference_codes_cache(
            ref_audio_path,
            effective_denoise,
            config_.n_vq,
            ref_codes);
    }
    ref_diag_ms("reference.total", t_ref_total);
''',
    "reference codes cache store",
)

replace_once(
    activity,
    "import java.util.UUID\n",
    "import java.security.MessageDigest\nimport java.util.UUID\n",
    "reference hashing import",
)

reference_block = f'''    private fun referenceStoreDir(): File =
        File(filesDir, "references/{cache_namespace}").apply {{ mkdirs() }}

    private fun referenceMarkerFile(): File =
        File(filesDir, "references/active-{cache_namespace}.txt")

    private fun sha256Hex(file: File): String {{
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered(1024 * 1024).use {{ input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {{
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }}
        }}
        return digest.digest().joinToString("") {{ byte ->
            "%02x".format(byte.toInt() and 0xff)
        }}
    }}

    private fun canonicalReferenceFile(hash: String): File =
        File(referenceStoreDir(), "$hash.wav")

    private fun writeActiveReferenceHash(hash: String) {{
        val marker = referenceMarkerFile()
        marker.parentFile?.mkdirs()
        val temp = File(marker.absolutePath + ".new")
        temp.writeText(hash + "\\n", Charsets.UTF_8)
        if (marker.exists() && !marker.delete()) {{
            temp.delete()
            throw IllegalStateException("Không thể cập nhật giọng mẫu đang dùng.")
        }}
        if (!temp.renameTo(marker)) {{
            temp.copyTo(marker, overwrite = true)
            temp.delete()
        }}
    }}

    private fun deleteReferenceCaches(wav: File) {{
        val suffixes = listOf(
            ".vieneu-speaker-v2.bin",
            ".vieneu-speaker-v3.bin",
            ".vieneu-speaker-v4.bin",
            ".vieneu-refcodes-v4.bin"
        )
        suffixes.forEach {{ suffix -> File(wav.absolutePath + suffix).delete() }}
    }}

    private fun pruneReferenceStore(active: File) {{
        val root = referenceStoreDir()
        val stale = root.listFiles()
            ?.filter {{ it.isFile && it.extension.equals("wav", ignoreCase = true) && it.absolutePath != active.absolutePath }}
            ?.sortedByDescending {{ it.lastModified() }}
            ?.drop(3)
            .orEmpty()
        stale.forEach {{ wav ->
            deleteReferenceCaches(wav)
            wav.delete()
        }}
    }}

    private fun referenceUsage(wav: Map<String, Any?>): Triple<Double?, Double?, String> {{
        val durationMs = (wav["duration_ms"] as? Number)?.toDouble()
        val usedMs = durationMs?.coerceAtMost(8000.0)
        val note = when {{
            durationMs == null -> ""
            durationMs > 8000.5 -> " • dùng 8,0 giây đầu"
            durationMs < 3000.0 -> " • mẫu ngắn dưới 3 giây"
            else -> ""
        }}
        return Triple(durationMs, usedMs, note)
    }}

    private fun migrateV3Reference(): File? {{
        val legacyRoot = File(filesDir, "references/{legacy_cache_namespace}")
        val legacyMarker = File(filesDir, "references/active-{legacy_cache_namespace}.txt")
        val hash = runCatching {{ legacyMarker.readText(Charsets.UTF_8).trim().lowercase() }}.getOrNull()
        if (hash == null || !hash.matches(Regex("[0-9a-f]{{64}}"))) return null
        val legacy = File(legacyRoot, "$hash.wav")
        if (!legacy.isFile || !looksLikeWav(legacy)) return null
        val target = canonicalReferenceFile(hash)
        if (!target.isFile) {{
            target.parentFile?.mkdirs()
            if (!legacy.renameTo(target)) {{
                legacy.copyTo(target, overwrite = true)
                legacy.delete()
            }}
        }} else {{
            legacy.delete()
        }}
        deleteReferenceCaches(target)
        legacyRoot.deleteRecursively()
        legacyMarker.delete()
        target.setLastModified(System.currentTimeMillis())
        writeActiveReferenceHash(hash)
        pruneReferenceStore(target)
        Diagnostics.log(
            "reference",
            "reference.cache_v4_migrated",
            data = mapOf(
                "reference_sha256" to hash,
                "from_cache_namespace" to "{legacy_cache_namespace}",
                "cache_namespace" to "{cache_namespace}",
                "wav" to Diagnostics.wavInfo(target)
            )
        )
        return target
    }}

    private fun migrateLegacyReference(): File? {{
        val legacy = File(filesDir, "references/reference.wav")
        if (!legacy.isFile || !looksLikeWav(legacy)) return null
        val hash = sha256Hex(legacy)
        val target = canonicalReferenceFile(hash)
        if (!target.isFile) {{
            target.parentFile?.mkdirs()
            if (!legacy.renameTo(target)) {{
                legacy.copyTo(target, overwrite = true)
                legacy.delete()
            }}
        }} else {{
            legacy.delete()
        }}
        deleteReferenceCaches(target)
        target.setLastModified(System.currentTimeMillis())
        writeActiveReferenceHash(hash)
        pruneReferenceStore(target)
        Diagnostics.log(
            "reference",
            "reference.cache_v4_migrated",
            data = mapOf(
                "reference_sha256" to hash,
                "from_cache_namespace" to "legacy_flat",
                "cache_namespace" to "{cache_namespace}",
                "wav" to Diagnostics.wavInfo(target)
            )
        )
        return target
    }}

    private fun restoreReferenceIfPresent() {{
        val marker = referenceMarkerFile()
        val hash = runCatching {{ marker.readText(Charsets.UTF_8).trim().lowercase() }}.getOrNull()
        val saved = if (hash != null && hash.matches(Regex("[0-9a-f]{{64}}"))) {{
            canonicalReferenceFile(hash).takeIf {{ it.isFile && looksLikeWav(it) }}
        }} else {{
            null
        }} ?: migrateV3Reference() ?: migrateLegacyReference() ?: return

        val referenceHash = saved.nameWithoutExtension
        saved.setLastModified(System.currentTimeMillis())
        referenceFile = saved
        val wav = Diagnostics.wavInfo(saved)
        val usage = referenceUsage(wav)
        referenceStatus.text =
            "Giọng mẫu: ${{referenceHash.take(12)}}… (${{saved.length() / 1024}} KB)${{usage.third}}"
        Diagnostics.log(
            "reference",
            "reference.restore",
            data = mapOf(
                "reference_sha256" to referenceHash,
                "cache_namespace" to "{cache_namespace}",
                "wav" to wav,
                "reference_duration_ms" to usage.first,
                "reference_used_ms" to usage.second,
                "reference_trimmed_to_8s" to ((usage.first ?: 0.0) > 8000.5),
                "speaker_cache_present" to File(saved.absolutePath + ".vieneu-speaker-v4.bin").isFile,
                "reference_codes_cache_present" to File(saved.absolutePath + ".vieneu-refcodes-v4.bin").isFile
            )
        )
    }}

    private fun importReference(uri: Uri) {{
        val span = Diagnostics.span(
            "reference",
            "reference.import",
            mapOf("authority" to uri.authority, "scheme" to uri.scheme)
        )
        executor.execute {{
            var temporary: File? = null
            try {{
                val dir = referenceStoreDir()
                val temp = File(dir, ".import-${{UUID.randomUUID()}}.wav")
                temporary = temp
                val copyStart = SystemClock.elapsedRealtimeNanos()
                contentResolver.openInputStream(uri)?.use {{ input ->
                    temp.outputStream().use {{ output -> input.copyTo(output, 1024 * 1024) }}
                }} ?: throw IllegalStateException("Không mở được tệp đã chọn.")
                val copyMs = (SystemClock.elapsedRealtimeNanos() - copyStart) / 1_000_000.0
                if (!looksLikeWav(temp)) {{
                    temp.delete()
                    temporary = null
                    throw IllegalArgumentException("Tệp đã chọn không phải WAV RIFF/WAVE hợp lệ.")
                }}

                val hashStart = SystemClock.elapsedRealtimeNanos()
                val referenceHash = sha256Hex(temp)
                val hashMs = (SystemClock.elapsedRealtimeNanos() - hashStart) / 1_000_000.0
                val target = canonicalReferenceFile(referenceHash)
                if (target.isFile) {{
                    if (!looksLikeWav(target)) {{
                        if (!target.delete()) throw IllegalStateException("Không thể thay giọng mẫu cache bị lỗi.")
                        if (!temp.renameTo(target)) {{
                            temp.copyTo(target, overwrite = true)
                            temp.delete()
                        }}
                    }} else {{
                        temp.delete()
                    }}
                }} else if (!temp.renameTo(target)) {{
                    temp.copyTo(target, overwrite = true)
                    temp.delete()
                }}
                temporary = null
                target.setLastModified(System.currentTimeMillis())
                writeActiveReferenceHash(referenceHash)
                pruneReferenceStore(target)
                referenceFile = target

                val name = queryDisplayName(uri) ?: "reference.wav"
                val wav = Diagnostics.wavInfo(target)
                val usage = referenceUsage(wav)
                Diagnostics.log(
                    "reference",
                    "reference.probe",
                    data = mapOf(
                        "display_name" to name,
                        "copy_ms" to copyMs,
                        "sha256_ms" to hashMs,
                        "reference_sha256" to referenceHash,
                        "cache_namespace" to "{cache_namespace}",
                        "speaker_cache_present" to File(target.absolutePath + ".vieneu-speaker-v4.bin").isFile,
                        "reference_codes_cache_present" to File(target.absolutePath + ".vieneu-refcodes-v4.bin").isFile,
                        "reference_duration_ms" to usage.first,
                        "reference_used_ms" to usage.second,
                        "reference_trimmed_to_8s" to ((usage.first ?: 0.0) > 8000.5),
                        "wav" to wav
                    )
                )
                span.end(
                    true,
                    mapOf(
                        "display_name" to name,
                        "copy_ms" to copyMs,
                        "sha256_ms" to hashMs,
                        "reference_sha256" to referenceHash,
                        "cache_namespace" to "{cache_namespace}",
                        "reference_duration_ms" to usage.first,
                        "reference_used_ms" to usage.second,
                        "reference_trimmed_to_8s" to ((usage.first ?: 0.0) > 8000.5),
                        "wav" to wav
                    )
                )
                runOnUiThread {{
                    referenceStatus.text =
                        "Giọng mẫu: $name (${{target.length() / 1024}} KB)${{usage.third}}"
                    statusText.text = when {{
                        (usage.first ?: 0.0) > 8000.5 ->
                            "Đã nhập giọng mẫu. VieNeu v3 Turbo sẽ dùng 8,0 giây đầu; cache mới được khóa theo SHA-256."
                        (usage.first ?: Double.MAX_VALUE) < 3000.0 ->
                            "Đã nhập giọng mẫu, nhưng mẫu dưới 3 giây có thể clone kém ổn định."
                        else ->
                            "Đã nhập giọng mẫu. Cache giọng và mã tham chiếu được khóa theo SHA-256 nội dung."
                    }}
                }}
            }} catch (t: Throwable) {{
                temporary?.delete()
                Diagnostics.error("reference", "reference.import.failure", t)
                span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
                runOnUiThread {{ statusText.text = "Lỗi giọng mẫu: ${{t.message}}" }}
            }}
        }}
    }}

    private fun exportOutput'''

regex_once(
    activity,
    r'''    private fun restoreReferenceIfPresent\(\) \{.*?\n    \}\n\n    private fun importReference\(uri: Uri\) \{.*?\n    \}\n\n    private fun exportOutput''',
    reference_block,
    "content-addressed Android reference storage v4",
)

activity_text = activity.read_text(encoding="utf-8")
activity_text = activity_text.replace(
    '"speaker_embedding_cache" to "persistent_v2"',
    '"speaker_embedding_cache" to "persistent_v4"',
)
activity_text = activity_text.replace(
    '"reference_codes_cache" to "none"',
    '"reference_codes_cache" to "persistent_v4"',
)
activity.write_text(activity_text, encoding="utf-8")

jni_text = jni.read_text(encoding="utf-8")
if "persistent_v2" not in jni_text:
    raise RuntimeError("generated JNI does not contain persistent_v2 marker")
jni_text = jni_text.replace("persistent_v2", "persistent_v4")
if '"reference_codes_cache"' not in jni_text:
    synthesis_marker = '                   << ",\\\"speaker_embedding_cache\\\":\\\"persistent_v4\\\""'
    if synthesis_marker not in jni_text:
        raise RuntimeError("generated JNI speaker cache diagnostic marker not found")
    jni_text = jni_text.replace(
        synthesis_marker,
        synthesis_marker
        + '\n                   << ",\\\"reference_codes_cache\\\":\\\"persistent_v4\\\""',
        1,
    )
jni.write_text(jni_text, encoding="utf-8")

replace_once(
    gradle,
    'versionCode = 20\n        versionName = "0.9.3-log-clear-ui-cleanup"',
    'versionCode = 21\n        versionName = "0.9.4-content-addressed-cache"',
    "bump cache-v4 Android version",
)

checks = {
    engine: (
        "kSpeakerCacheVersion = 4",
        "'4', '\\0'",
        ".vieneu-speaker-v4.bin",
        "kReferenceCodesCacheVersion = 4",
        ".vieneu-refcodes-v4.bin",
        "reference.codes_cache_hit",
        "reference.codes_cache_miss",
        "save_reference_codes_cache",
    ),
    activity: (
        'MessageDigest.getInstance("SHA-256")',
        cache_namespace,
        "active-" + cache_namespace,
        legacy_cache_namespace,
        ".vieneu-speaker-v4.bin",
        ".vieneu-refcodes-v4.bin",
        "persistent_v4",
        "reference_trimmed_to_8s",
        "dùng 8,0 giây đầu",
        "mẫu dưới 3 giây",
    ),
    jni: ("persistent_v4", "reference_codes_cache"),
    gradle: ("versionCode = 21", "0.9.4-content-addressed-cache"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing cache-v4 fragments {missing}")

print(
    "Applied reference cache v4 with preprocessing invalidation, persistent speaker embedding, "
    f"persistent reference codes and namespace {cache_namespace}"
)
