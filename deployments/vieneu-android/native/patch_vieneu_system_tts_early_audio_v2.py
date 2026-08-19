#!/usr/bin/env python3

import pathlib
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: patch_vieneu_system_tts_early_audio_v2.py <vieneu-source-dir> <android-root>")

source = pathlib.Path(sys.argv[1]).resolve()
android = pathlib.Path(sys.argv[2]).resolve()
engine_h = source / "src/vieneu/v3_native/vieneu_v3_native.h"
engine_cpp = source / "src/vieneu/v3_native/vieneu_v3_native.cpp"
native_kt = android / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
jni = android / "native/vieneu_jni.cpp"
store = android / "app/src/main/java/com/vieneu/voiceclone/VoiceProfileStore.kt"
settings_activity = android / "app/src/main/java/com/vieneu/voiceclone/SystemVoiceSettingsActivity.kt"
settings_layout = android / "app/src/main/res/layout/activity_system_voice_settings.xml"
service = android / "app/src/main/java/com/vieneu/voiceclone/VieNeuTtsService.kt"

for path in (engine_h, engine_cpp, native_kt, jni, store, settings_activity, settings_layout, service):
    if not path.is_file():
        raise RuntimeError(f"Missing early-audio patch target: {path}")


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Native engine: same utterance, same F32 acoustic generation. We only expose
# already-decoded stable prefixes while later acoustic frames are still being made.
replace_once(
    engine_h,
    '#include <memory>\n',
    '#include <functional>\n#include <memory>\n',
    'native stream functional include',
)
replace_once(
    engine_h,
    '''    VieneuProgressFn progress;
    float progress_base = 0.0f;
''',
    '''    VieneuProgressFn progress;
    std::function<bool(const std::vector<float>&, bool)> audio_chunk;
    int stream_first_frames = 0;
    int stream_interval_frames = 0;
    int stream_guard_frames = 0;
    float progress_base = 0.0f;
''',
    'native stream params',
)
replace_once(
    engine_cpp,
    '''        int actual_steps = 0;
        bool saw_eos = false;
        if (benchmark_enabled) acoustic_->reset_benchmark_stats();
''',
    '''        int actual_steps = 0;
        bool saw_eos = false;
        size_t streamed_samples = 0;
        int next_stream_frame = (params.audio_chunk && params.stream_first_frames > 0)
            ? params.stream_first_frames
            : 0;
        if (benchmark_enabled) acoustic_->reset_benchmark_stats();
''',
    'initialize early-audio state',
)
replace_once(
    engine_cpp,
    '''            for (int64_t code : codes) frames.push_back(static_cast<int32_t>(code));
            vieneu_report_progress(params.progress, "generate_frames", t + 1, max_frames, scaled_progress(0.18f + (static_cast<float>(t + 1) / static_cast<float>(max_frames)) * 0.68f), "Generating v3 native acoustic frames.");
            if (eos) {
''',
    '''            for (int64_t code : codes) frames.push_back(static_cast<int32_t>(code));
            vieneu_report_progress(params.progress, "generate_frames", t + 1, max_frames, scaled_progress(0.18f + (static_cast<float>(t + 1) / static_cast<float>(max_frames)) * 0.68f), "Generating v3 native acoustic frames.");

            const int generated_frame_count = static_cast<int>(frames.size() / config_.n_vq);
            if (params.audio_chunk && next_stream_frame > 0 && generated_frame_count >= next_stream_frame && !eos) {
                std::vector<float> preview_audio;
                std::string preview_error;
                if (codec_.decode(frames, generated_frame_count, preview_audio, preview_error) && !preview_audio.empty()) {
                    const size_t samples_per_frame = preview_audio.size() / static_cast<size_t>(generated_frame_count);
                    const size_t guard_samples = samples_per_frame * static_cast<size_t>((std::max)(0, params.stream_guard_frames));
                    const size_t stable_samples = preview_audio.size() > guard_samples
                        ? preview_audio.size() - guard_samples
                        : 0;
                    if (stable_samples > streamed_samples) {
                        std::vector<float> chunk(
                            preview_audio.begin() + static_cast<std::ptrdiff_t>(streamed_samples),
                            preview_audio.begin() + static_cast<std::ptrdiff_t>(stable_samples));
                        if (!params.audio_chunk(chunk, false)) {
                            error = "VIENEU_STREAM_ABORTED";
                            return false;
                        }
                        streamed_samples = stable_samples;
                    }
                }
                const int interval = (std::max)(1, params.stream_interval_frames);
                next_stream_frame = generated_frame_count + interval;
            }

            if (eos) {
''',
    'decode and emit stable PCM prefixes',
)
replace_once(
    engine_cpp,
    '''        bool ok = codec_.decode(frames, static_cast<int64_t>(frames.size() / config_.n_vq), out_audio, error);
        if (ok) vieneu_report_progress(params.progress, "decode_audio", 1, 1, scaled_progress(0.96f), "V3 native audio decode complete.");
''',
    '''        bool ok = codec_.decode(frames, static_cast<int64_t>(frames.size() / config_.n_vq), out_audio, error);
        if (ok && params.audio_chunk) {
            const size_t start = (std::min)(streamed_samples, out_audio.size());
            if (start < out_audio.size()) {
                std::vector<float> final_chunk(
                    out_audio.begin() + static_cast<std::ptrdiff_t>(start),
                    out_audio.end());
                if (!params.audio_chunk(final_chunk, true)) {
                    error = "VIENEU_STREAM_ABORTED";
                    return false;
                }
            }
        }
        if (ok) vieneu_report_progress(params.progress, "decode_audio", 1, 1, scaled_progress(0.96f), "V3 native audio decode complete.");
''',
    'emit final PCM remainder',
)

