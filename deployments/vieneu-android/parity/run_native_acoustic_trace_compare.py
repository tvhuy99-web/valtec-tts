#!/usr/bin/env python3
"""Locate the first sub-stage where native acoustic prefill diverges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def rms_norm(x: np.ndarray, weight: np.ndarray, eps: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    scale = np.float32(1.0) / np.sqrt(
        np.mean(x * x, axis=-1, keepdims=True, dtype=np.float32) + np.float32(eps)
    )
    return (x * scale * np.asarray(weight, dtype=np.float32)).astype(np.float32)


def linear(x: np.ndarray, weight: np.ndarray) -> np.ndarray:
    return (np.asarray(x, dtype=np.float32) @ np.asarray(weight, dtype=np.float32).T).astype(np.float32)


def silu(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return (x / (np.float32(1.0) + np.exp(-x, dtype=np.float32))).astype(np.float32)


def softmax(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    exp = np.exp(x - np.max(x), dtype=np.float32)
    return (exp / np.sum(exp, dtype=np.float64)).astype(np.float32)


def metrics(a: np.ndarray, b: np.ndarray) -> dict:
    left = np.asarray(a, dtype=np.float64).reshape(-1)
    right = np.asarray(b, dtype=np.float64).reshape(-1)
    if left.shape != right.shape:
        return {"shape_equal": False, "left": list(left.shape), "right": list(right.shape)}
    delta = left - right
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    return {
        "shape_equal": True,
        "count": int(left.size),
        "max_abs": float(np.max(np.abs(delta))) if left.size else 0.0,
        "mean_abs": float(np.mean(np.abs(delta))) if left.size else 0.0,
        "rmse": float(np.sqrt(np.mean(delta * delta))) if left.size else 0.0,
        "cosine": float(np.dot(left, right) / denom) if denom > 0 else float("nan"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-model-dir", required=True)
    parser.add_argument("--native-dump-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    model = Path(args.native_model_dir)
    dumps = Path(args.native_dump_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    config = json.loads((model / "config.json").read_text(encoding="utf-8"))
    heads = np.load(model / "vieneu_v3_heads.npz")
    weights = np.load(model / "acoustic" / "vieneu_acoustic_weights.npz")
    hidden = np.fromfile(dumps / "native_prefill_h.f32", dtype=np.float32)
    text_emb = heads["text_emb"].astype(np.float32)

    H = int(config["hidden_size"])
    n_heads = int(config["local_num_attention_heads"])
    head_dim = H // n_heads
    eps = float(config.get("rms_norm_eps", 1e-6))
    sgs = int(config["speech_generation_start_token_id"])

    token = np.stack([hidden, text_emb[sgs]]).astype(np.float32)
    x_pos = (token + weights["slot_pos_emb"].astype(np.float32)[[0, 1]]).astype(np.float32)

    prefix = "layers.0."
    norm1 = rms_norm(x_pos, weights[prefix + "norm1"], eps)
    qkv = linear(norm1, weights[prefix + "attn.qkv"]).reshape(2, 3, n_heads, head_dim)
    q = rms_norm(qkv[:, 0], weights[prefix + "attn.q_norm"], eps)
    k = rms_norm(qkv[:, 1], weights[prefix + "attn.k_norm"], eps)
    v = qkv[:, 2].astype(np.float32)

    attention = np.zeros((2, H), dtype=np.float32)
    inv_scale = np.float32(1.0 / np.sqrt(float(head_dim)))
    for token_index in range(2):
        count = token_index + 1
        for head in range(n_heads):
            scores = (k[:count, head] @ q[token_index, head]).astype(np.float32) * inv_scale
            probability = softmax(scores)
            begin = head * head_dim
            attention[token_index, begin:begin + head_dim] = probability @ v[:count, head]

    projected = linear(attention, weights[prefix + "attn.o_proj"])
    after_attention = (x_pos + projected).astype(np.float32)
    norm2 = rms_norm(after_attention, weights[prefix + "norm2"], eps)
    gate = linear(norm2, weights[prefix + "ff_gate"])
    up = linear(norm2, weights[prefix + "ff_up"])
    down = linear(silu(gate) * up, weights[prefix + "ff_down"])
    after_layer = (after_attention + down).astype(np.float32)
    final = rms_norm(after_layer, weights["norm"], eps)

    expected = {
        "00_x_plus_position": x_pos,
        "01_norm1": norm1,
        "02_q_norm": q.reshape(2, H),
        "03_k_norm": k.reshape(2, H),
        "04_v": v.reshape(2, H),
        "05_attention": attention,
        "06_norm2": norm2,
        "07_x_after_layer": after_layer,
        "08_final_norm": final,
    }

    comparison: dict[str, dict] = {}
    first_divergence = None
    for stage, array in expected.items():
        native_name = (
            "native_acoustic_initial.f32"
            if stage == "08_final_norm"
            else f"native_trace_{stage}.f32"
        )
        native_path = dumps / native_name
        if not native_path.is_file():
            comparison[stage] = {"missing_native_dump": native_name}
            continue
        native = np.fromfile(native_path, dtype=np.float32).reshape(array.shape)
        result = metrics(native, array)
        comparison[stage] = result
        if first_divergence is None and (
            not result.get("shape_equal", False)
            or result.get("max_abs", 1.0) > 1e-4
            or result.get("cosine", 0.0) < 0.999999
        ):
            first_divergence = stage
        array.tofile(output.parent / f"numpy_trace_{stage}.f32")

    report = {
        "first_divergence": first_divergence,
        "comparison": comparison,
        "interpretation": {
            "00_x_plus_position": "input ordering or slot positional embedding",
            "01_norm1": "RMSNorm implementation or norm weight loading",
            "02_q_norm": "Q projection/reshape or Q RMSNorm",
            "03_k_norm": "K projection/reshape or K RMSNorm",
            "04_v": "V projection/reshape",
            "05_attention": "causal mask, head layout, cache or softmax",
            "06_norm2": "O projection/residual or second RMSNorm",
            "07_x_after_layer": "FFN gate/up/down or residual",
            "08_final_norm": "final RMSNorm",
        },
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
