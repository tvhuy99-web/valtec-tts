package com.vieneu.voiceclone

import android.media.AudioFormat
import android.os.SystemClock
import android.speech.tts.SynthesisCallback
import android.speech.tts.SynthesisRequest
import android.speech.tts.TextToSpeech
import android.speech.tts.TextToSpeechService
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

class VieNeuTtsService : TextToSpeechService() {
    private val stopEpoch = AtomicInteger(0)
    private val warmExecutor = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        Diagnostics.start(this)
        warmExecutor.execute {
            runCatching { warmConfiguredVoice() }
                .onFailure {
                    Diagnostics.error("system_tts", "system_tts.warm.failure", it)
                }
        }
    }

    override fun onDestroy() {
        stopEpoch.incrementAndGet()
        runCatching { VieNeuNative.cancel() }
        warmExecutor.shutdownNow()
        super.onDestroy()
    }

    override fun onGetLanguage(): Array<String> = arrayOf("vie", "VNM", "")

    override fun onIsLanguageAvailable(lang: String?, country: String?, variant: String?): Int {
        val normalized = lang?.lowercase().orEmpty()
        return when (normalized) {
            "vi", "vie" -> if (country.isNullOrBlank() || country.equals("VN", true) || country.equals("VNM", true)) {
                TextToSpeech.LANG_COUNTRY_AVAILABLE
            } else {
                TextToSpeech.LANG_AVAILABLE
            }
            else -> TextToSpeech.LANG_NOT_SUPPORTED
        }
    }

    override fun onLoadLanguage(lang: String?, country: String?, variant: String?): Int =
        onIsLanguageAvailable(lang, country, variant)

    override fun onStop() {
        val epoch = stopEpoch.incrementAndGet()
        runCatching { VieNeuNative.cancel() }
            .onFailure {
                Diagnostics.error("system_tts", "system_tts.cancel_native.failure", it)
            }
        Diagnostics.log("system_tts", "system_tts.stop_requested", data = mapOf("stop_epoch" to epoch))
    }

    override fun onSynthesizeText(request: SynthesisRequest, callback: SynthesisCallback) {
        val requestStartNs = SystemClock.elapsedRealtimeNanos()
        val epoch = stopEpoch.get()
        val text = request.charSequenceText?.toString()?.trim().orEmpty()
        if (text.isBlank()) {
            callback.start(48_000, AudioFormat.ENCODING_PCM_16BIT, 1)
            callback.done()
            return
        }

        val settings = VoiceProfileStore.loadSettings(this)
        val profile = VoiceProfileStore.get(this, settings.profileId)
        if (profile == null || !profile.cacheReady || !profile.referenceFile.isFile) {
            Diagnostics.log(
                "system_tts",
                "system_tts.synthesis.rejected",
                level = "WARN",
                data = mapOf("reason" to "voice_profile_missing"),
            )
            callback.error()
            return
        }
        if (!ModelManager.isReady(this)) {
            Diagnostics.log(
                "system_tts",
                "system_tts.synthesis.rejected",
                level = "WARN",
                data = mapOf("reason" to "model_not_ready"),
            )
            callback.error()
            return
        }

        val requestRate = request.speechRate.takeIf { it > 0 }?.div(100.0f) ?: 1.0f
        val requestPitch = request.pitch.takeIf { it > 0 }?.div(100.0f) ?: 1.0f
        val effectiveRate = (settings.rate * requestRate).coerceIn(0.5f, 2.0f)
        val effectivePitch = (settings.pitch * requestPitch).coerceIn(0.5f, 2.0f)
        val chunks = splitText(text)
        val synthesisId = "system-tts-${UUID.randomUUID()}"
        val engineWasReady = VieNeuEngine.isReady()

        val totalSpan = Diagnostics.span(
            "system_tts",
            "system_tts.synthesis",
            mapOf(
                "synthesis_id" to synthesisId,
                "text_chars" to text.length,
                "chunks" to chunks.size,
                "profile_id" to profile.id,
                "profile_name" to profile.name,
                "rate" to effectiveRate,
                "pitch" to effectivePitch,
                "volume" to settings.volume,
                "engine_already_ready" to engineWasReady,
            ),
        )

        var callbackStarted = false
        var totalPcmBytes = 0L
        var firstPcmMs: Double? = null
        try {
            VieNeuEngine.ensureInitialized(this, synthesisId)
            for ((index, chunk) in chunks.withIndex()) {
                if (stopEpoch.get() != epoch) {
                    totalSpan.end(false, mapOf("cancelled" to true, "chunk_index" to index, "first_pcm_ms" to firstPcmMs))
                    return
                }

                val output = File(cacheDir, "$synthesisId-$index.wav")
                try {
                    val written = VieNeuNative.synthesize(
                        chunk,
                        profile.referenceFile.absolutePath,
                        profile.id,
                        true,
                        false,
                        "",
                        output.absolutePath,
                    ) ?: throw IllegalStateException(
                        VieNeuNative.lastError().ifBlank { "VieNeu không tạo được âm thanh." }
                    )
                    if (written != output.absolutePath || !output.isFile || output.length() <= 44L) {
                        throw IllegalStateException("VieNeu trả về WAV hệ thống không hợp lệ.")
                    }

                    val processed = PcmAudioProcessor.process(
                        WavPcmReader.read(output),
                        effectiveRate,
                        effectivePitch,
                        settings.volume,
                    )
                    if (stopEpoch.get() != epoch) {
                        totalSpan.end(false, mapOf("cancelled" to true, "chunk_index" to index, "first_pcm_ms" to firstPcmMs))
                        return
                    }

                    if (!callbackStarted) {
                        if (callback.start(processed.sampleRate, AudioFormat.ENCODING_PCM_16BIT, 1) != TextToSpeech.SUCCESS) {
                            throw IllegalStateException("Android TTS từ chối bắt đầu luồng PCM.")
                        }
                        callbackStarted = true
                    }
                    val bytes = WavPcmReader.toLittleEndianBytes(processed.samples)
                    val maxChunk = callback.maxBufferSize.coerceAtLeast(1024)
                    var offset = 0
                    while (offset < bytes.size) {
                        if (stopEpoch.get() != epoch) {
                            totalSpan.end(false, mapOf("cancelled" to true, "chunk_index" to index, "first_pcm_ms" to firstPcmMs))
                            return
                        }
                        val count = minOf(maxChunk, bytes.size - offset)
                        if (callback.audioAvailable(bytes, offset, count) != TextToSpeech.SUCCESS) {
                            throw IllegalStateException("Android TTS từ chối dữ liệu PCM.")
                        }
                        if (firstPcmMs == null) {
                            firstPcmMs = (SystemClock.elapsedRealtimeNanos() - requestStartNs) / 1_000_000.0
                            Diagnostics.log(
                                "system_tts",
                                "system_tts.first_pcm",
                                data = mapOf(
                                    "synthesis_id" to synthesisId,
                                    "first_pcm_ms" to firstPcmMs,
                                    "engine_already_ready" to engineWasReady,
                                    "profile_cache_ready" to profile.cacheReady,
                                    "text_chars" to text.length,
                                    "first_chunk_chars" to chunk.length,
                                    "sample_rate_hz" to processed.sampleRate,
                                ),
                            )
                        }
                        offset += count
                        totalPcmBytes += count
                    }
                } finally {
                    output.delete()
                }
            }

            if (stopEpoch.get() == epoch) {
                if (!callbackStarted) {
                    callback.start(48_000, AudioFormat.ENCODING_PCM_16BIT, 1)
                }
                callback.done()
                totalSpan.end(
                    true,
                    mapOf(
                        "pcm_bytes" to totalPcmBytes,
                        "first_pcm_ms" to firstPcmMs,
                        "engine_ready" to VieNeuEngine.isReady(),
                        "engine_already_ready" to engineWasReady,
                    ),
                )
            }
        } catch (t: Throwable) {
            val cancelled = stopEpoch.get() != epoch ||
                (t.message?.contains("VIENEU_CANCELLED", ignoreCase = true) == true)
            if (cancelled) {
                totalSpan.end(false, mapOf("cancelled" to true, "first_pcm_ms" to firstPcmMs))
                Diagnostics.log(
                    "system_tts",
                    "system_tts.synthesis.cancelled",
                    data = mapOf("synthesis_id" to synthesisId, "first_pcm_ms" to firstPcmMs),
                )
                return
            }
            Diagnostics.error(
                "system_tts",
                "system_tts.synthesis.failure",
                t,
                mapOf("synthesis_id" to synthesisId, "profile_id" to profile.id, "first_pcm_ms" to firstPcmMs),
            )
            totalSpan.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName), "first_pcm_ms" to firstPcmMs))
            callback.error()
        }
    }

    private fun warmConfiguredVoice() {
        val settings = VoiceProfileStore.loadSettings(this)
        val profile = VoiceProfileStore.get(this, settings.profileId) ?: return
        if (!profile.cacheReady || !ModelManager.isReady(this)) return
        val warmId = "system-tts-warm-${UUID.randomUUID()}"
        val initialized = VieNeuEngine.ensureInitialized(this, warmId)
        Diagnostics.log(
            "system_tts",
            "system_tts.warm.complete",
            data = mapOf(
                "profile_id" to profile.id,
                "engine_initialized_now" to initialized,
                "cache_ready" to profile.cacheReady,
            ),
        )
    }

    private fun splitText(text: String): List<String> {
        if (text.length <= MAX_CHARS_PER_CHUNK) return listOf(text)
        val result = ArrayList<String>()
        var remaining = text.trim()
        while (remaining.isNotEmpty()) {
            if (remaining.length <= MAX_CHARS_PER_CHUNK) {
                result += remaining
                break
            }
            val window = remaining.substring(0, MAX_CHARS_PER_CHUNK + 1)
            var cut = -1
            for (i in window.lastIndex downTo MIN_CHARS_BEFORE_SPLIT) {
                val c = window[i]
                if (c == '.' || c == '!' || c == '?' || c == ';' || c == ':' || c == '\n') {
                    cut = i + 1
                    break
                }
            }
            if (cut < 0) {
                for (i in MAX_CHARS_PER_CHUNK downTo MIN_CHARS_BEFORE_SPLIT) {
                    if (window[i].isWhitespace()) {
                        cut = i
                        break
                    }
                }
            }
            if (cut <= 0) cut = MAX_CHARS_PER_CHUNK
            result += remaining.substring(0, cut).trim()
            remaining = remaining.substring(cut).trimStart()
        }
        return result.filter { it.isNotBlank() }
    }

    companion object {
        private const val MAX_CHARS_PER_CHUNK = 280
        private const val MIN_CHARS_BEFORE_SPLIT = 120
    }
}