# Request-local JNI sink. There is deliberately no process-global stream sink:
# Android may create two TTS service objects in one process, and a global setter
# would allow one request to clear or replace another request's callback.
replace_once(
    native_kt,
    '''    external fun synthesizeDirect(text: String, referenceWav: String, voiceId: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String): FloatArray?
''',
    '''    external fun synthesizeDirect(text: String, referenceWav: String, voiceId: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String, streamSink: NativePcmStreamSink?): FloatArray?
''',
    'request-local Kotlin stream sink ABI',
)

jni_text = jni.read_text(encoding="utf-8")
start = jni_text.find('Java_com_vieneu_voiceclone_VieNeuNative_synthesizeDirect(')
end = jni_text.find('extern "C" JNIEXPORT jint JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_sampleRate', start)
if start < 0 or end < 0:
    raise RuntimeError('Unable to isolate synthesizeDirect for request-local stream callback')
direct = jni_text[start:end]
old_sig = 'jstring text, jstring reference_wav, jstring voice_id, jboolean use_ref_codes, jboolean deterministic, jstring dialect) {'
new_sig = 'jstring text, jstring reference_wav, jstring voice_id, jboolean use_ref_codes, jboolean deterministic, jstring dialect, jobject stream_sink) {'
if direct.count(old_sig) != 1:
    raise RuntimeError(f'synthesizeDirect request-local signature count={direct.count(old_sig)}')
direct = direct.replace(old_sig, new_sig, 1)
anchor = '        std::ostringstream start_data;\n'
if direct.count(anchor) != 1:
    raise RuntimeError(f'synthesizeDirect start_data anchor count={direct.count(anchor)}')
