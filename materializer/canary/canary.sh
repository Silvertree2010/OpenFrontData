#!/usr/bin/env bash
# Kanarienlauf (DESIGN.md §9): drei Läufe im selben Image, danach die harten Tore.
#
#   canary.sh <image> <records_root> <liste.tsv> <out_root> <cpus> <frei_gb|unbekannt> [<inventar.tsv>]
#
#   image         z.B. of-mat2:test (muss auf diesem Host existieren)
#   records_root  Wurzel der Records, wird als /in (nur lesbar) eingehängt
#   liste.tsv     Ausgabe von select.py (Spalte 1 relpath, Spalte 2 gid, '#' = Kommentar)
#   out_root      bekommt A/ B/ C/, Logs, Zeiten, gates.md, gates.json
#   cpus          --cpus des Containers, für alle drei Läufe gleich; WORKERS = cpus
#   frei_gb       freier Platz für die Zweigregel (Entwurf §7.3), oder "unbekannt"
#   inventar.tsv  optional, für die Hochrechnung auf 18'018 Partien (Grössenverteilung)
#
# Läufe (nacheinander, nie gleichzeitig, damit sie dieselbe CPU-Lage sehen):
#   A  alt:                 MAT_ENTRY=materialize_old.mjs NOOP_EVERY=200 THIN=30
#   B  neu, mit allem:      CELLS=1 TIER1_PCT=100 (Rest Standard)
#   C  neu, ohne Zusatz:    CELLS=0 TIER1_PCT=0
#
# Umgebung:
#   NICE=10              Priorität der Prozesse im Container (Entrypoint wird mit nice umwickelt)
#   RUNS="A B C"         nur einzelne Läufe (z.B. nach Abbruch); fertige Partien überspringt dispatch
#   SKIP_RUNS=1          keine Läufe, nur die Tore auf vorhandener Ausgabe
#   RUN_TIMEOUT=86400    Obergrenze je Lauf in Sekunden (danach docker kill)
#   CANARY_DOCKER_ARGS   zusätzliche docker-run-Argumente, NUR für Entwicklung (z.B. ein
#                        frisch gebautes Bündel über das im Image legen). Wird im Bericht vermerkt.
#
# Exit 0 nur, wenn gates.py alle Tore grün meldet. 2 = Aufruffehler, 3 = Voraussetzung fehlt.
set -uo pipefail

