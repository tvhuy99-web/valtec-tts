#!/usr/bin/env python3
"""Compare the native/NumPy acoustic path with the official PyTorch F32 model.

The published ``onnx_update`` acoustic graph is dynamically quantized int8, so
its exact token sequence is not an appropriate quality gate for the full-F32
native package. This audit loads the official model definition and the pinned
``update/model.safetensors`` checkpoint, then compares every cached acoustic
hidden state and greedy codebook decision while forcing the same previous codes.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import safetensors.torch
import torch


def load_official_classes(source: Path):
    vieneu_dir = source / "src" / "vieneu"
    engine_dir = vieneu_dir / "_v3_turbo_engine"
    package = types.ModuleType("vieneu")
    package.__path__ = [str(vieneu_dir)]
    sys.modules["vieneu"] = package
    subpackage = types.ModuleType("vieneu._v3_turbo_engine")
    subpackage.__path__ = [str(engine_dir)]
    sys.modules["vieneu._v3_turbo_engine"] = subpackage

    def load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"unable to load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    config_module = load(
        "vieneu._v3_turbo_engine.configuration_v3_turbo",
        engine_dir / "configuration_v3_turbo.py",
    )
    model_module = load(
        "vieneu._v3_turbo_engine.modeling_v3_turbo",
        engine_dir / "modeling_v3_turbo.py",
    )
    return config_module.VieNeuV3TurboConfig, model_module.AcousticDecoder


def find_tensor(state: dict[str, torch.Tensor], suffix: str) -> torch.Tensor:
    if suffix in state:
        return state[suffix]
    matches = [name for name in state if name.endswith(suffix)]
    if len(matches) != 1:
        raise KeyError(f"expected one checkpoint tensor ending with {suffix!r}, found {matches}")
    return state[matches[0]]


def metrics(left: np.ndarray, right: np.ndarray) -> dict:
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
        "cosine": float(np.dot(a, b) / denominator) if denominator else None,
    }


def top(values: np.ndarray, count: int = 5) -> list[dict]:
    flat = np.asarray(values).reshape(-1)
    indices = np.argsort(flat)[::-1][:count]
    return [{"id": int(index), "logit": float(flat[index])} for index in indices]


def read_native_frame(path: Path) -> tuple[list[int], bool]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    return [int(value) for value in row["codes"].split(":")], bool(int(row["eos"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-source", required=True, type=Path)
    parser.add_argument("--safetensors", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--native-dump-dir", required=True, type=Path)
    parser.add_argument("--numpy-dump-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    torch.set_grad_enabled(False)
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)

    config_class, decoder_class = load_official_classes(args.official_source)
    config_data = json.loads(args.config.read_text(encoding="utf-8"))
    config = config_class(**config_data)
    state = safetensors.torch.load_file(str(args.safetensors), device="cpu")

    decoder = decoder_class(config).cpu().float().eval()
    acoustic_state: dict[str, torch.Tensor] = {
        "slot_pos_emb.weight": find_tensor(state, "acoustic_decoder.slot_pos_emb.weight").float(),
        "norm.weight": find_tensor(state, "acoustic_decoder.norm.weight").float(),
    }
    for layer in range(int(config.local_num_hidden_layers)):
        source = f"acoustic_decoder.layers.{layer}"
        mappings = {
            "norm1.weight": "norm1.weight",
            "attn.qkv.weight": "attn.qkv.weight",
            "attn.q_norm.weight": "attn.q_norm.weight",
            "attn.k_norm.weight": "attn.k_norm.weight",
            "attn.o_proj.weight": "attn.o_proj.weight",
            "norm2.weight": "norm2.weight",
            "ff_up.weight": "ff_up.weight",
            "ff_gate.weight": "ff_gate.weight",
            "ff_down.weight": "ff_down.weight",
        }
        for destination, source_suffix in mappings.items():
            acoustic_state[f"layers.{layer}.{destination}"] = find_tensor(
                state, f"{source}.{source_suffix}"
            ).float()
    decoder.load_state_dict(acoustic_state, strict=True)

    text_embedding = find_tensor(state, "text_embeddings.weight").float().cpu()
    audio_embeddings = torch.stack(
        [
            find_tensor(state, f"audio_embeddings.{channel}.weight").float().cpu()
            for channel in range(int(config.n_vq))
        ],
        dim=0,
    )

    native_codes, native_eos = read_native_frame(args.native_dump_dir / "native_codes.csv")
    semantic_hidden = torch.from_numpy(
        np.fromfile(args.native_dump_dir / "native_prefill_h.f32", dtype=np.float32)
    ).reshape(1, -1)
    hidden_size = int(config.hidden_size)
    layers = int(config.local_num_hidden_layers)
    start_id = int(config.speech_generation_start_token_id)
    eos_id = int(config.speech_generation_end_token_id)

    token = torch.stack([semantic_hidden[0], text_embedding[start_id]], dim=0).reshape(
        1, 2, hidden_size
    )
    position = torch.tensor([0, 1], dtype=torch.long)
    output, past_k, past_v = decoder.cached_step(token, position, [None] * layers, [None] * layers)
    numpy_initial = np.fromfile(
        args.numpy_dump_dir / "numpy_acoustic_initial.f32", dtype=np.float32
    ).reshape(1, 2, hidden_size)
    initial_metrics = metrics(output.detach().cpu().numpy(), numpy_initial)
    slot0 = output[0, 0].detach().cpu()

    code_steps: list[dict] = []
    pytorch_codes: list[int] = []
    margins: list[float] = []

    for channel in range(int(config.n_vq)):
        if channel == 0:
            vector = output[0, 1]
            numpy_vector = numpy_initial[0, 1]
            previous_code = None
        else:
            previous_code = int(native_codes[channel - 1])
            embedding = audio_embeddings[channel - 1, previous_code].reshape(1, 1, hidden_size)
            position = torch.tensor([channel + 1], dtype=torch.long)
            output, past_k, past_v = decoder.cached_step(
                embedding, position, past_k, past_v
            )
            vector = output[0, 0]
            numpy_vector = np.fromfile(
                args.numpy_dump_dir / f"numpy_acoustic_step_{channel:03d}.f32",
                dtype=np.float32,
            ).reshape(1, 1, hidden_size)[0, 0]

        logits = torch.mv(audio_embeddings[channel], vector).detach().cpu().numpy()
        numpy_logits = np.asarray(numpy_vector, dtype=np.float32) @ (
            audio_embeddings[channel].detach().cpu().numpy().T
        )
        order = np.argsort(logits)[::-1]
        code = int(order[0])
        margin = float(logits[order[0]] - logits[order[1]])
        pytorch_codes.append(code)
        margins.append(margin)
        code_steps.append(
            {
                "channel": channel,
                "forced_previous_code": previous_code,
                "hidden": metrics(vector.detach().cpu().numpy(), numpy_vector),
                "logits": metrics(logits, numpy_logits),
                "pytorch_argmax": code,
                "numpy_argmax": int(np.argmax(numpy_logits)),
                "native_code": int(native_codes[channel]),
                "pytorch_top": top(logits),
                "numpy_top": top(numpy_logits),
                "pytorch_top1_margin": margin,
            }
        )

    pytorch_eos = int(torch.argmax(torch.mv(text_embedding, slot0)).item()) == eos_id
    first_hidden_divergence = next(
        (
            item["channel"]
            for item in code_steps
            if item["hidden"].get("max_abs", 1.0) > 1.0e-4
            or item["hidden"].get("cosine", 0.0) < 0.999999
        ),
        None,
    )
    first_code_mismatch = next(
        (
            {
                "channel": channel,
                "native": int(native),
                "pytorch": int(reference),
                "margin": margins[channel],
            }
            for channel, (native, reference) in enumerate(zip(native_codes, pytorch_codes))
            if native != reference
        ),
        None,
    )

    report = {
        "schema": 1,
        "reference": "official_pytorch_f32_update_model_safetensors",
        "initial_two_token_prefill": initial_metrics,
        "native_codes": native_codes,
        "pytorch_codes": pytorch_codes,
        "codes_exact": native_codes == pytorch_codes,
        "first_code_mismatch": first_code_mismatch,
        "native_eos": native_eos,
        "pytorch_eos": pytorch_eos,
        "eos_exact": native_eos == pytorch_eos,
        "top1_margins": margins,
        "code_steps": code_steps,
        "first_hidden_divergence": first_hidden_divergence,
        "conclusion": (
            "native_acoustic_matches_official_pytorch_f32"
            if native_codes == pytorch_codes
            and native_eos == pytorch_eos
            and first_hidden_divergence is None
            else "native_acoustic_diverges_from_official_pytorch_f32"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "initial_two_token_prefill": initial_metrics,
                "native_codes": native_codes,
                "pytorch_codes": pytorch_codes,
                "codes_exact": report["codes_exact"],
                "eos_exact": report["eos_exact"],
                "first_hidden_divergence": first_hidden_divergence,
                "first_code_mismatch": first_code_mismatch,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
