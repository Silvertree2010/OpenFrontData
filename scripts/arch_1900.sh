#!/bin/sh
# 19:00: Luefter zurueck auf AUTO (BIOS-Smart-Fan, enable=5) -> rampen wieder mit
# der Temperatur beim Zocken statt fest auf max. Falls der Chip 5 nicht mag, bleiben
# sie auf max (laut, aber sicher) -> ein Reboot setzt die BIOS-Defaults zurueck.
P=/home/netter/projects/openfront-ai
LOG="$P/logs/heat.log"
echo "=== 19:00 LUEFTER AUTO $(date +%H:%M:%S) ===" >> "$LOG"
for n in 1 2 4; do
  echo 5 | sudo -n tee /sys/class/hwmon/hwmon8/pwm${n}_enable >/dev/null 2>&1
done
echo "enable: pwm1=$(cat /sys/class/hwmon/hwmon8/pwm1_enable) pwm2=$(cat /sys/class/hwmon/hwmon8/pwm2_enable)" >> "$LOG"
