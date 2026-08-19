package com.vieneu.voiceclone

import android.media.AudioFormat
import android.os.SystemClock
import android.speech.tts.SynthesisCallback
import android.speech.tts.SynthesisRequest
import android.speech.tts.TextToSpeech
import android.speech.tts.TextToSpeechService
import android.speech.tts.Voice
import android.util.LruCache
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.roundToInt

class VieNeuTtsService : TextToSpeechService() {
    private val stopEpoch = AtomicInteger(0)
    private val warmExecutor = Executors.newSingleThreadExecutor()
    private val pcmCache = object : LruCache<String, Pcm16Audio>(PCM_CACHE_BYTES) {
        override fun sizeOf(key: String, value: Pcm16Audio): Int =
            (value.samples.size.toLong() * 2L).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
    }

    override fun onCreate() {
        super.onCreate()
        Diagnostics.start(this)
        configureNativeDiagnostics()
        Diagnostics.log(
            "system_tts",
            "system_tts.service_created",
            data = mapOf(
                "package" to packageName,
                "engine_discoverable" to VoiceCatalog.isEngineDiscoverable(this),
                "catalog_voice_count" to VoiceCatalog.list(this).size,
                "audio_transport" to "jni_float_direct",
                "utterance_split" to false,
                "pcm_cache_bytes" to PCM_CACHE_BYTES,
            ),
        )
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
        pcmCache.evictAll()
        warmExecutor.shutdownNow()
        super.onDestroy()
    }

    override fun onGetLanguage(): Array<String> = arrayOf("vie", "VNM", "")

    override fun onIsLanguageAvailable(lang: String?, country: String?, variant: String?): Int {
        val normalized = lang?.lowercase().orEmpty()
        return when (normalized) {
            "vi", "vie" -> if (
                country.isNullOrBlank() ||
                country.equals("VN", true) ||
                country.equals("VNM", true)
            ) {
                TextToSpeech.LANG_COUNTRY_AVAILABLE
            } else {
                TextToSpeech.LANG_AVAILABLE
            }
            else -> TextToSpeech.LANG_NOT_SUPPORTED
        }
    }

    override fun onLoadLanguage(lang: String?, country: String?, variant: String?): Int =
        onIsLanguageAvailable(lang, country, variant)

    override fun onGetVoices(): MutableList<Voice> {
        val voices = VoiceCatalog.list(this)
            .filter { it.isReady }
            .map { entry ->
                Voice(
                    entry.key,
                    Locale.forLanguageTag("vi-VN"),
                    Voice.QUALITY_HIGH,
                    Voice.LATENCY_LOW,
                    false,
                    emptySet(),
                )
            }
            .toMutableList()
        Diagnostics.log(
            "system_tts",
            "system_tts.voices_queried",
            data = mapOf(
                "voice_count" to voices.size,
                "voice_names" to voices.joinToString(",") { it.name },
            ),
        )
        return voices
    }

    override fun onIsValidVoiceName(voiceName: String?): Int =
        if (VoiceCatalog.find(this, voiceName)?.isReady == true) TextToSpeech.SUCCESS
        else TextToSpeech.ERROR

    override fun onLoadVoice(voiceName: String?): Int {
        val voice = VoiceCatalog.find(this, voiceName)
        val result = if (voice?.isReady == true) TextToSpeech.SUCCESS else TextToSpeech.ERROR
        Diagnostics.log(
            "system_tts",
            "system_tts.voice_load",
            data = mapOf(
                "voice_name" to voiceName,
                "resolved_label" to voice?.label,
                "resolved_source" to voice?.source?.name,
                "result" to result,
            ),
        )
        return result
    }

