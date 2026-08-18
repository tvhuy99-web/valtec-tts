#!/usr/bin/env python3


import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: optimize_vieneu_android_cache_v3.py <vieneu-source-dir> <android-root>"
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
cache_namespace = f"{package_id}-refv3-{manifest_sha}"

replace_once(
    engine,
    "constexpr uint32_t kSpeakerCacheVersion = 2;",
    "constexpr uint32_t kSpeakerCacheVersion = 3;",
    "speaker cache version",
)
replace_once(
    engine,
    "constexpr char kSpeakerCacheMagic[8] = {'V', 'N', 'S', 'P', 'K', '0', '2', '\\0'};",
    "constexpr char kSpeakerCacheMagic[8] = {'V', 'N', 'S', 'P', 'K', '0', '3', '\\0'};",
    "speaker cache magic",
)
replace_once(
    engine,
    '    return ref_audio_path + ".vieneu-speaker-v2.bin";\n',
    '    return ref_audio_path + ".vieneu-speaker-v3.bin";\n',
    "speaker cache filename",
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

    private fun pruneReferenceStore(active: File) {{
        val root = referenceStoreDir()
        val stale = root.listFiles()
            ?.filter {{ it.isFile && it.extension.equals("wav", ignoreCase = true) && it.absolutePath != active.absolutePath }}
            ?.sortedByDescending {{ it.lastModified() }}
            ?.drop(3)
            .orEmpty()
        stale.forEach {{ wav ->
            File(wav.absolutePath + ".vieneu-speaker-v3.bin").delete()
            wav.delete()
        }}
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
        File(legacy.absolutePath + ".vieneu-speaker-v2.bin").delete()
        File(legacy.absolutePath + ".vieneu-speaker-v3.bin").delete()
        target.setLastModified(System.currentTimeMillis())
        writeActiveReferenceHash(hash)
        pruneReferenceStore(target)
        Diagnostics.log(
            "reference",
            "reference.cache_v3_migrated",
            data = mapOf(
                "reference_sha256" to hash,
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
        }} ?: migrateLegacyReference() ?: return

        val referenceHash = saved.nameWithoutExtension
        saved.setLastModified(System.currentTimeMillis())
        referenceFile = saved
        referenceStatus.text = "Giọng mẫu: ${{referenceHash.take(12)}}… (${{saved.length() / 1024}} KB)"
        Diagnostics.log(
            "reference",
            "reference.restore",
            data = mapOf(
                "reference_sha256" to referenceHash,
                "cache_namespace" to "{cache_namespace}",
                "wav" to Diagnostics.wavInfo(saved),
                "speaker_cache_present" to File(saved.absolutePath + ".vieneu-speaker-v3.bin").isFile
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
                Diagnostics.log(
                    "reference",
                    "reference.probe",
                    data = mapOf(
                        "display_name" to name,
                        "copy_ms" to copyMs,
                        "sha256_ms" to hashMs,
                        "reference_sha256" to referenceHash,
                        "cache_namespace" to "{cache_namespace}",
                        "speaker_cache_present" to File(target.absolutePath + ".vieneu-speaker-v3.bin").isFile,
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
                        "wav" to wav
                    )
                )
                runOnUiThread {{
                    referenceStatus.text = "Giọng mẫu: $name (${{target.length() / 1024}} KB)"
                    statusText.text = "Đã nhập giọng mẫu. Cache giọng được khóa theo SHA-256 nội dung."
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
    "content-addressed Android reference storage",
)

activity_text = activity.read_text(encoding="utf-8")
activity_text = activity_text.replace(
    '"speaker_embedding_cache" to "persistent_v2"',
    '"speaker_embedding_cache" to "persistent_v3"',
)
activity.write_text(activity_text, encoding="utf-8")

jni_text = jni.read_text(encoding="utf-8")
if "persistent_v2" not in jni_text:
    raise RuntimeError("generated JNI does not contain persistent_v2 marker")
jni.write_text(jni_text.replace("persistent_v2", "persistent_v3"), encoding="utf-8")

replace_once(
    gradle,
    'versionCode = 20\n        versionName = "0.9.3-log-clear-ui-cleanup"',
    'versionCode = 21\n        versionName = "0.9.4-content-addressed-cache"',
    "bump cache-v3 Android version",
)

checks = {
    engine: (
        "kSpeakerCacheVersion = 3",
        "kSpeakerCacheMagic[8]",
        "'3', '\\0'",
        ".vieneu-speaker-v3.bin",
    ),
    activity: (
        "MessageDigest.getInstance(\"SHA-256\")",
        cache_namespace,
        "active-" + cache_namespace,
        ".vieneu-speaker-v3.bin",
        "persistent_v3",
        "Cache giọng được khóa theo SHA-256 nội dung",
    ),
    jni: ("persistent_v3",),
    gradle: ("versionCode = 21", "0.9.4-content-addressed-cache"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing cache-v3 fragments {missing}")

print(
    "Applied reference cache v3: SHA-256 content-addressed WAV storage, model-manifest "
    f"namespace {cache_namespace}, immutable reference paths and a new native cache format"
)
