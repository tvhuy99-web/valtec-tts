# VieNeu Voice Clone for Android

Experimental Android ARM64 application for VieNeu-TTS v3 Turbo using the native VieNeu-TTS.cpp runtime, llama.cpp and ONNX Runtime.

The APK intentionally does not bundle the ~660 MB model package. On first use, tap **Tải mô hình offline**. The authoritative model release URL, package identity, source revision and manifest SHA-256 are defined in `app/src/main/java/com/vieneu/voiceclone/ModelManager.kt`; this README intentionally does not duplicate those values. After the package is verified, synthesis runs fully offline.

The authoritative Android app version is `version.properties`. `app/build.gradle.kts` is finalized to read `VERSION_CODE` and `VERSION_NAME` from that file, and build patches must not introduce a second published-version source.

Current flow:

1. Download or repair verified model files.
2. Select a RIFF/WAVE reference recording or use a bundled preset voice.
3. Enter Vietnamese text and choose style, pronunciation and generation mode.
4. Keep the native engine process-scoped after its first initialization so Activity recreation does not reload the model.
5. Synthesize locally at 48 kHz with canonical OpenCL F32 acoustic compute.
6. JNI writes PCM16 WAV directly and returns only the output path to Kotlin.
7. Play or export the generated WAV.

Only `arm64-v8a` is built. Minimum Android version is API 28. This OpenCL quality build requires a compatible device OpenCL runtime; there is no CPU acoustic fallback in this build.

For the exact native materialization and runtime invariants, see `native/BUILD_ARCHITECTURE.md`.
