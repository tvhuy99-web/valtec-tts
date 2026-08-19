package com.vieneu.voiceclone

import java.io.File
import java.io.RandomAccessFile

data class Pcm16Audio(
    val sampleRate: Int,
    val channels: Int,
    val samples: ShortArray,
)

object WavPcmReader {
    fun read(file: File): Pcm16Audio {
        require(file.isFile && file.length() >= 44L) { "WAV đầu ra không tồn tại hoặc quá ngắn." }
        RandomAccessFile(file, "r").use { input ->
            require(readAscii(input, 4) == "RIFF") { "WAV thiếu RIFF header." }
            readLe32(input)
            require(readAscii(input, 4) == "WAVE") { "WAV thiếu WAVE header." }

            var audioFormat = -1
            var channels = -1
            var sampleRate = -1
            var bitsPerSample = -1
            var dataOffset = -1L
            var dataSize = -1L

            while (input.filePointer + 8L <= input.length()) {
                val id = readAscii(input, 4)
                val size = readLe32(input).toLong() and 0xffffffffL
                val payload = input.filePointer
                val padded = size + (size and 1L)
                require(payload + padded <= input.length()) { "WAV chunk $id vượt quá kích thước tệp." }

                when (id) {
                    "fmt " -> {
                        require(size >= 16L) { "WAV fmt chunk không hợp lệ." }
                        audioFormat = readLe16(input)
                        channels = readLe16(input)
                        sampleRate = readLe32(input)
                        readLe32(input)
                        readLe16(input)
                        bitsPerSample = readLe16(input)
                    }
                    "data" -> {
                        dataOffset = payload
                        dataSize = size
                    }
                }
                input.seek(payload + padded)
                if (audioFormat > 0 && dataOffset >= 0L) break
            }

            require(audioFormat == 1) { "Chỉ hỗ trợ WAV PCM." }
            require(channels == 1) { "VieNeu System TTS yêu cầu WAV mono." }
            require(bitsPerSample == 16) { "VieNeu System TTS yêu cầu PCM16." }
            require(sampleRate > 0) { "Sample rate WAV không hợp lệ." }
            require(dataOffset >= 0L && dataSize > 0L && dataSize % 2L == 0L) { "WAV data chunk không hợp lệ." }
            require(dataSize / 2L <= Int.MAX_VALUE) { "WAV quá lớn." }

            input.seek(dataOffset)
            val count = (dataSize / 2L).toInt()
            val samples = ShortArray(count)
            for (i in 0 until count) {
                samples[i] = readLe16(input).toShort()
            }
            return Pcm16Audio(sampleRate, channels, samples)
        }
    }

    fun toLittleEndianBytes(samples: ShortArray): ByteArray {
        val bytes = ByteArray(samples.size * 2)
        for (i in samples.indices) {
            val value = samples[i].toInt()
            bytes[i * 2] = (value and 0xff).toByte()
            bytes[i * 2 + 1] = ((value ushr 8) and 0xff).toByte()
        }
        return bytes
    }

    private fun readAscii(input: RandomAccessFile, count: Int): String {
        val bytes = ByteArray(count)
        input.readFully(bytes)
        return String(bytes, Charsets.US_ASCII)
    }

    private fun readLe16(input: RandomAccessFile): Int {
        val b0 = input.readUnsignedByte()
        val b1 = input.readUnsignedByte()
        return b0 or (b1 shl 8)
    }

    private fun readLe32(input: RandomAccessFile): Int {
        val b0 = input.readUnsignedByte()
        val b1 = input.readUnsignedByte()
        val b2 = input.readUnsignedByte()
        val b3 = input.readUnsignedByte()
        return b0 or (b1 shl 8) or (b2 shl 16) or (b3 shl 24)
    }
}
