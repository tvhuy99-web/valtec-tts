#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def resolve_report(path: Path, alternate_name: str) -> Path:
    if path.is_file():
        return path
    alternate = path.parent / alternate_name
    if alternate.is_file():
        return alternate
    raise FileNotFoundError(f"parity report not found: {path} or {alternate}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--same-asset-report", required=True, type=Path)
    parser.add_argument("--acoustic-report", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--min-backbone-cosine", type=float, default=0.99999)
    parser.add_argument("--max-backbone-max-abs", type=float, default=1.0e-3)
    args = parser.parse_args()

    same_path = resolve_report(args.same_asset_report, "same-asset-report.json")
    acoustic_path = resolve_report(args.acoustic_report, "report.json")
    same = json.loads(same_path.read_text(encoding="utf-8"))
    acoustic = json.loads(acoustic_path.read_text(encoding="utf-8"))

    hidden = same["same_prompt_backbone_hidden"]
    frame_exact = bool(same.get("frame0_codes_exact"))
    acoustic_exact = bool(acoustic.get("codes_exact"))
    eos_exact = bool(acoustic.get("eos_exact"))
    checks = {
        "backbone_cosine": float(hidden["cosine"]) >= args.min_backbone_cosine,
        "backbone_max_abs": float(hidden["max_abs"]) <= args.max_backbone_max_abs,
        "official_frame0_codes": frame_exact,
        "native_vs_numpy_codes": acoustic_exact,
        "native_vs_numpy_eos": eos_exact,
    }
    summary = {
        "schema": 1,
        "pass": all(checks.values()),
        "inputs": {
            "same_asset_report": str(same_path),
            "acoustic_report": str(acoustic_path),
        },
        "checks": checks,
        "thresholds": {
            "min_backbone_cosine": args.min_backbone_cosine,
            "max_backbone_max_abs": args.max_backbone_max_abs,
        },
        "measurements": {
            "backbone_hidden": hidden,
            "native_frame0_codes": same.get("native_frame0_codes"),
            "official_frame0_codes": same.get("official_onnx_same_hidden_and_heads_frame0_codes"),
            "native_numpy_first_mismatch": acoustic.get("first_code_mismatch"),
            "first_runtime_divergence": same.get("first_runtime_divergence"),
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["pass"]:
        failed = ", ".join(name for name, ok in checks.items() if not ok)
        raise SystemExit(f"strict model parity failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
