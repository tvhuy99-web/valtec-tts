# VieNeu Android native build

The Android build has two explicit phases.

1. `prepare_vieneu_android_build.py` materializes the pinned VieNeu checkout before CMake. CMake must never mutate source files during configure.
2. CMake consumes only the prepared checkout marked by `.vieneu-android-prepared.json` and builds the native libraries.

Production acoustic compute is canonical OpenCL F32. The former FP16/Q8 experiment followed by an F32 rollback is intentionally not part of the production materialization path.

Reference speaker caching is content-addressed (`SHA-256`) and namespaced by the pinned model manifest. The current native cache format is v3.

Synthesized waveform data remains native. JNI writes PCM16 WAV directly and returns only the output path to Kotlin; it must not allocate a Java `FloatArray` containing the full waveform.
