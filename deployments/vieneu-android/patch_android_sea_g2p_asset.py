#!/usr/bin/env python3

import pathlib
import re
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

# The cache-v4 generator emits this Kotlin line through a Python triple string;
# normalize the literal after all Android source generators have finished.
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
    print('Repaired generated MainActivity Kotlin newline literal')
elif fixed_newline_literal in activity_text:
    print('Generated MainActivity Kotlin newline literal is already valid')
else:
    raise RuntimeError('MainActivity newline-literal anchor was not found')


def replace_activity_once(old_text: str, new_text: str, label: str) -> None:
    global activity_text
    count = activity_text.count(old_text)
    if count != 1:
        raise RuntimeError(f'{label}: expected one final MainActivity match, found {count}')
    activity_text = activity_text.replace(old_text, new_text, 1)


def regex_activity_once(pattern: str, replacement: str, label: str) -> None:
    global activity_text
    activity_text, count = re.subn(
        pattern,
        lambda _match: replacement,
        activity_text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f'{label}: expected one final MainActivity regex match, found {count}')


# MainActivity reaches its final shape only after short-text, cache-v4 and direct
# WAV finalizers. Apply the shared voice catalog here, not during the earlier
# generation-quality pass, so the outside and system lists cannot diverge.
replace_activity_once(
    '    private fun selectedVoiceId(): String = voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { "" }\n',
    '''    private fun selectedCatalogVoice(): VoiceCatalogEntry? =
        VoiceCatalog.find(this, voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { "" })

    private fun selectedVoiceId(): String = selectedCatalogVoice()?.nativeVoiceId.orEmpty()
''',
    'shared catalog selected voice',
)

catalog_helpers = r'''    private fun loadVoices(root: File, preferredKey: String? = null) {
        val previousKey = voiceIds.getOrNull(voiceSpinner.selectedItemPosition)
        val catalog = VoiceCatalog.list(this)
        voiceIds = listOf("") + catalog.map { it.key }
        voiceDescriptions = listOf(
            "Dùng tệp WAV nói rõ, sạch, dài khoảng 4–8 giây để clone giọng."
        ) + catalog.map { voice ->
            buildString {
                append(voice.description)
                append(
                    when (voice.source) {
                        VoiceCatalogSource.PRESET -> " · Giọng có sẵn."
                        VoiceCatalogSource.SAVED -> " · Giọng clone đã lưu."
                    }
                )
                if (!voice.isReady) append(" · Chưa sẵn sàng.")
            }
        }
        val labels = listOf("Clone từ tệp WAV") + catalog.map { voice ->
            if (voice.isReady) voice.label else "${voice.label} (chưa sẵn sàng)"
        }
        voiceSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            labels,
        )
        val savedKey = VoiceProfileStore.loadSettings(this).voiceKey
        val desiredKey = sequenceOf(
            preferredKey,
            previousKey,
            savedKey,
            VoiceCatalog.default(this)?.key,
        )
            .filterNotNull()
            .firstOrNull { key -> catalog.any { it.key == key } }
        val selected = voiceIds.indexOf(desiredKey).takeIf { it >= 0 } ?: 0
        voiceSpinner.setSelection(selected, false)
        updateVoiceUi()
        Diagnostics.log(
            "voice",
            "catalog.loaded",
            data = mapOf(
                "model_root" to root.absolutePath,
                "preset_count" to catalog.count { it.source == VoiceCatalogSource.PRESET },
                "saved_count" to catalog.count { it.source == VoiceCatalogSource.SAVED },
                "selected_voice_key" to selectedCatalogVoice()?.key,
                "default_voice_key" to VoiceCatalog.default(this)?.key,
                "shared_catalog" to true,
            ),
        )
    }

    private fun updateVoiceUi() {
        val voice = selectedCatalogVoice()
        val clone = voice == null
        voiceInfo.text = voiceDescriptions.getOrElse(voiceSpinner.selectedItemPosition) { "" }
        findViewById<Button>(R.id.pickReferenceButton).isEnabled = clone
        saveVoiceProfileButton.isEnabled = clone && (
            VoiceProfileStore.activeReference(this)
                ?.let { VoiceProfileStore.hasWarmV4Caches(it) }
                ?: false
        )
        generateButton.text = when {
            clone -> "Clone và tạo giọng"
            voice?.source == VoiceCatalogSource.SAVED -> "Tạo bằng giọng đã lưu ${voice.label}"
            else -> "Tạo bằng giọng ${voice?.label ?: "VieNeu"}"
        }
        referenceStatus.text = when {
            clone -> referenceFile?.takeIf { it.isFile }?.let {
                "Giọng mẫu: ${it.name} (${it.length() / 1024} KB)"
            } ?: "Giọng mẫu: chưa chọn"
            voice?.source == VoiceCatalogSource.SAVED ->
                "Giọng đã lưu: ${voice.label} · dùng WAV và cache v4"
            else -> "Giọng có sẵn: ${voice?.label ?: "VieNeu"} · không cần WAV"
        }
    }

    private fun refreshModelStatus() {'''

