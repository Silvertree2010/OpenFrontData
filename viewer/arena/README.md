# Arena — der Prüfstand für Spielstärke

Verlust und Trefferquote sagen, wie gut das Netz die Trainingsdaten nachahmt. Sie sagen
nichts darüber, ob die KI spielen kann. Die Arena misst das Einzige, was zählt: was im
Spiel herauskommt.

## Wo das liegt und warum

Die Arena läuft **im gepatchten Client**, nicht in `env/`. Grund: gemessen werden soll
genau der Code, der auch im Viewer spielt — `src/core/aiAnfrage.ts` (Beobachtung und
Zellfakten) und `src/core/aiZiel.ts` (Zelle → Kachel, Prüfung mit der Engine). `env/`
zeigt auf `../vendor/openfront` (gibt es nicht mehr) und auf das alte `env/obs.ts`. Ein
Prüfstand, der eine andere Beobachtung baut als der Viewer, misst sich selbst.

```bash
# Client wie in viewer/README.md aufsetzen, dann:
cp -r viewer/arena <client>/arena
```

## Aufruf

```bash
cd <client mit Patch>
npx tsx arena/arena.ts \
    --inf http://127.0.0.1:8650/act \
    --seiten netz,nichtstun --partien 20 --saat lauf1 \
    --karte World --groesse Compact --bots 40 --ticks 3000 \
    --jobs 6 --aus /tmp/lauf1.jsonl
```

- `--aus` bekommt je Partie **und Seite** eine JSONL-Zeile.
- Auf **stdout** steht genau eine Zeile: die Zusammenfassung als JSON. Alles andere
  (Fortschritt, Engine-Geschwätz) geht nach stderr. So kann der Trainer stdout einfach
  durch `json.loads` schicken.
- Der Inferenz-Server wird **vorausgesetzt**, nicht mitgestartet. Er braucht die
  torch-Umgebung und die Kalibrierung, lebt also ausserhalb des Clients; und er lädt den
  Checkpoint bei jeder Änderung selbst nach, muss für einen Zwischenstand also nicht neu
  hochgefahren werden. Für den Trainer erledigt `trainer/arena.py` das Starten.

### Seiten

| Seite | Was sie tut |
|---|---|
| `netz` | fragt alle `decide_every` Ticks den Inferenz-Server |
| `nichtstun` | setzt die Startkachel und handelt danach nie — die Messlatte |

`nichtstun` ist kein Strohmann. Wer unter dieser Linie liegt, spielt sich aktiv kaputt —
genau der Fall, den die Trefferquote nicht sieht. Als drittes Band gibt es
`attrappe_server.mjs`: ein Server, der dasselbe Protokoll spricht, im richtigen Takt
handelt und dabei würfelt. Damit trennt man „handelt überhaupt" von „handelt klug".
Er darf in keiner Aussage über das Netz vorkommen.

### Gepaart

Beide Seiten spielen dieselbe Spiel-ID. Die bestimmt Karte, Nationen, Bots **und die
Startkachel** — nachgewiesen an gleicher `start_kachel` und gleichem `gebiet@500` auf
beiden Seiten. Der ganze Zufall der Arena kommt aus
`PseudoRandom(simpleHash(spielId + ":spawn"))`; `Math.random` kommt nicht vor. Zwei Läufe
mit derselben `--saat` liefern Zeile für Zeile dasselbe (geprüft: 6 von 6 Zeilen gleich,
bis auf die Uhrzeit-Felder `ms`/`ms_inferenz`).

### Ein Prozess je Partie

Das ist keine Vorliebe, sondern eine Messung: `loadTerrainMap` gibt dieselbe `GameMap`
aus einem Modul-Cache zurück, und `GameImpl` arbeitet darauf im Original. Die zweite
Partie im selben Prozess startet also auf dem Endbrett der ersten — sichtbar an
3 statt 43 lebenden Spielern bei Tick 250. Eigene Prozesse trennen sauber und machen
`--jobs` gratis.

## Kennzahlen je Zeile

