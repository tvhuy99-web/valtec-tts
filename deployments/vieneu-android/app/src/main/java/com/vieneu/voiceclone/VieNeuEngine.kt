package com.vieneu.voiceclone

import android.content.Context
import android.os.SystemClock

object VieNeuEngine {
    private val lock = Any()

    @Volatile
    private var ready = false

    @Volatile
    private var loadedModelPath: String? = null

    fun isReady(): Boolean = ready

    fun ensureInitialized(context: Context, generationId: String): Boolean {
        val appContext = context.applicationContext
        val modelDir = ModelManager.modelDir(appContext)
        val modelPath = modelDir.absolutePath

        synchronized(lock) {
            if (ready && loadedModelPath == modelPath) return false

            if (ready) {
                releaseLocked("model_path_changed")
            }

            val availableProcessors = Runtime.getRuntime().availableProcessors()
            val threads = availableProcessors.coerceIn(2, 6)
            val initSpan = Diagnostics.span(
                "engine",
                "engine.initialize",
                mapOf(
                    "generation_id" to generationId,
                    "engine_scope" to "process",
                    "threads" to threads,
                    "available_processors" to availableProcessors,
                    "model" to Diagnostics.modelSnapshot(modelDir)
                )
            )
            val initStart = SystemClock.elapsedRealtimeNanos()
            val error = VieNeuNative.initialize(modelPath, threads)
            val initMs = (SystemClock.elapsedRealtimeNanos() - initStart) / 1_000_000.0
            if (error.isNotEmpty()) {
                ready = false
                loadedModelPath = null
                initSpan.end(
                    false,
                    mapOf(
                        "generation_id" to generationId,
                        "engine_scope" to "process",
                        "wall_ms_direct" to initMs,
                        "error" to error
                    )
                )
                throw IllegalStateException(error)
            }

            loadedModelPath = modelPath
            ready = true
            initSpan.end(
                true,
                mapOf(
                    "generation_id" to generationId,
                    "engine_scope" to "process",
                    "wall_ms_direct" to initMs
                )
            )
            return true
        }
    }

    fun invalidate(reason: String) {
        synchronized(lock) {
            if (!ready) {
                loadedModelPath = null
                return
            }
            releaseLocked(reason)
        }
    }

    private fun releaseLocked(reason: String) {
        val span = Diagnostics.span(
            "engine",
            "engine.release",
            mapOf(
                "engine_scope" to "process",
                "reason" to reason,
                "model_path_present" to (loadedModelPath != null)
            )
        )
        runCatching { VieNeuNative.release() }
            .onSuccess {
                ready = false
                loadedModelPath = null
                span.end(true)
            }
            .onFailure {
                ready = false
                loadedModelPath = null
                Diagnostics.error(
                    "engine",
                    "engine.release.failure",
                    it,
                    mapOf("engine_scope" to "process", "reason" to reason)
                )
                span.end(false, mapOf("error" to (it.message ?: it.javaClass.simpleName)))
            }
    }
}
