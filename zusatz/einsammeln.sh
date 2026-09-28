#!/usr/bin/env bash
# Holt fertige Zusatzdateien von allen Hosts nach arch in EINEN Ordner, alle TAKT Sekunden,
# bis SOLL Dateien da sind oder MAX_STUNDEN um sind. Läuft auf arch.
#
#   setsid nohup bash ~/zusatz-bau/zusatz/einsammeln.sh >> ~/zusatz/einsammeln.log 2>&1 < /dev/null &
#
# Nie halbe Dateien im Zielordner (der Trainer liest ihn je Epoche neu ein):
#   - arch selbst: Hardlink auf die fertige Datei (lauf.ts schreibt .tmp und benennt um).
#   - apollo, node-1, node-2: rsync, das schreibt in eine Temp-Datei und benennt erst am Ende
#     um; .tmp auf der Quelle wird ausgeschlossen.
#   - node-3l: von arch nicht erreichbar, aber von apollo. tar-Strom arch ← apollo ← node-3l
#     in einen Eingangsordner, jede Datei mit zstd -t prüfen, erst dann umbenennen. Der Strom
#     berührt kein Mac-Dateisystem (Gross/Klein-Falle, 13.09.).
set -u
ZIEL=${ZIEL:-$HOME/zusatz/alle}
SOLL=${SOLL:-9544}
TAKT=${TAKT:-300}
MAX_STUNDEN=${MAX_STUNDEN:-16}
APOLLO=benutzer@apollo.example
REMOTE="benutzer@apollo.example benutzer@node-1.example benutzer@node-2.example"
N3=lucas@node-3.example
EINGANG="$ZIEL.eingang"
mkdir -p "$ZIEL" "$EINGANG"
ende=$(( $(date +%s) + MAX_STUNDEN * 3600 ))
echo "$(date +%H:%M) Einsammler startet: Ziel $ZIEL, Soll $SOLL, Takt ${TAKT}s"

while :; do
  # arch selbst: lokal per rsync (Temp-Datei, dann umbenennen). Kein Hardlink: die Dateien
  # gehören root (der Container schreibt sie), und fs.protected_hardlinks verbietet dann ln.
  rsync -a --ignore-existing --include='*.zusatz.zst' --exclude='*' "$HOME/zusatz/out/" "$ZIEL/" \
    || echo "$(date +%H:%M) lokales rsync fehlgeschlagen"
  for h in $REMOTE; do
    rsync -a --ignore-existing --include='*.zusatz.zst' --exclude='*' "$h:zusatz/out/" "$ZIEL/" \
      || echo "$(date +%H:%M) rsync von $h fehlgeschlagen (nächster Takt versucht es wieder)"
  done
  neu=$(comm -23 <(ssh "$APOLLO" "ssh $N3 'ls ~/zusatz/out'" 2>/dev/null | grep '\.zusatz\.zst$' | sort) \
                 <(ls "$ZIEL" | sort))
  if [ -n "$neu" ]; then
    printf '%s\n' $neu | ssh "$APOLLO" "ssh $N3 'cd ~/zusatz/out && tar -cf - -T -'" | tar -C "$EINGANG" -xf - \
      || echo "$(date +%H:%M) tar-Strom von node-3l fehlgeschlagen"
    for f in "$EINGANG"/*.zusatz.zst; do
      [ -e "$f" ] || continue
      if zstd -tq "$f"; then mv "$f" "$ZIEL/"; else echo "$(date +%H:%M) kaputt verworfen: ${f##*/}"; rm -f "$f"; fi
    done
  fi
  n=$(ls "$ZIEL" | grep -c '\.zusatz\.zst$')
  echo "$(date +%H:%M) $n/$SOLL"
  if [ "$n" -ge "$SOLL" ]; then echo "$(date +%H:%M) alle da, Einsammler endet"; break; fi
  if [ "$(date +%s)" -ge "$ende" ]; then echo "$(date +%H:%M) Zeitgrenze, Einsammler endet"; break; fi
  sleep "$TAKT"
done
