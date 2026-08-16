# VieNeu Voice Clone for Android

Experimental Android ARM64 application for VieNeu-TTS v3 Turbo using the native VieNeu-TTS.cpp runtime, llama.cpp and ONNX Runtime.

The APK intentionally does not bundle the ~660 MB model package. On first use, tap **Tải mô hình offline**. The app downloads the required files from `lastudio-community/VieNeu-TTS-v3-Turbo-CPP` into app-specific external storage and then runs synthesis fully offline.

Current POC flow:

1. Download/repair model files.
2. Select a RIFF/WAVE reference recording.
3. Enter Vietnamese text.
4. Choose `tu_nhien`, `tin_tuc`, or `doc_truyen` style.
5. Synthesize locally at 48 kHz.
6. Play or export the generated WAV.

Only `arm64-v8a` is built. Minimum Android version is API 28.
