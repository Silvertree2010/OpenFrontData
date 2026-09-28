#!/bin/sh
# Selbstaendige Zeitsteuerung auf Arch (unabhaengig von der VPN-Verbindung des Macs):
# wartet bis 18:30 -> arch_1830.sh, dann bis 19:00 -> arch_1900.sh.
P=~/projects/openfront-ai
LOG="$P/logs/heat.log"
echo "=== schedule scharf, jetzt $(date +%H:%M:%S), warte bis 18:30 ===" >> "$LOG"
s1=$(( $(date -d 18:30 +%s) - $(date +%s) )); [ "$s1" -lt 0 ] && s1=0
sleep "$s1"
sh "$P/scripts/arch_1830.sh"
s2=$(( $(date -d 19:00 +%s) - $(date +%s) )); [ "$s2" -lt 0 ] && s2=0
sleep "$s2"
sh "$P/scripts/arch_1900.sh"
echo "=== schedule fertig $(date +%H:%M:%S) ===" >> "$LOG"
