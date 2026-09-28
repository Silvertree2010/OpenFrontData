# Einheit A: Befund zur räumlichen Zielwahl (Prämissen und Ursachen)

Stand 2026-09-10. Engine: öffentlicher Klon HEAD `4e88d7816d2d21802ba012c881cb0a55a42aa0a2` (2026-09-09).
Die Aufzeichnungen liefen auf `8b45be575` (alle 65 lokalen Records) und `88cc95d8` (laut Memory der Grossteil
früher Records). Wo HEAD abweichen kann, steht es dabei. Markierung: **[V]** = verifiziert (Code gelesen oder
Skript gelaufen), **[S]** = Schätzung oder Schluss, **[U]** = unverifiziert.

---

## 1 Wichtigste Befunde (zuerst)

**B1. Der Grob-Kopf ist unkonditioniert und lokal. Das ist die Hauptursache, und der User nennt sie nicht.** [V]
`coarse = Conv2d(128,1,1)(stem(map))` (net.py:125, 159). Der Stem besteht aus zwei 3x3-Faltungen (net.py:46-49).
Daraus folgt:
- Rezeptives Feld 5x5 Grobzellen, das sind im Median 50 x 76 echte Kacheln (p5 bis p95 in der Breite 21 bis 89).
- Der Kopf sieht **weder den Kern noch atype, unit_type, Zielspieler, Truppen, Gold oder Spielphase.**
- Eine einzige Hitzekarte bedient vier Aktionstypen und zehn Einheitentypen, deren Legalmengen sich gegenseitig
  ausschliessen: eigenes Land (Bauten), fremde Küste (Boot), Wasser (Kriegsschiff), Feindland (Nukes).
- Der Kopf kann höchstens ein statisches Muster lernen: "Wie oft ist eine Zelle mit diesem lokalen Muster das
  Ziel irgendeiner Raumaktion". Der argmax über 16'200 Zellen trifft nur, wenn das Muster auf der Karte einmalig
  ist. Sonst landet er auf einem gleich aussehenden Muster irgendwo. Das allein erklärt "trifft die Gegend oder
  streut quer über die Karte".
- Der Fein-Kopf sieht zwar den Kern (net.py:167), aber auch der Kern enthält den gewählten atype nicht.

Indiz [S]: Der BUILD-Median von 20,4 Zellen besteht zu 78 % aus Strukturen (siehe 2). Ein Kopf, der nur
**maskiert und gleichverteilt übers eigene Gebiet** zieht, käme auf echter Topologie auf einen Median von
1,0 bis 7,2 Zellen bei 2k bis 20k Kacheln Gebiet und auf 8 bis 17 Zellen bei 100k Kacheln (Abschnitt 3).
Wahrscheinlich ist der Kopf also bei Bauten **schlechter als "irgendwo im eigenen Land"**. Er legt Bauten
vermutlich oft gar nicht ins eigene Gebiet. Nicht verifizierbar, weil die Shards gelöscht sind und die
Gebietsgrösse zum Entscheidungszeitpunkt unbekannt ist. Eine Prüfung würde es klären: der Anteil der
BUILD-Struktur-Argmaxe mit `eigen == 255`.

**B2. Die Prämisse "ein Angriff zielt auf eine Kachel" ist falsch.** [V]
- `AttackIntent` hat nur `targetID` und `troops` (Schemas.ts:608-612; INTENTS.md aus 293k echten Zügen).
- Die Engine startet den Angriff ohne Quellkachel (ExecutionManager.ts:60-66, `sourceTile=null`) und greift
  entlang **der ganzen eigenen Grenze** zum Ziel an (AttackExecution.ts:198).
- Angriffe sind 61,9 % aller Züge und werden vom Kachel-Kopf nie supervidiert (actions.py:260).
- Die Beispiele in H2 ("Feindkacheln an meiner Grenze") und H3 ("links oder rechts angreifen") betreffen den
  Kachel-Kopf deshalb gar nicht.
- Supervidiert werden nur BUILD_UNIT, BOAT, SPAWN und MOVE_WARSHIP (actions.py:261-269).

**B3. Die Etiketten sind rohe Klicks, und die Engine rastet sie ein.** [V an HEAD]
- Der Client schickt die Mauskachel, nicht die eingerastete Kachel (BuildPreviewController.ts:546/558,
  BuildMenu.ts:400, PlayerActionHandler.ts:37, WarshipSelectionController.ts:190).