stream_params = r'''        jmethodID stream_method = nullptr;
        if (stream_sink) {
            jclass stream_class = env->GetObjectClass(stream_sink);
            if (!stream_class) {
                set_error("Unable to resolve NativePcmStreamSink class.");
                return nullptr;
            }
            stream_method = env->GetMethodID(stream_class, "onNativePcmChunk", "([FZ)Z");
            env->DeleteLocalRef(stream_class);
            if (!stream_method) {
                if (env->ExceptionCheck()) env->ExceptionClear();
                set_error("NativePcmStreamSink.onNativePcmChunk([FZ)Z was not found.");
                return nullptr;
            }

            params.stream_first_frames = 2;
            params.stream_interval_frames = 2;
            params.stream_guard_frames = 1;
            params.audio_chunk = [env, stream_sink, stream_method](const std::vector<float>& chunk, bool is_final) -> bool {
                if (chunk.empty()) return true;
                if (chunk.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) return false;
                jfloatArray array = env->NewFloatArray(static_cast<jsize>(chunk.size()));
                if (!array) return false;
                env->SetFloatArrayRegion(array, 0, static_cast<jsize>(chunk.size()), chunk.data());
                const jboolean accepted = env->CallBooleanMethod(
                    stream_sink,
                    stream_method,
                    array,
                    is_final ? JNI_TRUE : JNI_FALSE);
                env->DeleteLocalRef(array);
                if (env->ExceptionCheck()) {
                    env->ExceptionClear();
                    return false;
                }
                return accepted == JNI_TRUE;
            };
        }

'''
direct = direct.replace(anchor, stream_params + anchor, 1)
jni.write_text(jni_text[:start] + direct + jni_text[end:], encoding="utf-8")

# Persist A/B switch. OFF is the baseline and remains the default.
replace_once(
    store,
    '''    val volume: Float,
    val voiceKey: String? = null,
)''',
    '''    val volume: Float,
    val voiceKey: String? = null,
    val earlyPlayback: Boolean = false,
)''',
    'SystemVoiceSettings early playback field',
)
replace_once(
    store,
    '''            volume = prefs.getFloat("volume", 1.0f).coerceIn(0.0f, 1.0f),
            voiceKey = resolved?.key,
''',
    '''            volume = prefs.getFloat("volume", 1.0f).coerceIn(0.0f, 1.0f),
            voiceKey = resolved?.key,
            earlyPlayback = prefs.getBoolean("early_playback", false),
''',
    'load early playback setting',
)
replace_once(
    store,
    '''            .putFloat("volume", settings.volume.coerceIn(0.0f, 1.0f))
            .apply()
''',
    '''            .putFloat("volume", settings.volume.coerceIn(0.0f, 1.0f))
            .putBoolean("early_playback", settings.earlyPlayback)
            .apply()
''',
    'save early playback setting',
)
replace_once(
    store,
    '''                "volume" to settings.volume,
            ),
''',
    '''                "volume" to settings.volume,
                "early_playback" to settings.earlyPlayback,
            ),
''',
    'diagnose early playback setting',
)

