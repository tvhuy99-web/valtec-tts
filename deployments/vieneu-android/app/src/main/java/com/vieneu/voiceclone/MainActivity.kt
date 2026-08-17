package com.vieneu.voiceclone

import android.app.Activity
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.provider.OpenableColumns
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.Spinner
import android.widget.TextView
import java.io.File
import java.io.FileInputStream
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private var referenceFile: File? = null
    private var outputFile: File? = null
    private var diagnosticsBundle: File? = null
    private var player: MediaPlayer? = null
    private var engineReady = false

    private lateinit var modelStatus: TextView
    private lateinit var referenceStatus: TextView
    private lateinit var downloadDetail: TextView
    private lateinit var statusText: TextView
    private lateinit var diagnosticsStatus: TextView
    private lateinit var downloadProgress: ProgressBar
    private lateinit var generateProgress: ProgressBar
    private lateinit var textInput: EditText
    private lateinit var styleSpinner: Spinner
    private lateinit var downloadButton: Button
    private lateinit var generateButton: Button
    private lateinit var playButton: Button
    private lateinit var saveButton: Button
    private lateinit var exportDiagnosticsButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        Diagnostics.start(this)
        Diagnostics.log(
            "app",
            "activity.onCreate",
            data = mapOf("saved_state" to (savedInstanceState != null)),
            snapshot = true
        )
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        modelStatus = findViewById(R.id.modelStatus)
        referenceStatus = findViewById(R.id.referenceStatus)
        downloadDetail = findViewById(R.id.downloadDetail)
        statusText = findViewById(R.id.statusText)
        diagnosticsStatus = findViewById(R.id.diagnosticsStatus)
        downloadProgress = findViewById(R.id.downloadProgress)
        generateProgress = findViewById(R.id.generateProgress)
        textInput = findViewById(R.id.textInput)
        styleSpinner = findViewById(R.id.styleSpinner)
        downloadButton = findViewById(R.id.downloadModelButton)
        generateButton = findViewById(R.id.generateButton)
        playButton = findViewById(R.id.playButton)
        saveButton = findViewById(R.id.saveButton)
        exportDiagnosticsButton = findViewById(R.id.exportDiagnosticsButton)

        styleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Tin tức", "Đọc truyện")
        )

        downloadButton.setOnClickListener {
            Diagnostics.log("ui", "click.download_model")
            downloadModel()
        }
        findViewById<Button>(R.id.pickReferenceButton).setOnClickListener {
            Diagnostics.log("ui", "click.pick_reference")
            pickReference()
        }
        generateButton.setOnClickListener {
            Diagnostics.log("ui", "click.generate")
            generate()
        }
        playButton.setOnClickListener {
            Diagnostics.log("ui", "click.play_output")
            playOutput()
        }
        saveButton.setOnClickListener {
            Diagnostics.log("ui", "click.save_output")
            saveOutput()
        }
        exportDiagnosticsButton.setOnClickListener {
            Diagnostics.log("ui", "click.export_diagnostics")
            exportDiagnostics()
        }
        findViewById<Button>(R.id.clearDiagnosticsButton).setOnClickListener {
            val deleted = Diagnostics.clearOldSessions()
            diagnosticsStatus.text = "Nhật ký hiện tại: ${Diagnostics.sessionId} · đã xóa $deleted phiên cũ"
        }

        diagnosticsStatus.text = "Nhật ký chi tiết: ${Diagnostics.sessionId}"
        val nativeDiagError = runCatching {
            VieNeuNative.configureDiagnostics(Diagnostics.currentSessionDir().absolutePath, Diagnostics.sessionId)
        }.getOrElse {
            Diagnostics.error("native", "diagnostics.configure.exception", it)
            it.message ?: it.javaClass.simpleName
        }
        if (nativeDiagError.isNotEmpty()) {
            Diagnostics.log(
                "native",
                "diagnostics.configure.failure",
                level = "WARN",
                data = mapOf("error" to nativeDiagError)
            )
            diagnosticsStatus.text = "Nhật ký app đang chạy; native log lỗi: $nativeDiagError"
        } else {
            Diagnostics.log(
                "native",
                "diagnostics.configure.success",
                data = mapOf("directory" to Diagnostics.currentSessionDir().absolutePath)
            )
        }

        refreshModelStatus()
    }

    override fun onStart() {
        super.onStart()
        Diagnostics.log("app", "activity.onStart")
    }

    override fun onResume() {
        super.onResume()
        Diagnostics.log("app", "activity.onResume", snapshot = true)
    }

    override fun onPause() {
        Diagnostics.log("app", "activity.onPause", snapshot = true)
        super.onPause()
    }

    override fun onStop() {
        Diagnostics.log("app", "activity.onStop")
        super.onStop()
    }

    private fun refreshModelStatus() {
        val root = ModelManager.modelDir(this)
        val ready = ModelManager.isReady(root)
        if (ready) {
            modelStatus.text = "Mô hình: đã sẵn sàng"
            downloadButton.text = "Kiểm tra / tải lại phần còn thiếu"
        } else {
            modelStatus.text = "Mô hình: chưa tải đủ (~660 MB)"
            downloadButton.text = "Tải mô hình offline"
        }
        downloadDetail.text = root.absolutePath
        Diagnostics.log(
            "model",
            "status.refresh",
            data = Diagnostics.modelSnapshot(root),
            snapshot = true
        )
    }

    private fun downloadModel() {
        downloadButton.isEnabled = false
        statusText.text = "Đang tải mô hình VieNeu..."
        val span = Diagnostics.span("model", "download.user_flow")
        executor.execute {
            try {
                ModelManager.downloadAll(this) { p ->
                    val filePct = if (p.totalBytes > 0L) ((p.downloadedBytes * 100L) / p.totalBytes).toInt().coerceIn(0, 100) else 0
                    val overall = (((p.fileNumber - 1) * 100 + filePct) / p.fileCount).coerceIn(0, 100)
                    runOnUiThread {
                        downloadProgress.progress = overall
                        val doneMb = p.downloadedBytes / 1_048_576.0
                        val totalText = if (p.totalBytes > 0L) " / %.1f MB".format(p.totalBytes / 1_048_576.0) else ""
                        downloadDetail.text = "${p.fileNumber}/${p.fileCount} ${p.path}: %.1f MB%s".format(doneMb, totalText)
                    }
                }
                engineReady = false
                span.end(true, mapOf("model" to Diagnostics.modelSnapshot(ModelManager.modelDir(this))))
                runOnUiThread {
                    downloadProgress.progress = 100
                    statusText.text = "Tải mô hình hoàn tất."
                    downloadButton.isEnabled = true
                    refreshModelStatus()
                }
            } catch (t: Throwable) {
                Diagnostics.error("model", "download.user_flow.failure", t)
                span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
                runOnUiThread {
                    statusText.text = "Lỗi tải model: ${t.message ?: t.javaClass.simpleName}"
                    downloadButton.isEnabled = true
                }
            }
        }
    }

    private fun pickReference() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "audio/*"
            putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"))
        }
        Diagnostics.log("reference", "picker.open")
        startActivityForResult(intent, REQUEST_REFERENCE)
    }

    private fun generate() {
        val text = textInput.text.toString().trim()
        val ref = referenceFile
        if (!ModelManager.isReady(this)) {
            Diagnostics.log("generation", "validation.failure", level = "WARN", data = mapOf("reason" to "model_not_ready"))
            statusText.text = "Hãy tải đầy đủ mô hình trước."
            return
        }
        if (ref == null || !ref.isFile) {
            Diagnostics.log("generation", "validation.failure", level = "WARN", data = mapOf("reason" to "reference_missing"))
            statusText.text = "Hãy chọn một tệp WAV làm giọng mẫu."
            return
        }
        if (text.isBlank()) {
            Diagnostics.log("generation", "validation.failure", level = "WARN", data = mapOf("reason" to "text_blank"))
            statusText.text = "Hãy nhập văn bản cần đọc."
            return
        }

        generateButton.isEnabled = false
        playButton.isEnabled = false
        saveButton.isEnabled = false
        generateProgress.visibility = View.VISIBLE
        statusText.text = if (engineReady) "Đang tổng hợp giọng..." else "Đang nạp mô hình vào RAM..."

        val style = when (styleSpinner.selectedItemPosition) {
            1 -> "tin_tuc"
            2 -> "doc_truyen"
            else -> "tu_nhien"
        }
        val generationId = UUID.randomUUID().toString()
        val totalSpan = Diagnostics.span(
            "generation",
            "generation.total",
            mapOf(
                "generation_id" to generationId,
                "text_chars" to text.length,
                "text_utf8_bytes" to text.toByteArray(Charsets.UTF_8).size,
                "style" to style,
                "engine_already_ready" to engineReady,
                "reference" to Diagnostics.wavInfo(ref)
            )
        )

        executor.execute {
            try {
                if (!engineReady) {
                    val threads = Runtime.getRuntime().availableProcessors().coerceIn(2, 6)
                    val initSpan = Diagnostics.span(
                        "engine",
                        "engine.initialize",
                        mapOf(
                            "generation_id" to generationId,
                            "threads" to threads,
                            "available_processors" to Runtime.getRuntime().availableProcessors(),
                            "model" to Diagnostics.modelSnapshot(ModelManager.modelDir(this))
                        )
                    )
                    val initStart = SystemClock.elapsedRealtimeNanos()
                    val error = VieNeuNative.initialize(ModelManager.modelDir(this).absolutePath, threads)
                    val initMs = (SystemClock.elapsedRealtimeNanos() - initStart) / 1_000_000.0
                    if (error.isNotEmpty()) {
                        initSpan.end(false, mapOf("generation_id" to generationId, "wall_ms_direct" to initMs, "error" to error))
                        throw IllegalStateException(error)
                    }
                    engineReady = true
                    initSpan.end(true, mapOf("generation_id" to generationId, "wall_ms_direct" to initMs))
                    runOnUiThread { statusText.text = "Model đã nạp. Đang clone và tổng hợp giọng..." }
                }

                val synthSpan = Diagnostics.span(
                    "generation",
                    "native.synthesize",
                    mapOf("generation_id" to generationId, "style" to style, "text_chars" to text.length)
                )
                val synthStart = SystemClock.elapsedRealtimeNanos()
                val audio = VieNeuNative.synthesize(text, ref.absolutePath, style)
                    ?: run {
                        val nativeError = VieNeuNative.lastError().ifBlank { "Native synthesis trả về null." }
                        synthSpan.end(false, mapOf("generation_id" to generationId, "error" to nativeError))
                        throw IllegalStateException(nativeError)
                    }
                val synthWallMs = (SystemClock.elapsedRealtimeNanos() - synthStart) / 1_000_000.0
                if (audio.isEmpty()) {
                    synthSpan.end(false, mapOf("generation_id" to generationId, "error" to "empty_audio"))
                    throw IllegalStateException("Không tạo được mẫu âm thanh nào.")
                }

                val sampleRate = VieNeuNative.sampleRate()
                val audioStats = Diagnostics.audioStats(audio, sampleRate)
                val audioDurationMs = (audioStats["duration_ms"] as? Number)?.toDouble() ?: 0.0
                synthSpan.end(
                    true,
                    mapOf(
                        "generation_id" to generationId,
                        "wall_ms_direct" to synthWallMs,
                        "rtf" to if (audioDurationMs > 0.0) synthWallMs / audioDurationMs else null,
                        "audio" to audioStats
                    )
                )

                val out = File(filesDir, "outputs/vieneu_${System.currentTimeMillis()}.wav")
                val writeSpan = Diagnostics.span(
                    "io",
                    "wav.write",
                    mapOf("generation_id" to generationId, "target" to out.absolutePath, "audio" to audioStats)
                )
                try {
                    WavWriter.writeMonoFloat(out, audio, sampleRate)
                    writeSpan.end(true, mapOf("bytes" to out.length(), "wav" to Diagnostics.wavInfo(out)))
                } catch (t: Throwable) {
                    Diagnostics.error("io", "wav.write.failure", t, mapOf("generation_id" to generationId))
                    writeSpan.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
                    throw t
                }
                outputFile = out
                totalSpan.end(
                    true,
                    mapOf(
                        "generation_id" to generationId,
                        "output" to Diagnostics.wavInfo(out),
                        "audio" to audioStats,
                        "native_synthesis_wall_ms" to synthWallMs,
                        "native_rtf" to if (audioDurationMs > 0.0) synthWallMs / audioDurationMs else null
                    )
                )
                runOnUiThread {
                    generateProgress.visibility = View.GONE
                    generateButton.isEnabled = true
                    playButton.isEnabled = true
                    saveButton.isEnabled = true
                    statusText.text = "Hoàn tất: ${out.name} (%.2f giây, RTF %.3f)".format(
                        audioDurationMs / 1000.0,
                        if (audioDurationMs > 0.0) synthWallMs / audioDurationMs else 0.0
                    )
                }
            } catch (t: Throwable) {
                Diagnostics.error(
                    "generation",
                    "generation.failure",
                    t,
                    mapOf("generation_id" to generationId, "native_last_error" to runCatching { VieNeuNative.lastError() }.getOrNull())
                )
                totalSpan.end(false, mapOf("generation_id" to generationId, "error" to (t.message ?: t.javaClass.simpleName)))
                runOnUiThread {
                    generateProgress.visibility = View.GONE
                    generateButton.isEnabled = true
                    statusText.text = "Lỗi tổng hợp: ${t.message ?: t.javaClass.simpleName}"
                }
            }
        }
    }

    private fun playOutput() {
        val file = outputFile ?: return
        val span = Diagnostics.span("playback", "wav.play", mapOf("file" to file.absolutePath, "wav" to Diagnostics.wavInfo(file)))
        runCatching {
            player?.release()
            player = MediaPlayer().apply {
                setDataSource(file.absolutePath)
                setOnCompletionListener {
                    Diagnostics.log("playback", "wav.play.complete", data = mapOf("file" to file.name))
                    span.end(true, mapOf("completed" to true))
                    it.release()
                    if (player === it) player = null
                }
                setOnErrorListener { mp, what, extra ->
                    Diagnostics.log(
                        "playback",
                        "wav.play.error",
                        level = "ERROR",
                        data = mapOf("file" to file.name, "what" to what, "extra" to extra)
                    )
                    span.end(false, mapOf("what" to what, "extra" to extra))
                    mp.release()
                    if (player === mp) player = null
                    true
                }
                prepare()
                Diagnostics.log("playback", "wav.play.prepared", data = mapOf("duration_ms" to duration))
                start()
            }
            statusText.text = "Đang phát ${file.name}"
        }.onFailure {
            Diagnostics.error("playback", "wav.play.exception", it, mapOf("file" to file.absolutePath))
            span.end(false, mapOf("error" to (it.message ?: it.javaClass.simpleName)))
            statusText.text = "Không phát được WAV: ${it.message}"
        }
    }

    private fun saveOutput() {
        val file = outputFile ?: return
        Diagnostics.log("io", "wav.export.picker_open", data = mapOf("file" to file.name, "bytes" to file.length()))
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "audio/wav"
            putExtra(Intent.EXTRA_TITLE, file.name)
        }
        startActivityForResult(intent, REQUEST_SAVE)
    }

    private fun exportDiagnostics() {
        exportDiagnosticsButton.isEnabled = false
        statusText.text = "Đang đóng gói nhật ký chi tiết..."
        executor.execute {
            try {
                val bundle = Diagnostics.createBundle(ModelManager.modelDir(this), referenceFile, outputFile)
                diagnosticsBundle = bundle
                runOnUiThread {
                    exportDiagnosticsButton.isEnabled = true
                    diagnosticsStatus.text = "Đã tạo gói nhật ký ${bundle.length() / 1024} KB"
                    val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                        addCategory(Intent.CATEGORY_OPENABLE)
                        type = "application/zip"
                        putExtra(Intent.EXTRA_TITLE, bundle.name)
                    }
                    startActivityForResult(intent, REQUEST_DIAGNOSTICS)
                }
            } catch (t: Throwable) {
                Diagnostics.error("diagnostics", "bundle.request.failure", t)
                runOnUiThread {
                    exportDiagnosticsButton.isEnabled = true
                    statusText.text = "Lỗi tạo nhật ký: ${t.message ?: t.javaClass.simpleName}"
                }
            }
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != RESULT_OK) {
            Diagnostics.log("ui", "activity_result.cancelled", data = mapOf("request_code" to requestCode, "result_code" to resultCode))
            return
        }
        val uri = data?.data ?: run {
            Diagnostics.log("ui", "activity_result.missing_uri", level = "WARN", data = mapOf("request_code" to requestCode))
            return
        }
        when (requestCode) {
            REQUEST_REFERENCE -> importReference(uri)
            REQUEST_SAVE -> exportOutput(uri)
            REQUEST_DIAGNOSTICS -> exportDiagnosticsBundle(uri)
        }
    }

    private fun importReference(uri: Uri) {
        val span = Diagnostics.span(
            "reference",
            "reference.import",
            mapOf("authority" to uri.authority, "scheme" to uri.scheme)
        )
        executor.execute {
            try {
                val dir = File(filesDir, "references").apply { mkdirs() }
                val target = File(dir, "reference.wav")
                val copyStart = SystemClock.elapsedRealtimeNanos()
                contentResolver.openInputStream(uri)?.use { input ->
                    target.outputStream().use { output -> input.copyTo(output, 1024 * 1024) }
                } ?: throw IllegalStateException("Không mở được tệp đã chọn.")
                val copyMs = (SystemClock.elapsedRealtimeNanos() - copyStart) / 1_000_000.0
                if (!looksLikeWav(target)) {
                    target.delete()
                    throw IllegalArgumentException("Tệp đã chọn không phải WAV RIFF/WAVE hợp lệ.")
                }
                referenceFile = target
                val name = queryDisplayName(uri) ?: "reference.wav"
                val wav = Diagnostics.wavInfo(target)
                Diagnostics.log(
                    "reference",
                    "reference.probe",
                    data = mapOf("display_name" to name, "copy_ms" to copyMs, "wav" to wav)
                )
                span.end(true, mapOf("display_name" to name, "copy_ms" to copyMs, "wav" to wav))
                runOnUiThread {
                    referenceStatus.text = "Giọng mẫu: $name (${target.length() / 1024} KB)"
                    statusText.text = "Đã nhập giọng mẫu. VieNeu sẽ dùng tối đa 8 giây đầu sau xử lý."
                }
            } catch (t: Throwable) {
                Diagnostics.error("reference", "reference.import.failure", t)
                span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
                runOnUiThread { statusText.text = "Lỗi giọng mẫu: ${t.message}" }
            }
        }
    }

    private fun exportOutput(uri: Uri) {
        val source = outputFile ?: return
        val span = Diagnostics.span("io", "wav.export", mapOf("source" to source.absolutePath, "bytes" to source.length()))
        executor.execute {
            try {
                val start = SystemClock.elapsedRealtimeNanos()
                contentResolver.openOutputStream(uri)?.use { output ->
                    source.inputStream().use { input -> input.copyTo(output, 1024 * 1024) }
                } ?: throw IllegalStateException("Không mở được nơi lưu.")
                val wallMs = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                span.end(true, mapOf("wall_ms_direct" to wallMs, "bytes" to source.length()))
                runOnUiThread { statusText.text = "Đã lưu WAV thành công." }
            } catch (t: Throwable) {
                Diagnostics.error("io", "wav.export.failure", t, mapOf("source" to source.absolutePath))
                span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
                runOnUiThread { statusText.text = "Lỗi lưu WAV: ${t.message}" }
            }
        }
    }

    private fun exportDiagnosticsBundle(uri: Uri) {
        val source = diagnosticsBundle ?: return
        val span = Diagnostics.span("diagnostics", "bundle.export", mapOf("source" to source.absolutePath, "bytes" to source.length()))
        executor.execute {
            try {
                contentResolver.openOutputStream(uri)?.use { output ->
                    source.inputStream().use { input -> input.copyTo(output, 256 * 1024) }
                } ?: throw IllegalStateException("Không mở được nơi lưu nhật ký.")
                span.end(true, mapOf("bytes" to source.length()))
                runOnUiThread {
                    statusText.text = "Đã xuất nhật ký. Hãy gửi ZIP này để phân tích hiệu năng."
                    diagnosticsStatus.text = "Nhật ký: ${Diagnostics.sessionId} · đã xuất ZIP"
                }
            } catch (t: Throwable) {
                Diagnostics.error("diagnostics", "bundle.export.failure", t)
                span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
                runOnUiThread { statusText.text = "Lỗi xuất nhật ký: ${t.message}" }
            }
        }
    }

    private fun looksLikeWav(file: File): Boolean {
        if (file.length() < 12L) return false
        val header = ByteArray(12)
        FileInputStream(file).use { if (it.read(header) != 12) return false }
        return String(header, 0, 4, Charsets.US_ASCII) == "RIFF" &&
            String(header, 8, 4, Charsets.US_ASCII) == "WAVE"
    }

    private fun queryDisplayName(uri: Uri): String? {
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) return cursor.getString(0)
        }
        return null
    }

    override fun onDestroy() {
        Diagnostics.log("app", "activity.onDestroy.begin", data = mapOf("engine_ready" to engineReady), snapshot = true)
        player?.release()
        player = null
        if (engineReady) {
            val span = Diagnostics.span("engine", "engine.release")
            runCatching { VieNeuNative.release() }
                .onSuccess {
                    engineReady = false
                    span.end(true)
                }
                .onFailure {
                    Diagnostics.error("engine", "engine.release.failure", it)
                    span.end(false, mapOf("error" to (it.message ?: it.javaClass.simpleName)))
                }
        }
        executor.shutdownNow()
        Diagnostics.log("app", "activity.onDestroy.end", snapshot = true)
        super.onDestroy()
    }

    companion object {
        private const val REQUEST_REFERENCE = 1001
        private const val REQUEST_SAVE = 1002
        private const val REQUEST_DIAGNOSTICS = 1003
    }
}
