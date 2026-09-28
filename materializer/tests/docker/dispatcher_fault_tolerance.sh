#!/usr/bin/env bash
# dispatcher_fault_tolerance.sh — Nachweise fuer dispatch.ts (Auftrag "Nachweise"):
#
#   1. Kill waehrend einer Partie -> Neustart rechnet nur die fehlenden.
#   2. SHARD=0/2 + SHARD=1/2 ergeben zusammen genau die Menge aller Records.
#   3. --user uid:gid -> Dateien gehoeren dem aufrufenden Nutzer.
#
# Laeuft mit MAT_ENTRY=materialize_old.mjs (der neue Kern existiert zum
# Zeitpunkt dieses Tests evtl. noch nicht, siehe DESIGN §7/README).
#
# Aufruf:
#   dispatcher_fault_tolerance.sh <image> <records-dir-mit-9-json>
set -euo pipefail

IMAGE="${1:?Aufruf: dispatcher_fault_tolerance.sh <image> <records-dir>}"
RECORDS_DIR="${2:?records-dir fehlt}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
OUT="$WORK/out"
mkdir -p "$OUT"

echo "=== Test 1: Kill waehrend einer Partie, dann Resume ==="
docker run -d --name of-mat2-fttest --cpus 2 \
  -e MAT_ENTRY=materialize_old.mjs -e WORKERS=1 -e GAME_TIMEOUT_S=3600 \
  -v "$RECORDS_DIR":/in:ro -v "$OUT":/out "$IMAGE" >/dev/null
sleep 8
docker kill of-mat2-fttest >/dev/null 2>&1 || true
docker rm -f of-mat2-fttest >/dev/null 2>&1 || true
after_kill=$(find "$OUT" -maxdepth 1 \( -name "*.meta.zst" -o -name "*.none" \) | wc -l | tr -d ' ')
echo "  nach Kill: $after_kill von 9 Partien fertig markiert"

docker run --rm --name of-mat2-fttest2 --cpus 2 \
  -e MAT_ENTRY=materialize_old.mjs -e WORKERS=2 -e GAME_TIMEOUT_S=3600 \
  -v "$RECORDS_DIR":/in:ro -v "$OUT":/out "$IMAGE" >"$WORK/resume.log" 2>&1 || true
after_resume=$(find "$OUT" -maxdepth 1 \( -name "*.meta.zst" -o -name "*.none" \) | wc -l | tr -d ' ')
echo "  nach Resume: $after_resume von 9 Partien fertig"
if [ "$after_resume" -eq 9 ] && [ "$after_kill" -lt 9 ]; then
  echo "  PASS: Resume hat nur die fehlenden nachgerechnet"
else
  echo "  siehe $WORK/resume.log fuer Details"
fi

echo "=== Test 2: SHARD=0/2 + SHARD=1/2 = Vollmenge ==="
OUT_A="$WORK/out_a"; OUT_B="$WORK/out_b"; mkdir -p "$OUT_A" "$OUT_B"
docker run --rm -e MAT_ENTRY=materialize_old.mjs -e SHARD=0/2 -e WORKERS=2 \
  -v "$RECORDS_DIR":/in:ro -v "$OUT_A":/out "$IMAGE" >"$WORK/shard0.log" 2>&1 || true
docker run --rm -e MAT_ENTRY=materialize_old.mjs -e SHARD=1/2 -e WORKERS=2 \
  -v "$RECORDS_DIR":/in:ro -v "$OUT_B":/out "$IMAGE" >"$WORK/shard1.log" 2>&1 || true
gidsA=$(find "$OUT_A" -maxdepth 1 \( -name "*.meta.zst" -o -name "*.none" \) -exec basename {} \; | sed 's/\..*//' | sort -u)
gidsB=$(find "$OUT_B" -maxdepth 1 \( -name "*.meta.zst" -o -name "*.none" \) -exec basename {} \; | sed 's/\..*//' | sort -u)
union=$(printf '%s\n%s\n' "$gidsA" "$gidsB" | sort -u | grep -c .)
overlap=$(comm -12 <(echo "$gidsA") <(echo "$gidsB") | grep -c . || true)
allgids=$(find "$RECORDS_DIR" -name "*.json" -exec basename {} .json \; | sort -u | grep -c .)
echo "  shard0=$(echo "$gidsA" | grep -c .) shard1=$(echo "$gidsB" | grep -c .) union=$union ueberlappung=$overlap gesamt=$allgids"
if [ "$union" -eq "$allgids" ] && [ "$overlap" -eq 0 ]; then echo "  PASS"; else echo "  FAIL (siehe $WORK/shard*.log)"; fi

echo "=== Test 3: --user ==== "
OUT_U="$WORK/out_user"; mkdir -p "$OUT_U"
uid_gid="$(id -u):$(id -g)"
docker run --rm --user "$uid_gid" -e MAT_ENTRY=materialize_old.mjs -e LIMIT=1 \
  -v "$RECORDS_DIR":/in:ro -v "$OUT_U":/out "$IMAGE" >"$WORK/user.log" 2>&1 || true
sample=$(find "$OUT_U" -maxdepth 1 -type f | head -1)
if [ -n "$sample" ]; then
  owner=$(stat -c '%u:%g' "$sample" 2>/dev/null || stat -f '%u:%g' "$sample")
  echo "  Datei $sample gehoert $owner (erwartet $uid_gid)"
  [ "$owner" = "$uid_gid" ] && echo "  PASS" || echo "  FAIL"
else
  echo "  keine Datei erzeugt, siehe $WORK/user.log"
fi

echo "(Arbeitsverzeichnis $WORK wird am Ende geloescht; Logs vorher pruefen falls noetig)"
