#!/bin/sh
# Sammelt Helfer-Shards (apollo, node-2) alle 5 min zentral nach data/shards.
cd /home/netter/projects/openfront-ai || exit 1
while true; do
  rsync -a --exclude=".log_*" apollo:of-work/shards/ data/shards/ 2>/dev/null
  rsync -a --exclude=".log_*" netter@100.81.22.70:of-work/shards/ data/shards/ 2>/dev/null
  echo "[collect $(date +%H:%M)] shards=$(ls data/shards/*.meta.zst 2>/dev/null | wc -l)"
  sleep 300
done
