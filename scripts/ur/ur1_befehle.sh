#!/usr/bin/env bash
# ur1: bc3 auf Ultimus_Rex feintunen, dann gegen bc3 bewerten. Erst starten, wenn arch frei ist
# (rl1 beendet, kein anderer GPU-Lauf). bc3/bc2 werden nur gelesen, nie geschrieben.
# Aufruf: setsid nohup bash ~/ur-dev/ur1_befehle.sh > ~/ur-dev/logs/ur1_gesamt.log 2>&1 < /dev/null &
# Einzelne Stufen: STUFEN="train eval kal arena" (Standard alle).
set -u
PY=$HOME/mat-dev/torchenv/bin/python
REP=$HOME/projects/openfront-ai/data/reputation.json
BC3=$HOME/mat-dev/netz-dev/checkpoints/bc3.pt
UR1_LAUF=$HOME/ur-dev/ckpt/ur1.pt                     # Trainingsstand + Snapshots ur1_s<N>.pt
UR1=$HOME/mat-dev/netz-dev/checkpoints/ur1.pt          # gewählter Stand (bester Val auf seinen Partien)
STUFEN=${STUFEN:-"train eval kal arena stil"}
mkdir -p ~/ur-dev/ckpt ~/ur-dev/logs
# Nie neben einem RL-Lauf oder einer anderen Arena starten (Ports, GPU, CPU)
if pgrep -f "[r]l_schleife.py|[a]rena.ts|[t]rainer/train.py" >/dev/null; then
  echo "$(date +%T) Abbruch: RL-/Arena-/Trainingslauf aktiv"; pgrep -af "[r]l_schleife.py|[a]rena.ts|[t]rainer/train.py"; exit 1
fi
cd ~/ur-dev/code || exit 1
# Spielerliste aus allem neu bauen, was bis jetzt eingesammelt ist (nur bestandene Partien mit Zusatzdatei)
[ -n "${OHNE_LISTE:-}" ] || $PY ~/ur-dev/spielerliste.py ~/of-ur || exit 1
# Planlänge: ~8 Epochen über seine Train-Samples bei Batch 256 (Zahl aus spielerliste.py)
N=$($PY -c "import json;print(json.load(open('$HOME/of-ur/spieler_ur.zahlen.json'))['samples']['train'])")
SCHRITTE=$(( N * 8 / 256 ))
echo "$(date +%T) seine Train-Samples $N -> $SCHRITTE Schritte"

case " $STUFEN " in *" train "*)
  # Feintuning: kleine LR (bc3 endete bei 1e-5, Spitze hier 3e-5), kurzer Warmup, Cosinus,
  # sonst wie bc3. Eval auf SEINEN Val-Partien alle 100 Schritte, Snapshots alle 200 (Überanpassung).
  $PY trainer/train.py --netz d1 --daten $HOME/of-ur/pool --zusatz $HOME/of-ur/zusatz \
    --spieler $HOME/of-ur/spieler_ur.tsv --reputation $REP --init $BC3 --frisch --ckpt $UR1_LAUF \
    --batch 256 --lr 3e-5 --lr-min 3e-6 --lr-plan cosinus --warmup 50 --clip 25 --adv-beta 0 \
    --schritte $SCHRITTE --epochen 100 --eval-start --eval-alle 100 --ckpt-alle 100 --snapshot-alle 200 \
    --eval-allg 8000 --eval-je-partie 400 --workers 4 --kein-push --metrik-pfad $HOME/ur-dev/logs/ur1_metrics.jsonl \
    > ~/ur-dev/logs/ur1_train.log 2>&1 || { echo "Training gescheitert"; exit 1; }
  echo "$(date +%T) Training fertig"
  # Überanpassung: Stand mit dem kleinsten Val-Verlust auf SEINEN Val-Partien nehmen
  # (Snapshots alle 200 Schritte oder der Endstand), nach checkpoints/ur1.pt kopieren
  WAHL=$($PY - "$HOME/ur-dev/logs/ur1_metrics.jsonl" "$UR1_LAUF" <<'EOF'
import json, os, sys
m, lauf = sys.argv[1], sys.argv[2]
val, ende = {}, None
for z in open(m):
    d = json.loads(z)
    if d.get("kind") == "val":
        val[d["step"]] = d["vloss"]
    elif d.get("kind") == "end":
        ende = d["step"]
def datei(s):
    sn = lauf[:-3] + f"_s{s}.pt"
    return sn if os.path.exists(sn) else (lauf if s == ende else None)
kand = {s: v for s, v in val.items() if s > 0 and datei(s)}
b = min(kand, key=kand.get)
print(json.dumps({"val_verlust": val, "ende": ende, "wahl": b}), file=sys.stderr)
print(datei(b))
EOF
) || { echo "Wahl gescheitert"; exit 1; }
  cp "$WAHL" "$UR1.tmp" && mv "$UR1.tmp" "$UR1"
  echo "$(date +%T) gewählt: $WAHL -> $UR1"
;; esac

