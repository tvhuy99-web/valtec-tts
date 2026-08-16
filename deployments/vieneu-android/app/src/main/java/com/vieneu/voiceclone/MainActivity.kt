package com.vieneu.voiceclone

import android.app.Activity
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.os.Bundle
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
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private var referenceFile: File? = null
    private var outputFile: File? = null
    private var player: MediaPlayer? = null
    private var engineReady = false

    private lateinit var modelStatus: TextView
    private lateinit var referenceStatus: TextView
    private lateinit var downloadDetail: TextView
    private lateinit var statusText: TextView
    private lateinit var downloadProgress: ProgressBar
    private lateinit var generateProgress: ProgressBar
    private lateinit var textInput: EditText
    private lateinit var styleSpinner: Spinner
    private lateinit var downloadButton: Button
    private lateinit var generateButton: Button
    private lateinit var playButton: Button
    private lateinit var saveButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        modelStatus = findViewById(R.id.modelStatus)
        referenceStatus = findViewById(R.id.referenceStatus)
        downloadDetail = findViewById(R.id.downloadDetail)
        statusText = findViewById(R.id.statusText)
        downloadProgress = findViewById(R.id.downloadProgress)
        generateProgress = findViewById(R.id.generateProgress)
        textInput = findViewById(R.id.textInput)
        styleSpinner = findViewById(R.id.styleSpinner)
        downloadButton = findViewById(R.id.downloadModelButton)
        generateButton = findViewById(R.id.generateButton)
        playButton = findViewById(R.id.playButton)
        saveButton = findViewById(R.id.saveButton)

        styleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Tin tức", "Đọc truyện")
        )

        downloadButton.setOnClickListener { downloadModel() }
        findViewById<Button>(R.id.pickReferenceButton).setOnClickListener { pickReference() }
        generateButton.setOnClickListener { generate() }
        playButton.setOnClickListener { playOutput() }
        saveButton.setOnClickListener { saveOutput() }

        refreshModelStatus()
    }

    private fun refreshModelStatus() {
        val root = ModelManager.modelDir(this)
        if (ModelManager.isReady(root)) {
            modelStatus.text = "Mô hình: đã sẵn sàng"
            downloadButton.text = "Kiểm tra / tải lại phần còn thiếu"
        } else {
            modelStatus.text = "Mô hình: chưa tải đủ (~660 MB)"
            downloadButton.text = "Tải mô hình offline"
        }
        downloadDetail.text = root.absolutePath
    }

    private fun downloadModel() {
        downloadButton.isEnabled = false
        statusText.text = "Đang tải mô hình VieNeu..."
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
                runOnUiThread {
                    downloadProgress.progress = 100
                    statusText.text = "Tải mô hình hoàn tất."
                    downloadButton.isEnabled = true
                    refreshModelStatus()
                }
            } catch (t: Throwable) {
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
        startActivityForResult(intent, REQUEST_REFERENCE)
    }

    private fun generate() {
        val text = textInput.text.toString().trim()
        val ref = referenceFile
        if (!ModelManager.isReady(this)) {
            statusText.text = "Hãy tải đầy đủ mô hình trước."
            return
        }
        if (ref == null || !ref.isFile) {
            statusText.text = "Hãy chọn một tệp WAV làm giọng mẫu."
            return
        }
        if (text.isBlank()) {
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

        executor.execute {
            try {
                if (!engineReady) {
                    val threads = Runtime.getRuntime().availableProcessors().coerceIn(2, 6)
                    val error = VieNeuNative.initialize(ModelManager.modelDir(this).absolutePath, threads)
                    if (error.isNotEmpty()) throw IllegalStateException(error)
                    engineReady = true
                    runOnUiThread { statusText.text = "Model đã nạp. Đang clone và tổng hợp giọng..." }
                }

                val audio = VieNeuNative.synthesize(text, ref.absolutePath, style)
                    ?: throw IllegalStateException(VieNeuNative.lastError().ifBlank { "Native synthesis trả về null." })
                if (audio.isEmpty()) throw IllegalStateException("Không tạo được mẫu âm thanh nào.")

                val out = File(filesDir, "outputs/vieneu_${System.currentTimeMillis()}.wav")
                WavWriter.writeMonoFloat(out, audio, VieNeuNative.sampleRate())
                outputFile = out
                runOnUiThread {
                    generateProgress.visibility = View.GONE
                    generateButton.isEnabled = true
                    playButton.isEnabled = true
                    saveButton.isEnabled = true
                    statusText.text = "Hoàn tất: ${out.name} (${audio.size / VieNeuNative.sampleRate()} giây)"
                }
            } catch (t: Throwable) {
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
        runCatching {
            player?.release()
            player = MediaPlayer().apply {
                setDataSource(file.absolutePath)
                setOnCompletionListener { it.release(); if (player === it) player = null }
                prepare()
                start()
            }
            statusText.text = "Đang phát ${file.name}"
        }.onFailure { statusText.text = "Không phát được WAV: ${it.message}" }
    }

    private fun saveOutput() {
        val file = outputFile ?: return
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "audio/wav"
            putExtra(Intent.EXTRA_TITLE, file.name)
        }
        startActivityForResult(intent, REQUEST_SAVE)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        when (requestCode) {
            REQUEST_REFERENCE -> importReference(uri)
            REQUEST_SAVE -> exportOutput(uri)
        }
    }

    private fun importReference(uri: Uri) {
        executor.execute {
            try {
                val dir = File(filesDir, "references").apply { mkdirs() }
                val target = File(dir, "reference.wav")
                contentResolver.openInputStream(uri)?.use { input ->
                    target.outputStream().use { output -> input.copyTo(output, 1024 * 1024) }
                } ?: throw IllegalStateException("Không mở được tệp đã chọn.")
                if (!looksLikeWav(target)) {
                    target.delete()
                    throw IllegalArgumentException("Tệp đã chọn không phải WAV RIFF/WAVE hợp lệ.")
                }
                referenceFile = target
                val name = queryDisplayName(uri) ?: "reference.wav"
                runOnUiThread {
                    referenceStatus.text = "Giọng mẫu: $name (${target.length() / 1024} KB)"
                    statusText.text = "Đã nhập giọng mẫu. VieNeu sẽ dùng tối đa 8 giây đầu sau xử lý."
                }
            } catch (t: Throwable) {
                runOnUiThread { statusText.text = "Lỗi giọng mẫu: ${t.message}" }
            }
        }
    }

    private fun exportOutput(uri: Uri) {
        val source = outputFile ?: return
        executor.execute {
            try {
                contentResolver.openOutputStream(uri)?.use { output ->
                    source.inputStream().use { input -> input.copyTo(output, 1024 * 1024) }
                } ?: throw IllegalStateException("Không mở được nơi lưu.")
                runOnUiThread { statusText.text = "Đã lưu WAV thành công." }
            } catch (t: Throwable) {
                runOnUiThread { statusText.text = "Lỗi lưu WAV: ${t.message}" }
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
        player?.release()
        executor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val REQUEST_REFERENCE = 1001
        private const val REQUEST_SAVE = 1002
    }
}
