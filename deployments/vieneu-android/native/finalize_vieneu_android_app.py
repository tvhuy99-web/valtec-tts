#!/usr/bin/env python3

import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: finalize_vieneu_android_app.py <android-root>")

root = Path(sys.argv[1]).resolve()
activity = root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
native_kt = root / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
layout = root / "app/src/main/res/layout/activity_main.xml"
jni = root / "native/vieneu_jni.cpp"
gradle = root / "app/build.gradle.kts"
version_file = root / "version.properties"
engine_manager = root / "app/src/main/java/com/vieneu/voiceclone/VieNeuEngine.kt"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path: Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match in {path}, found {count}")
    path.write_text(updated, encoding="utf-8")


def read_version() -> tuple[int, str]:
    if not version_file.is_file():
        raise RuntimeError(f"Missing authoritative version metadata: {version_file}")
    values = {}
    for raw in version_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Invalid version metadata line: {raw!r}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    try:
        version_code = int(values["VERSION_CODE"])
        version_name = values["VERSION_NAME"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError("version.properties must define numeric VERSION_CODE and VERSION_NAME") from exc
    if version_code <= 0 or not version_name:
        raise RuntimeError("Invalid VERSION_CODE or VERSION_NAME in version.properties")
    return version_code, version_name


version_code, version_name = read_version()
if not engine_manager.is_file():
    raise RuntimeError(f"Missing process-scoped engine manager: {engine_manager}")

replace_once(
    gradle,
    "import java.util.Base64\n",
    "import java.util.Base64\nimport java.util.Properties\n",
    "version metadata import",
)
replace_once(
    gradle,
    "val stableDebugKeystoreB64 = rootProject.file(\"../../.github/signing/vieneu-stable-debug.keystore.b64\")\n",
    '''val appVersion = Properties().apply {
    rootProject.file("version.properties").inputStream().use { load(it) }
}
val appVersionCode = requireNotNull(appVersion.getProperty("VERSION_CODE")) {
    "version.properties is missing VERSION_CODE"
}.toInt()
val appVersionName = requireNotNull(appVersion.getProperty("VERSION_NAME")) {
    "version.properties is missing VERSION_NAME"
}.also { require(it.isNotBlank()) { "VERSION_NAME must not be blank" } }

val stableDebugKeystoreB64 = rootProject.file("../../.github/signing/vieneu-stable-debug.keystore.b64")
''',
    "authoritative Gradle version loader",
)
replace_once(
    gradle,
    'versionCode = 22\n        versionName = "0.9.5-native-wav-canonical-f32"',
    "versionCode = appVersionCode\n        versionName = appVersionName",
    "replace final legacy version literals",
)

replace_once(
    activity,
    "    private var engineReady = false\n",
    "",
    "remove Activity-owned engine state",
)
replace_once(
    activity,
    '''                engineReady = false
                span.end(true, mapOf("model" to Diagnostics.modelSnapshot(ModelManager.modelDir(this))))
''',
    '''                VieNeuEngine.invalidate("model_download_complete")
                span.end(true, mapOf("model" to Diagnostics.modelSnapshot(ModelManager.modelDir(this))))
''',
    "invalidate process engine after model download",
)
replace_once(
    activity,
    '        statusText.text = if (engineReady) "Đang tổng hợp giọng..." else "Đang nạp mô hình vào RAM..."\n',
    '        statusText.text = if (VieNeuEngine.isReady()) "Đang tổng hợp giọng..." else "Đang nạp mô hình vào RAM..."\n',
    "read process engine state for UI",
)
activity_text = activity.read_text(encoding="utf-8")
activity_text = activity_text.replace(
    '"engine_already_ready" to engineReady',
    '"engine_already_ready" to VieNeuEngine.isReady()',
)
activity.write_text(activity_text, encoding="utf-8")

regex_once(
    activity,
    r'''                if \(!engineReady\) \{.*?                    runOnUiThread \{ statusText\.text = "Model đã nạp\. Đang clone và tổng hợp giọng\.\.\." \}\n                \}\n\n                val out = File''',
    '''                val initializedNow = VieNeuEngine.ensureInitialized(this, generationId)
                if (initializedNow) {
                    runOnUiThread { statusText.text = "Model đã nạp. Đang clone và tổng hợp giọng..." }
                }

                val out = File''',
    "delegate initialization to process-scoped engine",
)

regex_once(
    activity,
    r'''    override fun onDestroy\(\) \{.*?        super\.onDestroy\(\)\n    \}\n''',
    '''    override fun onDestroy() {
        Diagnostics.log(
            "app",
            "activity.onDestroy.begin",
            data = mapOf(
                "engine_ready" to VieNeuEngine.isReady(),
                "engine_scope" to "process"
            ),
            snapshot = true
        )
        player?.release()
        player = null
        executor.shutdownNow()
        Diagnostics.log(
            "app",
            "activity.onDestroy.end",
            data = mapOf(
                "engine_ready" to VieNeuEngine.isReady(),
                "engine_scope" to "process"
            ),
            snapshot = true
        )
        super.onDestroy()
    }
''',
    "stop releasing engine from Activity.onDestroy",
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
''',
    '''        generationModeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf(
                "Bám sát mẫu (khuyến nghị)",
                "Ổn định (chỉ speaker embedding)",
                "Kiểm chuẩn deterministic"
            )
        )
''',
    "make reference codes the default mode",
)
replace_once(
    activity,
    "        val useRefCodes = generationMode == 1\n",
    "        val useRefCodes = generationMode != 1\n",
    "enable reference codes by default",
)
replace_once(
    activity,
    '''        val generationProfile = when (generationMode) {
            1 -> "reference_codes"
            2 -> "deterministic_parity"
            else -> "speaker_embedding_stable"
        }
''',
    '''        val generationProfile = when (generationMode) {
            1 -> "speaker_embedding_stable"
            2 -> "deterministic_parity"
            else -> "reference_codes"
        }
''',
    "align generation profile labels",
)

activity_text = activity.read_text(encoding="utf-8")
activity_text, count = re.subn(r'\n    private lateinit var styleSpinner: Spinner\n', '\n', activity_text, count=1)
if count != 1:
    raise RuntimeError("style field: expected exactly one match")
activity_text, count = re.subn(r'\n        styleSpinner = findViewById\(R\.id\.styleSpinner\)\n', '\n', activity_text, count=1)
if count != 1:
    raise RuntimeError("style binding: expected exactly one match")
activity_text, count = re.subn(
    r'''\n        styleSpinner\.adapter = ArrayAdapter\(\n            this,\n            android\.R\.layout\.simple_spinner_dropdown_item,\n            arrayOf\("Tự nhiên", "Tin tức", "Đọc truyện"\)\n        \)\n''',
    '\n',
    activity_text,
    count=1,
)
if count != 1:
    raise RuntimeError("style adapter: expected exactly one match")
activity_text, count = re.subn(
    r'''\n        val style = when \(styleSpinner\.selectedItemPosition\) \{\n            1 -> "tin_tuc"\n            2 -> "doc_truyen"\n            else -> "tu_nhien"\n        \}\n''',
    '\n',
    activity_text,
    count=1,
)
if count != 1:
    raise RuntimeError("style selection: expected exactly one match")
style_map_count = activity_text.count('"style" to style,')
if style_map_count < 2:
    raise RuntimeError(f"style diagnostics: expected at least two matches, found {style_map_count}")
activity_text = activity_text.replace('                "style" to style,\n', '')
activity_text = activity_text.replace('                        "style" to style,\n', '')
if activity_text.count("                    style,\n") != 1:
    raise RuntimeError("style synthesis argument: expected exactly one match")
activity_text = activity_text.replace("                    style,\n", "", 1)
activity_text = activity_text.replace(
    "Ổn định dùng speaker embedding có cache. Deterministic chia đoạn, tối đa 96 khung mỗi đoạn và không chạy lại.",
    "Bám sát mẫu dùng speaker embedding và mã tham chiếu. Ổn định chỉ dùng speaker embedding. Deterministic dùng đầy đủ mã tham chiếu.",
)
activity.write_text(activity_text, encoding="utf-8")

# Kotlin cannot smart-cast a mutable Activity property across the recording worker.
# Preserve the existing recording flow while making every direct AudioRecord dereference explicit.
activity_text = activity.read_text(encoding="utf-8")
activity_text, recorder_direct_count = re.subn(
    r'(?<![?\w])recorder\.',
    'checkNotNull(recorder).',
    activity_text,
)
if recorder_direct_count < 1:
    raise RuntimeError("AudioRecord smart-cast fix: no direct recorder dereference found")
activity.write_text(activity_text, encoding="utf-8")

replace_once(
    native_kt,
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String, outputWav: String): String?",
    "external fun synthesize(text: String, referenceWav: String, voiceId: String, useRefCodes: Boolean, deterministic: Boolean, dialect: String, outputWav: String): String?",
    "remove style from Kotlin native API",
)
replace_once(
    jni,
    "jstring text, jstring reference_wav, jstring voice_id, jstring style, jboolean use_ref_codes, jboolean deterministic, jstring dialect, jstring output_wav) {",
    "jstring text, jstring reference_wav, jstring voice_id, jboolean use_ref_codes, jboolean deterministic, jstring dialect, jstring output_wav) {",
    "remove style from JNI API",
)
replace_once(
    jni,
    "        params.style = from_jstring(env, style);\n",
    "",
    "remove style assignment",
)
replace_once(
    layout,
    '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="16dp"
            android:text="Kiểu đọc"
            android:textStyle="bold" />

        <Spinner
            android:id="@+id/styleSpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

''',
    "",
    "remove style UI",
)

