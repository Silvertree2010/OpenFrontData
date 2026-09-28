# AI-Viewer — die Änderungen am OpenFront-Client

Hier liegt **nur der Unterschied**, nicht der ganze Client. Der Ursprung ist
öffentlich (`openfrontio/OpenFrontIO`), den muss niemand sichern.

## Anwenden

```bash
git clone https://github.com/openfrontio/OpenFrontIO.git openfront-client
cd openfront-client
git checkout 88cc95d8b6d74d951546da341be809bfb3cab960
git apply /pfad/zu/ai-viewer.patch
npm run inst          # npm ci --ignore-scripts, NICHT npm install
```

Der Commit ist nicht beliebig: die Aufzeichnungen im Pool wurden auf genau
dieser Engine gespielt, und nur damit läuft ein Replay bitgenau.

## Was der Patch enthält

18 Dateien.

- **`src/client/SpectatorUI.ts`** — eigene Zuschauer-Oberfläche. Blendet die
  komplette Standard-Oberfläche aus und zeichnet stattdessen Banner, Rangliste
  und Modellwerte. Spiel-Eingaben sind über den Transport stillgelegt.
- **`src/client/RelayServer.ts`** und **`relay_server.mjs`** — ein Host spielt
  die kanonische Partie und schickt seinen Zugstrom an ein Relay, alle Zuschauer
  spielen genau diesen Strom deterministisch nach. So sehen alle dasselbe, und
  niemand ausser dem Host muss das Modell befragen.
- **`src/core/worker/Worker.worker.ts`** — fragt den Inferenz-Server nach der
  nächsten Aktion und hängt sie an den nächsten Zug.
- **`src/core/aiAnfrage.ts`** — baut die Anfrage genau wie der Materialisierer die
  Trainingsdaten: Karte (u8, base64), Vektor, Gegner, Spielkonfiguration
  (`gameStartInfo.config`, früher `{}`) und die Zellfakten.
- **`src/core/cellFacts.ts`** — Zellfakten 90×180 (owner_major, own_frac, legal-Bits),
  portiert aus `materializer/src/cells.ts`; Logik unverändert, nur Import und
  `Uint8Array` statt Node-`Buffer`.
- **`src/core/aiZiel.ts`** — D0-Spielregel im Client: wählt in den bis zu 5
  Kandidaten-Zellen des Servers die Kachel (Regel je Typ, Entwurf ZIELWAHL §6) und
  prüft sie mit der Engine (`canBuild`, `canBuildTransportShip`,
  `wouldNukeBreakAlliance`). Die erste gültige Zelle gewinnt, sonst entfällt die Aktion.
- **`src/core/obsModel.ts`** — die Beobachtungskodierung, identisch mit
  `materializer/src/obs.ts` bis auf die Importpfade.
- **`share_host.ts`** — alternativer Host ohne Browser, gedacht für apollo.

## Wie oft das Modell handelt

Gemessen über 74 echte Partien handelt ein menschlicher Spieler im Median
**alle 73 Ticks** (p25 55, p75 100).

- **Altes Netz** (`env/inf_server.py`): Es kennt kein Nichtstun. Der Worker fragt
  deshalb nur alle 64 Ticks (`DECIDE_EVERY`), eine Drossel.
- **D0** (`trainer/inf_d0.py`): Nichtstun ist eine gelernte Klasse. Der Server handelt
  nur, wenn P(handeln) über einer Schwelle liegt. `trainer/kalibriere_schwelle.py`
  wählt sie auf Val-Partien so, dass der Median-Abstand je Spieler 73 Ticks ist,
  getrennt für jeden Abfragetakt k. Der Server nennt sein k in jeder Antwort
  (`decide_every`), der Worker übernimmt es. Mit `d0_rauch.pt` (Rauchtest, 1371
  Schritte) ergab k=32 die Schwelle 0,0241: Median 72,5, p25 45, p75 150 Ticks.
  Nach jedem neuen Checkpoint neu kalibrieren.

## Starten

1. Auf arch, im Trainer-Ordner (z. B. `~/mat-dev/netz-dev`), einmal je Checkpoint die
   Schwelle kalibrieren (etwa 5 min CPU):
   ```bash
   P=~/mat-dev/torchenv/bin/python; R=~/projects/openfront-ai/data/reputation.json
   $P trainer/kalibriere_schwelle.py --ckpt checkpoints/<d0>.pt --daten ~/of-mat2-out \
       --reputation $R --aus kalibrierung.json
   ```
2. Den Inferenz-Server starten, CPU reicht (65 bis 80 ms je Anfrage):
   ```bash
   $P trainer/inf_d0.py --ckpt checkpoints/<d0>.pt --kalibrierung kalibrierung.json \
       --decide-every 32 --port 8650 --host 0.0.0.0 --device cpu --reputation $R
   ```
   Er lädt die Checkpoint-Datei bei jeder Änderung neu. Die Schwelle bleibt dabei
   die kalibrierte. Für das alte Netz stattdessen
   `python env/inf_server.py --ckpt checkpoints/<alt>.pt --port 8650 --host 0.0.0.0 --device cpu`.
   Beide nutzen Port 8650, es läuft immer nur einer.
3. Lokal im Client: `node relay_server.mjs &` (Port 8652) und `npm run dev &`
   (vite 9000, Spielserver 3000).
4. `open http://localhost:9000/` — localhost ist automatisch der Host, das Spiel
   startet von selbst.

Die Adresse des Inferenz-Servers steht in `Worker.worker.ts` und in
`vite.config.ts` fest verdrahtet (`arch.example:8650`) und muss angepasst werden.

