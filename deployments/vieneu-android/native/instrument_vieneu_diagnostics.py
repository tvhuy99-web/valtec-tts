#!/usr/bin/env python3
import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: instrument_vieneu_diagnostics.py <vieneu-source-dir>")

root = pathlib.Path(sys.argv[1])
path = root / "src/vieneu/v3_native/vieneu_v3_native.cpp"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
'''bool VieneuV3NativeEngine::initialize(const VieneuV3NativeInit& init, std::string& error) {
    try {
        model_dir_ = init.model_dir;
        if (!assets_.load(init.model_dir, error)) return false;
        config_ = assets_.config();

        const std::string tok_path = init.tokenizer_path.empty() ? join_paths(init.model_dir, "tokenizer.json") : init.tokenizer_path;
        if (!tokenizer_.load(tok_path, error)) return false;

        prompt_builder_ = std::make_unique<V3NativePrompt>(config_, tokenizer_, assets_);
        acoustic_ = std::make_unique<V3NativeAcoustic>(config_, assets_, sampler_, init.n_threads);
        if (!acoustic_->initialize(error)) return false;
''',
'''bool VieneuV3NativeEngine::initialize(const VieneuV3NativeInit& init, std::string& error) {
    try {
        const bool diag = env_flag_enabled_local("VIENEU_V3_NATIVE_BENCHMARK");
        const auto t_init_total = std::chrono::high_resolution_clock::now();
        auto diag_ms = [diag](const char* stage, const std::chrono::high_resolution_clock::time_point& started) {
            if (!diag) return;
            const auto ended = std::chrono::high_resolution_clock::now();
            std::cout << "[V3NativeDiag] stage=" << stage
                      << " wall_ms=" << std::chrono::duration<double, std::milli>(ended - started).count() << "\\n";
        };

        model_dir_ = init.model_dir;
        auto t_stage = std::chrono::high_resolution_clock::now();
        if (!assets_.load(init.model_dir, error)) return false;
        diag_ms("init.assets_load", t_stage);
        config_ = assets_.config();

        const std::string tok_path = init.tokenizer_path.empty() ? join_paths(init.model_dir, "tokenizer.json") : init.tokenizer_path;
        t_stage = std::chrono::high_resolution_clock::now();
        if (!tokenizer_.load(tok_path, error)) return false;
        diag_ms("init.tokenizer_load", t_stage);

        t_stage = std::chrono::high_resolution_clock::now();
        prompt_builder_ = std::make_unique<V3NativePrompt>(config_, tokenizer_, assets_);
        acoustic_ = std::make_unique<V3NativeAcoustic>(config_, assets_, sampler_, init.n_threads);
        diag_ms("init.objects_create", t_stage);
        t_stage = std::chrono::high_resolution_clock::now();
        if (!acoustic_->initialize(error)) return false;
        diag_ms("init.acoustic_initialize", t_stage);
''',
"initialize head",
)

replace_once(
'''        backbone_params.n_threads = init.n_threads;
        backbone_params.n_threads_batch = init.n_threads;
        if (!backbone_.initialize(backbone_params)) {
            error = "Failed to initialize llama.cpp Qwen3 backbone model: " + backbone_params.model_path;
            return false;
        }

        V3CodecParams codec_params;
''',
'''        backbone_params.n_threads = init.n_threads;
        backbone_params.n_threads_batch = init.n_threads;
        t_stage = std::chrono::high_resolution_clock::now();
        if (!backbone_.initialize(backbone_params)) {
            error = "Failed to initialize llama.cpp Qwen3 backbone model: " + backbone_params.model_path;
            return false;
        }
        diag_ms("init.backbone_initialize", t_stage);

        V3CodecParams codec_params;
''',
"backbone timing",
)