required = {
    gradle: (
        'rootProject.file("version.properties")',
        "versionCode = appVersionCode",
        "versionName = appVersionName",
    ),
    activity: (
        "VieNeuEngine.ensureInitialized(this, generationId)",
        "VieNeuEngine.isReady()",
        'VieNeuEngine.invalidate("model_download_complete")',
        '"engine_scope" to "process"',
        '"Bám sát mẫu (khuyến nghị)"',
        "val useRefCodes = generationMode != 1",
        'else -> "reference_codes"',
        "checkNotNull(recorder).",
    ),
    engine_manager: (
        "object VieNeuEngine",
        "fun ensureInitialized",
        "fun invalidate",
        '"engine_scope" to "process"',
    ),
    native_kt: (
        "voiceId: String, useRefCodes: Boolean",
        "outputWav: String): String?",
    ),
    jni: (
        "jstring voice_id, jboolean use_ref_codes",
        "params.use_ref_codes = use_ref_codes == JNI_TRUE",
    ),
}
for path, fragments in required.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing app finalization fragments {missing}")

forbidden = {
    activity: ("engineReady", "VieNeuNative.initialize(", "VieNeuNative.release()", "styleSpinner", '"style" to style'),
    native_kt: ("style: String",),
    jni: ("jstring style", "from_jstring(env, style)"),
    layout: ("@+id/styleSpinner", 'android:text="Kiểu đọc"'),
}
for path, fragments in forbidden.items():
    text = path.read_text(encoding="utf-8")
    found = [fragment for fragment in fragments if fragment in text]
    if found:
        raise RuntimeError(f"{path}: stale app fragments remain {found}")

gradle_final = gradle.read_text(encoding="utf-8")
if re.search(r"versionCode\s*=\s*\d+", gradle_final) or re.search(r'versionName\s*=\s*"', gradle_final):
    raise RuntimeError("Final Gradle file contains a second literal app-version source")

print(
    f"Finalized Android app {version_name} ({version_code}): authoritative version, process engine, "
    "reference-code default and fixed natural style"
)
