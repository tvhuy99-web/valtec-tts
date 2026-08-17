#!/usr/bin/env python3
'''Android-only performance patches for the pinned VieNeu native source.

This script runs after diagnostics instrumentation and before add_subdirectory().
It intentionally fails closed if the pinned upstream source no longer matches.
'''

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: optimize_vieneu_android.py <vieneu-source-dir>')

root = pathlib.Path(sys.argv[1])
cpp_path = root / 'src/vieneu/v3_native/vieneu_v3_native.cpp'
header_path = root / 'src/vieneu/v3_native/vieneu_v3_native.h'
assets_cpp_path = root / 'src/vieneu/v3_native/v3_native_assets.cpp'
acoustic_cpp_path = root / 'src/vieneu/v3_native/v3_native_acoustic_ggml.cpp'
backbone_cpp_path = root / 'src/vieneu/v3_native/v3_native_backbone_llama.cpp'

cpp = cpp_path.read_text(encoding='utf-8')
header = header_path.read_text(encoding='utf-8')
assets_cpp = assets_cpp_path.read_text(encoding='utf-8')
acoustic_cpp = acoustic_cpp_path.read_text(encoding='utf-8')
backbone_cpp = backbone_cpp_path.read_text(encoding='utf-8')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


header = replace_once(
    header,
    '''    V3NativeDenoiser denoiser_;
    bool has_denoiser_ = false;
    std::vector<float> prompt_embeds_;
''',
    '''    V3NativeDenoiser denoiser_;
    bool has_denoiser_ = false;
    std::vector<float> prompt_embeds_;

    // Android hot-path cache. The app frequently synthesizes multiple texts with
    // the same reference WAV. Re-running speaker embedding + MOSS reference
    // encoding costs several seconds, so retain the tiny derived tensors and
    // invalidate them when file size/mtime or options change.
    bool reference_cache_valid_ = false;
    std::string reference_cache_path_;
    long long reference_cache_size_ = -1;
    long long reference_cache_mtime_ns_ = -1;
    bool reference_cache_denoise_ = false;
    bool reference_cache_use_codes_ = false;
    std::vector<float> reference_cache_speaker_emb_;
    std::vector<int64_t> reference_cache_codes_;
''',
    'reference cache members',
)

cpp = replace_once(
    cpp,
    '''#include <fstream>
#include <iostream>
#include <stdexcept>

#include <nlohmann/json.hpp>
''',
    '''#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>

#include <nlohmann/json.hpp>
''',
    'sys/stat include',
)

cpp = replace_once(
    cpp,
    '''    speaker_emb.clear();
    ref_codes.clear();
    V3NativeWaveform wav;
''',
    '''    speaker_emb.clear();
    ref_codes.clear();

    struct stat ref_stat {};
    const bool have_ref_stat = ::stat(ref_audio_path.c_str(), &ref_stat) == 0;
    const long long ref_size = have_ref_stat ? static_cast<long long>(ref_stat.st_size) : -1;
    long long ref_mtime_ns = -1;
    if (have_ref_stat) {
#if defined(__APPLE__)
        ref_mtime_ns = static_cast<long long>(ref_stat.st_mtimespec.tv_sec) * 1000000000LL +
                       static_cast<long long>(ref_stat.st_mtimespec.tv_nsec);
#elif defined(_WIN32)
        ref_mtime_ns = static_cast<long long>(ref_stat.st_mtime) * 1000000000LL;
#else
        ref_mtime_ns = static_cast<long long>(ref_stat.st_mtim.tv_sec) * 1000000000LL +
                       static_cast<long long>(ref_stat.st_mtim.tv_nsec);
#endif
    }

    const bool reference_cache_hit =
        reference_cache_valid_ && have_ref_stat &&
        reference_cache_path_ == ref_audio_path &&
        reference_cache_size_ == ref_size &&
        reference_cache_mtime_ns_ == ref_mtime_ns &&
        reference_cache_denoise_ == denoise_ref &&
        reference_cache_use_codes_ == use_ref_codes;

    if (reference_cache_hit) {
        speaker_emb = reference_cache_speaker_emb_;
        ref_codes = reference_cache_codes_;
        if (diag) {
            std::cout << "[V3NativeDiag] stage=reference.cache_hit wall_ms=0"
                      << " speaker_values=" << speaker_emb.size()
                      << " code_values=" << ref_codes.size() << "\n";
        }
        return true;
    }
    if (diag) {
        std::cout << "[V3NativeDiag] stage=reference.cache_miss wall_ms=0\n";
    }

    V3NativeWaveform wav;
''',
    'reference cache lookup',
)