replace_once(
'''        codec_params.codec_dir = init.codec_dir;
        codec_params.n_threads = init.n_threads;
        codec_params.n_vq = config_.n_vq;
        if (!codec_.initialize(codec_params, error)) return false;

        if (config_.use_speaker_embedding) {
''',
'''        codec_params.codec_dir = init.codec_dir;
        codec_params.n_threads = init.n_threads;
        codec_params.n_vq = config_.n_vq;
        t_stage = std::chrono::high_resolution_clock::now();
        if (!codec_.initialize(codec_params, error)) return false;
        diag_ms("init.codec_initialize", t_stage);

        if (config_.use_speaker_embedding) {
''',
"codec timing",
)

replace_once(
'''            if (!v3_native_file_exists(spk_path)) {
                error = "Missing v3 native speaker encoder: " + spk_path;
                return false;
            }
            if (!speaker_encoder_.initialize(spk_path, init.n_threads, error)) return false;
        }

        const std::string den_path = join_paths(init.model_dir, "denoiser.onnx");
''',
'''            if (!v3_native_file_exists(spk_path)) {
                error = "Missing v3 native speaker encoder: " + spk_path;
                return false;
            }
            t_stage = std::chrono::high_resolution_clock::now();
            if (!speaker_encoder_.initialize(spk_path, init.n_threads, error)) return false;
            diag_ms("init.speaker_encoder_initialize", t_stage);
        }

        const std::string den_path = join_paths(init.model_dir, "denoiser.onnx");
''',
"speaker encoder timing",
)

replace_once(
'''        if (v3_native_file_exists(den_path)) {
            std::string den_error;
            has_denoiser_ = denoiser_.initialize(den_path, init.n_threads, den_error);
            if (!has_denoiser_) {
                std::cerr << "[V3NativeEngine] Warning: failed to initialize denoiser: " << den_error << "\\n";
            }
        }

        voice_presets_.clear();
''',
'''        if (v3_native_file_exists(den_path)) {
            std::string den_error;
            t_stage = std::chrono::high_resolution_clock::now();
            has_denoiser_ = denoiser_.initialize(den_path, init.n_threads, den_error);
            diag_ms("init.denoiser_initialize", t_stage);
            if (!has_denoiser_) {
                std::cerr << "[V3NativeEngine] Warning: failed to initialize denoiser: " << den_error << "\\n";
            }
        }

        t_stage = std::chrono::high_resolution_clock::now();
        voice_presets_.clear();
''',
"denoiser timing",
)

replace_once(
'''        initialized_ = true;
        return true;
''',
'''        diag_ms("init.voices_parse", t_stage);
        initialized_ = true;
        diag_ms("init.total", t_init_total);
        return true;
''',
"init total",
)

replace_once(
'''    speaker_emb.clear();
    ref_codes.clear();
    V3NativeWaveform wav;
    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;
    v3_trim_seconds(wav, 8.0);

    if (denoise_ref && has_denoiser_) {
''',
'''    const bool diag = env_flag_enabled_local("VIENEU_V3_NATIVE_BENCHMARK");
    const auto t_ref_total = std::chrono::high_resolution_clock::now();
    auto ref_diag_ms = [diag](const char* stage, const std::chrono::high_resolution_clock::time_point& started) {
        if (!diag) return;
        const auto ended = std::chrono::high_resolution_clock::now();
        std::cout << "[V3NativeDiag] stage=" << stage
                  << " wall_ms=" << std::chrono::duration<double, std::milli>(ended - started).count() << "\\n";
    };

    speaker_emb.clear();
    ref_codes.clear();
    V3NativeWaveform wav;
    auto t_ref_stage = std::chrono::high_resolution_clock::now();
    if (!v3_read_wav_mono(ref_audio_path, wav, error)) return false;
    ref_diag_ms("reference.read_wav", t_ref_stage);
    t_ref_stage = std::chrono::high_resolution_clock::now();
    v3_trim_seconds(wav, 8.0);
    ref_diag_ms("reference.trim_8s", t_ref_stage);

    if (denoise_ref && has_denoiser_) {
''',
"reference start",
)

