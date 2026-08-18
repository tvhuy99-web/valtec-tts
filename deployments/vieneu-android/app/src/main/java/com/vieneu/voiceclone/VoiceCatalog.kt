package com.vieneu.voiceclone

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.speech.tts.TextToSpeech
import org.json.JSONObject
import java.io.File
import java.util.Locale

enum class VoiceCatalogSource {
    PRESET,
    SAVED,
}

data class VoiceCatalogEntry(
    val key: String,
    val label: String,
    val description: String,
    val source: VoiceCatalogSource,
    val nativeVoiceId: String,
    val referenceFile: File?,
    val profileId: String?,
    val isDefault: Boolean,
    val isReady: Boolean,
)

object VoiceCatalog {
    const val PRESET_PREFIX = "vieneu:preset:"
    const val SAVED_PREFIX = "vieneu:saved:"

    fun presetKey(id: String): String = PRESET_PREFIX + id

    fun savedKey(id: String): String = SAVED_PREFIX + id

    fun list(context: Context): List<VoiceCatalogEntry> {
        val modelReady = ModelManager.isReady(context)
        val presetSeed = loadPresetSeed(context, modelReady)
        val savedSeed = VoiceProfileStore.list(context).map { profile ->
            Seed(
                key = savedKey(profile.id),
                name = profile.name,
                description = "Giọng đã nhân bản và lưu trong VieNeu.",
                source = VoiceCatalogSource.SAVED,
                nativeVoiceId = profile.id,
                referenceFile = profile.referenceFile,
                profileId = profile.id,
                isDefault = false,
                isReady = modelReady && profile.cacheReady && profile.referenceFile.isFile,
            )
        }

        val seeds = presetSeed + savedSeed
        val duplicateNames = seeds.groupingBy { it.name.lowercase(Locale.ROOT) }.eachCount()
        return seeds.map { seed ->
            val duplicate = (duplicateNames[seed.name.lowercase(Locale.ROOT)] ?: 0) > 1
            val sourceSuffix = when (seed.source) {
                VoiceCatalogSource.PRESET -> " · có sẵn"
                VoiceCatalogSource.SAVED -> " · đã lưu"
            }
            val defaultSuffix = if (seed.isDefault) " (mặc định)" else ""
            VoiceCatalogEntry(
                key = seed.key,
                label = seed.name + if (duplicate) sourceSuffix else "" + defaultSuffix,
                description = seed.description,
                source = seed.source,
                nativeVoiceId = seed.nativeVoiceId,
                referenceFile = seed.referenceFile,
                profileId = seed.profileId,
                isDefault = seed.isDefault,
                isReady = seed.isReady,
            )
        }
    }

    fun find(context: Context, key: String?): VoiceCatalogEntry? {
        if (key.isNullOrBlank()) return null
        return list(context).firstOrNull { entry ->
            entry.key == key ||
                (entry.source == VoiceCatalogSource.SAVED && entry.profileId == key)
        }
    }

    fun default(context: Context): VoiceCatalogEntry? {
        val voices = list(context)
        return voices.firstOrNull { it.isDefault && it.isReady }
            ?: voices.firstOrNull { it.isReady }
            ?: voices.firstOrNull()
    }

    fun resolve(context: Context, settings: SystemVoiceSettings): VoiceCatalogEntry? {
        return find(context, settings.voiceKey)
            ?: settings.profileId?.let { find(context, savedKey(it)) }
            ?: default(context)
    }

    fun notifyChanged(context: Context, reason: String) {
        runCatching {
            context.sendBroadcast(Intent(TextToSpeech.Engine.ACTION_TTS_DATA_INSTALLED))
        }.onFailure {
            Diagnostics.error("voice_catalog", "voice_catalog.broadcast.failure", it)
        }
        Diagnostics.log(
            "voice_catalog",
            "voice_catalog.changed",
            data = mapOf(
                "reason" to reason,
                "voice_count" to list(context).size,
            ),
        )
    }

    fun isEngineDiscoverable(context: Context): Boolean {
        val intent = Intent(TextToSpeech.Engine.INTENT_ACTION_TTS_SERVICE).setPackage(context.packageName)
        return context.packageManager
            .queryIntentServices(intent, PackageManager.MATCH_DEFAULT_ONLY)
            .any { it.serviceInfo?.name == VieNeuTtsService::class.java.name }
    }

    private fun loadPresetSeed(context: Context, modelReady: Boolean): List<Seed> {
        val catalogFile = File(ModelManager.modelDir(context), "voices_v3_turbo.json")
        if (!catalogFile.isFile) return emptyList()
        return runCatching {
            val json = JSONObject(catalogFile.readText(Charsets.UTF_8))
            val defaultVoice = json.optString("default_voice")
            val presets = json.getJSONObject("presets")
            val names = ArrayList<String>()
            val keys = presets.keys()
            while (keys.hasNext()) names += keys.next()
            names.sortedWith(String.CASE_INSENSITIVE_ORDER).map { name ->
                val item = presets.getJSONObject(name)
                Seed(
                    key = presetKey(name),
                    name = name,
                    description = item.optString("description")
                        .ifBlank { "Giọng có sẵn trong VieNeu v3 Turbo." },
                    source = VoiceCatalogSource.PRESET,
                    nativeVoiceId = name,
                    referenceFile = null,
                    profileId = null,
                    isDefault = name == defaultVoice,
                    isReady = modelReady,
                )
            }
        }.onFailure {
            Diagnostics.error(
                "voice_catalog",
                "voice_catalog.preset_load.failure",
                it,
                mapOf("path" to catalogFile.absolutePath),
            )
        }.getOrDefault(emptyList())
    }

    private data class Seed(
        val key: String,
        val name: String,
        val description: String,
        val source: VoiceCatalogSource,
        val nativeVoiceId: String,
        val referenceFile: File?,
        val profileId: String?,
        val isDefault: Boolean,
        val isReady: Boolean,
    )
}
