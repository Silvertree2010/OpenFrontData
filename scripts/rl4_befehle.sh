#!/usr/bin/env bash
# rl4: drei Äste ab rl3_i99, je 20 Iterationen, nur die Endbelohnung R unterscheidet sich (16.09.).
#   rl4s  --ziel sieg     R = 0,5·Platz + 0,5·Sieg                    Φ (0,35; 0; 0,45)
#   rl4p  --ziel platz    R = Platz (Kontrolle)                       Φ (0,75; 0; 0,25) über --phi
#   rl4g  --ziel gebiet   R = 0,5·Land/Land des Grössten + 0,5·Sieg   Φ (0,35; 0; 0,45)
# Alle: Nations Medium, 400 Bots (bleibt so, bis 90 % Siege), λ 0,25 wie am Ende von rl3, Anker ur1.
# Jeder Ast endet mit 100 Paaren gegen rl3_i99 auf derselben Saat rl4_vergleich; Hauptkennzahl
# sind die Siege (McNemar, status.json → bewertung.p_sieg_mcnemar), daneben der Platz.
#
# Start (arch), wartet selbst, bis rl3 samt Schlussbewertung fertig ist, spielt dann den neuen
# Code aus ~/rl4-dev (Trainer) und ~/of-v34-rl4 (arena.ts) ein und prüft ihn:
#   setsid nohup bash ~/rl4-dev/scripts/rl4_befehle.sh > ~/d1-lauf/logs/rl4_kette.out 2>&1 < /dev/null &
# Abzweigpunkt: rl3_i99, fehlt der, der höchste vorhandene rl3_iNN.pt (steht im Log).
# Stoppen (ganze Kette, der laufende Ast bricht sauber ab):
#   kill -TERM -- -$(cat ~/d1-lauf/logs/rl4_kette.pid)
# Wieder starten mit demselben Befehl: fertige Äste werden übersprungen, der angefangene macht
# beim höchsten rl4x_iNN.pt weiter.
set -u
cd ~/d1-lauf
echo $$ > logs/rl4_kette.pid
trap 'echo "$(date +%H:%M) Kette gestoppt"; exit 143' TERM INT

C=~/mat-dev/netz-dev/checkpoints
PY=~/mat-dev/torchenv/bin/python
echo "$(date +%H:%M) wartet auf Ende von rl3"
while pgrep -f "[r]l_schleife.py" >/dev/null; do sleep 30; done
BASIS=$C/rl3_i99.pt
if [ ! -f "$BASIS" ]; then
  BASIS=$(ls $C/rl3_i*.pt | sort -V | tail -1)
fi
echo "$(date +%H:%M) Abzweigpunkt $BASIS"

if ! cmp -s ~/rl4-dev/trainer/rl_schleife.py trainer/rl_schleife.py; then
  echo "$(date +%H:%M) spielt den neuen Code ein"
  for f in belohnung.py trajektorie.py rl_schleife.py; do
    [ -f trainer/$f.vor-rl4 ] || cp trainer/$f trainer/$f.vor-rl4
    cp ~/rl4-dev/trainer/$f trainer/$f
  done
  cp ~/rl4-dev/trainer/tests/{belohnungtest,spurtest,schleifetest}.py trainer/tests/
  A=~/openfront-client-v34/arena/arena.ts
  [ -f $A.vor-rl4 ] || cp $A $A.vor-rl4
  cp ~/of-v34-rl4/arena/arena.ts $A
fi
for t in belohnungtest schleifetest; do
  if ! $PY trainer/tests/$t.py > logs/rl4_$t.log 2>&1; then
    echo "$(date +%H:%M) $t schlägt fehl (logs/rl4_$t.log), Abbruch"
    exit 1
  fi
done

phase() {  # Phase aus status.json eines Laufs, leer wenn es ihn noch nicht gibt
  $PY -c "import json,sys; print(json.load(open(sys.argv[1])).get('phase',''))" "logs/$1/status.json" 2>/dev/null
}

ast() {  # ast NAME ZIEL [weitere Argumente]
  local name=$1 ziel=$2; shift 2
  if [ "$(phase "$name")" = "fertig" ]; then
    echo "$(date +%H:%M) $name schon fertig, übersprungen"
    return 0
  fi
  echo "$(date +%H:%M) startet $name (Ziel $ziel)"
  $PY trainer/rl_schleife.py --name "$name" --ziel "$ziel" --start "$BASIS" --anker $C/ur1.pt --lam 0.25 \
    --kalibrierung ~/ur-dev/kal_ur1.json --client ~/openfront-client-v34 --stufe Medium --bots 400 --ki 1 \
    --partien 160 --jobs 12 --server 3 --iterationen 20 \
    --eval-paare 100 --eval-gegner "$BASIS" --eval-saat rl4_vergleich "$@" \
    >> "logs/$name.schleife.log" 2>&1 < /dev/null &
  wait $!
  local p
  p=$(phase "$name")
  echo "$(date +%H:%M) $name Ende, Phase $p"
  [ "$p" = "fertig" ]
}

ast rl4s sieg || exit 1
ast rl4p platz --phi 0.75,0,0.25 || exit 1
ast rl4g gebiet || exit 1
echo "$(date +%H:%M) alle drei Äste fertig"
