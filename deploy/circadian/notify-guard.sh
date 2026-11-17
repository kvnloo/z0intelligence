# --- circadian gate (CIRCADIAN-SPEC WP1) -------------------------------------
# In a quiet phase (after the freeze, after "done for today", asleep) a
# report is held and released as ONE digest when the owner is awake again.
# Fails open: if circadian is missing, slow or broken, deliver as before.
# Bypass for one call: CIRCADIAN_BYPASS=1. Class: CIRCADIAN_CLASS=report|decision|digest|critical.
if [ -z "${CIRCADIAN_BYPASS:-}" ] && [ -x "$HOME/.local/bin/circadian" ]; then
  timeout 10 "$HOME/.local/bin/circadian" route --source "__SOURCE__" --tag "__TAG__" \
    --title "__TITLE__" --body "__BODY__" --cls "${CIRCADIAN_CLASS:-report}" >/dev/null 2>&1
  if [ $? -eq 10 ]; then echo "__SOURCE__: held for the morning digest (circadian quiet phase)"; exit 0; fi
fi
# --- end circadian gate ------------------------------------------------------
