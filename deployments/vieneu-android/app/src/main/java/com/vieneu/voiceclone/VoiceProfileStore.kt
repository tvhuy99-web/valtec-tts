package com.vieneu.voiceclone

import android.content.Context
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.Properties

data class VoiceProfile(
    val id: String,
    val name: String,
    val referenceFile: File,
    val createdAtMs: Long,
    val cacheReady: Boolean,
)

data class SystemVoiceSettings(
    val profileId: String?,
    val rate: Float,
    val pitch: Float,
    val volume: Float,
)

object VoiceProfileStore {
    private const val ROOT = "voice_profiles"
    private const val META = "profile.properties"
    private const val REFERENCE = "reference.wav"
    private const val SPEAKER_CACHE = ".vieneu-speaker-v4.bin"
    private const val REFCODES_CACHE = ".vieneu-refcodes-v4.bin"
    private const val PREFS = "system_tts_settings_v1"

    private fun root(context: Context): File =
        File(context.filesDir, ROOT).apply { mkdirs() }

    fun activeReference(context: Context): File? {
        val references = File(context.filesDir, "references")
        if (!references.isDirectory) return null
        val marker = references.listFiles()
            ?.filter { it.isFile && it.name.startsWith("active-") && it.name.endsWith(".txt") }
            ?.maxByOrNull { it.lastModified() }
            ?: return null
        val hash = runCatching { marker.readText(Charsets.UTF_8).trim().lowercase() }.getOrNull()
            ?: return null
        if (!hash.matches(Regex("[0-9a-f]{64}"))) return null
        val namespace = marker.name.removePrefix("active-").removeSuffix(".txt")
        return File(references, "$namespace/$hash.wav")
            .takeIf { it.isFile && it.length() > 44L }
    }

    fun hasWarmV4Caches(reference: File): Boolean =
        File(reference.absolutePath + SPEAKER_CACHE).let { it.isFile && it.length() > 0L } &&
            File(reference.absolutePath + REFCODES_CACHE).let { it.isFile && it.length() > 0L }

    fun saveActiveVoice(context: Context, requestedName: String): VoiceProfile {
        val reference = activeReference(context)
            ?: throw IllegalStateException("Chưa có giọng mẫu đang hoạt động để lưu.")
        return saveVoice(context, reference, requestedName)
    }

    fun saveVoice(context: Context, reference: File, requestedName: String): VoiceProfile {
        require(reference.isFile && reference.length() > 44L) { "Giọng mẫu không hợp lệ." }
        if (!hasWarmV4Caches(reference)) {
            throw IllegalStateException(
                "Giọng này chưa có đủ cache v4. Hãy tạo thử ít nhất một câu ở chế độ bám sát mẫu trước khi lưu."
            )
        }

        val name = requestedName.trim().replace(Regex("\\s+"), " ").take(80)
        require(name.isNotBlank()) { "Tên giọng không được để trống." }
        val id = sha256(reference)
        val dir = File(root(context), id).apply { mkdirs() }
        val targetReference = File(dir, REFERENCE)
        val sourceMtime = reference.lastModified()

        copyAtomic(reference, targetReference)
        if (sourceMtime > 0L && !targetReference.setLastModified(sourceMtime)) {
            throw IllegalStateException("Không thể bảo toàn thời gian giọng mẫu để tái sử dụng cache.")
        }
        copyAtomic(
            File(reference.absolutePath + SPEAKER_CACHE),
            File(targetReference.absolutePath + SPEAKER_CACHE),
        )
        copyAtomic(
            File(reference.absolutePath + REFCODES_CACHE),
            File(targetReference.absolutePath + REFCODES_CACHE),
        )

        val existingCreated = readProfile(dir)?.createdAtMs
        val createdAt = existingCreated ?: System.currentTimeMillis()
        val meta = Properties().apply {
            setProperty("schema", "1")
            setProperty("id", id)
            setProperty("name", name)
            setProperty("created_at_ms", createdAt.toString())
            setProperty("source_last_modified_ms", sourceMtime.toString())
            setProperty("reference_sha256", id)
            setProperty("cache_version", "4")
        }
        writePropertiesAtomic(File(dir, META), meta)

        val profile = readProfile(dir)
            ?: throw IllegalStateException("Không đọc lại được hồ sơ giọng vừa lưu.")
        Diagnostics.log(
            "voice_profile",
            "voice_profile.saved",
            data = mapOf(
                "profile_id" to profile.id,
                "profile_name" to profile.name,
                "cache_ready" to profile.cacheReady,
                "reference" to Diagnostics.wavInfo(profile.referenceFile),
            ),
        )
        return profile
    }