# Accessible A/B switch in System Voice Settings.
replace_once(settings_activity, 'import android.widget.Spinner\n', 'import android.widget.Spinner\nimport android.widget.Switch\n', 'Switch import')
replace_once(
    settings_activity,
    '''    private lateinit var volumeValue: TextView
    private lateinit var status: TextView
''',
    '''    private lateinit var volumeValue: TextView
    private lateinit var earlyPlaybackSwitch: Switch
    private lateinit var status: TextView
''',
    'settings switch field',
)
replace_once(
    settings_activity,
    '''        volumeValue = findViewById(R.id.volumeValue)
        status = findViewById(R.id.systemVoiceStatus)
''',
    '''        volumeValue = findViewById(R.id.volumeValue)
        earlyPlaybackSwitch = findViewById(R.id.earlyPlaybackSwitch)
        status = findViewById(R.id.systemVoiceStatus)
''',
    'bind settings switch',
)
replace_once(
    settings_activity,
    '''        volumeSeek.progress = (settings.volume * 100f).toInt().coerceIn(0, volumeSeek.max)
        refreshControlLabels()
''',
    '''        volumeSeek.progress = (settings.volume * 100f).toInt().coerceIn(0, volumeSeek.max)
        earlyPlaybackSwitch.isChecked = settings.earlyPlayback
        refreshControlLabels()
''',
    'load settings switch state',
)
replace_once(
    settings_activity,
    '''            volume = (volumeSeek.progress / 100f).coerceIn(0.0f, 1.0f),
            voiceKey = voice?.key,
''',
    '''            volume = (volumeSeek.progress / 100f).coerceIn(0.0f, 1.0f),
            voiceKey = voice?.key,
            earlyPlayback = earlyPlaybackSwitch.isChecked,
''',
    'save settings switch state',
)
replace_once(
    settings_activity,
    '''        volumeSeek.isEnabled = !value
        voiceSpinner.isEnabled = !value
''',
    '''        volumeSeek.isEnabled = !value
        earlyPlaybackSwitch.isEnabled = !value
        voiceSpinner.isEnabled = !value
''',
    'busy state for early playback switch',
)
replace_once(
    settings_activity,
    '''                "voice_source" to voice.source.name,
            ),
''',
    '''                "voice_source" to voice.source.name,
                "early_playback" to settings.earlyPlayback,
            ),
''',
    'settings save-and-exit diagnostics',
)

layout_text = settings_layout.read_text(encoding="utf-8")
layout_anchor = '''        <TextView
            android:id="@+id/systemVoiceStatus"'''
if layout_text.count(layout_anchor) != 1:
    raise RuntimeError(f'early playback layout anchor count={layout_text.count(layout_anchor)}')
early_layout = '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="18dp"
            android:text="Thử nghiệm độ trễ"
            android:textStyle="bold" />

        <Switch
            android:id="@+id/earlyPlaybackSwitch"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp"
            android:text="Phát sớm khi đang tạo (A/B)"
            android:contentDescription="Phát sớm khi đang tạo. Bật để thử nhận âm thanh trước khi VieNeu tạo xong toàn bộ tiêu điểm. Tắt để dùng chế độ chuẩn." />

        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="4dp"
            android:text="Tắt: đợi tạo xong rồi phát. Bật: vẫn giữ nguyên một tiêu điểm, nhưng thử phát phần PCM ổn định đầu tiên trong lúc các frame còn lại tiếp tục được tạo. Mặc định tắt để dễ so sánh chất lượng và độ trễ."
            android:textSize="12sp" />

'''
settings_layout.write_text(layout_text.replace(layout_anchor, early_layout + layout_anchor, 1), encoding="utf-8")

# Service. Only uncached 1.0x-rate/1.0x-pitch requests use the experiment;
# volume is safe to apply per emitted chunk. Exact cache hits stay unchanged.
service_text = service.read_text(encoding="utf-8")
service_text = service_text.replace('import kotlin.math.roundToInt\n', 'import kotlin.math.abs\nimport kotlin.math.roundToInt\n', 1)
service_text = service_text.replace(
    '''                "engine_already_ready" to engineWasReady,
                "audio_transport" to "jni_float_direct",
''',
    '''                "engine_already_ready" to engineWasReady,
                "early_playback_requested" to settings.earlyPlayback,
                "audio_transport" to "jni_float_direct",
''',
    1,
)
service_text = service_text.replace(
    '''        var firstPcmMs: Double? = null
        var totalPcmBytes = 0L
        var cacheHit = false
''',
    '''        var firstPcmMs: Double? = null
        var totalPcmBytes = 0L
        var cacheHit = false
        var streamRejected = false
        var streamedPcmBytes = 0L
        var earlyPlaybackActive = false