if [ $# -lt 6 ]; then sed -n '2,30p' "$0"; exit 2; fi
IMAGE=$1; ROOT=$(cd "$2" && pwd) || exit 2; LIST=$(cd "$(dirname "$3")" && pwd)/$(basename "$3")
OUT=$4; CPUS=$5; FREE=$6; INV=${7:-}
HERE=$(cd "$(dirname "$0")" && pwd)
NICE=${NICE:-10}; RUNS=${RUNS:-"A B C"}; RUN_TIMEOUT=${RUN_TIMEOUT:-86400}
EXTRA=${CANARY_DOCKER_ARGS:-}

mkdir -p "$OUT" || exit 2
OUT=$(cd "$OUT" && pwd)
[ -f "$LIST" ] || { echo "Liste fehlt: $LIST" >&2; exit 2; }
[[ "$CPUS" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "cpus keine Zahl: $CPUS" >&2; exit 2; }

# ── Liste → gid-Datei für dispatch (LIST erwartet gids, nicht Pfade) ─────────
awk -F'\t' '!/^#/ && NF>=2 {print $2}' "$LIST" > "$OUT/gids.txt"
N=$(wc -l < "$OUT/gids.txt")
[ "$N" -gt 0 ] || { echo "Liste ohne Einträge" >&2; exit 2; }
miss=0
while IFS=$'\t' read -r rel gid _; do
  case "$rel" in \#*|"") continue;; esac
  [ -f "$ROOT/$rel" ] || { echo "Record fehlt: $ROOT/$rel" >&2; miss=1; }
done < "$LIST"
[ $miss -eq 0 ] || exit 3
# dispatch nimmt je gid den ersten Fund unter /in. Liegt dieselbe gid zweimal unter der
# Wurzel, könnte ein anderer Record gerechnet werden als ausgewählt: vorher prüfen.
dups=$(find "$ROOT" -name '*.json' -printf '%f\n' | sort | uniq -d | sed 's/\.json$//' | grep -Fxf "$OUT/gids.txt" || true)
[ -z "$dups" ] || { echo "gid mehrfach unter $ROOT: $dups" >&2; exit 3; }

# ── Image prüfen: altes Bündel und MAT_ENTRY-Schalter ─────────────────────────
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "Image $IMAGE fehlt" >&2; exit 3; }
probe=$(docker run --rm --entrypoint sh "$IMAGE" -c '
  for d in /app/w/*/; do s=$(basename "$d");
    [ -f "$d/dist/materialize_old.mjs" ] && echo "old $s"; [ -f "$d/dist/materialize.mjs" ] && echo "new $s"; done
  grep -q MAT_ENTRY /app/dist/dispatch.mjs && echo "switch"; command -v nice >/dev/null && echo "nice"' 2>&1)
echo "$probe" > "$OUT/image_probe.txt"
grep -q '^switch' <<<"$probe" || { echo "dispatch.mjs im Image kennt MAT_ENTRY nicht: altes Bündel nicht wählbar" >&2; exit 3; }
for s in $(awk -F'\t' '!/^#/ && NF>=3 {print $3}' "$LIST" | sort -u); do
  grep -q "^old $s" <<<"$probe" || { echo "materialize_old.mjs fehlt für $s im Image" >&2; exit 3; }
  grep -q "^new $s" <<<"$probe" || { echo "materialize.mjs fehlt für $s im Image" >&2; exit 3; }
done
ENTRY=(--entrypoint node); CMD=(/app/dist/dispatch.mjs)
if grep -q '^nice' <<<"$probe"; then ENTRY=(--entrypoint nice); CMD=(-n "$NICE" node /app/dist/dispatch.mjs); fi

# ── ein Lauf ──────────────────────────────────────────────────────────────────
NAMES=()
cleanup() { for n in "${NAMES[@]}"; do docker kill "$n" >/dev/null 2>&1; done; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

run() {  # run <A|B|C> <env...>
  local r=$1; shift
  local dir="$OUT/$r" name="canary-$r-$$"
  mkdir -p "$dir"
  local envs=(-e LIST=/list/gids.txt -e WORKERS="${CPUS%.*}")
  for kv in "$@"; do envs+=(-e "$kv"); done
  NAMES+=("$name")
  local t0; t0=$(date +%s.%N)
  echo "[canary] Lauf $r: $* (cpus $CPUS)" | tee -a "$OUT/canary.log"
  # shellcheck disable=SC2086
  timeout --signal=TERM "$RUN_TIMEOUT" docker run --rm --name "$name" --cpus "$CPUS" \
    --user "$(id -u):$(id -g)" \
    -v "$ROOT:/in:ro" -v "$dir:/out" -v "$OUT/gids.txt:/list/gids.txt:ro" \
    "${envs[@]}" $EXTRA "${ENTRY[@]}" "$IMAGE" "${CMD[@]}" > "$dir.log" 2>&1
  local rc=$?
  [ $rc -eq 124 ] && docker kill "$name" >/dev/null 2>&1
  local t1; t1=$(date +%s.%N)
  printf '{"run":"%s","rc":%d,"wall_s":%s,"cpus":"%s","env":"%s","extra_docker_args":"%s","image":"%s"}\n' \
    "$r" "$rc" "$(awk "BEGIN{printf \"%.1f\", $t1 - $t0}")" "$CPUS" "$*" "$EXTRA" "$IMAGE" > "$OUT/$r.run.json"
  echo "[canary] Lauf $r: rc=$rc, $(tail -1 "$dir.log")" | tee -a "$OUT/canary.log"
}

if [ "${SKIP_RUNS:-0}" != 1 ]; then
  for r in $RUNS; do
    case $r in
      A) run A MAT_ENTRY=materialize_old.mjs NOOP_EVERY=200 THIN=30 ;;
      B) run B CELLS=1 TIER1_PCT=100 ;;
      C) run C CELLS=0 TIER1_PCT=0 ;;
      *) echo "unbekannter Lauf $r" >&2; exit 2 ;;
    esac
  done
fi

args=(--out "$OUT" --list "$LIST" --records-root "$ROOT" --free-gb "$FREE")
[ -n "$INV" ] && args+=(--inventory "$INV")
python3 "$HERE/gates.py" "${args[@]}"
exit $?
