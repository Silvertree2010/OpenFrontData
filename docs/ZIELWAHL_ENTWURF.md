# Räumliche Zielwahl: konsolidierter Entwurf

Stand 2026-09-10. Engine-Klon öffentlich, HEAD `4e88d78` (2026-09-09). Die Aufzeichnungen liefen auf älteren Commits (lokal `8b45be575`), und die vendored Engine des Materializers liegt nicht auf dem Mac.

Markierungen:
- **verifiziert (Quelle)**: Code gelesen oder Skript gelaufen.
- **Schätzung**: hergeleitet, nicht gemessen.
- **Setzung**: bewusste Wahl ohne Messgrundlage.

Die Skripte liegen unter `amssp/scripts_A|B|C|Collector/`.

---

## 0 Kernaussage

1. **Hauptursache, von dir nicht genannt:** Der Grob-Kopf ist eine unbedingte 1x1-Faltung auf einem Stem aus zwei 3x3-Faltungen. Er sieht weder atype noch unit_type, Zielspieler, Gold oder Spielphase, und sein Sichtfeld ist 5x5 Zellen (Median rund 50 Kacheln breit). Eine einzige Hitzekarte bedient Stadt, Boot, Kriegsschiff und Atombombe, deren legale Mengen sich gegenseitig ausschliessen. Das allein erklärt "trifft die Gegend oder streut quer über die Karte".
2. **Entwurf D0:** Zuerst das Ziel (atype, dann unit_type, dann Zielspieler). Danach ein darauf bedingter, legalitätsmaskierter, **einstufiger** Zell-Zeiger auf dem bestehenden 180x90-Raster mit globalem Kontext. Die Kachel innerhalb der Zelle legen eine deterministische Regel und das Einrasten der Engine fest. Gelernt wird mit Kreuzentropie auf die **engine-aufgelöste** Kachel, mit weichem Ziel σ = R/2 in echten Kacheln.
3. **Jetzt in die Neu-Materialisierung**, ohne die Beobachtung zu ändern:
   - pro räumlichem Sample `res_tile`, `res_kind`, `dst_owner`
   - pro Sample `tick`, `sid`, `allies`, `team`
   - Spielkopf und Zeitmessung pro Stufe
   - das Besitzwechsel-Log (Tier 1)
   - Zellfakten auf 180x90 (`owner_major`, `own_frac`, 8 Legalitätsbits)
   - Spawn-Samples aus der Spawnphase
4. **Kanarienlauf vorher:** 50 Records. Er entscheidet anhand von Byte- und Zeitmessungen, ob Tier 1 für alle Partien oder für 25 % gespeichert wird (Regel in §7).

---

## 1 Wichtigere Ursache und Fehler in den Prämissen

### 1.1 Hauptursache: unbedingte, lokale Hitzekarte

Verifiziert (net.py:46-49, 125, 159; Collector-Nachprüfung).
- `coarse = Conv2d(128,1,1)(stem(map))`. Die Hitzekarte hängt nur am lokalen Kartenmuster.
- Rezeptives Feld 5x5 Grobzellen: im Median 50 Kacheln breit, p5 bis p95 21 bis 89 (verifiziert, scripts_A/map_scale.py, Collector-Lauf).
- Der Kopf kann nur lernen: "Wie oft ist eine Zelle mit diesem lokalen Muster das Ziel *irgendeiner* Raumaktion." Der argmax über 16'200 Zellen trifft nur, wenn das Muster auf der Karte einmalig ist.
- Nukes liegen im Median 701 Kacheln vom eigenen Spawn entfernt, Städte 83 (verifiziert, scripts_B/records_spatial.out, 65 Records). Eine unbedingte Karte muss beide Gegenden zugleich hoch bewerten.
- Der Fein-Kopf sieht den Kern, aber auch der Kern enthält den gewählten atype nicht.
- Median-Rang 260 passt zu "hat gelernt, wo überhaupt etwas Legales liegt, aber nicht die Wahl darin" (Schätzung: Struktur-Legalmengen 60 bis 955 Zellen, Küstenzellen 1'450 bis 3'900).

Gegenprüfungen, alle ausgeschlossen (verifiziert, A):
- Verlustgewicht: jeder Kopf Gewicht 1, und coarse hat mit ln 16'200 = 9,69 den grössten CE-Massstab.
- Ausrichtung zwischen Beobachtung und Label (obs.ts:197, actions.py:171-173).
- Kanalnormierung, Teacher Forcing.
- Raumanteil pro Batch (siehe §6).

### 1.2 Falsche oder ungenaue Prämissen im Auftrag

