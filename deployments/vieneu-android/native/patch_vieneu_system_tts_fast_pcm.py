#!/usr/bin/env python3

import pathlib
import re
import runpy
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_vieneu_system_tts_fast_pcm.py <android-root>")

root = pathlib.Path(sys.argv[1]).resolve()
native_kt = root / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
jni = root / "native/vieneu_jni.cpp"

for path in (native_kt, jni):
    if not path.is_file():
        raise RuntimeError(f"Missing materialized source: {path}")


def apply_process_state_patch() -> None:
    patch = pathlib.Path(__file__).with_name("patch_vieneu_system_tts_process_state.py")
    if not patch.is_file():
        raise RuntimeError(f"Missing System TTS process-state patch: {patch}")
    saved_argv = sys.argv[:]
    try:
        sys.argv = [str(patch), str(root)]
        runpy.run_path(str(patch), run_name="__main__")
    finally:
        sys.argv = saved_argv


kt_text = native_kt.read_text(encoding="utf-8")
jni_text = jni.read_text(encoding="utf-8")

kt_marker = "external fun synthesizeDirect("
jni_marker = "Java_com_vieneu_voiceclone_VieNeuNative_synthesizeDirect("
if kt_marker in kt_text or jni_marker in jni_text:
    if kt_marker not in kt_text or jni_marker not in jni_text:
        raise RuntimeError("Direct PCM ABI is only partially materialized")
    print("System TTS direct PCM JNI path already materialized")
    apply_process_state_patch()
    raise SystemExit(0)

kt_pattern = re.compile(
    r'(?m)^(\s*)external fun synthesize\('
    r'text: String, referenceWav: String, voiceId: String, '
    r'useRefCodes: Boolean, deterministic: Boolean, dialect: String, '
    r'outputWav: String\): String\?\s*$'
)
kt_match = kt_pattern.search(kt_text)
if not kt_match:
    raise RuntimeError("Final VieNeuNative.synthesize Kotlin ABI was not found")
indent = kt_match.group(1)
kt_direct = (
    kt_match.group(0)
    + "\n"
    + indent
    + "external fun synthesizeDirect(text: String, referenceWav: String, voiceId: String, "
      "useRefCodes: Boolean, deterministic: Boolean, dialect: String): FloatArray?"
)
kt_text = kt_text[:kt_match.start()] + kt_direct + kt_text[kt_match.end():]

start_token = (
    'extern "C" JNIEXPORT jstring JNICALL\n'
    'Java_com_vieneu_voiceclone_VieNeuNative_synthesize('
)
next_token = (
    'extern "C" JNIEXPORT jint JNICALL\n'
    'Java_com_vieneu_voiceclone_VieNeuNative_sampleRate'
)
start = jni_text.find(start_token)
end = jni_text.find(next_token, start + 1)
if start < 0 or end < 0:
    raise RuntimeError("Final JNI synthesize/sampleRate function anchors were not found")

synth_function = jni_text[start:end]
output_anchor = '        const std::string output_path = from_jstring(env, output_wav);\n'
output_pos = synth_function.find(output_anchor)
if output_pos < 0:
    raise RuntimeError("Final native-WAV success tail anchor was not found")

prefix = synth_function[:output_pos]
prefix = prefix.replace(
    'extern "C" JNIEXPORT jstring JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_synthesize(',
    'extern "C" JNIEXPORT jfloatArray JNICALL\nJava_com_vieneu_voiceclone_VieNeuNative_synthesizeDirect(',
    1,
)
old_signature_tail = (
    'jstring text, jstring reference_wav, jstring voice_id, '
    'jboolean use_ref_codes, jboolean deterministic, jstring dialect, jstring output_wav) {'
)
new_signature_tail = (
    'jstring text, jstring reference_wav, jstring voice_id, '
    'jboolean use_ref_codes, jboolean deterministic, jstring dialect) {'
)
if old_signature_tail not in prefix:
    raise RuntimeError("Final JNI synthesize argument list was not found")
prefix = prefix.replace(old_signature_tail, new_signature_tail, 1)

direct_tail = r'''        if (audio.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) {
            set_error("Generated direct PCM is too large for a Java float array.");
            return nullptr;
        }
        jfloatArray result = env->NewFloatArray(static_cast<jsize>(audio.size()));
        if (!result) {
            set_error("Unable to allocate direct System TTS PCM buffer.");
            return nullptr;
        }
        env->SetFloatArrayRegion(result, 0, static_cast<jsize>(audio.size()), audio.data());
        set_error("");

        const auto wall_end = std::chrono::steady_clock::now();
        const double wall_ms = std::chrono::duration<double, std::milli>(wall_end - wall_start).count();
        const int sample_rate = g_engine->sample_rate();
        const double audio_ms = sample_rate > 0
            ? static_cast<double>(audio.size()) * 1000.0 / static_cast<double>(sample_rate)
            : 0.0;
        std::ostringstream data;
        data << "{\"success\":true"
             << ",\"wall_ms\":" << wall_ms
             << ",\"process_cpu_ms\":" << (process_cpu_ms() - cpu_start)
             << ",\"rss_delta_bytes\":" << (rss_bytes() - rss_start)
             << ",\"samples\":" << audio.size()
             << ",\"sample_rate_hz\":" << sample_rate
             << ",\"audio_duration_ms\":" << audio_ms
             << ",\"rtf\":" << (audio_ms > 0.0 ? wall_ms / audio_ms : -1.0)
             << ",\"java_audio_buffer_bytes\":" << (audio.size() * sizeof(float))
             << ",\"audio_transport\":\"jni_float_direct\""
             << "}";
        native_event("synthesis.end", data.str());
        return result;
    } catch (const std::exception& e) {
        set_error(std::string("Native direct synthesis exception: ") + e.what());
        return nullptr;
    } catch (...) {
        set_error("Unknown native direct synthesis exception.");
        return nullptr;
    }
}

'''

direct_function = prefix + direct_tail
jni_text = jni_text[:end] + direct_function + jni_text[end:]

native_kt.write_text(kt_text, encoding="utf-8")
jni.write_text(jni_text, encoding="utf-8")

final_kt = native_kt.read_text(encoding="utf-8")
final_jni = jni.read_text(encoding="utf-8")
required = (
    (final_kt, "synthesizeDirect(text: String"),
    (final_kt, "dialect: String): FloatArray?"),
    (final_jni, "Java_com_vieneu_voiceclone_VieNeuNative_synthesizeDirect"),
    (final_jni, '"audio_transport\\\":\\\"jni_float_direct\\\"'),
    (final_jni, "SetFloatArrayRegion"),
)
missing = [fragment for text, fragment in required if fragment not in text]
if missing:
    raise RuntimeError(f"Direct System TTS PCM fragments missing after patch: {missing}")

print("Materialized direct in-memory F32 PCM JNI path for Android System TTS")
apply_process_state_patch()
