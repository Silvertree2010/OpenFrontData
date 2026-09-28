# Analyst C („speicher") — Q4 Speicherung, Q5 Kennzahl

Kennzeichnung: **[V]** verifiziert (Code gelesen oder gemessen), **[S]** synthetische Messung (echte Geländemasken, künstliche Besitzverhältnisse), **[E]** Schätzung/Herleitung, **[K]** Zahl vom Koordinator/Handoff, nicht selbst geprüft.

---

## 1 Entscheidung in Kürze

**Befund, der die Frage verschiebt:** Die ~340 Kernstunden sind zum grössten Teil **Encoder-Kosten, nicht Replay-Kosten**. `obs.ts scanTick` kostet pro abgetastetem Tick 8–11 ms [S] (davon ~6 ms das argmax über das 16200×600-`counts`-Feld). Die reine Simulation braucht ~0,33 ms pro Tick (3000 Ticks/s, Zahl des Users). Ein reines Replay des ganzen Pools liegt damit bei **~8–16 Kernstunden** [E], nicht bei 340. Das muss der Kanarienlauf bestätigen (Messhaken siehe §4, Punkt A6).

**Speicherentscheidung für die Neu-Materialisierung JETZT:**

| Block | wofür | Zusatz-Platte | Zusatz-CPU |
|---|---|---|---|
| **A Basisfelder** (alle Samples/Spiele) | eigene smallID, Verbündete, Team, expliziter Tick, Aufgelöst-Ergebnis (`res`), Spielkopf (Karte, Grösse, W×H, Commit), Spawn-Samples aus der Spawnphase | < 1 GB (+ ≤ 7 GB Spawn) | ~1 % |
| **B Tier 1: Zustandsstrom je Spiel** (Besitzwechsel-Log + Einheiten-Ereignisse + Checksummen) | jede Kachel-Sicht zu jedem Tick in Python rekonstruierbar, ohne Engine | 3,7–17,6 MB/Spiel [E/S] | < 1,5 % [E] |
| **C Tier 2: Kachel-Pyramide je räumlichem Sample** (3 × 160×160, Schritt 1/2/4, um das Label + 4 Art-Masken 90×180) | Varianten in voller Grösse ohne Tier 1 | 6–8 KB/Sample [S] | 1–2,5 % [E] |

**Entscheidungsregel mit einer Zahl aus dem Kanarienlauf (50 Records, ohnehin Pflicht):**
- Mittlere Grösse von Tier 1 **≤ 6 MB/Spiel** (≙ ≤ 110 GB für 18 018 Spiele): **A + B für alle Spiele**, C weglassen. C ist dann in Python aus B in < 1 h ableitbar.
- Sonst **A + B für eine feste 25-%-Teilmenge** (Hash-Auswahl, enthält alle Val-Spiele) **+ C für alle räumlichen Samples**. Zusatz: ~17–42 GB (B-Teilmenge) + ~60–80 GB (C, bei ~10 Mio räumlichen Samples) ≈ **80–120 GB**.
- **Rückfallstufe bei Plattennot:** A + B nur für eine 15-%-Teilmenge mit allen Val-Spielen. Zusatz ~10–25 GB, höchstens ~50 GB. Entwurfsvergleich dann auf ~2700 Spielen. Das ist mehr, als der letzte Lauf faktisch sah (~4400 Spiele wegen der leeren Shards). Das Hochskalieren läuft danach über ein schlankes Replay.

**Primäre Kennzahl (Q5):** **M@16**, je räumlichem Aktionstyp: Wahrscheinlichkeitsmasse, die die Policy innerhalb von 16 **echten** Kacheln um die menschliche Zielkachel legt. Gerechnet über die legal maskierte Kachelverteilung, bei Einsatz-Temperatur, auf einem festen, separat gespeicherten Eval-Set von 5000 räumlichen Samples.
- Sekundär 1: **ΔNLL_legal** (Bits besser als gleichverteilt über legale Kacheln).
- Sekundär 2: **Argmax-H@16**.
- Takt: alle 2000 Schritte, ~5 s, ~1,5 % Overhead [E].

