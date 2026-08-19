#!/usr/bin/env python3

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_vieneu_system_tts_process_state.py <android-root>')

android_root = pathlib.Path(sys.argv[1])
path = android_root / 'app/src/main/java/com/vieneu/voiceclone/VieNeuTtsService.kt'
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)


replace_once(
    'import java.util.concurrent.atomic.AtomicInteger\n',
    'import java.util.concurrent.atomic.AtomicBoolean\nimport java.util.concurrent.atomic.AtomicInteger\n',
    'AtomicBoolean import',
)

replace_once(
    '''class VieNeuTtsService : TextToSpeechService() {
    private val stopEpoch = AtomicInteger(0)
    private val warmExecutor = Executors.newSingleThreadExecutor()
    private val pcmCache = object : LruCache<String, Pcm16Audio>(PCM_CACHE_BYTES) {
        override fun sizeOf(key: String, value: Pcm16Audio): Int =
            (value.samples.size.toLong() * 2L).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
    }
''',
    '''class VieNeuTtsService : TextToSpeechService() {
    private val warmExecutor = Executors.newSingleThreadExecutor()
''',
    'move TTS state from service instance to process scope',
)

replace_once(
    '''        warmExecutor.execute {
            runCatching { warmConfiguredVoice() }
                .onFailure {
                    Diagnostics.error("system_tts", "system_tts.warm.failure", it)
                }
        }
''',
    '''        if (ModelManager.isReady(this) && warmStarted.compareAndSet(false, true)) {
            warmAbortRequested.set(false)
            warmInProgress.set(true)
            warmExecutor.execute {
                try {
                    warmConfiguredVoice()
                } catch (t: Throwable) {
                    val cancelled =
                        warmAbortRequested.get() ||
                        (t.message?.contains("VIENEU_CANCELLED", ignoreCase = true) == true)
                    if (cancelled) {
                        Diagnostics.log(
                            "system_tts",
                            "system_tts.warm.cancelled",
                            data = mapOf("reason" to "user_synthesis_or_service_stop"),
                        )
                    } else {
                        warmStarted.set(false)
                        Diagnostics.error("system_tts", "system_tts.warm.failure", t)
                    }
                } finally {
                    warmInProgress.set(false)
                }
            }
        } else {
            Diagnostics.log(
                "system_tts",
                "system_tts.warm.skipped",
                data = mapOf(
                    "already_started_in_process" to warmStarted.get(),
                    "model_ready" to ModelManager.isReady(this),
                ),
            )
        }
''',
    'single process-wide warm-up',
)

replace_once(
    '''    override fun onDestroy() {
        stopEpoch.incrementAndGet()
        runCatching { VieNeuNative.cancel() }
        pcmCache.evictAll()
        warmExecutor.shutdownNow()
        super.onDestroy()
    }
''',
    '''    override fun onDestroy() {
        // Do not clear the process PCM cache and do not advance the process stop epoch here.
        // Android can replace a TextToSpeechService instance while the app process stays alive;
        // destroying an old instance must not cancel a newer instance's synthesis.
        warmAbortRequested.set(true)
        warmExecutor.shutdownNow()
        super.onDestroy()
    }
''',
    'preserve process cache across service recreation',
)

replace_once(
    '''        val text = request.charSequenceText?.toString()?.trim().orEmpty()
        if (text.isBlank()) {
''',
    '''        val text = request.charSequenceText?.toString()?.trim().orEmpty()
        if (text.isBlank()) {
''',
    'system TTS text anchor',
)

replace_once(
    '''            callback.done()
            return
        }

        val settings = VoiceProfileStore.loadSettings(this)
''',
    '''            callback.done()
            return
        }

        // A real TalkBack/read-screen request always has priority over synthetic warm-up.
        // The native engine is process-wide, so allowing warm synthesis to own it can add
        // seconds of mutex wait to the first real focus. Cancellation is frame-granular.
        if (warmInProgress.get()) {
            warmAbortRequested.set(true)
            Diagnostics.log(
                "system_tts",
                "system_tts.warm.preempt_requested",
                data = mapOf("text_chars" to text.length),
            )
            runCatching { VieNeuNative.cancel() }
                .onFailure {
                    Diagnostics.error("system_tts", "system_tts.warm.preempt_failure", it)
                }
        }

        val settings = VoiceProfileStore.loadSettings(this)
''',
    'preempt warm-up for real synthesis',
)

replace_once(
    '''        span.end(
            false,
            mapOf(
                "cancelled" to true,
''',
    '''        span.end(
            true,
            mapOf(
                "cancelled" to true,
                "outcome" to "cancelled",
''',
    'do not count normal TalkBack cancellation as synthesis failure',
)

