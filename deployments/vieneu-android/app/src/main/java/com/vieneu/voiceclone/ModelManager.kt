package com.vieneu.voiceclone

import android.content.Context
import android.os.StatFs
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
        val available = StatFs(root.absolutePath).availableBytes
        if (!isReady(root) && available < MIN_FREE_BYTES) {
            throw IllegalStateException("Cần tối thiểu khoảng 750 MB dung lượng trống để tải mô hình VieNeu.")
        }

        requiredFiles.forEachIndexed { index, relativePath ->
            val target = File(root, relativePath)
            if (target.isFile && target.length() > 0L) {
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
    }

    private fun downloadOne(relativePath: String, target: File, progress: (Long, Long) -> Unit) {
        val part = File(target.absolutePath + ".part")
        var existing = if (part.exists()) part.length() else 0L
        var connection = openConnection(URL(BASE + relativePath + "?download=true"), existing)
        var code = connection.responseCode

        if (code == HTTP_RANGE_NOT_SATISFIABLE) {
            connection.disconnect()
            part.delete()
            existing = 0L
            connection = openConnection(URL(BASE + relativePath + "?download=true"), 0L)
            code = connection.responseCode
        }

        if (code != HttpURLConnection.HTTP_OK && code != HttpURLConnection.HTTP_PARTIAL) {
            val message = runCatching { connection.errorStream?.bufferedReader()?.readText() }.getOrNull()
            connection.disconnect()
            throw IllegalStateException("Không tải được $relativePath: HTTP $code${if (message.isNullOrBlank()) "" else " - $message"}")
        }

        if (code == HttpURLConnection.HTTP_OK && existing > 0L) {
            part.delete()
            existing = 0L
        }

        val contentRange = connection.getHeaderField("Content-Range")
        val totalFromRange = contentRange?.substringAfterLast('/')?.toLongOrNull()
        val total = totalFromRange ?: connection.contentLengthLong.takeIf { it > 0L }?.let { it + existing } ?: -1L

        RandomAccessFile(part, "rw").use { output ->
            if (existing == 0L) output.setLength(0L)
            output.seek(existing)
            connection.inputStream.use { input ->
                val buffer = ByteArray(1024 * 1024)
                var downloaded = existing
                var lastUpdate = 0L
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    output.write(buffer, 0, read)
                    downloaded += read
                    val now = System.currentTimeMillis()
                    if (now - lastUpdate >= 250L) {
                        progress(downloaded, total)
                        lastUpdate = now
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
            part.copyTo(target, overwrite = true)
            part.delete()
        }
    }

    private fun openConnection(initial: URL, rangeStart: Long): HttpURLConnection {
        var current = initial
        repeat(8) {
            val connection = current.openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 30_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("User-Agent", "VieNeuVoiceCloneAndroid/0.1")
            connection.setRequestProperty("Accept-Encoding", "identity")
            if (rangeStart > 0L) connection.setRequestProperty("Range", "bytes=$rangeStart-")
            val code = connection.responseCode
            if (code in intArrayOf(301, 302, 303, 307, 308)) {
                val location = connection.getHeaderField("Location")
                    ?: throw IllegalStateException("Máy chủ chuyển hướng nhưng không có Location.")
                current = URL(current, location)
                connection.disconnect()
            } else {
                return connection
            }
        }
        throw IllegalStateException("Quá nhiều lần chuyển hướng khi tải mô hình.")
    }
}