    fun list(context: Context): List<VoiceProfile> =
        root(context).listFiles()
            ?.filter { it.isDirectory }
            ?.mapNotNull { readProfile(it) }
            ?.sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.name })
            .orEmpty()

    fun get(context: Context, id: String?): VoiceProfile? {
        if (id.isNullOrBlank()) return null
        return readProfile(File(root(context), id))
    }

    fun delete(context: Context, id: String): Boolean {
        val dir = File(root(context), id)
        if (!dir.isDirectory) return false
        val removed = dir.deleteRecursively()
        if (removed) {
            val settings = loadSettings(context)
            if (settings.profileId == id) {
                saveSettings(context, settings.copy(profileId = list(context).firstOrNull()?.id))
            }
            Diagnostics.log("voice_profile", "voice_profile.deleted", data = mapOf("profile_id" to id))
        }
        return removed
    }

    fun loadSettings(context: Context): SystemVoiceSettings {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val requested = prefs.getString("profile_id", null)
        val validProfileId = requested?.takeIf { get(context, it) != null }
            ?: list(context).firstOrNull()?.id
        return SystemVoiceSettings(
            profileId = validProfileId,
            rate = prefs.getFloat("rate", 1.0f).coerceIn(0.5f, 2.0f),
            pitch = prefs.getFloat("pitch", 1.0f).coerceIn(0.5f, 2.0f),
            volume = prefs.getFloat("volume", 1.0f).coerceIn(0.0f, 1.0f),
        )
    }

    fun saveSettings(context: Context, settings: SystemVoiceSettings) {
        val profileId = settings.profileId?.takeIf { get(context, it) != null }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString("profile_id", profileId)
            .putFloat("rate", settings.rate.coerceIn(0.5f, 2.0f))
            .putFloat("pitch", settings.pitch.coerceIn(0.5f, 2.0f))
            .putFloat("volume", settings.volume.coerceIn(0.0f, 1.0f))
            .apply()
        Diagnostics.log(
            "system_tts",
            "system_tts.settings_saved",
            data = mapOf(
                "profile_id" to profileId,
                "rate" to settings.rate,
                "pitch" to settings.pitch,
                "volume" to settings.volume,
            ),
        )
    }

    private fun readProfile(dir: File): VoiceProfile? = runCatching {
        val reference = File(dir, REFERENCE)
        val metaFile = File(dir, META)
        if (!reference.isFile || reference.length() <= 44L || !metaFile.isFile) return null
        val properties = Properties().apply {
            FileInputStream(metaFile).use { load(it) }
        }
        val id = properties.getProperty("id")?.trim().orEmpty()
        val name = properties.getProperty("name")?.trim().orEmpty()
        val createdAt = properties.getProperty("created_at_ms")?.toLongOrNull() ?: 0L
        if (id.isBlank() || name.isBlank()) return null
        VoiceProfile(
            id = id,
            name = name,
            referenceFile = reference,
            createdAtMs = createdAt,
            cacheReady = hasWarmV4Caches(reference),
        )
    }.getOrNull()

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered(1024 * 1024).use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    private fun copyAtomic(source: File, target: File) {
        require(source.isFile && source.length() > 0L) { "Thiếu dữ liệu cache: ${source.name}" }
        target.parentFile?.mkdirs()
        val temp = File(target.absolutePath + ".new")
        source.inputStream().buffered(1024 * 1024).use { input ->
            temp.outputStream().buffered(1024 * 1024).use { output -> input.copyTo(output) }
        }
        if (target.exists() && !target.delete()) {
            temp.delete()
            throw IllegalStateException("Không thể cập nhật ${target.name}.")
        }
        if (!temp.renameTo(target)) {
            temp.copyTo(target, overwrite = true)
            temp.delete()
        }
    }

    private fun writePropertiesAtomic(target: File, properties: Properties) {
        target.parentFile?.mkdirs()
        val temp = File(target.absolutePath + ".new")
        FileOutputStream(temp).use { properties.store(it, null) }
        if (target.exists() && !target.delete()) {
            temp.delete()
            throw IllegalStateException("Không thể cập nhật metadata giọng.")
        }
        if (!temp.renameTo(target)) {
            temp.copyTo(target, overwrite = true)
            temp.delete()
        }
    }
}
