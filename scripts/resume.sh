#!/bin/sh
# Nach einem Reboot alles wieder anwerfen: Arch-Materializer (resume-fest, ueberspringt
# fertige Shards) + Einsammel-Loop. Helfer (apollo/node-2) laufen eigenstaendig weiter;
# dieses Skript startet sie NICHT neu (nur falls sie auch aus waren -> siehe unten).
# Aufruf auf Arch:  bash scripts/resume.sh
cd /home/netter/projects/openfront-ai || exit 1
mkdir -p logs

# Arch-Launcher (alten sauber weg, dann neu)
for p in $(pgrep -f mat_launch.py); do kill "$p" 2>/dev/null; done
sleep 1
IDS=$(docker ps -q --filter ancestor=of-mat)
[ -n "$IDS" ] && docker kill $IDS >/dev/null 2>&1
sleep 2
setsid nohup python3 scraper/mat_launch.py data/night_raw data/shards 8 --cpus 0 >> logs/mat1.log 2>&1 </dev/null &

# Einsammler (nur wenn nicht schon laeuft)
pgrep -f collect_persist.sh >/dev/null || \
  setsid nohup sh scripts/collect_persist.sh >> logs/collect.log 2>&1 </dev/null &

sleep 6
echo "resumed: launcher=$(pgrep -f mat_launch.py | wc -l) collect=$(pgrep -f collect_persist.sh | wc -l) container=$(docker ps -q --filter ancestor=of-mat | wc -l)"
echo "Tipp: falls apollo/node-2 auch aus waren -> auf dem jeweiligen Node 'cd of-work && setsid nohup python3 scraper/mat_launch.py records shards <N> --cpus <X>' (apollo 6/1, node-2 4/0)"
