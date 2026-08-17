#!/usr/bin/env python3
"""Build one internally consistent VieNeu native model core from one checkpoint.

The semantic backbone is first repackaged as a canonical Hugging Face Qwen3
checkpoint so llama.cpp's maintained converter owns all GGUF metadata and tensor
layout decisions. Heads and acoustic weights are extracted from the very same
safetensors file, preventing mixed-checkpoint native packages.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

import numpy as np
import safetensors.torch
import sentencepiece as spm
import torch


def require(sd: dict[str, torch.Tensor], key: str) -> torch.Tensor:
    if key not in sd:
        raise KeyError(f"missing checkpoint tensor: {key}")
    return sd[key].detach().cpu().float().contiguous()


def require_suffix(sd: dict[str, torch.Tensor], suffix: str) -> torch.Tensor:
    if suffix in sd:
        return require(sd, suffix)
    matches = [key for key in sd if key.endswith(suffix)]
    if len(matches) != 1:
        raise KeyError(f"expected one tensor ending with {suffix!r}, found {matches}")
    return require(sd, matches[0])


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


def create_loader_only_sentencepiece(output_dir: Path, vocab_size: int) -> None:
    """Create a GGUF loader vocabulary; VieNeu never asks llama.cpp to tokenize.

    The semantic backbone is driven exclusively through llama_batch.embd. The
    actual VieNeu tokenizer remains tokenizer.json and runs in VieNeu code. A
    deterministic SentencePiece model is therefore safe here and avoids making
    llama.cpp guess an unknown custom pre-tokenizer.
    """
    target = output_dir / "tokenizer.model"
    if target.exists():
        return
    corpus = output_dir / "loader_vocab_corpus.txt"
    alphabet = "".join(chr(0x4E00 + i) for i in range(256))
    lines = []
    for shift in range(256):
        rotated = alphabet[shift:] + alphabet[:shift]
        lines.append(rotated)
        lines.append(rotated[::2] + rotated[1::2])
    corpus.write_text("\n".join(lines) + "\n", encoding="utf-8")
    prefix = output_dir / "loader_tokenizer"
    spm.SentencePieceTrainer.train(
        input=str(corpus),
        model_prefix=str(prefix),
        vocab_size=vocab_size,
        model_type="bpe",
        character_coverage=1.0,
        hard_vocab_limit=True,
        bos_id=-1,
        eos_id=-1,
        pad_id=-1,
        unk_id=0,
        normalization_rule_name="identity",
        split_by_whitespace=False,
        remove_extra_whitespaces=False,
        shuffle_input_sentence=False,
        input_sentence_size=0,
    )
    shutil.move(str(prefix) + ".model", target)
    Path(str(prefix) + ".vocab").unlink(missing_ok=True)
    corpus.unlink(missing_ok=True)
    processor = spm.SentencePieceProcessor(model_file=str(target))
    if processor.get_piece_size() != vocab_size:
        raise ValueError(
            f"loader-only SentencePiece vocab mismatch: {processor.get_piece_size()} != {vocab_size}"
        )


def save_npz_stored(path: Path, arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, array in arrays.items():
            buffer = io.BytesIO()
            np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
            archive.writestr(f"{name}.npy", buffer.getvalue())


def save_heads(sd: dict[str, torch.Tensor], output_path: Path, n_vq: int) -> None:
    text_emb = require_suffix(sd, "text_embeddings.weight").numpy()
    audio_emb = np.stack(
        [require_suffix(sd, f"audio_embeddings.{channel}.weight").numpy() for channel in range(n_vq)],
        axis=0,
    )
    payload: dict[str, np.ndarray] = {"text_emb": text_emb, "audio_emb": audio_emb}
    optional = {
        "xvec_w": "xvec_proj.0.weight",
        "xvec_b": "xvec_proj.0.bias",
        "xvec_ln_w": "xvec_proj.1.weight",
        "xvec_ln_b": "xvec_proj.1.bias",
    }
    for output_name, suffix in optional.items():
        matches = [key for key in sd if key == suffix or key.endswith(suffix)]
        if len(matches) == 1:
            payload[output_name] = require(sd, matches[0]).numpy()
    payload["xvec_ln_eps"] = np.asarray([1.0e-5], dtype=np.float32)
    save_npz_stored(output_path, payload)


def save_acoustic(sd: dict[str, torch.Tensor], output_path: Path) -> int:
    layer_pattern = re.compile(r"(?:^|\.)acoustic_decoder\.layers\.(\d+)\.norm1\.weight$")
    layer_ids = sorted(
        {
            int(match.group(1))
            for key in sd
            if (match := layer_pattern.search(key)) is not None
        }
    )
    if not layer_ids or layer_ids != list(range(layer_ids[-1] + 1)):
        raise ValueError(f"invalid acoustic layer sequence detected: {layer_ids}")

    arrays: dict[str, np.ndarray] = {
        "slot_pos_emb": require_suffix(sd, "acoustic_decoder.slot_pos_emb.weight").numpy(),
        "norm": require_suffix(sd, "acoustic_decoder.norm.weight").numpy(),
    }
    for layer in layer_ids:
        source = f"acoustic_decoder.layers.{layer}"
        output = f"layers.{layer}."
        mappings = {
            "norm1": "norm1.weight",
            "attn.qkv": "attn.qkv.weight",
            "attn.q_norm": "attn.q_norm.weight",
            "attn.k_norm": "attn.k_norm.weight",
            "attn.o_proj": "attn.o_proj.weight",
            "norm2": "norm2.weight",
            "ff_up": "ff_up.weight",
            "ff_gate": "ff_gate.weight",
            "ff_down": "ff_down.weight",
        }
        for output_name, source_name in mappings.items():
            arrays[output + output_name] = require_suffix(sd, f"{source}.{source_name}").numpy()
    save_npz_stored(output_path, arrays)
    return len(layer_ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--safetensors", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--tokenizer-dir", action="append", default=[], type=Path)
    parser.add_argument("--output-hf-dir", required=True, type=Path)
    parser.add_argument("--output-heads", required=True, type=Path)
    parser.add_argument("--output-acoustic", required=True, type=Path)
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

    text_emb = require_suffix(sd, "text_embeddings.weight")
    vocab_size = int(text_emb.shape[0])
    if int(text_emb.shape[1]) != hidden:
        raise ValueError(f"text embedding hidden mismatch: {tuple(text_emb.shape)} vs hidden={hidden}")

    mapped: dict[str, torch.Tensor] = {
        "model.embed_tokens.weight": text_emb,
        "model.norm.weight": require_suffix(sd, "semantic_backbone.norm.weight"),
        "lm_head.weight": text_emb.clone(),
    }
    for layer in range(layers):
        source = f"semantic_backbone.layers.{layer}"
        output = f"model.layers.{layer}"
        mapped[f"{output}.input_layernorm.weight"] = require_suffix(sd, f"{source}.input_layernorm.weight")
        mapped[f"{output}.post_attention_layernorm.weight"] = require_suffix(sd, f"{source}.post_attention_layernorm.weight")
        for name in ("q_proj", "k_proj", "v_proj", "o_proj", "q_norm", "k_norm"):
            mapped[f"{output}.self_attn.{name}.weight"] = require_suffix(sd, f"{source}.self_attn.{name}.weight")
        for name in ("gate_proj", "up_proj", "down_proj"):
            mapped[f"{output}.mlp.{name}.weight"] = require_suffix(sd, f"{source}.mlp.{name}.weight")

    output_hf = args.output_hf_dir
    if output_hf.exists():
        shutil.rmtree(output_hf)
    output_hf.mkdir(parents=True)
    qwen_config = {
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
    (output_hf / "config.json").write_text(
        json.dumps(qwen_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    safetensors.torch.save_file(
        mapped,
        str(output_hf / "model.safetensors"),
        metadata={"format": "pt", "source_revision": args.source_revision},
    )
    copy_tokenizer_files(list(args.tokenizer_dir) + [args.config.parent], output_hf)
    create_loader_only_sentencepiece(output_hf, vocab_size)
    save_heads(sd, args.output_heads, n_vq)
    acoustic_layers = save_acoustic(sd, args.output_acoustic)

    metadata = {
        "schema": 1,
        "source_revision": args.source_revision,
        "source_safetensors": str(args.safetensors),
        "source_config": str(args.config),
        "architecture": qwen_config,
        "mapped_tensor_count": len(mapped),
        "mapped_tensor_shapes": {name: list(tensor.shape) for name, tensor in mapped.items()},
        "heads_file": str(args.output_heads),
        "acoustic_file": str(args.output_acoustic),
        "acoustic_layers": acoustic_layers,
        "n_vq": n_vq,
        "gguf_vocab": "loader-only-sentencepiece-external-embeddings",
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_hf),
                "mapped_tensors": len(mapped),
                "vocab": vocab_size,
                "backbone_layers": layers,
                "acoustic_layers": acoustic_layers,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
