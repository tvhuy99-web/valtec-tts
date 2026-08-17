#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-vieneu-model-parity-75ff82a7}"
MODEL_ROOT="${2:-models/pinned}"
EVIDENCE_ROOT="${3:-evidence}"

if [[ ! -f "$EVIDENCE_ROOT/PARITY_PASSED" ]]; then
  echo "Refusing to publish: strict parity marker is missing." >&2
  exit 1
fi
if [[ ! -f "$MODEL_ROOT/model-manifest.json" ]]; then
  echo "Refusing to publish: model manifest is missing." >&2
  exit 1
fi

ASSET_DIR="${RUNNER_TEMP:-/tmp}/vieneu-model-release-assets"
rm -rf "$ASSET_DIR"
mkdir -p "$ASSET_DIR"
cp "$MODEL_ROOT/model-manifest.json" "$ASSET_DIR/model-manifest.json"

python - "$MODEL_ROOT" "$ASSET_DIR" <<'PY'
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
manifest = json.loads((root / "model-manifest.json").read_text(encoding="utf-8"))
for item in manifest["files"]:
    relative = item["path"]
    source = root / relative
    if not source.is_file() or source.stat().st_size != int(item["bytes"]):
        raise SystemExit(f"invalid release source: {relative}")
    target = out / relative.replace("/", "__")
    shutil.copy2(source, target)
PY

(
  cd "$ASSET_DIR"
  sha256sum * | sort > release-assets.sha256
)

MANIFEST_SHA="$(sha256sum "$ASSET_DIR/model-manifest.json" | awk '{print $1}')"
SOURCE_REVISION="$(python - "$ASSET_DIR/model-manifest.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["source_revision"])
PY
)"

if gh release view "$TAG" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
  EXISTING_DIR="${RUNNER_TEMP:-/tmp}/vieneu-existing-release"
  rm -rf "$EXISTING_DIR"
  mkdir -p "$EXISTING_DIR"
  gh release download "$TAG" \
    --repo "$GITHUB_REPOSITORY" \
    --pattern model-manifest.json \
    --dir "$EXISTING_DIR"
  EXISTING_SHA="$(sha256sum "$EXISTING_DIR/model-manifest.json" | awk '{print $1}')"
  if [[ "$EXISTING_SHA" != "$MANIFEST_SHA" ]]; then
    echo "Immutable release $TAG exists with a different manifest: $EXISTING_SHA != $MANIFEST_SHA" >&2
    exit 1
  fi
  echo "MODEL_RELEASE_TAG=$TAG" >> "$GITHUB_ENV"
  echo "MODEL_MANIFEST_SHA256=$MANIFEST_SHA" >> "$GITHUB_ENV"
  echo "Immutable release $TAG already exists and matches manifest $MANIFEST_SHA"
  exit 0
fi

NOTES="${RUNNER_TEMP:-/tmp}/vieneu-model-release-notes.md"
cat > "$NOTES" <<EOF
Immutable VieNeu v3 Turbo native model package approved by strict numerical parity.

- Package: \`vieneu-v3-turbo-parity-rebuild-75ff82a7\`
- Official source revision: \`$SOURCE_REVISION\`
- Manifest SHA-256: \`$MANIFEST_SHA\`
- Semantic backbone: canonical llama.cpp Qwen3 F32 export
- Acoustic decoder: official PyTorch F32 checkpoint export
- Auxiliary codec/speaker assets: pinned native auxiliary snapshot

The Android app verifies package ID, source revision, file length, and SHA-256 for every asset before initialization.
EOF

gh release create "$TAG" "$ASSET_DIR"/* \
  --repo "$GITHUB_REPOSITORY" \
  --target "$GITHUB_SHA" \
  --title "VieNeu parity-approved model 75ff82a7" \
  --notes-file "$NOTES"

echo "MODEL_RELEASE_TAG=$TAG" >> "$GITHUB_ENV"
echo "MODEL_MANIFEST_SHA256=$MANIFEST_SHA" >> "$GITHUB_ENV"
echo "Published immutable model release $TAG with manifest $MANIFEST_SHA"