cpp = replace_once(
    cpp,
    '''    ref_diag_ms("reference.total", t_ref_total);
    return true;
}
''',
    '''    if (have_ref_stat) {
        reference_cache_valid_ = true;
        reference_cache_path_ = ref_audio_path;
        reference_cache_size_ = ref_size;
        reference_cache_mtime_ns_ = ref_mtime_ns;
        reference_cache_denoise_ = denoise_ref;
        reference_cache_use_codes_ = use_ref_codes;
        reference_cache_speaker_emb_ = speaker_emb;
        reference_cache_codes_ = ref_codes;
    }
    ref_diag_ms("reference.total", t_ref_total);
    return true;
}
''',
    'reference cache store',
)

assets_cpp = replace_once(
    assets_cpp,
    '''        text_emb_ = text->data;
        audio_emb_ = audio->data;
        text_emb_t_ = transpose_2d_local(text_emb_, config_.text_vocab_size, config_.hidden_size);
        audio_emb_t_ = transpose_audio_emb_local(audio_emb_, config_.n_vq, config_.audio_vocab_size, config_.hidden_size);
''',
    '''        text_emb_ = text->data;
        audio_emb_ = audio->data;
        text_emb_t_.clear();
        audio_emb_t_.clear();
''',
    'skip unused Android transposed head copies',
)

acoustic_cpp = replace_once(
    acoustic_cpp,
    '''        use_ggml_heads = env_flag_enabled("VIENEU_ACOUSTIC_GGML_HEADS", true);
''',
    '''        // Android perf build keeps only the original embedding matrices; the
        // transposed scalar fallback copies are intentionally not allocated.
        use_ggml_heads = true;
''',
    'force GGML heads on Android',
)

backbone_cpp = replace_once(
    backbone_cpp,
    '''    llama_context_params ctx_params = llama_context_default_params();
    ctx_params.n_ctx = 2048;
    ctx_params.n_threads = n_threads;
    ctx_params.n_threads_batch = n_threads_batch;
    ctx_params.embeddings = true; // Enable embeddings extraction
    ctx_params.no_perf = true;
''',
    '''    llama_context_params ctx_params = llama_context_default_params();
    ctx_params.n_ctx = 2048;
    // llama.cpp emits every token embedding while embeddings=true. Keep batches
    // bounded so the output/logit buffers never scale to the full 2048 context.
    ctx_params.n_batch = 128;
    ctx_params.n_ubatch = 128;
    ctx_params.n_outputs_max = 128;
    ctx_params.n_threads = n_threads;
    ctx_params.n_threads_batch = n_threads_batch;
    ctx_params.embeddings = true; // Enable embeddings extraction
    ctx_params.no_perf = true;

    if (std::getenv("VIENEU_V3_NATIVE_BENCHMARK")) {
        std::cout << "[V3NativeDiag] stage=backbone.context"
                  << " n_ctx=" << ctx_params.n_ctx
                  << " n_batch=" << ctx_params.n_batch
                  << " n_ubatch=" << ctx_params.n_ubatch
                  << " n_outputs_max=" << ctx_params.n_outputs_max << "\n";
    }
''',
    'bounded llama output and batch buffers',
)

