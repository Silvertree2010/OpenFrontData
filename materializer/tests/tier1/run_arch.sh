#!/usr/bin/env bash
# Tier-1-Nachweis auf arch. Aufruf (auf arch):
#   bash run_arch.sh <liste.tsv> <outroot> <mode> [extra harness args...]
# liste.tsv: je Zeile "<commit8>\t<gid>\t<record-pfad>"
# Läuft je Record in ~/mat-dev/w/<commit8>/mat-tier1 (Importpfade ../../vendor passen),
# nice 10, höchstens 3 gleichzeitig. Ausgabe je Record in <outroot>/<commit8>/<gid>/.
set -u
LIST=$1; OUT=$2; MODE=$3; shift 3
EXTRA="$*"
export OUT MODE EXTRA
run_one() {
  c=$1; g=$2; rec=$3
  d="$OUT/$c/$g"; mkdir -p "$d"
  cd ~/mat-dev/w/$c || exit 1
  tag=$MODE$(echo "$EXTRA" | tr ' @' '_-')
  if nice -n 10 npx tsx mat-tier1/tests/tier1/harness.ts "$rec" "$d" --mode "$MODE" $EXTRA > "$d/log.$tag.txt" 2>&1; then
    echo "ok   $c $g $tag"
  else
    echo "FAIL $c $g $tag: $(tail -3 "$d/log.$tag.txt" | tr '\n' ' ')"
  fi
}
export -f run_one
awk -F'\t' 'NF>=3{print $1" "$2" "$3}' "$LIST" | xargs -P3 -L1 bash -c 'run_one "$0" "$1" "$2"'
