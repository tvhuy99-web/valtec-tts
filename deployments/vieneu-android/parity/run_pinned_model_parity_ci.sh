#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
cd "$ROOT"

CPP_COMMIT="${CPP_COMMIT:-838979faf0354ffb3dff898e30b709f644fa7db4}"
OFFICIAL_SOURCE_COMMIT="${OFFICIAL_SOURCE_COMMIT:-54f42abf4460e68aac79c985b9446557c2180f2f}"
OFFICIAL_MODEL_REVISION="${OFFICIAL_MODEL_REVISION:-75ff82a72f54d55ed389e1eeb12041d3c4bac7d4}"
NATIVE_BASE_REVISION="${NATIVE_BASE_REVISION:-a7b4f40050d4ff6d5225d8fd4fe6d571913844a2}"
ORT_VERSION="${ORT_VERSION:-1.24.4}"

rm -rf upstream-cpp official-python models/official models/pinned build native-build onnxruntime-sdk onnxruntime.tgz evidence
mkdir -p models/official models/pinned/acoustic evidence/native evidence/same-asset evidence/acoustic-numpy build
exec > >(tee evidence/ci-run.log) 2>&1

echo "[parity-ci] clone pinned source trees"
git clone --recursive https://github.com/dduongtrandai/VieNeu-TTS.cpp.git upstream-cpp
git -C upstream-cpp checkout "$CPP_COMMIT"
git -C upstream-cpp submodule update --init --recursive
test "$(git -C upstream-cpp rev-parse HEAD)" = "$CPP_COMMIT"

git clone https://github.com/pnnbao97/VieNeu-TTS.git official-python
git -C official-python checkout "$OFFICIAL_SOURCE_COMMIT"
test "$(git -C official-python rev-parse HEAD)" = "$OFFICIAL_SOURCE_COMMIT"

git -C upstream-cpp rev-parse HEAD | tee evidence/cpp-source-revision.txt
git -C upstream-cpp/third_party/llama.cpp rev-parse HEAD | tee evidence/llama-source-revision.txt
git -C official-python rev-parse HEAD | tee evidence/python-source-revision.txt

echo "[parity-ci] download pinned model snapshots"
python - <<PY
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="lastudio-community/VieNeu-TTS-v3-Turbo-CPP",
    revision="$NATIVE_BASE_REVISION",
    local_dir="models/pinned",
)
snapshot_download(
    repo_id="pnnbao-ump/VieNeu-TTS-v3-Turbo",
    revision="$OFFICIAL_MODEL_REVISION",
    allow_patterns=["update/*", "onnx_update/*", "tokenizer*", "config.json"],
    local_dir="models/official",
)
PY

OFFICIAL_SAFE="$(find models/official -type f -name model.safetensors | head -n 1)"
OFFICIAL_CFG="$(find models/official -type f -path '*/update/config.json' | head -n 1)"
if [[ -z "$OFFICIAL_CFG" ]]; then
  OFFICIAL_CFG="$(find models/official -type f -name config.json | head -n 1)"
fi
OFFICIAL_TOKENIZER="$(find models/official -type f -name tokenizer.json | head -n 1)"
NATIVE_VOICES="models/pinned/voices_v3_turbo.json"

test -s "$OFFICIAL_SAFE"
test -s "$OFFICIAL_CFG"
test -s "$OFFICIAL_TOKENIZER"
test -s "$NATIVE_VOICES"
OFFICIAL_TOKENIZER_DIR="$(dirname "$OFFICIAL_TOKENIZER")"
sha256sum "$OFFICIAL_SAFE" "$OFFICIAL_CFG" "$OFFICIAL_TOKENIZER" "$NATIVE_VOICES" | tee evidence/pinned-inputs.sha256

echo "[parity-ci] export all trainable native components from one official checkpoint"
python deployments/vieneu-android/parity/prepare_standard_qwen3_backbone.py \
  --safetensors "$OFFICIAL_SAFE" \
  --config "$OFFICIAL_CFG" \
  --tokenizer-dir "$OFFICIAL_TOKENIZER_DIR" \
  --tokenizer-dir models/official/update \
  --tokenizer-dir models/official \
  --output-hf-dir build/qwen3-hf \
  --output-heads models/pinned/vieneu_v3_heads.npz \
  --output-acoustic models/pinned/acoustic/vieneu_acoustic_weights.npz \
  --metadata evidence/export-metadata.json \
  --source-revision "$OFFICIAL_MODEL_REVISION"

cp "$OFFICIAL_CFG" models/pinned/config.json
cp "$OFFICIAL_TOKENIZER" models/pinned/tokenizer.json

python upstream-cpp/third_party/llama.cpp/convert_hf_to_gguf.py \
  build/qwen3-hf \
  --outfile models/pinned/backbone.gguf \
  --outtype f32

test -s models/pinned/backbone.gguf
test -s models/pinned/vieneu_v3_heads.npz
test -s models/pinned/acoustic/vieneu_acoustic_weights.npz
ls -lh models/pinned/backbone.gguf models/pinned/vieneu_v3_heads.npz models/pinned/acoustic/vieneu_acoustic_weights.npz

echo "[parity-ci] generate immutable package manifest"
python - <<PY
import hashlib
import json
from pathlib import Path

