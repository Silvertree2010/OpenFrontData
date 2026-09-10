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

15 Dateien, 1330 neue Zeilen.

- **`src/client/SpectatorUI.ts`** — eigene Zuschauer-Oberfläche. Blendet die
  komplette Standard-Oberfläche aus und zeichnet stattdessen Banner, Rangliste
  und Modellwerte. Spiel-Eingaben sind über den Transport stillgelegt.
- **`src/client/RelayServer.ts`** und **`relay_server.mjs`** — ein Host spielt
  die kanonische Partie und schickt seinen Zugstrom an ein Relay, alle Zuschauer
  spielen genau diesen Strom deterministisch nach. So sehen alle dasselbe, und
  niemand ausser dem Host muss das Modell befragen.
- **`src/core/worker/Worker.worker.ts`** — fragt den Inferenz-Server nach der
  nächsten Aktion. Hier steht auch `DECIDE_EVERY`.
- **`share_host.ts`** — alternativer Host ohne Browser, gedacht für apollo.
- **`src/core/obsModel.ts`** — die Beobachtungskodierung im Client.

## DECIDE_EVERY steht auf 64, und das ist gemessen

Vorher stand es auf 8. Damit handelte das Modell **alle 6,8 Ticks**. Gemessen
über 74 echte Partien handelt ein menschlicher Spieler im Median **alle 73
Ticks** (p25 55, p75 100). Das Modell war also elfmal so aktiv, gab dabei
permanent Truppen aus und kam nie über 4k, während kleinere Nachbarn bei 7 bis
9k standen.

Das ist ein Pflaster, keine Lösung. Die Ursache liegt in den Daten: „nichts tun"
kommt in den Trainingsetiketten **7 Mal von 72320** vor, weil die
Materialisierung nur Zeitpunkte mit echtem Menschen-Intent aufgenommen hat. Das
Netz kann Abwarten nicht darstellen und antwortet auf jede Anfrage mit einer
Aktion. Erst ein Pool mit Nichtstun-Beispielen macht die Drossel überflüssig.

## Starten

1. Auf der Trainingsmaschine den Inferenz-Server:
   `python env/inf_server.py --ckpt checkpoints/<modell>.pt --port 8650 --host 0.0.0.0 --device cpu`
   Er lädt die Checkpoint-Datei bei jeder Änderung neu und folgt so einem
   laufenden Training.
2. Lokal: `node relay_server.mjs &` (Port 8652) und `npm run dev &`
   (vite 9000, Spielserver 3000).
3. `open http://localhost:9000/` — localhost ist automatisch der Host, das Spiel
   startet von selbst.

Die Adresse des Inferenz-Servers steht in `Worker.worker.ts` und in
`vite.config.ts` fest verdrahtet und muss angepasst werden.
