#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_android_preset_voices.py <android-dir>')
r=Path(sys.argv[1]); a=r/'app/src/main/java/com/vieneu/voiceclone/MainActivity.kt'; k=r/'app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt'; x=r/'app/src/main/res/layout/activity_main.xml'; j=r/'native/vieneu_jni.cpp'; g=r/'app/build.gradle.kts'

def p(f,o,n):
    s=f.read_text(); c=s.count(o)
    if c!=1: raise RuntimeError(f'{f}: expected 1 anchor, found {c}: {o[:60]!r}')
    f.write_text(s.replace(o,n,1))

p(k,'external fun synthesize(text: String, referenceWav: String, style: String): FloatArray?',
    'external fun synthesize(text: String, referenceWav: String, voiceId: String, style: String): FloatArray?')
p(j,'jstring text, jstring reference_wav, jstring style) {','jstring text, jstring reference_wav, jstring voice_id, jstring style) {')
p(j,'params.ref_audio_path = from_jstring(env, reference_wav);\n        params.style',
    'params.ref_audio_path = from_jstring(env, reference_wav);\n        params.voice_id = from_jstring(env, voice_id);\n        params.style')
p(j,'<< ",\\\"reference_wav\\\":" << quote(params.ref_audio_path)\n                   << ",\\\"style\\\":"',
    '<< ",\\\"reference_wav\\\":" << quote(params.ref_audio_path)\n                   << ",\\\"voice_id\\\":" << quote(params.voice_id)\n                   << ",\\\"voice_mode\\\":" << quote(params.ref_audio_path.empty() ? "preset" : "reference")\n                   << ",\\\"style\\\":"')

