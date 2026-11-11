#!/usr/bin/env bash
# Build the vllm-sr router binary with router.patch and install it where deploy.sh mounts it.
#   deploy/vllm-sr/build-router.sh          # builds on the MBP (owner rule: CPU work off groot) in golang:1.25-bookworm
# Why: upstream refuses fallback for any request that carries tool results (it calls them non-idempotent side
# effects), so every agent turn after its first tool call failed on the primary backend's 429/403.
# ROUTER_COMMIT is the upstream commit the pinned image (chart-local.patch digest, built 2026-10-20T09:57Z) was
# cut from; the unpatched build has the deployed binary's exact size (240100408 bytes).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROUTER_COMMIT=e38f1fe7
OUT=${ROUTER_BIN:-$HOME/.local/lib/vllm-sr/router-$ROUTER_COMMIT-$(sha256sum "$HERE/router.patch" | cut -c1-12)}
HOST=${BUILD_HOST:-mbp}
W=build/vllm-sr-router-$ROUTER_COMMIT
ssh "$HOST" "bash -c 'set -e; mkdir -p $W; cd $W; [ -d .git ] || { git init -q; git remote add origin https://github.com/vllm-project/semantic-router; }
git fetch -q --depth 1 origin $ROUTER_COMMIT 2>/dev/null || git fetch -q --shallow-since=2026-10-19 origin main
git checkout -q -f $ROUTER_COMMIT && git clean -qfd src/semantic-router/pkg'"
ssh "$HOST" "bash -c 'cd $W && git apply'" < "$HERE/router.patch"
ssh "$HOST" "bash -c 'cd $W/src/semantic-router && docker run --rm --memory 7g --cpus 4 -v \$PWD:/src -v sr-gomod:/go/pkg/mod \
  -v sr-gocache:/root/.cache/go-build -w /src golang:1.25-bookworm bash -c \"set -e
  go test ./pkg/extproc/ -run \\\"TestClientToolResultsKeepFallbackAllowed|Fallback\\\"; go test ./pkg/selection/ -run \\\"Random\\\" -count=1 -count=1
  go test ./pkg/fallback/ -count=1
  CGO_ENABLED=1 go build -trimpath -buildvcs=false -ldflags=\\\"-s -w\\\" -o /src/router-patched ./cmd\"'"
mkdir -p "$(dirname "$OUT")"
rsync -a "$HOST:$W/src/semantic-router/router-patched" "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
sha256sum "$OUT"
