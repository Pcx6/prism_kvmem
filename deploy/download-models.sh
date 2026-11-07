#!/bin/bash
# Download the model and vision projector from Hugging Face.
# Usage: DEST=models ./deploy/download-models.sh
set -euo pipefail

DEST="${DEST:-models}"
REPO_ID="${REPO_ID:-prism-ml/Ternary-Bonsai-2-27B-gguf}"
MODEL_FILE="${MODEL_FILE:-Ternary-Bonsai-2-27B-PTQ1_0.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf}"

mkdir -p "$DEST"
python3 -m pip install -q -U "huggingface_hub[cli]"
huggingface-cli download "$REPO_ID" "$MODEL_FILE" --local-dir "$DEST"
huggingface-cli download "$REPO_ID" "$MMPROJ_FILE" --local-dir "$DEST"

echo "MODEL=$DEST/$MODEL_FILE"
echo "MMPROJ=$DEST/$MMPROJ_FILE"
