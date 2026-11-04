#!/usr/bin/env bash
# Stamp the O19 kernel, install the Modal hard stop and the Hermes spend-gate plugin for one profile.
#   adapters/spend-stop/install.sh [HERMES_HOME]
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
HH=${1:-${HERMES_HOME:-$HOME/.hermes}}
SHA=$(git -C "$REPO" rev-parse --short HEAD)
LIB=$HOME/.local/lib/z0-guard/spend
mkdir -p "$LIB/$SHA/src/z0int/fsm"
cp "$REPO/src/z0int/fsm/ops.py" "$REPO/src/z0int/fsm/spend_gate.py" "$LIB/$SHA/src/z0int/fsm/"
: > "$LIB/$SHA/src/z0int/__init__.py"; : > "$LIB/$SHA/src/z0int/fsm/__init__.py"
ln -sfn "$LIB/$SHA" "$LIB/current"
if [ ! -x "$LIB/modal-venv/bin/modal" ]; then
  uv venv -q "$LIB/modal-venv" --python 3.12 && uv pip install -q --python "$LIB/modal-venv/bin/python" modal
fi
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/z0-spend" <<SH
#!/usr/bin/env bash
# O19 spend gate CLI (stamped kernel). Owner: z0-spend approve --surface S --cap-usd N --hours H
PYTHONPATH=$LIB/current/src Z0_SPEND_MODAL_CLI=$LIB/modal-venv/bin/modal exec python3 -m z0int.fsm.spend_gate "\$@"
SH
chmod +x "$HOME/.local/bin/z0-spend"
mkdir -p "$HOME/.config/systemd/user"
cp "$REPO"/adapters/spend-stop/z0-spend-stop@.{service,timer} "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now z0-spend-stop@modal.timer
mkdir -p "$HH/plugins/spend-gate"
cp "$REPO"/adapters/hermes-spend-gate/{__init__.py,plugin.yaml} "$HH/plugins/spend-gate/"
[ -f "$HH/spend-gate.json" ] || echo "{\"kernel\": \"$LIB/current/src\"}" > "$HH/spend-gate.json"
echo "stamped $SHA; timer: $(systemctl --user is-active z0-spend-stop@modal.timer); plugin in $HH/plugins/spend-gate"
echo "enable it: add spend-gate to plugins.enabled in $HH/config.yaml"
