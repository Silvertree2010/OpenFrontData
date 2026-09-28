#!/usr/bin/env bash
# Baut die Erweiterung. Gebaut wird IM Client-Arbeitsbaum, weil der Kern die Engine und
# das Wire-Format von dort holt (../src/core/...). Voreinstellung: ~/openfront-client-v33,
# der Stand, der auch ausgeliefert wird (v0.33.14).
#
#   ./bauen.sh [pfad-zum-client] [ziel]
set -euo pipefail
HIER="$(cd "$(dirname "$0")" && pwd)"
CLIENT="${1:-$HOME/openfront-client-v33}"
ZIEL="${2:-$HOME/ki-beobachter}"
ARBEIT="$CLIENT/erweiterung"
ESB="$CLIENT/node_modules/.bin/esbuild"

[ -x "$ESB" ] || { echo "esbuild fehlt in $CLIENT"; exit 1; }
mkdir -p "$ARBEIT" "$ZIEL"
# Wer schon im Arbeitsordner steht, kopiert nicht auf sich selbst.
if [ "$HIER" != "$ARBEIT" ]; then cp "$HIER"/*.ts "$ARBEIT"/; fi

# Zusatzfelder (Netz D1): EIN Modul für Offline-Lauf, Arena und Erweiterung. Es kommt
# byte-gleich aus dem Trainer-Repo (zusatz/src/felder.ts) und heisst hier zusatzFelder.ts.
# Keine eigene Fassung in der Erweiterung — ohne Quelle bricht der Bau ab.
ZUSATZ_FELDER="${ZUSATZ_FELDER:-$HOME/d1-lauf/zusatz/src/felder.ts}"
[ -f "$ZUSATZ_FELDER" ] || { echo "zusatz/src/felder.ts fehlt ($ZUSATZ_FELDER) — ZUSATZ_FELDER=… setzen"; exit 1; }
grep -q "export class ZusatzSpur" "$ZUSATZ_FELDER" || { echo "$ZUSATZ_FELDER ist ein Stand ohne ZusatzSpur"; exit 1; }
cp "$ZUSATZ_FELDER" "$ARBEIT/zusatzFelder.ts"
echo "Zusatzfelder: sha256 $(sha256sum "$ARBEIT/zusatzFelder.ts" | cut -c1-16) aus $ZUSATZ_FELDER"

# 1. Worker (Engine + Beobachtung) als eigenes Bündel
# Der Engine-Code stammt aus einem Node-Projekt und greift auf `process` zu; im Worker
# gibt es das nicht ("process is not defined"). Darum NODE_ENV fest ersetzen und einen
# schlanken Ersatz voranstellen.
"$ESB" "$ARBEIT/engineSpiegel.ts" --bundle --format=iife --target=es2022 \
  --define:process.env.NODE_ENV='"production"' \
  --banner:js='globalThis.process ??= { env: { NODE_ENV: "production" }, platform: "browser", argv: [], version: "", versions: {}, type: "browser", nextTick: (f) => queueMicrotask(f), on: () => {}, cwd: () => "/" };' \
  --outfile="$ARBEIT/engineSpiegel.bundle.js" --log-level=warning

# 2. Quelltext des Workers als Zeichenkette einbetten: ein Skript in der Seite kommt
#    an die Dateien der Erweiterung nicht heran, der Worker entsteht aus einem Blob.
node -e '
const fs=require("fs");
const q=fs.readFileSync(process.argv[1],"utf8");
fs.writeFileSync(process.argv[2],
  "// Erzeugt von bauen.sh — nicht von Hand ändern.\nexport const WORKER_QUELLE = "
  + JSON.stringify(q) + ";\n");
' "$ARBEIT/engineSpiegel.bundle.js" "$ARBEIT/workerQuelle.gen.ts"

# 3. Das Skript, das in der Seite läuft: Kern + Politik + Einblendung in einem
"$ESB" "$ARBEIT/start.ts" --bundle --format=iife --target=es2022 \
  --outfile="$ZIEL/start.js" --log-level=warning

# 4. Brücke: Inhaltsskript (isoliert) und Hintergrunddienst
"$ESB" "$ARBEIT/brueckeInhalt.ts" --bundle --format=iife --target=es2022 \
  --outfile="$ZIEL/brueckeInhalt.js" --log-level=warning
"$ESB" "$ARBEIT/dienst.ts" --bundle --format=iife --target=es2022 \
  --outfile="$ZIEL/dienst.js" --log-level=warning

[ "$HIER/manifest.json" -ef "$ZIEL/manifest.json" ] || cp "$HIER/manifest.json" "$ZIEL/"
[ -f "$HIER/README.md" ] && cp "$HIER/README.md" "$ZIEL/"
echo "gebaut nach $ZIEL:"
ls -l "$ZIEL" | awk '{print "  "$9" "$5}'
