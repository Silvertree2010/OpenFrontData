#!/bin/sh
# Holt die v2-Shards der Helferknoten nach arch und endet von selbst, sobald alle
# drei fertig sind (Marke .DONE_*) — spaetestens nach MAXH Stunden.
cd ~/projects/openfront-ai || exit 1
OUT=data/pool_shards_v2
mkdir -p "$OUT"
MAXH=${MAXH:-72}
ENDE=$(( $(date +%s) + MAXH * 3600 ))
hol() {
  for H in node-1.example node-2.example apollo.example; do
    rsync -a --exclude='.log_*' -e "ssh -o ConnectTimeout=6 -o BatchMode=yes" \
      "benutzer@$H:of-work/shards_v2/" "$OUT/" 2>/dev/null
  done
}
while :; do
  hol
  echo "[collect $(date +%m-%d_%H:%M)] shards=$(ls "$OUT" | grep -c 'meta.zst$') leer=$(ls "$OUT" | grep -c '\.none$') fertig=$(ls "$OUT"/.DONE_* 2>/dev/null | wc -l)"
  [ "$(ls "$OUT"/.DONE_* 2>/dev/null | wc -l)" -ge 3 ] && break
  [ "$(date +%s)" -gt "$ENDE" ] && { echo "[collect] Zeitgrenze erreicht, Abbruch"; break; }
  sleep 600
done
hol
python3 env/check_shards.py "$OUT" || echo "[collect] ACHTUNG: beanstandete Shards, siehe oben"
echo "[collect] fertig $(date +%m-%d_%H:%M): $(ls "$OUT" | grep -c 'meta.zst$') Shards, $(du -sh "$OUT" | cut -f1)"
