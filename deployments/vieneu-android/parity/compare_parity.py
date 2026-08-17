#!/usr/bin/env python3
"""Compare native C++ parity dumps with the official ONNX update pipeline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def metrics(left: np.ndarray, right: np.ndarray) -> dict:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape:
        return {"shape_equal": False, "left_shape": list(a.shape), "right_shape": list(b.shape)}
    delta = a - b
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    cosine = float(np.dot(a, b) / denom) if denom > 0.0 else float("nan")
    return {
        "shape_equal": True,
        "count": int(a.size),
        "max_abs": float(np.max(np.abs(delta))) if a.size else 0.0,
        "mean_abs": float(np.mean(np.abs(delta))) if a.size else 0.0,
        "rmse": float(np.sqrt(np.mean(delta * delta))) if a.size else 0.0,
        "cosine": cosine,
    }


def read_codes(path: Path) -> tuple[np.ndarray, np.ndarray]:
    frames: list[list[int]] = []
    eos: list[int] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            frames.append([int(value) for value in row["codes"].split(":") if value])
            eos.append(int(row["eos"]))
    return np.asarray(frames, dtype=np.int64), np.asarray(eos, dtype=np.int64)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-dir", required=True)
    parser.add_argument("--reference-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    native = Path(args.native_dir)
    reference = Path(args.reference_dir)
    native_meta = json.loads((native / "native_meta.json").read_text(encoding="utf-8"))
    ref_meta = json.loads((reference / "reference_meta.json").read_text(encoding="utf-8"))

    native_rows = np.fromfile(native / "native_rows.i64", dtype=np.int64).reshape(
        int(native_meta["rows"]), int(native_meta["cols"])
    )
    ref_rows = np.load(reference / "reference_rows.npy")
    hidden = int(native_meta["hidden"])
    native_anchor = np.fromfile(native / "native_anchor.f32", dtype=np.float32)
    ref_anchor = np.load(reference / "reference_anchor.npy")
    native_prompt = np.fromfile(native / "native_prompt.f32", dtype=np.float32).reshape(
        int(native_meta["rows"]), hidden
    )
    ref_prompt = np.load(reference / "reference_prompt.npy")[0]
    native_prefill = np.fromfile(native / "native_prefill_h.f32", dtype=np.float32)
    ref_prefill = np.load(reference / "reference_prefill_h.npy").reshape(-1)
    native_codes, native_eos = read_codes(native / "native_codes.csv")
    ref_codes, ref_eos = read_codes(reference / "reference_codes.csv")

    common_frames = min(native_codes.shape[0], ref_codes.shape[0])
    common_channels = min(native_codes.shape[1] if native_codes.ndim == 2 else 0,
                          ref_codes.shape[1] if ref_codes.ndim == 2 else 0)
    first_code_mismatch = None
    if common_frames and common_channels:
        mismatch = np.argwhere(
            native_codes[:common_frames, :common_channels] != ref_codes[:common_frames, :common_channels]
        )
        if mismatch.size:
            first_code_mismatch = {
                "frame": int(mismatch[0, 0]),
                "channel": int(mismatch[0, 1]),
                "native": int(native_codes[tuple(mismatch[0])]),
                "reference": int(ref_codes[tuple(mismatch[0])]),
            }

    report = {
        "native_meta": native_meta,
        "reference_meta": ref_meta,
        "rows_exact": bool(np.array_equal(native_rows, ref_rows)),
        "row_mismatch_count": int(np.count_nonzero(native_rows != ref_rows))
        if native_rows.shape == ref_rows.shape else None,
        "anchor": metrics(native_anchor, ref_anchor),
        "prompt": metrics(native_prompt, ref_prompt),
        "prefill_hidden": metrics(native_prefill, ref_prefill),
        "native_code_shape": list(native_codes.shape),
        "reference_code_shape": list(ref_codes.shape),
        "first_code_mismatch": first_code_mismatch,
        "common_code_values_equal": bool(
            common_frames
            and common_channels
            and np.array_equal(
                native_codes[:common_frames, :common_channels],
                ref_codes[:common_frames, :common_channels],
            )
        ),
        "native_eos_frames": np.flatnonzero(native_eos).astype(int).tolist(),
        "reference_eos_frames": np.flatnonzero(ref_eos).astype(int).tolist(),
        "decode_hidden": {},
    }

    for path in sorted(native.glob("native_decode_h_*.f32")):
        suffix = path.stem.rsplit("_", 1)[-1]
        ref_path = reference / f"reference_decode_h_{suffix}.npy"
        if not ref_path.is_file():
            continue
        report["decode_hidden"][suffix] = metrics(
            np.fromfile(path, dtype=np.float32), np.load(ref_path).reshape(-1)
        )
        if len(report["decode_hidden"]) >= 5:
            break

    # Identify the first stage that is already outside a conservative fp32 parity tolerance.
    if not report["rows_exact"]:
        first_divergence = "prompt_rows"
    elif (not report["anchor"].get("shape_equal") or
          report["anchor"].get("max_abs", 1.0) > 1.0e-4):
        first_divergence = "speaker_anchor_or_exported_heads"
    elif (not report["prompt"].get("shape_equal") or
          report["prompt"].get("max_abs", 1.0) > 2.0e-4):
        first_divergence = "prompt_embedding"
    elif (not report["prefill_hidden"].get("shape_equal") or
          report["prefill_hidden"].get("cosine", 0.0) < 0.999 or
          report["prefill_hidden"].get("max_abs", 1.0) > 1.0e-2):
        first_divergence = "semantic_backbone"
    elif first_code_mismatch is not None:
        first_divergence = "acoustic_decoder_or_output_heads"
    elif report["native_eos_frames"] != report["reference_eos_frames"]:
        first_divergence = "eos_head"
    else:
        first_divergence = "no_divergence_in_compared_stages"
    report["first_divergence"] = first_divergence

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
