package com.vieneu.voiceclone

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.speech.tts.TextToSpeech

class TtsDataActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        when (intent?.action) {
            TextToSpeech.Engine.ACTION_CHECK_TTS_DATA -> handleCheckData()
            TextToSpeech.Engine.ACTION_GET_SAMPLE_TEXT -> handleSampleText()
            else -> setResult(RESULT_CANCELED)
        }
        finish()
    }

    private fun handleCheckData() {
        val available = arrayListOf("vie-VNM")
        val result = Intent().apply {
            putStringArrayListExtra(TextToSpeech.Engine.EXTRA_AVAILABLE_VOICES, available)
            putStringArrayListExtra(TextToSpeech.Engine.EXTRA_UNAVAILABLE_VOICES, arrayListOf())
        }
        setResult(TextToSpeech.Engine.CHECK_VOICE_DATA_PASS, result)
        Diagnostics.log(
            "system_tts",
            "system_tts.check_data",
            data = mapOf(
                "engine_discoverable" to VoiceCatalog.isEngineDiscoverable(this),
                "catalog_voice_count" to VoiceCatalog.list(this).size,
            ),
        )
    }

    private fun handleSampleText() {
        val result = Intent().apply {
            putExtra(
                TextToSpeech.Engine.EXTRA_SAMPLE_TEXT,
                "Xin chào. Đây là giọng đọc hệ thống của VieNeu.",
            )
        }
        setResult(RESULT_OK, result)
    }
}
