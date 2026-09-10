#!/bin/sh
# 18:30: Arch-Materialisierung STOPPEN (kuehlt), Luefter auf MAX, Rest auf Helfer.
# NICHT runterfahren. Als Skriptdatei -> Kommandozeile ohne "mat_launch" -> kein Selbst-Kill.
P=/home/netter/projects/openfront-ai
cd "$P" || exit 1
LOG="$P/logs/heat.log"
echo "=== 18:30 STOP $(date +%H:%M:%S) ===" >> "$LOG"

# 1) Materializer stoppen -> CPU idle -> kuehlt
for p in $(pgrep -f mat_launch.py); do kill "$p" 2>/dev/null; done
sleep 1
IDS=$(docker ps -q --filter ancestor=of-mat)
[ -n "$IDS" ] && docker kill $IDS >/dev/null 2>&1
sleep 2
echo "gestoppt: container=$(docker ps -q --filter ancestor=of-mat | wc -l)" >> "$LOG"

# 2) Luefter MAX (nct6799: pwm1=CPU, pwm2=Gehaeuse; 4 zur Sicherheit; 3/6/7 sind eh 255)
for n in 1 2 4; do
  echo 1   | sudo -n tee /sys/class/hwmon/hwmon8/pwm${n}_enable >/dev/null 2>&1
  echo 255 | sudo -n tee /sys/class/hwmon/hwmon8/pwm${n}        >/dev/null 2>&1
done
echo "luefter MAX: pwm1=$(cat /sys/class/hwmon/hwmon8/pwm1) pwm2=$(cat /sys/class/hwmon/hwmon8/pwm2)" >> "$LOG"

# 3) Arch-Rest auf Helfer verteilen (best effort; Fehler aendern nichts an 1+2)
{
  "$P/.venv/bin/python" /tmp/compute_split.py >> "$LOG" 2>&1
  rsync -a /tmp/to_apollo/ apollo:of-work/records/ 2>>"$LOG"
  rsync -a /tmp/to_node2/  netter@100.81.22.70:of-work/records/ 2>>"$LOG"
  scp -q /tmp/restart.sh apollo:/tmp/restart.sh 2>/dev/null
  scp -q /tmp/restart.sh netter@100.81.22.70:/tmp/restart.sh 2>/dev/null
  ssh -o BatchMode=yes apollo               "sh /tmp/restart.sh records shards 6 1 /home/netter/of-work mat.log" >>"$LOG" 2>&1
  ssh -o BatchMode=yes netter@100.81.22.70  "sh /tmp/restart.sh records shards 4 0 /home/netter/of-work mat.log" >>"$LOG" 2>&1
  rm -rf /tmp/to_apollo /tmp/to_node2
} 2>>"$LOG"
echo "18:30 fertig $(date +%H:%M:%S)" >> "$LOG"
