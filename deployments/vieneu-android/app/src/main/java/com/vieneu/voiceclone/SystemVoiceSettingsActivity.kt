package com.vieneu.voiceclone

import android.app.Activity
import android.app.AlertDialog
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.SeekBar
import android.widget.Spinner
import android.widget.TextView
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors
import kotlin.math.max

class SystemVoiceSettingsActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private var profiles: List<VoiceProfile> = emptyList()
    private var previewTrack: AudioTrack? = null

    private lateinit var voiceSpinner: Spinner
    private lateinit var rateSeek: SeekBar
    private lateinit var pitchSeek: SeekBar
    private lateinit var volumeSeek: SeekBar
    private lateinit var rateValue: TextView
    private lateinit var pitchValue: TextView
    private lateinit var volumeValue: TextView
    private lateinit var status: TextView
    private lateinit var saveActiveVoiceButton: Button
    private lateinit var deleteVoiceButton: Button
    private lateinit var previewButton: Button
    private lateinit var saveSettingsButton: Button
    private lateinit var cancelSettingsButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Diagnostics.start(this)
        setContentView(R.layout.activity_system_voice_settings)

        voiceSpinner = findViewById(R.id.systemVoiceSpinner)
        rateSeek = findViewById(R.id.rateSeek)
        pitchSeek = findViewById(R.id.pitchSeek)
        volumeSeek = findViewById(R.id.volumeSeek)
        rateValue = findViewById(R.id.rateValue)
        pitchValue = findViewById(R.id.pitchValue)
        volumeValue = findViewById(R.id.volumeValue)
        status = findViewById(R.id.systemVoiceStatus)
        saveActiveVoiceButton = findViewById(R.id.saveActiveVoiceButton)
        deleteVoiceButton = findViewById(R.id.deleteVoiceButton)
        previewButton = findViewById(R.id.previewSystemVoiceButton)
        saveSettingsButton = findViewById(R.id.saveSystemSettingsButton)
        cancelSettingsButton = findViewById(R.id.cancelSystemSettingsButton)

        val listener = object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                refreshControlLabels()
            }
            override fun onStartTrackingTouch(seekBar: SeekBar?) = Unit
            override fun onStopTrackingTouch(seekBar: SeekBar?) = Unit
        }
        rateSeek.setOnSeekBarChangeListener(listener)
        pitchSeek.setOnSeekBarChangeListener(listener)
        volumeSeek.setOnSeekBarChangeListener(listener)

        saveActiveVoiceButton.setOnClickListener { askAndSaveActiveVoice() }
        deleteVoiceButton.setOnClickListener { deleteSelectedVoice() }
        previewButton.setOnClickListener { previewSelectedVoice() }
        saveSettingsButton.setOnClickListener { saveSettings() }
        cancelSettingsButton.setOnClickListener {
            loadSavedSettings()
            status.text = "Đã hủy thay đổi chưa lưu."
        }

        refreshProfiles()
        loadSavedSettings()
    }

    override fun onResume() {
        super.onResume()
        refreshProfiles(keepSelection = true)
    }

    private fun refreshProfiles(keepSelection: Boolean = false) {
        val previousId = if (keepSelection) selectedProfile()?.id else null
        profiles = VoiceProfileStore.list(this)
        voiceSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            if (profiles.isEmpty()) listOf("Chưa có giọng đã lưu") else profiles.map {
                if (it.cacheReady) it.name else "${it.name} (cache chưa sẵn sàng)"
            },
        )
        val desiredId = previousId ?: VoiceProfileStore.loadSettings(this).profileId
        val selected = profiles.indexOfFirst { it.id == desiredId }
        if (selected >= 0) voiceSpinner.setSelection(selected)
        deleteVoiceButton.isEnabled = profiles.isNotEmpty()
        previewButton.isEnabled = profiles.any { it.cacheReady }
        saveSettingsButton.isEnabled = profiles.any { it.cacheReady }
        if (profiles.isEmpty()) status.text = "Chưa có giọng nào được lưu."
    }

    private fun loadSavedSettings() {
        val settings = VoiceProfileStore.loadSettings(this)
        val selected = profiles.indexOfFirst { it.id == settings.profileId }
        if (selected >= 0) voiceSpinner.setSelection(selected)
        rateSeek.progress = ((settings.rate - 0.5f) * 100f).toInt().coerceIn(0, rateSeek.max)
        pitchSeek.progress = ((settings.pitch - 0.5f) * 100f).toInt().coerceIn(0, pitchSeek.max)
        volumeSeek.progress = (settings.volume * 100f).toInt().coerceIn(0, volumeSeek.max)
        refreshControlLabels()
    }

    private fun currentSettings(): SystemVoiceSettings = SystemVoiceSettings(
        profileId = selectedProfile()?.id,
        rate = (0.5f + rateSeek.progress / 100f).coerceIn(0.5f, 2.0f),
        pitch = (0.5f + pitchSeek.progress / 100f).coerceIn(0.5f, 2.0f),
        volume = (volumeSeek.progress / 100f).coerceIn(0.0f, 1.0f),
    )

    private fun selectedProfile(): VoiceProfile? =
        profiles.getOrNull(voiceSpinner.selectedItemPosition)

    private fun refreshControlLabels() {
        val settings = currentSettings()
        rateValue.text = "Tốc độ: %.2f×".format(settings.rate)
        pitchValue.text = "Độ cao: %.2f×".format(settings.pitch)
        volumeValue.text = "Âm lượng: %d%%".format((settings.volume * 100f).toInt())
    }

    private fun askAndSaveActiveVoice() {
        val active = VoiceProfileStore.activeReference(this)
        if (active == null) {
            status.text = "Chưa có giọng hiện tại. Hãy sang phần tạo giọng, chọn WAV và tạo thử một câu trước."
            return
        }
        val input = EditText(this).apply {
            hint = "Tên giọng"
            setSingleLine(true)
        }
        AlertDialog.Builder(this)
            .setTitle("Lưu giọng")
            .setMessage("Giọng đã lưu sẽ giữ cache v4 để lần sau sử dụng ngay.")
            .setView(input)
            .setNegativeButton("Hủy", null)
            .setPositiveButton("Lưu") { _, _ ->
                val name = input.text.toString().trim()
                if (name.isBlank()) {
                    status.text = "Tên giọng không được để trống."
                } else {
                    saveActiveVoice(active, name)
                }
            }
            .show()
    }

    private fun saveActiveVoice(reference: File, name: String) {
        setBusy(true, "Đang chuẩn bị và lưu giọng…")
        executor.execute {
            try {
                ensureWarmReference(reference)
                val profile = VoiceProfileStore.saveVoice(this, reference, name)
                runOnUiThread {
                    refreshProfiles()
                    val index = profiles.indexOfFirst { it.id == profile.id }
                    if (index >= 0) voiceSpinner.setSelection(index)
                    status.text = "Đã lưu giọng “${profile.name}”. Cache v4 đã sẵn sàng."
                    setBusy(false)
                }
            } catch (t: Throwable) {
                Diagnostics.error("voice_profile", "voice_profile.save.failure", t)
                runOnUiThread {
                    status.text = "Không lưu được giọng: ${t.message ?: t.javaClass.simpleName}"
                    setBusy(false)
                }
            }
        }
    }

    private fun ensureWarmReference(reference: File) {
        if (VoiceProfileStore.hasWarmV4Caches(reference)) return
        if (!ModelManager.isReady(this)) {
            throw IllegalStateException("Mô hình chưa sẵn sàng.")
        }
        val generationId = "voice-profile-warm-${UUID.randomUUID()}"
        VieNeuEngine.ensureInitialized(this, generationId)
        val output = File(cacheDir, "$generationId.wav")
        val written = VieNeuNative.synthesize(
            "Xin chào.",
            reference.absolutePath,
            "voice-profile-warm",
            true,
            false,
            "",
            output.absolutePath,
        )
        output.delete()
        if (written == null) {
            throw IllegalStateException(VieNeuNative.lastError().ifBlank { "Không warm được cache giọng." })
        }
        if (!VoiceProfileStore.hasWarmV4Caches(reference)) {
            throw IllegalStateException("VieNeu chưa tạo đủ speaker-v4 và refcodes-v4 cho giọng này.")
        }
    }

    private fun deleteSelectedVoice() {
        val profile = selectedProfile() ?: return
        AlertDialog.Builder(this)
            .setTitle("Xóa giọng")
            .setMessage("Xóa giọng “${profile.name}”? Tệp giọng và cache đã lưu sẽ bị xóa.")
            .setNegativeButton("Hủy", null)
            .setPositiveButton("Xóa") { _, _ ->
                VoiceProfileStore.delete(this, profile.id)
                refreshProfiles()
                loadSavedSettings()
                status.text = "Đã xóa giọng “${profile.name}”."
            }
            .show()
    }

    private fun saveSettings() {
        val settings = currentSettings()
        val profile = selectedProfile()
        if (profile == null || !profile.cacheReady) {
            status.text = "Hãy chọn một giọng có cache v4 sẵn sàng."
            return
        }
        VoiceProfileStore.saveSettings(this, settings)
        status.text = "Đã lưu cấu hình giọng đọc hệ thống."
    }

    private fun previewSelectedVoice() {
        val profile = selectedProfile()
        if (profile == null || !profile.cacheReady) {
            status.text = "Hãy chọn một giọng đã lưu có cache v4."
            return
        }
        val settings = currentSettings()
        setBusy(true, "Đang tạo câu nghe thử…")
        executor.execute {
            val output = File(cacheDir, "system-voice-preview-${System.currentTimeMillis()}.wav")
            try {
                val generationId = "system-preview-${UUID.randomUUID()}"
                VieNeuEngine.ensureInitialized(this, generationId)
                val written = VieNeuNative.synthesize(
                    "Xin chào. Đây là giọng đọc hệ thống của VieNeu.",
                    profile.referenceFile.absolutePath,
                    profile.id,
                    true,
                    false,
                    "",
                    output.absolutePath,
                ) ?: throw IllegalStateException(
                    VieNeuNative.lastError().ifBlank { "Không tạo được câu nghe thử." }
                )
                if (written != output.absolutePath || !output.isFile) {
                    throw IllegalStateException("VieNeu không tạo được WAV nghe thử hợp lệ.")
                }
                val processed = PcmAudioProcessor.process(
                    WavPcmReader.read(output),
                    settings.rate,
                    settings.pitch,
                    settings.volume,
                )
                val bytes = WavPcmReader.toLittleEndianBytes(processed.samples)
                runOnUiThread {
                    playPcm(processed.sampleRate, bytes)
                    status.text = "Đang nghe thử “${profile.name}”."
                    setBusy(false)
                }
            } catch (t: Throwable) {
                Diagnostics.error("system_tts", "system_tts.preview.failure", t)
                runOnUiThread {
                    status.text = "Không nghe thử được: ${t.message ?: t.javaClass.simpleName}"
                    setBusy(false)
                }
            } finally {
                output.delete()
            }
        }
    }

    private fun playPcm(sampleRate: Int, bytes: ByteArray) {
        previewTrack?.stop()
        previewTrack?.release()
        previewTrack = null
        if (bytes.isEmpty()) return

        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build()
            )
            .setBufferSizeInBytes(max(minBuffer, bytes.size))
            .setTransferMode(AudioTrack.MODE_STATIC)
            .build()
        track.write(bytes, 0, bytes.size)
        track.notificationMarkerPosition = bytes.size / 2
        track.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
            override fun onMarkerReached(audioTrack: AudioTrack) {
                audioTrack.release()
                if (previewTrack === audioTrack) previewTrack = null
            }
            override fun onPeriodicNotification(audioTrack: AudioTrack) = Unit
        })
        previewTrack = track
        track.play()
    }

    private fun setBusy(busy: Boolean, message: String? = null) {
        saveActiveVoiceButton.isEnabled = !busy
        deleteVoiceButton.isEnabled = !busy && profiles.isNotEmpty()
        previewButton.isEnabled = !busy && profiles.any { it.cacheReady }
        saveSettingsButton.isEnabled = !busy && profiles.any { it.cacheReady }
        cancelSettingsButton.isEnabled = !busy
        if (message != null) status.text = message
    }

    override fun onDestroy() {
        previewTrack?.stop()
        previewTrack?.release()
        previewTrack = null
        executor.shutdownNow()
        super.onDestroy()
    }
}
