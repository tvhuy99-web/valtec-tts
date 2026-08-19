package com.vieneu.voiceclone

/**
 * JNI callback used only by the optional System TTS early-playback experiment.
 * A whole Android utterance remains one VieNeu synthesis request; native code
 * may expose stable decoded PCM prefixes while it continues generating frames.
 */
interface NativePcmStreamSink {
    fun onNativePcmChunk(audio: FloatArray, isFinal: Boolean): Boolean
}
