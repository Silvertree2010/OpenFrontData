#!/usr/bin/env bash
# Materialisierungslauf v2 auf EINEM Helferknoten. Endlich, raeumt selbst auf.
#   run_v2.sh <name> <n_container>
# Schleife, weil der Launcher hoechstens n Commit-Gruppen gleichzeitig bedient;
# er ist resume-fest, jeder Durchgang nimmt sich den Rest. Ende: Marke .DONE_<name>.
NAME="$1"; N="$2"
cd /home/netter/of-work || exit 1
mkdir -p shards_v2
# Sperre: zwei gleichzeitige Laeufe auf einem Knoten verdoppeln die Container und
# damit die Last (auf apollo einmal passiert: 10 statt 6 bei 12 Kernen).
exec 9> .run_v2.lock
if command -v flock > /dev/null && ! flock -n 9; then
  echo "[$NAME] laeuft bereits, Abbruch" >> mat_v2.log; exit 1
fi
echo "[$NAME] Start $(date +%F_%H:%M), $N Container, NOOP_EVERY=200" >> mat_v2.log
for i in 1 2 3 4 5; do
  NOOP_EVERY=200 python3 scraper/mat_launch.py records_v2 shards_v2 "$N" --cpus 1 >> mat_v2.log 2>&1
  if tail -20 mat_v2.log | grep -q "^\[launch\] 0 zu materialisieren"; then break; fi
done
echo "[$NAME] Ende $(date +%F_%H:%M)" >> mat_v2.log
rm -f shards_v2/.log_* 2>/dev/null
touch "shards_v2/.DONE_$NAME"
