#!/usr/bin/env bash
# arch: Ergebnisse der drei Hosts nach ~/of-ur holen (Pool je Host, Zusatzdateien gemeinsam).
# Wiederholt alle 10 min, bis alle drei Hosts FERTIG melden oder es 16:55 ist. Nur Lesen auf den Hosts.
# Aufruf: setsid nohup bash ~/ur-dev/einsammeln.sh > ~/ur-dev/einsammeln.log 2>&1 < /dev/null &
set -u
Z=$HOME/of-ur
mkdir -p "$Z/pool" "$Z/zusatz"
R="rsync -a --timeout=60 --bwlimit=40000 --exclude=*.tmp --exclude=*.part"
E="ssh -o BatchMode=yes -o ConnectTimeout=15"
holen() {  # name ziel basis
  local n=$1 h=$2 b=$3
  mkdir -p "$Z/pool/$n"
  $R -e "$E" "$h:$b/out/" "$Z/pool/$n/" 2>&1 | tail -2
  $R -e "$E" --include='*.zusatz.zst' --exclude='*' "$h:$b/zus/" "$Z/zusatz/" 2>&1 | tail -2
  $E "$h" "tail -1 $b/lauf.log" 2>/dev/null
}
while :; do
  f=0
  for spec in "node-1 benutzer@node-1.example ur-w/lauf" "node-2 benutzer@node-2.example of-ur" "apollo benutzer@apollo.example of-ur"; do
    set -- $spec
    s=$(holen "$1" "$2" "$3")
    echo "$(date +%T) $1: $(ls "$Z/pool/$1" | grep -c '\.ok$') ok, letzte Zeile: $s"
    case "$s" in *FERTIG*) f=$((f + 1)) ;; esac
  done
  echo "$(date +%T) Zusatzdateien gesamt: $(ls "$Z/zusatz" | grep -c '\.zusatz\.zst$')"
  [ "$f" -eq 3 ] && { echo "$(date +%T) ALLE FERTIG"; break; }
  [ "$(date +%H%M)" -ge 1655 ] && { echo "$(date +%T) Zeitgrenze 16:55"; break; }
  sleep 600
done
