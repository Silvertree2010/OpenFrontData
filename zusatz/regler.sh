#!/usr/bin/env bash
# Regelt den of-zusatz-Container auf apollo nach der CPU-Temperatur (i7-8700K,
# Warnschwelle 82 °C, kritisch 100 °C). Vorbild: ~/of-mat2-regler.sh vom
# Materialisierer-Lauf; der greift nur bei of-mat2-*-Containern.
#
# Anlass (13.09.): mit festen 5 Kernen lag die Paket-Temperatur 2 Minuten lang
# bei 83-87 °C, mehrfach auf oder über der Drosselschwelle 86 °C. Einzelwerte
# streuten dabei von 68 bis 87 °C binnen 10 s — deshalb zählt hier das Mittel
# aus 6 Messungen je Minute, nicht ein einzelner Wert.
#
# Je Minute: Mittel >= 86 °C -> 0,25 Kerne weniger (mindestens 2,0),
#            Mittel <= 80 °C -> 0,25 Kerne mehr (höchstens 5,0, Vorgabe für apollo).
# Die Jobzahl im Container bleibt; weniger Kerne heisst nur langsamer, nichts geht verloren.
# Endet von selbst, sobald der Container nicht mehr läuft.
#
#   setsid nohup bash ~/zusatz/regler.sh 400 >> ~/zusatz/regler.log 2>&1 < /dev/null &
set -u
t=$(grep -l coretemp /sys/class/hwmon/hwmon*/name | head -1 | xargs dirname)
c=${1:-400}                  # Hundertstelkerne zum Start
max=500; min=200; schritt=25
kerne() { printf "%d.%02d" $(($1 / 100)) $(($1 % 100)); }

echo "$(date +%H:%M) Regler startet bei $(kerne $c) Kernen"
while [ "$(docker inspect -f '{{.State.Running}}' of-zusatz 2>/dev/null)" = "true" ]; do
  summe=0
  for i in 1 2 3 4 5 6; do
    summe=$((summe + $(cat "$t/temp1_input") / 1000))
    sleep 10
  done
  temp=$((summe / 6))
  n=$c
  if [ "$temp" -ge 86 ] && [ "$c" -gt "$min" ]; then n=$((c - schritt)); fi
  if [ "$temp" -le 80 ] && [ "$c" -lt "$max" ]; then n=$((c + schritt)); fi
  if [ "$n" -ne "$c" ]; then
    docker update --cpus "$(kerne $n)" of-zusatz >/dev/null && c=$n
    echo "$(date +%H:%M) Mittel ${temp} °C -> $(kerne $c) Kerne"
  fi
done
echo "$(date +%H:%M) of-zusatz läuft nicht mehr, Regler endet"