- Die Engine macht daraus:
  - Bauwerk: nächste gültige eigene Kachel im Radius 15, mit Abstand 15 zu jedem Gebäude.
  - Boot: nächste per Wasser erreichbare Küstenkachel **des Besitzers der Klickkachel**, innerhalb von
    Manhattan-Distanz 50.
  - Kriegsschiff: patrouilliert zufällig im Kasten ±50 um den Klick.
  - MIRV: nur der Zielspieler zählt.
  - SPAWN nach der Spawnphase: No-op.
- Ein grosser Teil der Etikettvarianz unterhalb von etwa 15 bis 50 Kacheln ist also simulationsirrelevant.
  Exakte CE und die Exakttrefferquote bestrafen Rauschen.

**B4. Das Raster ist nicht die Karte.** [V]
- `tile_encode` normiert jede Karte auf 1440x720 (actions.py:169-175).
- Echte Karten sind 256 bis 6000 Kacheln breit und meist fast quadratisch. Compact nutzt `map4x`
  (TerrainMapLoader.ts:82-85).
- Grobzelle im Median **10 x 15 Kacheln** (Breite p5 bis p95: 4,2 bis 17,8, Höhe 6,7 bis 25,3).
  Seitenverhältnis Breite/Höhe im Median 0,65.
- 35 % der Spiele haben W < 1440. Dort überauflöst das Feinraster, und einzelne Feinklassen kommen nie vor.
- "Eine Grobzelle sind 8 Kacheln" stimmt nicht. Alle "Kacheln" in eval_spatial sind Rastereinheiten, im Median
  1,25 x 1,91 echte Kacheln und anisotrop. "> 32 Kacheln" heisst auf der Median-Karte etwa 40 bis 61 echte
  Kacheln.

**B5. Die alten Messungen sind teils anders zu lesen.** [V] Details in Abschnitt 4.
- Die Baselines C3/C4 sind durch einen Quantisierungs-Offset verfälscht.
- Die Raumzahlen stammen aus Epoche 0 (Schritt 47'000 bzw. 60'000), nicht aus dem Stand "nach zwei Epochen".
- Die Kopf-Trefferquoten (3,5 % / 1,8 %) stammen aus nur etwa fünf Val-Partien.
- "Fein schlechter als Konstante" ist genau das, was der argmax eines informationslosen Kopfs analytisch
  liefert. Es ist kein Zeichen von "Schaden".

---

## 2 Engine-Semantik je räumlichem Intent

Die Anteile stammen aus 65 lokalen Records (`8b45be575`, 66'982 räumliche Intents ohne Spawn,
scripts_A/label_composition.py). Sie sind ein Proxy für den 18k-Pool [S].

