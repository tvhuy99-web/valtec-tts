#!/usr/bin/env python3
"""Run the official ONNX update pipeline and dump deterministic parity tensors."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf

PHONEMES = "zˈaː6w nˈa2j bˈaː6n kˈɔɜ xwˈɛ4 xˌoŋ"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-source", required=True)
    parser.add_argument("--onnx-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--codec-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-frames", type=int, default=80)
    args = parser.parse_args()

    import sys
    sys.path.insert(0, str(Path(args.official_source) / "src"))
    from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir = Path(args.model_dir)
    voices = json.loads((model_dir / "voices_v3_turbo.json").read_text(encoding="utf-8"))
    voice_id = voices.get("default_voice")
    presets = voices.get("presets", {})
    if not voice_id or voice_id not in presets:
        if not presets:
            raise RuntimeError("No preset voices found in voices_v3_turbo.json")
        voice_id = next(iter(presets))
    preset = presets[voice_id]
    speaker_emb = np.asarray(preset["speaker_emb"], dtype=np.float32).reshape(-1)
    ref_codes = np.asarray(preset["codes"], dtype=np.int64)
    if ref_codes.ndim != 2 or ref_codes.shape[1] != 16:
        raise RuntimeError(f"Unexpected preset code shape: {ref_codes.shape}")

    engine = OnnxV3LiteEngine(
        checkpoint_path=str(model_dir),
        onnx_dir=args.onnx_dir,
        codec_dir=args.codec_dir,
        threads=2,
    )

    style_id = engine._resolve_style_id()
    anchor = engine._speaker_anchor(speaker_emb)
    rows = engine._build_rows(PHONEMES, ref_codes, style_id)
    prompt = engine._embed_rows(rows, anchor)

    np.save(out_dir / "reference_anchor.npy", anchor)
    np.save(out_dir / "reference_rows.npy", rows)
    np.save(out_dir / "reference_prompt.npy", prompt)

    pre = engine.sess_pre.run(None, {"inputs_embeds": prompt})
    past_k = [pre[1 + i] for i in range(engine.L)]
    past_v = [pre[1 + engine.L + i] for i in range(engine.L)]
    hidden = pre[0][:, -1].astype(np.float32)
    np.save(out_dir / "reference_prefill_h.npy", hidden)

    prompt_tokens = int(prompt.shape[1])
    history = [set() for _ in range(engine.n_vq)]
    frames: list[np.ndarray] = []
    eos_frame = -1
    code_rows: list[tuple[int, int, str]] = []

    for frame_index in range(args.max_frames):
        codes, eos = engine._acoustic_frame(
            hidden,
            temperature=0.0,
            top_k=0,
            top_p=1.0,
            rep_pen=1.2,
            hist=history,
        )
        code_array = np.asarray(codes, dtype=np.int64)
        frames.append(code_array)
        code_rows.append((frame_index, int(eos), ":".join(str(int(v)) for v in code_array)))
        if eos:
            eos_frame = frame_index
            break

        slot = np.full((1, 1, engine.n_vq + 1), engine.audio_pad, dtype=np.int64)
        slot[:, :, 0] = engine.sgs
        slot[0, 0, 1:] = code_array
        slot_embed = engine._embed_rows(slot[0], anchor)
        feed = {
            "inputs_embeds": slot_embed,
            "position_ids": np.asarray([[prompt_tokens + frame_index]], dtype=np.int64),
        }
        for layer in range(engine.L):
            feed[f"past_k_{layer}"] = past_k[layer]
            feed[f"past_v_{layer}"] = past_v[layer]
        decoded = engine.sess_dec.run(None, feed)
        hidden = decoded[0][:, 0].astype(np.float32)
        np.save(out_dir / f"reference_decode_h_{frame_index:03d}.npy", hidden)
        past_k = [decoded[1 + i] for i in range(engine.L)]
        past_v = [decoded[1 + engine.L + i] for i in range(engine.L)]

    with (out_dir / "reference_codes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame", "eos", "codes"])
        writer.writerows(code_rows)

    frame_matrix = np.stack(frames) if frames else np.zeros((0, engine.n_vq), dtype=np.int64)
    np.save(out_dir / "reference_codes.npy", frame_matrix)
    waveform = engine._decode_codes(frame_matrix) if frame_matrix.size else np.zeros(0, dtype=np.float32)
    sf.write(out_dir / "reference.wav", waveform, engine.SAMPLE_RATE, subtype="PCM_16")

    meta = {
        "phonemes": PHONEMES,
        "voice_id": voice_id,
        "style_id": int(style_id),
        "speaker_values": int(speaker_emb.size),
        "reference_code_shape": list(ref_codes.shape),
        "rows": list(rows.shape),
        "prompt": list(prompt.shape),
        "hidden": int(engine.hidden),
        "backbone_layers": int(engine.L),
        "acoustic_layers": int(engine.L_loc),
        "generated_frames": int(frame_matrix.shape[0]),
        "eos_frame": int(eos_frame),
    }
    (out_dir / "reference_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
