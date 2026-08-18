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


def verify_pinned_submodules(source: Path) -> None:
    status = run_checked("git", "submodule", "status", "--recursive", cwd=source)
    invalid = []
    for line in status.splitlines():
        if not line:
            continue
        prefix = line[0]
        if prefix != " ":
            invalid.append(line)
    if invalid:
        raise RuntimeError(
            "VieNeu submodule revisions do not match the pinned gitlinks:\n" + "\n".join(invalid)
        )


def read_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Invalid properties line in {path}: {raw!r}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


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

    source_dirty = run_checked(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
        "--ignore-submodules=dirty",
        cwd=source,
    )
    if source_dirty.strip():
        raise RuntimeError(
            "VieNeu tracked source must be clean before Android materialization:\n" + source_dirty
        )
    verify_pinned_submodules(source)

    repo_dirty = run_checked("git", "status", "--porcelain", "--untracked-files=no", cwd=repo_root)
    if repo_dirty.strip():
        raise RuntimeError(
            "Repository checkout must be clean before Android materialization:\n" + repo_dirty
        )

    v092_source_script = native_dir / "optimize_vieneu_android_v092_source.py"
    v092_android_script = native_dir / "optimize_vieneu_android_v092_android.py"
    v092_source_original = v092_source_script.read_bytes()
    v092_android_original = v092_android_script.read_bytes()

    run_script(native_dir / "instrument_vieneu_diagnostics.py", str(source))
    run_script(native_dir / "optimize_vieneu_android.py", str(source))

    with preserve_file(v092_source_script):
        run_script(native_dir / "optimize_vieneu_android_memory.py", str(source))

    run_script(native_dir / "optimize_vieneu_android_acoustic_opencl.py", str(source))
    run_script(native_dir / "optimize_vieneu_android_generation_quality.py", str(source))

    with preserve_file(v092_android_script):
        run_script(native_dir / "optimize_vieneu_android_short_text.py", str(native_dir / "vieneu_jni.cpp"))

    run_script(native_dir / "optimize_vieneu_android_opencl_runtime.py", str(source))
    run_script(native_dir / "patch_llama_opencl_qcom_shuffle.py", str(source))
    run_script(native_dir / "optimize_vieneu_android_cache_v3.py", str(source), str(android_root))
    run_script(native_dir / "finalize_vieneu_android_direct_wav.py", str(android_root))
    run_script(native_dir / "finalize_vieneu_android_app.py", str(android_root))

    if v092_source_script.read_bytes() != v092_source_original:
        raise RuntimeError("v092 source patch driver was not restored after materialization")
    if v092_android_script.read_bytes() != v092_android_original:
        raise RuntimeError("v092 Android patch driver was not restored after materialization")

    repo_changed_all = [
        line.strip()
        for line in run_checked("git", "diff", "--name-only", cwd=repo_root).splitlines()
        if line.strip()
    ]
    unexpected_repo_changes = [
        path
        for path in repo_changed_all
        if not path.startswith("deployments/vieneu-android/app/")
        and path != "deployments/vieneu-android/native/vieneu_jni.cpp"
    ]
    if unexpected_repo_changes:
        raise RuntimeError(
            "Materialization modified build tooling instead of only generated Android sources: "
            + ", ".join(unexpected_repo_changes)
        )

    run_checked("git", "diff", "--check", cwd=source)
    run_checked("git", "submodule", "foreach", "--recursive", "git diff --check", cwd=source)
    run_checked("git", "diff", "--check", "--", "deployments/vieneu-android", cwd=repo_root)

    source_patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--submodule=diff"],
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
        for line in run_checked(
            "git",
            "diff",
            "--name-only",
            "--submodule=diff",
            cwd=source,
        ).splitlines()
        if line.strip()
    ]
    android_files = [
        path
        for path in repo_changed_all
        if path.startswith("deployments/vieneu-android/app/")
        or path == "deployments/vieneu-android/native/vieneu_jni.cpp"
    ]

    version_values = read_properties(android_root / "version.properties")
    try:
        app_version_code = int(version_values["VERSION_CODE"])
        app_version_name = version_values["VERSION_NAME"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError("version.properties must define VERSION_CODE and VERSION_NAME") from exc

    manifest = {
        "schema": 3,
        "upstream_revision": actual_revision,
        "source_patch_sha256": sha256_bytes(source_patch),
        "source_patch_bytes": len(source_patch),
        "source_changed_files": source_files,
        "android_patch_sha256": sha256_bytes(android_patch),
        "android_patch_bytes": len(android_patch),
        "android_changed_files": android_files,
        "reference_cache": "content-addressed-v3",
        "acoustic_runtime": "opencl-f32-canonical",
        "audio_transport": "native-wav-pcm16",
        "java_audio_buffer_bytes": 0,
        "engine_scope": "process",
        "version_source": "deployments/vieneu-android/version.properties",
        "app_version_code": app_version_code,
        "app_version_name": app_version_name,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    marker = source / ".vieneu-android-prepared.json"
    marker.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