backbone_cpp = replace_once(
    backbone_cpp,
    '''    const int32_t n_tokens = static_cast<int32_t>(embeds.size() / hidden_size_);
    if (n_tokens <= 0) return false;
''',
    '''    const int32_t n_tokens = static_cast<int32_t>(embeds.size() / hidden_size_);
    if (n_tokens <= 0) return false;
    if (std::getenv("VIENEU_V3_NATIVE_BENCHMARK")) {
        std::cout << "[V3NativeDiag] stage=backbone.prefill_meta"
                  << " tokens=" << n_tokens
                  << " context_capacity=" << prefill_capacity_
                  << " chunk_tokens=128\n";
    }
''',
    'backbone prefill diagnostics',
)

backbone_cpp = replace_once(
    backbone_cpp,
    '''    // Reset KV cache and decoded position
    clear_kv_cache();

    // Copy input embeddings
    std::memcpy(prefill_batch_.embd, embeds.data(), embeds.size() * sizeof(float));

    for (int32_t i = 0; i < n_tokens; ++i) {
        prefill_batch_.pos[i] = i;
        prefill_batch_.n_seq_id[i] = 1;
        prefill_batch_.seq_id[i][0] = 0;
        prefill_batch_.logits[i] = (i == n_tokens - 1); // request logits/embedding output for last token only
    }
    prefill_batch_.n_tokens = n_tokens;

    int res = llama_decode(ctx_, prefill_batch_);

    if (res != 0) {
        std::cerr << "[V3NativeBackbone] Prefill failed with code: " << res << std::endl;
        return false;
    }

    decoded_pos_ = n_tokens;
''',
    '''    // Reset KV cache once, then feed the prompt in bounded blocks. llama.cpp
    // currently treats embeddings=true as output-all, so a single large prefill
    // would require one output row per prompt token. Chunking keeps the maximum
    // output allocation at 128 rows while preserving the same causal KV sequence.
    clear_kv_cache();

    constexpr int32_t kPrefillChunkTokens = 128;
    const bool diag = std::getenv("VIENEU_V3_NATIVE_BENCHMARK") != nullptr;
    for (int32_t chunk_start = 0; chunk_start < n_tokens; chunk_start += kPrefillChunkTokens) {
        const int32_t chunk_tokens = (std::min)(kPrefillChunkTokens, n_tokens - chunk_start);
        const size_t chunk_values = static_cast<size_t>(chunk_tokens) * static_cast<size_t>(hidden_size_);
        const size_t src_offset = static_cast<size_t>(chunk_start) * static_cast<size_t>(hidden_size_);

        std::memcpy(
            prefill_batch_.embd,
            embeds.data() + src_offset,
            chunk_values * sizeof(float));

        for (int32_t i = 0; i < chunk_tokens; ++i) {
            prefill_batch_.pos[i] = chunk_start + i;
            prefill_batch_.n_seq_id[i] = 1;
            prefill_batch_.seq_id[i][0] = 0;
            prefill_batch_.logits[i] = (i == chunk_tokens - 1);
        }
        prefill_batch_.n_tokens = chunk_tokens;

        if (diag) {
            std::cout << "[V3NativeDiag] stage=backbone.prefill_chunk"
                      << " start=" << chunk_start
                      << " tokens=" << chunk_tokens << "\n";
        }

        const int res = llama_decode(ctx_, prefill_batch_);
        if (res != 0) {
            std::cerr << "[V3NativeBackbone] Prefill chunk failed with code: " << res
                      << " start=" << chunk_start
                      << " tokens=" << chunk_tokens << std::endl;
            return false;
        }

        decoded_pos_ = chunk_start + chunk_tokens;
    }
''',
    'chunk Android prefill to bounded output allocation',
)

header_path.write_text(header, encoding='utf-8')
cpp_path.write_text(cpp, encoding='utf-8')
assets_cpp_path.write_text(assets_cpp, encoding='utf-8')
acoustic_cpp_path.write_text(acoustic_cpp, encoding='utf-8')
backbone_cpp_path.write_text(backbone_cpp, encoding='utf-8')

print('Applied Android reference cache, compact heads, and chunked llama prefill')
