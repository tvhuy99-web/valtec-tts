package com.vieneu.voiceclone

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.os.Process
import android.os.StatFs
import android.os.SystemClock
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.atomic.AtomicLong
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.math.abs
import kotlin.math.sqrt

object Diagnostics {
    private const val TAG = "VieNeuDiag"
    private const val SCHEMA_VERSION = 2
    private const val KEEP_SESSIONS = 8
    private val lock = Any()
    private val sequence = AtomicLong(0L)

    @Volatile private var initialized = false
    private lateinit var appContext: Context
    private lateinit var rootDir: File
    private lateinit var sessionDir: File
    private lateinit var appLog: File
    private var sessionStartWallMs = 0L
    private var sessionStartElapsedNs = 0L

    lateinit var sessionId: String
        private set

    fun start(context: Context) {
        synchronized(lock) {
            if (initialized) return
            appContext = context.applicationContext
            rootDir = File(appContext.filesDir, "diagnostics").apply { mkdirs() }
            sessionId = sessionName()
            sessionDir = File(rootDir, sessionId).apply { mkdirs() }
            appLog = File(sessionDir, "app.jsonl")
            sessionStartWallMs = System.currentTimeMillis()
            sessionStartElapsedNs = SystemClock.elapsedRealtimeNanos()
            initialized = true
            pruneOldSessionsLocked()
        }
        log(
            category = "session",
            event = "session.start",
            data = deviceInfo(),
            snapshot = true
        )
    }

    fun currentSessionDir(): File {
        ensureStarted()
        return sessionDir
    }

    fun currentLogPath(): String {
        ensureStarted()
        return appLog.absolutePath
    }

    fun log(
        category: String,
        event: String,
        level: String = "INFO",
        data: Map<String, Any?> = emptyMap(),
        snapshot: Boolean = false
    ) {
        if (!initialized) return
        try {
            val obj = JSONObject()
            obj.put("schema", SCHEMA_VERSION)
            obj.put("seq", sequence.incrementAndGet())
            obj.put("ts_epoch_ms", System.currentTimeMillis())
            obj.put("session_elapsed_ms", (SystemClock.elapsedRealtimeNanos() - sessionStartElapsedNs) / 1_000_000.0)
            obj.put("session_id", sessionId)
            obj.put("pid", Process.myPid())
            obj.put("tid", Process.myTid())
            obj.put("thread", Thread.currentThread().name)
            obj.put("level", level)
            obj.put("category", category)
            obj.put("event", event)
            obj.put("data", toJsonObject(data))
            if (snapshot) obj.put("runtime", toJsonObject(runtimeSnapshot()))
            synchronized(lock) {
                appLog.appendText(obj.toString() + "\n", Charsets.UTF_8)
            }
            when (level) {
                "ERROR" -> Log.e(TAG, "$category/$event ${data["error"] ?: ""}")
                "WARN" -> Log.w(TAG, "$category/$event")
                else -> Log.d(TAG, "$category/$event")
            }
        } catch (t: Throwable) {
            Log.e(TAG, "Unable to write diagnostics", t)
        }
    }

    fun error(category: String, event: String, throwable: Throwable, data: Map<String, Any?> = emptyMap()) {
        val merged = LinkedHashMap<String, Any?>()
        merged.putAll(data)
        merged["error_class"] = throwable.javaClass.name
        merged["error"] = throwable.message ?: throwable.javaClass.simpleName
        merged["stack"] = throwable.stackTraceToString()
        log(category, event, "ERROR", merged, snapshot = true)
    }

    fun span(category: String, event: String, data: Map<String, Any?> = emptyMap()): Span {
        ensureStarted()
        return Span(category, event, data)
    }

    inner class Span internal constructor(
        private val category: String,
        private val event: String,
        private val startData: Map<String, Any?>
    ) {
        private val startNs = SystemClock.elapsedRealtimeNanos()
        private val startCpuMs = Process.getElapsedCpuTime()
        private val startPssKb = runCatching { Debug.getPss() }.getOrDefault(-1)
        private val startNativeHeap = Debug.getNativeHeapAllocatedSize()
        private var ended = false

        init {
            log(category, "$event.begin", data = startData, snapshot = true)
        }

        fun end(success: Boolean = true, data: Map<String, Any?> = emptyMap()) {
            if (ended) return
            ended = true
            val endNs = SystemClock.elapsedRealtimeNanos()
            val endCpuMs = Process.getElapsedCpuTime()
            val endPssKb = runCatching { Debug.getPss() }.getOrDefault(-1)
            val endNativeHeap = Debug.getNativeHeapAllocatedSize()
            val merged = LinkedHashMap<String, Any?>()
            merged.putAll(data)
            merged["success"] = success
            merged["wall_ms"] = (endNs - startNs) / 1_000_000.0
            merged["process_cpu_ms"] = endCpuMs - startCpuMs
            if (startPssKb >= 0 && endPssKb >= 0) merged["pss_delta_kb"] = endPssKb - startPssKb
            merged["native_heap_delta_bytes"] = endNativeHeap - startNativeHeap
            log(category, "$event.end", if (success) "INFO" else "ERROR", merged, snapshot = true)
        }
    }

