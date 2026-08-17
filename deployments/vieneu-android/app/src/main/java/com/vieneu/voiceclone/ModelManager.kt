package com.vieneu.voiceclone

import android.content.Context
import android.os.StatFs
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.Locale

object ModelManager {
    private const val RELEASE_TAG = "vieneu-model-parity-75ff82a7"
    private const val RELEASE_BASE =
        "https://github.com/tvhuy99-web/valtec-tts/releases/download/$RELEASE_TAG/"
    private const val MANIFEST_ASSET = "model-manifest.json"
    private const val MANIFEST_FILE = "model-manifest.json"
    private const val VERIFIED_FILE = ".verified-model.json"
    private const val EXPECTED_PACKAGE_ID = "vieneu-v3-turbo-parity-rebuild-75ff82a7"
    private const val EXPECTED_SOURCE_REVISION =
        "75ff82a72f54d55ed389e1eeb12041d3c4bac7d4"

    // Filled after the parity-approved release is produced. Per-file SHA-256,
    // package ID, and source revision are still mandatory before this is set.
    private const val EXPECTED_MANIFEST_SHA256 = ""

    private const val FREE_SPACE_MARGIN_BYTES = 128L * 1024L * 1024L
    private const val HTTP_RANGE_NOT_SATISFIABLE = 416
    private const val MAX_MANIFEST_BYTES = 2L * 1024L * 1024L

    val requiredFiles = listOf(
        "config.json",
        "tokenizer.json",
        "voices_v3_turbo.json",
        "backbone.gguf",
        "vieneu_v3_heads.npz",
        "speaker_encoder.onnx",
        "denoiser.onnx",
        "acoustic/vieneu_acoustic_weights.npz",
        "codec/moss_audio_tokenizer_decode_full.onnx",
        "codec/moss_audio_tokenizer_decode_shared.data",
        "codec/moss_audio_tokenizer_encode.onnx",
        "codec/moss_audio_tokenizer_encode.data"
    )

    data class Progress(
        val fileNumber: Int,
        val fileCount: Int,
        val path: String,
        val downloadedBytes: Long,
        val totalBytes: Long
    )

    private data class ManifestEntry(
        val path: String,
        val bytes: Long,
        val sha256: String
    )

    private data class ModelManifest(
        val packageId: String,
        val sourceRevision: String,
        val nativeAuxRevision: String,
        val exporterRevision: String,
        val entries: List<ManifestEntry>
    )

    fun modelDir(context: Context): File {
        val base = context.getExternalFilesDir(null) ?: context.filesDir
        return File(base, "vieneu-v3-turbo-native")
    }

    fun isReady(context: Context): Boolean = isReady(modelDir(context))

    fun isReady(root: File): Boolean {
        val manifestFile = File(root, MANIFEST_FILE)
        val verifiedFile = File(root, VERIFIED_FILE)
        if (!manifestFile.isFile || !verifiedFile.isFile) return false

        return runCatching {
            val manifestText = manifestFile.readText(Charsets.UTF_8)
            val manifestSha = sha256Hex(manifestText.toByteArray(Charsets.UTF_8))
            val manifest = parseAndValidateManifest(manifestText, manifestSha)
            val marker = JSONObject(verifiedFile.readText(Charsets.UTF_8))
            if (marker.optString("package_id") != manifest.packageId) return@runCatching false
            if (marker.optString("source_revision") != manifest.sourceRevision) return@runCatching false
            if (!marker.optString("manifest_sha256").equals(manifestSha, ignoreCase = true)) {
                return@runCatching false
            }
            manifest.entries.all { entry ->
                val file = File(root, entry.path)
                file.isFile && file.length() == entry.bytes
            }
        }.getOrElse {
            Diagnostics.error(
                "model",
                "ready.validation_failure",
                it,
                mapOf("root" to root.absolutePath)
            )
            false
        }
    }

