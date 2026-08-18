# VieNeu Android native build

The Android build has two explicit phases.

1. `prepare_vieneu_android_build.py` materializes the pinned VieNeu checkout before CMake. CMake must never mutate source files during configure.
2. CMake consumes only the prepared checkout marked by `.vieneu-android-prepared.json` and builds the native libraries.

Production acoustic compute is canonical OpenCL F32. The former FP16/Q8 experiment followed by an F32 rollback is intentionally not part of the production materialization path.

Reference speaker caching is content-addressed (`SHA-256`) and namespaced by the pinned model manifest. The current native cache format is v3.

Synthesized waveform data remains native. JNI writes PCM16 WAV directly and returns only the output path to Kotlin; it must not allocate a Java `FloatArray` containing the full waveform.

The native engine is process-scoped through `VieNeuEngine`. `MainActivity` may observe whether the engine is warm, but it must not own `VieNeuNative.initialize()`/`release()` or release the engine from `onDestroy()`. A successful model download invalidates the process engine so the next synthesis reloads verified files.

`version.properties` is the single published Android version source. The final materialization step rewrites Gradle to read `VERSION_CODE` and `VERSION_NAME` from that file and rejects literal final app versions. Intermediate historical patch labels are migration anchors only and are not release metadata.

Documentation must point to authoritative files instead of copying mutable release identifiers: app version comes from `version.properties`, while model release/package/revision/hash come from `ModelManager.kt`.
