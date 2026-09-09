#!/usr/bin/env bash
# Wiederaufnahme-Test: eine winzige .meta.zst darf NICHT als fertiges Spiel gelten.
#
# Genau daran hat der Pool 7827 leere Shards behalten: die Datei existierte, also
# uebersprang jeder Folgelauf das Spiel fuer immer. Der Test stellt das alte
# Verhalten mit MIN_META=0 nach — dort MUSS er fehlschlagen — und prueft danach
# das neue. Zusaetzlich: ein Spiel ohne Samples hinterlaesst eine .none-Marke und
# wird beim naechsten Lauf nicht neu gerechnet.
#
#   env/test_resume.sh [record.json] [leeres_record.json]
set -u
cd "$(dirname "$0")/.."
REC="${1:-data/pool_records/nk/nkrVkM6Y.json}"
LEER="${2:-data/pool_records/13/13UD59Ug.json}"
GID="$(basename "$REC" .json)"; LGID="$(basename "$LEER" .json)"
DIR="$(mktemp -d)"; trap 'rm -rf "$DIR"' EXIT
fehler=0
pruefe() { if [ "$2" = "$3" ]; then echo "  ok   $1: $2"; else echo "  FEHL $1: $2 (erwartet $3)"; fehler=1; fi; }

# Kaputter Rest eines abgebrochenen Laufs: winzige meta.zst + leere maps
printf '\x28\xb5\x2f\xfd\x20\x00\x01\x00\x00' > "$DIR/$GID.meta.zst"
: > "$DIR/$GID.maps"
klein=$(wc -c < "$DIR/$GID.meta.zst")

echo "[1] altes Verhalten (MIN_META=0): winzige meta.zst gilt als fertig — muss ueberspringen"
out=$(MIN_META=0 npx tsx env/materialize.ts --list <(echo "$REC") "$DIR" 2>&1 | tail -1)
echo "    $out"
pruefe "uebersprungen (Fehlerbild)" "$(wc -c < "$DIR/$GID.meta.zst")" "$klein"

echo "[2] mit Fix: winziger Shard wird neu materialisiert"
NOOP_EVERY=200 npx tsx env/materialize.ts --list <(echo "$REC") "$DIR" 2>&1 | tail -1 | sed 's/^/    /'
neu=$(wc -c < "$DIR/$GID.meta.zst")
[ "$neu" -gt 1000 ] && pruefe "meta.zst gewachsen" "gross" "gross" || pruefe "meta.zst gewachsen" "$neu" ">1000"

echo "[3] echter Shard wird beim naechsten Lauf uebersprungen"
out=$(NOOP_EVERY=200 npx tsx env/materialize.ts --list <(echo "$REC") "$DIR" 2>&1 | tail -1)
echo "    $out"
pruefe "unveraendert" "$(wc -c < "$DIR/$GID.meta.zst")" "$neu"

if [ -f "$LEER" ]; then
  echo "[4] Partie ohne Samples: .none-Marke statt Pseudo-Shard, danach uebersprungen"
  NOOP_EVERY=200 npx tsx env/materialize.ts --list <(echo "$LEER") "$DIR" 2>&1 | tail -1 | sed 's/^/    /'
  [ -f "$DIR/$LGID.none" ] && pruefe ".none angelegt" "ja" "ja" || pruefe ".none angelegt" "nein" "ja"
  [ -f "$DIR/$LGID.meta.zst" ] && pruefe "keine leere meta.zst" "vorhanden" "fehlt" || pruefe "keine leere meta.zst" "fehlt" "fehlt"
  out=$(NOOP_EVERY=200 npx tsx env/materialize.ts --list <(echo "$LEER") "$DIR" 2>&1 | tail -1)
  echo "    $out"
fi

[ "$fehler" = 0 ] && echo "TEST OK" || echo "TEST FEHLGESCHLAGEN"
exit "$fehler"
