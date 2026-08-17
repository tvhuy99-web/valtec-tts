#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from run_reference_parity import load_engine_class


def metrics(left: np.ndarray, right: np.ndarray) -> dict:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape:
        return {"shape_equal": False, "left_shape": list(left.shape), "right_shape": list(right.shape)}
    delta = a - b
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return {
        "shape_equal": True,
        "count": int(a.size),
        "max_abs": float(np.max(np.abs(delta))) if a.size else 0.0,
        "mean_abs": float(np.mean(np.abs(delta))) if a.size else 0.0,
        "rmse": float(np.sqrt(np.mean(delta * delta))) if a.size else 0.0,
        "cosine": float(np.dot(a, b) / denom) if denom else None,
    }


def top(values: np.ndarray, count: int = 5) -> list[dict]:
    flat = np.asarray(values).reshape(-1)
    indices = np.argsort(flat)[::-1][:count]
    return [{"id": int(index), "logit": float(flat[index])} for index in indices]


def read_codes(path: Path) -> list[int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    return [int(value) for value in row["codes"].split(":")]


def split_past(outputs, layers: int):
    return outputs[1:1 + layers], outputs[1 + layers:1 + 2 * layers]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-source", required=True, type=Path)
    parser.add_argument("--onnx-dir", required=True, type=Path)
    parser.add_argument("--native-model-dir", required=True, type=Path)
    parser.add_argument("--native-dump-dir", required=True, type=Path)
    parser.add_argument("--numpy-dump-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    engine_class = load_engine_class(args.official_source)
    engine = engine_class(
        checkpoint_path=str(args.native_model_dir),
        onnx_dir=str(args.onnx_dir),
        codec_dir=str(args.native_model_dir / "codec"),
        threads=2,
    )
    heads = np.load(args.native_model_dir / "vieneu_v3_heads.npz")
    text_emb = heads["text_emb"].astype(np.float32)
    audio_emb = heads["audio_emb"].astype(np.float32)
    semantic_hidden = np.fromfile(
        args.native_dump_dir / "native_prefill_h.f32", dtype=np.float32
    ).reshape(1, -1)
    oracle_codes = read_codes(args.native_dump_dir / "native_codes.csv")

    token = np.stack(
        [semantic_hidden[0], text_emb[int(engine.sgs)]], axis=0
    )[None].astype(np.float32)
    feed = {
        "token_emb": token,
        "position_ids": np.asarray([[0, 1]], dtype=np.int64),
    }
    feed.update(engine._empty_past())
    outputs = engine.sess_ac.run(None, feed)
    onnx_hidden = np.asarray(outputs[0], dtype=np.float32)
    past_k, past_v = split_past(outputs, int(engine.L_loc))

    numpy_initial = np.fromfile(
        args.numpy_dump_dir / "numpy_acoustic_initial.f32", dtype=np.float32
    ).reshape(1, 2, int(engine.hidden))
    stages: list[dict] = [
        {
            "stage": "initial_two_token_prefill",
            "hidden": metrics(onnx_hidden, numpy_initial),
        }
    ]

    numpy_code_hidden = numpy_initial[0, 1]
    onnx_code_hidden = onnx_hidden[0, 1]
    numpy_logits = numpy_code_hidden @ audio_emb[0].T
    onnx_logits = onnx_code_hidden @ audio_emb[0].T
    code_steps: list[dict] = [
        {
            "channel": 0,
            "forced_previous_code": None,
            "hidden": metrics(onnx_code_hidden, numpy_code_hidden),
            "logits": metrics(onnx_logits, numpy_logits),
            "onnx_top": top(onnx_logits),
            "numpy_top": top(numpy_logits),
            "onnx_argmax": int(np.argmax(onnx_logits)),
            "numpy_argmax": int(np.argmax(numpy_logits)),
            "oracle_code": int(oracle_codes[0]),
        }
    ]

    for channel in range(1, int(engine.n_vq)):
        previous_code = int(oracle_codes[channel - 1])
        embedding = audio_emb[channel - 1, previous_code].astype(np.float32)
        feed = {
            "token_emb": embedding.reshape(1, 1, int(engine.hidden)),
            "position_ids": np.asarray([[channel + 1]], dtype=np.int64),
        }
        feed.update(engine._past_feed(past_k, past_v))
        outputs = engine.sess_ac.run(None, feed)
        onnx_hidden = np.asarray(outputs[0], dtype=np.float32)
        past_k, past_v = split_past(outputs, int(engine.L_loc))
        numpy_hidden = np.fromfile(
            args.numpy_dump_dir / f"numpy_acoustic_step_{channel:03d}.f32",
            dtype=np.float32,
        ).reshape(1, 1, int(engine.hidden))
        stages.append(
            {
                "stage": f"step_{channel:03d}",
                "forced_previous_code": previous_code,
                "hidden": metrics(onnx_hidden, numpy_hidden),
            }
        )

        onnx_vector = onnx_hidden[0, 0]
        numpy_vector = numpy_hidden[0, 0]
        onnx_logits = onnx_vector @ audio_emb[channel].T
        numpy_logits = numpy_vector @ audio_emb[channel].T
        code_steps.append(
            {
                "channel": channel,
                "forced_previous_code": previous_code,
                "hidden": metrics(onnx_vector, numpy_vector),
                "logits": metrics(onnx_logits, numpy_logits),
                "onnx_top": top(onnx_logits),
                "numpy_top": top(numpy_logits),
                "onnx_argmax": int(np.argmax(onnx_logits)),
                "numpy_argmax": int(np.argmax(numpy_logits)),
                "oracle_code": int(oracle_codes[channel]),
            }
        )

    first_hidden_divergence = next(
        (
            item["channel"]
            for item in code_steps
            if item["hidden"].get("max_abs", 0.0) > 1.0e-5
        ),
        None,
    )
    first_argmax_divergence = next(
        (
            item["channel"]
            for item in code_steps
            if item["onnx_argmax"] != item["numpy_argmax"]
        ),
        None,
    )
    report = {
        "schema": 1,
        "forced_oracle_codes": oracle_codes,
        "initial_full_hidden": stages[0]["hidden"],
        "stages": stages,
        "code_steps": code_steps,
        "first_hidden_divergence_over_1e_5": first_hidden_divergence,
        "first_argmax_divergence": first_argmax_divergence,
        "conclusion": (
            "onnx_acoustic_matches_native_npz_math"
            if first_argmax_divergence is None
            else "onnx_acoustic_graph_or_weights_diverge_from_native_npz"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "first_hidden_divergence_over_1e_5": first_hidden_divergence,
                "first_argmax_divergence": first_argmax_divergence,
                "code_steps": [
                    {
                        "channel": item["channel"],
                        "hidden_max_abs": item["hidden"].get("max_abs"),
                        "hidden_cosine": item["hidden"].get("cosine"),
                        "onnx_argmax": item["onnx_argmax"],
                        "numpy_argmax": item["numpy_argmax"],
                    }
                    for item in code_steps
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