root = Path("models/pinned")
required = [
    "config.json",
    "tokenizer.json",
    "voices_v3_turbo.json",
    "backbone.gguf",
    "vieneu_v3_heads.npz",
    "speaker_encoder.onnx",
    "denoiser.onnx",
    "acoustic/vieneu_acoustic_weights.npz",
    "codec/moss_audio_tokenizer_decode_full.onnx",
    "codec/moss_audio_tokenizer_decode_shared.data",
    "codec/moss_audio_tokenizer_encode.onnx",
    "codec/moss_audio_tokenizer_encode.data",
]
files = []
for relative in required:
    path = root / relative
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"missing required model file: {relative}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    files.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
manifest = {
    "schema": 1,
    "package_id": "vieneu-v3-turbo-parity-rebuild-75ff82a7",
    "source_repository": "pnnbao-ump/VieNeu-TTS-v3-Turbo",
    "source_revision": "$OFFICIAL_MODEL_REVISION",
    "native_aux_repository": "lastudio-community/VieNeu-TTS-v3-Turbo-CPP",
    "native_aux_revision": "$NATIVE_BASE_REVISION",
    "voices_source": "native_aux_revision",
    "exporter_repository": "dduongtrandai/VieNeu-TTS.cpp",
    "exporter_revision": "$CPP_COMMIT",
    "files": files,
}
text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
(root / "model-manifest.json").write_text(text, encoding="utf-8")
Path("evidence/model-manifest.json").write_text(text, encoding="utf-8")
print(text)
PY

echo "[parity-ci] instrument clean native runtime"
python deployments/vieneu-android/parity/patch_native_parity_includes.py upstream-cpp
python deployments/vieneu-android/parity/patch_native_parity.py upstream-cpp
git -C upstream-cpp diff --check

echo "[parity-ci] fetch ONNX Runtime SDK"
curl --fail --location --retry 5 --retry-delay 3 \
  -o onnxruntime.tgz \
  "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz"
tar -xzf onnxruntime.tgz
mv "onnxruntime-linux-x64-${ORT_VERSION}" onnxruntime-sdk

echo "[parity-ci] build exact CPU-F32 native reference"
cmake -S upstream-cpp -B native-build -G Ninja \
  -DONNXRUNTIME_ROOT="$ROOT/onnxruntime-sdk" \
  -DVIENEU_PORTABLE_CPU=ON \
  -DVIENEU_NATIVE_BACKEND=cpu \
  -DVIENEU_SEA_G2P=OFF \
  -DBUILD_TESTING=OFF \
  -DGGML_OPENMP=OFF \
  -DCMAKE_BUILD_TYPE=Release
cmake --build native-build --target vieneu-tts-cli --parallel 2

PARITY_VOICE="$(python - <<'PY'
import json
with open("models/pinned/voices_v3_turbo.json", encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("default_voice") or next(iter(data["presets"])))
PY
)"
test -n "$PARITY_VOICE"
echo "[parity-ci] preset=$PARITY_VOICE"

CLI="$(find native-build -type f -name vieneu-tts-cli -perm -111 | head -n 1)"
test -n "$CLI"
export LD_LIBRARY_PATH="$ROOT/onnxruntime-sdk/lib:$(dirname "$CLI"):${LD_LIBRARY_PATH:-}"
export VIENEU_PARITY_PHONEMES='zˈaː6w nˈa2j bˈaː6n kˈɔɜ xwˈɛ4 xˌoŋ'
export VIENEU_PARITY_DIR="$ROOT/evidence/native"
export VIENEU_PARITY_FORCE_GREEDY=1
export VIENEU_V3_NATIVE_DEBUG_TAGS=1
export VIENEU_V3_NATIVE_BENCHMARK=1
export VIENEU_ACOUSTIC_Q8_FFN=0
export VIENEU_ACOUSTIC_DIRECT_LINEAR=1
export VIENEU_ACOUSTIC_GGML_HEADS=0
export OMP_NUM_THREADS=2

"$CLI" \
  --profile vieneu-v3-native \
  --model-dir "$ROOT/models/pinned" \
  --codec-dir "$ROOT/models/pinned/codec" \
  --voice "$PARITY_VOICE" \
  --style tu_nhien \
  --text parity \
  --max-new-frames 80 \
  --threads 2 \
  --output "$ROOT/evidence/native/native.wav" \
  2>&1 | tee evidence/native/native-run.log

echo "[parity-ci] compare native acoustic against same-weight NumPy"
python deployments/vieneu-android/parity/run_native_acoustic_numpy_parity.py \
  --native-model-dir models/pinned \
  --native-dump-dir evidence/native \
  --output evidence/acoustic-numpy/report.json \
  2>&1 | tee evidence/acoustic-numpy/run.log

echo "[parity-ci] compare rebuilt native runtime against official ONNX"
python deployments/vieneu-android/parity/run_same_asset_parity.py \
  --official-source official-python \
  --onnx-dir models/official/onnx_update \
  --native-model-dir models/pinned \
  --native-dump-dir evidence/native \
  --output-dir evidence/same-asset \
  2>&1 | tee evidence/same-asset/run.log

echo "[parity-ci] enforce strict numerical thresholds"
python deployments/vieneu-android/parity/validate_pinned_model_parity.py \
  --same-asset-report evidence/same-asset/same-asset-report.json \
  --acoustic-report evidence/acoustic-numpy/report.json \
  --summary evidence/parity-summary.json

touch evidence/PARITY_PASSED
echo "[parity-ci] PASS"
