#!/usr/bin/env python3

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


def normalize_changed_text(root: Path, relative_paths: list[str]) -> None:
    for relative in relative_paths:
        path = root / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        output = []
        for line in text.splitlines(keepends=True):
            if line.endswith("\r\n"):
                body, ending = line[:-2], "\r\n"
            elif line.endswith("\n"):
                body, ending = line[:-1], "\n"
            elif line.endswith("\r"):
                body, ending = line[:-1], "\r"
            else:
                body, ending = line, ""
            output.append(body.rstrip(" \t") + ending)
        normalized = "".join(output)
        if normalized != text:
            path.write_text(normalized, encoding="utf-8")


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
    run_script(native_dir / "optimize_vieneu_android_cache_v4.py", str(source), str(android_root))
    run_script(native_dir / "finalize_vieneu_android_direct_wav.py", str(android_root))
    run_script(native_dir / "finalize_vieneu_android_app.py", str(android_root))

    # System TTS uses only the canonical full-buffer direct PCM transport.
    # Early/prefix PCM was deliberately removed after device A/B testing found
    # audible stutter. Do not reintroduce a streaming callback here.
    run_script(native_dir / "patch_vieneu_system_tts_fast_pcm.py", str(android_root))

    if v092_source_script.read_bytes() != v092_source_original:
        raise RuntimeError("v092 source patch driver was not restored after materialization")
    if v092_android_script.read_bytes() != v092_android_original:
        raise RuntimeError("v092 Android patch driver was not restored after materialization")

    source_changed_all = [
        line.strip()
        for line in run_checked("git", "diff", "--name-only", cwd=source).splitlines()
        if line.strip()
    ]
    repo_changed_all = [
        line.strip()
        for line in run_checked("git", "diff", "--name-only", cwd=repo_root).splitlines()
        if line.strip()
    ]
    normalize_changed_text(source, source_changed_all)
    normalize_changed_text(repo_root, repo_changed_all)

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

    final_native_kt = android_root / "app/src/main/java/com/vieneu/voiceclone/VieNeuNative.kt"
    final_jni = android_root / "native/vieneu_jni.cpp"
    final_service = android_root / "app/src/main/java/com/vieneu/voiceclone/VieNeuTtsService.kt"
    final_store = android_root / "app/src/main/java/com/vieneu/voiceclone/VoiceProfileStore.kt"
    final_settings = android_root / "app/src/main/java/com/vieneu/voiceclone/SystemVoiceSettingsActivity.kt"
    final_layout = android_root / "app/src/main/res/layout/activity_system_voice_settings.xml"

    direct_contract = {
        final_native_kt: (
            "synthesizeDirect(text: String",
            "dialect: String): FloatArray?",
        ),
        final_jni: (
            "Java_com_vieneu_voiceclone_VieNeuNative_synthesizeDirect",
            "jni_float_direct",
            "SetFloatArrayRegion",
        ),
        final_service: (
            "VieNeuNative.synthesizeDirect(",
            "system_tts.pcm_cache.hit",
            "system_tts.warm.preempt_requested",
            '"outcome" to "cancelled"',
            '"utterance_split" to false',
            '"audio_transport" to "jni_float_direct"',
        ),
    }
    for path, fragments in direct_contract.items():
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in text]
        if missing:
            raise RuntimeError(f"Direct System TTS materialization contract missing in {path}: {missing}")

    acoustic_source = source / "src/vieneu/v3_native/v3_native_acoustic_ggml.cpp"
    acoustic_text = acoustic_source.read_text(encoding="utf-8")
    acoustic_contract = (
        "ops.qkv.run_batch2",
        "ops.o_proj.run_batch2",
        "ops.ffn.run_batch2",
        "OpenCL acoustic linear batch2 graph compute failed",
    )
    missing_acoustic = [fragment for fragment in acoustic_contract if fragment not in acoustic_text]
    if missing_acoustic:
        raise RuntimeError(f"F32 acoustic batch2 materialization missing: {missing_acoustic}")

    forbidden_early_audio = (
        "NativePcmStreamSink",
        "earlyPlayback",
        "early_playback",
        "jni_float_stream",
        "onNativePcmChunk",
        "stream_first_frames",
        "VIENEU_STREAM_ABORTED",
        "Phát sớm khi đang tạo",
    )
    for path in (final_native_kt, final_jni, final_service, final_store, final_settings, final_layout):
        text = path.read_text(encoding="utf-8")
        found = [fragment for fragment in forbidden_early_audio if fragment in text]
        if found:
            raise RuntimeError(f"Removed early-audio path survived in {path}: {found}")

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
        "reference_cache": "content-addressed-v4",
        "acoustic_runtime": "opencl-f32-canonical-batch2",
        "acoustic_initial_token_batch": 2,
        "audio_transport": "native-wav-pcm16",
        "system_tts_audio_transport": "jni-f32-direct",
        "system_tts_utterance_split": False,
        "system_tts_pcm_cache": "exact-lru",
        "system_tts_early_playback_ab": False,
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
