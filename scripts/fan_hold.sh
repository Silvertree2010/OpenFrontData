#!/bin/sh
# Haelt CPU- (pwm1) und Gehaeuse-Luefter (pwm2) auf MAX gegen den ASUS-EC, der alle
# paar Sekunden seine Kurve reasserted. Jede Sekunde 255 schreiben -> effektiv max.
# Um 19:00 zurueck auf Auto (enable=5) und Schluss.
LOG=~/projects/openfront-ai/logs/heat.log
END=$(date -d 19:00 +%s)
echo "fan_hold START $(date +%H:%M:%S) bis 19:00" >> "$LOG"
while [ "$(date +%s)" -lt "$END" ]; do
  for n in 1 2; do
    echo 1   | sudo -n tee /sys/class/hwmon/hwmon8/pwm${n}_enable >/dev/null 2>&1
    echo 255 | sudo -n tee /sys/class/hwmon/hwmon8/pwm${n}        >/dev/null 2>&1
  done
  sleep 1
done
for n in 1 2 4; do echo 5 | sudo -n tee /sys/class/hwmon/hwmon8/pwm${n}_enable >/dev/null 2>&1; done
echo "fan_hold ENDE $(date +%H:%M:%S) -> auto (enable1=$(cat /sys/class/hwmon/hwmon8/pwm1_enable))" >> "$LOG"
