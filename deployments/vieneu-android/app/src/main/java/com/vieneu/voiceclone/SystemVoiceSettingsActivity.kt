package com.vieneu.voiceclone

import android.app.Activity
import android.app.AlertDialog
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Bundle
import android.view.View
import android.widget.AdapterView
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
    private var voices: List<VoiceCatalogEntry> = emptyList()
    private var previewTrack: AudioTrack? = null
    private var busy = false

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
        voiceSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                refreshVoiceActions()
                selectedVoice()?.let { voice ->
                    status.text = buildString {
                        append(voice.label)
                        append(" · ")
                        append(
                            when (voice.source) {
                                VoiceCatalogSource.PRESET -> "giọng có sẵn"
                                VoiceCatalogSource.SAVED -> "giọng đã lưu"
                            },
                        )
                        if (!voice.isReady) append(" · chưa sẵn sàng")
                    }
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {
                refreshVoiceActions()
            }
        }

        saveActiveVoiceButton.setOnClickListener { askAndSaveActiveVoice() }
        deleteVoiceButton.setOnClickListener { deleteSelectedVoice() }
        previewButton.setOnClickListener { previewSelectedVoice() }
        saveSettingsButton.setOnClickListener { saveSettingsAndExit() }
        cancelSettingsButton.setOnClickListener {
            setResult(RESULT_CANCELED)
            finish()
        }

        refreshVoices()
        loadSavedSettings()
        Diagnostics.log(
            "system_tts",
            "system_tts.settings_opened",
            data = mapOf(
                "engine_discoverable" to VoiceCatalog.isEngineDiscoverable(this),
                "voice_count" to voices.size,
            ),
        )
    }

    override fun onResume() {
        super.onResume()
        refreshVoices(keepSelection = true)
    }

    private fun refreshVoices(keepSelection: Boolean = false, preferredKey: String? = null) {
        val previousKey = if (keepSelection) selectedVoice()?.key else null
        voices = VoiceCatalog.list(this)
        voiceSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            if (voices.isEmpty()) {
                listOf("Chưa có giọng VieNeu sẵn sàng")
            } else {
                voices.map { voice ->
                    if (voice.isReady) voice.label else "${voice.label} (chưa sẵn sàng)"
                }
            },
        )
        val savedKey = VoiceProfileStore.loadSettings(this).voiceKey
        val desiredKey = sequenceOf(preferredKey, previousKey, savedKey, VoiceCatalog.default(this)?.key)
            .filterNotNull()
            .firstOrNull { key -> voices.any { it.key == key } }
        val selected = voices.indexOfFirst { it.key == desiredKey }
        if (selected >= 0) voiceSpinner.setSelection(selected, false)
        refreshVoiceActions()
        if (voices.isEmpty()) {
            status.text = if (ModelManager.isReady(this)) {
                "Không đọc được danh sách giọng VieNeu."
            } else {
                "Mô hình chưa sẵn sàng nên chưa thể dùng giọng hệ thống."
            }
        }
    }

    private fun loadSavedSettings() {
        val settings = VoiceProfileStore.loadSettings(this)
        val selected = voices.indexOfFirst { it.key == settings.voiceKey }
        if (selected >= 0) voiceSpinner.setSelection(selected, false)
        rateSeek.progress = ((settings.rate - 0.5f) * 100f).toInt().coerceIn(0, rateSeek.max)
        pitchSeek.progress = ((settings.pitch - 0.5f) * 100f).toInt().coerceIn(0, pitchSeek.max)
        volumeSeek.progress = (settings.volume * 100f).toInt().coerceIn(0, volumeSeek.max)
        refreshControlLabels()
        refreshVoiceActions()
    }

    private fun currentSettings(): SystemVoiceSettings {
        val voice = selectedVoice()
        return SystemVoiceSettings(
            profileId = voice?.profileId,
            rate = (0.5f + rateSeek.progress / 100f).coerceIn(0.5f, 2.0f),
            pitch = (0.5f + pitchSeek.progress / 100f).coerceIn(0.5f, 2.0f),
            volume = (volumeSeek.progress / 100f).coerceIn(0.0f, 1.0f),
            voiceKey = voice?.key,
        )
    }

    private fun selectedVoice(): VoiceCatalogEntry? =
        voices.getOrNull(voiceSpinner.selectedItemPosition)

    private fun refreshControlLabels() {
        val settings = currentSettings()
        rateValue.text = "Tốc độ: %.2f×".format(settings.rate)
        pitchValue.text = "Độ cao: %.2f×".format(settings.pitch)
        volumeValue.text = "Âm lượng: %d%%".format((settings.volume * 100f).toInt())
    }

    private fun refreshVoiceActions() {
        val voice = selectedVoice()
        val activeReferenceReady = VoiceProfileStore.activeReference(this)?.isFile == true
        saveActiveVoiceButton.isEnabled = !busy && activeReferenceReady
        deleteVoiceButton.isEnabled = !busy && voice?.source == VoiceCatalogSource.SAVED
        previewButton.isEnabled = !busy && voice?.isReady == true
        saveSettingsButton.isEnabled = !busy && voice?.isReady == true
        cancelSettingsButton.isEnabled = !busy
    }

    private fun askAndSaveActiveVoice() {
        val active = VoiceProfileStore.activeReference(this)
        if (active == null) {
            status.text = "Chưa có giọng clone hiện tại để lưu."
            return
        }
        val input = EditText(this).apply {
            hint = "Tên giọng"
            setSingleLine(true)
        }
        AlertDialog.Builder(this)
            .setTitle("Lưu giọng clone")
            .setMessage("Giọng này sẽ xuất hiện đồng thời trong danh sách tạo giọng và danh sách giọng hệ thống.")
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
                    refreshVoices(preferredKey = VoiceCatalog.savedKey(profile.id))
                    status.text = "Đã lưu giọng “${profile.name}”. Hai danh sách giọng đã đồng bộ."
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
        val voice = selectedVoice() ?: return
        if (voice.source != VoiceCatalogSource.SAVED || voice.profileId.isNullOrBlank()) {
            status.text = "Giọng có sẵn của VieNeu không thể xóa."
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Xóa giọng")
            .setMessage("Xóa giọng “${voice.label}”? Giọng này sẽ biến mất khỏi cả hai danh sách.")
            .setNegativeButton("Hủy", null)
            .setPositiveButton("Xóa") { _, _ ->
                VoiceProfileStore.delete(this, voice.profileId)
                refreshVoices()
                loadSavedSettings()
                status.text = "Đã xóa giọng “${voice.label}” khỏi cả hai danh sách."
            }
            .show()
    }

    private fun saveSettingsAndExit() {
        val settings = currentSettings()
        val voice = selectedVoice()
        if (voice == null || !voice.isReady) {
            status.text = "Hãy chọn một giọng VieNeu đã sẵn sàng."
            return
        }
        VoiceProfileStore.saveSettings(this, settings)
        VoiceCatalog.notifyChanged(this, "system_voice_settings_saved")
        Diagnostics.log(
            "system_tts",
            "system_tts.settings_save_and_exit",
            data = mapOf(
                "voice_key" to voice.key,
                "voice_label" to voice.label,
                "voice_source" to voice.source.name,
            ),
        )
        setResult(RESULT_OK)
        finish()
    }

    private fun previewSelectedVoice() {
        val voice = selectedVoice()
        if (voice == null || !voice.isReady) {
            status.text = "Hãy chọn một giọng VieNeu đã sẵn sàng."
            return
        }
        val referencePath = when (voice.source) {
            VoiceCatalogSource.PRESET -> ""
            VoiceCatalogSource.SAVED -> voice.referenceFile?.takeIf { it.isFile }?.absolutePath
                ?: run {
                    status.text = "Giọng đã lưu bị thiếu tệp mẫu."
                    return
                }
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
                    referencePath,
                    voice.nativeVoiceId,
                    true,
                    false,
                    "",
                    output.absolutePath,
                ) ?: throw IllegalStateException(
                    VieNeuNative.lastError().ifBlank { "Không tạo được câu nghe thử." }
                )
                if (written != output.absolutePath || !output.isFile || output.length() <= 44L) {
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
                    status.text = "Đang nghe thử “${voice.label}”."
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

    private fun setBusy(value: Boolean, message: String? = null) {
        busy = value
        rateSeek.isEnabled = !value
        pitchSeek.isEnabled = !value
        volumeSeek.isEnabled = !value
        voiceSpinner.isEnabled = !value
        refreshVoiceActions()
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
