#!/usr/bin/env bash
# Vier Inferenz-Server für den Vierfenster-Viewer, einer je Fenster.
#
# Die Wahlart (Ziehen oder Argmax, Temperatur, Top-k) ist in inf_d0.py eine Einstellung
# des Servers, nicht der Anfrage. Ein Fenster wählt sie also, indem es einen anderen
# Port wählt — darum hier eine Tabelle Port → Checkpoint → Wahlart.
#
# Alles auf CPU: die GPU bleibt frei. 16 Kerne, vier Server, --threads 3 lässt Luft.
# Gemessen kostet eine Anfrage rund 20 ms; ein Fenster fragt alle 32 Ticks.
#
#   ./starte_inf.sh                 # startet, was noch nicht läuft
#   ./starte_inf.sh --stopp         # beendet die selbst gestarteten
#   ./starte_inf.sh --lage          # zeigt, was antwortet
#
# Belegte Ports werden übersprungen, nicht überfahren — auf 8650 läuft oft schon einer.
# 8652 und 8653 bleiben frei: 8652 ist die Vorgabe von relay_server.mjs, und vite leitet
# /relay auf 127.0.0.1:8652 derselben Maschine. Auf arch laufen vite und die
# Inferenz-Server zusammen — ein Server auf 8652 wäre dort das Relay.
set -u

NETZ_DEV="${NETZ_DEV:-$HOME/mat-dev/netz-dev}"
PYTHON="${PYTHON:-$HOME/mat-dev/torchenv/bin/python}"
REPUTATION="${REPUTATION:-$HOME/projects/openfront-ai/data/reputation.json}"
SCHWELLE="${SCHWELLE:-0.0159564614291412}"   # bc2, Ziel 44 Ticks, k=32
TAKT="${TAKT:-32}"
THREADS="${THREADS:-3}"
PIDDATEI="${PIDDATEI:-$HOME/.vier-viewer-inf.pids}"

# Port | Checkpoint | ziehen | temp | top_k | Beschriftung im Viewer
TABELLE=(
  "8650|checkpoints/bc2.pt|1|1.0|4|bc2 · Ziehen T1.0 k4"
  "8651|checkpoints/bc2.pt|0|1.0|0|bc2 · Argmax"
  "8654|checkpoints/bc2_s60000.pt|1|1.0|4|bc2_s60000 · Ziehen"
  "8655|checkpoints/bc2_s20000.pt|1|1.0|4|bc2_s20000 · Ziehen"
)

antwortet() { curl -sf -m 2 "http://127.0.0.1:$1/" >/dev/null 2>&1; }

lage() {
  for eintrag in "${TABELLE[@]}"; do
    IFS='|' read -r port ckpt ziehen temp topk name <<<"$eintrag"
    if antwortet "$port"; then
      echo "  $port  $name  →  $(curl -s -m 2 "http://127.0.0.1:$port/")"
    else
      echo "  $port  $name  →  tot"
    fi
  done
}

stopp() {
  [ -f "$PIDDATEI" ] || { echo "keine eigene PID-Datei, nichts zu beenden"; return; }
  while read -r pid; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "beendet: $pid"
  done <"$PIDDATEI"
  rm -f "$PIDDATEI"
}

case "${1:-}" in
  --stopp) stopp; exit 0 ;;
  --lage) lage; exit 0 ;;
esac

cd "$NETZ_DEV" || { echo "kein $NETZ_DEV"; exit 1; }
: >"$PIDDATEI"
for eintrag in "${TABELLE[@]}"; do
  IFS='|' read -r port ckpt ziehen temp topk name <<<"$eintrag"
  if antwortet "$port"; then
    echo "Port $port läuft schon — bleibt, wie er ist ($name)"
    continue
  fi
  if [ ! -f "$ckpt" ]; then
    echo "Port $port übersprungen: $ckpt fehlt"
    continue
  fi
  ziehen_flag=()
  [ "$ziehen" = "1" ] && ziehen_flag=(--ziehen)
  PYTHONPATH=materializer/py nohup "$PYTHON" trainer/inf_d0.py \
    --ckpt "$ckpt" --port "$port" --host 0.0.0.0 --device cpu --threads "$THREADS" \
    --schwelle "$SCHWELLE" --decide-every "$TAKT" --reputation "$REPUTATION" \
    "${ziehen_flag[@]}" --temp "$temp" --top-k "$topk" \
    >"/tmp/inf_$port.log" 2>&1 &
  echo "$!" >>"$PIDDATEI"
  echo "gestartet: $port  $name  (PID $!)"
done

echo "warte, bis alle antworten …"
for eintrag in "${TABELLE[@]}"; do
  IFS='|' read -r port rest <<<"$eintrag"
  for _ in $(seq 180); do antwortet "$port" && break; sleep 1; done
done
lage
