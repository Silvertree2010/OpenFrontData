#!/usr/bin/env bash
# Startet einen Harness-Lauf auf arch. Nur Tests, schreibt nur unter ~/mat-dev/cells-out.
#   run_arch.sh <modus> <gid> [tag] [harness-optionen...]
# Findet Record und Commit in ~/mat-dev/records.tsv, laeuft in ~/mat-dev/w/<sha8>
# (Code liegt dort unter mat-cells/). Ergebnis: ~/mat-dev/cells-out/<modus>_<gid>_<tag>.json
set -euo pipefail
mode=$1; gid=$2; tag=${3:-a}; shift 3 || shift $#
row=$(awk -F'\t' -v g="$gid" '$2==g {print $1"\t"$3; exit}' ~/mat-dev/records.tsv)
[ -n "$row" ] || { echo "gid $gid nicht in records.tsv" >&2; exit 2; }
rec=${row%%$'\t'*}; c8=${row##*$'\t'}
extra=()
if [ "$mode" = cost ]; then extra=(--thin ~/mat-dev/ref/thin/$c8/a/$gid.meta.zst); fi
out=~/mat-dev/cells-out; mkdir -p "$out"
cd ~/mat-dev/w/$c8
nice -n 10 timeout 3h npx tsx mat-cells/tests/cells/harness.ts "$mode" "$rec" "${extra[@]}" "$@" \
  > "$out/${mode}_${gid}_${tag}.json" 2> "$out/${mode}_${gid}_${tag}.log"
echo "$mode $gid $tag rc=$? $(head -c 300 "$out/${mode}_${gid}_${tag}.json")"