regex_activity_once(
    r'''    private fun loadVoices\(root: File\) \{.*?\n    \}\n\n    private fun updateVoiceUi\(\) \{.*?\n    \}\n\n    private fun refreshModelStatus\(\) \{''',
    catalog_helpers,
    'replace final independent preset list with VoiceCatalog',
)

regex_activity_once(
    r'''    override fun onResume\(\) \{.*?\n    \}\n\n    override fun onPause\(\) \{''',
    r'''    override fun onResume() {
        super.onResume()
        Diagnostics.log("app", "activity.onResume", snapshot = true)
        if (ModelManager.isReady(this)) {
            loadVoices(ModelManager.modelDir(this))
        }
        saveVoiceProfileButton.isEnabled = selectedCatalogVoice() == null && (
            VoiceProfileStore.activeReference(this)
                ?.let { VoiceProfileStore.hasWarmV4Caches(it) }
                ?: false
        )
        Diagnostics.log(
            "system_tts",
            "system_tts.discovery",
            data = mapOf(
                "discoverable" to VoiceCatalog.isEngineDiscoverable(this),
                "voice_count" to VoiceCatalog.list(this).size,
            ),
        )
    }

    override fun onPause() {''',
    'refresh shared catalog on resume',
)

replace_activity_once(
    '''        val voiceId = selectedVoiceId()
        val clone = voiceId.isBlank()
''',
    '''        val catalogVoice = selectedCatalogVoice()
        val voiceId = catalogVoice?.nativeVoiceId.orEmpty()
        val clone = catalogVoice == null
''',
    'generation shared catalog selection',
)

replace_activity_once(
    '''        if (clone && (ref == null || !ref.isFile)) {
''',
    '''        if (!clone && catalogVoice?.isReady != true) {
            Diagnostics.log(
                "generation",
                "validation.failure",
                level = "WARN",
                data = mapOf(
                    "reason" to "selected_voice_not_ready",
                    "voice_key" to catalogVoice?.key,
                ),
            )
            statusText.text = "Giọng đang chọn chưa sẵn sàng."
            return
        }
        if (clone && (ref == null || !ref.isFile)) {
''',
    'validate shared catalog voice readiness',
)

replace_activity_once(
    '''        val referencePath = if (clone) ref!!.absolutePath else ""
        val voiceMode = if (clone) "reference" else "preset"
''',
    '''        val referencePath = when {
            clone -> ref!!.absolutePath
            catalogVoice?.source == VoiceCatalogSource.SAVED ->
                catalogVoice.referenceFile?.takeIf { it.isFile }?.absolutePath
                    ?: throw IllegalStateException("Giọng đã lưu bị thiếu tệp WAV tham chiếu.")
            else -> ""
        }
        val voiceMode = when {
            clone -> "reference"
            catalogVoice?.source == VoiceCatalogSource.SAVED -> "saved"
            else -> "preset"
        }
''',
    'generation saved voice reference path',
)

replace_activity_once(
    '''                "reference" to if (clone && ref != null) Diagnostics.wavInfo(ref) else null
''',
    '''                "reference" to when {
                    clone && ref != null -> Diagnostics.wavInfo(ref)
                    catalogVoice?.source == VoiceCatalogSource.SAVED && catalogVoice.referenceFile != null ->
                        Diagnostics.wavInfo(catalogVoice.referenceFile)
                    else -> null
                }
''',
    'generation synchronized voice diagnostics',
)

