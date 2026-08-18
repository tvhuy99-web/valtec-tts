#!/usr/bin/env python3

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
                    saveVoiceProfileButton.isEnabled = true
                    statusText.text = "Hoàn tất: ${out.name} (%.2f giây, RTF %.3f)".format(
''',
    'enable voice save after generation',
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
        saveVoiceProfileButton.isEnabled = VoiceProfileStore.activeReference(this)
            ?.let { VoiceProfileStore.hasWarmV4Caches(it) }
            ?: false
    }
''',
    'refresh voice save state on resume',
)

save_helpers = r'''    private fun askSaveVoiceProfile() {
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
            .setMessage("VieNeu sẽ giữ giọng và cache v4 để lần sau dùng ngay.")
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
                    saveVoiceProfileButton.isEnabled = true
                    statusText.text = "Đã lưu giọng “${profile.name}”. Lần sau có thể dùng ngay từ cache v4."
                }
            } catch (t: Throwable) {
                Diagnostics.error("voice_profile", "voice_profile.save_from_clone.failure", t)
                runOnUiThread {
                    saveVoiceProfileButton.isEnabled = VoiceProfileStore.activeReference(this)
                        ?.let { VoiceProfileStore.hasWarmV4Caches(it) }
                        ?: false
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
)
missing = [fragment for fragment in required if fragment not in final]
if missing:
    raise RuntimeError(f'MainActivity missing system TTS UI fragments: {missing}')

print('Connected saved voice profiles and system-TTS settings to MainActivity')
