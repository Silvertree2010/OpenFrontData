#!/usr/bin/env bash
# materializer/fleet/stop.sh — of-mat2 auf jedem Host sauber stoppen.
#
# `docker stop` schickt SIGTERM (dispatch.ts faengt es ab, beendet laufende
# Kinder und startet nichts Neues mehr, siehe DESIGN.md §7) und wartet bis zu
# `-t 60` Sekunden, bevor es SIGKILL nachschiebt.
#
# Aufruf: materializer/fleet/stop.sh <hosts-datei>
set -euo pipefail

HOSTS_FILE="${1:?Aufruf: materializer/fleet/stop.sh <hosts-datei>}"
mapfile -t LINES < <(grep -vE '^[[:space:]]*(#|$)' "$HOSTS_FILE")

for line in "${LINES[@]}"; do
  read -r NAME SSH RECORDS OUTDIR CPUS MODE <<< "$line"
  echo "[stop] $NAME ($SSH)"
  timeout 90 ssh -o ConnectTimeout=10 "$SSH" \
    "docker stop -t 60 of-mat2 2>/dev/null; docker rm -f of-mat2 2>/dev/null; true" \
    || echo "  (ssh fehlgeschlagen oder kein Container)"
done
