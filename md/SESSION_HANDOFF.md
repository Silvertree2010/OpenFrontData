# Session-Handoff — OpenFront-KI (Stand 2026-09-09, 17:45)

Ersetzt den Handoff vom Morgen. Dauerhafte Fakten stehen in den Auto-Memories.

## Zustand jetzt
- **Training gestoppt**, sauber nach dem Ende der zweiten Epoche. Auf Wunsch des Users
  pausiert bis morgen. Auf arch läuft **nichts** mehr, die Flotte steht komplett still,
  nichts startet von selbst (keine morning-train-Unit mehr vorhanden).
- **Endstand Lauf `20260909-152706`:** Schritt 149194, zwei von drei Epochen fertig.
  `checkpoints/bc_big.pt` und `checkpoints/bc_big_e1.pt` stehen beide auf Epoche 2, die
  Wiederaufnahme startet also direkt die dritte Epoche. Zwischenstände alle 20000 Schritte.
- **Validierung je Epoche:** Epoche 1 Verlust 21,297 / Kern 42,6 %. Epoche 2 Verlust
  20,718 / Kern 44,0 %. Also ein kleiner, echter Fortschritt für 137 Minuten Rechenzeit.
- Board läuft: https://apollo.tail5f3917.ts.net:8443/
- AI-Viewer läuft: Inferenz-Server auf arch:8650 (lädt neue Checkpoints selbst nach),
  Client auf dem Mac unter `~/openfront-client`, Startreihenfolge in den Memories.

## Morgen: dritte Epoche zu Ende bringen (rund 2 h 17 min)
```bash
ssh netter@100.120.102.64
cd ~/projects/openfront-ai
setsid env ADV_BETA=1.5 VALUE_W=1.0 .venv/bin/python env/bc_fit.py \
  --shards data/pool_shards --device cuda --epochs 3 --batch 128 --shuffle-buf 8192 \
  --ckpt checkpoints/bc_big.pt --records-dir data/pool_records \
  --log-every 100 --ckpt-every 1000 --snapshot-every 20000 >> logs/bc_big.log 2>&1 < /dev/null &
```
**Kein `--fresh`.** Zum Stoppen nach einer Epoche liegt `scripts/stop_after_epoch.sh`
bereit; darin den Snapshot-Namen auf die passende Epoche anpassen.

## Der eigentliche Engpass (heute gemessen, nicht vermutet)
Das Netz weiss **was** und **wen**, aber nicht **wo**. Gemessen mit `env/eval_spatial.py`
auf dem Validierungsanteil bei Schritt 47000: Grob-Kachel 2,69 % exakt, aber **83,6 % der
Vorhersagen liegen weiter als 32 Kacheln daneben**. Der Fein-Kopf ist **schlechter als
eine Konstante** (4,8 Kacheln Abstand gegenüber 3,1 für die blosse Zellmitte).
Alle Zahlen und Vergleichsmassstäbe im Memory `openfront-kachelkopf-messung`.

## Offene Arbeit, nach Nutzen sortiert
1. **`eval_spatial.py` gegen `bc_big_e1.pt`** laufen lassen und mit der Messung bei
   Schritt 47000 vergleichen. Klärt, ob zusätzliche Epochen das Zielen überhaupt bessern.
   Läuft auf CPU, stört kein Training.
2. **Lauf 2** mit den beiden räumlichen Änderungen. Auf arch liegen `env/bc_fit.py.v2-20260909`
   und `env/bc_train.py.v2-20260909` mit den Schaltern `--coarse-sigma`, `--coarse-soft-mix`
   und `--fine-off`; Vorgaben sind neutral, ohne Schalter also altes Verhalten.
   **Nicht aktiviert**, weil der Nachweis fehlt, dass der abstandsbewusste Verlust den
   Kachelabstand wirklich senkt. Genau dieser Nachweis fehlt noch, dazu ein Startskript.
3. **Flottenlauf v2** für den neuen Pool. `scripts/start_fleet_v2.sh` auf arch ist fertig
   und geprüft, aber **nie gestartet**. Bringt zwei Dinge auf einmal: die Nichtstun-Samples
   und die Reparatur der leeren Shards. Gemessen 163 GB und rund 339 Kernstunden, mit
   11 Containern etwa 31 Stunden. Zielverzeichnis muss **frisch** sein.
4. **Truppenwachstum:** die Handlungsrate ist im Client auf Menschentempo gedrosselt
   (`DECIDE_EVERY` 64). Das ist ein Pflaster. Lernen, *wann* man handelt, geht nur über
   die Nichtstun-Daten aus Punkt 3.

## Was heute schiefging und behoben ist
- **Stromausfall 15:05**, alle Knoten weg, arch startete von selbst neu. Verlust: 800
  Trainingsschritte. Die Wiederaufnahme beginnt die laufende Epoche **von vorn**, das
  kostete zusätzlich 50 Minuten.
- **64 % des Trainingspools sind leere Shards.** Der Pool hat 7,2 statt 18 Millionen
  Samples, das Training lief faktisch auf rund 4400 statt 12217 Partien.
- Board zeigte über eine Stunde „pausiert" bei laufendem Training, danach einen leeren
  Kopf, danach eine um zwei Stunden zu optimistische Restzeit. Alles behoben, alles mit
  Regressionstest. Details im Memory `openfront-stromausfall-jsonl`.
- Absturz des Vorwärtslaufs bei Samples ohne gültigen Gegner, behoben.

## Nicht vergessen
- Alles ist **lokal committet, nichts gepusht**, in beiden Repos.
- Der Cloudflare-Token `cfut_…` aus einer früheren Session sollte widerrufen werden.
- Wake-on-LAN auf arch funktioniert wieder und hat den Neustart überstanden.