| Typ | Anteil räuml. Etiketten | Was die Engine mit der Kachel macht | Effektive Auflösung | file:line (HEAD) |
|---|---|---|---|---|
| City | 16,9 % | Klickkachel muss **eigen** sein. Sonst Abbruch. Dann eigene Kacheln im Radius 15 (4er-Flutung), kein Gebäude jeglichen Typs näher als 15. Nimmt die **nächste** gültige Kachel. | Region im eigenen Land. Einrasten ±15 Kacheln. Wirkung: Kappe + Verwundbarkeit + Bahn. | ConstructionExecution.ts:59; PlayerImpl.ts:1696-1778 (Eigentum 1708, r=15 1711, Abstand 1760, sortiert 1772); Config.ts:1108 (structureMinDist 15) |
| Defense Post | 10,8 % | wie City | ±15 Einrasten. Wirkradius 30. | Config.ts:350 |
| Port | 8,1 % | Eigene Küstenkachel innerhalb Manhattan 20 vom Klick, zusätzlich Strukturregeln | nächste eigene Küste, ±20 | PlayerImpl.ts:1650-1671; Config.ts:896 |
| Factory | 6,8 % | wie City | ±15 | wie City |
| SAM Launcher | 3,2 % | wie City | ±15. Reichweite 70 (L1) bis 150. | Config.ts:1057-1063 |
| Missile Silo | 2,7 % | wie City | ±15. Ort praktisch nur Flugzeit und Deckung. | wie City |
| Warship (bauen) | 7,0 % | Klick muss Wasser sein. Spawnt am **nächsten eigenen Hafen** derselben Wasserkomponente. Klick = Patrouillenpunkt. | Patrouille zufällig ±50 um den Klick. Zielerfassung 130. → ±50 Kacheln egal. | PlayerImpl.ts:1673-1690; ConstructionExecution.ts:128-131; WarshipExecution.ts:757-782; Config.ts:1116/1120 |
| Move Warship | 2,1 % | Klick in derselben Wasserkomponente wie das Schiff, sonst still ignoriert. Wird Patrouillenpunkt. | ±50 | MoveWarshipExecution.ts:17-40 |
| Atom Bomb | 5,4 % | Exakte Kachel ist das Detonationszentrum. Nur unpassierbares Terrain und Teamkollegen (Team-Modus) sind verboten. Silo = nächstes bereites. **Keine Reichweitengrenze.** | innen 12 / aussen 30 Kacheln. Wenige Kacheln zählen nur an Gebäuderändern und beim Allianzbruch. | PlayerImpl.ts:1600-1648; NukeExecution.ts:63-122; Config.ts:1016-1027 |
| Hydrogen Bomb | 1,5 % | wie Atom | innen 80 / aussen 100 → ±10 Kacheln fast egal | wie oben |
| MIRV | 0,08 % | Kachel muss Besitzer haben. Die Sprengköpfe verteilen sich zufällig im Radius 1500 **auf dem Land des Zielspielers**. | **nur der Zielspieler zählt** | PlayerImpl.ts:1566-1570; MIRVExecution.ts:67, 236-262 |
| Boat | 35,4 % | Ziel = **Besitzer der Klickkachel** (nicht self, angreifbar). Landung = nächste Küstenkachel dieses Besitzers, erreichbar über eine eigene Wasserkomponente, innerhalb Manhattan 50. Dort `conquer` + Angriff ab der Landekachel. | Identität von Zielspieler und Landeküstenkachel. Alle Klicks mit derselben nächsten Küste sind gleichwertig. | TransportShipExecution.ts:64, 109, 119, 261-272; TransportShipUtils.ts:5-42; SpatialQuery.ts:126-155 (bfsNearest 44-75) |
| Spawn | (1,5 % der Eval-Samples) | Materialisiert werden nur Intents **nach** der Spawnphase (materialize.ts:197, extract.ts:125). Die Engine verwirft sie an HEAD (`fromIntent && !queuedDuringSpawnPhase`). Bei Zufallsspawn gibt es gar keine Spawn-Intents. | **sim-irrelevant (No-op)** [V an HEAD, U an 8b45be575] | SpawnExecution.ts:74-81; Config.ts:774-782 (150/300 Züge) |
| Attack | 0 % (kein Kachelfeld) | Keine Kachel. Angriff über die ganze Grenze zum Ziel. | – | Schemas.ts:608-612; AttackExecution.ts:198 |

Summen: Strukturen 48,5 %, Boot 35,4 %, Kriegsschiff 9,1 %, Nukes 7,0 %. Spawn-Intents: nur 98 von 8'194
(1,2 %) liegen nach Zug 300 (scripts_A/spawn_and_territory.py). SPAWN ist also dünn **und** ein No-op.

**Laut an den Collector: simulationsirrelevante Etiketten auf Kachelebene.**
- SPAWN: komplett (No-op).
- MIRV: die Kachel ausser dem Besitzer.
- Kriegsschiff: alles innerhalb von ±50 Kacheln. Das sind etwa 3 bis 5 Grobzellen auf der Median-Karte.
- Strukturen: die Klickposition innerhalb von ~15 Kacheln, weil die Engine auf die nächste gültige Kachel
  einrastet.

Damit ist die **Feinebene für etwa 57 % der Etiketten** (Strukturen + Kriegsschiffe) weitgehend Rauschen.
Relevant auf etwa 5 Kacheln genau ist sie nur bei Atombombe und Bootslandung (~40 %).

Versionsvorbehalt:
- MECHANIK.md (geprüft an `8b45be575`) bestätigt Gebäudeabstand 15, die Nuke-Radien, die SAM-Formel und
  `findExistingUnitToUpgrade`. In der alten Version konnte ein Bauklick innerhalb von 15 Kacheln eines
  gleichartigen Gebäudes still zur Aufwertung werden [U, ob engine- oder clientseitig].
