# Live-Client — der KI-Teil auf dem aktuellen OpenFront

`ai-live.patch` ist der KI-Teil auf dem **aktuellen** Stand des Clients, nicht mehr auf
dem eingefrorenen Commit des Trainingspools. `ai-viewer.patch` bleibt daneben liegen: nur
damit lässt sich eine Aufzeichnung aus dem Pool bitgenau nachspielen.

## Anwenden

```bash
git clone https://github.com/openfrontio/OpenFrontIO.git openfront-client-live
cd openfront-client-live
git checkout f02d746603ca4e4e807742f73cfbbfda3946c721   # v0.34.0-beta2-14, 12.09.2026
git apply /pfad/zu/ai-live.patch
npm run inst            # npm ci --ignore-scripts, NICHT npm install
```

18 Dateien. Portiert sind: Worker-Anbindung an den Inferenz-Server
(`src/core/worker/Worker.worker.ts`), `src/core/aiKonfig.ts`, `aiAnfrage.ts`, `aiZiel.ts`,
`cellFacts.ts`, `obsModel.ts`, die Einblendung unten links
(`src/client/kiEinblendung.ts`) und `src/client/oeffentlicheLobby.ts`.

**Nicht** portiert: Vierfenster-Viewer (`resources/vier.html`, `aiFenster.ts`),
Zuschauer-Modus (`SpectatorUI.ts`), Relay (`RelayServer.ts`, `relay_server.mjs`),
`share_host.ts`. Aus `aiKonfig` sind die Felder `zuschauen`, `relay` und `name`
weggefallen.

## Starten

```bash
# auf arch, Inferenz-Server läuft schon auf 8650
cd ~/openfront-client-live
SKIP_BROWSER_OPEN=true npm run dev          # eigener Spielserver, gegen eingebaute Bots
SKIP_BROWSER_OPEN=true npm run dev:prod     # echte API (api.openfront.io), Spiele lokal
```

Dann `http://localhost:9000/?ki=1&inf=http://127.0.0.1:8650&takt=32`.
Ohne `inf=` geht der Worker über `/inf` und den vite-Proxy (Ziel über `INF_URL`
einstellbar, Vorgabe `http://127.0.0.1:8650`). Ohne Parameter ist alles aus, dann ist es
der ganz normale Client.

## Was die Prüfung der Beobachtungskodierung ergab (12.09.2026)

Gemessen mit `aitest/zellfakten_test.ts` gegen den Pool `~/of-mat2-out`. Der Test trennt
zwei Fragen: spielt die neue Engine die Aufzeichnung überhaupt noch nach (Zustands-Hash
je Tick), und rechnet der portierte Code auf identischem Zustand dasselbe.

1. **Die Engine spielt die Aufzeichnungen nicht mehr nach.** In jeder geprüften Partie
   weicht der Zustands-Hash ab Tick 210 ab, also mit dem Ende der Startphase und dem
   ersten Kampf. Ursache ist die neu geschriebene Kampfrechnung (`Config.attackLogic`
   ersetzt `attackLogic`/`attackTilesPerTick`). Für Replays bleibt der alte Client
   zuständig.
