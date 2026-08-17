#!/usr/bin/env python3
"""Compare native C++ acoustic generation with an independent NumPy oracle.

Both paths consume the exact same native package hidden state, heads and
acoustic NPZ weights. Besides output codes, this audit compares the decoder
hidden tensors immediately after the two-token prefill and each incremental KV
cache step, locating the first internal divergence.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def rms_norm(x: np.ndarray, weight: np.ndarray, eps: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    scale = np.float32(1.0) / np.sqrt(
        np.mean(x * x, axis=-1, keepdims=True, dtype=np.float32) + np.float32(eps)
    )
    return (x * scale * weight).astype(np.float32)


def silu(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return (x / (np.float32(1.0) + np.exp(-x, dtype=np.float32))).astype(np.float32)


def linear(x: np.ndarray, weight: np.ndarray) -> np.ndarray:
    return np.matmul(
        np.asarray(x, dtype=np.float32), np.asarray(weight, dtype=np.float32).T
    ).astype(np.float32)


def softmax(scores: np.ndarray) -> np.ndarray:
    scores = np.asarray(scores, dtype=np.float32)
    shifted = scores - np.max(scores)
    values = np.exp(shifted, dtype=np.float32)
    return (values / np.sum(values, dtype=np.float64)).astype(np.float32)


def tensor_metrics(left: np.ndarray, right: np.ndarray) -> dict:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape:
        return {"shape_equal": False, "left_shape": list(a.shape), "right_shape": list(b.shape)}
    delta = a - b
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return {
        "shape_equal": True,
        "count": int(a.size),
        "max_abs": float(np.max(np.abs(delta))) if a.size else 0.0,
        "mean_abs": float(np.mean(np.abs(delta))) if a.size else 0.0,
        "rmse": float(np.sqrt(np.mean(delta * delta))) if a.size else 0.0,
        "cosine": float(np.dot(a, b) / denominator) if denominator > 0 else float("nan"),
    }


def read_native_frame0(path: Path) -> tuple[list[int], bool]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    return [int(value) for value in row["codes"].split(":")], bool(int(row["eos"]))


class NativeAcousticOracle:
    def __init__(self, config: dict, acoustic: np.lib.npyio.NpzFile):
        self.config = config
        self.acoustic = acoustic
        self.H = int(config["hidden_size"])
        self.n_heads = int(config["local_num_attention_heads"])
        self.head_dim = self.H // self.n_heads
        self.layers = int(config["local_num_hidden_layers"])
        self.eps = float(config.get("rms_norm_eps", 1e-6))
        self.slot_pos = acoustic["slot_pos_emb"].astype(np.float32)
        self.final_norm = acoustic["norm"].astype(np.float32)
        self.cache_k: list[np.ndarray] = []
        self.cache_v: list[np.ndarray] = []

    def reset(self) -> None:
        self.cache_k = [np.zeros((0, self.H), dtype=np.float32) for _ in range(self.layers)]
        self.cache_v = [np.zeros((0, self.H), dtype=np.float32) for _ in range(self.layers)]

    def step(self, token: np.ndarray, positions: list[int]) -> np.ndarray:
        x = np.asarray(token, dtype=np.float32).reshape(len(positions), self.H).copy()
        x += self.slot_pos[np.asarray(positions, dtype=np.int64)]
        sequence = x.shape[0]
        inv_sqrt = np.float32(1.0 / np.sqrt(float(self.head_dim)))

        for layer in range(self.layers):
            prefix = f"layers.{layer}."
            norm1 = self.acoustic[prefix + "norm1"].astype(np.float32)
            qkv_w = self.acoustic[prefix + "attn.qkv"].astype(np.float32)
            q_norm_w = self.acoustic[prefix + "attn.q_norm"].astype(np.float32)
            k_norm_w = self.acoustic[prefix + "attn.k_norm"].astype(np.float32)
            o_w = self.acoustic[prefix + "attn.o_proj"].astype(np.float32)
            norm2 = self.acoustic[prefix + "norm2"].astype(np.float32)
            gate_w = self.acoustic[prefix + "ff_gate"].astype(np.float32)
            up_w = self.acoustic[prefix + "ff_up"].astype(np.float32)
            down_w = self.acoustic[prefix + "ff_down"].astype(np.float32)

            normalized = rms_norm(x, norm1, self.eps)
            qkv = linear(normalized, qkv_w).reshape(
                sequence, 3, self.n_heads, self.head_dim
            )
            q = rms_norm(qkv[:, 0], q_norm_w, self.eps)
            k = rms_norm(qkv[:, 1], k_norm_w, self.eps)
            v = qkv[:, 2]

            past_k = self.cache_k[layer]
            past_v = self.cache_v[layer]
            all_k = np.concatenate([past_k, k.reshape(sequence, self.H)], axis=0)
            all_v = np.concatenate([past_v, v.reshape(sequence, self.H)], axis=0)
            past = past_k.shape[0]
            attended = np.zeros((sequence, self.H), dtype=np.float32)
            for token_index in range(sequence):
                attend_count = past + token_index + 1
                reshaped_k = all_k[:attend_count].reshape(
                    attend_count, self.n_heads, self.head_dim
                )
                reshaped_v = all_v[:attend_count].reshape(
                    attend_count, self.n_heads, self.head_dim
                )
                for head in range(self.n_heads):
                    scores = (reshaped_k[:, head] @ q[token_index, head]).astype(np.float32)
                    probabilities = softmax(scores * inv_sqrt)
                    begin = head * self.head_dim
                    end = begin + self.head_dim
                    attended[token_index, begin:end] = probabilities @ reshaped_v[:, head]

            self.cache_k[layer] = all_k
            self.cache_v[layer] = all_v
            x = (x + linear(attended, o_w)).astype(np.float32)
            n2 = rms_norm(x, norm2, self.eps)
            fused = silu(linear(n2, gate_w)) * linear(n2, up_w)
            x = (x + linear(fused, down_w)).astype(np.float32)

        return rms_norm(x, self.final_norm, self.eps)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-model-dir", required=True)
    parser.add_argument("--native-dump-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    model_dir = Path(args.native_model_dir)
    dump_dir = Path(args.native_dump_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    heads = np.load(model_dir / "vieneu_v3_heads.npz")
    acoustic = np.load(model_dir / "acoustic" / "vieneu_acoustic_weights.npz")
    text_emb = heads["text_emb"].astype(np.float32)
    audio_emb = heads["audio_emb"].astype(np.float32)
    hidden = np.fromfile(dump_dir / "native_prefill_h.f32", dtype=np.float32)
    native_codes, native_eos = read_native_frame0(dump_dir / "native_codes.csv")

    oracle = NativeAcousticOracle(config, acoustic)
    oracle.reset()
    sgs = int(config["speech_generation_start_token_id"])
    eos_id = int(config["speech_generation_end_token_id"])
    initial = np.stack([hidden, text_emb[sgs]])
    local = oracle.step(initial, [0, 1])
    local.astype(np.float32).tofile(output.parent / "numpy_acoustic_initial.f32")
    slot0 = local[0].copy()

    hidden_comparisons: dict[str, dict] = {}
    native_initial_path = dump_dir / "native_acoustic_initial.f32"
    if native_initial_path.is_file():
        native_initial = np.fromfile(native_initial_path, dtype=np.float32).reshape(local.shape)
        hidden_comparisons["initial_two_token_prefill"] = tensor_metrics(native_initial, local)

    numpy_codes: list[int] = []
    margins: list[float] = []
    logits = local[1] @ audio_emb[0].T
    order = np.argpartition(logits, -2)[-2:]
    best = int(order[np.argmax(logits[order])])
    second = int(order[np.argmin(logits[order])])
    numpy_codes.append(best)
    margins.append(float(logits[best] - logits[second]))

    for channel in range(1, int(config["n_vq"])):
        local = oracle.step(audio_emb[channel - 1, numpy_codes[-1]][None], [channel + 1])
        local.astype(np.float32).tofile(output.parent / f"numpy_acoustic_step_{channel:03d}.f32")
        native_step_path = dump_dir / f"native_acoustic_step_{channel:03d}.f32"
        if native_step_path.is_file():
            native_step = np.fromfile(native_step_path, dtype=np.float32).reshape(local.shape)
            hidden_comparisons[f"step_{channel:03d}"] = tensor_metrics(native_step, local)
        logits = local[0] @ audio_emb[channel].T
        order = np.argpartition(logits, -2)[-2:]
        best = int(order[np.argmax(logits[order])])
        second = int(order[np.argmin(logits[order])])
        numpy_codes.append(best)
        margins.append(float(logits[best] - logits[second]))

    numpy_eos = int(np.argmax(slot0 @ text_emb.T)) == eos_id
    mismatch = None
    for channel, (left, right) in enumerate(zip(native_codes, numpy_codes)):
        if left != right:
            mismatch = {
                "channel": channel,
                "native_cpp": left,
                "numpy_same_weights": right,
                "numpy_top1_margin": margins[channel],
            }
            break

    first_hidden_divergence = None
    for stage, values in hidden_comparisons.items():
        if (
            not values.get("shape_equal", False)
            or values.get("cosine", 0.0) < 0.999999
            or values.get("max_abs", 1.0) > 1.0e-4
        ):
            first_hidden_divergence = stage
            break

    report = {
        "native_cpp_codes": native_codes,
        "numpy_same_weights_codes": numpy_codes,
        "codes_exact": native_codes == numpy_codes,
        "first_code_mismatch": mismatch,
        "native_cpp_eos": native_eos,
        "numpy_same_weights_eos": numpy_eos,
        "eos_exact": native_eos == numpy_eos,
        "numpy_top1_margins": margins,
        "hidden_comparisons": hidden_comparisons,
        "first_hidden_divergence": first_hidden_divergence,
        "conclusion": (
            "native_acoustic_math_matches_its_exported_weights"
            if native_codes == numpy_codes and native_eos == numpy_eos
            else "native_acoustic_math_diverges_from_same_weight_numpy_oracle"
        ),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
