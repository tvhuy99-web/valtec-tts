#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>
#include <unistd.h>

#include "vieneu/v3_native/vieneu_v3_native.h"

namespace {
constexpr const char* TAG = "VieNeuJNI";
std::mutex g_mutex;
std::mutex g_log_mutex;
std::unique_ptr<VieneuV3NativeEngine> g_engine;
std::unique_ptr<std::ofstream> g_event_stream;
std::string g_last_error;
std::string g_diag_dir;
std::string g_session_id;
std::chrono::steady_clock::time_point g_synth_start;

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

long long epoch_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

double monotonic_ms() {
    return std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

double process_cpu_ms() {
    timespec ts{};
    if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts) != 0) return -1.0;
    return static_cast<double>(ts.tv_sec) * 1000.0 + static_cast<double>(ts.tv_nsec) / 1.0e6;
}

long long rss_bytes() {
    std::ifstream fs("/proc/self/statm");
    long long total_pages = 0;
    long long rss_pages = 0;
    if (!(fs >> total_pages >> rss_pages)) return -1;
    const long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) return -1;
    return rss_pages * static_cast<long long>(page);
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (unsigned char c : value) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (c < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c)
                        << std::dec << std::setfill(' ');
                } else {
                    out << static_cast<char>(c);
                }
        }
    }
    return out.str();
}

void native_event(const std::string& event, const std::string& data_json = "{}") {
    std::lock_guard<std::mutex> log_lock(g_log_mutex);
    if (!g_event_stream || !g_event_stream->is_open()) return;
    (*g_event_stream)
        << "{\"schema\":2"
        << ",\"ts_epoch_ms\":" << epoch_ms()
        << ",\"monotonic_ms\":" << std::fixed << std::setprecision(3) << monotonic_ms()
        << ",\"session_id\":\"" << json_escape(g_session_id) << "\""
        << ",\"pid\":" << getpid()
        << ",\"event\":\"" << json_escape(event) << "\""
        << ",\"process_cpu_ms\":" << std::fixed << std::setprecision(3) << process_cpu_ms()
        << ",\"rss_bytes\":" << rss_bytes()
        << ",\"data\":" << data_json
        << "}\n";
    g_event_stream->flush();
}

std::string quote(const std::string& value) {
    return std::string("\"") + json_escape(value) + "\"";
}

void set_error(const std::string& value) {
    g_last_error = value;
    if (!value.empty()) {
        __android_log_print(ANDROID_LOG_ERROR, TAG, "%s", value.c_str());
        native_event("native.error", std::string("{\"message\":") + quote(value) + "}");
    }
}

bool should_log_progress(const VieneuProgressEvent& event) {
    const std::string stage = event.stage ? event.stage : "";
    if (stage != "generate_frames") return true;
    if (event.current <= 1 || event.current == event.total) return true;
    return event.current % 10 == 0;
}

void log_progress(const VieneuProgressEvent& event) {
    if (!should_log_progress(event)) return;
    const auto now = std::chrono::steady_clock::now();
    const double synth_elapsed_ms = std::chrono::duration<double, std::milli>(now - g_synth_start).count();
    std::ostringstream data;
    data << "{"
         << "\"stage\":" << quote(event.stage ? event.stage : "")
         << ",\"current\":" << event.current
         << ",\"total\":" << event.total
         << ",\"progress\":" << std::fixed << std::setprecision(6) << event.progress
         << ",\"synth_elapsed_ms\":" << std::fixed << std::setprecision(3) << synth_elapsed_ms
         << ",\"message\":" << quote(event.message)
         << "}";
    native_event("synthesis.progress", data.str());
}