---

## 2 Heutiges Format und Kostenstruktur

### 2.1 Was pro Sample gespeichert wird [V] (materialize.ts, Mac-Kopie vom 09.09. 15:06)
- `<gid>.maps`: Folge `[uint32 LE Länge][zstd(Karte)]`. Die Karte ist 18×90×180 float, quantisiert auf uint8 (`round((x+1)*127.5)`), roh 291 600 B, laut Kommentar ~11 KB zstd [K]. Einzelblock-zstd mit Standardlevel.
- `<gid>.meta.zst`: zstd(JSONL) einer ganzen Partie, eine Zeile pro Sample:
  - `turn`, `clientID`, `mapW`, `mapH`, `troops`, `gold`, `oppIds`, `ownUnitIds`, `ownAttackIds`
  - `own` (Dict, 19+ Felder), `opps` (≤ 24 Dicts inkl. `id` = smallID, `ally`, `user`, `clan`)
  - `intent` roh, also inklusive roher Kachelindex `tile`/`dst` und `unit`
  - `w` (1 oder NOOP_EVERY), `win`
- Label-Encode und Featurisieren passieren erst in Python (`dataset.py` → `actions.encode`, `featurize.py`) [V]. Config kommt aus den Records [V].
- **Fehlt heute, wird für jede Neu-Featurisierung gebraucht:** die **smallID des Handelnden** (die Besitz-IDs im Kachelzustand sind smallIDs), die Liste der Verbündeten ausserhalb der Top-24 und das Team. Ausserdem fehlen `game.ticks()` als expliziter Tick, der Kartenname, die Kartengrösse und der Engine-Commit im Shard. Der Kartenname steht nur im Record. Und es fehlt, wo die Aktion tatsächlich landete.
- **Spawn-Samples fehlen fast vollständig** [V]: `materialize.ts` emittiert nur bei `!game.inSpawnPhase()`. In den 65 lokalen Records gibt es im Mittel 126 Spawn-Intents pro Partie [V]. Die alte Auswertung hatte nur 60 SPAWN-Samples von 4122 [K]. Dieses Gate ist die wahrscheinliche Ursache [E]; die Engine-Semantik gehört Unit A.
- Die Mac-Kopie kann hinter arch zurückliegen: `start_fleet_v2.sh` rsynct die `env/materialize.ts` von arch auf die Knoten [V]. Alle Aussagen hier beziehen sich auf die Mac-Kopie.

### 2.2 Wohin die Kernstunden gehen
- Die Schleifenstruktur von `scanTick` ist nachgebaut und gemessen (`scripts_C/bench_encoder.mjs`, M4 Pro, synthetischer Zustand) [S]:

  | Karte | scanTick | davon argmax 16200×600 | Tile-Schleife | encodeMap + Quantisieren + zstd pro Sample |
  |---|---|---|---|---|
  | 1000×500 | 11,4 ms | 6,2 | 4,5 | 0,62 ms |
  | 2000×1000 | 10,6 ms | 6,1 | 4,0 | 0,50 ms |
  | 2904×1672 | 8,4 ms | 5,7 | 2,2 | 0,66 ms |

