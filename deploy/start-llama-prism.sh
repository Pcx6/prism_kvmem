#!/bin/bash
# Rollback target: plain Prism llama-server, no KVMem, no vision (131072 ctx).
set -u
ulimit -c 0

REPO="$(cd "$(dirname "$0")/.." && pwd)"
: "${MODEL:?set MODEL to the language model gguf}"
: "${API_KEY:?set API_KEY to the bearer token clients must send}"

PORT="${PORT:-14803}"
THREADS="${THREADS:-24}"
BIN="${LLAMA_BIN:-$REPO/build-sm70/bin/llama-server}"

export LD_LIBRARY_PATH="${LLAMA_LIB_DIR:-$(dirname "$BIN")}"

exec "$BIN" \
  -m "$MODEL" -a "${ALIAS:-Bonsai-2-27B}" \
  -c "${CTX:-131072}" -ngl 99 -t "$THREADS" -np 1 --reasoning on \
  -ub 1024 -b 4096 -fa on --cache-reuse 256 --cache-ram 0 -ctxcp 0 \
  --host "${HOST:-127.0.0.1}" --port "$PORT" --api-key "$API_KEY"
