#!/usr/bin/env bash
# Aufseher: startet den Metadaten-Lauf neu, falls er stirbt, und hoert auf,
# wenn nichts mehr offen ist. Der Sammler ueberspringt Erledigtes selbst,
# ein Neustart kostet also nichts.
cd "$(dirname "$0")/.."
open_count() {
  python3 -c "import sqlite3;print(sqlite3.connect('data/index.sqlite').execute(\"SELECT COUNT(*) FROM games WHERE type='Public' AND meta_at IS NULL\").fetchone()[0])" 2>/dev/null || echo -1
}
for i in $(seq 1 200); do
  python3 -u scraper/collect.py --rps 1.0 meta --limit 200000 --where "type='Public'" >> logs/meta.log 2>&1
  rc=$?
  n=$(open_count)
  echo "[aufseher] Lauf $i endete rc=$rc, noch offen: $n  $(date -Is)" >> logs/meta.log
  if [ "$n" = "0" ]; then echo "[meta] ALLES FERTIG $(date -Is)" >> logs/meta.log; exit 0; fi
  sleep 5
done
echo "[meta] AUFGEGEBEN nach 200 Laeufen" >> logs/meta.log