    fun downloadAll(context: Context, progress: (Progress) -> Unit) {
        val root = modelDir(context)
        root.mkdirs()
        val totalSpan = Diagnostics.span(
            "model",
            "download.all",
            mapOf(
                "release_tag" to RELEASE_TAG,
                "root" to root.absolutePath,
                "required_files" to requiredFiles.size
            )
        )

        try {
            val manifestText = downloadManifest()
            val manifestSha = sha256Hex(manifestText.toByteArray(Charsets.UTF_8))
            val manifest = parseAndValidateManifest(manifestText, manifestSha)
            Diagnostics.log(
                "model",
                "manifest.accepted",
                data = mapOf(
                    "package_id" to manifest.packageId,
                    "source_revision" to manifest.sourceRevision,
                    "native_aux_revision" to manifest.nativeAuxRevision,
                    "exporter_revision" to manifest.exporterRevision,
                    "manifest_sha256" to manifestSha,
                    "file_count" to manifest.entries.size,
                    "total_bytes" to manifest.entries.sumOf { it.bytes }
                )
            )

            val invalidEntries = mutableListOf<ManifestEntry>()
            manifest.entries.forEachIndexed { index, entry ->
                val target = File(root, entry.path)
                if (verifyFile(target, entry, "existing")) {
                    progress(
                        Progress(
                            index + 1,
                            manifest.entries.size,
                            entry.path,
                            entry.bytes,
                            entry.bytes
                        )
                    )
                } else {
                    if (target.exists()) {
                        Diagnostics.log(
                            "model",
                            "existing.delete_invalid",
                            level = "WARN",
                            data = mapOf(
                                "file" to entry.path,
                                "bytes" to target.length(),
                                "expected_bytes" to entry.bytes
                            )
                        )
                        target.delete()
                    }
                    invalidEntries += entry
                }
            }

            val available = StatFs(root.absolutePath).availableBytes
            val requiredDownloadBytes = invalidEntries.sumOf { entry ->
                val part = File(root, entry.path + ".part")
                (entry.bytes - part.length().coerceIn(0L, entry.bytes)).coerceAtLeast(0L)
            }
            val requiredFreeBytes = requiredDownloadBytes + FREE_SPACE_MARGIN_BYTES
            Diagnostics.log(
                "model",
                "download.preflight",
                data = mapOf(
                    "ready_before" to isReady(root),
                    "available_bytes" to available,
                    "required_download_bytes" to requiredDownloadBytes,
                    "required_free_bytes" to requiredFreeBytes,
                    "invalid_files" to invalidEntries.map { it.path },
                    "snapshot" to Diagnostics.modelSnapshot(root)
                )
            )
            if (available < requiredFreeBytes) {
                val requiredMb = requiredFreeBytes / (1024L * 1024L)
                throw IllegalStateException(
                    "Không đủ dung lượng để cài bộ mô hình đã xác minh. Cần khoảng $requiredMb MB trống."
                )
            }

            invalidEntries.forEach { entry ->
                val index = manifest.entries.indexOf(entry)
                val target = File(root, entry.path)
                target.parentFile?.mkdirs()
                downloadOne(entry, target) { downloaded, total ->
                    progress(
                        Progress(
                            index + 1,
                            manifest.entries.size,
                            entry.path,
                            downloaded,
                            total
                        )
                    )
                }
            }

            val finalFailures = manifest.entries.filterNot { entry ->
                verifyFile(File(root, entry.path), entry, "final")
            }
            if (finalFailures.isNotEmpty()) {
                throw IllegalStateException(
                    "Bộ mô hình không vượt qua xác minh cuối: ${finalFailures.joinToString { it.path }}"
                )
            }

            atomicWrite(File(root, MANIFEST_FILE), manifestText)
            val marker = JSONObject()
                .put("schema", 1)
                .put("package_id", manifest.packageId)
                .put("source_revision", manifest.sourceRevision)
                .put("manifest_sha256", manifestSha)
                .put("verified_at_ms", System.currentTimeMillis())
                .put("file_count", manifest.entries.size)
            atomicWrite(File(root, VERIFIED_FILE), marker.toString(2) + "\n")

            if (!isReady(root)) {
                throw IllegalStateException("Bộ mô hình đã tải nhưng dấu xác minh không hợp lệ.")
            }
            totalSpan.end(
                true,
                mapOf(
                    "package_id" to manifest.packageId,
                    "source_revision" to manifest.sourceRevision,
                    "manifest_sha256" to manifestSha,
                    "downloaded_files" to invalidEntries.map { it.path },
                    "snapshot" to Diagnostics.modelSnapshot(root)
                )
            )
        } catch (t: Throwable) {
            Diagnostics.error(
                "model",
                "download.all.failure",
                t,
                mapOf("root" to root.absolutePath, "release_tag" to RELEASE_TAG)
            )
            totalSpan.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
            throw t
        }
    }

