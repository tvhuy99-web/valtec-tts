package com.vieneu.voiceclone

import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import kotlin.math.roundToInt

object WavWriter {
    fun writeMonoFloat(file: File, samples: FloatArray, sampleRate: Int) {
        file.parentFile?.mkdirs()
        val dataSize = samples.size * 2
        BufferedOutputStream(FileOutputStream(file)).use { out ->
            out.write("RIFF".toByteArray(Charsets.US_ASCII))
            writeLe32(out, 36 + dataSize)
            out.write("WAVE".toByteArray(Charsets.US_ASCII))
            out.write("fmt ".toByteArray(Charsets.US_ASCII))
            writeLe32(out, 16)
            writeLe16(out, 1)
            writeLe16(out, 1)
            writeLe32(out, sampleRate)
            writeLe32(out, sampleRate * 2)
            writeLe16(out, 2)
            writeLe16(out, 16)
            out.write("data".toByteArray(Charsets.US_ASCII))
            writeLe32(out, dataSize)
            for (sample in samples) {
                val pcm = (sample.coerceIn(-1f, 1f) * 32767f).roundToInt()
                writeLe16(out, pcm)
            }
        }
    }

    private fun writeLe16(out: BufferedOutputStream, value: Int) {
        out.write(value and 0xff)
        out.write((value ushr 8) and 0xff)
    }

    private fun writeLe32(out: BufferedOutputStream, value: Int) {
        out.write(value and 0xff)
        out.write((value ushr 8) and 0xff)
        out.write((value ushr 16) and 0xff)
        out.write((value ushr 24) and 0xff)
    }
}