save_helpers = r'''    private fun askSaveVoiceProfile() {
        if (selectedCatalogVoice() != null) {
            statusText.text = "Chỉ giọng clone từ WAV mới cần lưu thành giọng mới."
            return
        }
        val reference = VoiceProfileStore.activeReference(this) ?: referenceFile
        if (reference == null || !reference.isFile) {
            statusText.text = "Chưa có giọng mẫu để lưu."
            return
        }
        val input = EditText(this).apply {
            hint = "Tên giọng"
            setSingleLine(true)
        }
        AlertDialog.Builder(this)
            .setTitle("Lưu giọng")
            .setMessage("Giọng đã lưu sẽ xuất hiện đồng thời ở màn hình này và trong giọng đọc hệ thống.")
            .setView(input)
            .setNegativeButton("Hủy", null)
            .setPositiveButton("Lưu") { _, _ ->
                val name = input.text.toString().trim()
                if (name.isBlank()) {
                    statusText.text = "Tên giọng không được để trống."
                } else {
                    saveVoiceProfile(reference, name)
                }
            }
            .show()
    }

    private fun saveVoiceProfile(reference: File, name: String) {
        saveVoiceProfileButton.isEnabled = false
        statusText.text = "Đang chuẩn bị cache và lưu giọng..."
        executor.execute {
            var warmOutput: File? = null
            try {
                if (!VoiceProfileStore.hasWarmV4Caches(reference)) {
                    val generationId = "voice-profile-warm-${UUID.randomUUID()}"
                    VieNeuEngine.ensureInitialized(this, generationId)
                    val output = File(cacheDir, "$generationId.wav")
                    warmOutput = output
                    val written = VieNeuNative.synthesize(
                        "Xin chào.",
                        reference.absolutePath,
                        "voice-profile-warm",
                        true,
                        false,
                        "",
                        output.absolutePath
                    ) ?: throw IllegalStateException(
                        VieNeuNative.lastError().ifBlank { "Không warm được cache giọng." }
                    )
                    if (written != output.absolutePath) {
                        throw IllegalStateException("VieNeu trả về đường dẫn warm cache không hợp lệ.")
                    }
                    if (!VoiceProfileStore.hasWarmV4Caches(reference)) {
                        throw IllegalStateException("VieNeu chưa tạo đủ speaker-v4 và refcodes-v4.")
                    }
                }
                val profile = VoiceProfileStore.saveVoice(this, reference, name)
                runOnUiThread {
                    loadVoices(
                        ModelManager.modelDir(this),
                        VoiceCatalog.savedKey(profile.id),
                    )
                    statusText.text =
                        "Đã lưu giọng “${profile.name}”. Hai danh sách giọng đã đồng bộ."
                }
            } catch (t: Throwable) {
                Diagnostics.error("voice_profile", "voice_profile.save_from_clone.failure", t)
                runOnUiThread {
                    saveVoiceProfileButton.isEnabled = selectedCatalogVoice() == null && (
                        VoiceProfileStore.activeReference(this)
                            ?.let { VoiceProfileStore.hasWarmV4Caches(it) }
                            ?: false
                    )
                    statusText.text = "Không lưu được giọng: ${t.message ?: t.javaClass.simpleName}"
                }
            } finally {
                warmOutput?.delete()
            }
        }
    }

    private fun exportDiagnostics() {'''

regex_activity_once(
    r'''    private fun askSaveVoiceProfile\(\) \{.*?\n    \}\n\n    private fun saveVoiceProfile\(reference: File, name: String\) \{.*?\n    \}\n\n    private fun exportDiagnostics\(\) \{''',
    save_helpers,
    'synchronize saved voice immediately in final MainActivity',
)

activity.write_text(activity_text, encoding='utf-8')
final_activity = activity.read_text(encoding='utf-8')
required_activity = (
    'selectedCatalogVoice()',
    'VoiceCatalog.list(this)',
    'VoiceCatalog.savedKey(profile.id)',
    'VoiceCatalogSource.SAVED',
    '"shared_catalog" to true',
    'system_tts.discovery',
    'val catalogVoice = selectedCatalogVoice()',
    'voiceMode = when',
)
missing_activity = [fragment for fragment in required_activity if fragment not in final_activity]
if missing_activity:
    raise RuntimeError(f'Final MainActivity shared-catalog fragments missing: {missing_activity}')

forbidden_activity = (
    'val json = JSONObject(File(root, "voices_v3_turbo.json").readText())',
    'val voiceMode = if (clone) "reference" else "preset"',
)
stale_activity = [fragment for fragment in forbidden_activity if fragment in final_activity]
if stale_activity:
    raise RuntimeError(f'Final MainActivity still contains independent voice-list logic: {stale_activity}')
print('Applied final synchronized preset/saved VoiceCatalog to MainActivity')

# System-TTS behavior lives in tracked Kotlin sources. Verify it after all source
# materialization so the APK cannot silently diverge from the reviewed service.
service = app / 'src/main/java/com/vieneu/voiceclone/VieNeuTtsService.kt'
service_text = service.read_text(encoding='utf-8')
required_service = (
    'configureNativeDiagnostics()',
    'system_tts.native_diagnostics.ready',
    'warm_synthesis_performed',
    'VoiceCatalog.resolve(this, settings)',
    'override fun onGetVoices()',
    'override fun onLoadVoice(',
    'request.voiceName',
    'system_tts.first_pcm',
    'VieNeuNative.cancel()',
)
missing_service = [fragment for fragment in required_service if fragment not in service_text]
if missing_service:
    raise RuntimeError(f'VieNeuTtsService unified system-TTS fragments missing: {missing_service}')
print('Verified unified Android system-TTS service and hot-warm diagnostics')

catalog = app / 'src/main/java/com/vieneu/voiceclone/VoiceCatalog.kt'
catalog_text = catalog.read_text(encoding='utf-8')
required_catalog = (
    'PRESET_PREFIX',
    'SAVED_PREFIX',
    'voices_v3_turbo.json',
    'ACTION_TTS_DATA_INSTALLED',
    'MATCH_DEFAULT_ONLY',
)
missing_catalog = [fragment for fragment in required_catalog if fragment not in catalog_text]
if missing_catalog:
    raise RuntimeError(f'VoiceCatalog fragments missing: {missing_catalog}')
print('Verified one shared preset/saved VoiceCatalog')

gradle = app / 'build.gradle.kts'
if not gradle.is_file():
    raise RuntimeError(f'Missing Android Gradle file: {gradle}')