- Boot-Einrastung (`closestReachableShore`, maxDist 50) und das Spawn-Sicherheitstor sind an HEAD gelesen, an
  den Record-Commits **nicht** geprüft [U].

---

## 3 Legalmengen (echte Kartendateien an HEAD, scripts_A/legal_sets.py)

Das eigene Gebiet ist als kompakter BFS-Klumpen simuliert, 3 Seeds, Median. Echte Gebiete sind weniger kompakt
und belegen deshalb eher **mehr** Zellen [S]. Proxy für Gebietsgrössen (Endstand, Public-Spiele, index.sqlite):
Median 11k (Normal) bzw. 17k (Compact), p75 50k bzw. 68k, p90 etwa 195k Kacheln.

| Karte | Zelle (Kacheln) | Gebiet | Struktur-legale Zellen | Hafen-legal | Boot-Landeküsten-Zellen | Boot-Klick-Zellen (≤50 K.) | Maske + gleichverteilt: Struktur-Median / P(exakt) |
|---|---|---|---|---|---|---|---|
| World Normal | 11,1x11,1 | 2k / 20k / 100k | 24 / 211 / 955 | 12 / 186 / 690 | ~2'000 bis 2'257 | 10'554 bis 11'440 | 2,2 / 5,3 % · 7,2 / 0,6 % · 16,3 / 0,2 % |
| World Compact | 5,6x5,6 | 2k / 20k / 100k | 83 / 679 / 915 | 39 / 571 / 813 | 1'770 bis 2'072 | 12'487 bis 14'013 | 4,2 · 26,2 (Seed-Rauschen) · 16,3 Zellen |
| Europe Normal | 16,1x18,6 | 2k / 20k / 100k | 15 / 95 / 398 | 0 / 78 / 352 | ~3'800 bis 3'915 | ~11'450 | 1,4 / 11 % · 4,5 / 1,4 % · 9,8 / 0,3 % |
| Branching Paths | 13,8x23,9 | 2k / 20k / 100k | 14 / 87 / 368 | – | ~2'840 | ~6'950 | 1,4 · 4,2 · 10,4 |
| Korea | 6,0x24,3 | 2k / 20k / 100k | 27 / 176 / 789 | – | ~1'500 | ~8'700 | 2,2 · 7,1 · 17,3 |
| Giant World Map | 22,8x21,6 | 2k / 20k / 100k | 10 / 59 / 237 | – | ~3'470 | ~10'050 | 1,0 / 17 % · 3,2 / 2,3 % · 8,1 / 0,4 % |

Weitere Legalmengen:
- Nukes (Atom/H): alle nicht unpassierbaren Zellen, 8'190 bis 16'200.
- MIRV: jede besessene Zelle, im Mittelspiel also fast alles Land (6'000 bis 15'600 Land-Zellen).
- Kriegsschiff: Ozeanzellen, 3'313 bis 13'458.
- Boot mit "Maske + gleichverteilt über Landeküste": Median 51 bis 66 Zellen, P(exakt) < 0,1 %. Die Maske
  allein ist hier nutzlos.

**Folgerung.** Die Maskierung schrumpft die Wahl **nur bei Strukturen** (48,5 % der Etiketten) auf ein paar Dutzend
bis ein paar Hundert Zellen, bei grossen Gebieten oder Compact-Karten auf bis zu etwa 1'000. Bei Boot (35 %),
Nukes (7 %) und Kriegsschiff (9 %) bleiben Tausende Zellen. Beim Boot entscheidet der **Zielspieler** (Besitzer
der Klickkachel), und den kann eine Maske nicht wählen.

---

## 4 Gültigkeit von eval_spatial.py und der alten Zahlen

Was es misst [V]:
- Val-Split identisch zu bc_fit (`list_games` + `split_games`, seed 1, val 4 %). Nur Samples mit Grob- und
  Fein-Etikett, höchstens 12 pro Spiel (`--per-game 12`).
- Typen: BUILD_UNIT (alle zehn Einheitentypen), BOAT, SPAWN, MOVE_WARSHIP.
- Abstand: euklidisch in Grobzellen; "Kacheln" sind Grobabstand x 8, also Rastereinheiten (eval_spatial.py:214).
- B2 bedingt den Fein-Kopf auf die echte Grobzelle. C5/C6 laufen auf denselben Samples.

