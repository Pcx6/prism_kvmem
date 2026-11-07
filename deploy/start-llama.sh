#!/bin/bash
# Fused KVMem + Prism llama-server with vision.
# All deployment specific values come from the environment:
#   MODEL / MMPROJ / API_KEY  (required)
#   PORT (14803), ALIAS (Bonsai-2-27B), CTX (262144), THREADS (24)
#   KV_BUDGET (12288), KV_GEN_RESERVE (22528), KV_TTL (86400)
#   LLAMA_BIN / LLAMA_LIB_DIR (default: build-sm70 under the repo root)
set -u
ulimit -c 0

REPO="$(cd "$(dirname "$0")/.." && pwd)"
: "${MODEL:?set MODEL to the language model gguf}"
: "${MMPROJ:?set MMPROJ to the vision projector gguf}"
: "${API_KEY:?set API_KEY to the bearer token clients must send}"

PORT="${PORT:-14803}"
ALIAS="${ALIAS:-Bonsai-2-27B}"
CTX="${CTX:-262144}"
KV_BUDGET="${KV_BUDGET:-12288}"
KV_GEN_RESERVE="${KV_GEN_RESERVE:-22528}"
KV_TTL="${KV_TTL:-86400}"
THREADS="${THREADS:-24}"
BIN="${LLAMA_BIN:-$REPO/build-sm70/bin/llama-server}"

export LD_LIBRARY_PATH="${LLAMA_LIB_DIR:-$(dirname "$BIN")}"

exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" -a "$ALIAS" \
  -c "$CTX" -ngl 99 -t "$THREADS" -np 1 --reasoning on \
  -ub 1024 -b 4096 -fa on --cache-reuse 256 --cache-ram 0 -ctxcp 0 \
  --kvmem --kvmem-budget "$KV_BUDGET" --kvmem-gen-reserve "$KV_GEN_RESERVE" \
  --kvmem-idle-ttl "$KV_TTL" \
  --host "${HOST:-127.0.0.1}" --port "$PORT" --api-key "$API_KEY"
