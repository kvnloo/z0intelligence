#!/bin/bash
# Install circadiand from this checkout as a user service, with a rollback.
#   deploy/circadian/install.sh            install/upgrade (keeps the previous release)
#   ~/.local/bin/circadian-rollback        restore the notify scripts, stop the service
set -euo pipefail
SRC=$(cd "$(dirname "$0")/../.." && pwd)
SHA=$(git -C "$SRC" rev-parse --short=12 HEAD)
LIB=$HOME/.local/lib/circadian
REL=$LIB/releases/$SHA
BK=$HOME/.local/share/circadian/rollback
mkdir -p "$LIB/releases" "$BK" "$HOME/.config/circadian" "$HOME/.config/systemd/user"
chmod 700 "$HOME/.config/circadian"
rm -rf "$REL.tmp"; mkdir -p "$REL.tmp"
git -C "$SRC" archive HEAD src/z0int/__init__.py src/z0int/human_posture.py src/z0int/circadian deploy/circadian bend/circadian \
  | tar -x -C "$REL.tmp"
rm -rf "$REL"; mv "$REL.tmp" "$REL"
[ -L "$LIB/current" ] && ln -sfn "$(readlink "$LIB/current")" "$LIB/previous"
ln -sfn "$REL" "$LIB/current"
install -m 755 "$REL/deploy/circadian/circadian" "$HOME/.local/bin/circadian"
install -m 644 "$REL/deploy/circadian/circadiand.service" "$HOME/.config/systemd/user/circadiand.service"
[ -f "$HOME/.config/circadian/config.json" ] || install -m 600 "$REL/deploy/circadian/config.example.json" "$HOME/.config/circadian/config.json"

# Wire the notify scripts once; keep the originals for rollback.
wire() {  # file source tag title body
  local f=$1
  [ -f "$f" ] || return 0
  grep -q "circadian gate" "$f" && return 0
  [ -f "$BK/$(basename "$f").orig" ] || cp -p "$f" "$BK/$(basename "$f").orig"
  local guard; guard=$(sed -e "s|__SOURCE__|$2|g" -e "s|__TAG__|$3|g" -e "s|__TITLE__|$4|g" -e "s|__BODY__|$5|g" \
    "$REL/deploy/circadian/notify-guard.sh")
  python3 - "$f" "$guard" "$6" <<'PY'
import sys
path, guard, anchor = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().split("\n")
idx = next(i for i, l in enumerate(lines) if l.startswith(anchor))
lines[idx:idx] = guard.split("\n")
open(path + ".new", "w").write("\n".join(lines))
PY
  chmod --reference="$f" "$f.new"; mv "$f.new" "$f"
}
wire "$HOME/.local/bin/z0-phone-notify" z0-phone-notify '$1' '$2' '$3' 'A='
wire "$HOME/.local/bin/dash-report" dash-report 'dash-report' '$1' '$2' 'BUN='
# dash-report already asked the gate; its z0-phone-notify fallback must not ask twice
if [ -f "$HOME/.local/bin/dash-report" ] && ! grep -q 'CIRCADIAN_BYPASS=1 "$HOME/.local/bin/z0-phone-notify"' "$HOME/.local/bin/dash-report"; then
  sed -i 's|if "$HOME/.local/bin/z0-phone-notify" dash-report|if CIRCADIAN_BYPASS=1 "$HOME/.local/bin/z0-phone-notify" dash-report|' "$HOME/.local/bin/dash-report"
fi

cat > "$HOME/.local/bin/circadian-rollback" <<RB
#!/bin/bash
# Undo circadiand: restore the original notify scripts, stop and disable the service.
set -u
for f in z0-phone-notify dash-report; do
  [ -f "$BK/\$f.orig" ] && cp -p "$BK/\$f.orig" "$HOME/.local/bin/\$f" && echo "restored \$f"
done
systemctl --user disable --now circadiand.service 2>/dev/null; echo "circadiand stopped"
echo "held notices (if any): $HOME/.local/state/circadian/deferred.jsonl"
RB
chmod 755 "$HOME/.local/bin/circadian-rollback"
systemctl --user daemon-reload
systemctl --user enable circadiand.service >/dev/null
systemctl --user restart circadiand.service
echo "circadiand $SHA installed; rollback: circadian-rollback"
