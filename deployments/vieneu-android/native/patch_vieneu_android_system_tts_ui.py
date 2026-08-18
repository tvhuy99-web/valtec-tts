#!/usr/bin/env python3

import re
from pathlib import Path

native_dir = Path(__file__).resolve().parent
android_root = native_dir.parent
activity = android_root / 'app/src/main/java/com/vieneu/voiceclone/MainActivity.kt'
text = activity.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one match, found {count}')
    text = text.replace(old, new, 1)


def regex_once(pattern: str, replacement: str, label: str) -> None:
    global text
    text, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f'{label}: expected one regex match, found {count}')


replace_once(
    'import android.app.Activity\n',
    'import android.app.Activity\nimport android.app.AlertDialog\n',
    'system TTS AlertDialog import',
)

replace_once(
    '    private lateinit var exportDiagnosticsButton: Button\n',
    '''    private lateinit var exportDiagnosticsButton: Button
    private lateinit var saveVoiceProfileButton: Button
    private lateinit var systemVoiceSettingsButton: Button
''',
    'system TTS button fields',
)

replace_once(
    '        exportDiagnosticsButton = findViewById(R.id.exportDiagnosticsButton)\n',
    '''        exportDiagnosticsButton = findViewById(R.id.exportDiagnosticsButton)
        saveVoiceProfileButton = findViewById(R.id.saveVoiceProfileButton)
        systemVoiceSettingsButton = findViewById(R.id.systemVoiceSettingsButton)
''',
    'system TTS button bindings',
)

replace_once(
    '''        findViewById<Button>(R.id.pickReferenceButton).setOnClickListener {
            Diagnostics.log("ui", "click.pick_reference")
            pickReference()
        }
''',
    '''        findViewById<Button>(R.id.pickReferenceButton).setOnClickListener {
            Diagnostics.log("ui", "click.pick_reference")
            saveVoiceProfileButton.isEnabled = false
            pickReference()
        }
''',
    'disable voice save while selecting reference',
)

replace_once(
    '''        saveButton.setOnClickListener {
            Diagnostics.log("ui", "click.save_output")
            saveOutput()
        }
        exportDiagnosticsButton.setOnClickListener {
''',
    '''        saveButton.setOnClickListener {
            Diagnostics.log("ui", "click.save_output")
            saveOutput()
        }
        saveVoiceProfileButton.setOnClickListener {
            Diagnostics.log("ui", "click.save_voice_profile")
            askSaveVoiceProfile()
        }
        systemVoiceSettingsButton.setOnClickListener {
            Diagnostics.log("ui", "click.system_voice_settings")
            startActivity(Intent(this, SystemVoiceSettingsActivity::class.java))
        }
        exportDiagnosticsButton.setOnClickListener {
''',
    'system TTS button listeners',
)

replace_once(
    '        saveButton.isEnabled = false\n        generateProgress.visibility = View.VISIBLE\n',
    '''        saveButton.isEnabled = false
        saveVoiceProfileButton.isEnabled = false
        generateProgress.visibility = View.VISIBLE
''',
    'disable voice save during generation',
)

replace_once(
    '''                    playButton.isEnabled = true
                    saveButton.isEnabled = true
                    statusText.text = "Hoàn tất: ${out.name} (%.2f giây, RTF %.3f)".format(
''',
    '''                    playButton.isEnabled = true
                    saveButton.isEnabled = true
                    saveVoiceProfileButton.isEnabled = clone && (
                        VoiceProfileStore.activeReference(this)
                            ?.let { VoiceProfileStore.hasWarmV4Caches(it) }
                            ?: false
                    )
                    statusText.text = "Hoàn tất: ${out.name} (%.2f giây, RTF %.3f)".format(
''',
    'enable clone voice save after generation',
)

replace_once(
    '''    override fun onResume() {
        super.onResume()
        Diagnostics.log("app", "activity.onResume", snapshot = true)
    }
''',
    '''    override fun onResume() {
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
''',
    'refresh synchronized voice catalog on resume',
)

replace_once(
    '    private fun selectedVoiceId(): String = voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { "" }\n',
    '''    private fun selectedCatalogVoice(): VoiceCatalogEntry? =
        VoiceCatalog.find(this, voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { "" })

    private fun selectedVoiceId(): String = selectedCatalogVoice()?.nativeVoiceId.orEmpty()
''',
    'resolve selected voice through shared catalog',
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

regex_once(
    r'''    private fun loadVoices\(root: File\) \{.*?\n    \}\n\n    private fun updateVoiceUi\(\) \{.*?\n    \}\n\n    private fun refreshModelStatus\(\) \{''',
    catalog_helpers,
    'replace independent preset list with shared VoiceCatalog',
)

replace_once(
    '''        val voiceId = selectedVoiceId()
        val clone = voiceId.isBlank()
''',
    '''        val catalogVoice = selectedCatalogVoice()
        val voiceId = catalogVoice?.nativeVoiceId.orEmpty()
        val clone = catalogVoice == null
''',
    'generation shared catalog selection',
)

replace_once(
    '''        if (clone && (ref == null || !ref.isFile)) {
''',
    '''        if (!clone && catalogVoice?.isReady != true) {
            Diagnostics.log(
                "generation",
                "validation.failure",
                level = "WARN",
                data = mapOf("reason" to "selected_voice_not_ready", "voice_key" to catalogVoice?.key),
            )
            statusText.text = "Giọng đang chọn chưa sẵn sàng."
            return
        }
        if (clone && (ref == null || !ref.isFile)) {
''',
    'validate synchronized catalog voice readiness',
)

replace_once(
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
    'generation reference path for preset and saved catalog voices',
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
                        "Đã lưu giọng “${profile.name}”. Danh sách tạo giọng và giọng hệ thống đã đồng bộ."
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

'''

replace_once(
    '    private fun exportDiagnostics() {\n',
    save_helpers + '    private fun exportDiagnostics() {\n',
    'voice profile save helpers',
)

activity.write_text(text, encoding='utf-8')
final = activity.read_text(encoding='utf-8')
required = (
    'saveVoiceProfileButton',
    'SystemVoiceSettingsActivity::class.java',
    'VoiceProfileStore.saveVoice',
    'voice-profile-warm-',
    'selectedCatalogVoice()',
    'VoiceCatalog.list(this)',
    'VoiceCatalog.savedKey(profile.id)',
    'VoiceCatalogSource.SAVED',
    '"shared_catalog" to true',
    'system_tts.discovery',
)
missing = [fragment for fragment in required if fragment not in final]
if missing:
    raise RuntimeError(f'MainActivity missing synchronized system TTS UI fragments: {missing}')

forbidden = (
    'val json = JSONObject(File(root, "voices_v3_turbo.json").readText())',
    'val voiceMode = if (clone) "reference" else "preset"',
)
stale = [fragment for fragment in forbidden if fragment in final]
if stale:
    raise RuntimeError(f'MainActivity still contains independent voice-catalog fragments: {stale}')

print('Connected MainActivity and system TTS to one synchronized VoiceCatalog')
