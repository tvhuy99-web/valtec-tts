#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def resolve_report(path: Path, alternate_name: str) -> Path:
    if path.is_file():
        return path
    alternate = path.parent / alternate_name
    if alternate.is_file():
        return alternate
    raise FileNotFoundError(f"parity report not found: {path} or {alternate}")


def run_acoustic_weight_audit(summary_path: Path) -> Path | None:
    onnx_dir = Path("models/official/onnx_update")
    native_acoustic = Path("models/pinned/acoustic/vieneu_acoustic_weights.npz")
    script = Path(__file__).with_name("audit_acoustic_onnx_weights.py")
    if not onnx_dir.is_dir() or not native_acoustic.is_file() or not script.is_file():
        return None
    output = summary_path.parent / "acoustic-onnx-weight-audit.json"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--onnx-dir",
            str(onnx_dir),
            "--native-acoustic",
            str(native_acoustic),
            "--output",
            str(output),
        ],
        check=True,
    )
    return output


def run_acoustic_state_audit(summary_path: Path) -> Path | None:
    required = [
        Path("official-python"),
        Path("models/official/onnx_update"),
        Path("models/pinned"),
        Path("evidence/native/native_prefill_h.f32"),
        Path("evidence/acoustic-numpy/numpy_acoustic_initial.f32"),
    ]
    script = Path(__file__).with_name("run_acoustic_onnx_state_parity.py")
    if not all(path.exists() for path in required) or not script.is_file():
        return None
    output = summary_path.parent / "acoustic-onnx-state-parity.json"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--official-source",
            "official-python",
            "--onnx-dir",
            "models/official/onnx_update",
            "--native-model-dir",
            "models/pinned",
            "--native-dump-dir",
            "evidence/native",
            "--numpy-dump-dir",
            "evidence/acoustic-numpy",
            "--output",
            str(output),
        ],
        check=True,
    )
    return output


def find_one(root: Path, name: str, preferred_fragment: str | None = None) -> Path | None:
    candidates = sorted(path for path in root.rglob(name) if path.is_file())
    if preferred_fragment:
        preferred = [path for path in candidates if preferred_fragment in path.as_posix()]
        if preferred:
            return preferred[0]
    return candidates[0] if candidates else None


def run_pytorch_f32_audit(summary_path: Path) -> Path:
    safetensors_path = find_one(Path("models/official"), "model.safetensors", "/update/")
    config_path = find_one(Path("models/official"), "config.json", "/update/")
    script = Path(__file__).with_name("run_official_pytorch_acoustic_parity.py")
    required = [
        Path("official-python"),
        Path("evidence/native/native_prefill_h.f32"),
        Path("evidence/acoustic-numpy/numpy_acoustic_initial.f32"),
        script,
    ]
    if safetensors_path is None or config_path is None or not all(path.exists() for path in required):
        raise FileNotFoundError("official PyTorch F32 acoustic parity inputs are incomplete")
    output = summary_path.parent / "acoustic-pytorch-f32-parity.json"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--official-source",
            "official-python",
            "--safetensors",
            str(safetensors_path),
            "--config",
            str(config_path),
            "--native-dump-dir",
            "evidence/native",
            "--numpy-dump-dir",
            "evidence/acoustic-numpy",
            "--output",
            str(output),
        ],
        check=True,
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--same-asset-report", required=True, type=Path)
    parser.add_argument("--acoustic-report", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--min-backbone-cosine", type=float, default=0.99999)
    parser.add_argument("--max-backbone-max-abs", type=float, default=1.0e-3)
    parser.add_argument("--min-pytorch-hidden-cosine", type=float, default=0.999999)
    parser.add_argument("--max-pytorch-hidden-max-abs", type=float, default=1.0e-4)
    args = parser.parse_args()

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    weight_audit_path = run_acoustic_weight_audit(args.summary)
    onnx_state_path = run_acoustic_state_audit(args.summary)
    pytorch_path = run_pytorch_f32_audit(args.summary)

    same_path = resolve_report(args.same_asset_report, "same-asset-report.json")
    acoustic_path = resolve_report(args.acoustic_report, "report.json")
    same = json.loads(same_path.read_text(encoding="utf-8"))
    acoustic = json.loads(acoustic_path.read_text(encoding="utf-8"))
    pytorch = json.loads(pytorch_path.read_text(encoding="utf-8"))

    hidden = same["same_prompt_backbone_hidden"]
    pytorch_hidden_metrics = [pytorch["initial_two_token_prefill"]] + [
        item["hidden"] for item in pytorch.get("code_steps", [])
    ]
    pytorch_hidden_ok = all(
        item.get("shape_equal", False)
        and float(item.get("cosine", 0.0)) >= args.min_pytorch_hidden_cosine
        and float(item.get("max_abs", 1.0)) <= args.max_pytorch_hidden_max_abs
        for item in pytorch_hidden_metrics
    )

    checks = {
        "backbone_cosine": float(hidden["cosine"]) >= args.min_backbone_cosine,
        "backbone_max_abs": float(hidden["max_abs"]) <= args.max_backbone_max_abs,
        "native_vs_numpy_codes": bool(acoustic.get("codes_exact")),
        "native_vs_numpy_eos": bool(acoustic.get("eos_exact")),
        "native_vs_official_pytorch_codes": bool(pytorch.get("codes_exact")),
        "native_vs_official_pytorch_eos": bool(pytorch.get("eos_exact")),
        "native_vs_official_pytorch_hidden": pytorch_hidden_ok,
    }
    quantized_onnx_exact = bool(same.get("frame0_codes_exact"))
    summary = {
        "schema": 2,
        "reference_policy": {
            "semantic_backbone": "official_fp32_onnx_update",
            "acoustic_quality": "official_pytorch_f32_update_model_safetensors",
            "acoustic_onnx": "informational_only_because_graph_uses_dynamic_int8_matmul",
        },
        "pass": all(checks.values()),
        "inputs": {
            "same_asset_report": str(same_path),
            "acoustic_report": str(acoustic_path),
            "acoustic_pytorch_f32_parity": str(pytorch_path),
            "acoustic_onnx_weight_audit": str(weight_audit_path) if weight_audit_path else None,
            "acoustic_onnx_state_parity": str(onnx_state_path) if onnx_state_path else None,
        },
        "checks": checks,
        "informational_checks": {
            "native_vs_quantized_onnx_frame0_codes": quantized_onnx_exact,
        },
        "thresholds": {
            "min_backbone_cosine": args.min_backbone_cosine,
            "max_backbone_max_abs": args.max_backbone_max_abs,
            "min_pytorch_hidden_cosine": args.min_pytorch_hidden_cosine,
            "max_pytorch_hidden_max_abs": args.max_pytorch_hidden_max_abs,
        },
        "measurements": {
            "backbone_hidden": hidden,
            "native_frame0_codes": same.get("native_frame0_codes"),
            "quantized_onnx_frame0_codes": same.get("official_onnx_same_hidden_and_heads_frame0_codes"),
            "official_pytorch_frame0_codes": pytorch.get("pytorch_codes"),
            "native_numpy_first_mismatch": acoustic.get("first_code_mismatch"),
            "native_pytorch_first_mismatch": pytorch.get("first_code_mismatch"),
            "pytorch_first_hidden_divergence": pytorch.get("first_hidden_divergence"),
            "quantized_onnx_first_runtime_divergence": same.get("first_runtime_divergence"),
        },
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["pass"]:
        failed = ", ".join(name for name, ok in checks.items() if not ok)
        raise SystemExit(f"strict model parity failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