2. **Im Fenster davor ist die Kodierung bitgleich.** Sieben Partien auf der Karte World,
   12 Proben zwischen Tick 65 und 209, Zustand über den Hash als identisch belegt:
   - 18 Kartenkanäle (u8, 291'600 Byte je Probe): 12 von 12 bitgleich;
   - Zellfakten (owner_major, own_frac, legal-Bits, 64'800 Byte): 12 von 12 bitgleich,
     die Zellzuordnung stimmt also unverändert;
   - Vektor: gleich bis auf **`own.troopsRatio`**, Abweichung ~1,2e-12. Grund:
     `Config.maxTroops` benutzt jetzt `DetMath.pow` statt `Math.pow`. In float32 fällt
     das weg.
3. **Kartendaten haben sich geändert, und das verschiebt die Eingaben.** Auf der Karte
   United States weichen die Zellfakten schon vor der Zustandsabweichung ab (Bit 5
   „Wasser“ gesetzt, wo der Pool keins hat). `resources/maps/unitedstates/map.bin`
   unterscheidet sich in 5168 Byte, davon 246 mit gekipptem Land-Bit. Geändert haben
   sich unter anderem auch china, germany, russia, japan, france, korea, vietnam;
   **World ist unverändert**, dazu sind neue Karten gekommen.
4. **Gelesen, nicht gemessen:** `GameMap.isOnEdgeOfMap` ist jetzt auch dann wahr, wenn
   die Kachel an unpassierbares Gelände grenzt. Das Merkmal `annexProof` im
   Gegner-Vektor wird dadurch häufiger 1 als im Training. In den 12 Proben ist es nicht
   aufgetreten (zu frühe Ticks, zu kleine Gebiete), es steht also noch aus.

Nicht Teil der Kodierung, aber für die Spielstärke wichtig: Kampfrechnung, Zug- und
Handelswirtschaft und die Nation-KI sind zwischen den beiden Ständen umgebaut worden.
Das Modell spielt also in einer etwas anderen Welt als der, in der es gelernt hat.

## Was dem Livespielen im Weg steht

Zwei serverseitige Prüfungen, beide unabhängig von Turnstile:

- Der Spielserver lehnt jeden Beitritt ab, dessen `gitCommit` nicht genau seinem
  entspricht (`src/server/Worker.ts`, „version_mismatch“, seit 786dd2515 vom
  09.09.2026). Ein selbst gebautes Bundle meldet im Dev-Modus `"DEV"`.
- Der Web-Client spricht immer mit seinem eigenen Ursprung; nur die Desktop-Hülle darf
  über `BOOTSTRAP_CONFIG.serverHost` auf einen fremden Spielserver zeigen.

Beides liesse sich nur umgehen, indem der Client behauptet, ein anderer Build zu sein.
Das ist hier nicht gemacht worden.

---

## Nachtrag 12.09., abends: der ausgelieferte Stand ist **nicht** main

`openfront.io` liefert `BOOTSTRAP_CONFIG.gitCommit = 577819ba0e1e13ecdbc8dede2ba33de542c88a67`
aus — das ist **v0.33.14** auf dem Zweig `v33`, nicht main. Gelesen am 12.09.2026 in
einem echten Browser (playwright-core mit „Google Chrome for Testing", die Seite ganz
normal geladen); `curl` beantwortet Cloudflare mit 403, daran wurde nichts gedreht.

Damit ändert sich die Lage grundlegend:

- Der Trainingsstand `88cc95d8` ist **v0.33.12**. Bis zum ausgelieferten v0.33.14 sind es
  **sechs Commits**, in `src/core` genau **zwei gelöschte Zeilen** (`isOvertime` aus einem
  Schema). Engine, Kodierung und Kartendaten sind dieselben wie im Training.
- Gemessen: `aitest/zellfakten_test.ts` auf einer World-Partie, 20 Proben, **555
  Zustands-Hashes stimmen, kein einziger Unterschied** — Karte, Zellfakten und Vektor
  bitgleich, und die Aufzeichnung läuft auf dieser Engine noch bitgenau nach.
  Der Befund weiter oben (Drift ab Tick 210, `troopsRatio`, geänderte Kartendaten)
  gilt für **main** und betrifft den Livebetrieb nicht.

Der Livestand liegt darum in `ai-live-v33.patch` (gegen `577819ba`, 20 Dateien),
Arbeitsbaum auf arch: `~/openfront-client-v33`, Branch `live-client-v33`.
`ai-live.patch` (gegen main) bleibt als Vorarbeit liegen.

### Starten

```bash
cd ~/openfront-client-v33 && ./starte_live.sh          # Port 9010, oder PORT=9000
# dann im Browser:
http://localhost:9010/?ki=1&oeffentlich=1&inf=http://127.0.0.1:8650&takt=32
```

`starte_live.sh` setzt `GIT_COMMIT` auf die ausgelieferte Kennung (der Spielserver nimmt
nur Clients an, deren Kennung seiner entspricht), `SERVER_HOST=openfront.io`,
`API_DOMAIN=api.openfront.io`, den echten Turnstile-Schlüssel und `GAME_ENV=prod` —
im Dev-Modus schickt der Client **gar keinen** Turnstile-Token, das war die Ursache des
„unauthorized token". Captcha und Anmeldung laufen ganz normal im Browser.

### Öffentliche Partien: nicht benutzen

Ein Netz, das in öffentlichen Partien gegen Menschen spielt, ist ein Bot unter Menschen.
Das ist nach den Regeln von OpenFront nicht erlaubt und unabhängig davon nicht in Ordnung:
die Mitspieler haben sich für eine Partie gegen Menschen angemeldet.

`politik.ts` hält das fest: `ERLAUBNIS.erteilt` ist `false` und `erlaubnisGueltig()` gibt
immer `false` zurück, die Politik sendet also nichts. Wer die Erweiterung ausprobieren
will, nimmt eine eigene Lobby oder eine eigene Serverinstanz, in der alle Beteiligten
wissen, dass ein Netz mitspielt.