case " $STUFEN " in *" eval "*)
  # Allgemeine Val-Partien von bc3 (393): verliert ur1 dort gegenüber bc3? (Vergessen/Überanpassung)
  for M in bc3:$BC3 ur1:$UR1; do
    $PY trainer/train.py --netz d1 --daten $HOME/of-mat2-out --zusatz $HOME/zusatz/alle --reputation $REP \
      --val-aus $BC3 --init ${M#*:} --frisch --ckpt $HOME/ur-dev/ckpt/eval_allg_${M%%:*}.pt --batch 256 --eval-nur \
      --workers 4 --kein-push --metrik-pfad $HOME/ur-dev/logs/eval_allg_${M%%:*}.jsonl > ~/ur-dev/logs/eval_allg_${M%%:*}.log 2>&1
    # Seine Val-Partien (Stil): vorhergesagte gegen echte Aktionstypen
    $PY trainer/train.py --netz d1 --daten $HOME/of-ur/pool --zusatz $HOME/of-ur/zusatz --spieler $HOME/of-ur/spieler_ur.tsv \
      --reputation $REP --init ${M#*:} --frisch --ckpt $HOME/ur-dev/ckpt/eval_ur_${M%%:*}.pt --batch 256 --schritte 1 --eval-nur \
      --eval-allg 8000 --eval-je-partie 400 --workers 4 --kein-push --metrik-pfad $HOME/ur-dev/logs/eval_ur_${M%%:*}.jsonl \
      > ~/ur-dev/logs/eval_ur_${M%%:*}.log 2>&1
  done
  echo "$(date +%T) Evals fertig"
;; esac

case " $STUFEN " in *" kal "*)
  # Schwelle für ur1 wie für bc3 (kal_bc3.json): 20 allgemeine Val-Partien, Ziel-Median 44 Ticks
  $PY trainer/kalibriere_schwelle.py --ckpt $UR1 --daten $HOME/of-mat2-out --zusatz $HOME/zusatz/alle \
    --reputation $REP --ziel 44 --aus $HOME/ur-dev/kal_ur1.json > ~/ur-dev/logs/kal_ur1.log 2>&1
  echo "$(date +%T) Kalibrierung fertig: $($PY -c "import json;print(json.load(open('$HOME/ur-dev/kal_ur1.json'))['schwelle']['32'])")"
;; esac

case " $STUFEN " in *" arena "*)
  # Kernbewertung: ur1 gegen bc3 gepaart, 200 Paare, Nations Medium, World, 400 Bots (wie echte
  # öffentliche FFA), 12 000 Ticks, Sampling Top-4 an die Saat gekoppelt, breite Spielweise-Kennzahlen
  # mit Holm (User 14.09.: Platz allein sagt wenig). Jeweils eigene Schwelle. Laufzeitweg ~/d1-lauf.
  # Schwellen ausdrücklich bei k=32 (Takt 32), wie rl_schleife.py sie setzt
  S_UR1=$($PY -c "import json;print(json.load(open('$HOME/ur-dev/kal_ur1.json'))['schwelle']['32'])")
  S_BC3=$($PY -c "import json;print(json.load(open('$HOME/d1-lauf/kal_bc3.json'))['schwelle']['32'])")
  echo "$(date +%T) Arena: Schwelle ur1 $S_UR1, bc3 $S_BC3"
  cd ~/d1-lauf || exit 1
  ARENA_SCHWIERIGKEIT=Medium D0_ZIEHEN=1 D0_TEMP=1.0 D0_TOP_K=4 $PY trainer/arena.py \
    --ckpt $UR1 --schwelle $S_UR1 \
    --b-ckpt $BC3 --b-schwelle $S_BC3 \
    --reputation $REP --client $HOME/openfront-client-arena --partien 200 --ticks 12000 --bots 400 \
    --karte World --seiten netz --jobs 14 --takt 32 --saat ur1_bc3 --wahl-saat 1 --spielweise --ki 1 \
    --port 8691 --threads 2 --server 3 \
    --aus $HOME/ur-dev/logs/ur1_bc3.jsonl --python $PY --zeitlimit 5400 > ~/ur-dev/logs/ur1_bc3.log 2>&1
  echo "$(date +%T) Arena fertig, Auswertung in ~/ur-dev/logs/ur1_bc3.log"
;; esac

case " $STUFEN " in *" stil "*)
  # Stilvergleich: seine echten Aktionen gegen ur1 und bc3, offline (gleiche Zustände, seine
  # Val-Partien) und im Spiel (Arena-Seiten a = ur1, b = bc3)
  cd ~/ur-dev/code || exit 1
  $PY ~/ur-dev/ur_stil.py --basis $HOME/of-ur --reputation $REP --ckpt ur1=$UR1 --ckpt bc3=$BC3 \
    --arena ur1=$HOME/ur-dev/logs/ur1_bc3.a.jsonl --arena bc3=$HOME/ur-dev/logs/ur1_bc3.b.jsonl \
    --aus $HOME/ur-dev/logs/stil.json > ~/ur-dev/logs/stil.log 2>&1
  echo "$(date +%T) Stilvergleich fertig, Bericht in ~/ur-dev/logs/stil.log"
;; esac
