package com.vieneu.voiceclone

object VieNeuNative {
    init {
        System.loadLibrary("vieneu_jni")
    }

    external fun initialize(modelDir: String, threads: Int): String
    external fun synthesize(text: String, referenceWav: String, style: String): FloatArray?
    external fun sampleRate(): Int
    external fun lastError(): String
    external fun release()
}
