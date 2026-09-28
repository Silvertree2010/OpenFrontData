#!/usr/bin/env bash
# Baut das Bild für den Zusatzlauf auf arch und speichert es als Datei zum Verteilen.
#
#   zusatz/docker/bauen.sh [<baeume>] [<zusatz-quelle>] [<ziel-tar>]
# Standard: ~/mat-dev/w als Quelle der Engine-Bäume, dieses Repo als Quelle von zusatz/,
# Ausgabe ~/mat-dev/zusatz-image.tar.
#
# Der Kontext entsteht mit cp -al (Hardlinks, kein zusätzlicher Plattenplatz) und enthält
# je Commit vendor/openfront (mit node_modules und resources), tsconfig.json und zusatz/src.
set -euo pipefail
BAEUME=${1:-$HOME/mat-dev/w}
QUELLE=${2:-$(cd "$(dirname "$0")/.." && pwd)}
ZIEL=${3:-$HOME/mat-dev/zusatz-image.tar}
KONTEXT=$(mktemp -d "$HOME/mat-dev/zusatz-ctx-XXXX")
trap 'rm -rf "$KONTEXT"' EXIT

for c in "$BAEUME"/*/; do
  sha=$(basename "$c")
  [ -d "$c/vendor/openfront" ] || continue
  mkdir -p "$KONTEXT/w/$sha"
  cp -al "$c/vendor" "$KONTEXT/w/$sha/vendor"          # echte Verzeichnisse, kein Symlink
  cp "$c/tsconfig.json" "$KONTEXT/w/$sha/tsconfig.json"
  cp -r "$QUELLE" "$KONTEXT/w/$sha/zusatz"
  echo "Baum $sha vorbereitet"
done
cp "$QUELLE/treiber.py" "$KONTEXT/treiber.py"
cp "$QUELLE/docker/Dockerfile" "$KONTEXT/Dockerfile"

docker build -t of-zusatz:1 "$KONTEXT"
docker save of-zusatz:1 -o "$ZIEL"
echo "Bild gebaut und gespeichert: $ZIEL ($(du -h "$ZIEL" | cut -f1))"
echo "Verteilen: ssh <host> 'docker load' < $ZIEL"