- Nicht gemessen: `encodeVec` (läuft pro Sample die Grenzen der Top-24-Gegner ab), Einheiten-Stempeln, JSON, die Simulation selbst.
- `scanTick` läuft nur in Ticks, in denen ein Sample entsteht [V]. Anteil der Ticks mit ≥ 1 handelndem Menschen: Median 27 %, Mittel 32 % (8–71 %) über die 65 Records [V]. Dazu kommen Nichtstun-Ticks, bei 1/200 pro Mensch ≤ ~15 % [E]. Zusammen werden **~35–42 % aller Ticks** gescannt.
- Pro Partie (Median 9311 Ticks in den 65 Records [V]): ~3300–3900 Scans × ~10 ms ≈ 33–39 s auf M4 Pro. Simulation 9311/3000 ≈ 3 s. Karten-Encode ~1 s.
- Zum Vergleich [K]: 339 Kern-h / 18 018 Partien = **68 Kern-s pro Partie**. Das passt zu einer Scan-dominierten Rechnung, weil die Flottenkerne langsamer sind als der M4 Pro und `encodeVec` noch dazukommt.
- **Folgerung [E]:** Die Simulation macht ~5 % der Kosten aus. Eine Neu-Featurisierung braucht nur dann 340 Kern-h, wenn sie wieder diesen Encoder benutzt. Ein schlankes Replay, das zusätzliche Felder rausschreibt, kostet ~8–16 Kern-h (18 018 × 5000–9300 Ticks / 3000 Ticks/s). Das sind ~1–2 h auf den 8 Kernen von arch.
- Vorbehalt: Die 3000 Ticks/s sind die Zahl des Users. Für späte 100-Spieler-Zustände ist sie hier nicht geprüft.
- Nebenbefund, nicht beauftragt: `stride` = 600 im argmax kostet ~60 % eines Scans. Mit der tatsächlichen Spielerzahl würde der Pool mehrfach billiger, bei identischer Ausgabe. Das würde ich nur nach dem Lauf anfassen.

---

## 3 Strategievergleich

Annahmen:
- Pool mit 18 018 Spielen. Mittlere Landfläche pro Partie 1,17 Mio Kacheln [V/E]: nach `multiplayer_frequency` gewichtete Manifeste, Normal 1,46 Mio, Compact 0,36 Mio, Mix 74/26 wie in den 65 Records.
- **Räumliche Samples im Pool: ~10 Mio (Spanne 4,5–13 Mio)** [E]. Herleitung: alter Pool 1636 Samples/Partie; räumlicher Anteil nach Angriffs-Ausdünnung ~30–36 %, weil 22,9 % aller Intents räumlich (ohne Spawn) sind [V INTENTS.md]; hochgerechnet auf 18 018 Partien. Die 4,5 Mio des Koordinators (25 % × 18 Mio) sind die Untergrenze.

