#!/usr/bin/env bash
# wrong_tsconfig_test.sh — Gegenprobe zu bundle_equivalence.sh: einmal MIT
# der falschen Option (useDefineForClassFields=true) buendeln und zeigen,
# dass es bricht oder abweicht (Auftrag Nachweise, Punkt 2).
#
# Arbeitet direkt auf einem Engine-Baum (wie ihn der Dockerfile-Build je Commit
# anlegt: <tree>/vendor/openfront mit node_modules, <tree>/env/materialize.ts+
# obs.ts, <tree>/tsconfig.json), nicht auf dem fertigen Image — das spart einen
# vollen Docker-Rebuild fuer eine einzelne Compileroption und benutzt denselben
# esbuild-Binary/dieselbe Version wie im Image.
#
# Aufruf:
#   wrong_tsconfig_test.sh <tree-dir> <record.json> <richtiges .maps zum Vergleich>
set -euo pipefail

TREE="${1:?Aufruf: wrong_tsconfig_test.sh <tree-dir> <record.json> <richtiges .maps>}"
RECORD="${2:?record.json fehlt}"
GOOD_MAPS="${3:?Pfad zu einer bekannt richtigen .maps-Datei fehlt}"

ESBUILD="$TREE/vendor/openfront/node_modules/.bin/esbuild"
WORK="$(mktemp -d)"

# Falsches tsconfig: Kopie mit useDefineForClassFields=true. sed statt JSON-
# Parser, weil tsconfig.json // Kommentare enthaelt (kein reines JSON).
# MUSS im Baum selbst liegen (nicht unter /tmp): die "paths"-Eintraege sind
# relativ zum Ordner der tsconfig.json, ein Verschieben bricht die Aufloesung
# von resources/*.json-Importen.
BAD_TSCONFIG="$TREE/tsconfig.bad.json"
trap 'rm -rf "$WORK" "$BAD_TSCONFIG"' EXIT
sed 's/"useDefineForClassFields"[[:space:]]*:[[:space:]]*false/"useDefineForClassFields": true/' \
  "$TREE/tsconfig.json" > "$BAD_TSCONFIG"
grep -q '"useDefineForClassFields": true' "$BAD_TSCONFIG" || { echo "[wrong-tsconfig] sed hat die Option nicht gefunden/ersetzt"; exit 1; }

echo "[wrong-tsconfig] buendle mit useDefineForClassFields=true ..."
set +e
"$ESBUILD" "$TREE/env/materialize.ts" \
  --bundle --platform=node --format=esm --target=node24 \
  --tsconfig="$BAD_TSCONFIG" \
  --outfile="$WORK/materialize_bad.mjs" 2>"$WORK/build.err"
build_rc=$?
set -e
if [ "$build_rc" -ne 0 ]; then
  echo "[wrong-tsconfig] Buendeln selbst schon gescheitert:"
  cat "$WORK/build.err"
  exit 0
fi

mkdir -p "$WORK/out"
set +e
node "$WORK/materialize_bad.mjs" "$RECORD" "$WORK/out" >"$WORK/run.log" 2>&1
run_rc=$?
set -e

gid=$(basename "$RECORD" .json)
badmaps="$WORK/out/${gid}.maps"

if [ "$run_rc" -ne 0 ]; then
  echo "[wrong-tsconfig] Lauf bricht wie erwartet (exit $run_rc):"
  tail -20 "$WORK/run.log"
  exit 0
fi

if [ ! -f "$badmaps" ]; then
  echo "[wrong-tsconfig] Lauf lief durch, aber ohne .maps (0 Samples o.ae.) — ebenfalls ein Unterschied zur Referenz."
  exit 0
fi

h_bad=$(sha256sum "$badmaps" | cut -d' ' -f1)
h_good=$(sha256sum "$GOOD_MAPS" | cut -d' ' -f1)
if [ "$h_bad" = "$h_good" ]; then
  echo "[wrong-tsconfig] UNERWARTET: byte-gleich trotz falscher Option — Annahme ueberpruefen!"
  exit 1
else
  echo "[wrong-tsconfig] wie erwartet abweichend: bad=$h_bad good=$h_good"
  exit 0
fi
