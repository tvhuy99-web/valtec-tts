#!/usr/bin/env python3
"""Repackage VieNeu's semantic backbone as a standard Qwen3 HF checkpoint.

The upstream native exporter writes GGUF metadata manually. This script first
builds a canonical Hugging Face Qwen3 checkpoint so llama.cpp's maintained
convert_hf_to_gguf.py owns all tensor-name and architecture metadata decisions.
It also exports the VieNeu embedding/head NPZ from the exact same safetensors.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import safetensors.torch
import torch


def require(sd: dict[str, torch.Tensor], key: str) -> torch.Tensor:
    if key not in sd:
        raise KeyError(f"missing checkpoint tensor: {key}")
    return sd[key].detach().cpu().float().contiguous()


def copy_tokenizer_files(source_dirs: list[Path], output_dir: Path) -> None:
    names = (
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "merges.txt",
        "vocab.json",
    )
    copied: set[str] = set()
    for source in source_dirs:
        for name in names:
            candidate = source / name
            if candidate.is_file() and name not in copied:
                shutil.copy2(candidate, output_dir / name)
                copied.add(name)
    if "tokenizer.json" not in copied:
        raise FileNotFoundError("tokenizer.json was not found in the supplied checkpoint directories")
    tokenizer_config = output_dir / "tokenizer_config.json"
    if not tokenizer_config.exists():
        tokenizer_config.write_text(
            json.dumps({"tokenizer_class": "PreTrainedTokenizerFast"}, indent=2) + "\n",
            encoding="utf-8",
        )


def save_heads(sd: dict[str, torch.Tensor], output_path: Path, n_vq: int) -> None:
    text_emb = require(sd, "text_embeddings.weight").numpy()
    audio_emb = np.stack(
        [require(sd, f"audio_embeddings.{channel}.weight").numpy() for channel in range(n_vq)],
        axis=0,
    )
    payload: dict[str, np.ndarray] = {
        "text_emb": text_emb,
        "audio_emb": audio_emb,
    }
    optional = {
        "xvec_w": "xvec_proj.0.weight",
        "xvec_b": "xvec_proj.0.bias",
        "xvec_ln_w": "xvec_proj.1.weight",
        "xvec_ln_b": "xvec_proj.1.bias",
    }
    for out_name, source_name in optional.items():
        if source_name in sd:
            payload[out_name] = require(sd, source_name).numpy()
    payload["xvec_ln_eps"] = np.asarray([1.0e-5], dtype=np.float32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output_path, **payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--safetensors", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--tokenizer-dir", action="append", default=[], type=Path)
    parser.add_argument("--output-hf-dir", required=True, type=Path)
    parser.add_argument("--output-heads", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    sd = safetensors.torch.load_file(str(args.safetensors), device="cpu")

    hidden = int(cfg.get("hidden_size", 768))
    layers = int(cfg.get("num_hidden_layers", 12))
    heads = int(cfg.get("num_attention_heads", 12))
    kv_heads = int(cfg.get("num_key_value_heads", 4))
    intermediate = int(cfg.get("intermediate_size", 3072))
    n_vq = int(cfg.get("n_vq", 16))
    if hidden <= 0 or layers <= 0 or heads <= 0 or kv_heads <= 0 or hidden % heads:
        raise ValueError("invalid Qwen3 architecture values in VieNeu config")

    text_emb = require(sd, "text_embeddings.weight")
    vocab_size = int(text_emb.shape[0])
    if int(text_emb.shape[1]) != hidden:
        raise ValueError(f"text embedding hidden mismatch: {tuple(text_emb.shape)} vs hidden={hidden}")

    mapped: dict[str, torch.Tensor] = {
        "model.embed_tokens.weight": text_emb,
        "model.norm.weight": require(sd, "semantic_backbone.norm.weight"),
        "lm_head.weight": text_emb.clone(),
    }
    for layer in range(layers):
        src = f"semantic_backbone.layers.{layer}"
        dst = f"model.layers.{layer}"
        mapped[f"{dst}.input_layernorm.weight"] = require(sd, f"{src}.input_layernorm.weight")
        mapped[f"{dst}.post_attention_layernorm.weight"] = require(sd, f"{src}.post_attention_layernorm.weight")
        for name in ("q_proj", "k_proj", "v_proj", "o_proj", "q_norm", "k_norm"):
            mapped[f"{dst}.self_attn.{name}.weight"] = require(sd, f"{src}.self_attn.{name}.weight")
        for name in ("gate_proj", "up_proj", "down_proj"):
            mapped[f"{dst}.mlp.{name}.weight"] = require(sd, f"{src}.mlp.{name}.weight")

    out = args.output_hf_dir
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    qwen_cfg = {
        "architectures": ["Qwen3ForCausalLM"],
        "model_type": "qwen3",
        "vocab_size": vocab_size,
        "hidden_size": hidden,
        "intermediate_size": intermediate,
        "num_hidden_layers": layers,
        "num_attention_heads": heads,
        "num_key_value_heads": kv_heads,
        "head_dim": hidden // heads,
        "hidden_act": "silu",
        "max_position_embeddings": int(cfg.get("max_position_embeddings", 2048)),
        "initializer_range": 0.02,
        "rms_norm_eps": float(cfg.get("rms_norm_eps", 1.0e-6)),
        "rope_theta": float(cfg.get("rope_theta", 1_000_000.0)),
        "attention_bias": False,
        "use_cache": True,
        "tie_word_embeddings": True,
        "torch_dtype": "float32",
        "bos_token_id": cfg.get("bos_token_id"),
        "eos_token_id": cfg.get("eos_token_id"),
    }
    (out / "config.json").write_text(json.dumps(qwen_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    safetensors.torch.save_file(mapped, str(out / "model.safetensors"), metadata={"format": "pt"})
    copy_tokenizer_files(list(args.tokenizer_dir) + [args.config.parent], out)
    save_heads(sd, args.output_heads, n_vq)

    tensor_shapes = {name: list(tensor.shape) for name, tensor in mapped.items()}
    metadata = {
        "schema": 1,
        "source_revision": args.source_revision,
        "source_safetensors": str(args.safetensors),
        "source_config": str(args.config),
        "architecture": qwen_cfg,
        "mapped_tensor_count": len(mapped),
        "mapped_tensor_shapes": tensor_shapes,
        "heads_file": str(args.output_heads),
        "n_vq": n_vq,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "mapped_tensors": len(mapped), "vocab": vocab_size, "layers": layers}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
