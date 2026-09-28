#!/usr/bin/env bash
# Spielweise (14.09.): fertige Befehle. Nichts davon läuft von selbst, jeder Schritt einzeln.
#
#   Mac:  bash scripts/spielweise_befehle.sh sync            Code nach arch kopieren (rechnet nichts)
#   arch: bash ~/d1-lauf/scripts/spielweise_befehle.sh bitgleich        2 × 4 Partien bc3, 3000 Ticks,
#         gleiche Saat, anderes --jobs → müssen bitgleich sein (~5–10 min, Vordergrund)
#   arch: bash ~/d1-lauf/scripts/spielweise_befehle.sh probelauf        60 Paare rl1 gegen bc3,
#         Medium/400 Bots, 12000 Ticks, gekoppelt, mit Spielweise (Hintergrund, grob 20–40 min)
#   arch: bash ~/d1-lauf/scripts/spielweise_befehle.sh menschen_rauch   2 Partien je Gruppe (Minuten)
#   arch: bash ~/d1-lauf/scripts/spielweise_befehle.sh menschen         120 Val-Partien + Ultimus_Rex
#         (Hintergrund, 6 Prozesse; Dauer unbekannt, own.zst wird in Python nachgespielt)
#
# Ergebnisse: ~/d1-lauf/logs/sw_probe.{log,a.jsonl,b.jsonl,spielweise.json},
# ~/d1-lauf/logs/menschen_spielweise.json. Beides schiebt lobster_push (Timer) aufs LobsterBoard.
# Braucht PY mit numpy und zstd (Python ≥ 3.14 oder Paket zstandard); sonst PY=… setzen.
set -euo pipefail
PY=${PY:-$HOME/mat-dev/torchenv/bin/python}
CL=${ARENA_CLIENT:-$HOME/openfront-client-arena}
CK=$HOME/mat-dev/netz-dev/checkpoints
KAL=$HOME/d1-lauf/kal_bc3.json
REP=$HOME/projects/openfront-ai/data/reputation.json
LOG=$HOME/d1-lauf/logs
MENSCHEN=(--pool "$HOME/of-mat2-out" --zusatz "$HOME/zusatz/alle" --karten "$CL/resources/maps"
          --ur-pool "$HOME/of-ur/pool" --ur-zusatz "$HOME/of-ur/zusatz" --ur-liste "$HOME/of-ur/spieler_ur.tsv"
          --records "$HOME/projects/openfront-ai/data")

case "${1:-}" in
  sync)   # auf dem Mac
    A=$HOME/projects/private/openfront-ai-arena/viewer/arena
    T=$HOME/projects/private/openfront-ai-trainer
    rsync -a "$A"/{arena.ts,spielweise.ts,spur.ts,auswertung.py,aa_analyse.py,menschen.py,verteilung.py,tests} arch:openfront-client-arena/arena/
    rsync -a "$T"/trainer/{spielen.py,inf_d0.py,arena.py,rl_schleife.py} arch:d1-lauf/trainer/
    rsync -a "$T"/trainer/tests/wahltest.py arch:d1-lauf/trainer/tests/
    rsync -a "$T"/scripts/spielweise_befehle.sh arch:d1-lauf/scripts/
    rsync -a "$T"/board/lobster_push.py arch:mat-dev/lobster_push.py
    echo "kopiert. Unit-Tests auf arch (leicht): $PY ~/d1-lauf/trainer/tests/wahltest.py"
    ;;
  bitgleich)
    cd "$HOME/d1-lauf"
    for k in 1 2; do
      env ARENA_SCHWIERIGKEIT=Medium D0_ZIEHEN=1 D0_TOP_K=4 "$PY" trainer/arena.py --ckpt "$CK/bc3.pt" \
        --kalibrierung "$KAL" --reputation "$REP" --client "$CL" --python "$PY" --partien 4 --ticks 3000 \
        --bots 400 --karte World --seiten netz --jobs $((6 - 2 * k)) --saat bitgleich --wahl-saat 1 \
        --port 8695 --aus "$LOG/bitgleich_$k.jsonl" > "$LOG/bitgleich_$k.out" 2>&1
    done
    python3 "$CL/arena/tests/bitgleich.py" "$LOG/bitgleich_1.jsonl" "$LOG/bitgleich_2.jsonl"
    ;;
  probelauf)
    cd "$HOME/d1-lauf"
    setsid nohup env ARENA_SCHWIERIGKEIT=Medium D0_ZIEHEN=1 D0_TOP_K=4 "$PY" trainer/arena.py \
      --ckpt "$CK/rl1_i76.pt" --kalibrierung "$KAL" --b-ckpt "$CK/bc3.pt" --b-kalibrierung "$KAL" \
      --reputation "$REP" --client "$CL" --python "$PY" --partien 60 --ticks 12000 --bots 400 --karte World \
      --seiten netz --ki 1 --jobs 14 --server 3 --threads 2 --port 8691 --saat sw_probe --wahl-saat 1 \
      --spielweise --zeitlimit 7200 --aus "$LOG/sw_probe.jsonl" > "$LOG/sw_probe.log" 2>&1 < /dev/null &
    echo "läuft (PID $!): tail -f $LOG/sw_probe.log; Tabelle danach am Ende von sw_probe.log"
    ;;
  menschen_rauch)
    cd "$CL"
    "$PY" arena/menschen.py "${MENSCHEN[@]}" --max-partien 2 --aus /tmp/menschen_rauch.json
    ;;
  menschen)
    cd "$CL"
    setsid nohup "$PY" arena/menschen.py "${MENSCHEN[@]}" --stichprobe 120 --jobs 6 \
      --aus "$LOG/menschen_spielweise.json" > "$LOG/menschen.log" 2>&1 < /dev/null &
    echo "läuft (PID $!): tail -f $LOG/menschen.log"
    ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
