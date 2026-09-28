#!/usr/bin/env bash
# Ultimus_Rex-Lauf je Host: Materialisierer v2 (of-mat2:378fc18), dann Zusatzlauf (of-zusatz:2)
# über alle Partien mit bestandener Hash-Prüfung. Zwei Phasen: rec/p1 (v0.33.11-14, sicherer Kern)
# vor rec/p2 (ältere Commits, auf 88cc95d8 umgebogen; die Hash-Prüfung entscheidet je Partie).
# Aufruf: setsid nohup bash ur_lauf.sh <kerne> <basis> > <basis>/lauf.out 2>&1 < /dev/null &
set -u
C=${1:-3}
B=${2:-$HOME/of-ur}
cd "$B" || exit 1
mkdir -p out zus
for P in p1 p2; do
  [ -d "rec/$P" ] || continue
  docker rm -f of-ur-mat of-ur-zus >/dev/null 2>&1
  echo "MAT $P START $(date +%T) kerne=$C partien=$(find rec/$P -name '*.json' | wc -l)" >> lauf.log
  docker run --rm --name of-ur-mat --cpus "$C" --cpu-shares 256 --memory "$((C * 3))g" \
    --user "$(id -u):$(id -g)" -e SHARD=0/1 -e WORKERS="$C" -e MAT_VERSION=378fc18 -e GAME_TIMEOUT_S=3600 \
    -v "$B/rec/$P:/in:ro" -v "$B/out:/out" of-mat2:378fc18 >> mat.log 2>&1
  echo "MAT $P ENDE $(date +%T) rc=$? ok=$(ls out | grep -c '\.ok$') err=$(ls out | grep -c '\.err$') none=$(ls out | grep -c '\.none$')" >> lauf.log
  # Auftrag: gid, commit8 (Baum), Record-Pfad relativ zu /in (= rec), Shard-Unterordner im Pool (".")
  : > auftrag.tsv
  n_weg=0
  for f in $(cd rec && ls "$P"/*/*.json); do
    gid=$(basename "$f" .json)
    ok="out/$gid.ok"
    [ -e "$ok" ] || continue
    if grep -q '"desync":null' "$ok" && ! grep -q '"tick_error":"' "$ok" \
       && ! grep -q '"checked":0[,}]' "$ok" && ! grep -q '"samples":0[,}]' "$ok"; then
      c8=$(head -c 400 "rec/$f" | grep -o '"gitCommit":"[0-9a-f]\{8\}' | cut -d'"' -f4)
      printf '%s\t%s\t%s\t.\n' "$gid" "$c8" "$f" >> auftrag.tsv
    else
      n_weg=$((n_weg + 1))
    fi
  done
  echo "ZUS $P START $(date +%T) auftrag=$(wc -l < auftrag.tsv) nicht_bestanden=$n_weg" >> lauf.log
  docker run --rm --name of-ur-zus --cpus "$C" --memory "$((C * 2))g" -e JOBS="$C" \
    -v "$B/rec:/in:ro" -v "$B/out:/pool:ro" -v "$B/zus:/out" -v "$B/auftrag.tsv:/auftrag.tsv:ro" \
    of-zusatz:2 >> zus.log 2>&1
  echo "ZUS $P ENDE $(date +%T) rc=$? dateien=$(ls zus | grep -c '\.zusatz\.zst$')" >> lauf.log
done
echo "FERTIG $(date +%T)" >> lauf.log
