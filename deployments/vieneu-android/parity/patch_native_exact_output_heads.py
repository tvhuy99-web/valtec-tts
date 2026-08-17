#!/usr/bin/env python3
"""Replace the native transposed output-head helper with exact row-major matvec.

The acoustic hidden state matches an independent NumPy oracle through channel 1,
but the selected channel-1 code differs. This patch isolates the output-head
projection by multiplying the original row-major embedding tables directly.
"""

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_native_exact_output_heads.py <VieNeu-TTS.cpp checkout>")

path = pathlib.Path(sys.argv[1]) / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
text = path.read_text(encoding="utf-8")

old_audio = '''        } else {
            const float* head = assets.audio_emb_t().data() + static_cast<int64_t>(ch) * config.hidden_size * config.audio_vocab_size;
            ScopedBenchTimer timer(benchmark_enabled, bench.sample_head_matvec_ms);
            matvec_transposed_native(vec, head, config.hidden_size, config.audio_vocab_size, logits);
        }
'''
new_audio = '''        } else {
            const float* head = assets.audio_emb().data() +
                static_cast<int64_t>(ch) * config.audio_vocab_size * config.hidden_size;
            ScopedBenchTimer timer(benchmark_enabled, bench.sample_head_matvec_ms);
            logits.resize(static_cast<size_t>(config.audio_vocab_size));
            linear_matvec_cpu(
                head,
                vec,
                config.audio_vocab_size,
                config.hidden_size,
                n_threads,
                logits.data());
        }
'''
count = text.count(old_audio)
if count != 1:
    raise RuntimeError(f"audio output-head anchor: expected one match, found {count}")
text = text.replace(old_audio, new_audio, 1)

old_eos = '''            } else {
                ScopedBenchTimer timer(impl_->benchmark_enabled, impl_->bench.eos_head_matvec_ms);
                matvec_transposed_native(impl_->slot0.data(), impl_->assets.text_emb_t().data(), H, impl_->config.text_vocab_size, impl_->text_logits);
            }
'''
new_eos = '''            } else {
                ScopedBenchTimer timer(impl_->benchmark_enabled, impl_->bench.eos_head_matvec_ms);
                impl_->text_logits.resize(static_cast<size_t>(impl_->config.text_vocab_size));
                linear_matvec_cpu(
                    impl_->assets.text_emb().data(),
                    impl_->slot0.data(),
                    impl_->config.text_vocab_size,
                    H,
                    impl_->n_threads,
                    impl_->text_logits.data());
            }
'''
count = text.count(old_eos)
if count != 1:
    raise RuntimeError(f"EOS output-head anchor: expected one match, found {count}")
text = text.replace(old_eos, new_eos, 1)

path.write_text(text, encoding="utf-8")
print("Replaced transposed acoustic/text output heads with exact row-major matvec")