replace_once(
    '''    private fun warmConfiguredVoice() {
        val settings = VoiceProfileStore.loadSettings(this)
''',
    '''    private fun warmConfiguredVoice() {
        if (warmAbortRequested.get()) return
        val settings = VoiceProfileStore.loadSettings(this)
''',
    'warm abort before voice resolution',
)

replace_once(
    '''        val warmId = "system-tts-warm-${UUID.randomUUID()}"
        val initialized = VieNeuEngine.ensureInitialized(this, warmId)
        val startNs = SystemClock.elapsedRealtimeNanos()
''',
    '''        val warmId = "system-tts-warm-${UUID.randomUUID()}"
        val initialized = VieNeuEngine.ensureInitialized(this, warmId)
        if (warmAbortRequested.get()) {
            Diagnostics.log(
                "system_tts",
                "system_tts.warm.cancelled",
                data = mapOf("reason" to "preempted_before_native_synthesis"),
            )
            return
        }
        val startNs = SystemClock.elapsedRealtimeNanos()
''',
    'warm abort before native synthesis',
)

replace_once(
    '''        val warmError = if (direct == null || direct.isEmpty()) {
            VieNeuNative.lastError().ifBlank { "Warm-up direct PCM không tạo âm thanh." }
        } else {
            null
        }

        Diagnostics.log(
''',
    '''        val warmError = if (direct == null || direct.isEmpty()) {
            VieNeuNative.lastError().ifBlank { "Warm-up direct PCM không tạo âm thanh." }
        } else {
            null
        }
        val warmCancelled =
            warmAbortRequested.get() ||
            (warmError?.contains("VIENEU_CANCELLED", ignoreCase = true) == true)

        Diagnostics.log(
''',
    'classify warm cancellation',
)

replace_once(
    '''            level = if (warmError == null) "INFO" else "WARN",
            data = mapOf(
''',
    '''            level = if (warmError == null || warmCancelled) "INFO" else "WARN",
            data = mapOf(
''',
    'warm cancellation log level',
)

replace_once(
    '''                "warm_synthesis_success" to (warmError == null),
                "warm_synthesis_ms" to warmSynthesisMs,
''',
    '''                "warm_synthesis_success" to (warmError == null),
                "warm_cancelled" to warmCancelled,
                "warm_synthesis_ms" to warmSynthesisMs,
''',
    'warm cancellation diagnostics',
)

replace_once(
    '''        if (warmError != null) throw IllegalStateException(warmError)
    }

    companion object {
        private const val SAMPLE_RATE = 48_000
        private const val PCM_CACHE_BYTES = 12 * 1024 * 1024
    }
''',
    '''        if (warmError != null && !warmCancelled) throw IllegalStateException(warmError)
    }

    companion object {
        private const val SAMPLE_RATE = 48_000
        private const val PCM_CACHE_BYTES = 12 * 1024 * 1024

        // TextToSpeechService may be recreated without killing the app process. Native VieNeu,
        // cancellation and the exact PCM cache are process resources, so their coordination must
        // also be process-wide rather than tied to one service object.
        private val stopEpoch = AtomicInteger(0)
        private val warmStarted = AtomicBoolean(false)
        private val warmInProgress = AtomicBoolean(false)
        private val warmAbortRequested = AtomicBoolean(false)
        private val pcmCache = object : LruCache<String, Pcm16Audio>(PCM_CACHE_BYTES) {
            override fun sizeOf(key: String, value: Pcm16Audio): Int =
                (value.samples.size.toLong() * 2L)
                    .coerceAtMost(Int.MAX_VALUE.toLong())
                    .toInt()
        }
    }
''',
    'process-wide TTS companion state',
)

path.write_text(text, encoding='utf-8')

final_text = path.read_text(encoding='utf-8')
required = (
    'private val stopEpoch = AtomicInteger(0)',
    'private val warmStarted = AtomicBoolean(false)',
    'private val warmInProgress = AtomicBoolean(false)',
    'private val pcmCache = object : LruCache<String, Pcm16Audio>(PCM_CACHE_BYTES)',
    'system_tts.warm.preempt_requested',
    '"outcome" to "cancelled"',
    'warm_cancelled',
)
missing = [fragment for fragment in required if fragment not in final_text]
if missing:
    raise RuntimeError(f'System TTS process-state patch missing fragments: {missing}')

print('Applied process-wide System TTS cancellation, warm arbitration and PCM cache state')