p(x,'android:text="VieNeu Voice Clone"','android:text="VieNeu TTS &amp; Voice Clone"')
p(x,'android:text="Clone giọng tiếng Việt offline bằng VieNeu-TTS v3 Turbo"','android:text="Dùng giọng có sẵn hoặc clone giọng tiếng Việt offline bằng VieNeu-TTS v3 Turbo"')
p(x,'        <TextView\n            android:id="@+id/referenceStatus"', '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="20dp"
            android:text="Nguồn giọng"
            android:textStyle="bold" />

        <Spinner
            android:id="@+id/voiceSpinner"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp" />

        <TextView
            android:id="@+id/voiceInfo"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp"
            android:text="Đang đọc giọng từ mô hình..."
            android:textSize="13sp" />

        <TextView
            android:id="@+id/referenceStatus"''')
p(x,'android:text="Clone và tạo giọng"','android:text="Tạo giọng"')

p(a,'import android.view.View\nimport android.widget.ArrayAdapter', 'import android.view.View\nimport android.widget.AdapterView\nimport android.widget.ArrayAdapter')
p(a,'import java.util.UUID\nimport java.util.concurrent.Executors', 'import java.util.UUID\nimport java.util.concurrent.Executors\nimport org.json.JSONObject')
p(a,'private var engineReady = false\n\n    private lateinit var modelStatus', '''private var engineReady = false
    private var voiceIds = listOf("")
    private var voiceDescriptions = listOf("Dùng WAV của bạn để clone giọng.")

    private lateinit var modelStatus''')
p(a,'private lateinit var textInput: EditText\n    private lateinit var styleSpinner: Spinner', '''private lateinit var textInput: EditText
    private lateinit var voiceSpinner: Spinner
    private lateinit var voiceInfo: TextView
    private lateinit var styleSpinner: Spinner''')
p(a,'textInput = findViewById(R.id.textInput)\n        styleSpinner', '''textInput = findViewById(R.id.textInput)
        voiceSpinner = findViewById(R.id.voiceSpinner)
        voiceInfo = findViewById(R.id.voiceInfo)
        styleSpinner''')
p(a,'''        styleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Tin tức", "Đọc truyện")
        )

        downloadButton.setOnClickListener {''', '''        styleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            arrayOf("Tự nhiên", "Tin tức", "Đọc truyện")
        )
        voiceSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) = updateVoiceUi()
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }

        downloadButton.setOnClickListener {''')

p(a,'    private fun refreshModelStatus() {', '''    private fun selectedVoiceId(): String = voiceIds.getOrElse(voiceSpinner.selectedItemPosition) { "" }

    private fun loadVoices(root: File) {
        val ids = mutableListOf("")
        val descriptions = mutableListOf("Dùng tệp WAV nói rõ, sạch, dài khoảng 4–8 giây để clone giọng.")
        var defaultVoice = ""
        runCatching {
            val json = JSONObject(File(root, "voices_v3_turbo.json").readText())
            defaultVoice = json.optString("default_voice")
            val presets = json.getJSONObject("presets")
            val names = mutableListOf<String>()
            val keys = presets.keys()
            while (keys.hasNext()) names += keys.next()
            names.sorted().forEach { name ->
                val item = presets.getJSONObject(name)
                ids += name
                descriptions += item.optString("description").ifBlank { "Giọng có sẵn trong VieNeu v3 Turbo" }
            }
        }.onFailure { Diagnostics.error("voice", "catalog.load_failure", it) }
        voiceIds = ids
        voiceDescriptions = descriptions
        val labels = ids.mapIndexed { index, id ->
            if (index == 0) "Clone từ tệp WAV" else if (id == defaultVoice) "$id (mặc định)" else id
        }
        voiceSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
        val selected = ids.indexOf(defaultVoice).takeIf { it > 0 } ?: 0
        voiceSpinner.setSelection(selected, false)
        updateVoiceUi()
        Diagnostics.log("voice", "catalog.loaded", data = mapOf("preset_count" to (ids.size - 1), "default_voice" to defaultVoice))
    }

    private fun updateVoiceUi() {
        val id = selectedVoiceId()
        val clone = id.isBlank()
        voiceInfo.text = voiceDescriptions.getOrElse(voiceSpinner.selectedItemPosition) { "" }
        findViewById<Button>(R.id.pickReferenceButton).isEnabled = clone
        generateButton.text = if (clone) "Clone và tạo giọng" else "Tạo bằng giọng $id"
        referenceStatus.text = if (clone) {
            referenceFile?.takeIf { it.isFile }?.let { "Giọng mẫu: ${it.name} (${it.length()/1024} KB)" } ?: "Giọng mẫu: chưa chọn"
        } else "Giọng có sẵn: $id · không cần WAV"
    }

    private fun refreshModelStatus() {''')
p(a,'''        if (ready) {
            modelStatus.text = "Mô hình: đã sẵn sàng"
            downloadButton.text = "Kiểm tra / tải lại phần còn thiếu"
        } else {''', '''        if (ready) {
            modelStatus.text = "Mô hình: đã sẵn sàng"
            downloadButton.text = "Kiểm tra / tải lại phần còn thiếu"
            loadVoices(root)
        } else {''')

p(a,'''        val text = textInput.text.toString().trim()
        val ref = referenceFile
        if (!ModelManager.isReady(this)) {''', '''        val text = textInput.text.toString().trim()
        val ref = referenceFile
        val voiceId = selectedVoiceId()
        val clone = voiceId.isBlank()
        if (!ModelManager.isReady(this)) {''')
p(a,'if (ref == null || !ref.isFile) {','if (clone && (ref == null || !ref.isFile)) {')
p(a,'statusText.text = "Hãy chọn một tệp WAV làm giọng mẫu."','statusText.text = "Hãy chọn WAV làm giọng mẫu hoặc chuyển sang một giọng có sẵn."')
p(a,'''        val generationId = UUID.randomUUID().toString()
        val totalSpan''', '''        val referencePath = if (clone) ref!!.absolutePath else ""
        val voiceMode = if (clone) "reference" else "preset"
        val generationId = UUID.randomUUID().toString()
        val totalSpan''')
p(a,'''                "style" to style,
                "engine_already_ready" to engineReady,
                "reference" to Diagnostics.wavInfo(ref)''', '''                "style" to style,
                "voice_id" to voiceId,
                "voice_mode" to voiceMode,
                "engine_already_ready" to engineReady,
                "reference" to if (clone && ref != null) Diagnostics.wavInfo(ref) else null''')
p(a,'mapOf("generation_id" to generationId, "style" to style, "text_chars" to text.length)',
    'mapOf("generation_id" to generationId, "style" to style, "voice_id" to voiceId, "voice_mode" to voiceMode, "text_chars" to text.length)')
p(a,'VieNeuNative.synthesize(text, ref.absolutePath, style)','VieNeuNative.synthesize(text, referencePath, voiceId, style)')
p(a,'''                    referenceStatus.text = "Giọng mẫu: $name (${target.length() / 1024} KB)"
                    statusText.text''', '''                    updateVoiceUi()
                    statusText.text'')

p(g,'versionCode = 15\n        versionName = "0.7.1-eos-quality"','versionCode = 16\n        versionName = "0.8.0-preset-voices-early-eos"')
for f,need in {a:['voiceSpinner','catalog.loaded','referencePath, voiceId'],k:['voiceId: String'],j:['params.voice_id','voice_mode'],x:['@+id/voiceSpinner','@+id/voiceInfo'],g:['0.8.0-preset-voices-early-eos']}.items():
    s=f.read_text(); missing=[v for v in need if v not in s]
    if missing: raise RuntimeError(f'{f}: missing {missing}')
print('Enabled dynamic preset voices, optional WAV cloning and Android v16')