| Prämisse | Befund | Quelle |
|---|---|---|
| "Ein Angriff zielt auf eine Kachel" | **Falsch.** `AttackIntent` hat nur `targetID` und `troops`. Die Engine greift ohne Quellkachel über die ganze Grenze an. Angriffe sind 61,9 % der Züge und werden vom Kachel-Kopf nie supervidiert. Supervidiert werden nur BUILD_UNIT, BOAT, SPAWN, MOVE_WARSHIP. | verifiziert (Schemas.ts:608-612, AttackExecution.ts:198, actions.py HEAD_SCHEMA) |
| "Kachelraster 1440x720" | Das ist ein Normierungsraster, keine Karte. Echte Karten sind 256 bis 6000 Kacheln breit. Bei 35,4 % der Partien ist W < 1440, dort kommen Feinklassen nie vor. | verifiziert (actions.py:169-175, map_scale.py) |
| "Eine Grobzelle sind 8 Kacheln" | **Falsch.** 8x8 Rastereinheiten sind im Median **10 x 15 echte Kacheln** (nach Partien gewichtet, 8'648 öffentliche Partien), anisotrop (Breite/Höhe 0,65). Alle "Kacheln" in eval_spatial sind Rastereinheiten von 1,25 x 1,91 Kacheln. "> 32 Kacheln" heisst real etwa 40 bis 61 Kacheln. | verifiziert (map_scale.py, eval_spatial.py:163) |
| Etiketten = Zielkacheln | Es sind **rohe Mausklicks**, die Engine rastet sie ein. Bauwerk: nächste gültige eigene Kachel im Radius 15, Abstand 15 zu Gebäuden. Hafen: eigene Küste im Manhattan-Radius 20. Boot: nächste erreichbare Küste **des Besitzers der Klickkachel** (Manhattan ≤ 50). Kriegsschiff: Patrouille zufällig in ±50 um den Klick. MIRV: nur der Zielspieler zählt. Unterhalb von 15 bis 50 Kacheln ist die Etikettvarianz grossteils simulationsirrelevant. | verifiziert an HEAD (PlayerImpl.ts:1696-1778, Config.ts:896/1108/1116, TransportShipUtils.ts:33-41, WarshipExecution.ts:766-772); an 8b45be575 nur Mindestabstand 15, Upgrade-Umwandlung und Nuke-Radien (MECHANIK.md) |
| SPAWN "60 Beispiele, dünne Basis" | Diese 60 sind Spawn-Klicks **nach** der Spawnphase. Die Engine verwirft sie an HEAD als No-op. Die gültigen Spawn-Klicks in der Phase filtert materialize.ts:197 (`!game.inSpawnPhase()`) vollständig heraus. | verifiziert (SpawnExecution.ts `fromIntent && !queuedDuringSpawnPhase`; materialize.ts:197; Collector pool_checks.py: 8'096 in der Phase, 98 danach) |
| "Alle Zahlen sind gemessen" | Gemessen ja, aber teils falsch berechnet: Die Baselines C3/C4 sind verfälscht (§3). | verifiziert |
| "Nach zwei Epochen" | Die Raumtabelle stammt aus Epoche 0 (Schritt 47k bzw. 60k von rund 74,6k je Epoche). Die Kopf-Treffertabelle ist aus 7'680 aufeinanderfolgenden Samples, also rund 5 Partien. | verifiziert (bc_train.py:38, bc_fit.evaluate), Partienzahl Schätzung |
| "340 Kernstunden, jedes Mal" | Wahrscheinlich zum grössten Teil **Encoder-Kosten**, nicht Simulation. Ein schlankes Replay würde dann ~8 bis 16 Kernstunden kosten. Das ist nicht am echten Lauf gemessen (Bewertung in §7.4). | Schätzung (C), Struktur verifiziert |

---

## 2 Urteile zu den vier Vermutungen

**H1 Auflösungsbruch: TEILWEISE.** Der Mechanismus stimmt, die Erklärung für die Schlagzeilenzahlen nicht.
- Pro Zelle gibt es nur Aggregate aus einer Stichprobe von ~4x4 Kacheln: Landanteil, Mehrheitsbesitzer. Gebäude werden auf die Zelle gestempelt. Die Position innerhalb der Zelle fehlt (verifiziert, obs.ts:147-189).
- Fein-Top-1 liegt bei 1,8 %, Zufall bei 1,56 %. Der Kopf ist also informationslos.
- Die Feinebene trägt aber nur ~3 von 136 Rastereinheiten Medianfehler bei, **rund 2 %**.
- Für ~57 % der Etiketten (Strukturen, Kriegsschiffe) ist die Subzellposition simulationsirrelevant, weil die Engine auf ±15 bzw. ±50 einrastet.
- "Schlechter als eine Konstante" ist genau der argmax eines informationslosen Kopfs. Analytisch ergibt eine Beinahe-Eckzelle 4,93, die Zellmitte 3,09 und Zufall 4,14; gemessen wurden 4,8, 3,1 und 4,1 (verifiziert, fine_baselines.py). Das ist kein Beleg für H1.

**H2 Keine Maskierung: TEILWEISE.**
- Richtig für Strukturen (48,5 % der räumlichen Etiketten): 10 bis ~955 legale Zellen statt 16'200 (verifiziert an echten Kartendateien, legal_sets.py; kompakte Gebiete, also eher Untergrenze).
- Falsch für Boot, Nuke und Kriegsschiff (51,5 %): 1'450 bis 16'200 legale Zellen. Beim Boot bleibt mit Maske und Gleichverteilung ein Median-Abstand von 51 bis 66 Zellen. Die Maske allein hilft dort nicht, erst die Wahl des Zielspielers.
- Das Beispiel "Feindkacheln an meiner Grenze für einen Angriff" ist gegenstandslos, weil Angriffe keine Kachel haben.
- Entscheidend: Eine Maske setzt voraus, dass der Kopf auf den Typ bedingt ist. Beim heutigen Kopf gibt es pro Sample gar keine definierte Maske.

**H3 Mehrdeutigkeit: FALSCH als Mechanismus.** Echte Mehrdeutigkeit gibt es trotzdem.
- Kategoriale Kreuzentropie mittelt keine Positionen. Ihr Optimum ist die volle mehrgipflige Verteilung, und der argmax fällt auf einen Gipfel, **nie auf die Mitte**.
- "Die Mitte ist eine dritte Kachel" gilt nur für Koordinaten-Regression, für Dekodieren über den Erwartungswert und für einen Erwartungsabstands-Term.
- Die "nah oder weit"-Form ist teils ein Artefakt der Bin-Einteilung: Grobabstände mal 8 können nur 0, 8, 11,3, 16 und so weiter sein. Die Bins "1", "2" und "3-4" sind daher per Konstruktion leer. Eine **eingipflige** Gaussverteilung mit dem gemessenen Median ergäbe 96,4 % "> 32" (verifiziert, logbin_artifact.py).
- Real ist Mehrdeutigkeit trotzdem gross. Das nächste gleichartige Ziel desselben Spielers liegt im Median 6,4 Zellen bzw. 79 Kacheln entfernt. 62,7 % der Etiketten haben innerhalb von ±300 Ticks ein weiteres gleichartiges Etikett (verifiziert, ambiguity_sets.py, 65 Records). Das ist relevant für Verlust und Kennzahl (§6, §8), nicht als Ursache.

**H4 Kein Abstandsbegriff: TEILWEISE, sekundär.**
- Die Prämisse stimmt, CE ist geometrieblind. Trotzdem lernt die CE eine Rangordnung: Median-Rang 260 statt 8'100.
- 83,6 % der Fehler liegen weit ausserhalb jeder sinnvollen Kernbreite. Ein Abstandsterm ändert daran nichts.
- Ein weiches Ziel ist trotzdem richtig, aber als **Entrauschung passend zur Engine-Auflösung** (Tabelle in §6), nicht als Mittel gegen Fernfehler.
- Ein Erwartungs-Abstands-Term würde die Mitte-Falle aus H3 erst erzeugen. **Nicht verwenden.**

---

## 3 Gültigkeit der bisherigen Messung

Was steht:
- Median-Rang 260 und die Top-k-Werte sind korrekt gegen die echten Logits gerechnet (eval_spatial.py:349). Der Kopf lernt messbar etwas.
- Der Val-Split ist identisch zu bc_fit.

Was nicht steht (alles verifiziert, ausser wo vermerkt):
1. **C3/C4 sind verfälscht.**
   - materialize.ts quantisiert 0 auf `round(127,5) = 128`. dataset.py:36 macht daraus `128/127,5 − 1 = +0,0039`, und `clamp(min=0)` in eval_spatial.py:356 behält den Wert (Collector-Nachprüfung).
   - C3: Jede nicht-eigene Zelle zieht den Schwerpunkt zur Kartenmitte.
   - C4: `argmax` über gleichwertige 1,0-Zellen liefert die oberste, dann linkeste eigene Zelle.
   - "Kopf schlägt Schwerpunkt eigenes Gebiet (27,8 gegen 30,5)" ist damit **nicht belegt**.
2. **Falsche Referenz.** Zufall und häufigste Zelle sind korrekt gerechnet, aber die richtige Messlatte ist gleichverteilt über legale Kacheln.
3. **Einheiten.** Alle Abstände sind Rastereinheiten, anisotrop und je Karte verschieden: 4 bis 25 Kacheln pro Zelle. Die Aufschlüsselung nach Typ mischt diese Skalen.
4. **Checkpoint-Mix.** Die Raumtabelle ist aus Epoche 0, die Kopf-Treffertabelle nach zwei Epochen, aus ~5 Partien. 3,5 % gegen 2,69 % coarse ist durch diese Stichprobe erklärbar.
5. **Stichprobe.** 4'122 Samples passen nicht zu 488 Val-Partien × ~36 % nichtleere Shards × 12 pro Partie (≤ 2'112). Nicht auflösbar, das JSON ist gelöscht.
6. **SPAWN n = 60** sind No-op-Etiketten. Streichen.
7. **Bimodalität** ist nicht belegt (Bin-Artefakt, H3).
8. **Val-Split.** Er hängt an der seed-gemischten Verzeichnisliste. Mit dem neuen Pool (12'217 auf 18'018 Partien, Angabe C) wechseln die Val-Partien. Alte und neue Zahlen sind dann nicht vergleichbar, und der alte Checkpoint kann neue Val-Partien im Training gesehen haben (§8).

---

## 4 Frage 1: Zieldarstellung

### Entscheidung D0

**Ziel zuerst, dann typ- und zielbedingter, maskierter einstufiger Zell-Zeiger auf 180x90. Die Kachel wählen Regel und Engine, kein gelernter Fein-Kopf.**

1. **Reihenfolge:** atype, unit_type, Zielspieler, Zelle. Zielspieler je Typ:
   - Bauwerke: SELF.
   - Boot, Atom, Wasserstoff, MIRV: Besitzer der Klickkachel (`dst_owner`). Das Label ist der Gegner-Slot oder NEUTRAL, bzw. UNRESOLVED ausserhalb der Top 24; die Gebietsebene wird trotzdem aus der echten smallID gebaut.
   - Kriegsschiff: KEINS. Spawn: NEUTRAL.
   - HEAD_SCHEMA: BOAT und die Nuke-Typen bekommen `target`.
2. **Anfrage:** `q = MLP(core 768 ⊕ emb(atype) 32 ⊕ emb(unit_type) 32 ⊕ e_ziel 160) → 256`. Dazu kommen Zellbreite und Zellhöhe in Kacheln als Skalare.
3. **Globaler Kontext:**
   - FiLM(q) auf dem Encoder-Engpass (320 Kanäle, 12x23 = 276 Tokens), dann **eine** Self-Attention-Schicht.
   - Decoder mit Skips zurück auf 90x180.
   - Zusatzebenen auf voller Rasterauflösung: Gebiet des Ziels (`owner_major == Ziel`), Legalität für diesen Typ (aus `legal_bits`), `own_frac` exakt.
4. **Logits:** `⟨P·q, f_c⟩ + b(f_c)`. Die Maske wird als −∞ gesetzt, log-softmax in fp32.
5. **Kosten:** ×1,18 Trainings-FLOPs gegenüber heute (verifiziert, B flops2.py; Kopf nur auf dem räumlichen Teilbatch). Die reale Laufzeit liegt vermutlich bei ×1,2 bis 1,4 (Schätzung: auf der CPU lag die Laufzeit rund 1,4-mal über dem FLOP-Wert).
6. **Spielen:**
   - argmax über die maskierten Zellen, dann die Auflösung innerhalb der Zelle (§6).
   - Lehnt die Engine ab (`canBuild` false), die nächste Zelle der Top 5 versuchen, sonst no_op.
   - Nukes zusätzlich mit `wouldNukeBreakAlliance` maskieren.

Warum D0 gegen die gemessenen Zahlen:
- Die Fernfehler (83,6 %) entstehen, weil der Kopf nicht bedingt ist, nicht wegen der Auflösung (§1.1, H1).
- D0 behebt Bedingung und Sichtfeld direkt. Die Maske wirkt erst mit Bedingung (H2). Beim Boot, dem grössten Posten mit 35 %, entscheidet der Zielspieler.
- Die Feinebene ist 2 % des Fehlers. Der mittlere Versatz eines gleichverteilten Punkts zur Zellmitte beträgt 4,9 Kacheln in der Median-Zelle und 8,3 in der p95-Zelle (verifiziert, cell_center_offset.py). Das liegt unter dem Wirkradius R jedes Typs, ausser bei Atomwaffen auf grossen Karten. Genau das prüfen V1 und V2.

**Warum 180x90 statt Bs 144x144-Leinwand:** Die Leinwand ist nicht verworfen, nur vertagt.
- Die Beobachtung bleibt im laufenden Lauf unverändert. Das vermeidet ein neues Fehlerrisiko in obs.ts unter Zeitdruck.
- Der alte Checkpoint läuft dann auf den neuen Daten und liefert eine Baseline mit der neuen Kennzahl.
- Die Zellfakten passen aufs selbe Raster.
- Anisotropie steht in As Ursachenranking nur auf "niedrig bis mittel". D0 rechnet Verlust und Kennzahl ohnehin in echten Kacheln.
- Die 144-Leinwand ist später aus Tier 1 ableitbar (§9c).

### Zwei Varianten mit Speicherfolgen

- **V1, zweistufig mit Fein-Stufe:** Stufe 1 ist D0. Stufe 2 bekommt einen 48x48-Ausschnitt in voller Auflösung um die Mitte der gewählten Zelle, im Training der echten Zelle.
  - Eingang: 5 Bitebenen (Beziehung 2 Bit, blockiert durch Abstand 15, Fallout, Deckung) plus Gelände aus der Kartendatei. Drei 3x3-Faltungen mit FiLM(q).
  - Logits über die legalen Kacheln der Zelle. 70 k Parameter, 0,05 GFLOP (verifiziert, B flops.py).
  - Prüft H1 richtig: Ist Präzision unterhalb der Zelle lernbar und besser als die Regel?
- **V2, einstufig auf feinerem Raster 360x180:** Zellen im Median 5 x 7,6 Kacheln, mittlerer Versatz zur Mitte 2,4 (verifiziert, cell_center_offset.py). Bs Kopf endet dafür mit einer zusätzlichen ×2-Stufe plus 1x1-Zeiger; Kosten nicht nachgemessen, B nennt +0,93 GFLOP für die analoge 288-Variante.
  - Prüft, ob die Zellgrösse die Grobentscheidung deckelt, vor allem bei Atom und Boot.

### Varianten ohne Speicherfolgen

Jederzeit testbar:
- D0 ohne Maske
- D0 ohne Ziel-zuerst (ein gemeinsamer Kopf)
- D0 mit σ = 0
- L-set-Verlust (§6)

### Verworfen

- **Heutige Zweistufe:** unbedingt, Sichtfeld 5x5 Zellen, Fein-Stufe ohne Information (4,8 ≈ konstanter argmax 4,93).
- **Einstufig auf voller Kachelauflösung** (bis ~6 Mio Ausgänge): Die Engine rastet ohnehin auf 15 bis 50 ein, der Rechenaufwand ist untragbar.
- **Zeiger auf eine Kandidatenliste legaler Kacheln:**
  - Auf Zellebene ist das identisch mit der maskierten Hitzekarte.
  - Auf Kachelebene hat eigenes Land 1,8k bis 108k Kacheln, und die Kandidatenregel würde bei der Materialisierung festgeschrieben.
- **Hitzekarte plus Offset-Regression:** Ohne Eingang unterhalb der Zelle konvergiert sie zur Zellmitte (3,04 gegen 3,09). Direkte x/y-Regression mittelt Modi, **nur dort gilt H3**.
- **Getrennte Köpfe je Typ:** MIRV hat 0,08 % der Etiketten, Wasserstoff 1,5 %. Eigene Köpfe verhungern, FiLM teilt die Parameter.
- **180x180-Leinwand:** ×2,35 Kosten für 12- statt 15-Kachel-Zellen (verifiziert, flops2.py).

---

## 5 Frage 2: Beobachtungsauflösung

- **Die Weltsicht 180x90 bleibt die einzige Sicht, die D0 braucht.** Sie bekommt drei Zusatzebenen:
  - `own_frac`: exakt, statt Mehrheit aus einer Stichprobe von ~4x4 Kacheln. Randzellen mit 40 % Eigenanteil sehen heute 0 aus (verifiziert, Code obs.ts:147-189; Rate nicht gemessen).
  - Gebiet des Zielspielers (aus `owner_major`).
  - Legalität je Typ (aus `legal_bits`).
- Die heutigen 18 Kanäle kennen nur eigen, verbündet und feindlich, **keine Spieleridentität**. Die Ebene "Gebiet des Ziels" braucht deshalb `owner_major`.
- **Kein spielerzentrierter Ausschnitt.** Anteil der Ziele innerhalb eines Quadrats um den eigenen Spawn (verifiziert, B crop_coverage.py, 62'210 Etiketten; der Spawn dient als Ersatz für das Heimatgebiet):
  - Selbst bei 1024 Kacheln Seitenlänge fehlen 61,5 % der Nukes, 15,3 % der Boote und 17,0 % der Kriegsschiff-Bauten.
  - Als Ausgaberaum taugt ein solcher Ausschnitt nicht. Als Zusatzeingang hilft er nur bei Bauwerken, und deren Präzision unter 15 Kacheln überschreibt die Engine.
- **Volle Auflösung nur entscheidungszentriert und nur in V1:** 48x48 um die gewählte Zelle.
  - Nach Kachelgrösse: 48 Kacheln entsprechen 3 bis 5 Median-Zellen, ausreichend für Abstand 15 und den Atom-Innenradius 12.
- **Anisotropie** (Zellen 10 x 15, Seitenverhältnis 0,65):
  - kein neuer Raster im laufenden Lauf,
  - Distanzen in Verlust und Kennzahl in echten Kacheln,
  - Zellbreite und Zellhöhe als Skalare in q.

---

## 6 Frage 3: Mehrdeutigkeit

### Eine Tabelle für Verlust, Kennzahl und Spielregel

R ist der Wirkradius in echten Kacheln. Das weiche Ziel nutzt **σ = R/2**, die Kennzahl **M@R**. Regel für die Herleitung: R ist die Granularität, ab der die Engine ein anderes Ergebnis erzeugt. Wo das Ergebnis zufällig streut, ist R der Versatz, bei dem sich zwei Ergebnisse noch zu 50 % überdecken.

| Typ | Anteil räuml. Etik. (65 Rec.) | Lernziel `res_tile` | R | Herleitung | σ | Spielzeit: Zelle → Kachel |
|---|---|---|---|---|---|---|
| City, Defense Post, Factory, SAM, Silo | 40,4 % | `canBuild(typ, klick)` = eingerastete Kachel; bei Upgrade die Kachel der aufgewerteten Einheit | **15** | Einrastradius 15 = `structureMinDist` 15. Zwei verschiedene legale Ergebnisse liegen ≥ 15 auseinander (verifiziert, Config.ts:1108; MECHANIK an 8b45be575) | 7,5 | eigenes Bauwerk gleichen Typs in der Zelle → dessen Kachel (Upgrade); sonst nicht blockierte eigene Kachel nächst Zellmitte; die Engine rastet ein |
| Port | 8,1 % | `canBuild` | **20** | `radiusPortSpawn` 20, Manhattan (verifiziert, Config.ts:896) | 10 | eigene bebaubare Küstenkachel nächst Zellmitte |
| Boot | 35,4 % | `targetTransportTile(klick)` = nächste erreichbare Küste von `owner(klick)`, dazu `dst_owner` | **15** + gleicher Zielspieler | Landekachel = Angriffsursprung (verifiziert, TransportShipUtils.ts:33-41). 15 ist eine **Setzung** in Höhe der Bau-Granularität. | 7,5 | Kachel des Ziels nächst Zellmitte; die Engine wählt die Küste |
| Atombombe | 5,4 % | Klickkachel (Detonationszentrum). **Achtung:** `canBuild` liefert bei Nukes das Silo, nicht das Ziel | **10** | garantierte Innenscheibe r = 12, 50 % Überlappung bei d = 9,7 (verifiziert, NukeExecution.ts:65-102, radius_and_ci.py) | 5 | Zellmitte, geprüft mit `wouldNukeBreakAlliance` |
| Wasserstoffbombe | 1,5 % | Klickkachel | **60** | Innenscheibe r = 80, 50 % bei d = 64,6, abgerundet (verifiziert, gleiche Quellen) | 30 | Zellmitte |
| MIRV | 0,08 % | nur `dst_owner` | – | Sprengköpfe zufällig auf dem Land des Ziels (verifiziert A, MIRVExecution.ts:236-262) | kein räumlicher Verlust | Kachel des Ziels nächst Zellmitte |
| Kriegsschiff bauen / bewegen | 9,1 % | Klickkachel (= Patrouillenpunkt) | **40** | Patrouille zufällig in ±50 (`warshipPatrolRange` 100): 50 % Überlappung bei d = 41 diagonal bis 50 auf der Achse (verifiziert, Config.ts:1116, WarshipExecution.ts:766-772, radius_and_ci.py) | 20 | Wasserkachel der richtigen Komponente nächst Zellmitte |
| Spawn (Spawnphase) | neu | letzter Klick in der Phase | **15** | **Setzung.** Spawnscheibe r = 4, keine Einrastung (verifiziert, getSpawnTiles, Util.ts:145) | 7,5 | herrenlose Landkachel nächst Zellmitte |

**Verlust (D0-Standard):**
- Kreuzentropie über legale Zellen. Ziel: `0,5·onehot(c*) + 0,5·q_σ`, mit `q_σ(c) ∝ exp(−d_Kacheln(Zellmitte c, res_tile)² / 2σ²) · legal(c)`, renormiert. Rechenaufwand O(B·Zellen).
- Samples, deren Label-Zelle ausserhalb der Maske liegt, fallen aus dem räumlichen Verlust und werden gezählt.
- Die Label-Zelle wird nicht in die Maske geodert, das würde die Antwort verraten.
- Der vorhandene `COARSE_SIGMA` in bc_train.py ist in Zellen definiert und damit anisotrop. Er wird durch σ je Typ in Kacheln ersetzt.

**Warum σ = R/2 und nicht Bs σ = R:**
- Eine 2D-Gaussverteilung mit σ = R legt nur 39,3 % ihrer Masse innerhalb R. 61 % fielen auf Kacheln mit anderem Ergebnis, und das widerspricht Bs eigener Obergrenze: σ muss deutlich unter dem Abstand gleichartiger Ziele von 63 bis 81 Kacheln bleiben.
- σ = R/2 legt 86,5 % innerhalb R (verifiziert, radius_and_ci.py).
- Bei 10 x 15-Zellen wirkt das weiche Ziel damit vor allem an Zellrändern und bei Kriegsschiffen und Wasserstoffbomben. Das ist beabsichtigt.

**Label-Kanonisierung:**
- Gelernt wird auf `res_tile`, nicht auf den Klick. Das entfernt die Klickstreuung innerhalb des Einrastradius.
- Ungültige Klicks (`res_kind = ungültig`) fallen aus dem räumlichen Verlust.
- Zweig, falls die Record-Commits anders einrasten: Der Kanarienlauf misst den Abstand Klick → `res_tile`. Sonst auf den Klick lernen, σ Bauwerke 10.

**Mehrere Modi:** Das Kategorial ist schon mehrgipflig, ein Mixture- oder Diffusionskopf ist unnötig. Speicherfreie Variante **L-set** (B):
- Verlust `−log Σ_{c∈S} p_c`, wobei S = {c*} ∪ aufgelöste gleichartige Ziele desselben Spielers in ±300 Ticks, die **zum Zeitpunkt t legal** sind.
- Braucht nur `sid`, `tick` und `res_tile`.

**Entscheidungsregel beim Spielen:** argmax mit Top-5-Rückfall über die Engine.
- Kein Ziehen im BC-Einsatz. Bei einer breiten Verteilung landet eine gezogene Zelle meist ausserhalb der guten Menge. Das ist eine Überlegung, nicht gemessen.
- Temperatur und Ziehen erst in der RL-Phase.
- Die Worker-Temperatur 1,3 (Angabe C) ist lokal nicht belegt; inf_server.py hat Standard 1,0.

**Realistisch bei 18'000 Partien und einer GPU:**
- geteilter FiLM-Kopf, Masken, weiches Ziel, L-set, alles ohne Zusatzdaten.
- Nicht realistisch bzw. unnötig: Mixture- oder Diffusionsköpfe, eigene Köpfe je Typ.
- Raumanteil der Samples (verifiziert, 65 Records, pool_checks.py):
  - 37,2 % ohne Nichtstun-Samples,
  - 27,6 % mit der Obergrenze der Nichtstun-Samples (NOOP_EVERY = 200),
  - also rund 35 bis 48 räumliche Samples je Batch von 128 (Schätzung für den Pool).
- Seltene Typen (Wasserstoff, MIRV, move_warship) brauchen trotzdem einen Sampler, der nach Typ schichtet. Den löst du getrennt.
- Varianten auf festen Schrittbudgets vergleichen, nicht auf Epochen. Schätzung: 30 bis 40 Mio Samples mit Nichtstun, eine Epoche 11 bis 17 h bei 650 bis 800 Samples/s.

---

## 7 Frage 4: Materialisierung

### 7.1 Grundsatz

- **Tier 1**, das Besitzwechsel-Log pro Partie, ist der kanonische Speicher. Aus ihm leiten sich jede Auflösung, jedes Zentrum, jede Legalitätsregel und jede Tick-Auswahl in Python ab, ohne Engine. Seine Grösse skaliert mit Kartengrösse mal Umkämpftheit, nicht mit der Zahl der Samples.
- Die **Zellfakten auf 180x90** sind die Versicherung: D0 ist damit auf allen Partien trainierbar, auch wenn Tier 1 nur für eine Teilmenge Platz hat.
- Die bisherigen 18x90x180-Tensoren bleiben unverändert.

### 7.2 Feldliste für den Lauf, der jetzt startet (nach Priorität)

Bytes sind Schätzungen, bei Tier 1 von C synthetisch gemessen, und mit ~10 Mio räumlichen Samples gerechnet (Spanne 4,5 bis 13 Mio, Schätzung C).

| Prio | Feld | Form, dtype | welche Samples | Bytes gesamt | wofür |
|---|---|---|---|---|---|
| P1 | `tick` = `game.ticks()`, `sid` (eigene smallID), `allies` (alle smallIDs), `team` | Skalare/Liste in meta-JSON | alle | ~3 B/Sample nach zstd → < 0,2 GB | Ausrichtung auf Tier 1; Besitz-IDs im Zustand sind smallIDs; Beziehungskanäle bei Neu-Featurisierung |
| P1 | `res_tile` (Tabelle §6: `canBuild` bei Bauwerken und Port, `targetTransportTile` beim Boot, **Klickkachel** bei Nukes und Kriegsschiff), `res_kind` (neu / Upgrade / ungültig), `res_unit_id` | int32, u8, int32 | räumliche | ~0,1 GB | Lernziel, Kennzahl, Rauschquote |
| P1 | `dst_owner` = smallID von `owner(klick)` im Entscheidungstick | u16 | räumliche | in obiger Zeile | Ziel-Label Boot und Nukes |
| P1 | Spielkopf `<gid>.hdr.json`: Kartenname, Grösse, W, H, Landkacheln, Engine-Commit, Tick des Spawnphasen-Endes, Spielertabelle smallID ↔ clientID ↔ playerID ↔ Team ↔ Typ, Formatversion | JSON | pro Partie | ~40 MB | alles Abgeleitete |
| P1 | Zeitmessung pro Partie: Summen um `runner.executeNextTick()`, `scanTick`, `encodeVec`, `encodeMap`+zstd, JSON; Anzahl Samples, davon räumlich; Bytes je Block | Log | pro Partie | – | Entscheidungsregel 7.3, Prämisse 7.4 |
| P1 | Spawn-Intents **nach** der Phase nicht emittieren | – | – | – | No-op an HEAD |
| P2 | Tier 1 `<gid>.own.zst`: untere 16 Bit des Kachelzustands (Besitz + Fallout), nur Änderungen; Frames zu 512 Ticks, Kachelrefs sortiert und delta-kodiert, zstd 3; Keyframes am Spawnphasen-Ende und alle 4'096 Ticks; sofort streamen (RAM) | pro Partie | Hash-Auswahl (7.3) | 3,7 bis 17,6 MB/Partie → **66 bis 320 GB alle**, 17 bis 80 GB bei 25 % | alles Weitere |
| P2 | `<gid>.units.zst`: Gebäude (Entstehen, Stufe, Besitzer, Zerstörung, Kachel), Kriegs- und Transportschiffe (Position alle 8 Ticks), Nukes (Start, Ziel, Einschläge) | pro Partie | wie Tier 1 | 1 bis 5 GB alle | Strukturkanäle, Blockierbit, Schiffsmasken |
| P2 | `<gid>.chk`: CRC32 des Zustands an 2 zufälligen Sample-Ticks | pro Partie | wie Tier 1 | vernachlässigbar | Der Python-Rekonstruktor muss bit-genau treffen, sonst gilt die Partie als kaputt |
| P3 | Zellfakten 180x90: `owner_major` (= das in scanTick ohnehin berechnete `ownerG`, pro Tick einmal, von den Samples referenziert), `own_frac` exakt (Iteration über die eigenen Kacheln, kein Vollscan), `legal_bits` (8 Bit, unten) | 90x180 u16 + u8 + u8, roh 64,8 KB | räumliche | 3 bis 7 KB/Sample → **30 bis 70 GB** | D0 auf allen Partien: Zielgebiet, Masken, exakte Eigenebene |
| P4 | Spawn-Samples: **letzter** Spawn-Klick je Mensch in der Phase (Vorab-Scan der Züge), Beobachtung in diesem Tick, `spawn_final = 1` | ganzes Sample | ~26 bis 36 je Partie (verifiziert, 65 Records: Median 26, Mittel 35,8) → 0,47 bis 0,65 Mio | 5 bis 8 GB | Spawn-Kopf; grosse Partien, im Pool eher weniger |
| P5 | **nur Zweig B:** V1-Ausschnitt 96x96 Kacheln 1:1 um die Klickkachel, 5 Bitebenen + Ursprung 2×int16 | u8 | räumliche | 0,5 bis 1,3 KB/Sample → 5 bis 13 GB | V1 auf allen Partien ohne Tier 1 |

`legal_bits` je Zelle (aus B):
- Bit 0: ≥ 1 eigene Kachel
- Bit 1: eigene Kachel ≥ 15 von jedem Bauwerk
- Bit 2: eigene bebaubare Ozeanküste
- Bit 3: fremde, nicht verbündete Küste, per Wasser erreichbar
- Bit 4: Wasser in einer Komponente mit eigenem aktivem Hafen
- Bit 5: Wasser
- Bit 6: herrenloses passierbares Land
- Bit 7: nukebar

Berechnung: aus der Iteration über eigene Kacheln, eine vorberechnete Küstenliste mit Wasserkomponenten-ID und Bauwerksscheiben, **kein Vollscan**. Die Upgrade-Legalität folgt aus den Strukturkanälen.

Warum 96x96 und nicht 48x48: Im Training wird der 48er-Ausschnitt um die **Zellmitte** geschnitten, nicht um das Label. Sonst verrät die Lage des Ausschnitts die Position innerhalb der Zelle. Die Zellmitte liegt bis zu halber Zellgrösse vom Klick entfernt (Passage bis 17 Kacheln), also braucht es mindestens 24 + 17 Kacheln Rand.

### 7.3 Entscheidungsregel (Kanarienlauf, 50 Records, vor dem Volllauf)

**Messen:**
- t1 = MB je Partie für Tier 1
- z = KB je räumlichem Sample für die Zellfakten
- s = Anteil der Simulation an der Gesamtzeit
- o = CPU-Aufschlag gegenüber dem alten Materializer auf denselben Records
- ausserdem: Byte-Gleichheit der bisherigen `.maps`, Checksummen, Rate der Maskenverletzungen, Abstand Klick → `res_tile` je Typ

**Datentore, hart:**
- Bisherige `.maps` nicht byte-gleich: nicht starten.
- Checksumme < 100 %: Tier-1-Format bzw. Parser reparieren. Die Record-Commits fehlen im Klon, das alte Paketformat kann abweichen.
- Maskenverletzung > 1 %: Bits reparieren.

**Umfang von Tier 1:**
- **Zweig A:** 18'018 × t1 + Fixteile (Zellfakten + Spawn, ~40 bis 80 GB) + 20 % Puffer ≤ freier Platz → **Tier 1 für alle**, ohne Ausschnitte. Bei unbekanntem Platz gilt Cs Schwelle **t1 ≤ 6 MB**.
- **Zweig B:** sonst Tier 1 für `sha1(gid) % 100 < 25` plus alle Val-Partien, dazu die V1-Ausschnitte für alle räumlichen Samples. Gesamt rund 60 bis 175 GB (Schätzung).
- **Zweig C (Rückfall):** 15 %, keine Ausschnitte. Gesamt rund 45 bis 125 GB (Schätzung). V1 und V2 dann nur auf etwa 2'700 Partien.

**CPU-Aufschlag:** o > 15 % → zuerst die V1-Ausschnitte streichen, dann Bit 1 (später aus Tier 1).

**Sim-Anteil**, damit die Empfehlung auch trägt, wenn C falsch liegt:
- s ≤ 20 %: Cs Befund steht. Ein weiterer schlanker Replay-Lauf ohne Encoder kostet höchstens rund 70 Kernstunden (Schätzung, 0,2 × 340). Zweig B/C verliert sein Risiko.
- s ≥ 50 %: Die Prämisse fällt, Speicher ist die einzige Versicherung. Zweig A mit Vorrang, auch wenn dafür Platz geschaffen werden muss.

### 7.4 Wie belastbar ist "340 Kernstunden sind Encoder-Kosten"

- **Verifiziert, Struktur:** obs.ts:109-189 legt ein Feld `counts` von 16'200 × 600 Int32 an (38,9 MB). Es wird pro gescanntem Tick genullt und für jede Zelle über 599 Slots maximiert. scanTick läuft in jedem Tick mit mindestens einem Sample.
- **Verifiziert, synthetisch:** Cs Mikrobenchmark (scripts_C/bench_encoder.mjs) bildet die Schleifen nach. Collector-Nachlauf auf demselben M4 Pro: 8,5 bis 12,1 ms pro Scan, davon argmax 6,1 bis 7,4 ms.
- **Konsistenz:** 3'300 bis 3'900 Scans × ~10 ms + 3 s Simulation passt zu 68 Kern-s je Partie (340 h / 18'018). Das ist eine Plausibilitätsprüfung, kein Beweis.
- **Nicht gemessen:** `encodeVec`, Einheiten-Stempeln, JSON, die Simulation in späten 100-Spieler-Zuständen, die Flottenkerne, Unterschiede zwischen Mac- und arch-Kopie.
- **Urteil:** Wahrscheinlich richtig, als Planungsgrundlage aber zu schwach. Deshalb Zeitmessung um `executeNextTick` im Kanarienlauf. Die misst unabhängig von der Engine-Version, `tickExecutionDuration` gibt es nur sicher an HEAD.
- **Nebenbefund, optional:** Stride 600 auf die grösste smallID + 1 zu senken, liefert identische Ausgabe und spart Scanzeit. Nur mit Byte-Gleichheit auf ≥ 3 Records einsetzen, sonst nach dem Lauf.

### 7.5 Bewusst nicht speichern

- Volle Momentaufnahmen pro Sample: 0,2 bis 1,5 TB.
- Kandidatenlisten auf Kachelebene: abgeleitet, zu gross.
- Cs Ausschnitt-Pyramide mit Schrittweite 2 und 4: Keine gewählte Variante nutzt sie, und grober Kontext um das Label würde die Antwort verraten.
- 144x144- und 288x288- bzw. 360x180-Ebenen pro Sample: 4- bis 5-mal die Zellfakten, aus Tier 1 ableitbar.
- Ausschnitte für nicht-räumliche Samples.
- Handels- und Zugschiffe, Skalarspuren der Spieler pro Tick.
- Engine-Regeln über die 8 Faktenbits hinaus.
- zstd 19 schon beim Materialisieren (offline nachkomprimierbar).
- Spawn-Samples nach der Phase.

---

## 8 Frage 5: Fortschrittsmass

**Primär: M@R je Typ.** Wahrscheinlichkeitsmasse der Policy innerhalb von R echten Kacheln um `res_tile`, R aus der Tabelle in §6.
- Beim Boot zählen nur Kacheln im Gebiet von `dst_owner`.
- MIRV: nur die Trefferquote des Zielspielers.
- Die Kachelverteilung: Zellmasse gleichverteilt auf die legalen Kacheln der Zelle, bei Zweistufern das Produkt der Stufen. Damit ist die Kennzahl entwurfsneutral, auch der alte Kopf lässt sich messen.
- Berichtet wird je Typ und als nach Anteil gewichtetes Mittel.
- Die Kurve r ∈ {R/2, 2R, 4R} wird kostenlos mitgeloggt.

**Sekundär:**
1. **ΔNLL_legal** = log₂ q(res_tile) + log₂ |L|, Bits besser als gleichverteilt-legal. Stetig, proper, reagiert zuerst. Verhindert, dass M@R ein Zuspitzen auf den Modus belohnt.
2. **H@R** unter argmax plus Auflösung innerhalb der Zelle: das, was gieriges Spielen tatsächlich trifft.

**Messlatten** auf demselben Eval-Set, auf rohem uint8 bzw. exaktem `own_frac`, **nicht** dequantisiert:
- **gleichverteilt-legal:** synthetisch M@16 bei build 1,6 bis 14,8 % Median, Boot 0,07 bis 0,9 % (Schätzung C); exakt in Sekunden auf dem Eval-Set.
- **Gebietsschwerpunkt**, auf die nächste legale Zelle gerastet.
- **alter Checkpoint:** läuft, weil die Beobachtung gleich bleibt. **Nur auf Eval-Partien ausserhalb seines Trainings** zählen, also alte Val-Partien oder die rund 5'800 neu hinzugekommenen. Dafür muss der alte Split aus der alten Spieleliste rekonstruiert werden.

**Eval-Set:**
- 5'000 räumliche Samples, geschichtet: Strukturen 1'500, Boot 1'500, Nukes 800, Kriegsschiff 700, Spawn 500.
- Höchstens 12 pro Partie und Typ.
- Aus Hash-Val-Partien (`sha1(gid) % 25 == 0`), dieselbe Funktion im Trainer. Damit ist der Split unabhängig von der Poolliste.
- Separate Datei, ~75 MB (Schätzung C), aus Tier 1 bzw. Zellfakten gebaut. Val-Partien liegen in jedem Zweig in Tier 1.

**Stichprobengrösse** (verifiziert, Collector-Nachrechnung von ci_math.py; ICC-Annahme 0,09, Designeffekt 2):
- n = 2'000 bei p = 0,2: ±2,5 pp.
- Gepaarter Vergleich zweier Checkpoints mit 80 % Power und 10 % diskordanten Samples: Δ = 2 pp braucht 1'960 Samples, Δ = 5 pp braucht 312.
- Strukturen und Boot trennen damit Unterschiede um 2 bis 3 pp; Nukes, Kriegsschiff und Spawn nur um 5 pp.

**Takt und Kosten:**
- Alle 2'000 Schritte (5,3 min bei 800 Samples/s) und bei jedem Snapshot.
- Vorwärtslauf über 5k Samples 2 bis 4 s (Schätzung), Overhead rund 1 %.

**Regeln:**
- Eine Änderung wirkt, wenn M@R gepaart bei Strukturen **und** Boot positiv ist, das 95-%-KI 0 ausschliesst und ΔNLL_legal nicht schlechter wird.
- Früher Abbruch: nach 20k Schritten (~53 min) M@R nicht signifikant über gleichverteilt-legal.

---

## 9 Roadmap

### (a) Jetzt, vor und während der Neu-Materialisierung

1. Materializer-Patch P1 bis P4. P5 nur, wenn Zweig B absehbar ist. P1 ist unverzichtbar, ohne P2 oder P3 ist D0 nicht trainierbar.
2. Kanarienlauf auf 50 Records, darunter die grössten Karten (Passage, Korea, Giant World Map), mit altem und neuem Materializer auf denselben Records. Datentore und Regel aus 7.3.
3. Zweig festlegen, Volllauf starten. Den Python-Rekonstruktor für Tier 1 parallel bauen, die Checksummen der Kanarien-Partien müssen 100 % treffen.

### (b) Erste Experimente, in dieser Reihenfolge

| # | Experiment | Kosten | Kennzahl | Go / No-go |
|---|---|---|---|---|
| E1 | Datenprüfung ohne GPU auf dem Eval-Set: Rate der Maskenverletzungen, `res_kind` je Typ, Abstand Klick → `res_tile` je Typ, alle Messlatten, alter Checkpoint | Minuten CPU | M@R, ΔNLL, H@R der Messlatten | Maskenverletzung ≤ 1 %, sonst Bits reparieren. Median Klick → `res_tile` bei Strukturen ≈ 0 → Einrasten an Record-Commits anders, σ-Zweig aus §6 |
| E2 | D0 gegen den heutigen Kopf, gleicher Stamm, gleiche Daten | je 20k Schritte, ~1 h | M@R gepaart | **Go**, wenn M@R bei Strukturen und Boot > heute (KI ohne 0) **und** M@R Strukturen ≥ 2× gleichverteilt-legal. Sonst Bedingung debuggen (sieht der Kopf atype und Ziel?), nicht weitertrainieren |
| E3 | Ablation, ohne Speicherfolgen: D0 ohne Maske, ohne Ziel-zuerst, mit σ = 0 | 3 × ~1 h | ΔM@R zu D0 | Keine Schwelle. Liefert den Beitrag von Bedingung (B1), Maske (H2) und weichem Ziel (H4) |
| E4 | V1 Fein-Stufe gegen Zellmitte-Regel, nur Atom und Boot | ~1 h | H@R, Abstand zu `res_tile` | Behalten, wenn H@R +3 pp gepaart (KI ohne 0) **und** Median-Abstand ≥ 2 Kacheln besser. Sonst Fein-Stufe endgültig streichen |
| E5 | V2 360x180 gegen D0 | ~1,5 h | M@R Atom und Boot | Behalten, wenn +3 pp gepaart (KI ohne 0) bei ≤ 1,3× Trainingszeit |
| E6 | L-set-Verlust gegen D0 | ~1 h | ΔNLL, M@R | Behalten, wenn ΔNLL nicht schlechter und M@R +2 pp |

### (c) Später

- Isotrope 144x144-Leinwand (Bs D0-Raster), aus Tier 1 abgeleitet, nur in Zweig A: Test, ob Isotropie zählt.
- Den Stride-Fix im Encoder, nur mit Byte-Gleichheit.
- Ziehen und Temperatur für die RL-Phase.
- Sampler, der seltene Typen schichtet (löst du getrennt).
- Für Landangriffe gibt es nichts räumlich zu lernen: Ihr "Wo" ist der Zielspieler plus die Truppenmenge. Räumlich ist nur die Bootslandung.

---

## 10 Offene Punkte und Deckungslücken

- **Engine an den Record-Commits:** `vendor/openfront` fehlt auf dem Mac, 8b45be575 und 88cc95d8 fehlen im Klon.
  - Boot-Einrastung, Spawn-Tor und Paketformat der Tile-Updates sind nur an HEAD gelesen.
  - An 8b45be575 bestätigt MECHANIK.md nur Mindestabstand 15, Upgrade-Umwandlung und Nuke-Radien.
  - E1 (Klick → `res_tile`) und die Checksumme decken das auf.
- **Echte Kanarien-Zahlen fehlen:** Tier-1-Bytes (Spanne 66 bis 320 GB), Bytes der Zellfakten, CPU-Aufschlag, Sim-Anteil, Anzahl räumlicher Samples (4,5 bis 13 Mio).
- **Freier Plattenplatz** auf apollo und arch ist unbekannt.
- **GPU:** Durchsatz der 5080 mit D0 und ob das Training am Loader oder an der GPU hängt, ist nicht gemessen. Aktivierungsspeicher von D0 ebenfalls nicht.
- **Wirkung von D0:** Kein Training ist gelaufen. Alle erwarteten Verbesserungen sind Schätzungen.
- **Stichprobe:** Die Anteile stammen aus 65 grossen Records mit hohem Elo und einem Commit. index.sqlite enthält 8'648 öffentliche Partien, das ist nicht der 18k-Pool. Die Geometrie (10 x 15) ist ein Proxy.
- **Boot-Maske mit Ziel:** Grösse nur geschätzt (Dutzende bis wenige Hundert Zellen). Falls ≥ 1'000: in V1 eine Stufe zur Wahl der Landeküste.
- Die Mac-Kopie von materialize.ts kann hinter der arch-Kopie liegen.
- Der Widerspruch 4'122 Samples zu 488 Partien ist nicht auflösbar.
- Der alte Split ist nur mit der alten Spieleliste rekonstruierbar.
- ICC 0,09 ist angenommen.
- Die Worker-Temperatur 1,3 ist unbelegt.
- R für Boot und Spawn sind Setzungen.

---

## 11 Attribution und Konsolidierung

### Herkunft der Befunde

| Befund | Einheit |
|---|---|
| Unbedingter, lokaler Grob-Kopf als Hauptursache | mehrfach (A, B) |
| Angriffe tragen keine Kachel | mehrfach (A, B, C) |
| Rohe Klicks und Engine-Einrasten | mehrfach (A, B) |
| Rastereinheiten ≠ Kacheln, Zellgeometrie | mehrfach (A, B, C) |
| Quantisierungsfehler C3/C4 | A (von B, C übernommen) |
| Fein-Kopf = argmax-Artefakt | A (B übernimmt) |
| Bin-Artefakt "bimodal", H3-Mechanismus | A (B konvergent) |
| Checkpoint-Mix, ~5 Partien, 4'122-Widerspruch | A |
| Legalmengen auf echten Karten | A |
| D0-Architektur, FLOPs, verworfene Optionen, Ausschnitt-Abdeckung, Mehrdeutigkeitsabstände, L-set, Auflösung innerhalb der Zelle | B |
| Tier-1-Log, Bytes, Kosten-Prämisse Encoder, M@r/ΔNLL/H@r, KI-Rechnung, Hash-Split, Spielkopf/smallID-Lücke | C |
| Spawn-Gate in materialize.ts | mehrfach (A, B, C) |
| `res_tile`/`dst_owner` speichern | mehrfach (A, B, C) |
| `canBuild` liefert bei Nukes das Silo; Leck beim alten Checkpoint auf neuem Split; Überlappungsradien; σ = R/2; Raumanteil mit Nichtstun 27,6 %; Menschen pro Partie beim Spawn | Collector |

### Aufgelöste Konflikte

1. **Zellgeometrie 10 x 15 (A) gegen 11,4 x 17,2 (B).** Beide Werte stimmen: A gewichtet nach 8'648 öffentlichen Partien, B nach räumlichen Etiketten in 65 grossen Records. Einheitlich gilt 10 x 15; alle Entwurfsgrössen sind in Kacheln angegeben, der Wert zählt nur für die Übersetzung alter Zahlen.
2. **Spawn.** Bs "alle 8'194 in der Phase" ist tautologisch, weil die Phase dort als `turn ≤ letzter Spawn-Intent` definiert ist (records_spatial.py:55-65). Mit der Engine-Grenze 300 bzw. 150 Züge liegen 8'096 in der Phase und 98 danach. Die 60 Eval-Samples sind No-ops. Entscheidung: nach der Phase verwerfen, in der Phase den letzten Klick je Mensch aufnehmen.
3. **Radien.** Eine R-Tabelle, σ = R/2, Kennzahl M@R. Das ersetzt Bs σ (15/10/30/20) und Cs konstantes r = 16 bzw. 32. Kriegsschiff 40 statt 20 (B) oder 50 (A), Wasserstoff 60 statt 30.
4. **Ausschnitt.** Kein Ausschnitt in D0. V1 aus Tier 1, nur in Zweig B als 96x96 1:1 um den Klick gespeichert. Cs Pyramide mit Schrittweite 2 und 4 fällt weg.
5. **Raster.** D0 auf 180x90 statt 144x144, V2 auf 360x180 statt 288x288. Tier 1 als kanonischer Speicher, Zellfakten auf 180x90 als Versicherung.
6. **Kosten-Prämisse.** Als Schätzung übernommen, Struktur verifiziert, Benchmark reproduziert. Die Regel in 7.3 trägt in beide Richtungen. Zeitmessung um `executeNextTick` statt nur `tickExecutionDuration`.
7. **Raumanteil 24 % (A) gegen 37 % (B).** Das sind verschiedene Nenner, vor bzw. nach der Angriffs-Ausdünnung. Mit Nichtstun-Samples 27,6 %.
8. **Berechnung von `res`.** Engine-Abfrage im Entscheidungstick (A, B) statt Beobachtung des Ergebnisses innerhalb von ≤ 5 Ticks (C), je Typ unterschieden (Nuke-Falle).
9. **Dekodieren.** argmax (B) statt Ziehen mit T = 1,3 (C, unbelegt); die Kennzahl misst die Verteilung und den argmax.
10. **Bimodalität.** C behandelt sie als real, A zeigt das Bin-Artefakt. A gilt. Cs Argument, dass der Median taub ist, gilt für Mischungen trotzdem.
11. **Legalität.** Bs regelbasierte Bits und Cs 4 Art-Masken werden zu einem 8-Bit-Faktenfeld, berechnet ohne Vollscan.
12. **Eval-Schichten.** Cs "build 2000" enthielt Nukes. Nukes bekommen eine eigene Schicht, weil ihr R abweicht.

### Konsolidierungsoperationen (ehrlich gezählt)

- **Deduplizierungen: 13.** Hauptursache B1, Angriff ohne Kachel, Rastereinheiten, Einrast-Semantik, Quantisierungsfehler, Fein-Artefakt, H3-Mechanismus, Spawn-Gate, Hash-Split/Leck, `res`/`dst_owner`, Legalmengen, Bin-Artefakt, echte Kacheln in Verlust und Kennzahl.
- **Konfliktauflösungen: 12** (Liste oben).
- **Gegenprüfungen: 20.**
  - Projekt: net.py-Kopf, actions.py (HEAD_SCHEMA, tile_encode), obs.ts (Stichprobe und Stride 600), materialize.ts (Gate, Emit, Quantisierung), dataset.py/eval_spatial.py (Offset, clamp), bc_train.py COARSE_SIGMA, inf_server-Temperatur, MECHANIK.md (Upgrade), fehlendes `vendor/openfront`.
  - Engine: SpawnExecution (Tor, Neuspawn, Radius 4), Config (15/20/100/130/Nuke-Radien), WarshipExecution (Patrouille), NukeExecution (innen garantiert), TransportShipUtils (`targetTransportTile`), `canBuild` (Silo bei Nukes).
  - Skripte: Bs Definition der Spawnphase, pool_checks.py, map_scale-Nachlauf, bench_encoder-Nachlauf, radius_and_ci.py + cell_center_offset.py.
- **Gestrichen: 8.** Cs Pyramide Schrittweite 2/4, 144x144 als D0-Raster im laufenden Lauf, 288x288-V2, `on_map`-Kanal, Bs Spawn-Heuristik als Rückfall, Spawn-Samples nach der Phase, Cs getrenntes 4-Masken-Format (im Bitfeld aufgegangen), `tickExecutionDuration` als einziger Zeitmesser.
- **Summe: 53.**
