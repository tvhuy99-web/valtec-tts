#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper


def sha256(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def metrics(left: np.ndarray, right: np.ndarray) -> dict:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape:
        return {"shape_equal": False, "left": list(left.shape), "right": list(right.shape)}
    delta = a - b
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return {
        "shape_equal": True,
        "count": int(a.size),
        "max_abs": float(np.max(np.abs(delta))) if a.size else 0.0,
        "mean_abs": float(np.mean(np.abs(delta))) if a.size else 0.0,
        "rmse": float(np.sqrt(np.mean(delta * delta))) if a.size else 0.0,
        "cosine": float(np.dot(a, b) / denom) if denom else None,
        "exact": bool(np.array_equal(left, right)),
        "allclose_1e_6": bool(np.allclose(left, right, rtol=1e-6, atol=1e-6)),
    }


def tokens(name: str) -> set[str]:
    aliases = {
        "attn": "attention",
        "qkv": "qkv",
        "proj": "projection",
        "ff": "mlp",
        "norm1": "norm1",
        "norm2": "norm2",
    }
    raw = re.findall(r"[a-z]+|\d+", name.lower())
    out = set(raw)
    out.update(aliases.get(item, item) for item in raw)
    return out


def tensor_record(name: str, array: np.ndarray, origin: str) -> dict:
    values = np.asarray(array)
    finite = values[np.isfinite(values)]
    return {
        "name": name,
        "origin": origin,
        "shape": list(values.shape),
        "dtype": str(values.dtype),
        "bytes": int(values.nbytes),
        "sha256": sha256(values),
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
        "mean": float(finite.mean()) if finite.size else None,
        "rms": float(np.sqrt(np.mean(finite.astype(np.float64) ** 2))) if finite.size else None,
    }


def load_onnx_tensors(model_path: Path) -> dict[str, tuple[np.ndarray, str]]:
    model = onnx.load(str(model_path), load_external_data=True)
    found: dict[str, tuple[np.ndarray, str]] = {}
    for initializer in model.graph.initializer:
        found[initializer.name] = (numpy_helper.to_array(initializer), "initializer")
    for node_index, node in enumerate(model.graph.node):
        if node.op_type != "Constant":
            continue
        for attribute in node.attribute:
            if attribute.type == onnx.AttributeProto.TENSOR:
                name = node.output[0] if node.output else f"constant_{node_index}"
                found[name] = (numpy_helper.to_array(attribute.t), "constant")
    return found


def best_candidates(source_name: str, source: np.ndarray,
                    onnx_tensors: dict[str, tuple[np.ndarray, str]]) -> list[dict]:
    source_tokens = tokens(source_name)
    ranked = []
    for candidate_name, (candidate, origin) in onnx_tensors.items():
        transforms: list[tuple[str, np.ndarray]] = []
        if candidate.shape == source.shape:
            transforms.append(("direct", candidate))
        if source.ndim == 2 and candidate.ndim == 2 and candidate.T.shape == source.shape:
            transforms.append(("transpose", candidate.T))
        if not transforms:
            continue
        overlap = len(source_tokens & tokens(candidate_name))
        for transform, values in transforms:
            comparison = metrics(source, values)
            ranked.append({
                "candidate": candidate_name,
                "origin": origin,
                "transform": transform,
                "name_token_overlap": overlap,
                "comparison": comparison,
                "candidate_sha256": sha256(candidate),
            })
    ranked.sort(
        key=lambda item: (
            0 if item["comparison"].get("exact") else 1,
            0 if item["comparison"].get("allclose_1e_6") else 1,
            -item["name_token_overlap"],
            item["comparison"].get("rmse", float("inf")),
        )
    )
    return ranked[:8]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx-dir", required=True, type=Path)
    parser.add_argument("--native-acoustic", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    model_path = args.onnx_dir / "vieneu_acoustic_cached.onnx"
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    onnx_model = onnx.load(str(model_path), load_external_data=False)
    tensors = load_onnx_tensors(model_path)
    native = np.load(args.native_acoustic)

    native_matches = {}
    for name in native.files:
        source = native[name]
        native_matches[name] = {
            "source": tensor_record(name, source, "native_npz"),
            "candidates": best_candidates(name, source, tensors),
        }

    report = {
        "schema": 1,
        "onnx_path": str(model_path),
        "onnx_ir_version": int(onnx_model.ir_version),
        "opsets": [
            {"domain": item.domain, "version": int(item.version)}
            for item in onnx_model.opset_import
        ],
        "inputs": [
            {"name": item.name, "type": str(item.type)} for item in onnx_model.graph.input
        ],
        "outputs": [
            {"name": item.name, "type": str(item.type)} for item in onnx_model.graph.output
        ],
        "operator_counts": {},
        "onnx_tensor_count": len(tensors),
        "onnx_tensors": [
            tensor_record(name, value, origin)
            for name, (value, origin) in sorted(tensors.items())
        ],
        "native_tensor_count": len(native.files),
        "native_matches": native_matches,
    }
    for node in onnx_model.graph.node:
        report["operator_counts"][node.op_type] = report["operator_counts"].get(node.op_type, 0) + 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {}
    for name, item in native_matches.items():
        top = item["candidates"][0] if item["candidates"] else None
        summary[name] = None if top is None else {
            "candidate": top["candidate"],
            "transform": top["transform"],
            "name_token_overlap": top["name_token_overlap"],
            "comparison": top["comparison"],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
