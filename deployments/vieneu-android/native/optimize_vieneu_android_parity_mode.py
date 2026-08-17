#!/usr/bin/env python3
"""Expose stable/reference/deterministic modes after Android JNI generation patches.

The default mode uses only the 192-dimensional speaker embedding. This matches
the official VieNeu API's consistency-oriented path and avoids feeding a long
reference-code prompt into short target sentences. A fidelity mode remains
available, and a deterministic greedy mode is included for tensor/code parity
investigation.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_parity_mode.py <android-root>")

root = Path(sys.argv[1]).resolve()
activity = root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
native_kt = root / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
layout = root / "app/src/main/res/layout/activity_main.xml"
jni = root / "native/vieneu_jni.cpp"
gradle = root / "app/build.gradle.kts"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Kotlin/JNI API: carry consistency mode and deterministic parity mode explicitly.
replace_once(
    native_kt,
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String): FloatArray?",
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean): FloatArray?",
    "extend Kotlin synthesis API",
)
replace_once(
    jni,
    "jstring text, jstring reference_wav, jstring voice_id, jstring style) {",
    "jstring text, jstring reference_wav, jstring voice_id, jstring style, jboolean use_ref_codes, jboolean deterministic) {",
    "extend JNI synthesis API",
)
replace_once(
    jni,
    "        params.use_ref_codes = true;\n",
    "        const bool deterministic_mode = deterministic == JNI_TRUE;\n        params.use_ref_codes = use_ref_codes == JNI_TRUE;\n",
    "use requested reference-code policy",
)
replace_once(
    jni,
    "        params.repetition_penalty = 1.2f;\n        // Keep the native upstream safety budget.",
    "        params.repetition_penalty = 1.2f;\n        if (deterministic_mode) {\n            // Greedy generation is the only useful mode for parity comparison:\n            // the same prompt must produce exactly the same 16 codes per frame.\n            params.temperature = 0.0f;\n            params.top_k = 1;\n            params.top_p = 1.0f;\n            params.repetition_penalty = 1.0f;\n        }\n        // Keep the native upstream safety budget.",
    "configure deterministic sampling",
)
replace_once(
    jni,
    '''                   << ",\\\"denoise_ref\\\":true"
                   << ",\\\"use_ref_codes\\\":true"
                   << ",\\\"apply_watermark\\\":true}";''',
    '''                   << ",\\\"denoise_ref\\\":true"
                   << ",\\\"use_ref_codes\\\":" << (params.use_ref_codes ? "true" : "false")
                   << ",\\\"deterministic_mode\\\":" << (deterministic_mode ? "true" : "false")
                   << ",\\\"acoustic_backend\\\":\\\"upstream_cpu_f32\\\""
                   << ",\\\"semantic_backend\\\":\\\"opencl\\\""
                   << ",\\\"apply_watermark\\\":true}";''',
    "log parity mode",
)
replace_once(
    jni,
    "                if (candidate_audio_ms + 0.5 < minimum_audio_ms) {",
    "                if (!deterministic_mode && candidate_audio_ms + 0.5 < minimum_audio_ms) {",
    "do not hide deterministic parity output behind duration heuristic",
)

# UI: add an explicit quality/parity selector.
replace_once(
    activity,
    "    private lateinit var voiceInfo: TextView\n    private lateinit var styleSpinner: Spinner",
    "    private lateinit var voiceInfo: TextView\n    private lateinit var generationModeSpinner: Spinner\n    private lateinit var styleSpinner: Spinner",
    "declare generation mode spinner",
)
replace_once(
    activity,
    "        voiceInfo = findViewById(R.id.voiceInfo)\n        styleSpinner = findViewById(R.id.styleSpinner)",
    "        voiceInfo = findViewById(R.id.voiceInfo)\n        generationModeSpinner = findViewById(R.id.generationModeSpinner)\n        styleSpinner = findViewById(R.id.styleSpinner)",
    "bind generation mode spinner",
)
replace_once(
    activity,
    '''        styleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Tin tức", "Đọc truyện")
        )
        voiceSpinner.onItemSelectedListener''',
    '''        styleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Tin tức", "Đọc truyện")
        )
        generationModeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf(
                "Ổn định (khuyến nghị)",
                "Bám sát mẫu (mã tham chiếu)",
                "Kiểm chuẩn deterministic"
            )
        )
        voiceSpinner.onItemSelectedListener''',
    "configure generation modes",
)
replace_once(
    activity,
    "    private fun selectedVoiceId(): String = voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { \"\" }\n\n    private fun loadVoices",
    '''    private fun selectedVoiceId(): String = voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { "" }

    private fun normalizeTextForTts(raw: String): String {
        val text = raw.trim()
        if (text.isBlank() || text.last() in ".!?…") return text
        val lower = text.lowercase()
        val questionEndings = listOf(" không", " chưa", " à", " ư", " hả", " sao", " thế nào")
        return text + if (questionEndings.any { lower.endsWith(it) }) "?" else "."
    }

    private fun loadVoices''',
    "add punctuation normalization",
)
replace_once(
    activity,
    '''        val text = textInput.text.toString().trim()
        val ref = referenceFile
        val voiceId = selectedVoiceId()
        val clone = voiceId.isBlank()''',
    '''        val rawText = textInput.text.toString().trim()
        val text = normalizeTextForTts(rawText)
        val ref = referenceFile
        val voiceId = selectedVoiceId()
        val clone = voiceId.isBlank()''',
    "normalize target text",
)
replace_once(
    activity,
    '''        val style = when (styleSpinner.selectedItemPosition) {
            1 -> "tin_tuc"
            2 -> "doc_truyen"
            else -> "tu_nhien"
        }
        val referencePath''',
    '''        val style = when (styleSpinner.selectedItemPosition) {
            1 -> "tin_tuc"
            2 -> "doc_truyen"
            else -> "tu_nhien"
        }
        val generationMode = generationModeSpinner.selectedItemPosition
        val useRefCodes = generationMode == 1
        val deterministic = generationMode == 2
        val generationProfile = when (generationMode) {
            1 -> "reference_codes"
            2 -> "deterministic_parity"
            else -> "speaker_embedding_stable"
        }
        val referencePath''',
    "resolve generation profile",
)
replace_once(
    activity,
    '                "voice_mode" to voiceMode,\n                "engine_already_ready" to engineReady,',
    '                "voice_mode" to voiceMode,\n                "generation_profile" to generationProfile,\n                "use_ref_codes" to useRefCodes,\n                "deterministic" to deterministic,\n                "acoustic_backend" to "upstream_cpu_f32",\n                "engine_already_ready" to engineReady,',
    "log generation profile",
)
replace_once(
    activity,
    'mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "text_chars" to text.length)',
    'mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "generation_profile" to generationProfile, "use_ref_codes" to useRefCodes, "deterministic" to deterministic, "text_chars" to text.length)',
    "log native synthesis profile",
)
replace_once(
    activity,
    "VieNeuNative.synthesize(text, referencePath, voiceId, style)",
    "VieNeuNative.synthesize(text, referencePath, voiceId, style, useRefCodes, deterministic)",
    "pass parity mode to JNI",
)

replace_once(
    layout,
    '''        <Spinner
            android:id="@+id/styleSpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

        <Button
            android:id="@+id/generateButton"''',
    '''        <Spinner
            android:id="@+id/styleSpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="16dp"
            android:text="Chế độ chất lượng"
            android:textStyle="bold" />

        <Spinner
            android:id="@+id/generationModeSpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="5dp"
            android:text="Ổn định chỉ dùng dấu giọng speaker embedding. Bám sát mẫu thêm mã âm thanh tham chiếu. Deterministic dùng greedy để đối chiếu từng codebook."
            android:textSize="12sp" />

        <Button
            android:id="@+id/generateButton"''',
    "add generation mode controls",
)
replace_once(
    layout,
    'android:text="Dùng giọng có sẵn hoặc clone giọng tiếng Việt offline bằng VieNeu-TTS v3 Turbo"',
    'android:text="Bản parity: semantic OpenCL, acoustic CPU F32 chuẩn; dùng giọng có sẵn hoặc clone offline"',
    "describe hybrid parity runtime",
)
replace_once(
    gradle,
    'versionCode = 16\n        versionName = "0.8.0-preset-voices-early-eos"',
    'versionCode = 17\n        versionName = "0.9.0-parity-cpu-acoustic"',
    "bump Android parity version",
)

checks = {
    activity: ("generationModeSpinner", "speaker_embedding_stable", "normalizeTextForTts", "useRefCodes, deterministic"),
    native_kt: ("useRefCodes: Boolean", "deterministic: Boolean"),
    layout: ("@+id/generationModeSpinner", "acoustic CPU F32"),
    jni: ("deterministic_mode", "upstream_cpu_f32", "params.use_ref_codes = use_ref_codes == JNI_TRUE"),
    gradle: ("0.9.0-parity-cpu-acoustic", "versionCode = 17"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing parity fragments {missing}")

print("Enabled speaker-embedding stability mode, reference-code fidelity mode and deterministic parity mode")
