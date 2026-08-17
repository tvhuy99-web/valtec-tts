#!/usr/bin/env python3
"""Patch generated Android/JNI sources for VieNeu 0.9.2."""

from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_v092_android.py <android-root>")

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


def regex_once(path: Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match in {path}, found {count}")
    path.write_text(updated, encoding="utf-8")


# JNI and Kotlin API.
replace_once(
    native_kt,
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean): FloatArray?",
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String): FloatArray?",
    "extend Kotlin native API with dialect",
)
replace_once(
    jni,
    "jstring text, jstring reference_wav, jstring voice_id, jstring style, jboolean use_ref_codes, jboolean deterministic) {",
    "jstring text, jstring reference_wav, jstring voice_id, jstring style, jboolean use_ref_codes, jboolean deterministic, jstring dialect) {",
    "extend JNI API with dialect",
)
replace_once(
    jni,
    "        params.style = from_jstring(env, style);\n",
    "        params.style = from_jstring(env, style);\n        params.dialect = from_jstring(env, dialect);\n        if (params.dialect != \"south\") params.dialect = \"north\";\n",
    "read and validate dialect",
)
replace_once(
    jni,
    '''        if (deterministic_mode) {
            params.temperature = 0.0f;
            params.top_k = 1;
            params.top_p = 1.0f;
            params.repetition_penalty = 1.0f;
        }
''',
    '''        if (deterministic_mode) {
            params.temperature = 0.0f;
            params.top_k = 1;
            params.top_p = 1.0f;
            params.repetition_penalty = 1.2f;
            // Long text remains supported: the native engine splits it into
            // independent chunks before applying this per-chunk frame ceiling.
            params.max_chars = 96;
        }
''',
    "bounded deterministic sampling",
)
replace_once(
    jni,
    '''                   << ",\\\"deterministic_mode\\\":" << (deterministic_mode ? "true" : "false")
                   << ",\\\"acoustic_backend\\\":\\\"opencl_f32\\\""
''',
    '''                   << ",\\\"deterministic_mode\\\":" << (deterministic_mode ? "true" : "false")
                   << ",\\\"dialect\\\":" << quote(params.dialect)
                   << ",\\\"deterministic_frame_cap\\\":96"
                   << ",\\\"deterministic_chunk_chars\\\":96"
                   << ",\\\"speaker_embedding_cache\\\":\\\"persistent_v2\\\""
                   << ",\\\"acoustic_backend\\\":\\\"opencl_f32\\\""
''',
    "log 0.9.2 runtime policy",
)
replace_once(
    jni,
    '''        const int frame_caps[] = {300, 450};
        const uint32_t request_seed = stable_request_seed(params);
        for (int attempt = 0; attempt < 2; ++attempt) {
            params.max_new_frames = frame_caps[attempt];
''',
    '''        const int normal_frame_caps[] = {300, 450};
        const int max_attempts = deterministic_mode ? 1 : 2;
        const int deterministic_frame_cap = 96;
        const uint32_t request_seed = stable_request_seed(params);
        for (int attempt = 0; attempt < max_attempts; ++attempt) {
            params.max_new_frames = deterministic_mode
                ? deterministic_frame_cap
                : normal_frame_caps[attempt];
''',
    "one-pass deterministic loop",
)
replace_once(
    jni,
    '''                ",\\\"max_attempts\\\":2,\\\"max_new_frames\\\":" +
                std::to_string(params.max_new_frames) +
''',
    '''                ",\\\"max_attempts\\\":" + std::to_string(max_attempts) +
                ",\\\"max_new_frames\\\":" + std::to_string(params.max_new_frames) +
''',
    "dynamic attempt diagnostics",
)
replace_once(
    jni,
    "            if (!retryable) break;\n",
    "            if (!retryable || attempt + 1 >= max_attempts) break;\n",
    "stop deterministic after one attempt",
)
replace_once(
    jni,
    '''            const std::string final_error = is_no_eos_error(error)
                ? "Model không phát tín hiệu kết thúc sau 2 lần thử; không lưu âm thanh bị cắt."
                : (error.empty() ? "VieNeu synthesis failed." : error);
''',
    '''            const std::string final_error = is_no_eos_error(error)
                ? (deterministic_mode
                    ? "Deterministic không phát EOS trong 96 khung của một đoạn; hãy rút ngắn câu đang báo lỗi."
                    : "Model không phát tín hiệu kết thúc sau 2 lần thử; không lưu âm thanh bị cắt.")
                : (error.empty() ? "VieNeu synthesis failed." : error);
''',
    "deterministic no-EOS error",
)
replace_once(
    jni,
    '''                 << ",\\\"attempts\\\":2"
''',
    '''                 << ",\\\"attempts\\\":" << max_attempts
''',
    "dynamic failure attempts",
)