''',
    1,
)

old_native_call = '''                val nativeStartNs = SystemClock.elapsedRealtimeNanos()
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
'''
if service_text.count(old_native_call) != 1:
    raise RuntimeError(f'service native direct call anchor count={service_text.count(old_native_call)}')
new_native_call = '''                earlyPlaybackActive = settings.earlyPlayback &&
                    abs(effectiveRate - 1.0f) < 0.0001f &&
                    abs(effectivePitch - 1.0f) < 0.0001f
                if (settings.earlyPlayback && !earlyPlaybackActive) {
                    Diagnostics.log(
                        "system_tts",
                        "system_tts.early_playback.fallback",
                        data = mapOf(
                            "synthesis_id" to synthesisId,
                            "reason" to "rate_or_pitch_not_1x",
                            "rate" to effectiveRate,
                            "pitch" to effectivePitch,
                        ),
                    )
                }

                val streamSink = if (earlyPlaybackActive) {
                    object : NativePcmStreamSink {
                        override fun onNativePcmChunk(audio: FloatArray, isFinal: Boolean): Boolean {
                            if (stopEpoch.get() != epoch) return false
                            if (audio.isEmpty()) return true
                            val samples = ShortArray(audio.size)
                            for (index in audio.indices) {
                                val scaled = (audio[index] * settings.volume).coerceIn(-1.0f, 1.0f)
                                samples[index] = (scaled * 32767.0f)
                                    .roundToInt()
                                    .coerceIn(-32767, 32767)
                                    .toShort()
                            }
                            val bytes = WavPcmReader.toLittleEndianBytes(samples)
                            val maxChunk = callback.maxBufferSize.coerceAtLeast(1024)
                            var offset = 0
                            while (offset < bytes.size) {
                                if (stopEpoch.get() != epoch) return false
                                val count = minOf(maxChunk, bytes.size - offset)
                                val accepted = callback.audioAvailable(bytes, offset, count)
                                if (accepted != TextToSpeech.SUCCESS) {
                                    streamRejected = true
                                    return false
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
                                            "sample_rate_hz" to SAMPLE_RATE,
                                            "cache_hit" to false,
                                            "early_playback" to true,
                                            "stream_final_chunk" to isFinal,
                                            "audio_transport" to "jni_float_stream",
                                            "utterance_split" to false,
                                        ),
                                    )
                                }
                                offset += count
                                streamedPcmBytes += count
                                totalPcmBytes += count
                            }
                            return true
                        }
                    }
                } else null

                val nativeStartNs = SystemClock.elapsedRealtimeNanos()
                val direct = VieNeuNative.synthesizeDirect(
                    text,
                    referencePath,
                    voice.nativeVoiceId,
                    true,
                    false,
                    "",
                    streamSink,
                )
                val nativeDirectMs =
                    (SystemClock.elapsedRealtimeNanos() - nativeStartNs) / 1_000_000.0
                if (direct == null) {
                    val nativeError = VieNeuNative.lastError()
                    if (streamRejected || stopEpoch.get() != epoch ||
                        nativeError.contains("VIENEU_STREAM_ABORTED", ignoreCase = true) ||
                        nativeError.contains("VIENEU_CANCELLED", ignoreCase = true)
                    ) {
                        finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                        return
                    }
                    throw IllegalStateException(
                        nativeError.ifBlank { "VieNeu direct PCM không tạo được âm thanh." }
                    )
                }
'''
service_text = service_text.replace(old_native_call, new_native_call, 1)

# Warm-up never streams to Android.
warm_call_old = '''        val direct = VieNeuNative.synthesizeDirect(
            "Xin chào.",
            referencePath,
            voice.nativeVoiceId,
            true,
            false,
            "",
        )
'''
warm_call_new = '''        val direct = VieNeuNative.synthesizeDirect(
            "Xin chào.",
            referencePath,
            voice.nativeVoiceId,
            true,
            false,
            "",
            null,
        )
