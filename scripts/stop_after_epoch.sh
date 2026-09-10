#!/usr/bin/env bash
# Stoppt das BC-Training sauber, sobald Epoche 2 (Index 1) fertig ist.
# Wichtig: der Wiederaufnahmepunkt muss HINTER der Epochengrenze liegen,
# sonst wiederholt der naechste Lauf die ganze Epoche (135 min).
set -u
cd "$HOME/projects/openfront-ai" || exit 9
SNAP=checkpoints/bc_big_e1.pt
LOG=logs/stop_after_epoch.log
log(){ echo "$(date +%H:%M:%S) $*" >> "$LOG"; }

log "Waechter gestartet, wartet auf $SNAP"
for _ in $(seq 1 2880); do [ -f "$SNAP" ] && break; sleep 5; done
[ -f "$SNAP" ] || { log "ABBRUCH: Snapshot kam nicht"; exit 1; }

s1=$(stat -c %s "$SNAP"); sleep 6; s2=$(stat -c %s "$SNAP")
while [ "$s1" != "$s2" ]; do s1=$s2; sleep 6; s2=$(stat -c %s "$SNAP"); done
log "Snapshot vollstaendig ($s2 Bytes)"

PID=$(pgrep -f "env/bc_fi[t]\.py" | head -1)
if [ -n "$PID" ]; then
  kill -TERM "$PID"; log "SIGTERM an $PID"
  for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 2; done
  if kill -0 "$PID" 2>/dev/null; then kill -KILL "$PID"; log "SIGKILL noetig"; fi
fi
log "Trainer gestoppt"

.venv/bin/python - >> "$LOG" 2>&1 <<'PY'
import torch, shutil, os
ck, sn = "checkpoints/bc_big.pt", "checkpoints/bc_big_e1.pt"
def ep(p):
    try: return torch.load(p, map_location="cpu", weights_only=False).get("epoch")
    except Exception as e: return f"FEHLER {e}"
e_ck, e_sn = ep(ck), ep(sn)
print(f"  bc_big.pt Epoche={e_ck} | bc_big_e1.pt Epoche={e_sn}")
if e_sn == 2 and e_ck != 2:
    shutil.copy2(sn, ck + ".tmp"); os.replace(ck + ".tmp", ck)
    print("  bc_big.pt aus Snapshot ersetzt -> morgen startet Epoche 3")
else:
    print("  bc_big.pt bleibt unveraendert")
PY
log "fertig"