# Android controls.
replace_once(
    activity,
    '''    private lateinit var generationModeSpinner: Spinner
    private lateinit var styleSpinner: Spinner''',
    '''    private lateinit var generationModeSpinner: Spinner
    private lateinit var dialectSpinner: Spinner
    private lateinit var claritySpinner: Spinner
    private lateinit var styleSpinner: Spinner''',
    "declare pronunciation controls",
)
replace_once(
    activity,
    '''        generationModeSpinner = findViewById(R.id.generationModeSpinner)
        styleSpinner = findViewById(R.id.styleSpinner)''',
    '''        generationModeSpinner = findViewById(R.id.generationModeSpinner)
        dialectSpinner = findViewById(R.id.dialectSpinner)
        claritySpinner = findViewById(R.id.claritySpinner)
        styleSpinner = findViewById(R.id.styleSpinner)''',
    "bind pronunciation controls",
)
replace_once(
    activity,
    '''        generationModeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf(
                "Ổn định (khuyến nghị)",
                "Bám sát mẫu (mã tham chiếu)",
                "Kiểm chuẩn deterministic"
            )
        )
        voiceSpinner.onItemSelectedListener''',
    '''        generationModeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf(
                "Ổn định (khuyến nghị)",
                "Bám sát mẫu (mã tham chiếu)",
                "Kiểm chuẩn deterministic"
            )
        )
        dialectSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Miền Bắc", "Miền Nam")
        )
        claritySpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Rõ chữ")
        )
        voiceSpinner.onItemSelectedListener''',
    "configure pronunciation controls",
)

regex_once(
    activity,
    r'''    private fun normalizeTextForTts\(raw: String\): String \{.*?\n    \}\n\n    private fun loadVoices''',
    r'''    private fun normalizeTextForTts(raw: String, clearSpeech: Boolean): String {
        var text = raw.trim().replace(Regex("\\s+"), " ")
        if (text.isBlank()) return text
        if (clearSpeech) text = applyClearSpeechPunctuation(text)
        if (text.last() in ".!?…") return text
        val lower = text.lowercase()
        val questionEndings = listOf(" không", " chưa", " à", " ư", " hả", " sao", " thế nào")
        return text + if (questionEndings.any { lower.endsWith(it) }) "?" else "."
    }

    private fun applyClearSpeechPunctuation(raw: String): String {
        val introductory = Regex(
            "(?iu)\\b(dạo này|hôm nay|bây giờ|trước tiên|sau đó|tuy nhiên|vì vậy|do đó|nói chung)\\b(?![,.;:!?])"
        )
        val withIntroPauses = raw.replace(introductory, "\$1,")
        val words = withIntroPauses.split(Regex("\\s+")).filter { it.isNotBlank() }
        if (words.size <= 14) return withIntroPauses

        val result = ArrayList<String>(words.size)
        var wordsSincePause = 0
        for (word in words) {
            result += word
            wordsSincePause++
            val hasPause = word.lastOrNull()?.let {
                it == ',' || it == '.' || it == ';' || it == ':' || it == '!' || it == '?' || it == '…'
            } == true
            if (hasPause) {
                wordsSincePause = 0
            } else if (wordsSincePause >= 14) {
                result[result.lastIndex] = word + ","
                wordsSincePause = 0
            }
        }
        return result.joinToString(" ")
    }

    private fun loadVoices''',
    "clear-speech text normalization",
)
replace_once(
    activity,
    '''        refreshModelStatus()
    }
''',
    '''        refreshModelStatus()
        restoreReferenceIfPresent()
    }
''',
    "restore persisted reference on startup",
)
replace_once(
    activity,
    "    private fun importReference(uri: Uri) {\n",
    '''    private fun restoreReferenceIfPresent() {
        val saved = File(filesDir, "references/reference.wav")
        if (!saved.isFile || !looksLikeWav(saved)) return
        referenceFile = saved
        referenceStatus.text = "Giọng mẫu: reference.wav đã lưu (${saved.length() / 1024} KB)"
        Diagnostics.log(
            "reference",
            "reference.restore",
            data = mapOf(
                "wav" to Diagnostics.wavInfo(saved),
                "speaker_cache_present" to File(saved.absolutePath + ".vieneu-speaker-v2.bin").isFile
            )
        )
    }

    private fun importReference(uri: Uri) {
''',
    "restore persisted reference helper",
)
replace_once(
    activity,
    '''                val target = File(dir, "reference.wav")
                val copyStart = SystemClock.elapsedRealtimeNanos()''',
    '''                val target = File(dir, "reference.wav")
                File(target.absolutePath + ".vieneu-speaker-v2.bin").delete()
                val copyStart = SystemClock.elapsedRealtimeNanos()''',
    "invalidate cache when replacing reference",
)
replace_once(
    activity,
    '''        val rawText = textInput.text.toString().trim()
        val text = normalizeTextForTts(rawText)
        val ref = referenceFile''',
    '''        val rawText = textInput.text.toString().trim()
        val clearSpeech = claritySpinner.selectedItemPosition == 1
        val text = normalizeTextForTts(rawText, clearSpeech)
        val ref = referenceFile''',
    "apply clear speech",
)
replace_once(
    activity,
    '''        val generationMode = generationModeSpinner.selectedItemPosition
        val useRefCodes = generationMode == 1''',
    '''        val generationMode = generationModeSpinner.selectedItemPosition
        val dialect = if (dialectSpinner.selectedItemPosition == 1) "south" else "north"
        val useRefCodes = generationMode == 1''',
    "resolve dialect",
)
replace_once(
    activity,
    '''                "generation_profile" to generationProfile,
                "use_ref_codes" to useRefCodes,''',
    '''                "generation_profile" to generationProfile,
                "dialect" to dialect,
                "clear_speech" to clearSpeech,
                "speaker_embedding_cache" to "persistent_v2",
                "use_ref_codes" to useRefCodes,''',
    "log pronunciation settings",
)
replace_once(
    activity,
    'mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "generation_profile" to generationProfile, "use_ref_codes" to useRefCodes, "deterministic" to deterministic, "text_chars" to text.length)',
    'mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "generation_profile" to generationProfile, "dialect" to dialect, "clear_speech" to clearSpeech, "use_ref_codes" to useRefCodes, "deterministic" to deterministic, "text_chars" to text.length)',
    "log native pronunciation settings",
)
replace_once(
    activity,
    "VieNeuNative.synthesize(text, referencePath, voiceId, style, useRefCodes, deterministic)",
    "VieNeuNative.synthesize(text, referencePath, voiceId, style, useRefCodes, deterministic, dialect)",
    "pass dialect to native",
)