Probleme:
1. **C3/C4-Baselines falsch** [V] (scripts_A/baseline_quant_bug.py).
   - materialize.ts:154 quantisiert 0 auf 128, dataset.py:36 dequantisiert das zu **+0,00392**. `clamp(min=0)`
     (eval_spatial.py:356) behält den Wert.
   - Jede Nicht-eigen-Zelle zieht deshalb den "Schwerpunkt" zur Kartenmitte. Die eigenen Zellen tragen bei 20
     Zellen nur 24 % des Gewichts, bei 100 Zellen 61 %, bei 1'000 Zellen 94 %.
   - C4 ("stärkste eigene Kachel"): alle eigenen Zellen sind exakt 1,0, und `argmax` liefert den ersten
     Index, also die **oberste und dann linkeste eigene Zelle**.
   - Die Leer-Prüfung (`tot <= 1e-6`) greift nie. Die Abdeckung ist immer 100 %.
   - **Die Aussage "Kopf schlägt Schwerpunkt eigenes Gebiet" (27,8 gegen 30,5) ist damit nicht belegt.**
2. **Fein "schlechter als Konstante" ist ein argmax-Artefakt** [V] (scripts_A/fine_baselines.py).
   - Bei gleichverteiltem Feinetikett gilt analytisch: Zufall 4,14 (gemessen 4,1), Zellmitte (4,4) 3,09
     (gemessen 3,1), Randzellen 4,37 bis 4,56, randnahe Eckzellen 4,93, Ecke 5,47.
   - Die gemessenen 4,8 entsprechen einem argmax, der auf periphere Zellen fällt.
   - Fein-Top-1 liegt bei 1,8 %, Zufall bei 1/64 = 1,56 %.
   - Die Feinetiketten verhalten sich im Mittelwert **wie gleichverteilt**: keine Konzentration zur Mitte.
   - Der Kopf ist informationslos, nicht "aktiv schädlich". Die richtige Konstante ist die Mitte.
