#!/usr/bin/env python3
"""Materialize all pinned VieNeu Android source changes before CMake configure."""

import argparse
import contextlib
import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path


def run_checked(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def run_script(path: Path, *arguments: str) -> None:
    saved_argv = sys.argv[:]
    try:
        sys.argv = [str(path), *arguments]
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = saved_argv


@contextlib.contextmanager
def preserve_file(path: Path):
    original = path.read_bytes()
    try:
        yield
    finally:
        path.write_bytes(original)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir")
    parser.add_argument("android_root")
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--source-diff-output", required=True)
    parser.add_argument("--android-diff-output", required=True)
    parser.add_argument("--manifest-output", required=True)
    args = parser.parse_args()

    source = Path(args.source_dir).resolve()
    android_root = Path(args.android_root).resolve()
    native_dir = Path(__file__).resolve().parent
    repo_root = android_root.parent.parent

    if not (source / ".git").exists():
        raise RuntimeError(f"VieNeu source is not a git checkout: {source}")
    if not (repo_root / ".git").exists():
        raise RuntimeError(f"Android project is not inside the repository checkout: {repo_root}")

    actual_revision = run_checked("git", "rev-parse", "HEAD", cwd=source).strip()
    if actual_revision != args.expected_revision:
        raise RuntimeError(
            f"Pinned VieNeu revision mismatch: expected {args.expected_revision}, got {actual_revision}"
        )

    source_dirty = run_checked("git", "status", "--porcelain", "--untracked-files=no", cwd=source)
    if source_dirty.strip():
        raise RuntimeError("VieNeu source must be clean before Android materialization")

    repo_dirty = run_checked("git", "status", "--porcelain", "--untracked-files=no", cwd=repo_root)
    if repo_dirty.strip():
        raise RuntimeError("Repository checkout must be clean before Android materialization")

    v092_source_script = native_dir / "optimize_vieneu_android_v092_source.py"
    v092_android_script = native_dir / "optimize_vieneu_android_v092_android.py"

    run_script(native_dir / "instrument_vieneu_diagnostics.py", str(source))
    run_script(native_dir / "optimize_vieneu_android.py", str(source))

    with preserve_file(v092_source_script):
        run_script(native_dir / "optimize_vieneu_android_memory.py", str(source))

    run_script(native_dir / "optimize_vieneu_android_acoustic_f16.py", str(source))
    run_script(native_dir / "optimize_vieneu_android_acoustic_opencl.py", str(source))
    run_script(native_dir / "optimize_vieneu_android_acoustic_quality.py", str(source))
    run_script(native_dir / "optimize_vieneu_android_generation_quality.py", str(source))

    with preserve_file(v092_android_script):
        run_script(native_dir / "optimize_vieneu_android_short_text.py", str(native_dir / "vieneu_jni.cpp"))

    run_script(native_dir / "optimize_vieneu_android_opencl_runtime.py", str(source))
    run_script(native_dir / "patch_llama_opencl_qcom_shuffle.py", str(source))
    run_script(native_dir / "optimize_vieneu_android_cache_v3.py", str(source), str(android_root))

    run_checked("git", "diff", "--check", cwd=source)
    run_checked("git", "diff", "--check", "--", "deployments/vieneu-android", cwd=repo_root)

    source_patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=str(source),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    if not source_patch:
        raise RuntimeError("VieNeu materialization produced an empty source diff")

    android_patch = subprocess.run(
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--",
            "deployments/vieneu-android/app",
            "deployments/vieneu-android/native/vieneu_jni.cpp",
        ],
        cwd=str(repo_root),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    if not android_patch:
        raise RuntimeError("Android materialization produced an empty project diff")

    source_diff_path = Path(args.source_diff_output).resolve()
    android_diff_path = Path(args.android_diff_output).resolve()
    manifest_path = Path(args.manifest_output).resolve()
    source_diff_path.parent.mkdir(parents=True, exist_ok=True)
    android_diff_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    source_diff_path.write_bytes(source_patch)
    android_diff_path.write_bytes(android_patch)

    source_files = [
        line.strip()
        for line in run_checked("git", "diff", "--name-only", cwd=source).splitlines()
        if line.strip()
    ]
    android_files = [
        line.strip()
        for line in run_checked(
            "git",
            "diff",
            "--name-only",
            "--",
            "deployments/vieneu-android/app",
            "deployments/vieneu-android/native/vieneu_jni.cpp",
            cwd=repo_root,
        ).splitlines()
        if line.strip()
    ]

    manifest = {
        "schema": 1,
        "upstream_revision": actual_revision,
        "source_patch_sha256": sha256_bytes(source_patch),
        "source_patch_bytes": len(source_patch),
        "source_changed_files": source_files,
        "android_patch_sha256": sha256_bytes(android_patch),
        "android_patch_bytes": len(android_patch),
        "android_changed_files": android_files,
        "reference_cache": "content-addressed-v3",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    marker = source / ".vieneu-android-prepared.json"
    marker.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    if v092_source_script.read_bytes() != (native_dir / "optimize_vieneu_android_v092_source.py").read_bytes():
        raise RuntimeError("v092 source patch driver was not restored")
    if v092_android_script.read_bytes() != (native_dir / "optimize_vieneu_android_v092_android.py").read_bytes():
        raise RuntimeError("v092 Android patch driver was not restored")

    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