    fun modelSnapshot(root: File): Map<String, Any?> {
        val files = ModelManager.requiredFiles.map { relative ->
            val f = File(root, relative)
            mapOf(
                "path" to relative,
                "exists" to f.isFile,
                "bytes" to if (f.isFile) f.length() else 0L,
                "modified_ms" to if (f.isFile) f.lastModified() else 0L
            )
        }
        val stat = runCatching { StatFs(root.absolutePath) }.getOrNull()
        return mapOf(
            "root" to root.absolutePath,
            "ready" to ModelManager.isReady(root),
            "required_file_count" to ModelManager.requiredFiles.size,
            "present_file_count" to files.count { it["exists"] == true },
            "total_present_bytes" to files.sumOf { (it["bytes"] as? Long) ?: 0L },
            "free_bytes" to stat?.availableBytes,
            "files" to files
        )
    }

    fun wavInfo(file: File): Map<String, Any?> {
        val result = LinkedHashMap<String, Any?>()
        result["path"] = file.absolutePath
        result["bytes"] = file.length()
        if (!file.isFile || file.length() < 12L) {
            result["valid_riff_wave"] = false
            return result
        }
        try {
            RandomAccessFile(file, "r").use { raf ->
                val riff = ByteArray(4).also { raf.readFully(it) }
                raf.skipBytes(4)
                val wave = ByteArray(4).also { raf.readFully(it) }
                val valid = String(riff, Charsets.US_ASCII) == "RIFF" && String(wave, Charsets.US_ASCII) == "WAVE"
                result["valid_riff_wave"] = valid
                if (!valid) return result

                var format = -1
                var channels = -1
                var sampleRate = -1
                var bits = -1
                var dataBytes = 0L
                while (raf.filePointer + 8L <= raf.length()) {
                    val idBytes = ByteArray(4).also { raf.readFully(it) }
                    val chunk = String(idBytes, Charsets.US_ASCII)
                    val size = readLe32(raf).toLong() and 0xffffffffL
                    val payload = raf.filePointer
                    if (payload + size > raf.length()) break
                    when (chunk) {
                        "fmt " -> if (size >= 16L) {
                            format = readLe16(raf)
                            channels = readLe16(raf)
                            sampleRate = readLe32(raf)
                            raf.skipBytes(6)
                            bits = readLe16(raf)
                        }
                        "data" -> dataBytes += size
                    }
                    raf.seek(payload + size + (size and 1L))
                }
                result["audio_format"] = format
                result["channels"] = channels
                result["sample_rate_hz"] = sampleRate
                result["bits_per_sample"] = bits
                result["data_bytes"] = dataBytes
                if (sampleRate > 0 && channels > 0 && bits > 0) {
                    val bytesPerSecond = sampleRate.toDouble() * channels.toDouble() * bits.toDouble() / 8.0
                    result["duration_ms"] = if (bytesPerSecond > 0.0) dataBytes * 1000.0 / bytesPerSecond else null
                }
            }
        } catch (t: Throwable) {
            result["probe_error"] = t.message
        }
        return result
    }

    fun audioStats(audio: FloatArray, sampleRate: Int): Map<String, Any?> {
        if (audio.isEmpty() || sampleRate <= 0) return mapOf("samples" to audio.size, "sample_rate_hz" to sampleRate)
        var sumSquares = 0.0
        var sumAbs = 0.0
        var peak = 0.0
        var clipped = 0L
        for (sample in audio) {
            val v = sample.toDouble()
            val a = abs(v)
            if (a > peak) peak = a
            if (a >= 0.999) clipped++
            sumSquares += v * v
            sumAbs += a
        }
        return mapOf(
            "samples" to audio.size,
            "sample_rate_hz" to sampleRate,
            "duration_ms" to audio.size * 1000.0 / sampleRate.toDouble(),
            "peak" to peak,
            "rms" to sqrt(sumSquares / audio.size.toDouble()),
            "mean_abs" to sumAbs / audio.size.toDouble(),
            "clipped_samples" to clipped,
            "clipped_pct" to clipped * 100.0 / audio.size.toDouble()
        )
    }

