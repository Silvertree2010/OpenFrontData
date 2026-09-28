#!/usr/bin/env bash
# arch, sparsam (CPU, nice 19, 1 Lader-Worker, 2 Threads): prüft den Spielerfilter auf echten Daten.
#  1. Spielerliste bauen
#  2. train.py --spieler: 3 Schritte auf der CPU, eigener Checkpoint unter ~/ur-dev/ckpt (bc3 nur gelesen)
#  3. bc3 --eval-nur auf seinen Val-Partien (Baseline: vorhergesagte vs. echte Aktionstypen)
set -u
cd ~/ur-dev/code || exit 1
mkdir -p ~/ur-dev/ckpt ~/ur-dev/logs
PY=~/mat-dev/torchenv/bin/python
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
nice -n 19 $PY ~/ur-dev/spielerliste.py ~/of-ur
GEM="--netz d1 --daten $HOME/of-ur/pool --zusatz $HOME/of-ur/zusatz --spieler $HOME/of-ur/spieler_ur.tsv \
 --reputation $HOME/projects/openfront-ai/data/reputation.json --geraet cpu --workers 1 --puffer 4096 --offen 4 \
 --kein-push --adv-beta 0"
nice -n 19 $PY trainer/train.py $GEM --init $HOME/mat-dev/netz-dev/checkpoints/bc3.pt --frisch \
  --ckpt $HOME/ur-dev/ckpt/rauch.pt --batch 16 --schritte 3 --epochen 1 --eval-alle 0 \
  --eval-allg 256 --eval-je-partie 16 --metrik-pfad $HOME/ur-dev/logs/rauch_metrics.jsonl
echo "=== bc3 auf seinen Val-Partien"
nice -n 19 $PY trainer/train.py $GEM --init $HOME/mat-dev/netz-dev/checkpoints/bc3.pt --frisch \
  --ckpt $HOME/ur-dev/ckpt/eval_bc3_ur.pt --batch 64 --schritte 1 --eval-nur \
  --eval-allg 4000 --eval-je-partie 64 --metrik-pfad $HOME/ur-dev/logs/eval_bc3_ur.jsonl
echo RAUCH_ENDE