void redirect_native_console(const std::string& dir) {
    const std::string stdout_path = dir + "/native-stdout.log";
    const std::string stderr_path = dir + "/native-stderr.log";
    FILE* out = std::freopen(stdout_path.c_str(), "a", stdout);
    FILE* err = std::freopen(stderr_path.c_str(), "a", stderr);
    if (out) std::setvbuf(stdout, nullptr, _IOLBF, 0);
    if (err) std::setvbuf(stderr, nullptr, _IOLBF, 0);
}
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_configureDiagnostics(
        JNIEnv* env, jobject, jstring log_dir, jstring session_id) {
    try {
        const std::string dir = from_jstring(env, log_dir);
        const std::string sid = from_jstring(env, session_id);
        if (dir.empty()) return to_jstring(env, "Diagnostics directory is empty.");

        {
            std::lock_guard<std::mutex> log_lock(g_log_mutex);
            g_diag_dir = dir;
            g_session_id = sid;
            g_event_stream = std::make_unique<std::ofstream>(dir + "/native-events.jsonl", std::ios::app);
            if (!g_event_stream->is_open()) {
                g_event_stream.reset();
                return to_jstring(env, "Unable to open native diagnostics file.");
            }
        }

        setenv("VIENEU_V3_NATIVE_BENCHMARK", "1", 1);
        setenv("VIENEU_V3_NATIVE_DEBUG_TAGS", "0", 1);
        redirect_native_console(dir);
        native_event(
            "native.diagnostics.configured",
            std::string("{\"directory\":") + quote(dir) +
            ",\"benchmark\":true,\"frame_log_stride\":10}");
        return to_jstring(env, "");
    } catch (const std::exception& e) {
        return to_jstring(env, std::string("Native diagnostics exception: ") + e.what());
    } catch (...) {
        return to_jstring(env, "Unknown native diagnostics exception.");
    }
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_initialize(
        JNIEnv* env, jobject, jstring model_dir, jint threads) {
    std::lock_guard<std::mutex> lock(g_mutex);
    const auto wall_start = std::chrono::steady_clock::now();
    const double cpu_start = process_cpu_ms();
    const long long rss_start = rss_bytes();
    try {
        const std::string root = from_jstring(env, model_dir);
        if (root.empty()) {
            set_error("Model directory is empty.");
            return to_jstring(env, g_last_error);
        }
        const int thread_count = std::clamp(static_cast<int>(threads), 1, 8);
        native_event(
            "engine.initialize.begin",
            std::string("{\"model_dir\":") + quote(root) +
            ",\"threads\":" + std::to_string(thread_count) + "}");

        auto candidate = std::make_unique<VieneuV3NativeEngine>();
        VieneuV3NativeInit init;
        init.model_dir = root;
        init.codec_dir = root + "/codec";
        init.n_threads = thread_count;

        std::string error;
        if (!candidate->initialize(init, error)) {
            const auto wall_end = std::chrono::steady_clock::now();
            std::ostringstream data;
            data << "{\"success\":false"
                 << ",\"wall_ms\":" << std::chrono::duration<double, std::milli>(wall_end - wall_start).count()
                 << ",\"process_cpu_ms\":" << (process_cpu_ms() - cpu_start)
                 << ",\"rss_delta_bytes\":" << (rss_bytes() - rss_start)
                 << ",\"error\":" << quote(error) << "}";
            native_event("engine.initialize.end", data.str());
            set_error(error.empty() ? "VieNeu engine initialization failed." : error);
            return to_jstring(env, g_last_error);
        }

        g_engine = std::move(candidate);
        set_error("");
        const auto wall_end = std::chrono::steady_clock::now();
        std::ostringstream data;
        data << "{\"success\":true"
             << ",\"wall_ms\":" << std::chrono::duration<double, std::milli>(wall_end - wall_start).count()
             << ",\"process_cpu_ms\":" << (process_cpu_ms() - cpu_start)
             << ",\"rss_delta_bytes\":" << (rss_bytes() - rss_start)
             << ",\"sample_rate_hz\":" << g_engine->sample_rate() << "}";
        native_event("engine.initialize.end", data.str());
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

    const auto wall_start = std::chrono::steady_clock::now();
    const double cpu_start = process_cpu_ms();
    const long long rss_start = rss_bytes();
    g_synth_start = wall_start;

    try {
        VieneuV3NativeParams params;
        params.text = from_jstring(env, text);
        params.ref_audio_path = from_jstring(env, reference_wav);
        params.style = from_jstring(env, style);
        params.denoise_ref = true;
        params.use_ref_codes = true;
        params.apply_watermark = true;
        params.max_chars = 384;
        params.progress = [](const VieneuProgressEvent& event) { log_progress(event); };

        std::ostringstream start_data;
        start_data << "{\"text_chars\":" << params.text.size()
                   << ",\"text\":" << quote(params.text)
                   << ",\"reference_wav\":" << quote(params.ref_audio_path)
                   << ",\"style\":" << quote(params.style)
                   << ",\"temperature\":" << params.temperature
                   << ",\"top_k\":" << params.top_k
                   << ",\"top_p\":" << params.top_p
                   << ",\"max_new_frames\":" << params.max_new_frames
                   << ",\"repetition_penalty\":" << params.repetition_penalty
                   << ",\"max_chars\":" << params.max_chars
                   << ",\"denoise_ref\":true"
                   << ",\"use_ref_codes\":true"
                   << ",\"apply_watermark\":true}";
        native_event("synthesis.begin", start_data.str());

        std::vector<float> audio;
        std::string error;
        if (!g_engine->synthesize(params, audio, error)) {
            const auto wall_end = std::chrono::steady_clock::now();
            std::ostringstream data;
            data << "{\"success\":false"
                 << ",\"wall_ms\":" << std::chrono::duration<double, std::milli>(wall_end - wall_start).count()
                 << ",\"process_cpu_ms\":" << (process_cpu_ms() - cpu_start)
                 << ",\"rss_delta_bytes\":" << (rss_bytes() - rss_start)
                 << ",\"error\":" << quote(error) << "}";
            native_event("synthesis.end", data.str());
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

        const auto wall_end = std::chrono::steady_clock::now();
        const double wall_ms = std::chrono::duration<double, std::milli>(wall_end - wall_start).count();
        const int sample_rate = g_engine->sample_rate();
        const double audio_ms = sample_rate > 0 ? static_cast<double>(audio.size()) * 1000.0 / static_cast<double>(sample_rate) : 0.0;
        std::ostringstream data;
        data << "{\"success\":true"
             << ",\"wall_ms\":" << wall_ms
             << ",\"process_cpu_ms\":" << (process_cpu_ms() - cpu_start)
             << ",\"rss_delta_bytes\":" << (rss_bytes() - rss_start)
             << ",\"samples\":" << audio.size()
             << ",\"sample_rate_hz\":" << sample_rate
             << ",\"audio_duration_ms\":" << audio_ms
             << ",\"rtf\":" << (audio_ms > 0.0 ? wall_ms / audio_ms : -1.0)
             << "}";
        native_event("synthesis.end", data.str());
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
    native_event("engine.release.begin");
    g_engine.reset();
    g_last_error.clear();
    native_event("engine.release.end");
}
