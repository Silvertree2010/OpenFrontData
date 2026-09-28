#!/usr/bin/env bash
# arch: Records von node-1 auf node-2/apollo verteilen (tar-Strom über arch), ur_lauf.sh überall starten.
# Aufruf: setsid nohup bash ~/ur-dev/verteilen.sh > ~/ur-dev/verteilen.log 2>&1 < /dev/null &
set -u
N1=benutzer@node-1.example; N2=benutzer@node-2.example; AP=benutzer@apollo.example
S="ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=10"
log() { echo "$(date +%T) $*"; }
$S $N1 "cat ~/ur-w/ur_lauf.sh" | $S $N2 "cat > ~/of-ur/ur_lauf.sh" && log "ur_lauf.sh -> node-2"
$S $N1 "cat ~/ur-w/ur_lauf.sh" | $S $AP "cat > ~/of-ur/ur_lauf.sh" && log "ur_lauf.sh -> apollo"
for P in p1 p2; do
  $S $N1 "mkdir -p ~/ur-w/lauf/rec && bash ~/ur-w/paket.sh node-1 $P | tar xf - -C ~/ur-w/lauf/rec" && log "node-1 $P"
  $S $N1 "bash ~/ur-w/paket.sh node-2 $P" | $S $N2 "mkdir -p ~/of-ur/rec && tar xf - -C ~/of-ur/rec" && log "node-2 $P"
  $S $N1 "bash ~/ur-w/paket.sh apollo $P" | $S $AP "mkdir -p ~/of-ur/rec && tar xf - -C ~/of-ur/rec" && log "apollo $P"
done
log "Bestand node-1 $($S $N1 'find ~/ur-w/lauf/rec -name "*.json" | wc -l') node-2 $($S $N2 'find ~/of-ur/rec -name "*.json" | wc -l') apollo $($S $AP 'find ~/of-ur/rec -name "*.json" | wc -l')"
$S $N1 "cp ~/ur-w/ur_lauf.sh ~/ur-w/lauf/ && setsid nohup bash ~/ur-w/lauf/ur_lauf.sh 3 \$HOME/ur-w/lauf > ~/ur-w/lauf/lauf.out 2>&1 < /dev/null &" && log "node-1 gestartet"
$S $N2 "setsid nohup bash ~/of-ur/ur_lauf.sh 3 \$HOME/of-ur > ~/of-ur/lauf.out 2>&1 < /dev/null &" && log "node-2 gestartet"
$S $AP "setsid nohup bash ~/of-ur/ur_lauf.sh 3 \$HOME/of-ur > ~/of-ur/lauf.out 2>&1 < /dev/null &" && log "apollo gestartet"
log FERTIG
