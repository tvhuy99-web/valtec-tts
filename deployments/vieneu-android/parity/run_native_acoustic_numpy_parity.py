#!/usr/bin/env python3
"""Compare native C++ acoustic generation with an independent NumPy oracle.

Both paths consume the exact same native package hidden state, heads and
acoustic NPZ weights. This distinguishes a C++ acoustic math bug from an asset
revision mismatch against the official ONNX package.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def rms_norm(x: np.ndarray, weight: np.ndarray, eps: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    scale = np.float32(1.0) / np.sqrt(np.mean(x * x, axis=-1, keepdims=True, dtype=np.float32) + np.float32(eps))
    return (x * scale * weight).astype(np.float32)


def silu(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return (x / (np.float32(1.0) + np.exp(-x, dtype=np.float32))).astype(np.float32)


def linear(x: np.ndarray, weight: np.ndarray) -> np.ndarray:
    return np.matmul(np.asarray(x, dtype=np.float32), np.asarray(weight, dtype=np.float32).T).astype(np.float32)


def softmax(scores: np.ndarray) -> np.ndarray:
    scores = np.asarray(scores, dtype=np.float32)
    shifted = scores - np.max(scores)
    values = np.exp(shifted, dtype=np.float32)
    return (values / np.sum(values, dtype=np.float64)).astype(np.float32)


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
        S = x.shape[0]
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
            qkv = linear(normalized, qkv_w).reshape(S, 3, self.n_heads, self.head_dim)
            q = qkv[:, 0]
            k = qkv[:, 1]
            v = qkv[:, 2]
            q = rms_norm(q, q_norm_w, self.eps)
            k = rms_norm(k, k_norm_w, self.eps)

            past_k = self.cache_k[layer]
            past_v = self.cache_v[layer]
            all_k = np.concatenate([past_k, k.reshape(S, self.H)], axis=0)
            all_v = np.concatenate([past_v, v.reshape(S, self.H)], axis=0)
            past = past_k.shape[0]
            attended = np.zeros((S, self.H), dtype=np.float32)
            for s in range(S):
                attend_count = past + s + 1
                for head in range(self.n_heads):
                    qh = q[s, head]
                    kh = all_k[:attend_count].reshape(attend_count, self.n_heads, self.head_dim)[:, head]
                    vh = all_v[:attend_count].reshape(attend_count, self.n_heads, self.head_dim)[:, head]
                    scores = (kh @ qh).astype(np.float32) * inv_sqrt
                    probs = softmax(scores)
                    attended[s, head * self.head_dim:(head + 1) * self.head_dim] = probs @ vh

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
    slot0 = local[0].copy()

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

    report = {
        "native_cpp_codes": native_codes,
        "numpy_same_weights_codes": numpy_codes,
        "codes_exact": native_codes == numpy_codes,
        "first_code_mismatch": mismatch,
        "native_cpp_eos": native_eos,
        "numpy_same_weights_eos": numpy_eos,
        "eos_exact": native_eos == numpy_eos,
        "numpy_top1_margins": margins,
        "conclusion": (
            "native_acoustic_math_matches_its_exported_weights"
            if native_codes == numpy_codes and native_eos == numpy_eos
            else "native_acoustic_math_diverges_from_same_weight_numpy_oracle"
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
