package com.vieneu.voiceclone

import android.content.Context
import android.os.StatFs
import android.os.SystemClock
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL

object ModelManager {
    private const val REPO = "lastudio-community/VieNeu-TTS-v3-Turbo-CPP"
    private const val BASE = "https://huggingface.co/$REPO/resolve/main/"
    private const val MIN_FREE_BYTES = 750_000_000L
    private const val HTTP_RANGE_NOT_SATISFIABLE = 416

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

    fun modelDir(context: Context): File {
        val base = context.getExternalFilesDir(null) ?: context.filesDir
        return File(base, "vieneu-v3-turbo-native")
    }

    fun isReady(context: Context): Boolean = isReady(modelDir(context))

    fun isReady(root: File): Boolean = requiredFiles.all {
        val file = File(root, it)
        file.isFile && file.length() > 0L
    }

    fun downloadAll(context: Context, progress: (Progress) -> Unit) {
        val root = modelDir(context)
        root.mkdirs()
        val totalSpan = Diagnostics.span(
            "model",
            "download.all",
            mapOf("repo" to REPO, "root" to root.absolutePath, "required_files" to requiredFiles.size)
        )
        try {
            val available = StatFs(root.absolutePath).availableBytes
            Diagnostics.log(
                "model",
                "download.preflight",
                data = mapOf(
                    "ready_before" to isReady(root),
                    "available_bytes" to available,
                    "minimum_free_bytes" to MIN_FREE_BYTES,
                    "snapshot" to Diagnostics.modelSnapshot(root)
                )
            )
            if (!isReady(root) && available < MIN_FREE_BYTES) {
                throw IllegalStateException("Cần tối thiểu khoảng 750 MB dung lượng trống để tải mô hình VieNeu.")
            }

            requiredFiles.forEachIndexed { index, relativePath ->
                val target = File(root, relativePath)
                if (target.isFile && target.length() > 0L) {
                    Diagnostics.log(
                        "model",
                        "download.file.skip_existing",
                        data = mapOf("file" to relativePath, "bytes" to target.length(), "index" to index + 1)
                    )
                    progress(Progress(index + 1, requiredFiles.size, relativePath, target.length(), target.length()))
                    return@forEachIndexed
                }
                target.parentFile?.mkdirs()
                downloadOne(relativePath, target) { downloaded, total ->
                    progress(Progress(index + 1, requiredFiles.size, relativePath, downloaded, total))
                }
            }

            if (!isReady(root)) {
                throw IllegalStateException("Tải xong nhưng bộ mô hình chưa đầy đủ.")
            }
            totalSpan.end(true, mapOf("snapshot" to Diagnostics.modelSnapshot(root)))
        } catch (t: Throwable) {
            Diagnostics.error("model", "download.all.failure", t, mapOf("root" to root.absolutePath))
            totalSpan.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
            throw t
        }
    }

    private fun downloadOne(relativePath: String, target: File, progress: (Long, Long) -> Unit) {
        val fileSpan = Diagnostics.span("model", "download.file", mapOf("file" to relativePath))
        val part = File(target.absolutePath + ".part")
        var existing = if (part.exists()) part.length() else 0L
        val wallStartNs = SystemClock.elapsedRealtimeNanos()
        var bytesFromNetwork = 0L
        try {
            Diagnostics.log(
                "model",
                "download.file.begin",
                data = mapOf("file" to relativePath, "resume_bytes" to existing, "target" to target.absolutePath)
            )
            var connection = openConnection(URL(BASE + relativePath + "?download=true"), existing, relativePath)
            var code = connection.responseCode

            if (code == HTTP_RANGE_NOT_SATISFIABLE) {
                Diagnostics.log(
                    "model",
                    "download.file.range_reset",
                    level = "WARN",
                    data = mapOf("file" to relativePath, "resume_bytes" to existing, "http_code" to code)
                )
                connection.disconnect()
                part.delete()
                existing = 0L
                connection = openConnection(URL(BASE + relativePath + "?download=true"), 0L, relativePath)
                code = connection.responseCode
            }

            if (code != HttpURLConnection.HTTP_OK && code != HttpURLConnection.HTTP_PARTIAL) {
                val message = runCatching { connection.errorStream?.bufferedReader()?.readText() }.getOrNull()
                connection.disconnect()
                throw IllegalStateException("Không tải được $relativePath: HTTP $code${if (message.isNullOrBlank()) "" else " - $message"}")
            }

            if (code == HttpURLConnection.HTTP_OK && existing > 0L) {
                Diagnostics.log(
                    "model",
                    "download.file.server_ignored_range",
                    level = "WARN",
                    data = mapOf("file" to relativePath, "discarded_partial_bytes" to existing)
                )
                part.delete()
                existing = 0L
            }

            val contentRange = connection.getHeaderField("Content-Range")
            val totalFromRange = contentRange?.substringAfterLast('/')?.toLongOrNull()
            val total = totalFromRange ?: connection.contentLengthLong.takeIf { it > 0L }?.let { it + existing } ?: -1L
            Diagnostics.log(
                "model",
                "download.file.response",
                data = mapOf(
                    "file" to relativePath,
                    "http_code" to code,
                    "content_length" to connection.contentLengthLong,
                    "content_range" to contentRange,
                    "expected_total_bytes" to total,
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
                        val nowWall = System.currentTimeMillis()
                        if (nowWall - lastUiUpdate >= 250L) {
                            progress(downloaded, total)
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
                                    "file" to relativePath,
                                    "downloaded_bytes" to downloaded,
                                    "total_bytes" to total,
                                    "percent" to if (total > 0L) downloaded * 100.0 / total else null,
                                    "instant_bytes_per_sec" to deltaBytes * 1000.0 / deltaMs
                                )
                            )
                            lastLogAt = elapsedNow
                            lastLogBytes = downloaded
                        }
                    }
                    progress(downloaded, total)
                    if (total > 0L && downloaded < total) {
                        throw IllegalStateException("Tệp $relativePath bị thiếu dữ liệu: $downloaded/$total bytes")
                    }
                }
            }
            connection.disconnect()

            if (target.exists()) target.delete()
            if (!part.renameTo(target)) {
                Diagnostics.log("model", "download.file.rename_fallback", level = "WARN", data = mapOf("file" to relativePath))
                part.copyTo(target, overwrite = true)
                part.delete()
            }
            val wallMs = (SystemClock.elapsedRealtimeNanos() - wallStartNs) / 1_000_000.0
            val averageBps = if (wallMs > 0.0) bytesFromNetwork * 1000.0 / wallMs else 0.0
            fileSpan.end(
                true,
                mapOf(
                    "file" to relativePath,
                    "final_bytes" to target.length(),
                    "network_bytes" to bytesFromNetwork,
                    "average_bytes_per_sec" to averageBps
                )
            )
        } catch (t: Throwable) {
            Diagnostics.error("model", "download.file.failure", t, mapOf("file" to relativePath, "partial_bytes" to part.length()))
            fileSpan.end(false, mapOf("file" to relativePath, "error" to (t.message ?: t.javaClass.simpleName)))
            throw t
        }
    }

    private fun openConnection(initial: URL, rangeStart: Long, relativePath: String): HttpURLConnection {
        var current = initial
        repeat(8) { redirectIndex ->
            val connection = current.openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 30_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("User-Agent", "VieNeuVoiceCloneAndroid/0.2-diag")
            connection.setRequestProperty("Accept-Encoding", "identity")
            if (rangeStart > 0L) connection.setRequestProperty("Range", "bytes=$rangeStart-")
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
}
