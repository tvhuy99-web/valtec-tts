#!/usr/bin/env python3

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_android_sea_g2p_asset.py <android-app-dir>')

app = pathlib.Path(sys.argv[1])
path = app / 'src/main/java/com/vieneu/voiceclone/ModelManager.kt'
text = path.read_text(encoding='utf-8')

old = '''    fun modelDir(context: Context): File {
        val base = context.getExternalFilesDir(null) ?: context.filesDir
        return File(base, "vieneu-v3-turbo-native")
    }
'''
new = '''    fun modelDir(context: Context): File {
        val base = context.getExternalFilesDir(null) ?: context.filesDir
        val root = File(base, "vieneu-v3-turbo-native")
        root.mkdirs()
        ensureBundledSeaG2pDictionary(context, root)
        return root
    }

    private fun ensureBundledSeaG2pDictionary(context: Context, root: File) {
        val target = File(root, "sea_g2p.bin")
        if (target.isFile && target.length() > 0L) return

        val temporary = File(root, "sea_g2p.bin.part")
        context.assets.open("sea_g2p.bin").use { input ->
            temporary.outputStream().buffered().use { output -> input.copyTo(output) }
        }
        if (target.exists()) target.delete()
        if (!temporary.renameTo(target)) {
            temporary.copyTo(target, overwrite = true)
            temporary.delete()
        }
        check(target.isFile && target.length() > 0L) {
            "Không thể cài từ điển phát âm tiếng Việt sea-g2p."
        }
    }
'''

if new in text:
    print('Android sea-g2p dictionary install is already patched')
else:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f'ModelManager.modelDir anchor: expected one match, found {count}'
        )
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    print('Patched Android sea-g2p dictionary install')

# optimize_vieneu_android_cache_v4.py builds a Kotlin block inside a Python
# triple-quoted string. Its newline marker is currently materialized as a real
# line break between Kotlin quotes. Repair that generated source before Gradle.
activity = app / 'src/main/java/com/vieneu/voiceclone/MainActivity.kt'
activity_text = activity.read_text(encoding='utf-8')
broken_newline_literal = '        temp.writeText(hash + "\n", Charsets.UTF_8)'
fixed_newline_literal = '        temp.writeText(hash + "\\n", Charsets.UTF_8)'

if broken_newline_literal in activity_text:
    activity_text = activity_text.replace(
        broken_newline_literal,
        fixed_newline_literal,
        1,
    )
    activity.write_text(activity_text, encoding='utf-8')
    print('Repaired generated MainActivity Kotlin newline literal')
elif fixed_newline_literal in activity_text:
    print('Generated MainActivity Kotlin newline literal is already valid')
else:
    raise RuntimeError('MainActivity newline-literal anchor was not found')

# A system TTS service can be started by Android without MainActivity ever
# running. Configure native diagnostics in the service itself so TalkBack runs
# retain OpenCL/acoustic timing, then spend the acceptable cold-start budget on
# one real F32 synthesis to warm lazy GPU/runtime paths before later requests.
service = app / 'src/main/java/com/vieneu/voiceclone/VieNeuTtsService.kt'
service_text = service.read_text(encoding='utf-8')

on_create_old = '''        Diagnostics.start(this)
        warmExecutor.execute {
'''
on_create_new = '''        Diagnostics.start(this)
        configureNativeDiagnostics()
        warmExecutor.execute {
'''
if on_create_new not in service_text:
    count = service_text.count(on_create_old)
    if count != 1:
        raise RuntimeError(f'VieNeuTtsService.onCreate anchor: expected one match, found {count}')
    service_text = service_text.replace(on_create_old, on_create_new, 1)

warm_old = '''    private fun warmConfiguredVoice() {
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
'''
warm_new = '''    private fun configureNativeDiagnostics() {
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
        val profile = VoiceProfileStore.get(this, settings.profileId) ?: return
        if (!profile.cacheReady || !ModelManager.isReady(this)) return

        val warmId = "system-tts-warm-${UUID.randomUUID()}"
        val initialized = VieNeuEngine.ensureInitialized(this, warmId)
        var warmSynthesisMs: Double? = null
        var warmSynthesisSuccess: Boolean? = null
        var warmError: String? = null

        if (initialized) {
            val output = File(cacheDir, "$warmId.wav")
            try {
                val startNs = SystemClock.elapsedRealtimeNanos()
                val written = VieNeuNative.synthesize(
                    "Xin chào.",
                    profile.referenceFile.absolutePath,
                    profile.id,
                    true,
                    false,
                    "",
                    output.absolutePath,
                )
                warmSynthesisMs = (SystemClock.elapsedRealtimeNanos() - startNs) / 1_000_000.0
                warmSynthesisSuccess = written == output.absolutePath && output.isFile && output.length() > 44L
                if (warmSynthesisSuccess != true) {
                    warmError = VieNeuNative.lastError().ifBlank { "Warm-up synthesis không tạo WAV hợp lệ." }
                }
            } finally {
                output.delete()
            }
        }

        Diagnostics.log(
            "system_tts",
            "system_tts.warm.complete",
            level = if (warmError == null) "INFO" else "WARN",
            data = mapOf(
                "profile_id" to profile.id,
                "engine_initialized_now" to initialized,
                "cache_ready" to profile.cacheReady,
                "warm_synthesis_performed" to initialized,
                "warm_synthesis_success" to warmSynthesisSuccess,
                "warm_synthesis_ms" to warmSynthesisMs,
                "warm_error" to warmError,
            ),
        )
        if (warmError != null) {
            throw IllegalStateException(warmError)
        }
    }
'''
if warm_new not in service_text:
    count = service_text.count(warm_old)
    if count != 1:
        raise RuntimeError(f'VieNeuTtsService warm block: expected one match, found {count}')
    service_text = service_text.replace(warm_old, warm_new, 1)

required_service = (
    'configureNativeDiagnostics()',
    'system_tts.native_diagnostics.ready',
    'warm_synthesis_performed',
    '"Xin chào."',
    'warm_synthesis_ms',
)
missing_service = [fragment for fragment in required_service if fragment not in service_text]
if missing_service:
    raise RuntimeError(f'VieNeuTtsService warm/diagnostics fragments missing: {missing_service}')
service.write_text(service_text, encoding='utf-8')
print('Enabled cold-start native diagnostics and full-F32 system-TTS warm-up synthesis')

gradle = app / 'build.gradle.kts'
if not gradle.is_file():
    raise RuntimeError(f'Missing Android Gradle file: {gradle}')
