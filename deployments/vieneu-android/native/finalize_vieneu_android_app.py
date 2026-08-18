#!/usr/bin/env python3
"""Finalize Android app lifecycle and authoritative version metadata."""

import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: finalize_vieneu_android_app.py <android-root>")

root = Path(sys.argv[1]).resolve()
activity = root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
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
    ),
    engine_manager: (
        "object VieNeuEngine",
        "fun ensureInitialized",
        "fun invalidate",
        '"engine_scope" to "process"',
    ),
}
for path, fragments in required.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing app finalization fragments {missing}")

activity_final = activity.read_text(encoding="utf-8")
forbidden_activity = ("engineReady", "VieNeuNative.initialize(", "VieNeuNative.release()")
found_activity = [fragment for fragment in forbidden_activity if fragment in activity_final]
if found_activity:
    raise RuntimeError(f"{activity}: Activity still owns native engine lifecycle {found_activity}")

gradle_final = gradle.read_text(encoding="utf-8")
if re.search(r"versionCode\s*=\s*\d+", gradle_final) or re.search(r'versionName\s*=\s*"', gradle_final):
    raise RuntimeError("Final Gradle file contains a second literal app-version source")

print(
    f"Finalized Android app {version_name} ({version_code}): version.properties is authoritative "
    "and the native engine is process-scoped outside MainActivity"
)