'''
if service_text.count(warm_call_old) != 1:
    raise RuntimeError(f'warm direct call anchor count={service_text.count(warm_call_old)}')
service_text = service_text.replace(warm_call_old, warm_call_new, 1)

old_bytes = '''            val bytes = WavPcmReader.toLittleEndianBytes(processed.samples)
            val maxChunk = callback.maxBufferSize.coerceAtLeast(1024)
            var offset = 0
            while (offset < bytes.size) {
'''
new_bytes = '''            val bytes = if (earlyPlaybackActive && streamedPcmBytes > 0L) {
                ByteArray(0)
            } else {
                WavPcmReader.toLittleEndianBytes(processed.samples)
            }
            val maxChunk = callback.maxBufferSize.coerceAtLeast(1024)
            var offset = 0
            while (offset < bytes.size) {
'''
if service_text.count(old_bytes) != 1:
    raise RuntimeError(f'service output loop anchor count={service_text.count(old_bytes)}')
service_text = service_text.replace(old_bytes, new_bytes, 1)

old_reject = '''                if (result != TextToSpeech.SUCCESS) {
                    // TalkBack frequently calls stop() while a focus is changing. Treat a
                    // callback rejection after that stop as normal cancellation, not ERROR.
                    if (stopEpoch.get() != epoch) {
                        finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                        return
                    }
                    throw IllegalStateException("Android TTS từ chối dữ liệu PCM.")
                }
'''
new_reject = '''                if (result != TextToSpeech.SUCCESS) {
                    Diagnostics.log(
                        "system_tts",
                        "system_tts.callback_rejected_as_cancel",
                        data = mapOf(
                            "synthesis_id" to synthesisId,
                            "stop_epoch_changed" to (stopEpoch.get() != epoch),
                            "cache_hit" to cacheHit,
                        ),
                    )
                    finishCancelled(totalSpan, synthesisId, firstPcmMs, cacheHit)
                    return
                }
'''
if service_text.count(old_reject) != 1:
    raise RuntimeError(f'service callback rejection anchor count={service_text.count(old_reject)}')
service_text = service_text.replace(old_reject, new_reject, 1)
service_text = service_text.replace(
    '''                        "cache_hit" to cacheHit,
                        "audio_transport" to "jni_float_direct",
''',
    '''                        "cache_hit" to cacheHit,
                        "early_playback_requested" to settings.earlyPlayback,
                        "early_playback_active" to earlyPlaybackActive,
                        "streamed_pcm_bytes" to streamedPcmBytes,
                        "audio_transport" to if (earlyPlaybackActive) "jni_float_stream" else "jni_float_direct",
''',
    1,
)
service.write_text(service_text, encoding="utf-8")

contracts = {
    engine_h: ('audio_chunk', 'stream_first_frames', 'stream_guard_frames'),
    engine_cpp: ('VIENEU_STREAM_ABORTED', 'streamed_samples', 'preview_audio'),
    native_kt: ('streamSink: NativePcmStreamSink?): FloatArray?',),
    jni: ('jobject stream_sink', 'onNativePcmChunk', 'params.stream_first_frames = 2'),
    store: ('earlyPlayback: Boolean = false', '"early_playback"'),
    settings_activity: ('earlyPlaybackSwitch', 'settings.earlyPlayback'),
    settings_layout: ('@+id/earlyPlaybackSwitch', 'Phát sớm khi đang tạo (A/B)'),
    service: ('system_tts.early_playback.fallback', 'jni_float_stream', 'callback_rejected_as_cancel', 'streamSink,'),
}
for path, fragments in contracts.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"Early-audio contract missing in {path}: {missing}")

for forbidden in ('g_stream_sink', 'setStreamSink(', 'Java_com_vieneu_voiceclone_VieNeuNative_setStreamSink'):
    if forbidden in native_kt.read_text(encoding="utf-8") or forbidden in jni.read_text(encoding="utf-8"):
        raise RuntimeError(f"Unsafe process-global stream sink survived materialization: {forbidden}")

print("Applied request-local same-utterance early PCM A/B path; baseline remains default OFF")
