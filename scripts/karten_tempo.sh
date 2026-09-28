#!/usr/bin/env bash
# Wie teuer sind normale Karten gegenüber kompakten, bei echter Lobbygrösse?
#
# Hintergrund: Der öffentliche Spielplan (src/server/MapPlaylist.ts) macht etwa jede dritte
# Partie kompakt (100 Bots, 25 % der Spieler); die übrigen laufen auf normalen Karten mit
# 400 Bots und bis zu 125 Spielern. rl9 trainiert dagegen nur auf Compact mit 400 Bots.
# Diese Messung sagt, was eine Umstellung kostet: Sekunden je Partie, Episoden je Minute
# und Entscheidungen je Minute (das ist die Währung des Trainings).
#
# Aufruf auf arch, nichts anderes darf laufen (die Zahlen sind sonst wertlos):
#   bash ~/d1-lauf/scripts/karten_tempo.sh [PARTIEN] [TICKS]
set -u
PARTIEN=${1:-4}; TICKS=${2:-1500}
C=~/mat-dev/netz-dev/checkpoints
CK=$C/rl9_i02.pt; [ -f "$CK" ] || CK=$C/rl8_i50.pt
PY=~/mat-dev/torchenv/bin/python
KLIENT=~/openfront-client-v34
LOGS=~/d1-lauf/logs

for p in 8821 8822 8823 8824; do
  (setsid $PY ~/d1-lauf/trainer/inf_gpu.py --ckpt "$CK" --port $p --host 127.0.0.1 --device cuda \
     --schwelle 0.017338573932192958 --reputation ~/projects/openfront-ai/data/reputation.json \
     --ziehen --top-k 4 > "$LOGS/tempo_$p.log" 2>&1 &)
done
sleep 20
INF=http://127.0.0.1:8821/act,http://127.0.0.1:8822/act,http://127.0.0.1:8823/act,http://127.0.0.1:8824/act

for groesse in Compact Normal; do
  aus=/tmp/tempo_$groesse.jsonl; rm -f "$aus"
  t0=$(date +%s)
  (cd $KLIENT && ARENA_SCHWIERIGKEIT=Medium timeout 3600 npx tsx arena/arena.ts --inf "$INF" \
      --partien "$PARTIEN" --ki auto --bots auto --karte zufall --groesse "$groesse" --seiten netz \
      --jobs "$PARTIEN" --takt 32 --ticks "$TICKS" --saat "tempo_$groesse" --aus "$aus" \
      > /tmp/tempo_$groesse.out 2>/tmp/tempo_$groesse.err)
  t1=$(date +%s)
  $PY - "$aus" "$((t1 - t0))" "$groesse" <<'PY'
import json, sys, collections
zeilen = [json.loads(l) for l in open(sys.argv[1])]
sek, groesse = int(sys.argv[2]), sys.argv[3]
je = collections.defaultdict(list)
for z in zeilen:
    je[z["spiel_id"]].append(z)
partien = len(je)
entsch = sum(z["anfragen"] for z in zeilen)
ticks = sum(max(x["ende_tick"] for x in zs) for zs in je.values())
print(f"{groesse:8s} {partien} Partien, {len(zeilen)} Episoden, {entsch} Entscheidungen, {sek} s "
      f"=> {60*len(zeilen)/max(1,sek):6.1f} Episoden/min, {60*entsch/max(1,sek):7.0f} Entscheidungen/min, "
      f"{ticks/max(1,sek):5.0f} Ticks/s gesamt, KIs je Partie "
      f"{sorted(len(v) for v in je.values())}")
PY
done
for p in $(pgrep -f "inf_gpu.py --ckpt"); do kill "$p" 2>/dev/null; done