Worauf achten: Mit D0 sollte das Modell etwa alle 70 Ticks handeln, nicht bei jeder
Frage. Bauwerke sollten im eigenen Gebiet stehen, Boote am Gebiet des Ziels landen. Im
Server-Log (`[act]`-Zeilen) stehen Aktion, Einheit und P(handeln).

## Spielstärke messen

`viewer/arena/` ist der Prüfstand: N Partien ohne Browser und ohne Zuschauer, je Partie
eine JSONL-Zeile, gepaart gegen einen „Nichtstun-Bot". Aufsetzen mit
`cp -r viewer/arena <client>/arena`, Anleitung in `viewer/arena/README.md`. Aus dem
Trainer heraus: `trainer/arena.py`.

## Prüfen ohne Browser

Die Tests laufen im gepatchten Client. Dazu die Dateien aus `viewer/tests/` nach
`<client>/aitest/` kopieren.

- `npx tsx aitest/zellfakten_test.ts <record.json> <materialisierer-ordner> <gid>`:
  Die Zellfakten des Clients sind bitgleich zu den `.cells`-Blöcken.
  11.09.: 76 Paare an 60 Ticks, 0 Abweichungen.
- `npx tsx aitest/ende_zu_ende_test.ts <record.json> <ordner> <gid> http://…/act 32`
  prüft vier Dinge:
  - Anfragen des Clients bitgleich zu den Trainingsdaten;
  - Server antwortet;
  - Kachelwahl gegen die Engine.

  11.09.: 375 Proben bitgleich, 63 von 63 Zellen aufgelöst.
- `trainer/tests/inftest.py --ckpt … --daten ~/of-mat2-out`: Die Server-Entscheidung
  stimmt mit dem Trainer-Eval auf denselben Samples überein. 11.09.: 96 Samples,
  Logit-Abweichung 0.

## Vierfenster-Viewer

Eine Seite, vier Spielfenster, in jedem ein anderer Checkpoint. Startskript für die
Inferenz-Server: `viewer/starte_inf.sh` (läuft auf arch, vier Ports, CPU).

```bash
# 1. auf arch: vier Server, einer je Fenster
ssh arch '~/starte_inf.sh'          # 8650 Ziehen · 8651 Argmax · 8652/8653 ältere Stände

# 2. auf dem Mac, im gepatchten Client:
SKIP_BROWSER_OPEN=true npm run dev  # vite 9000, eigener Spielserver 3000
open http://localhost:9000/vier.html
```

Jedes Fenster ist ein eigener iframe auf `/?ki=1&inf=…` und damit ein vollständiger
Client mit eigenem Spiel, eigenem Worker und eigenem Inferenz-Server. Was das Fenster
tut, steht allein in der Adresse (`src/core/aiKonfig.ts`, Felder `ki`, `inf`,
`zuschauen`, `auto`, `karte`, `groesse`, `bots`, `tempo`, `takt`, `name`, `relay`).
**Ohne Parameter ist alles aus** — dann ist `/` der ganz normale Client, in dem der
Nutzer selbst spielt. Vorher war der Zuschauer-Modus fest verdrahtet.

Steuerung (oben global, je Kachel einzeln): Pause, Tempo, neu starten, gross ziehen,
Serverauswahl. Unter jeder Kachel laufen die Kennzahlen mit: Tick, Gebiet, Platz,
Truppen, Gold, Handlungen je 1000 Ticks, P(handeln), letzte Aktion, Checkpoint-Schritt.
Sie kommen aus dem Worker, der das echte Spiel hat, und gehen per `postMessage` hoch.

Die Wahlart (Ziehen mit Temperatur und Top-k oder Argmax) ist in `inf_d0.py` eine
Einstellung des **Servers**, nicht der Anfrage. Ein Fenster wählt sie darum über den
Port. Wer sie je Fenster frei einstellen will, muss `inf_d0.do_POST` erlauben,
`wahl` aus der Anfrage zu übernehmen — das ist eine Zeile dort, aber Trainer-Gebiet.

### Einzelfenster

```bash
# eigener Server, Modell spielt gegen die eingebauten Bots
http://localhost:9000/?ki=1&inf=http://127.0.0.1:8650&takt=32

# öffentliche Lobby: gesperrt, siehe Abschnitt weiter unten
```

Das Einzelfenster zeigt die **normale Oberfläche**, nicht die Zuschauer-Ansicht, und die
Eingabesperre ist aus: der Nutzer kann jederzeit selbst klicken, bauen, angreifen — er und
das Modell steuern dasselbe Volk. Die Kennzahlen stehen in einer kleinen, wegklappbaren
Einblendung unten links. Der Vierfenster-Viewer setzt `zuschauen=1` ausdrücklich, weil die
normale Oberfläche auf einer Viertelseite unbedienbar wäre.

Öffentlich (`oeffentlich=1`): kein Autostart und kein eigener Startpunkt. Der Nutzer sieht
den normalen Startbildschirm, klickt ein öffentliches Spiel an und setzt seinen Startpunkt
selbst; ab dem Ende der Startphase spielt das Modell (`spawnSelbst=false` in
`aiKonfig.ts`, ausgewertet in `Worker.worker.ts`). In der Einblendung laufen die Spieldauer
und ein Knopf „Partie verlassen" mit.

**Öffentliche Lobbys sind gesperrt.** Ein Netz, das dort gegen Menschen spielt, ist ein Bot
unter Menschen: nach den Regeln von OpenFront nicht erlaubt und den Mitspielern gegenüber
nicht fair. `politik.ts` gibt in `erlaubnisGueltig()` immer `false` zurück, die Politik
sendet also keine Züge. Gedacht ist die Erweiterung für eigene Lobbys oder eine eigene
Serverinstanz, in der alle wissen, dass ein Netz mitspielt.
