#!/usr/bin/env bash
# materializer/fleet/status.sh — ein ssh je Host (mit timeout): fertig/leer/Fehler/Rest,
# Containerstatus und die letzten Logzeilen.
#
# Aufruf: materializer/fleet/status.sh <hosts-datei>
set -euo pipefail

HOSTS_FILE="${1:?Aufruf: materializer/fleet/status.sh <hosts-datei>}"
mapfile -t LINES < <(grep -vE '^[[:space:]]*(#|$)' "$HOSTS_FILE")

for line in "${LINES[@]}"; do
  read -r NAME SSH RECORDS OUTDIR CPUS MODE <<< "$line"
  echo "=== $NAME ($SSH) ==="
  timeout 30 ssh -o ConnectTimeout=10 "$SSH" '
    set -e
    RECORDS="'"$RECORDS"'"
    OUT="'"$OUTDIR"'"
    total=$(find "$RECORDS" -name "*.json" 2>/dev/null | wc -l | tr -d " ")
    ok=$(find "$OUT" -maxdepth 1 -name "*.ok" 2>/dev/null | wc -l | tr -d " ")
    none=$(find "$OUT" -maxdepth 1 -name "*.none" 2>/dev/null | wc -l | tr -d " ")
    err=$(find "$OUT" -maxdepth 1 -name "*.err" 2>/dev/null | wc -l | tr -d " ")
    # Alter Materialisierer (MAT_ENTRY=materialize_old.mjs) kennt kein .ok/.none-
    # Format 1:1 -- zaehle .meta.zst zusaetzlich als "fertig", falls .ok fehlt.
    meta=$(find "$OUT" -maxdepth 1 -name "*.meta.zst" 2>/dev/null | wc -l | tr -d " ")
    fertig=$((ok + none))
    if [ "$fertig" -eq 0 ] && [ "$meta" -gt 0 ]; then fertig=$meta; fi
    rest=$((total - fertig - err))
    [ "$rest" -lt 0 ] && rest=0
    echo "records=$total fertig(ok/none)=$fertig fehler(err)=$err rest=$rest"
    if docker ps -a --format "{{.Names}} {{.Status}}" 2>/dev/null | grep -q "^of-mat2 "; then
      docker ps -a --format "{{.Names}} {{.Status}}" | grep "^of-mat2 "
      docker logs --tail 5 of-mat2 2>&1 | sed "s/^/  | /"
    else
      echo "of-mat2: kein Container"
    fi
  ' || echo "  (ssh/Abfrage fehlgeschlagen)"
done
