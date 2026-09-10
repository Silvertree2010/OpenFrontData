#!/usr/bin/env bash
# Startet das BC-Training auf arch, fortsetzend (kein --fresh).
# Die Wache steht bewusst in einer eigenen Datei: steht der Startbefehl im
# selben Kommando wie die Pruefung, findet `pgrep -f` den eigenen Shell-
# Prozess und meldet faelschlich "laeuft schon".
set -u
cd "$HOME/projects/openfront-ai" || exit 9

RUNNING=$(pgrep -f 'bc_fit\.py' -a | grep -c 'python env/bc_fit\.py' || true)
if [ "${RUNNING:-0}" -gt 0 ]; then
  echo "Trainer laeuft bereits:"; pgrep -f 'bc_fit\.py' -a | grep 'python env/bc_fit\.py'
  exit 8
fi

echo -n "Ausgangs-Checkpoint: "
.venv/bin/python -c 'import torch;d=torch.load("checkpoints/bc_big.pt",map_location="cpu",weights_only=False);print("Epoche",d.get("epoch"),"Schritt",d.get("gstep") or d.get("step"))'

# Kein systemd-inhibit: ueber SSH ohne Sitzung verweigert polkit den Griff.
# Gestern lief das Training 7 h ohne Suspend; nur die leerlaufende Maschine schlief ein.
setsid env ADV_BETA=1.5 VALUE_W=1.0 nohup \
  .venv/bin/python env/bc_fit.py \
    --shards data/pool_shards --device cuda --epochs 3 --batch 128 --shuffle-buf 8192 \
    --ckpt checkpoints/bc_big.pt --records-dir data/pool_records \
    --log-every 100 --ckpt-every 1000 --snapshot-every 20000 \
  >> logs/bc_big.log 2>&1 < /dev/null &
echo "gestartet"