| Strategie | Bytes | Summe Pool | Zusatz-CPU | ermöglicht später ohne Engine |
|---|---|---|---|---|
| (a) volle Momentaufnahme pro Sample (uint16, W×H) | 21–148 KB [S], je nach Karte | 10 Mio × ~60 KB ≈ **0,6 TB** (0,2–1,5 TB) | gering | alles, aber zu gross → **verworfen** |
| (b) Zustandsstrom pro Spiel: Keyframes + Besitzwechsel | 1,0–1,25 B/Wechsel [S], sortiert + Delta + zstd; 2,3 B in Engine-Rohreihenfolge. 2,4–6 Wechsel/Landkachel [S/E] → **3,7–17,6 MB/Spiel** | **66–320 GB** (zentral ~100–170) | < 1 s/Spiel [E] | jede Auflösung, jedes Zentrum, jede Legalitätsregel, Kandidatenlisten, jede Tick-Auswahl. Python-Rekonstruktion ~0,01 s/Spiel [S] |
| (c) feste Ausschnitte pro räumlichem Sample | 64²: 0,2–0,4 KB; 128²: 0,75–1,3 KB; 256²: 2,6–4 KB (Schritt 1); 256/2: 1,0–1,4 KB; 512/4: 1,2–1,5 KB [S]. **Pyramide 3 × 160² (Schritt 1/2/4): 4,5–5,9 KB, p90 6,3–7,5 KB** [S] | 10 Mio × 5,2 KB ≈ **52 GB** (23–68) | ~1 ms/Sample [E] | Label-zentrierte Varianten mit Versatz-Jitter ≤ ±16/±32/±64 Kacheln; Legalität auf Kachelebene nur im Fenster |
| (d) Legalitätsmasken 90×180, bitgepackt (2025 B roh) | Bau/eigenes Land ~0,07–0,11 KB; Boot/Küste 0,6–1,2 KB [S]. **4 Art-Masken zusammen 1,2–2,5 KB** [S] | 10 Mio × ~2 KB ≈ **20 GB** | ~1 ms/Sample [E] | maskierte Heatmap auf 90×180; Kandidaten auf Zellebene |
| (d') Kandidatenlisten auf Kachelebene | eigenes Land: 1,8k–108k Kacheln/Spieler [S] → zu gross | – | – | aus (b) oder (c) ableitbar → **nicht speichern** |
| (e) Labelseite: smallID, Verbündete, Team, Tick, `res{ok,tile,dt}`, Spielkopf | ~3 B/Sample nach zstd [S] | **< 0,2 GB** | vernachlässigbar | Schnapp-Regeln, Label-Rauschen (nicht ausgeführte Züge), korrekte Beziehungskanäle |

Die Kosten von (b) skalieren mit Kartengrösse × Umkämpftheit, **nicht mit der Sample-Zahl**. Andere Nichtstun-Raten oder mehr Spieler pro Tick verteuern (b) nicht.

Grösste Unsicherheit bei (b): echte Wechsel pro Landkachel (Wiedereroberung, Nukes, Fallout) und echte Bytes pro Wechsel. Die synthetischen Fronten sind sauberer als echte, deshalb setze ich real 1,25–2,5 B an. Genau das misst der Kanarienlauf.

---

## 4 Feldliste für die Neu-Materialisierung JETZT

### A Basis (alle Spiele und Samples, Pflicht)
1. Meta pro Sample, zusätzlich:
   - `tick` = `game.ticks()` beim Emit (int)
   - `sid` = eigene smallID (int)
   - `allies` = smallIDs aller Verbündeten (int[])
   - `team` (int|null)
   - zstd ~3 B/Sample [S]
2. Meta pro **räumlichem** Sample (build_unit, boat, move_warship, spawn): `res = {ok, tile, dt}`. Die Semantik (Schnapp-Regel, was „ok" heisst) bestimmt Unit A.
   - build_unit: neue Einheit des Typs mit `ownerID == sid` innerhalb von ≤ 5 Ticks, deren `pos`.
   - boat: Landeposition des erzeugten Transportschiffs bei Ankunft; `dt` = Ticks bis Ankunft.
   - move_warship: Position bei `reachedTarget`.
   - spawn: `spawnTile()` nach der Spawnphase.
   - Mit verzögertem Nachtragen, weil die Metazeilen ohnehin erst am Spielende geschrieben werden [V].
3. Spielkopf `<gid>.hdr.json`:
   - Kartenname, `gameMapSize`, W, H, `num_land_tiles`, Engine-Commit, Tick des Spawnphasen-Endes
   - Spielertabelle: smallID ↔ clientID ↔ playerID ↔ Team ↔ Typ
   - Versionsnummer des Formats
4. **Spawn-Samples:** Gate für `type == "spawn"` öffnen, nur den **letzten** Spawn-Intent je Mensch (per Vorab-Scan der Record-Züge), `spawn_final = 1`.
   - ~0,5–0,7 Mio Samples, ≤ 7 GB [E]
   - ≤ ~40 Zusatz-Scans pro Partie [E]
5. **Messhaken im Log pro Partie:**
   - Gesamtzeit
   - Summe `gu.tickExecutionDuration`, das ist reine Sim-Zeit; der Callback liefert sie bereits [V im Engine-HEAD]
   - Summe der scanTick-Zeit
   - Anzahl Samples, davon räumlich
   - Bytes von Tier 1 und Tier 2

   Das beweist oder widerlegt §2.2 und liefert die Zahl für die Entscheidungsregel.

### B Tier 1 (alle Spiele, sonst 25-%-Teilmenge; Auswahl `sha1(gid) % 100 < 25`, alle Val-Spiele immer dabei)
6. `<gid>.own.zst`, Besitzwechsel-Log:
   - Quelle: der Tick-Callback `gu.packedTileUpdates` [V im HEAD: Uint32-Paare `(ref, state | terrain << 16)`].
   - Nur die unteren 16 Bit behalten. Nur Einträge schreiben, deren **Besitz- oder Fallout-Bit** sich gegenüber einem Besitz-Spiegel (uint16 W×H, ≤ 20 MB) geändert hat.
   - Frames zu je 512 Ticks. Pro Frame: Anzahl pro Tick (u16), Kachelrefs pro Tick sortiert und delta-kodiert (int32), neuer Zustand (u16). zstd Level 3.
   - Keyframe (voller u16-Zustand) am Ende der Spawnphase und alle 4096 Ticks, jeweils 21–148 KB [S].
   - Ausrichtung: Batch-Schlüssel = `game.ticks()` nach `executeNextTick`. Sample-Zustand = alle Batches mit Schlüssel ≤ `meta.tick`.
   - **Achtung:** Die Record-Commits 88cc95d8 und 8b45be57 fehlen im Engine-Clone von Unit A [V]. Dort kann das Paketformat anders sein, älter vermutlich BigUint64 `ref << 16 | state` [E]. Der Parser muss beide Formate annehmen.
7. `<gid>.units.zst`, Ereignisse aus `gu.updates[Unit]`:
   - Gebäude (City, Port, Factory, DefensePost, MissileSilo, SAMLauncher): Entstehen, Level, Besitzer, Zerstörung, Bau fertig. Felder: Tick, ID, Typ, Besitzer, Kachel, Level.
   - Kriegsschiffe und Transportschiffe: Entstehen, Ende und Besitzer; Position alle 8 Ticks.
   - Nukes: Start, Ziel, Einschlagkacheln (`packedNukeImpacts`).
   - ~0,05–0,3 MB/Spiel, 1–5 GB gesamt [E, nicht gemessen].
8. `<gid>.chk`: CRC32 des vollen Zustandspuffers an 2 zufälligen Sample-Ticks. Der Python-Rekonstruktor muss bit-genau treffen, sonst gilt das Spiel als kaputt. So beweist sich die Messvorrichtung selbst.
9. RAM: Frames sofort streamen, nicht die ganze Partie puffern. Apollo hatte schon Swap-Thrashing [K].

### C Tier 2 (nur wenn Tier 1 nicht für alle Spiele gespeichert wird; nur räumliche Samples)
10. Pyramide aus drei 160×160-Ausschnitten (Besitz-uint16), jeweils in einem zstd-Block:
    - Zentrum = rohe Label-Kachel
    - Schritt 1 (±80 Kacheln), Schritt 2 (±160), Schritt 4 (±320)
    - Randbereiche mit 0 aufgefüllt, Zentrum-Offset gespeichert
11. Gebäudeliste im Schritt-4-Fenster: Typ u8, Besitzer u16, Level u8, dx i16, dy i16. ~0,1–0,2 KB [E].
12. Vier Art-Masken 90×180, bitgepackt: Zelle enthält ≥ 1 Kachel der Art {eigenes Land, eigene Küste, fremde Küste an einem Gewässer, das an die eigene Küste stösst, von der eigenen Küste erreichbares Wasser}.
    - Das sind Existenz-Fakten, keine Engine-Regeln. Legalität nach Regelversion X leitet man später in Python ab.
    - Gewässer-IDs einmal pro Karte vorrechnen.

**Summe:**
- A + B-alle: < 1 GB + ≤ 7 GB + 66–320 GB. Gewählt wird es nur, wenn der Kanarienlauf ≤ 6 MB/Spiel zeigt, also ≤ ~118 GB.
- A + B25 % + C: ~8 + 17–42 (worst 80) + 60–80 GB (27–104) ≈ **85–130 GB**.
- Zusatz-CPU jeweils ~2–5 % ≈ +7–17 Kern-h [E].

### Bewusst NICHT gespeichert
- Volle Momentaufnahmen pro Sample: 0,2–1,5 TB.
- Engine-Legalitätsmasken für alle Typen: regelabhängig, gehört Unit A. Aus A, B und C ableitbar.
- Kandidatenlisten: abgeleitet.
- Handels- und Zugschiffe: nicht zielrelevant, hohe Datenmenge.
- Spieler-Skalarspuren pro Tick: für räumliche Varianten unnötig.
- Ausschnitte für nicht-räumliche Samples.
- Ausschnitte um modellabhängige Zentren: im Materialisierungszeitpunkt unbekannt.
- zstd-19 schon beim Materialisieren: ~10× langsamer, spart nur ~15–20 % [S]; lässt sich offline nachkomprimieren.
- Die 18×90×180-Tensoren bleiben erhalten. Das Training hängt heute an ihnen. Mit Tier 1 für alle Spiele wären sie später in Python ersetzbar, das spart dann ~160 GB.

---

## 5 Rückfallstufe (Platte knapp)
- A (inkl. Spawn und Messhaken) + B nur für 15 % (`sha1 % 100 < 15`, alle Val-Spiele) + C weglassen.
- Zusatz ~10–25 GB, worst case ~50 GB.
- Ermöglicht: alle drei Entwurfsfamilien und beliebige Varianten auf ~2700 Spielen (≈ 1,5 Mio räumliche Samples [E]), dazu das exakte Eval-Set.
- Hochskalieren des Siegers per schlankem Replay: ~8–16 Kern-h [E]. Das gilt nur, wenn die Messhaken aus A5 das bestätigen.

---

## 6 Kennzahl (Q5)

### 6.1 Was an eval_spatial.py für die neue Kennzahl geändert werden muss
Das Urteil über die alten Zahlen gehört Unit A. Für die neue Kennzahl zählt nur:
1. Abstände werden im 1440×720-Raster gemessen und „Kacheln" genannt [V: `grid_xy`]. Das Raster ist je Karte anisotrop: 2000×1000 → 1,39 Kacheln pro Rastereinheit; Korea-Compact 544×1094 → 0,38 × 1,52. Die neue Kennzahl rechnet in **echten** Kacheln aus `mapW`/`mapH`.
2. Nur Argmax. Der Worker **sampelt** aber mit Temperatur 1,3 [K]. Das Einsatzverhalten ist also die Verteilung, nicht der Gipfel.
3. Keine Legalität. Die Messlatte „Zufall über 16200 Zellen" ist deshalb die falsche.
4. Der Val-Split mischt die Verzeichnisliste mit Seed [V: `split_games`]. Ein neuer Pool (12 217 → 18 018) ergibt andere Val-Spiele, alte und neue Zahlen sind dann nicht vergleichbar.
5. Jeder Lauf entpackt Shards über Minuten. Das Cap `per_game=12` ist gut und bleibt.
6. Median statt Anteil ist unter der gemessenen Bimodalität (83,6 % > 32) fast taub. Solange der Nahbereich unter 50 % liegt, sitzt der Median im Fernmodus und bewegt sich kaum, selbst wenn der Nahanteil von 16 % auf 40 % steigt [E, aus der Verteilungsform].

### 6.2 Definition
Für Sample i:
- ℓ_i = Zielkachel: `res.tile`, falls `ok`, sonst die rohe Kachel. Koordinaten (t mod W, t div W).
- q_i(t) = die Kachelverteilung der Policy, legal maskiert, bei Temperatur τ (τ = 1 und Einsatz-τ):
  - Zell-Kopf: Zellmasse gleich verteilt auf die legalen Kacheln der Zelle.
  - Zweistufig: Produkt der beiden Stufen.
  - Zeiger: direkt.
- Jede Variante liefert dafür eine Top-K-Liste (K ≤ 256, ≥ 99 % Masse). Damit ist die Kennzahl entwurfsneutral.

Die Kennzahlen:
- **Primär M@r** = Σ_{‖t−ℓ‖₂ ≤ r} q_i(t), gemittelt je Aktionstyp.
  - r = 16 Kacheln für build_unit, boat, move_warship; r = 32 für Spawn.
  - Die Kurve r ∈ {4, 8, 32, 64} wird gratis mitprotokolliert, entschieden wird auf M@16.
  - r = 16 ≈ eine 90×180-Zelle auf einer 2000er-Karte (11×11 Kacheln); Unit A kann den Radius an die Schnapp-Regel anpassen.
- **Sekundär 1 ΔNLL_legal** = log₂ q_i(ℓ_i) + log₂|L_i|, also Bits gegenüber gleichverteilt-legal.
  - Stetig und proper, reagiert zuerst.
  - Wichtig, weil M@r allein keine properen Anreize setzt: M@r belohnt Zuspitzen auf den Modus. NLL verhindert das.
  - Labels ausserhalb der Maske werden gezählt, nicht gewertet. Das ergibt nebenbei eine Label-Rauschquote.
- **Sekundär 2 Argmax-H@16**: greedy über die Einsatz-Dekodierung und Schnapp-Regel, was Greedy-Spiel tatsächlich trifft.

### 6.3 Messlatten
Exakt werden sie auf dem Eval-Set gerechnet; hier synthetisch [S], zur Grössenordnung.

| Baseline | build_unit | boat |
|---|---|---|
| gleichverteilt-legal, M@16 = \|L ∩ Kreis\|/\|L\| | Median 1,6 % (Europa) / 4,9 % (Welt) / 14,8 % (Korea-Compact); Mittel 3,8–28 % | 0,07–0,9 % |
| gleichverteilt-legal, ΔNLL | 0 Bit (per Definition) | 0 Bit |
| Schwerpunkt-Punkt, H@16 | ≈ 804/\|eigene Kacheln\| bei gleichverteiltem Label; real kleiner, weil Labels an Grenzen und Küsten liegen (alter C3-Wert 2,7 % in ≤ 32 Rastereinheiten) [K] | – |

- Ein unmaskiertes Modell kann negative ΔNLL haben.
- Der M@16-Wert des heutigen Modells ist nicht gemessen. Aus 16,4 % in ≤ 32 Rastereinheiten [K] folgt vermutlich < 10 % [E].

### 6.4 Stichprobengrösse und Takt
Aus `scripts_C/ci_math.py`: 95-%-KI, Designeffekt 2 bei ≤ 12 Samples pro Spiel (ICC ~0,09 [E]).

- ±1 pp bei p = 0,2 braucht 6 147 unabhängige bzw. **12 294** geclusterte Samples.
- ±2 pp braucht 3 074.
- Bei n = 2000 und p = 0,2: **±2,5 pp**. Bei n = 500 und p = 0,1: ±3,7 pp.
- **Gepaarter Vergleich zweier Checkpoints auf demselben Set** ist das eigentliche Werkzeug, 80 % Power:
  - Δ = 2 pp braucht 1 963 Samples bei 10 % diskordanten Samples.
  - Δ = 5 pp braucht 314.
  - M@r ist stetig, die Varianz liegt unter der des Binärtreffers. Die Zahlen sind also konservativ.

Eval-Set:
- **5000 räumliche Samples**, geschichtet: build 2000, boat 2000, move_warship 500, spawn 500.
- Aus Hash-Val-Spielen (`sha1(gid) % 25 == 0`, gleiche Funktion im Trainer).
- ≤ 12 pro Spiel und Typ.
- Warship und Spawn trennen damit nur Unterschiede ≥ ~5 pp.

Eval-Datei: **separat und klein, ~75 MB** [E]. Inhalt pro Sample:
- uint8-Karte, own/opp/config
- Label roh + `res`, W, H
- bitgepacktes Legal-Fenster 96×96 um das Label
- legale Kachelzahl pro 90×180-Zelle (u16, zstd)
- |L|

Sie wird in Python aus Tier 1 gebaut, weil alle Val-Spiele in B liegen, also ohne Engine.

Takt und Kosten:
- Alle 2000 Schritte (≈ 5,3 min bei 800 Samples/s × Batch 128) und bei jedem Snapshot.
- Vorwärtslauf 5k auf der 5080 ~2–4 s [E], weil Training mit Rückwärtslauf 800/s schafft. Kennzahlen < 1 s CPU. Overhead ~1,5 % [E].

Entscheidungsregel: Eine Variante gilt als wirksam, wenn M@16 im gepaarten Vergleich zur Vorgängervariante positiv ist und das KI 0 ausschliesst, bei gleichzeitig ΔNLL ≥ Vorgänger.

Früher Abbruch: Liegt nach ~20k Schritten (~53 min) M@16 nicht signifikant über gleichverteilt-legal, wird der Lauf gestoppt.

---

## 7 Randbedingungen für den Entwurf (an Unit B)
1. Ohne Tier 1 für ein Spiel sind alle Ausschnitte **label-zentriert**: höchstens 128² auf Schritt 1 (±16 Jitter), 256 auf Schritt 2 (±32), 512 auf Schritt 4 (±64). Das trägt Lehrer-Vorgabe-Zweistufer und Soft-Targets bis σ ≈ 25 Kacheln. Ausschnitte um das **vorhergesagte** Zentrum lassen sich in voller Grösse nur mit Tier 1 oder einem schlanken Replay trainieren.
2. Legalität in voller Grösse muss aus den 4 Art-Masken + Gelände + Skalaren (Gold, Einheitenzahlen) ausdrückbar sein. Legalität auf Kachelebene gibt es ausserhalb der Fenster nur mit Tier 1.
3. Kandidaten-Zeiger in voller Grösse: Kandidaten auf Zellebene aus den Masken. Kandidaten auf Kachelebene ausserhalb der Fenster nur mit Tier 1.
4. `attack` hat keine Kachel [V INTENTS.md], dafür ist nichts zu speichern.
5. Spawn ist nur lernbar, wenn A4 umgesetzt wird.
6. Distanzen in Verlust und Kennzahl immer in echten Kacheln (W, H liegen pro Sample vor).

---

## 8 Offene Unsicherheiten
- Echte Wechsel pro Landkachel und Bytes pro Wechsel. Das ist die ganze Tier-1-Spanne 66–320 GB. Auflösung durch den Kanarienlauf mit A5.
- Echte Zahl der räumlichen Samples: 4,5–13 Mio, zentral 10 Mio [E]. Kanarienlauf zählt sie.
- Sim-Tempo in späten Grosspartien und Kosten von `encodeVec`: nicht gemessen. Messhaken A5.
- Paketformat der Tile-Updates an den Record-Commits: nicht prüfbar, Commits nicht im Clone. Die Checksumme B8 fängt Fehler.
- Schnapp-Semantik für `res`: gehört Unit A.
- Freier Plattenplatz auf apollo und arch: unbekannt. Deshalb die Regel und die Rückfallstufe.
- Messlatten in §6.3 sind synthetisch. Die echten Werte liefert das Eval-Set in Sekunden.
- Mac-`materialize.ts` kann hinter arch zurückliegen.
- ICC 0,09 ist angenommen.

---
