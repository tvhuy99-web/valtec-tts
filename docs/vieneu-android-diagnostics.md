# VieNeu Android diagnostics

This diagnostics mode is designed to explain the complete latency path from Android app startup to generated WAV playback/export without relying on a single coarse stopwatch.

## Session bundle

The app creates one session directory per process session and keeps the most recent sessions. `Xuất nhật ký chẩn đoán ZIP` creates a bundle containing:

- `manifest.json`: device/runtime snapshot, model-file manifest, reference WAV metadata, output WAV metadata.
- `sessions/<session>/app.jsonl`: Android/Kotlin lifecycle, model/network/I/O/generation events.
- `sessions/<session>/native-events.jsonl`: JNI/native begin/end/progress events with CPU and RSS measurements.
- `sessions/<session>/native-stdout.log`: VieNeu benchmark output and deep `[V3NativeDiag]` stage timings.
- `sessions/<session>/native-stderr.log`: VieNeu warnings and errors written to stderr.

The JSONL format is one complete JSON object per line so a partially written/crashed session can still be inspected up to the last complete event.

## Correlation

`session_id` correlates all files from one app session. Each generation also receives a `generation_id` in `app.jsonl`, allowing multiple TTS runs in one session to be compared without mixing them.

Timestamps use epoch milliseconds plus monotonic elapsed time. Performance comparisons should use monotonic/wall durations rather than subtracting wall-clock timestamps.

## App-level stages

Important `app.jsonl` categories/events include:

- `session/session.start`: device identity, Android version, ABI, SoC when exposed, CPU count, memory class, total/available RAM, storage, CPU frequencies, battery and thermal state.
- `app/activity.*`: Android lifecycle boundaries.
- `model/status.refresh`: complete required-model-file presence/size snapshot.
- `model/download.*`: preflight, per-file begin/end, resume behavior, HTTP response, transfer progress, average throughput and failure detail.
- `network/http.*`: redirect chain, host, response code and connection/header latency without logging signed query strings.
- `reference/reference.import`: content-provider import cost and copied byte count.
- `reference/reference.probe`: RIFF/WAVE format, channels, sample rate, bit depth, data bytes and duration.
- `engine/engine.initialize`: Android-side cold engine initialization wall time, process CPU time, PSS/native-heap delta and runtime snapshots.
- `generation/native.synthesize`: JNI/native synthesis wall time and output metrics.
- `generation/generation.total`: complete user-flow latency including initialization when needed, synthesis and WAV write.
- `io/wav.write`: generated WAV serialization cost.
- `playback/wav.play`: MediaPlayer prepare/start/completion/error.
- `io/wav.export`: Storage Access Framework export cost.
- `diagnostics/bundle.*`: log bundle creation/export.

## Runtime snapshots

Major span begin/end events record:

- `process_cpu_ms`: process CPU time, not wall time.
- `pss_kb`: proportional set size as reported by Android.
- `proc_vm_rss_bytes`: process resident memory from `/proc/self/status` when readable.
- `native_heap_allocated_bytes` / `native_heap_size_bytes`.
- Java heap used/total/max.
- CPU current/min/max frequency and governor when sysfs exposes it.
- battery level, temperature, voltage, charging state.
- Android thermal status and thermal headroom when the OS exposes them.
- free application storage.

Comparing a slow run with a fast run should first check thermal state and CPU frequencies before attributing a regression to the TTS algorithm.

## Native pipeline events

`native-events.jsonl` records:

- `native.diagnostics.configured`.
- `engine.initialize.begin/end`: native wall time, process CPU time, RSS delta, thread count and sample rate.
- `synthesis.begin/end`: text/reference/style and the effective default native inference parameters.
- `synthesis.progress`: VieNeu's own progress callback.
- `native.error`.
- `engine.release.begin/end`.

VieNeu progress stages currently include:

1. `prepare`
2. `chunk`
3. `prefill`
4. `generate_frames`
5. `decode_audio`
6. `complete`

`generate_frames` is sampled every 10 frames plus first/final frame. Logging every frame would perturb the workload being measured and make benchmark results less trustworthy.

## Deep VieNeu timing

The CI build pins VieNeu-TTS.cpp to commit `838979faf0354ffb3dff898e30b709f644fa7db4` and instruments that exact source before compilation. `native-stdout.log` contains `[V3NativeDiag]` entries for:

### Cold initialization

- `init.assets_load`
- `init.tokenizer_load`
- `init.objects_create`
- `init.acoustic_initialize`
- `init.backbone_initialize`
- `init.codec_initialize`
- `init.speaker_encoder_initialize`
- `init.denoiser_initialize`
- `init.voices_parse`
- `init.total`

### Reference voice enrollment

- `reference.read_wav`
- `reference.trim_8s`
- `reference.denoise`
- `reference.speaker_embedding`
- `reference.resample_48k`
- `reference.codec_encode`
- `reference.total`

### Prompt and synthesis

- `prompt.build_and_embed`
- VieNeu built-in `Prefill (backbone)`
- VieNeu built-in `Acoustic generation` plus average milliseconds per frame
- VieNeu built-in `Backbone decode` plus average milliseconds per step
- VieNeu built-in `MOSS codec decode`
- acoustic backend's own benchmark statistics when emitted by VieNeu

This separates reference cloning overhead from autoregressive generation and final codec decoding.

## Output-quality counters

The app records generated float-audio:

- sample count and sample rate
- duration
- peak absolute amplitude
- RMS
- mean absolute amplitude
- clipped sample count and percentage

These are not perceptual-quality scores. They are intended to catch obvious level/clipping changes when performance settings are modified.

## Real-time factor (RTF)

`RTF = synthesis wall time / generated audio duration`.

- RTF below `1.0`: synthesis is faster than real time.
- RTF `1.0`: 10 seconds of audio requires roughly 10 seconds to synthesize.
- RTF above `1.0`: synthesis is slower than real time.

Use RTF for comparing different output lengths; use individual stage timings to decide what to optimize.

## Recommended comparison protocol

For a performance change, keep the following fixed: phone, reference WAV, text, style, model files and thread count.

Measure cold and warm behavior separately:

- Cold run: app/process starts and engine must initialize.
- Warm runs: engine is already loaded; repeat the same generation several times.

For warm performance, compare the median of multiple runs rather than one run. Also compare thermal status, CPU frequency, PSS/RSS and stage-level timings. A change should not be called faster solely because one end-to-end run happened to finish sooner.

## Privacy

Detailed diagnostics intentionally capture the synthesis text, local model/reference/output paths, device model and performance telemetry. The app warns about this before sharing. The ZIP should only be shared when that diagnostic content is acceptable.
