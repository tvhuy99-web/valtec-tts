#!/usr/bin/env python3
"""Isolate runtime math from model-package drift using native package assets."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from run_reference_parity import load_engine_class

PHONEMES = "zˈaː6w nˈa2j bˈaː6n kˈɔɜ xwˈɛ4 xˌoŋ"


def tensor_metrics(left: np.ndarray, right: np.ndarray) -> dict:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape:
        return {"shape_equal": False, "left_shape": list(a.shape), "right_shape": list(b.shape)}
    delta = a - b
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return {
        "shape_equal": True,
        "count": int(a.size),
        "max_abs": float(np.max(np.abs(delta))) if a.size else 0.0,
        "mean_abs": float(np.mean(np.abs(delta))) if a.size else 0.0,
        "rmse": float(np.sqrt(np.mean(delta * delta))) if a.size else 0.0,
        "cosine": float(np.dot(a, b) / denom) if denom > 0 else float("nan"),
    }


def read_first_native_codes(path: Path) -> tuple[list[int], bool]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)
    return [int(v) for v in row["codes"].split(":")], bool(int(row["eos"]))


def split_past(outputs, layers: int):
    return outputs[1:1 + layers], outputs[1 + layers:1 + 2 * layers]


def official_acoustic_frame(engine, hidden: np.ndarray, text_emb: np.ndarray,
                            audio_emb: np.ndarray, sgs: int, eos_id: int):
    h = np.asarray(hidden, dtype=np.float32).reshape(1, -1)
    H = h.shape[-1]
    cond = h[0]
    txt = text_emb[sgs].astype(np.float32)
    token = np.stack([cond, txt])[None].astype(np.float32)
    feed = {"token_emb": token, "position_ids": np.asarray([[0, 1]], dtype=np.int64)}
    feed.update(engine._empty_past())
    outputs = engine.sess_ac.run(None, feed)
    local_hidden = outputs[0]
    past_k, past_v = split_past(outputs, engine.L_loc)
    slot0 = local_hidden[0, 0].astype(np.float32)
    codes = [int(np.argmax(local_hidden[0, 1].astype(np.float32) @ audio_emb[0].T))]
    for channel in range(1, engine.n_vq):
        embedding = audio_emb[channel - 1, codes[-1]].astype(np.float32)
        feed = {
            "token_emb": embedding.reshape(1, 1, H),
            "position_ids": np.asarray([[channel + 1]], dtype=np.int64),
        }
        feed.update(engine._past_feed(past_k, past_v))
        outputs = engine.sess_ac.run(None, feed)
        local_hidden = outputs[0]
        past_k, past_v = split_past(outputs, engine.L_loc)
        codes.append(int(np.argmax(local_hidden[0, 0].astype(np.float32) @ audio_emb[channel].T)))
    eos = int(np.argmax(slot0 @ text_emb.T)) == int(eos_id)
    return codes, eos


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-source", required=True)
    parser.add_argument("--onnx-dir", required=True)
    parser.add_argument("--native-model-dir", required=True)
    parser.add_argument("--native-dump-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    native_model = Path(args.native_model_dir)
    native_dump = Path(args.native_dump_dir)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    OnnxV3LiteEngine = load_engine_class(Path(args.official_source))
    engine = OnnxV3LiteEngine(
        checkpoint_path=str(native_model),
        onnx_dir=args.onnx_dir,
        codec_dir=str(native_model / "codec"),
        threads=2,
    )

    voices = json.loads((native_model / "voices_v3_turbo.json").read_text(encoding="utf-8"))
    voice_id = voices.get("default_voice") or next(iter(voices["presets"]))
    preset = voices["presets"][voice_id]
    speaker_emb = np.asarray(preset["speaker_emb"], dtype=np.float32).reshape(-1)

    native_meta = json.loads((native_dump / "native_meta.json").read_text(encoding="utf-8"))
    rows = np.fromfile(native_dump / "native_rows.i64", dtype=np.int64).reshape(
        int(native_meta["rows"]), int(native_meta["cols"])
    )
    native_anchor_cpp = np.fromfile(native_dump / "native_anchor.f32", dtype=np.float32)
    native_prompt_cpp = np.fromfile(native_dump / "native_prompt.f32", dtype=np.float32).reshape(
        int(native_meta["rows"]), int(native_meta["hidden"])
    )
    native_prefill_cpp = np.fromfile(native_dump / "native_prefill_h.f32", dtype=np.float32)
    native_codes, native_eos = read_first_native_codes(native_dump / "native_codes.csv")

    heads = np.load(native_model / "vieneu_v3_heads.npz")
    text_emb = heads["text_emb"].astype(np.float32)
    audio_emb = heads["audio_emb"].astype(np.float32)
    xvec_w = heads["xvec_w"].astype(np.float32)
    xvec_b = heads["xvec_b"].astype(np.float32)
    ln_w = heads["xvec_ln_w"].astype(np.float32)
    ln_b = heads["xvec_ln_b"].astype(np.float32)
    ln_eps = float(np.asarray(heads["xvec_ln_eps"]).reshape(-1)[0])

    anchor = speaker_emb @ xvec_w.T + xvec_b
    anchor = (anchor - anchor.mean()) / np.sqrt(anchor.var() + ln_eps)
    anchor = (anchor * ln_w + ln_b).astype(np.float32)

    prompt = text_emb[rows[:, 0]].copy()
    audio_pad = int(engine.audio_pad)
    for channel in range(engine.n_vq):
        ids = rows[:, channel + 1]
        valid = ids != audio_pad
        safe = np.where(valid, ids, 0)
        prompt += audio_emb[channel, safe] * valid[:, None]
    prompt += anchor[None]
    prompt = prompt.astype(np.float32)

    official_prefill = engine.sess_pre.run(None, {"inputs_embeds": prompt[None]})[0][:, -1].reshape(-1).astype(np.float32)
    official_codes, official_eos = official_acoustic_frame(
        engine,
        native_prefill_cpp,
        text_emb,
        audio_emb,
        int(engine.sgs),
        int(engine.eos_speech),
    )

    first_code_mismatch = None
    for channel, (native_code, official_code) in enumerate(zip(native_codes, official_codes)):
        if native_code != official_code:
            first_code_mismatch = {
                "channel": channel,
                "native": native_code,
                "official_onnx_same_hidden_and_heads": official_code,
            }
            break

    report = {
        "voice_id": voice_id,
        "phonemes": PHONEMES,
        "same_native_heads_anchor": tensor_metrics(native_anchor_cpp, anchor),
        "same_native_heads_prompt": tensor_metrics(native_prompt_cpp, prompt),
        "same_prompt_backbone_hidden": tensor_metrics(native_prefill_cpp, official_prefill),
        "native_frame0_codes": native_codes,
        "official_onnx_same_hidden_and_heads_frame0_codes": official_codes,
        "native_frame0_eos": native_eos,
        "official_onnx_same_hidden_and_heads_frame0_eos": official_eos,
        "first_acoustic_code_mismatch": first_code_mismatch,
        "frame0_codes_exact": native_codes == official_codes,
    }
    if report["same_native_heads_anchor"].get("max_abs", 1.0) > 1e-5:
        report["first_runtime_divergence"] = "native_speaker_anchor_math"
    elif report["same_native_heads_prompt"].get("max_abs", 1.0) > 1e-5:
        report["first_runtime_divergence"] = "native_prompt_embedding_math"
    elif (report["same_prompt_backbone_hidden"].get("cosine", 0.0) < 0.999 or
          report["same_prompt_backbone_hidden"].get("max_abs", 1.0) > 1e-2):
        report["first_runtime_divergence"] = "gguf_semantic_backbone"
    elif first_code_mismatch is not None:
        report["first_runtime_divergence"] = "native_acoustic_decoder_or_acoustic_weights"
    elif native_eos != official_eos:
        report["first_runtime_divergence"] = "native_eos_head"
    else:
        report["first_runtime_divergence"] = "no_runtime_divergence_in_compared_frame"

    np.save(output / "sameasset_anchor.npy", anchor)
    np.save(output / "sameasset_prompt.npy", prompt)
    np.save(output / "sameasset_official_prefill_h.npy", official_prefill)
    (output / "same-asset-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
