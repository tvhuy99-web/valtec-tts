#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "vieneu/v3_native/vieneu_v3_native.h"

namespace {
constexpr const char* TAG = "VieNeuJNI";
std::mutex g_mutex;
std::unique_ptr<VieneuV3NativeEngine> g_engine;
std::string g_last_error;

std::string from_jstring(JNIEnv* env, jstring value) {
    if (!value) return {};
    const char* chars = env->GetStringUTFChars(value, nullptr);
    if (!chars) return {};
    std::string out(chars);
    env->ReleaseStringUTFChars(value, chars);
    return out;
}

jstring to_jstring(JNIEnv* env, const std::string& value) {
    return env->NewStringUTF(value.c_str());
}

void set_error(const std::string& value) {
    g_last_error = value;
    if (!value.empty()) {
        __android_log_print(ANDROID_LOG_ERROR, TAG, "%s", value.c_str());
    }
}
} // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_initialize(
        JNIEnv* env, jobject, jstring model_dir, jint threads) {
    std::lock_guard<std::mutex> lock(g_mutex);
    try {
        const std::string root = from_jstring(env, model_dir);
        if (root.empty()) {
            set_error("Model directory is empty.");
            return to_jstring(env, g_last_error);
        }

        auto candidate = std::make_unique<VieneuV3NativeEngine>();
        VieneuV3NativeInit init;
        init.model_dir = root;
        init.codec_dir = root + "/codec";
        init.n_threads = std::clamp(static_cast<int>(threads), 1, 8);

        std::string error;
        if (!candidate->initialize(init, error)) {
            set_error(error.empty() ? "VieNeu engine initialization failed." : error);
            return to_jstring(env, g_last_error);
        }

        g_engine = std::move(candidate);
        set_error("");
        __android_log_print(ANDROID_LOG_INFO, TAG, "VieNeu v3 native engine initialized");
        return to_jstring(env, "");
    } catch (const std::exception& e) {
        set_error(std::string("Native initialize exception: ") + e.what());
        return to_jstring(env, g_last_error);
    } catch (...) {
        set_error("Unknown native initialize exception.");
        return to_jstring(env, g_last_error);
    }
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_synthesize(
        JNIEnv* env, jobject, jstring text, jstring reference_wav, jstring style) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (!g_engine) {
        set_error("VieNeu engine is not initialized.");
        return nullptr;
    }

    try {
        VieneuV3NativeParams params;
        params.text = from_jstring(env, text);
        params.ref_audio_path = from_jstring(env, reference_wav);
        params.style = from_jstring(env, style);
        params.denoise_ref = true;
        params.use_ref_codes = true;
        params.apply_watermark = true;
        params.max_chars = 384;

        std::vector<float> audio;
        std::string error;
        if (!g_engine->synthesize(params, audio, error)) {
            set_error(error.empty() ? "VieNeu synthesis failed." : error);
            return nullptr;
        }
        if (audio.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) {
            set_error("Generated audio is too large for a Java float array.");
            return nullptr;
        }

        jfloatArray result = env->NewFloatArray(static_cast<jsize>(audio.size()));
        if (!result) {
            set_error("Unable to allocate Java audio buffer.");
            return nullptr;
        }
        env->SetFloatArrayRegion(result, 0, static_cast<jsize>(audio.size()), audio.data());
        set_error("");
        return result;
    } catch (const std::exception& e) {
        set_error(std::string("Native synthesis exception: ") + e.what());
        return nullptr;
    } catch (...) {
        set_error("Unknown native synthesis exception.");
        return nullptr;
    }
}

extern "C" JNIEXPORT jint JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_sampleRate(JNIEnv*, jobject) {
    std::lock_guard<std::mutex> lock(g_mutex);
    return g_engine ? g_engine->sample_rate() : 48000;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_lastError(JNIEnv* env, jobject) {
    std::lock_guard<std::mutex> lock(g_mutex);
    return to_jstring(env, g_last_error);
}

extern "C" JNIEXPORT void JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_release(JNIEnv*, jobject) {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_engine.reset();
    g_last_error.clear();
}
