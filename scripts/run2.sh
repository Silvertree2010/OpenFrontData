#!/bin/bash
# Lauf 2 des BC-Netzes: abstandsbewusster Grob-Verlust + abgeschalteter Fein-Kopf.
#
# Unterschied zu Lauf 1 (checkpoints/bc_big.pt): nur die beiden Kachel-Schalter,
# sonst identische Hyperparameter (Batch 128, lr 3e-4, Shuffle-Puffer 8192,
# Seed 1, ADV_BETA/VALUE_W wie gehabt). Eine Epoche.
#   --coarse-sigma 3.0  weiches 2D-Gauss-Ziel (Breite 3 GROBZELLEN = 24 Kacheln)
#                       statt harter Kreuzentropie auf exakte Uebereinstimmung
#   --fine-off          Fein-Kopf raus aus Verlust und Vorhersage; Feinposition
#                       ist die Mitte der Grobzelle (gemessen 3.1 statt 4.8
#                       Kacheln Abstand). Der Kopf bleibt im Checkpoint.
#
# Schreibt NICHT nach checkpoints/bc_big.pt, logs/bc_big.log oder
# logs/metrics.jsonl - Lauf 1 bleibt vollstaendig erhalten.
#
# Bedienung:   bash scripts/run2.sh            (Vordergrund, Log mit tee)
#              SIGMA=2.0 bash scripts/run2.sh  (andere Breite)
#              FRESH=0 bash scripts/run2.sh    (abgebrochenen Lauf fortsetzen)
#              RUNNER=echo bash scripts/run2.sh   (Trockenlauf: nur Kommando zeigen)
set -u
P=/home/netter/projects/openfront-ai
cd "$P" || exit 1

NAME=${NAME:-bc_geo}
CKPT=${CKPT:-checkpoints/$NAME.pt}
LOG=${LOG:-logs/$NAME.log}
METRICS=${METRICS:-logs/metrics_$NAME.jsonl}
SIGMA=${SIGMA:-3.0}
MIX=${MIX:-1.0}
EPOCHS=${EPOCHS:-1}
BATCH=${BATCH:-128}
FRESH=${FRESH:-1}
MINFREE_MIB=${MINFREE_MIB:-10000}

# --- Schutz 1: Lauf 1 nicht ueberschreiben -----------------------------------
case "$CKPT" in
  *bc_big*|*bc_real*|*bc_awr*) echo "[abbruch] $CKPT gehoert zu einem alten Lauf"; exit 1;;
esac
case "$LOG$METRICS" in
  *bc_big.log*|*logs/metrics.jsonl*) echo "[abbruch] Log-Ziel gehoert zu Lauf 1"; exit 1;;
esac

# --- Schutz 2: keine zwei Trainings auf einer GPU ----------------------------
if pgrep -f 'bc_[f]it.py --shards' >/dev/null; then
  echo "[abbruch] es laeuft bereits ein bc_fit:"; pgrep -af 'bc_[f]it.py --shards'; exit 1
fi

# --- Schutz 3: genug freier VRAM ---------------------------------------------
FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
if [ -z "$FREE" ] || [ "$FREE" -lt "$MINFREE_MIB" ]; then
  echo "[abbruch] nur ${FREE:-?} MiB VRAM frei, ${MINFREE_MIB} MiB noetig"; exit 1
fi

FRESH_FLAG=""
[ "$FRESH" = "1" ] && FRESH_FLAG="--fresh"

echo "[lauf2] $(date '+%F %T')  Checkpoint $CKPT  Sigma $SIGMA  Mix $MIX  Fein-Kopf AUS  VRAM frei ${FREE} MiB"
mkdir -p logs checkpoints

${RUNNER:-.venv/bin/python} env/bc_fit.py \
  --shards data/pool_shards \
  --records-dir data/pool_records \
  --device cuda \
  --epochs "$EPOCHS" \
  --batch "$BATCH" \
  --shuffle-buf 8192 \
  --ckpt "$CKPT" \
  $FRESH_FLAG \
  --log-every 100 \
  --ckpt-every 1000 \
  --snapshot-every 20000 \
  --metrics-path "$METRICS" \
  --coarse-sigma "$SIGMA" \
  --coarse-soft-mix "$MIX" \
  --fine-off 2>&1 | tee -a "$LOG"