    fun createBundle(modelRoot: File, reference: File?, output: File?): File {
        ensureStarted()
        val span = span("diagnostics", "bundle.create")
        val out = File(appContext.cacheDir, "vieneu-diagnostics-${timestampForFile()}.zip")
        try {
            ZipOutputStream(out.outputStream().buffered()).use { zip ->
                val manifest = JSONObject()
                manifest.put("schema", SCHEMA_VERSION)
                manifest.put("created_epoch_ms", System.currentTimeMillis())
                manifest.put("active_session", sessionId)
                manifest.put("device", toJsonObject(deviceInfo()))
                manifest.put("runtime", toJsonObject(runtimeSnapshot()))
                manifest.put("model", toJsonObject(modelSnapshot(modelRoot)))
                manifest.put("reference", reference?.let { toJsonObject(wavInfo(it)) } ?: JSONObject.NULL)
                manifest.put("output", output?.let { toJsonObject(wavInfo(it)) } ?: JSONObject.NULL)
                addBytes(zip, "manifest.json", manifest.toString(2).toByteArray(Charsets.UTF_8))

                rootDir.listFiles()?.sortedBy { it.name }?.forEach { session ->
                    if (session.isDirectory) addDirectory(zip, session, "sessions/${session.name}")
                }
            }
            span.end(true, mapOf("bundle" to out.absolutePath, "bytes" to out.length()))
            return out
        } catch (t: Throwable) {
            span.end(false, mapOf("error" to (t.message ?: t.javaClass.simpleName)))
            throw t
        }
    }

    fun clearOldSessions(): Int {
        ensureStarted()
        var deleted = 0
        synchronized(lock) {
            rootDir.listFiles()?.forEach { f ->
                if (f.isDirectory && f.absolutePath != sessionDir.absolutePath && f.deleteRecursively()) deleted++
            }
        }
        log("diagnostics", "sessions.cleared", data = mapOf("deleted" to deleted))
        return deleted
    }

    private fun addDirectory(zip: ZipOutputStream, dir: File, prefix: String) {
        dir.listFiles()?.sortedBy { it.name }?.forEach { f ->
            val name = "$prefix/${f.name}"
            if (f.isDirectory) addDirectory(zip, f, name) else {
                zip.putNextEntry(ZipEntry(name))
                f.inputStream().buffered().use { it.copyTo(zip, 256 * 1024) }
                zip.closeEntry()
            }
        }
    }

    private fun addBytes(zip: ZipOutputStream, name: String, bytes: ByteArray) {
        zip.putNextEntry(ZipEntry(name))
        zip.write(bytes)
        zip.closeEntry()
    }

    private fun deviceInfo(): Map<String, Any?> {
        val am = appContext.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memInfo = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        val stat = StatFs(appContext.filesDir.absolutePath)
        val cpuFreqs = (0 until Runtime.getRuntime().availableProcessors()).mapNotNull { cpu ->
            val value = readText("/sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_cur_freq")?.toLongOrNull()
            value?.let { mapOf("cpu" to cpu, "khz" to it) }
        }
        return linkedMapOf(
            "manufacturer" to Build.MANUFACTURER,
            "brand" to Build.BRAND,
            "model" to Build.MODEL,
            "device" to Build.DEVICE,
            "hardware" to Build.HARDWARE,
            "soc_model" to if (Build.VERSION.SDK_INT >= 31) Build.SOC_MODEL else null,
            "sdk_int" to Build.VERSION.SDK_INT,
            "release" to Build.VERSION.RELEASE,
            "abis" to Build.SUPPORTED_ABIS.toList(),
            "available_processors" to Runtime.getRuntime().availableProcessors(),
            "memory_class_mb" to am.memoryClass,
            "large_memory_class_mb" to am.largeMemoryClass,
            "device_total_ram_bytes" to memInfo.totalMem,
            "device_available_ram_bytes" to memInfo.availMem,
            "low_memory" to memInfo.lowMemory,
            "app_storage_free_bytes" to stat.availableBytes,
            "app_storage_total_bytes" to stat.totalBytes,
            "cpu_frequencies" to cpuFreqs,
            "battery" to batterySnapshot(),
            "thermal" to thermalSnapshot()
        )
    }