replace_once(
'''        V3NativeWaveform clean;
        std::string warning;
        if (denoiser_.denoise(wav, clean, warning)) {
            wav = std::move(clean);
        } else if (!warning.empty()) {
            std::cerr << "[V3NativeEngine] Warning: " << warning << "\\n";
        }
    }

    if (config_.use_speaker_embedding) {
        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;
    }

    if (use_ref_codes) {
        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
''',
'''        V3NativeWaveform clean;
        std::string warning;
        t_ref_stage = std::chrono::high_resolution_clock::now();
        if (denoiser_.denoise(wav, clean, warning)) {
            wav = std::move(clean);
        } else if (!warning.empty()) {
            std::cerr << "[V3NativeEngine] Warning: " << warning << "\\n";
        }
        ref_diag_ms("reference.denoise", t_ref_stage);
    }

    if (config_.use_speaker_embedding) {
        t_ref_stage = std::chrono::high_resolution_clock::now();
        if (!speaker_encoder_.embed(wav.mono, wav.sample_rate, speaker_emb, error)) return false;
        ref_diag_ms("reference.speaker_embedding", t_ref_stage);
    }

    if (use_ref_codes) {
        t_ref_stage = std::chrono::high_resolution_clock::now();
        std::vector<float> mono48 = v3_resample_linear(wav.mono, wav.sample_rate, sample_rate());
        ref_diag_ms("reference.resample_48k", t_ref_stage);
''',
"reference denoise embed",
)

replace_once(
'''        std::vector<float> stereo(static_cast<size_t>(2 * frames), 0.0f);
        std::copy(mono48.begin(), mono48.end(), stereo.begin());
        std::copy(mono48.begin(), mono48.end(), stereo.begin() + frames);
        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
    }
    return true;
}
''',
'''        std::vector<float> stereo(static_cast<size_t>(2 * frames), 0.0f);
        std::copy(mono48.begin(), mono48.end(), stereo.begin());
        std::copy(mono48.begin(), mono48.end(), stereo.begin() + frames);
        t_ref_stage = std::chrono::high_resolution_clock::now();
        if (!codec_.encode_stereo(stereo, frames, ref_codes, error)) return false;
        ref_diag_ms("reference.codec_encode", t_ref_stage);
    }
    ref_diag_ms("reference.total", t_ref_total);
    return true;
}
''',
"reference codec total",
)

replace_once(
'''    out_audio.clear();
    const V3PromptRows rows = prompt_builder_->build_rows(phonemes, ref_codes, style_token_id);
    prompt_builder_->embed_rows_into(rows, speaker_anchor, prompt_embeds_);

    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
        const bool benchmark_enabled = env_flag_enabled_local("VIENEU_V3_NATIVE_BENCHMARK");
''',
'''    out_audio.clear();
    const bool benchmark_enabled = env_flag_enabled_local("VIENEU_V3_NATIVE_BENCHMARK");
    const auto t_prompt_start = benchmark_enabled ? std::chrono::high_resolution_clock::now() : std::chrono::high_resolution_clock::time_point{};
    const V3PromptRows rows = prompt_builder_->build_rows(phonemes, ref_codes, style_token_id);
    prompt_builder_->embed_rows_into(rows, speaker_anchor, prompt_embeds_);
    if (benchmark_enabled) {
        const auto t_prompt_end = std::chrono::high_resolution_clock::now();
        std::cout << "[V3NativeDiag] stage=prompt.build_and_embed wall_ms="
                  << std::chrono::duration<double, std::milli>(t_prompt_end - t_prompt_start).count() << "\\n";
    }

    std::lock_guard<std::mutex> lock(run_mutex_);
    try {
''',
"prompt timing",
)

path.write_text(text, encoding="utf-8")
print(f"Instrumented {path}")
