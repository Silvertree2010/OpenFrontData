#!/usr/bin/env bash
# bundle_equivalence.sh — Nachweis Punkt 2 aus dem Auftrag:
#
#   Der ALTE Materialisierer, gebuendelt (im Image) vs. `npx tsx` (Referenz),
#   muss fuer die 9 Referenz-Records byte-gleiche .maps ergeben (sha256).
#
# Aufruf:
#   bundle_equivalence.sh <image> <records.tsv> <ref-root> [gid...]
#
#   <image>       gebautes of-mat2-Image (materializer/docker/Dockerfile)
#   <records.tsv> Spalten: path gameID commit8 size gameMap players turns
#                 (Format wie ~/mat-dev/records.tsv)
#   <ref-root>    Wurzel der npx-tsx-Referenzausgaben, <ref-root>/<sha8>/.../<gid>.maps
#   [gid...]      optional: nur diese gids pruefen (Default: die 9 aus DESIGN §10)
#
# Fuer jede gid: Container mit ueberschriebenem Entrypoint startet
# `node /app/w/<sha8>/dist/materialize_old.mjs <record> /out` direkt (ohne
# dispatch.ts), damit dieser Test unabhaengig vom Dispatcher ist. Vergleicht
# sha256 der entstandenen .maps mit der Referenz.
set -euo pipefail

IMAGE="${1:?Aufruf: bundle_equivalence.sh <image> <records.tsv> <ref-root> [gid...]}"
RECORDS_TSV="${2:?records.tsv fehlt}"
REF_ROOT="${3:?ref-root fehlt}"
shift 3
GIDS=("$@")
if [ "${#GIDS[@]}" -eq 0 ]; then
  GIDS=(uLwSPQFK dJtnLxJA FCxzAh2Y H2NkRg5R JwFNdofK omTgUNMh bqfEEyVi A9iejjLi HzjyLWzY)
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0 fail=0 missing_ref=0

for gid in "${GIDS[@]}"; do
  line=$(awk -F'\t' -v g="$gid" '$2==g{print}' "$RECORDS_TSV" | head -1)
  if [ -z "$line" ]; then echo "[$gid] FEHLT in $RECORDS_TSV"; fail=$((fail+1)); continue; fi
  recpath=$(echo "$line" | cut -f1)
  sha8=$(echo "$line" | cut -f3)

  refmaps=$(find "$REF_ROOT/$sha8" -name "${gid}.maps" 2>/dev/null | head -1)
  if [ -z "$refmaps" ]; then
    echo "[$gid] keine Referenz unter $REF_ROOT/$sha8 gefunden"
    missing_ref=$((missing_ref+1)); continue
  fi

  outdir="$WORK/$gid"
  mkdir -p "$outdir"
  recdir=$(dirname "$recpath")
  recfile=$(basename "$recpath")

  docker run --rm \
    --entrypoint node \
    -v "$recdir":/rec:ro \
    -v "$outdir":/out \
    "$IMAGE" "/app/w/${sha8}/dist/materialize_old.mjs" "/rec/${recfile}" /out \
    > "$outdir/run.log" 2>&1 || { echo "[$gid] Container-Lauf fehlgeschlagen, siehe $outdir/run.log"; fail=$((fail+1)); continue; }

  gotmaps="$outdir/${gid}.maps"
  if [ ! -f "$gotmaps" ]; then
    echo "[$gid] keine .maps erzeugt (siehe $outdir/run.log)"; fail=$((fail+1)); continue
  fi

  h1=$(sha256sum "$gotmaps" | cut -d' ' -f1)
  h2=$(sha256sum "$refmaps" | cut -d' ' -f1)
  if [ "$h1" = "$h2" ]; then
    echo "[$gid] PASS  ($sha8, sha256 $h1)"
    pass=$((pass+1))
  else
    echo "[$gid] FAIL  ($sha8, gebuendelt=$h1 referenz=$h2)"
    fail=$((fail+1))
  fi
done

echo "--- $pass PASS, $fail FAIL, $missing_ref ohne Referenz (von ${#GIDS[@]}) ---"
[ "$fail" -eq 0 ] && [ "$missing_ref" -eq 0 ]