3. **Checkpoint-Mix** [V].
   - Die Raumtabelle stammt von Schritt 47'000 (Memory) bzw. 60'000 (bc_train.py:38), also vor dem Ende von
     Epoche 1 (etwa 74,6k Schritte pro Epoche = 149'194 / 2).
   - Die Kopf-Treffertabelle ist "nach zwei Epochen".
   - Ob weitere Epochen das Zielen verbessern, ist ungemessen (Handoff, offener Punkt 1).
4. **Kopf-Treffertabelle aus etwa fünf Partien** [V Code, S Anzahl].
   - `bc_fit.evaluate` nimmt 60 Batches x 128 mit `shuffle_buf=1` in Spielreihenfolge. Das sind 7'680
     aufeinanderfolgende Samples, bei ~1'600 Samples pro nichtleerer Partie also etwa 5 Partien.
   - Fein ist dort lehrerbedingt (bc_fit.py:178). Coarse 3,5 % gegen 2,69 % ist durch diese Stichprobe
     erklärbar.
5. **Stichprobenzahl passt nicht zu den Standardwerten** [S].
   - 488 Val-Spiele, davon ~36 % nichtleer (64 % leere Shards laut Handoff), ergeben etwa 176 Spiele mal 12,
     also höchstens ~2'112 Samples. Gemessen wurden 4'122.
   - Also lief die Messung mit anderem `--per-game`, auf anderen Shards, oder die Leerquote im Val weicht ab.
   - "488 ungesehene Partien" überschätzt die Zahl der beitragenden Partien. `meta.games_used` im gelöschten
     JSON hätte es gezeigt. SPAWN mit n=60 ist statistisch wertlos.
6. Leck-Risiko [U, gering]: Der Split hängt an der vollständigen sortierten Spieleliste. Hätte sich der Pool
   zwischen Trainingsstart und Eval geändert, wären Val-Spiele trainiert worden. Der Selbsttest prüft das nicht.
7. **Einheiten** [V]. Abstände sind anisotrop und kartenabhängig (siehe B4). Die Aufschlüsselung nach Typ mischt
   Karten mit 4 bis 25 Kacheln pro Zelle.
8. **"Bimodal" ist teils Bin-Layout** [V] (scripts_A/logbin_artifact.py).
   - Grobabstände x 8 können nur 0, 8, 11,3, 16 und so weiter sein. Die Bins "1", "2" und "3-4" sind darum
     **per Konstruktion leer**, und der offene Bin ">32" schluckt alles ab 4 Zellen.
   - Eine **unimodale** Gauss-Verteilung mit dem gemessenen Median (136 Rastereinheiten) ergäbe 96,4 % in
     ">32" und 0,1 % exakt.
   - Die gemessene Verteilung passt zu einer Mischung aus etwa 10 % informativen Fällen und 90 % breiter Streuung.
9. **BUILD-Median 20,4 ist fast nur Strukturen** [V Anteile]: 78 % Strukturen, 11 % Nukes, 11 % Kriegsschiff-Bau.
   Der Wert ist nicht nuke-dominiert.

Die Zahlen "Median-Rang 260" und "Top-k" sind korrekt berechnet (Rang gegen echte Logits, eval_spatial.py:349).
Ihre Deutung steht in Abschnitt 5.

---

## 5 Ursachen-Ranking (Wirkung auf die gemessenen Zahlen)

1. **Unkonditionierte, lokale Hitzekarte (B1). Hoch.** Erklärt Median 17 Zellen, 83,6 % weit, BUILD 20,4 und
   MOVE_WARSHIP 20,6. Beim Kriegsschiff ist das Ziel Wasser, die Karte wird aber von Bau- und Boot-Mustern
   dominiert (48,5 % und 35,4 % der Etiketten). Median-Rang 260 passt zur Grösse einer "plausibel aussehenden"
   Menge von Zellen (Struktur-Legalmengen 60 bis 955, Küstenzellen 1'450 bis 3'900) [S]. Rein architektonisch
   verifiziert.
2. **Etikett- und Metrik-Semantik (B3, B4, SPAWN-No-op). Mittel für die Metrik, niedrig für die Mediandistanz.**
   - Menschliche Klickstreuung innerhalb des Einrast-Radius (15 Kacheln, das sind 1 bis 1,5 Zellen) deckelt die
     erreichbare Exakttrefferquote auch für eine perfekte Politik.
   - Die Rastereinheiten verzerren alle Distanzen um den Faktor 1,25 bis 1,9 und anisotrop.
3. **Skaleninkonsistenz der Beobachtung. Niedrig bis mittel.** Dieselbe 3x3-Faltung sieht Muster auf 4 bis 25
   Kacheln pro Zelle, also Grenzen, Küsten und Gebäude in wechselnder Grösse [S].
4. **Fehlende Maske (H2). Mittel, aber erst nach Konditionierung und nur für Strukturen.**
5. **Mehrdeutigkeit (H3). Unbekannt, von Punkt 1 nicht trennbar.**
6. **Kein Abstand im Verlust (H4). Niedrig für die Schlagzeilenzahlen.**
7. **Auflösungsbruch Fein (H1). Vernachlässigbar für die Schlagzeilenzahlen.** Die Fein-Ebene trägt ~3 von
   136 Rastereinheiten Medianfehler bei, also ~2 %.

Geprüft und **ausgeschlossen**:
- Verlustgewicht [V]: Jeder Kopf hat Gewicht 1 (bc_train.py:175-193). Coarse hat mit ln 16'200 = 9,69 den
  grössten CE-Massstab und ist also nicht untergewichtet.
- Raumanteil pro Batch [V/S]: 24,1 % der Samples vor Angriffs-Ausdünnung (THIN=30 erhöht den Anteil noch),
  also 31 oder mehr pro Batch von 128.
- Ausrichtung Obs zu Label [V]: beide floor(y·90/H) bzw. floor(x·180/W) (obs.ts:197, actions.py:171-173).
- Kanalnormierung [V]: [-1,1].
- Teacher Forcing beim Fein-Kopf [V]: Standard.
- Die GroupNorm-Mittelung über die ganze Karte gibt nur ein schwaches globales Signal, keine Position.

---

## 6 Urteile H1 bis H4

**H1 Auflösungsbruch: RICHTIG im Mechanismus, IRRELEVANT für die Schlagzeilenzahlen.**
- Pro Zelle gibt es nur Aggregate: Landanteil aus ~4x4 Stichproben, Mehrheitsbesitzer, Anteile, Gebäude pro
  Zelle gestempelt (obs.ts:151-172, 196-208). Die Subzellposition fehlt also praktisch vollständig.
- Die Feinetiketten verhalten sich wie gleichverteilt (C5/C6 analytisch reproduziert). Die ~6 Bit
  Subzellinformation sind aus der Beobachtung nicht ableitbar.
- Aber: Die Feinebene macht ~2 % des Fehlers aus, und für ~57 % der Etiketten (Strukturen, Kriegsschiff, dazu
  MIRV und Spawn) ist die Subzellposition simulationsirrelevant, weil die Engine auf ±15 bzw. ±50 Kacheln
  einrastet.
- "Schlechter als Konstante" ist der argmax eines informationslosen Kopfs, kein Beweis für H1.

**H2 Keine Maskierung: TEILWEISE.**
- Richtig für Strukturen (48,5 %): eigenes Land ergibt 10 bis 955 Zellen statt 16'200.
- Falsch für Boot, Nuke und Kriegsschiff (51,5 %): 1'450 bis 16'200 Zellen.
- Zwei Punkte des Users stimmen nicht: Angriffe haben gar keine Kachel (B2), und "ein paar hundert legal" gilt
  nur für kleine bis mittlere Gebiete.
- Entscheidend: Eine Maske setzt Konditionierung auf atype und unit_type voraus. Beim heutigen Kopf gäbe es
  nicht einmal eine definierte Maske pro Sample.

**H3 Mehrdeutigkeit: im Mechanismus FALSCH, als Erklärung UNBELEGT.**
- Kategoriale CE mittelt keine Positionen. Sie verteilt Wahrscheinlichkeit auf die Modi, und der argmax fällt
  auf einen der Modi, **nie auf die Mitte**. "Die Mitte ist eine dritte Kachel" gilt nur für Regression auf
  Koordinaten oder für Erwartungswert-Verluste.
- Das Muster "nah oder weit" entsteht genauso aus B1, wenn gleich aussehende Muster verschiedener Aktionstypen
  sich über die Karte verteilen. Dazu kommt das Bin-Artefakt (Abschnitt 4, Punkt 8).
- Echte Mehrdeutigkeit (mehrere gute Bauplätze) gibt es sicher, ist aber mit diesen Zahlen nicht nachweisbar.

**H4 Kein Abstandsbegriff: TEILWEISE, sekundär.**
- Die Prämisse stimmt: CE ist geometrieblind.
- Aber die CE lernt trotzdem eine Rangordnung (Median-Rang 260 statt 8'100). Der Grossteil des Fehlers
  (Median 17 Zellen) liegt weit ausserhalb jeder sinnvollen Kernbreite.
- Ein weiches Ziel ist trotzdem **angebracht**, und zwar als Entrauschung passend zur effektiven Auflösung aus
  Abschnitt 2: Strukturen ~15 Kacheln, Kriegsschiff ~50, Atombombe ~5 bis 10, Boot entlang der Küste. Die Breite
  muss **in Kacheln und pro Typ** gelten, nicht in Zellen (Zellen sind 4 bis 25 Kacheln).
- Ein Erwartungs-Abstands-Term würde dagegen genau die Mitte-Falle aus H3 erzeugen.
- Erwarteter Nutzen: Verschiebung innerhalb der Bins ≤ 2 Zellen, nicht bei den 83,6 %.

---

## 7 Randbedingungen für B (Darstellung) und C (Speicher/Metrik)

- **Konditionierung zuerst.** Die Zielverteilung muss vom gewählten atype und unit_type abhängen, beim Boot
  zusätzlich vom Zielspieler (der Besitzer der Zielkachel **ist** das Angriffsziel). Reihenfolge:
  atype → unit_type → (Ziel) → Ort. Ohne das wirken Maske und Verlust auf eine Mischung.
- **Engine-exakte Legalmasken** (an HEAD, Record-Commits prüfen):
  - Strukturen: eigene Kachel (PlayerImpl.ts:1708). Hafen: eigene Küste ≤ 20.
  - Warship-Bau: Wasser in der Komponente eines eigenen aktiven Hafens.
  - Move-Warship: dieselbe Wasserkomponente wie das Schiff.
  - Boot: Kachel mit fremdem oder neutralem, angreifbarem Besitzer, der eine erreichbare Küste innerhalb von
    Manhattan 50 hat.
  - Atom/H: nicht unpassierbar, nicht Teamkollege, bereites Silo nötig. MIRV: besessene Kachel.
  - SPAWN nach der Spawnphase: **aus den Daten nehmen** (No-op).
- **Legalmengengrössen** (Abschnitt 3):
  - Strukturen 10 bis ~1'000 Zellen, abhängig von Gebiet (Median-Endstand 11k bis 17k Kacheln) und Karte.
  - Boot-Landeküste 1'450 bis 3'900 Zellen. Nukes und Kriegsschiff Tausende.
  - Eine Kandidatenliste legaler **Zellen** ist bei Strukturen klein. Bei Boot und Nukes braucht es zuerst die
    Spielerwahl.
- **Nötige Präzision:**
  - Unter etwa 8 bis 15 Kacheln bringt Genauigkeit bei Strukturen, Kriegsschiff, MIRV und Spawn nichts.
  - Etwa 5 Kacheln sind höchstens bei Atombombe (aussen 30) und Bootslandung sinnvoll.
  - 1-Kachel-Präzision ist überall Überauflösung.
- **Einheiten:**
  - Alles in echten Kacheln und isotrop definieren. Die Grobzelle ist heute 4 bis 25 Kacheln breit, im
    Median 10 x 15, Seitenverhältnis 0,65.
  - 35 % der Spiele haben W < 1440 (unerreichbare Feinklassen).
  - Kernbreiten und Crop-Grössen in Kacheln angeben.
- **Kontext:** Das heutige rezeptive Feld sind 5 Zellen, also 21 bis 89 Kacheln (p5 bis p95). Die
  Struktur-Legalmenge reicht bei 100k Kacheln über bis zu ~30 Zellen Radius. Der Ortskopf braucht globalen
  Kontext bzw. den Kern.
- **Für C: Speicherbedarf.** Pro Etikett die rohe Klickkachel plus W und H (vorhanden), zusätzlich:
  - die **engine-aufgelöste Effektivkachel** (Ergebnis von canBuild / targetTransportTile / Patrouillenpunkt),
  - den **Besitzer der Zielkachel** (Boot, Nuke, MIRV).
  Beides ist beim Replay billig abfragbar. Das Gelände lässt sich aus den Kartendateien regenerieren.
- **Für C: Metrik.**
  - "Trifft dieselbe Effektivkachel bzw. liegt im Wirkradius des Typs" statt Exakttreffer. Beim Boot zusätzlich
    "gleicher Zielspieler".
  - Baselines "Maske + gleichverteilt" und "korrekter Gebietsschwerpunkt" auf dem rohen uint8 (`eigen == 255`)
    rechnen, nicht auf dem dequantisierten Kanal.
  - Val-Stichprobe über viele Partien streuen (nicht die ersten 60 Batches).

---

## 8 Offene Unsicherheiten

- **Engine-Versionen:** Boot-Einrastung, Spawn-Tor, Upgrade-Umwandlung und Kartendateien an `8b45be575` und
  `88cc95d8` sind nicht geprüft. Die Legalmengen stammen aus HEAD-Karten. Karten wurden über Versionen teils
  neu skaliert [U].
- **Gebietsgrösse zum Entscheidungszeitpunkt** in den Eval-Samples ist unbekannt. Damit ist "Kopf schlechter
  als Maske + gleichverteilt bei Bauten" nur eine Schätzung.
- **Eval-JSON gelöscht:** `games_used`, Anzahl pro Typ und der Widerspruch 4'122 zu 488 Spielen sind nicht
  auflösbar.
- **Anteile aus 65 Records**, einem Commit und Aufzeichnungen mit hohem Elo. Der 18k-Pool kann abweichen, zum
  Beispiel mehr Nukes bei höherem Elo (ELO_ANALYSIS).
- **Legalmengen** kommen aus kompakten BFS-Klumpen mit 3 Seeds. Ausreisser wie "World Compact 20k: 26,2" sind
  Seed-Rauschen durch langgezogene Kontinente.
- Wie viel echte menschliche Mehrdeutigkeit es bei Bauplätzen gibt, ist ohne Replay-Daten nicht messbar.
- Der Inhalt der v2-Varianten auf arch (`bc_fit.py.v2` usw.) ist nicht gesehen. Die lokale bc_train.py enthält
  bereits COARSE_SIGMA und FINE_OFF (Sigma in Zellen, siehe Einheiten-Punkt in Abschnitt 7).

---