replace_once(
    layout,
    '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="5dp"
            android:text="Ổn định chỉ dùng dấu giọng speaker embedding. Bám sát mẫu thêm mã âm thanh tham chiếu. Deterministic dùng greedy để tạo kết quả lặp lại."
            android:textSize="12sp" />

        <Button
            android:id="@+id/generateButton"''',
    '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="5dp"
            android:text="Ổn định dùng speaker embedding có cache. Deterministic chia đoạn, tối đa 96 khung mỗi đoạn và không chạy lại."
            android:textSize="12sp" />

        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="16dp"
            android:text="Phát âm"
            android:textStyle="bold" />

        <Spinner
            android:id="@+id/dialectSpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

        <Spinner
            android:id="@+id/claritySpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="5dp"
            android:text="Miền Nam đổi âm đầu d/gi sang âm y. Rõ chữ thêm các nhịp nghỉ nhẹ cho câu dài."
            android:textSize="12sp" />

        <Button
            android:id="@+id/generateButton"''',
    "add pronunciation UI",
)
replace_once(
    layout,
    'android:text="Bản nhanh: semantic và acoustic OpenCL F32; dùng giọng có sẵn hoặc clone offline"',
    'android:text="OpenCL F32 nhanh, cache giọng mẫu, phát âm Bắc/Nam và chế độ Rõ chữ"',
    "update app description",
)
replace_once(
    gradle,
    'versionCode = 18\n        versionName = "0.9.1-fast-opencl-eos"',
    'versionCode = 19\n        versionName = "0.9.2-cache-dialect-clear"',
    "bump Android 0.9.2 version",
)

checks = {
    jni: ("deterministic_frame_cap = 96", "max_attempts = deterministic_mode ? 1 : 2", "persistent_v2", "params.dialect"),
    native_kt: ("dialect: String",),
    activity: ("dialectSpinner", "claritySpinner", "applyClearSpeechPunctuation", "restoreReferenceIfPresent", "persistent_v2"),
    layout: ("@+id/dialectSpinner", "@+id/claritySpinner", "Rõ chữ"),
    gradle: ("versionCode = 19", "0.9.2-cache-dialect-clear"),
}
for path, fragments in checks.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing 0.9.2 Android fragments {missing}")

jni_text = jni.read_text(encoding="utf-8")
forbidden = ("params.repetition_penalty = 1.0f", "const int frame_caps[] = {300, 450};")
found = [fragment for fragment in forbidden if fragment in jni_text]
if found:
    raise RuntimeError(f"{jni}: stale unsafe fragments remain {found}")

print(
    "Applied Android 0.9.2 UI/JNI: one-pass deterministic chunks, persistent "
    "reference restore, Bắc/Nam pronunciation and clear speech"
)
