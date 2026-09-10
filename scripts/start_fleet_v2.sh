#!/usr/bin/env bash
# Materialisierungslauf v2 starten — laeuft AUF ARCH.
#
#   scripts/start_fleet_v2.sh [--nur-pruefen]
#
# Reihenfolge: einsammeln, pruefen, Code verteilen, starten, Einsammler anwerfen.
# Beanstandete Shards werden auf arch UND auf dem Knoten geloescht, damit die
# Wiederaufnahme sie neu macht. arch selbst rechnet nicht mit.
set -u
cd /home/netter/projects/openfront-ai || exit 1
OUT=data/pool_shards_v2
KNOTEN=(100.81.143.39 100.81.22.70 100.81.201.63)
NAME=(node1 node2 apollo)
CONTAINER=(3 2 6)
S="ssh -n -o ConnectTimeout=6 -o BatchMode=yes"
mkdir -p "$OUT" logs

echo "== 1. einsammeln =="
for H in "${KNOTEN[@]}"; do
  rsync -a --exclude='.log_*' -e "ssh -o ConnectTimeout=6 -o BatchMode=yes" \
    "netter@$H:of-work/shards_v2/" "$OUT/" 2>/dev/null
done
echo "   $(ls "$OUT" | grep -c 'meta.zst$') Shards, $(ls "$OUT" | grep -c '\.none$') leere Partien"

echo "== 2. pruefen (Metazeilen gegen Kartenblöcke) =="
if ! python3 env/check_shards.py "$OUT" > /tmp/check_v2.txt 2>&1; then
  grep '^KAPUTT' /tmp/check_v2.txt | awk '{print $2}' | tr -d ':' > /tmp/kaputt.txt
  echo "   $(wc -l < /tmp/kaputt.txt) beanstandet — werden ueberall geloescht und neu gemacht:"
  cat /tmp/kaputt.txt
  while read -r g; do
    rm -f "$OUT/$g.meta.zst" "$OUT/$g.maps"
    for H in "${KNOTEN[@]}"; do $S "netter@$H" "rm -f of-work/shards_v2/$g.meta.zst of-work/shards_v2/$g.maps"; done
  done < /tmp/kaputt.txt
else
  tail -1 /tmp/check_v2.txt
fi
[ "${1:-}" = "--nur-pruefen" ] && exit 0

echo "== 3. Code verteilen =="
for H in "${KNOTEN[@]}"; do
  rsync -a -e "ssh -o ConnectTimeout=6 -o BatchMode=yes" env/materialize.ts env/obs.ts "netter@$H:of-work/env/"
  rsync -a -e "ssh -o ConnectTimeout=6 -o BatchMode=yes" scraper/mat_launch.py "netter@$H:of-work/scraper/"
  rsync -a -e "ssh -o ConnectTimeout=6 -o BatchMode=yes" scripts/run_v2.sh "netter@$H:of-work/"
  $S "netter@$H" "chmod +x of-work/run_v2.sh"
done

echo "== 4. starten =="
for i in 0 1 2; do
  H=${KNOTEN[$i]}
  # laeuft dort schon etwas? (Sperre im Skript faengt es zusaetzlich ab)
  if [ "$($S "netter@$H" 'ps -eo cmd | grep -c "[m]at_launch.py"')" -gt 0 ]; then
    echo "   ${NAME[$i]}: laeuft bereits, uebersprungen"; continue
  fi
  # timeout: die Verbindung haengt, obwohl die Arbeit drueben laeuft — der
  # abgesetzte Prozess gibt den Kanal nicht frei. Am 10.09.2026 blieb das
  # Skript deshalb bei node-1 stehen und startete node-2/apollo nie.
  # Die Arbeit ueberlebt das Kappen, auf allen drei Knoten geprueft.
  timeout 25 $S "netter@$H" "cd ~/of-work && nohup setsid ./run_v2.sh ${NAME[$i]} ${CONTAINER[$i]} > /dev/null 2>&1 < /dev/null & sleep 3; echo ok" > /dev/null
  echo "   ${NAME[$i]}: ${CONTAINER[$i]} Container"
done

echo "== 5. Einsammler =="
if ! pgrep -f "[c]ollect_v2.sh" > /dev/null; then
  nohup setsid scripts/collect_v2.sh >> logs/collect_v2.log 2>&1 < /dev/null &
  echo "   laeuft, Log logs/collect_v2.log"
else
  echo "   laeuft schon"
fi
echo "Anhalten:  for H in ${KNOTEN[*]}; do ssh netter@\$H 'pkill -f \"run_v2[.]sh\"; pkill -f \"mat_launch[.]py\"; docker ps --filter ancestor=of-mat -q | xargs -r docker kill'; done"
