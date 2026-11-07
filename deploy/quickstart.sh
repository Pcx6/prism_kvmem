#!/bin/bash
# One-command launcher for the KVMem x Prism release.
#   curl -fL https://github.com/Pcx6/prism_llama_kvmem_v100/releases/download/v0.1.0/quickstart.sh | bash
# Optional environment:
#   DEST=$HOME/.local/opt/prism_kvmem   install dir
#   MODELS=$DEST/models                 model dir (about 6.2 GB)
#   API_KEY=...                         bearer token (random if unset)
#   PORT=14803                          listen port
set -euo pipefail

TAG="${TAG:-v0.1.0}"
REPO="${REPO:-Pcx6/prism_llama_kvmem_v100}"
BASE="https://github.com/${REPO}/releases/download/${TAG}"
DEST="${DEST:-$HOME/.local/opt/prism_kvmem}"
MODELS="${MODELS:-$DEST/models}"
PORT="${PORT:-14803}"
if [ -z "${API_KEY:-}" ]; then
  API_KEY="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi

if [ ! -x "$DEST/bin/llama-server" ]; then
  echo "[quickstart] installing binaries to $DEST"
  mkdir -p "$DEST"
  curl -fL "$BASE/prism_kvmem-$TAG-linux-sm70.tar.gz" | tar xz -C "$DEST" --strip-components=1
fi

need=0
for f in Ternary-Bonsai-2-27B-PTQ1_0.gguf Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf; do
  [ -f "$MODELS/$f" ] || need=1
done
if [ "$need" = "1" ]; then
  echo "[quickstart] downloading models to $MODELS (about 6.2 GB)"
  command -v huggingface-cli >/dev/null || python3 -m pip install -U "huggingface_hub[cli]"
  mkdir -p "$MODELS"
  huggingface-cli download prism-ml/Ternary-Bonsai-2-27B-gguf Ternary-Bonsai-2-27B-PTQ1_0.gguf --local-dir "$MODELS"
  huggingface-cli download prism-ml/Ternary-Bonsai-2-27B-gguf Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf --local-dir "$MODELS"
fi

export MODEL="$MODELS/Ternary-Bonsai-2-27B-PTQ1_0.gguf"
export MMPROJ="$MODELS/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf"
export API_KEY PORT
echo "[quickstart] API key: $API_KEY"
echo "[quickstart] endpoint: http://127.0.0.1:$PORT/v1/chat/completions"
export LLAMA_BIN="$DEST/bin/llama-server"
export LLAMA_LIB_DIR="$DEST/bin"
exec "$DEST/deploy/start-llama.sh"