    private fun downloadManifest(): String {
        val span = Diagnostics.span(
            "model",
            "manifest.download",
            mapOf("url" to RELEASE_BASE + MANIFEST_ASSET)
        )
        var connection: HttpURLConnection? = null
        return try {
            connection = openConnection(URL(RELEASE_BASE + MANIFEST_ASSET), 0L, MANIFEST_ASSET)
            val code = connection.responseCode
            if (code != HttpURLConnection.HTTP_OK) {
                val message = runCatching {
                    connection.errorStream?.bufferedReader()?.readText()
                }.getOrNull()
                throw IllegalStateException(
                    "Không tải được manifest mô hình: HTTP $code${if (message.isNullOrBlank()) "" else " - $message"}"
                )
            }
            if (connection.contentLengthLong > MAX_MANIFEST_BYTES) {
                throw IllegalStateException("Manifest mô hình lớn bất thường.")
            }
            val bytes = connection.inputStream.use { input ->
                val output = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(16 * 1024)
                var total = 0L
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    total += read
                    if (total > MAX_MANIFEST_BYTES) {
                        throw IllegalStateException("Manifest mô hình vượt giới hạn kích thước.")
                    }
                    output.write(buffer, 0, read)
                }
                output.toByteArray()
            }
            val text = bytes.toString(Charsets.UTF_8)
            span.end(
                true,
                mapOf(
                    "bytes" to bytes.size,
                    "sha256" to sha256Hex(bytes),
                    "host" to connection.url.host
                )
            )
            text
        } catch (t: Throwable) {
            span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
            throw t
        } finally {
            connection?.disconnect()
        }
    }

    private fun parseAndValidateManifest(text: String, manifestSha: String): ModelManifest {
        if (EXPECTED_MANIFEST_SHA256.isNotBlank() &&
            !EXPECTED_MANIFEST_SHA256.equals(manifestSha, ignoreCase = true)
        ) {
            throw IllegalStateException(
                "SHA-256 manifest không đúng: $manifestSha"
            )
        }

        val json = JSONObject(text)
        if (json.optInt("schema") != 1) {
            throw IllegalStateException("Phiên bản manifest mô hình không được hỗ trợ.")
        }
        val packageId = json.getString("package_id")
        val sourceRevision = json.getString("source_revision")
        if (packageId != EXPECTED_PACKAGE_ID) {
            throw IllegalStateException("Sai gói mô hình: $packageId")
        }
        if (sourceRevision != EXPECTED_SOURCE_REVISION) {
            throw IllegalStateException("Sai revision mô hình: $sourceRevision")
        }

        val array = json.getJSONArray("files")
        val entries = ArrayList<ManifestEntry>(array.length())
        val seen = HashSet<String>()
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            val path = item.getString("path")
            val bytes = item.getLong("bytes")
            val sha256 = item.getString("sha256").lowercase(Locale.US)
            if (path.startsWith('/') || path.contains("..") || path.contains('\\')) {
                throw IllegalStateException("Đường dẫn không an toàn trong manifest: $path")
            }
            if (!seen.add(path)) {
                throw IllegalStateException("Tệp bị lặp trong manifest: $path")
            }
            if (bytes <= 0L || !sha256.matches(Regex("[0-9a-f]{64}"))) {
                throw IllegalStateException("Metadata không hợp lệ cho tệp $path")
            }
            entries += ManifestEntry(path, bytes, sha256)
        }
        if (seen != requiredFiles.toSet()) {
            val missing = requiredFiles.filterNot(seen::contains)
            val unexpected = seen.filterNot(requiredFiles::contains)
            throw IllegalStateException(
                "Danh sách model không đúng. Thiếu=$missing, thừa=$unexpected"
            )
        }
        return ModelManifest(
            packageId = packageId,
            sourceRevision = sourceRevision,
            nativeAuxRevision = json.optString("native_aux_revision"),
            exporterRevision = json.optString("exporter_revision"),
            entries = entries
        )
    }

    private fun verifyFile(file: File, entry: ManifestEntry, phase: String): Boolean {
        if (!file.isFile) {
            Diagnostics.log(
                "model",
                "verify.missing",
                level = "WARN",
                data = mapOf("phase" to phase, "file" to entry.path)
            )
            return false
        }
        if (file.length() != entry.bytes) {
            Diagnostics.log(
                "model",
                "verify.size_mismatch",
                level = "WARN",
                data = mapOf(
                    "phase" to phase,
                    "file" to entry.path,
                    "actual_bytes" to file.length(),
                    "expected_bytes" to entry.bytes
                )
            )
            return false
        }

        val started = SystemClock.elapsedRealtimeNanos()
        val actual = sha256Hex(file)
        val wallMs = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000.0
        val valid = actual.equals(entry.sha256, ignoreCase = true)
        Diagnostics.log(
            "model",
            if (valid) "verify.success" else "verify.hash_mismatch",
            level = if (valid) "INFO" else "WARN",
            data = mapOf(
                "phase" to phase,
                "file" to entry.path,
                "bytes" to entry.bytes,
                "expected_sha256" to entry.sha256,
                "actual_sha256" to actual,
                "wall_ms" to wallMs,
                "bytes_per_sec" to if (wallMs > 0.0) entry.bytes * 1000.0 / wallMs else 0.0
            )
        )
        return valid
    }

    private fun downloadOne(
        entry: ManifestEntry,
        target: File,
        progress: (Long, Long) -> Unit
    ) {
        val asset = entry.path.replace("/", "__")
        val fileSpan = Diagnostics.span(
            "model",
            "download.file",
            mapOf(
                "file" to entry.path,
                "asset" to asset,
                "expected_bytes" to entry.bytes,
                "expected_sha256" to entry.sha256
            )
        )
        val part = File(target.absolutePath + ".part")
        if (part.length() > entry.bytes) part.delete()
        var existing = if (part.isFile) part.length() else 0L
        val wallStartNs = SystemClock.elapsedRealtimeNanos()
        var bytesFromNetwork = 0L
        var connection: HttpURLConnection? = null

        try {
            Diagnostics.log(
                "model",
                "download.file.begin",
                data = mapOf(
                    "file" to entry.path,
                    "asset" to asset,
                    "resume_bytes" to existing,
                    "target" to target.absolutePath
                )
            )
            connection = openConnection(URL(RELEASE_BASE + asset), existing, entry.path)
            var code = connection.responseCode

            if (code == HTTP_RANGE_NOT_SATISFIABLE) {
                connection.disconnect()
                part.delete()
                existing = 0L
                Diagnostics.log(
                    "model",
                    "download.file.range_reset",
                    level = "WARN",
                    data = mapOf("file" to entry.path, "http_code" to code)
                )
                connection = openConnection(URL(RELEASE_BASE + asset), 0L, entry.path)
                code = connection.responseCode
            }

            if (code != HttpURLConnection.HTTP_OK && code != HttpURLConnection.HTTP_PARTIAL) {
                val message = runCatching {
                    connection.errorStream?.bufferedReader()?.readText()
                }.getOrNull()
                throw IllegalStateException(
                    "Không tải được ${entry.path}: HTTP $code${if (message.isNullOrBlank()) "" else " - $message"}"
                )
            }

            if (code == HttpURLConnection.HTTP_OK && existing > 0L) {
                Diagnostics.log(
                    "model",
                    "download.file.server_ignored_range",
                    level = "WARN",
                    data = mapOf("file" to entry.path, "discarded_partial_bytes" to existing)
                )
                part.delete()
                existing = 0L
            }

            val contentRange = connection.getHeaderField("Content-Range")
            val totalFromRange = contentRange?.substringAfterLast('/')?.toLongOrNull()
            if (totalFromRange != null && totalFromRange != entry.bytes) {
                throw IllegalStateException(
                    "Máy chủ báo sai kích thước cho ${entry.path}: $totalFromRange/${entry.bytes}"
                )
            }
            Diagnostics.log(
                "model",
                "download.file.response",
                data = mapOf(
                    "file" to entry.path,
                    "http_code" to code,
                    "content_length" to connection.contentLengthLong,
                    "content_range" to contentRange,
                    "expected_total_bytes" to entry.bytes,
                    "resume_bytes" to existing,
                    "server" to connection.url.host
                )
            )

            RandomAccessFile(part, "rw").use { output ->
                if (existing == 0L) output.setLength(0L)
                output.seek(existing)
                connection.inputStream.use { input ->
                    val buffer = ByteArray(1024 * 1024)
                    var downloaded = existing
                    var lastUiUpdate = 0L
                    var lastLogAt = SystemClock.elapsedRealtime()
                    var lastLogBytes = downloaded
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        downloaded += read
                        bytesFromNetwork += read
                        if (downloaded > entry.bytes) {
                            throw IllegalStateException(
                                "Tệp ${entry.path} lớn hơn manifest: $downloaded/${entry.bytes}"
                            )
                        }
                        val nowWall = System.currentTimeMillis()
                        if (nowWall - lastUiUpdate >= 250L) {
                            progress(downloaded, entry.bytes)
                            lastUiUpdate = nowWall
                        }
                        val elapsedNow = SystemClock.elapsedRealtime()
                        if (elapsedNow - lastLogAt >= 1000L) {
                            val deltaBytes = downloaded - lastLogBytes
                            val deltaMs = (elapsedNow - lastLogAt).coerceAtLeast(1L)
                            Diagnostics.log(
                                "model",
                                "download.file.progress",
                                data = mapOf(
                                    "file" to entry.path,
                                    "downloaded_bytes" to downloaded,
                                    "total_bytes" to entry.bytes,
                                    "percent" to downloaded * 100.0 / entry.bytes,
                                    "instant_bytes_per_sec" to deltaBytes * 1000.0 / deltaMs
                                )
                            )
                            lastLogAt = elapsedNow
                            lastLogBytes = downloaded
                        }
                    }
                    progress(downloaded, entry.bytes)
                    if (downloaded != entry.bytes) {
                        throw IllegalStateException(
                            "Tệp ${entry.path} bị thiếu dữ liệu: $downloaded/${entry.bytes} byte"
                        )
                    }
                }
            }

            if (!verifyFile(part, entry, "download_part")) {
                part.delete()
                throw IllegalStateException("SHA-256 không đúng cho ${entry.path}")
            }
            if (target.exists() && !target.delete()) {
                throw IllegalStateException("Không thể thay tệp cũ ${entry.path}")
            }
            if (!part.renameTo(target)) {
                part.copyTo(target, overwrite = true)
                part.delete()
            }
            if (!verifyFile(target, entry, "installed")) {
                target.delete()
                throw IllegalStateException("Tệp đã cài không vượt qua xác minh: ${entry.path}")
            }

            val wallMs = (SystemClock.elapsedRealtimeNanos() - wallStartNs) / 1_000_000.0
            fileSpan.end(
                true,
                mapOf(
                    "file" to entry.path,
                    "final_bytes" to target.length(),
                    "network_bytes" to bytesFromNetwork,
                    "average_bytes_per_sec" to if (wallMs > 0.0) {
                        bytesFromNetwork * 1000.0 / wallMs
                    } else {
                        0.0
                    },
                    "sha256" to entry.sha256
                )
            )
        } catch (t: Throwable) {
            Diagnostics.error(
                "model",
                "download.file.failure",
                t,
                mapOf(
                    "file" to entry.path,
                    "partial_bytes" to part.length(),
                    "expected_bytes" to entry.bytes,
                    "expected_sha256" to entry.sha256
                )
            )
            fileSpan.end(false, mapOf("file" to entry.path, "error" to (t.message ?: t.javaClass.simpleName)))
            throw t
        } finally {
            connection?.disconnect()
        }
    }

    private fun openConnection(
        initial: URL,
        rangeStart: Long,
        relativePath: String
    ): HttpURLConnection {
        var current = initial
        repeat(8) { redirectIndex ->
            val connection = current.openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 30_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("User-Agent", "VieNeuVoiceCloneAndroid/0.7-parity")
            connection.setRequestProperty("Accept-Encoding", "identity")
            if (rangeStart > 0L) {
                connection.setRequestProperty("Range", "bytes=$rangeStart-")
            }
            val connectStart = SystemClock.elapsedRealtimeNanos()
            val code = connection.responseCode
            val connectMs = (SystemClock.elapsedRealtimeNanos() - connectStart) / 1_000_000.0
            Diagnostics.log(
                "network",
                "http.response",
                data = mapOf(
                    "file" to relativePath,
                    "redirect_index" to redirectIndex,
                    "host" to current.host,
                    "path" to current.path,
                    "http_code" to code,
                    "connect_and_headers_ms" to connectMs,
                    "range_start" to rangeStart
                )
            )
            if (code in intArrayOf(301, 302, 303, 307, 308)) {
                val location = connection.getHeaderField("Location")
                    ?: throw IllegalStateException("Máy chủ chuyển hướng nhưng không có Location.")
                val next = URL(current, location)
                Diagnostics.log(
                    "network",
                    "http.redirect",
                    data = mapOf(
                        "file" to relativePath,
                        "from_host" to current.host,
                        "to_host" to next.host,
                        "http_code" to code
                    )
                )
                current = next
                connection.disconnect()
            } else {
                return connection
            }
        }
        throw IllegalStateException("Quá nhiều lần chuyển hướng khi tải mô hình.")
    }

    private fun sha256Hex(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered(1024 * 1024).use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { byte -> "%02x".format(byte) }
    }

    private fun sha256Hex(bytes: ByteArray): String {
        return MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { byte -> "%02x".format(byte) }
    }

    private fun atomicWrite(target: File, text: String) {
        target.parentFile?.mkdirs()
        val temp = File(target.absolutePath + ".new")
        temp.writeText(text, Charsets.UTF_8)
        if (target.exists() && !target.delete()) {
            temp.delete()
            throw IllegalStateException("Không thể thay ${target.name}")
        }
        if (!temp.renameTo(target)) {
            temp.copyTo(target, overwrite = true)
            temp.delete()
        }
    }
}