    override fun onGetDefaultVoiceNameFor(
        lang: String?,
        country: String?,
        variant: String?,
    ): String {
        if (onIsLanguageAvailable(lang, country, variant) < TextToSpeech.LANG_AVAILABLE) return ""
        val settings = VoiceProfileStore.loadSettings(this)
        return VoiceCatalog.resolve(this, settings)
            ?.takeIf { it.isReady }
            ?.key
            ?: VoiceCatalog.default(this)?.takeIf { it.isReady }?.key.orEmpty()
    }

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
            callback.start(SAMPLE_RATE, AudioFormat.ENCODING_PCM_16BIT, 1)
            callback.done()
            return
        }

        val settings = VoiceProfileStore.loadSettings(this)
        val requestedVoiceName = request.voiceName?.takeIf { it.isNotBlank() }
        val voice = requestedVoiceName
            ?.let { VoiceCatalog.find(this, it) }
            ?.takeIf { it.isReady }
            ?: VoiceCatalog.resolve(this, settings)?.takeIf { it.isReady }
            ?: VoiceCatalog.default(this)?.takeIf { it.isReady }
        if (voice == null) {
            Diagnostics.log(
                "system_tts",
                "system_tts.synthesis.rejected",
                level = "WARN",
                data = mapOf(
                    "reason" to "voice_missing_or_not_ready",
                    "requested_voice_name" to requestedVoiceName,
                    "catalog_voice_count" to VoiceCatalog.list(this).size,
                ),
            )
            callback.error()
            return
        }
        if (!ModelManager.isReady(this)) {
            Diagnostics.log(
                "system_tts",
                "system_tts.synthesis.rejected",
                level = "WARN",
                data = mapOf("reason" to "model_not_ready", "voice_key" to voice.key),
            )
            callback.error()
            return
        }

        val referencePath = when (voice.source) {
            VoiceCatalogSource.PRESET -> ""
            VoiceCatalogSource.SAVED -> voice.referenceFile
                ?.takeIf { it.isFile && it.length() > 44L }
                ?.absolutePath
                ?: run {
                    Diagnostics.log(
                        "system_tts",
                        "system_tts.synthesis.rejected",
                        level = "WARN",
                        data = mapOf("reason" to "saved_voice_reference_missing", "voice_key" to voice.key),
                    )
                    callback.error()
                    return
                }
        }

        val requestRate = request.speechRate.takeIf { it > 0 }?.div(100.0f) ?: 1.0f
        val requestPitch = request.pitch.takeIf { it > 0 }?.div(100.0f) ?: 1.0f
        val effectiveRate = (settings.rate * requestRate).coerceIn(0.5f, 2.0f)
        val effectivePitch = (settings.pitch * requestPitch).coerceIn(0.5f, 2.0f)
        val synthesisId = "system-tts-${UUID.randomUUID()}"
        val engineWasReady = VieNeuEngine.isReady()
        val cacheKey = pcmCacheKey(
            voice.key,
            text,
            effectiveRate,
            effectivePitch,
            settings.volume,
        )

        val totalSpan = Diagnostics.span(
            "system_tts",
            "system_tts.synthesis",
            mapOf(
                "synthesis_id" to synthesisId,
                "text_chars" to text.length,
                "chunks" to 1,
                "utterance_split" to false,
                "requested_voice_name" to requestedVoiceName,
                "voice_key" to voice.key,
                "voice_label" to voice.label,
                "voice_source" to voice.source.name,
                "native_voice_id" to voice.nativeVoiceId,
                "profile_id" to voice.profileId,
                "rate" to effectiveRate,
                "pitch" to effectivePitch,
                "volume" to settings.volume,
                "engine_already_ready" to engineWasReady,
                "audio_transport" to "jni_float_direct",
            ),
        )

        var firstPcmMs: Double? = null
        var totalPcmBytes = 0L
        var cacheHit = false
        try {
            val cached = pcmCache.get(cacheKey)
            val processed = if (cached != null) {
                cacheHit = true
                Diagnostics.log(
                    "system_tts",
                    "system_tts.pcm_cache.hit",
                    data = mapOf(
                        "synthesis_id" to synthesisId,
                        "voice_key" to voice.key,
                        "text_chars" to text.length,
                        "pcm_samples" to cached.samples.size,
                    ),
                )
                cached
            } else {
                Diagnostics.log(
                    "system_tts",
                    "system_tts.pcm_cache.miss",
                    data = mapOf(
                        "synthesis_id" to synthesisId,
                        "voice_key" to voice.key,
                        "text_chars" to text.length,
                    ),
                )
                VieNeuEngine.ensureInitialized(this, synthesisId)
                if (stopEpoch.get() != epoch) {
                    finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                    return
                }

                // Start Android's sink before the expensive model call. No audio is emitted
                // yet; this only lets the framework prepare its AudioTrack in parallel.
                if (callback.start(SAMPLE_RATE, AudioFormat.ENCODING_PCM_16BIT, 1) != TextToSpeech.SUCCESS) {
                    if (stopEpoch.get() != epoch) {
                        finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                        return
                    }
                    throw IllegalStateException("Android TTS từ chối bắt đầu luồng PCM.")
                }

                val nativeStartNs = SystemClock.elapsedRealtimeNanos()
                val direct = VieNeuNative.synthesizeDirect(
                    text,
                    referencePath,
                    voice.nativeVoiceId,
                    true,
                    false,
                    "",
                ) ?: throw IllegalStateException(
                    VieNeuNative.lastError().ifBlank { "VieNeu direct PCM không tạo được âm thanh." }
                )
                val nativeDirectMs =
                    (SystemClock.elapsedRealtimeNanos() - nativeStartNs) / 1_000_000.0
                if (stopEpoch.get() != epoch) {
                    finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                    return
                }
                val sampleRate = VieNeuNative.sampleRate().takeIf { it > 0 } ?: SAMPLE_RATE
                val pcm = floatToPcm16(direct, sampleRate)
                val result = PcmAudioProcessor.process(
                    pcm,
                    effectiveRate,
                    effectivePitch,
                    settings.volume,
                )
                pcmCache.put(cacheKey, result)
                Diagnostics.log(
                    "system_tts",
                    "system_tts.pcm_cache.store",
                    data = mapOf(
                        "synthesis_id" to synthesisId,
                        "voice_key" to voice.key,
                        "text_chars" to text.length,
                        "pcm_samples" to result.samples.size,
                        "native_direct_ms" to nativeDirectMs,
                        "cache_bytes" to pcmCache.size(),
                    ),
                )
                result
            }

            if (cacheHit) {
                if (callback.start(processed.sampleRate, AudioFormat.ENCODING_PCM_16BIT, 1) != TextToSpeech.SUCCESS) {
                    if (stopEpoch.get() != epoch) {
                        finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                        return
                    }
                    throw IllegalStateException("Android TTS từ chối bắt đầu PCM cache.")
                }
            }

            val bytes = WavPcmReader.toLittleEndianBytes(processed.samples)
            val maxChunk = callback.maxBufferSize.coerceAtLeast(1024)
            var offset = 0
            while (offset < bytes.size) {
                if (stopEpoch.get() != epoch) {
                    finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                    return
                }
                val count = minOf(maxChunk, bytes.size - offset)
                val result = callback.audioAvailable(bytes, offset, count)
                if (result != TextToSpeech.SUCCESS) {
                    // TalkBack frequently calls stop() while a focus is changing. Treat a
                    // callback rejection after that stop as normal cancellation, not ERROR.
                    if (stopEpoch.get() != epoch) {
                        finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                        return
                    }
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
                            "voice_key" to voice.key,
                            "voice_source" to voice.source.name,
                            "text_chars" to text.length,
                            "sample_rate_hz" to processed.sampleRate,
                            "cache_hit" to cacheHit,
                            "audio_transport" to "jni_float_direct",
                            "utterance_split" to false,
                        ),
                    )
                }
                offset += count
                totalPcmBytes += count
            }

            if (stopEpoch.get() == epoch) {
                callback.done()
                totalSpan.end(
                    true,
                    mapOf(
                        "pcm_bytes" to totalPcmBytes,
                        "first_pcm_ms" to firstPcmMs,
                        "engine_ready" to VieNeuEngine.isReady(),
                        "engine_already_ready" to engineWasReady,
                        "voice_key" to voice.key,
                        "cache_hit" to cacheHit,
                        "audio_transport" to "jni_float_direct",
                        "utterance_split" to false,
                    ),
                )
            }
        } catch (t: Throwable) {
            val cancelled = stopEpoch.get() != epoch ||
                (t.message?.contains("VIENEU_CANCELLED", ignoreCase = true) == true)
            if (cancelled) {
                finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                return
            }
            Diagnostics.error(
                "system_tts",
                "system_tts.synthesis.failure",
                t,
                mapOf(
                    "synthesis_id" to synthesisId,
                    "voice_key" to voice.key,
                    "profile_id" to voice.profileId,
                    "first_pcm_ms" to firstPcmMs,
                    "cache_hit" to cacheHit,
                    "audio_transport" to "jni_float_direct",
                ),
            )
            totalSpan.end(
                false,
                mapOf(
                    "error" to (t.message ?: t.javaClass.simpleName),
                    "first_pcm_ms" to firstPcmMs,
                    "cache_hit" to cacheHit,
                ),
            )
            callback.error()
        }
    }

    private fun finishCancelled(
        span: Diagnostics.Span,
        synthesisId: String,
        firstPcmMs: Double?,
        cacheHit: Boolean,
    ) {
        span.end(
            false,
            mapOf(
                "cancelled" to true,
                "first_pcm_ms" to firstPcmMs,
                "cache_hit" to cacheHit,
            ),
        )
        Diagnostics.log(
            "system_tts",
            "system_tts.synthesis.cancelled",
            data = mapOf(
                "synthesis_id" to synthesisId,
                "first_pcm_ms" to firstPcmMs,
                "cache_hit" to cacheHit,
            ),
        )
    }

    private fun floatToPcm16(audio: FloatArray, sampleRate: Int): Pcm16Audio {
        val samples = ShortArray(audio.size)
        for (index in audio.indices) {
            val clipped = audio[index].coerceIn(-1.0f, 1.0f)
            samples[index] = (clipped * 32767.0f)
                .roundToInt()
                .coerceIn(-32767, 32767)
                .toShort()
        }
        return Pcm16Audio(sampleRate, 1, samples)
    }

    private fun pcmCacheKey(
        voiceKey: String,
        text: String,
        rate: Float,
        pitch: Float,
        volume: Float,
    ): String = buildString(voiceKey.length + text.length + 48) {
        append(voiceKey)
        append('\u0000')
        append(rate.toBits())
        append(':')
        append(pitch.toBits())
        append(':')
        append(volume.toBits())
        append('\u0000')
        append(text)
    }

    private fun configureNativeDiagnostics() {
        val error = try {
            VieNeuNative.configureDiagnostics(
                Diagnostics.currentSessionDir().absolutePath,
                Diagnostics.sessionId,
            )
        } catch (t: Throwable) {
            Diagnostics.error("system_tts", "system_tts.native_diagnostics.exception", t)
            return
        }
        if (error.isNotEmpty()) {
            Diagnostics.log(
                "system_tts",
                "system_tts.native_diagnostics.failure",
                level = "WARN",
                data = mapOf("error" to error),
            )
        } else {
            Diagnostics.log(
                "system_tts",
                "system_tts.native_diagnostics.ready",
                data = mapOf("session_id" to Diagnostics.sessionId),
            )
        }
    }

    private fun warmConfiguredVoice() {
        val settings = VoiceProfileStore.loadSettings(this)
        val voice = VoiceCatalog.resolve(this, settings)?.takeIf { it.isReady } ?: return
        if (!ModelManager.isReady(this)) return
        val referencePath = when (voice.source) {
            VoiceCatalogSource.PRESET -> ""
            VoiceCatalogSource.SAVED -> voice.referenceFile?.takeIf { it.isFile }?.absolutePath ?: return
        }

        val warmId = "system-tts-warm-${UUID.randomUUID()}"
        val initialized = VieNeuEngine.ensureInitialized(this, warmId)
        val startNs = SystemClock.elapsedRealtimeNanos()
        val direct = VieNeuNative.synthesizeDirect(
            "Xin chào.",
            referencePath,
            voice.nativeVoiceId,
            true,
            false,
            "",
        )
        val warmSynthesisMs = (SystemClock.elapsedRealtimeNanos() - startNs) / 1_000_000.0
        val warmError = if (direct == null || direct.isEmpty()) {
            VieNeuNative.lastError().ifBlank { "Warm-up direct PCM không tạo âm thanh." }
        } else {
            null
        }

        Diagnostics.log(
            "system_tts",
            "system_tts.warm.complete",
            level = if (warmError == null) "INFO" else "WARN",
            data = mapOf(
                "voice_key" to voice.key,
                "voice_label" to voice.label,
                "voice_source" to voice.source.name,
                "engine_initialized_now" to initialized,
                "warm_synthesis_performed" to true,
                "warm_synthesis_success" to (warmError == null),
                "warm_synthesis_ms" to warmSynthesisMs,
                "warm_samples" to (direct?.size ?: 0),
                "warm_error" to warmError,
                "audio_transport" to "jni_float_direct",
            ),
        )
        if (warmError != null) throw IllegalStateException(warmError)
    }

    companion object {
        private const val SAMPLE_RATE = 48_000
        private const val PCM_CACHE_BYTES = 12 * 1024 * 1024
    }
}