`gebiet` und `gebiet_rel` bei Tick 500/1000/2000/3000 (Anteil aller Landkacheln, und
Anteil am Grössten), `rang`, `ueberleben_ticks`, `verlauf` (alle 250 Ticks: Gebiet,
Truppen, Gold, Lebende, Führer), `handlungen`, `handlungen_je_1000`, `anfragen`,
`nichtstun`, `ohne_kachel`, `serverfehler`, `aktionen` (Häufigkeit je Art),
`platz`/`von`/`sieg`, `abbruchgrund`, `ms`/`ms_inferenz`.

Tick 500 ist dabei, weil die Karte dort schon voll ist (gemessen auf Bosphorus Straits:
98,8 % belegt bei Tick 500). Wer erst ab 1000 hinschaut, verpasst die Phase, in der das
Spiel entschieden wird.

**Zielband Handlungsrate:** 20 bis 25 Handlungen je 1000 Ticks (Mensch: Median alle
44 Ticks, 14'466 Spieler). `handlungen_je_1000` prüft das direkt. Kalibriert wird mit
`trainer/kalibriere_schwelle.py --ziel 44`; die Kontrollzahl im Ergebnis (`mensch.median`)
sagt, was die Menschen in den benutzten Val-Partien selbst tun — am 12.09. waren das
51 Ticks, nicht 44. Die 44 stammen aus dem ganzen Pool, die Val-Auswahl ist kleiner.

## Auswertung

```bash
python3 arena/auswertung.py /tmp/lauf1.jsonl                 # netz gegen nichtstun
python3 arena/auswertung.py alt.jsonl neu.jsonl --a-seite netz --b-seite netz
python3 arena/auswertung.py /tmp/lauf1.jsonl --json
```

Median, p25, p75 je Seite; gepaart die Median-Differenz, wie oft welche Seite gewinnt,
ein exakter Vorzeichentest und Wilcoxon in der Normalnäherung. Mit wenigen Partien kann
kein Test etwas sagen — bei 5 Paaren ist p ≥ 0,0625, egal wie klar das Bild ist. Für ein
Urteil braucht es 20 Partien und mehr; das Skript sagt es auch selbst dazu.

## Spielweise (seit 14.09.)

Der Platz allein sagt wenig darüber, *wie* eine KI spielt. `spielweise.ts` misst je KI-Spieler
alle 250 Ticks (`--verlauf-alle`) und fasst je Partie zusammen; die Zeile trägt
`spielweise.verlauf` (spaltenweise) und `spielweise.summe`, dazu `ms_spielweise`.

| Bereich | Kennzahlen | Herkunft |
|---|---|---|
| Wirtschaft | Gold, Gold pro Minute (Einkommen), ausgegeben, Anteil Handel | Engine-Stats `gold[]` (Einkommen je Quelle); ausgegeben = Einkommen − Δ Bestand |
| Strukturen | Anzahl je Typ, gebaut/verloren/erobert/aufgewertet | `p.units()`; Stats `units[typ]` |
| Militär | Truppen, Truppen-Max, Angriffe gesendet/erhalten, laufende Angriffe, Boote, Nukes | `p.troops()`, `config.maxTroops`; Zähler um `stats.attack`; Stats `boats`, `bombs` |
| Diplomatie | Anfragen gesendet/erhalten, geschlossen, aktiv, Verrat, verraten worden, Embargos | GameUpdates (AllianceRequest/-Reply, BrokeAlliance, EmbargoEvent); `p.alliances()`, Stats `betrayals` |
| Gebiet | Anteil, Überleben, Platz, Stücke, grösstes Stück, 16×16-Raster, Streuung, Küste | `p.tiles()`, `map.isShore`; Formeln in `verteilung()` und `verteilung.py` |

Die Messung liest nur mit: gleiche Saat mit und ohne Messung ergibt dieselben Zeilen (geprüft
14.09., `ARENA_SPIELWEISE=0` schaltet sie ab). Kosten: 3,6 ms in einer ganzen Partie (0,28 %);
Obergrenze `tests/spielweise_bench.ts` (400 Bots): 0,51 ms je Probe bei im Mittel 9800
eigenen Kacheln gegen 299 ms Engine je 250 Ticks, also 0,17 % je KI.

`auswertung.py` gibt je Seite Median und p25–p75 aus und gepaart je Kennzahl die
Median-Differenz, wer höher liegt, und p nach **Holm** über alle 38 Kennzahlen (A/A zeigte bei
10 Kennzahlen 20–25 % Scheinbefunde; Holm hält die familienweise Rate bei 5 %,
`tests/holm_test.py`). `--json-aus` schreibt Tabelle und Verlaufskurven fürs LobsterBoard
(`trainer/arena.py --spielweise` erledigt das nach einem Paar).

**Gleiche Zufallszahlen im Paar:** jede Anfrage trägt `wahl_schluessel = Spiel-ID|clientID|Tick`;
mit `inf_d0 --wahl-saat N` hängt das Ziehen nur an (Saat, Schlüssel, Kopf), nicht an der
Reihenfolge der Anfragen. Zwei Läufe derselben Saat sind bitgleich, auch mit anderem `--jobs`
(`tests/bitgleich.py a.jsonl b.jsonl`); ohne Saat nicht.

**Menschen-Referenz:** `menschen.py` rechnet dieselben Kennzahlen aus dem Pool (meta, zusatz,
own.zst, units.zst), ohne Engine-Replay. Was genähert ist oder fehlt, steht im Kopf des Skripts
und in der Ausgabe (`herkunft`). Test: `tests/menschen_test.py` (synthetische Partie).

## Mehrere KIs und Aufnahme

```bash
npx tsx arena/arena.ts --seiten netz --partien 1 --ki 20 --ticks 36000 \
    --karte World --bots 40 --saat ki20 --aus /abs/ki20.jsonl --aufnahme /abs/aufnahmen
```

- `--ki N` (Standard 1): N Menschen-Plätze, alle vom selben Server, eine Anfrage je Spieler.
  Alle setzen ihre Startkachel im selben Tick (im Einzelspiel beendet der erste
  Menschen-Spawn die Startphase); die Kacheln halten `--start-abstand` auch untereinander
  ein. Je KI eine JSONL-Zeile mit `ki`, `name`, `platz`, `ueberleben_ticks` und `partie`
  (Grund, Sieger, Führer). Mit N > 1 endet die Partie, wenn alle N tot sind oder die
  Engine einen Sieger meldet (+20 Ticks). N = 1 ohne `--aufnahme` spielt Zeile für Zeile
  wie vorher (geprüft: gleiche Zeile bis auf `ms`).
- `--aufnahme ORDNER`: je Partie `ORDNER/<id>.json` als GameRecord. Die ID ist 8 Zeichen
  (Schema), clientIDs `ki000001`…; Konfiguration und Intents laufen durch die Schemas des
  Clients, alle 100 Ticks steht der Zustands-Hash drin. `trainer/arena.py` reicht `--ki`,
  `--aufnahme` und `--zeitlimit` durch.

Abspielen auf dem Mac (Dev-Build, gitCommit "DEV", keine Versionsprüfung):

```bash
cd ~/projects/private/openfront-client && npm run dev            # vite 9000 + Spielserver 3000
node ~/projects/private/openfront-ai-arena/viewer/arena/aufnahme_server.mjs \
    ~/projects/private/openfront-ai-arena/data/aufnahmen          # Port 8787
open http://localhost:9000/game/<id>                                # Liste: http://localhost:8787/
```

Der Client holt archivierte Partien im Dev-Build von `http://localhost:8787/game/<id>`
(`getApiBase()`), der Aufnahme-Server liefert genau das. Beim Abspielen prüft der Client
die Hashes (`hash verified` / `desync` in der Konsole).

## Spielaufbau, und was er festlegt

`gameType: Singleplayer`. Dann endet die Startphase, sobald der Mensch eine Kachel wählt.
Die Arena wartet darum bis `--start-tick` (Standard 100, die Länge, die die Engine im
Einzelspiel selbst vorsieht), damit Nationen und Bots zuerst auf dem Brett stehen.

Die Startkachel zieht `--start-abstand` (Standard 30) mit ein: bevorzugt wird eine Kachel,
in deren Umkreis niemand sitzt. Rein gleichverteilt zu ziehen würde vor allem messen, wie
schnell ein eingekesselter Start stirbt. Die Kehrseite: **der Spawn-Kopf des Netzes wird
nicht bewertet**, beide Seiten bekommen dieselbe Kachel geschenkt. Anders wäre der
Vergleich nicht gepaart.

Ein weiterer bewusster Unterschied zum Viewer: die Arena wartet auf die Antwort des
Servers und handelt im Zustand desselben Ticks. Der Worker im Browser rechnet nebenher und
hängt die Aktion an einen späteren Turn. Die Arena misst die KI also einen Hauch
optimistischer — dafür wiederholbar.

## Erster Lauf gegen ein echtes Netz, 12.09. auf arch

`d0_rauch.pt` (1371 Schritte, kurz trainiert), Schwelle auf Ziel 44 Ticks kalibriert
(k=32 → 0,015940), World/Compact, 40 Bots, 113 Spieler, Tick-Limit 3000,
8 gepaarte Partien, `inf_d0` auf CPU.

| Kennzahl (Median) | netz | nichtstun | gepaart |
|---|---|---|---|
| gebiet@500 | 0,01448 | 0,00026 | 8 von 8, p = 0,0078 |
| gebiet@1000 | 0,02245 | 0,00026 | 7 von 8, p = 0,016 |
| gebiet_rel@1000 | 0,343 | 0,0046 | 7 von 8, p = 0,016 |
| ueberleben_ticks | 1513 | 1108 | 6 von 8, p = 0,29 — nicht belegt |
| platz | 40,5 / 113 | 52 / 113 | 5 von 8, p = 0,45 — nicht belegt |

Das Netz wächst also messbar über die Messlatte hinaus. Zwei Funde, die keine
Trefferquote gezeigt hätte:

1. **Die Schwelle greift nicht.** 434 Anfragen, davon **0 Nichtstun**: das Netz handelt bei
   jeder Frage, die Rate ist 31,5 je 1000 Ticks und damit nur der Abfragetakt (32), nicht
   die Schwelle. Zielband sind 20 bis 25. `kalibriere_schwelle.py` rechnet auf den
   Zuständen echter Menschen; auf den Zuständen, die die KI selbst erzeugt, liegt
   P(handeln) durchweg höher. Die Kalibrierung muss aus Arena-Partien kommen, nicht aus
   Val-Partien — oder die Arena regelt die Schwelle selbst nach.
2. **Das Netz kennt nur Angriff.** 427 ATTACK, 7 SPAWN, kein einziges Bauwerk und kein
   Boot. Bei diesem Rauch-Checkpoint ist das zu erwarten, aber genau diese Zeile
   (`aktionen`) ist der Punkt, an dem man es sieht.

### Laufzeit, gemessen

- Je `netz`-Partie 4,6 s im Median (2,8 bis 8,1 s je nach Überlebensdauer), davon
  1,0 s Inferenz — also **21 %**, rund 20 ms je Anfrage. Die Engine ist der teure Teil,
  nicht das Netz.
- Ein Arena-Eval mit 8 gepaarten Partien: **18,6 s** mit `--jobs 4`.
- Skalierung (8 `netz`-Partien, ein Server): seriell 33,8 s, `--jobs 4` 11,3 s,
  `--jobs 8` 9,6 s.
- **Ein `inf_d0` reicht.** 16 Partien mit `--jobs 12`: ein Server 18,4 s, vier Server
  17,5 s — 5 % Unterschied. Die Komma-Liste bei `--inf` bleibt für den Fall, dass ein
  grösseres Netz die Rechnung umdreht; nötig ist sie nicht.