    private fun runtimeSnapshot(): Map<String, Any?> {
        val runtime = Runtime.getRuntime()
        val usedJava = runtime.totalMemory() - runtime.freeMemory()
        return linkedMapOf(
            "process_cpu_ms" to Process.getElapsedCpuTime(),
            "pss_kb" to runCatching { Debug.getPss() }.getOrNull(),
            "native_heap_allocated_bytes" to Debug.getNativeHeapAllocatedSize(),
            "native_heap_size_bytes" to Debug.getNativeHeapSize(),
            "java_heap_used_bytes" to usedJava,
            "java_heap_total_bytes" to runtime.totalMemory(),
            "java_heap_max_bytes" to runtime.maxMemory(),
            "proc_vm_rss_bytes" to procVmRssBytes(),
            "free_storage_bytes" to runCatching { StatFs(appContext.filesDir.absolutePath).availableBytes }.getOrNull(),
            "battery" to batterySnapshot(),
            "thermal" to thermalSnapshot()
        )
    }

    private fun batterySnapshot(): Map<String, Any?> {
        val intent = runCatching {
            appContext.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        }.getOrNull()
        if (intent == null) return emptyMap()
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        return mapOf(
            "level_pct" to if (level >= 0 && scale > 0) level * 100.0 / scale else null,
            "temperature_c" to intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1).takeIf { it >= 0 }?.div(10.0),
            "voltage_mv" to intent.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1).takeIf { it >= 0 },
            "status" to intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1),
            "plugged" to intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1)
        )
    }

    private fun thermalSnapshot(): Map<String, Any?> {
        if (Build.VERSION.SDK_INT < 29) return emptyMap()
        val pm = appContext.getSystemService(Context.POWER_SERVICE) as PowerManager
        val result = LinkedHashMap<String, Any?>()
        result["status"] = runCatching { pm.currentThermalStatus }.getOrNull()
        if (Build.VERSION.SDK_INT >= 30) {
            result["headroom_10s"] = runCatching { pm.getThermalHeadroom(10) }.getOrNull()
        }
        return result
    }

    private fun procVmRssBytes(): Long? {
        val line = runCatching { File("/proc/self/status").useLines { lines -> lines.firstOrNull { it.startsWith("VmRSS:") } } }.getOrNull()
        return line?.trim()?.split(Regex("\\s+"))?.getOrNull(1)?.toLongOrNull()?.times(1024L)
    }

    private fun readText(path: String): String? = runCatching { File(path).readText().trim() }.getOrNull()

    private fun toJsonObject(map: Map<String, Any?>): JSONObject {
        val obj = JSONObject()
        map.forEach { (k, v) -> obj.put(k, toJsonValue(v)) }
        return obj
    }

    private fun toJsonValue(value: Any?): Any? = when (value) {
        null -> JSONObject.NULL
        is JSONObject, is JSONArray, is String, is Number, is Boolean -> value
        is Map<*, *> -> {
            val obj = JSONObject()
            value.forEach { (k, v) -> obj.put(k?.toString() ?: "null", toJsonValue(v)) }
            obj
        }
        is Iterable<*> -> JSONArray().also { arr -> value.forEach { arr.put(toJsonValue(it)) } }
        is Array<*> -> JSONArray().also { arr -> value.forEach { arr.put(toJsonValue(it)) } }
        else -> value.toString()
    }

    private fun readLe16(raf: RandomAccessFile): Int {
        val b0 = raf.readUnsignedByte()
        val b1 = raf.readUnsignedByte()
        return b0 or (b1 shl 8)
    }

    private fun readLe32(raf: RandomAccessFile): Int {
        val b0 = raf.readUnsignedByte()
        val b1 = raf.readUnsignedByte()
        val b2 = raf.readUnsignedByte()
        val b3 = raf.readUnsignedByte()
        return b0 or (b1 shl 8) or (b2 shl 16) or (b3 shl 24)
    }

    private fun pruneOldSessionsLocked() {
        val dirs = rootDir.listFiles()?.filter { it.isDirectory }?.sortedByDescending { it.lastModified() } ?: return
        dirs.drop(KEEP_SESSIONS).forEach { if (it.absolutePath != sessionDir.absolutePath) it.deleteRecursively() }
    }

    private fun sessionName(): String = "session-${timestampForFile()}-${Process.myPid()}"

    private fun timestampForFile(): String {
        val format = SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US)
        format.timeZone = TimeZone.getDefault()
        return format.format(Date())
    }

    private fun ensureStarted() {
        check(initialized) { "Diagnostics.start(context) must be called first." }
    }
}
