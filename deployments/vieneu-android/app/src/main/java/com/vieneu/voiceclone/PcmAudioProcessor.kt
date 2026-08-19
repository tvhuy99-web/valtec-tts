package com.vieneu.voiceclone

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

object PcmAudioProcessor {
    fun process(
        input: Pcm16Audio,
        rate: Float,
        pitch: Float,
        volume: Float,
    ): Pcm16Audio {
        val safeRate = rate.coerceIn(0.5f, 2.0f)
        val safePitch = pitch.coerceIn(0.5f, 2.0f)
        val safeVolume = volume.coerceIn(0.0f, 1.0f)

        if (abs(safeRate - 1.0f) < 0.0001f &&
            abs(safePitch - 1.0f) < 0.0001f &&
            abs(safeVolume - 1.0f) < 0.0001f
        ) {
            return input
        }

        var signal = FloatArray(input.samples.size) { index -> input.samples[index] / 32768.0f }

        if (abs(safePitch - 1.0f) >= 0.0001f) {
            signal = resampleSinc(signal, safePitch.toDouble())
        }

        val stretch = safePitch / safeRate
        if (abs(stretch - 1.0f) >= 0.0001f) {
            signal = timeStretchWsola(signal, stretch.toDouble(), input.sampleRate)
        }

        val output = ShortArray(signal.size)
        for (i in signal.indices) {
            val scaled = (signal[i] * safeVolume).coerceIn(-1.0f, 1.0f)
            output[i] = (scaled * 32767.0f).roundToInt().coerceIn(-32767, 32767).toShort()
        }
        return Pcm16Audio(input.sampleRate, input.channels, output)
    }

    private fun resampleSinc(input: FloatArray, pitchFactor: Double): FloatArray {
        if (input.isEmpty() || pitchFactor <= 0.0) return input
        val targetLength = max(1, ceil(input.size / pitchFactor).toInt())
        val output = FloatArray(targetLength)
        val radius = 12
        val cutoff = min(1.0, 1.0 / pitchFactor)

        for (outIndex in 0 until targetLength) {
            val sourcePosition = outIndex * pitchFactor
            val center = sourcePosition.toInt()
            var weighted = 0.0
            var weightSum = 0.0
            val start = max(0, center - radius + 1)
            val end = min(input.lastIndex, center + radius)
            for (sampleIndex in start..end) {
                val distance = sourcePosition - sampleIndex
                val normalized = distance / radius
                if (abs(normalized) >= 1.0) continue
                val x = PI * distance * cutoff
                val sinc = if (abs(x) < 1.0e-9) 1.0 else sin(x) / x
                val window = 0.5 + 0.5 * cos(PI * normalized)
                val weight = sinc * window * cutoff
                weighted += input[sampleIndex] * weight
                weightSum += weight
            }
            output[outIndex] = if (abs(weightSum) > 1.0e-9) (weighted / weightSum).toFloat() else 0.0f
        }
        return output
    }

    private fun timeStretchWsola(input: FloatArray, stretch: Double, sampleRate: Int): FloatArray {
        if (input.isEmpty() || stretch <= 0.0 || abs(stretch - 1.0) < 0.0001) return input

        val frameSize = max(256, (sampleRate * 0.040).roundToInt()).let { if (it % 2 == 0) it else it + 1 }
        if (input.size < frameSize * 2) {
            return resizeLinear(input, max(1, (input.size * stretch).roundToInt()))
        }

        val overlap = frameSize / 2
        val synthesisHop = frameSize - overlap
        val analysisHop = synthesisHop / stretch
        val searchRadius = max(16, (sampleRate * 0.006).roundToInt())
        val targetLength = max(1, (input.size * stretch).roundToInt())
        val output = FloatArray(targetLength + frameSize)

        val firstCount = min(frameSize, input.size)
        input.copyInto(output, 0, 0, firstCount)
        var lastWritten = firstCount
        var outputPosition = synthesisHop
        var expectedInput = analysisHop

        while (outputPosition < targetLength && expectedInput < input.size - overlap) {
            val expected = expectedInput.roundToInt()
            val minCandidate = max(0, expected - searchRadius)
            val maxCandidate = min(input.size - overlap - 1, expected + searchRadius)
            if (minCandidate > maxCandidate) break

            var bestCandidate = minCandidate
            var bestScore = Double.NEGATIVE_INFINITY
            var candidate = minCandidate
            while (candidate <= maxCandidate) {
                var dot = 0.0
                var leftEnergy = 0.0
                var rightEnergy = 0.0
                for (i in 0 until overlap) {
                    val left = output[outputPosition + i].toDouble()
                    val right = input[candidate + i].toDouble()
                    dot += left * right
                    leftEnergy += left * left
                    rightEnergy += right * right
                }
                val score = if (leftEnergy > 1.0e-12 && rightEnergy > 1.0e-12) {
                    dot / kotlin.math.sqrt(leftEnergy * rightEnergy)
                } else {
                    -1.0
                }
                if (score > bestScore) {
                    bestScore = score
                    bestCandidate = candidate
                }
                candidate += 4
            }

            val available = min(frameSize, input.size - bestCandidate)
            val overlapCount = min(overlap, available)
            for (i in 0 until overlapCount) {
                val mix = i.toFloat() / max(1, overlapCount - 1).toFloat()
                val old = output[outputPosition + i]
                val fresh = input[bestCandidate + i]
                output[outputPosition + i] = old * (1.0f - mix) + fresh * mix
            }
            if (available > overlapCount) {
                input.copyInto(
                    output,
                    outputPosition + overlapCount,
                    bestCandidate + overlapCount,
                    bestCandidate + available,
                )
            }
            lastWritten = max(lastWritten, outputPosition + available)
            outputPosition += synthesisHop
            expectedInput += analysisHop
        }

        val useful = min(targetLength, lastWritten)
        if (useful <= 0) return input
        val trimmed = output.copyOf(useful)
        return if (useful == targetLength) trimmed else resizeLinear(trimmed, targetLength)
    }

    private fun resizeLinear(input: FloatArray, targetLength: Int): FloatArray {
        if (targetLength <= 0 || input.isEmpty()) return FloatArray(0)
        if (targetLength == input.size) return input
        if (targetLength == 1) return floatArrayOf(input.first())
        val output = FloatArray(targetLength)
        val scale = (input.size - 1).toDouble() / (targetLength - 1).toDouble()
        for (i in output.indices) {
            val position = i * scale
            val left = position.toInt().coerceIn(0, input.lastIndex)
            val right = min(input.lastIndex, left + 1)
            val fraction = (position - left).toFloat()
            output[i] = input[left] * (1.0f - fraction) + input[right] * fraction
        }
        return output
    }
}
