#!/usr/bin/env bash
# Deploy or refresh the turbo router (vLLM Semantic Router) in the local k3s cluster.
#   deploy/vllm-sr/deploy.sh            # uses deploy/vllm-sr/config.yaml
#   deploy/vllm-sr/deploy.sh other.yaml
# Idempotent: the upstream chart is fetched once at a pinned commit, patched with chart-local.patch
# (pinned image digest, desktop-safe memory caps). configMap.applyValuesRevision is set to the
# config file's hash so a changed config always replaces the live ConfigMap and an unchanged one never does.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CFG=$(realpath "${1:-$HERE/config.yaml}")
SR_COMMIT=62c4f39e35aa2dfe929eb18223a7509ebeff6563   # vllm-project/semantic-router chart the patch applies to
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/vllm-sr-chart/$SR_COMMIT
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/k3s.yaml}
VSR=${VSR:-$HOME/.local/share/vllm-sr/venv/bin/vllm-sr}
ENV_EXEC=${ENV_EXEC:-/mnt/zer0models/workspace/zer0/oss/tools/dsh-env-exec.py}
ENV_PY=${ENV_PY:-$HOME/.hermes/hermes-agent/venv/bin/python}

if [[ ! -f $CACHE/.patched ]]; then
  rm -rf "$CACHE.tmp" && mkdir -p "$CACHE.tmp"
  git -C "$CACHE.tmp" init -q
  git -C "$CACHE.tmp" remote add origin https://github.com/vllm-project/semantic-router
  git -C "$CACHE.tmp" sparse-checkout set deploy/helm/semantic-router
  git -C "$CACHE.tmp" fetch -q --depth 1 origin "$SR_COMMIT"
  git -C "$CACHE.tmp" checkout -q FETCH_HEAD
  (cd "$CACHE.tmp/deploy/helm/semantic-router" && patch -s -p1 < "$HERE/chart-local.patch")
  # Minimal mode enables none of the subcharts (redis, milvus, jaeger, prometheus, grafana);
  # drop them so helm does not demand five external chart repos.
  "$(dirname "$VSR")/python" - "$CACHE.tmp/deploy/helm/semantic-router" <<'PY'
import pathlib, sys, yaml
root = pathlib.Path(sys.argv[1])
chart = yaml.safe_load((root / "Chart.yaml").read_text())
chart.pop("dependencies", None)
(root / "Chart.yaml").write_text(yaml.safe_dump(chart, sort_keys=False))
(root / "Chart.lock").unlink(missing_ok=True)
PY
  touch "$CACHE.tmp/.patched"
  rm -rf "$CACHE" && mv "$CACHE.tmp" "$CACHE"
fi
# Owner rule: no Google/Gemini route or key may ever reach the router (Gemini only via the agy CLI).
python3 "$HERE/no_google_guard.py" "$CFG" "$0" || { echo "deploy refused by no_google_guard" >&2; exit 1; }
CHART=$CACHE/deploy/helm/semantic-router
REV="cfg-$(sha256sum "$CFG" | cut -c1-12)"
sed -i -E "s/^  applyValuesRevision: .*/  applyValuesRevision: \"$REV\"/" "$CHART/values.yaml"

cd "$HERE"
# `vllm-sr serve` waits with `kubectl wait pod -l ...`, which can latch onto the outgoing pod and block
# until its 600s timeout after a rollout. Bound it and gate on the Deployment rollout instead.
LOG=$(mktemp)
set +e
timeout 180 "$ENV_PY" "$ENV_EXEC" --allow GROQ_API_KEY --allow CEREBRAS_API_KEY --allow NVIDIA_API_KEY --allow OPENROUTER_API_KEY --allow VLLM_SR_SUBPROXY_TOKEN -- \
  env PATH="$(dirname "$VSR"):$HOME/.local/bin:$PATH" KUBECONFIG="$KUBECONFIG" \
  "$VSR" serve --target kubernetes --config "$CFG" --chart-dir "$CHART" --namespace vllm-sr \
  --profile dev --platform cpu --minimal > "$LOG" 2>&1
rc=$?
set -e
grep -v API_KEY "$LOG" | grep -E 'upgraded|deployed|rror' || true
# Helm's success line is the contract. After it, any CLI exit (124 = our timeout, 1 = its pod wait hit the
# deleted outgoing pod: "kubectl wait failed ... NotFound") is the CLI's wait, not the deploy; the rollout gate below decides.
if ! grep -qE 'has been upgraded|Install complete|deployed successfully' "$LOG"; then
  echo "deploy failed (rc=$rc); log: $LOG" >&2; exit 1
fi
rm -f "$LOG"
kubectl -n vllm-sr rollout status deploy/semantic-router --timeout=300s

IP=$(kubectl -n vllm-sr get svc semantic-router -o jsonpath='{.spec.clusterIP}')
echo "router: http://$IP:8899/v1  config revision: $REV"
echo "hermes: hermes -p clean config set providers.vllm-sr.base_url http://$IP:8899/v1"
