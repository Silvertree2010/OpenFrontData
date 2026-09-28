# B — Entwurf der räumlichen Zielwahl (Analyst B, „entwurf")

Stand 2026-09-10. Alle Zahlen mit Herkunft: **[M]** = von mir per Skript gemessen (Skript genannt), **[A]** = Skript von Einheit A, von mir lokal ausgeführt, **[Q]** = aus dem Quelltext abgelesen, **[S]** = Schätzung/Ableitung, nicht gemessen.

Wichtige Einheitenfalle vorab: eval_spatial.py nennt 1440×720-**Gittereinheiten** „Kacheln". Echte Kacheln pro Gittereinheit sind im Median 1,42 (x) × 2,15 (y), gewichtet nach räumlichen Labels [M grid_geometry.py]; über 8 648 öffentliche Partien gewichtet 1,25 × 1,91 [A map_scale.py]. Eine Grobzelle ist im Median also nicht 8×8, sondern **11,4 × 17,2 echte Kacheln**. Sie ist anisotrop (Breite/Höhe im Median 0,58; auf Korea 6,0 × 24,3 Kacheln). „83,6 % > 32 Kacheln" heisst deshalb real „> 32 Gittereinheiten ≈ > 45–70 Kacheln". Im ganzen Entwurf wird in echten Kacheln gerechnet.

---

## 1 Entscheidung in einem Absatz

**D0 = zuerst das Ziel, dann ein typ-bedingter, legalitätsmaskierter einstufiger Zell-Zeiger auf einer isotropen 144×144-Leinwand. Danach löst die Engine deterministisch die Kachel auf, statt dass ein gelernter Fein-Kopf sie wählt.**

- **Reihenfolge der Entscheidungen:** atype → unit_type → Zielspieler → Zelle.
- **Zielspieler je Typ:** eigenes Gebiet bei Bauwerken, Besitzer der Zielkachel bei Boot und Nuke, neutral bei Spawn.
- **Zellgrösse:** eine Zelle hat `ceil(max(W,H)/144)` Kacheln Seitenlänge, im Median 15 (q10 8, q90 20) [M]. Das deckt sich mit dem Einrast-Radius der Engine von 15 Kacheln und `structureMinDist` = 15 [Q].
- **Kopf:** ein anfragebedingter U-Net-lite-Decoder mit FiLM, einer globalen Self-Attention-Schicht und Punktprodukt-Zeiger. 1,58 M Parameter, 4,1 GFLOP/Sample, läuft nur auf dem räumlichen Teil des Batches (~37 % der Samples [S]).
- **Kosten:** Mit schlankerem Stem ×1,07 der heutigen Trainingskosten (FLOP-gemessen). Mit heutigem Stem ×1,50, bei heutiger 180×90-Auflösung ×1,18 [M flops2.py].
- **Verlust:** Kreuzentropie über den legalen Zellen. Ziel ist 0,5 · One-Hot + 0,5 · Gauss mit σ in echten Kacheln (15 für Bauwerke und Boot, 10 Atom, 30 Wasserstoff, 20 Kriegsschiff), auf die Maske beschnitten. Gelernt wird auf die **von der Engine aufgelöste** Kachel, nicht auf den rohen Klick.
- **Spielen:** argmax über die maskierten Zellen. Lehnt die Engine ab, die nächste Zelle der Top-5 versuchen.
- **Varianten mit Speicherfolgen:**
  - **V1:** zweistufig mit gelerntem Fein-Kopf auf einem entscheidungszentrierten 48×48-Ausschnitt in voller Auflösung (1:1). Prüft Vermutung 1 sauber.
  - **V2:** einstufig auf einem 288×288-Ausgaberaster (7,5-Kachel-Zellen). Prüft, ob die Zellgrösse die Grobentscheidung deckelt.
- **Varianten ohne Speicherfolgen, trotzdem testbar:** gemeinsamer Kopf ohne Ziel-zuerst; mengenwertiger Verlust.

---

## 2 F1 — Darstellung der Zielkachel

### 2.1 Worauf die Entscheidung steht

**Wer ein Kachel-Label hat [M records_spatial.py, 65 Records]:** Nur build_unit, boat, spawn und move_warship tragen eine Kachel. Angriffe tragen nur targetID und troops (INTENTS.md, HEAD_SCHEMA). Das Beispiel „links oder rechts angreifen" betrifft den Kachel-Kopf also gar nicht.

Anteile an den räumlichen Labels ausserhalb der Spawn-Phase (n = 66 954):

| Typ | Anteil |
|---|---|
| Boot | 35,4 % |
| City | 16,9 % |
| Defense Post | 10,8 % |
| Port | 8,1 % |
| Warship (bauen) | 7,0 % |
| Factory | 6,8 % |
| Atom | 5,4 % |
| SAM | 3,2 % |
| Silo | 2,7 % |
| move_warship | 2,1 % |
| Wasserstoff | 1,5 % |
| MIRV | 0,08 % (56 Labels) |

Spawn: **alle 8 194 Spawn-Intents fallen in die Spawn-Phase**. materialize.ts verwirft sie mit `!game.inSpawnPhase()`, deshalb kamen im Eval nur 60 Spawn-Samples vor.

**Wie die Engine die Kachel auflöst** [Q, HEAD-Commit 4e88d78; die Records liefen auf älteren Commits, das prüft A]:
- **Bauwerke:** Der Klick muss auf eigenem Land liegen. Die Engine sucht dann eigene Kacheln im Euklid-Radius 15, verwirft alles, was näher als 15 an einem Bauwerk liegt, und nimmt die dem Klick nächste (`validStructureSpawnTiles`).
- **Upgrade statt Neubau:** Liegt innerhalb von 15 Kacheln ein gleichartiges eigenes Bauwerk, wird der Bauklick zum Upgrade (`findExistingUnitToUpgrade`, `structureMinDist` = 15).
- **Port:** eigene Küstenkachel im Manhattan-Radius 20 (`radiusPortSpawn` = 20).
- **Boot:** landet an `closestReachableShore(owner(tile), …)`, also an der nächsten erreichbaren Küste **des Besitzers der Klickkachel**.
- **Kriegsschiff:** Die Klickkachel muss Wasser sein, das Schiff startet am nächsten eigenen Port.
- **Nukes:** exakte Kachel. MIRV verlangt eine Kachel mit Besitzer.

Folge: Unterhalb von etwa 15 Kacheln bestimmt bei Bauwerken, Port und Boot die Engine das Ergebnis, nicht der Klick. Beim Boot zählt ausserdem zuerst, **wessen** Küste angesteuert wird.

**Legale Mengen** [A legal_sets.py, simuliertes Gebiet auf echtem Gelände, 180×90-Zellen]:
- Bauwerk-legal: 59–679 Zellen bei 20 000 eigenen Kacheln, 237–915 Zellen bei 100 000.
- Boots-Landeküste (alle fremden Küsten): 1 450–3 915 Zellen.
- Maske plus gleichverteilte Wahl ergibt bei Booten immer noch **50–65 Zellen Median-Abstand**. Die Maske allein hilft dem Boot also fast nicht. Erst die Bedingung auf den Zielspieler macht Boote lösbar, und Boote sind der grösste Einzelposten mit 35 %.

**Einordnung der Messung** [S, Folgerung, kein Befund]: Der Median-Rang 260 von 16 200 liegt in dem Bereich, den „Maske plus Gleichverteilung über eigene Zellen" bei Bauwerken erreichen würde (Median-Rang etwa die halbe legale Menge, also 30–450). Das passt zu einem Kopf, der ungefähr „wo überhaupt etwas Legales liegt" gelernt hat, aber nicht die Wahl darin.

Das deckt sich mit dem Netzbau [Q net.py]:
- `coarse_head = Conv2d(128,1,1)` sitzt direkt auf einem 2-Schicht-Stem. Das rezeptive Feld ist 5×5 Zellen, im Median 50 Kacheln breit [A].
- Die Hitzekarte ist **nicht** bedingt auf Kern, atype, unit_type oder Ziel.
- Eine einzige lokale Bewertungsfunktion wird per Softmax über die ganze Karte verglichen, gleich für Stadt, Atombombe und Boot.
- Nukes liegen im Median 633 Kacheln vom eigenen Spawn entfernt, Städte 75 [M crop_coverage.py]. Eine unbedingte Hitzekarte muss beide Gegenden gleichzeitig hoch bewerten, und argmax trifft dann für einen der Typen die falsche.

### 2.2 Gewählt: D0

**Ausgaberaum je Typ.** Zellen einer isotropen 144×144-Leinwand. Die Karte liegt oben links, der Rest ist „ausserhalb" und immer maskiert. Belegt sind im Median 78 % der Zellen, also rund 16 100 echte Zellen, so viele wie heute [M grid_geometry.py]. Maskierung je (Typ, Ziel):

| Typ | Ziel (neuer bzw. erweiterter Zeiger) | Maske (Zellen) | Grössenordnung |
|---|---|---|---|
| City, Factory, DefensePost, SAM, Silo | SELF | eigene Zelle mit ≥1 eigenen Kachel ≥15 vom nächsten Bauwerk entfernt **oder** mit eigenem Bauwerk desselben Typs (Upgrade-Klick) | 60–900 [A] |
| Port | SELF | eigene Küstenkachel, bebaubar | 0–800 [A] |
| Boot | owner(dst): Gegner-Slot oder NEUTRAL | Landeküste (fremd, nicht verbündet, per Wasser erreichbar) **∧ Gebiet des Ziels**, 1 Zelle dilatiert | ohne Ziel 1 450–3 900 [A], mit Ziel [S] Dutzende bis wenige Hundert |
| Atom, Wasserstoff | owner(tile): Gegner oder NEUTRAL | nicht unpassierbar, nicht Teamkamerad, ∧ Gebiet des Ziels | Gebiet des Ziels |
| MIRV | owner(tile), nur Gegner | Gebiet des Ziels | Gebiet des Ziels |
| Warship bauen / move_warship | keins | Ozeanwasser in einer Komponente mit eigenem aktivem Port bzw. mit dem gewählten Schiff | Tausende |
| Spawn | NEUTRAL | herrenloses passierbares Land | Tausende |

**Wie die Bedingung eingeht** (Parameter und FLOPs gemessen, flops.py/flops2.py):

1. `q = MLP([core(768) ⊕ emb(atype,32) ⊕ emb(unit_type,32) ⊕ e_ziel(160)])` → 256. Dabei ist e_ziel die OppEncoder-Einbettung des gewählten Slots bzw. eine gelernte Spezialeinbettung für SELF, NEUTRAL oder KEINS. Beim Training per Lehrer-Vorgabe, beim Spielen aus dem Ziel-Zeiger.
2. FiLM mit q auf dem Engpass des Encoders (320 Kanäle; bei 144×144 sind das 18×18 = 324 Tokens), dann **eine** Transformer-Schicht über diese Tokens. Damit ist das rezeptive Feld global.
3. Decoder mit Encoder-Skips hoch auf 144×144 (3×3-Faltungen auf den mittleren Stufen, 1×1 auf voller Auflösung).
4. Auf voller Auflösung zwei zusätzliche Eingangsebenen: **Gebiet des Ziels** (zur Trainingszeit aus `owner_major` == Ziel-smallID abgeleitet) und **Legalität für diesen Typ**.
5. Logits je Zelle: `⟨P·q, f_c⟩ + b(f_c)`. Maske als −∞, log-softmax in fp32.

HEAD_SCHEMA erweitern: BOAT und die Nuke-Typen von BUILD_UNIT bekommen `target`. Label ist der Slot von `dst_owner`, UNRESOLVED wenn nicht unter den Top 24. Die Ebene „Gebiet des Ziels" wird trotzdem aus der echten Besitzer-ID gebaut.

**Legalitätsmasken:** Sie werden **nicht** aus den heutigen Kanälen abgeleitet. „eigen" ist heute eine Mehrheitsentscheidung über rund 1/16 der Kacheln einer Zelle (Stichprobe in obs.ts), Randzellen mit 40 % Eigenanteil sehen dort 0 aus. Stattdessen schreibt die Materialisierung **ein Bitfeld pro Zelle (uint8)**, exakt aus einem Vollscan in der Engine (Bits in §6). Liegt die Label-Zelle trotzdem ausserhalb der Maske, entfällt der räumliche Verlust dieses Samples und die Rate wird geloggt. Die Label-Zelle wird **nicht** in die Maske geodert, das würde die Antwort verraten. Liegt die Rate über 1 %, ist die Maske falsch; so wird ein Datenfehler sichtbar statt versteckt.

**Wahl der Kachel beim Spielen:** argmax der maskierten Zellverteilung, danach die deterministische Auflösung aus §5. Lehnt die Engine ab (`canBuild` false, zum Beispiel wegen Abstandsregel), die nächste Zelle in Wahrscheinlichkeitsreihenfolge, höchstens k = 5, sonst no_op. Bei Nukes zusätzlich `wouldNukeBreakAlliance` als Maske, wie in MECHANIK.md empfohlen.

**Rechenkosten** (FLOPs gemessen per torch FlopCounter, ohne elementweise Operationen, also Untergrenze):

| Konfiguration | Encoder GFLOP | Kopf GFLOP | Trainingskosten vs. heute |
|---|---|---|---|
| heutiges Netz | 7,24 (Stem 4,09) | 0 | ×1,00 |
| D0 auf 180×90 | 7,31 | 3,30 | ×1,18 |
| D0 auf 144×144, heutiger Stem | 9,29 | 4,17 | ×1,50 |
| **D0 auf 144×144, Stem 64/96** | 6,16 | 4,09 | **×1,07** |
| D0 auf 180×180 | 14,55 | 6,53 | ×2,35 |

Der Kopf läuft nur auf dem räumlichen Teilbatch (37 % [S]). Nur falls das Training rechengebunden ist, heisst ×1,07 grob 750 statt 800 Samples/s [S]. Ob die 800/s heute am Loader oder an der GPU hängen, ist nicht gemessen. Auf der CPU war die Laufzeit des Kopfs relativ zum Netz 0,66, bei einem FLOP-Verhältnis von 0,46 [M]. Der reale Aufschlag kann also etwa 1,4-mal über dem FLOP-Wert liegen, also ×1,10 bis ×1,25 [S].

### 2.3 Variante V1: zweistufig mit Fein-Kopf auf 1:1-Ausschnitt

Stufe 1 = D0. Stufe 2 bekommt einen **48×48-Kachel-Ausschnitt 1:1 um die Mitte der gewählten Zelle** (beim Training: der echten Zelle). Eingang sind 4 Bitebenen plus Gelände aus der statischen Kartendatei. Drei 3×3-Faltungen mit FiLM(q). Logits über die Kacheln der Zentrumszelle (bis 20×20, auf die Zellgrösse gepolstert, auf legale Kacheln maskiert). Gemessen im Prototyp mit 8×8-Ausgang: 70 k Parameter, 0,05 GFLOP/Sample [M flops.py], vernachlässigbar.

Zweck: Vermutung 1 wird damit **richtig** getestet. Der heutige Fein-Kopf hat keine Information unterhalb der Zelle; sein B2-Wert von 4,8 entspricht einem konstanten argmax auf einer Beinahe-Ecken-Zelle (4,93) [A fine_baselines.py]. Wer V1 gegen die Einrast-Regel aus §5 misst, erfährt, ob Präzision unter der Zellgrösse überhaupt lernbar ist und sich lohnt.

### 2.4 Variante V2: einstufig auf feinerem Weltraster (288×288)

Der D0-Decoder läuft bis 144×144, wird ×2 hochgerechnet, bekommt 2–4 gespeicherte Zähl- und Legalitätsebenen in 288×288 dazu und endet in einer 1×1-Zeiger-Schicht. Das sind 82 944 Logits, Zellen im Median 7,5 Kacheln. Kosten: +0,93 GFLOP, 18 k Parameter [M flops2.py].

Zweck: Deckelt die 15-Kachel-Zelle die Grobentscheidung? Die Engine-Radien sagen voraus: nein (siehe §5). V2 ist darum die Variante mit der **niedrigsten Priorität**. Wird ihr Speicher zu teuer (C), fällt sie weg und die speicherfreien Varianten rücken nach.

### 2.5 Verworfen, mit Grund

- **Heutige Zweistufe (unbedingte 1×1-Hitzekarte + Fein-MLP):** Grobstufe ohne Bedingung und mit 5×5-Zellen-Sichtfeld. Feinstufe ohne Information unterhalb der Zelle; 4,8 ≈ konstanter argmax (4,93) [A].
- **Einstufig auf voller Kachelauflösung** (bis 2800×2188 ≈ 6 M Ausgänge): Die Engine rastet ohnehin auf 15 Kacheln ein, und der Rechenaufwand ist untragbar.
- **Zeiger auf eine Kandidatenliste legaler Kacheln:** Auf Zellebene ist das dasselbe wie die maskierte Hitzekarte. Auf Kachelebene sind die legalen Mengen 10³–10⁶ Kacheln gross (eigenes Gebiet 20 000–100 000 Kacheln), und die Kandidaten samt Merkmalen müssten bei der Materialisierung festgeschrieben werden. Jede spätere Änderung der Kandidatenregel würde neu materialisieren.
- **Hitzekarte + Offset-Regression:** Ohne Eingang unterhalb der Zelle strebt die Regression gegen die Zellmitte (3,04 gegenüber 3,09 für die Konstante [A]), also kein Gewinn. Direkte x/y-Regression mittelt Modi; **nur dort** gilt Vermutung 3.
- **Getrennte Köpfe je Typ:** MIRV hat 0,08 % der räumlichen Labels, rund 5 000 Samples bei 6,7 M [S], Wasserstoff 1,5 %. Eigene Köpfe würden verhungern. FiLM teilt die Parameter.
- **180×180-Leinwand:** ×2,35 Kosten für 12- statt 15-Kachel-Zellen, obwohl die Engine bei 15 einrastet.

---

## 3 F2 — Auflösung der Beobachtung

**Entscheidung:**
- Die **Weltsicht bleibt die einzige Sicht, die D0 braucht**. Sie wird isotrop (144×144-Leinwand, Zellseite `ceil(max(W,H)/144)`).
- **Kein** spielerzentrierter Ausschnitt.
- Fein aufgelöst wird nur in V1, und dort **entscheidungszentriert** (48×48 Kacheln, 1:1).
- Die volle Kartenauflösung braucht das Netz **nirgends**. Nur die Engine braucht sie, bei der Materialisierung (exakte Zählwerte, Legalitätsbits, aufgelöste Kachel) und beim Spielen (Einrasten).

**Warum isotrop:**
- Heute ist eine Zelle im Median 11,4 × 17,2 Kacheln [M] (öffentliche Partien 10,0 × 15,3 [A]), auf Korea 6,0 × 24,3, auf Passage 33 × 4,4.
- Derselbe 3×3-Filter bedeutet so auf jeder Karte etwas anderes.
- Ein σ in Zellen bedeutet in x und y verschiedene Kachelabstände.
- Die 144-Leinwand hat so viele belegte Zellen wie heute (Median 78 % von 20 736), aber quadratische Zellen von 15 Kacheln [M].
- Sie verlangt ohnehin eine Neu-Materialisierung. Die ist wegen der Nichtstun-Samples sowieso geplant, also nur **ein** Lauf.
- Rückfall, falls das Umstellen von obs.ts als zu riskant gilt: D0 auf 180×90 (×1,18) mit anisotropem Gauss (σx = σ/Zellbreite, σy = σ/Zellhöhe). Die Entscheidung über den Kopf ändert sich dadurch nicht.

**Warum kein egozentrischer Ausschnitt um das eigene Gebiet.** Anteil der Ziele in einem quadratischen Ausschnitt mit Halbbreite R um den eigenen Spawn als Heimatersatz [M crop_coverage.py, 62 210 Labels]:

| Ausschnitt (Seite) | alle | Bauwerke | Boot | Nukes | Warship bauen |
|---|---|---|---|---|---|
| 64 Kacheln (R 32) | 9,2 % | 15,8 % | 3,8 % | 0,5 % | 2,9 % |
| 128 (R 64) | 23,6 % | 37,0 % | 13,4 % | 1,2 % | 12,2 % |
| 256 (R 128) | 46,5 % | 65,0 % | 34,7 % | 4,1 % | 30,3 % |
| 512 (R 256) | 68,7 % | 85,0 % | 61,1 % | 15,2 % | 56,1 % |
| 1024 (R 512) | 86,4 % | 95,4 % | 84,7 % | 38,5 % | 83,0 % |

Selbst ein 1024-Kachel-Ausschnitt verfehlt 61 % der Nukes und 15 % der Boote. Als Ausgaberaum taugt er nicht, die Weltsicht muss bleiben. Als *zusätzlicher* Eingang in feiner Auflösung nützt er nur bei Bauwerken, und die Präzision dort überschreibt die Engine unterhalb von 15 Kacheln ohnehin.

Den C3-Schwerpunkt aus der Messung (Median 24,4 Zellen) verwende ich **nicht** zur Bemessung. Nicht-eigene Zellen kommen dequantisiert als +0,0039 zurück, das zieht den Schwerpunkt zur Kartenmitte [A baseline_quant_bug.py].

**Kanäle:**
- **144×144, alle Samples (Netz-Eingang):** die 18 bisherigen Kanäle plus
  - `own_frac`: exakter Anteil eigener Kacheln, Vollscan statt Mehrheit über 1/16 Stichprobe
  - `on_map`
  - `shore`: Anteil Ozeanküste
  - die Legalitätsbits als Ebenen

  `on_map` und `shore` sind statisch und werden zur Trainingszeit aus der Kartendatei abgeleitet, nicht gespeichert.
- **V1 in 1:1:** nur Grössen, die innerhalb einer Zelle variieren und auf Kachelebene zählen: Besitzbeziehung (eigen/verbündet/feind/herrenlos, 2 Bit), „blockiert durch Abstand 15 zu irgendeinem Bauwerk" (1 Bit), Fallout (1 Bit), Deckung durch Verteidigungsposten (1 Bit). Gelände kommt aus der statischen Kartendatei über den Ausschnitt-Ursprung.
- **V2 in 288×288:** `own_count` und das Legalitätsbitfeld, optional `owner_major`.

---

## 4 F3 — Mehrdeutigkeit

**Gilt „die Mitte zwischen zwei Modi" für diesen Kopf? Nein.** Ein Softmax-Kategorialkopf mit Kreuzentropie hat als Optimum die volle bedingte Verteilung, auch wenn sie mehrgipflig ist; argmax nimmt dann einen Gipfel, nicht die Mitte. Modi mitteln nur (a) Regressionsköpfe, (b) Dekodieren über den Erwartungswert und (c) ein Erwartungsabstands-Term Σ p_c · d(c, c*), denn dessen Minimierer ist eine Punktmasse im geometrischen Median. **Deshalb kein Abstandsterm im Verlust, sondern ein weiches Ziel.**

Die „nah oder weit"-Form des Histogramms beweist Mehrgipfligkeit auch nicht: Eine eingipflige Gauss-Verteilung mit σ = 14,4 Zellen ergibt in denselben log-Klassen 96,4 % „> 32" und Median 136 [A logbin_artifact.py]. Die Verdikte über Vermutung 3 und 4 gibt A. Mein Entwurf hängt von ihnen nicht ab.

**Echte Mehrdeutigkeit gibt es trotzdem, und sie ist gross** [M ambiguity_sets.py, 66 982 Labels]:
- Das nächste gleichartige Ziel desselben Spielers liegt im Median **6,4 Zellen = 79 Kacheln** entfernt, in 64,6 % der Fälle mehr als 4 Zellen (City 4,9 Zellen / 63 Kacheln, Defense Post 5,4 / 70, Boot 6,9 / 81).
- 62,7 % aller Labels haben innerhalb von ±300 Ticks mindestens ein weiteres gleichartiges Label desselben Spielers (Boot 84 %, Defense Post 77 %, Atom 73 %).
- Wählt das Netz „eine gültige, aber andere" Stelle, zählt das in rund 65 % der Fälle als Fehler über 4 Zellen. Ein Teil der 83,6 % ist also womöglich keine Unfähigkeit. Die Kennzahl muss das trennen; das ist Sache von C, ich melde nur die Zahl.

**Verlust (D0-Standard):**
- Kreuzentropie über den legalen Zellen mit Ziel `0,5·onehot(c*) + 0,5·q_σ`, wobei `q_σ(c) ∝ exp(−d_Kacheln(c,c*)² / 2σ²) · legal(c)`, renormiert. Rechenaufwand O(B·Zellen), vernachlässigbar.
- σ je Typ in echten Kacheln: **15** für Bauwerke und Boot, **10** Atom, **30** Wasserstoff, **20** Kriegsschiff; MIRV bekommt nur das Ziel-Label und σ = 50.
- Begründung von unten: Einrast-Radius 15 und `structureMinDist` 15 [Q]; Boot rastet ohnehin an der nächsten Küste ein; Atom-Innenradius 12, Wasserstoff 80/100 (MECHANIK.md).
- Begründung von oben: Aufeinanderfolgende gleichartige Ziele liegen 63–81 Kacheln auseinander. σ muss deutlich darunter bleiben, sonst verschmilzt zum Beispiel „Stadt innen" mit „Verteidigungsposten am Rand". σ = 15 ist ein Viertel bis ein Fünftel dieses Abstands.
- Auf der 144-Leinwand ist das σ ≈ 1 Zelle bei c = 15 und 1,9 Zellen bei c = 8.
- Ein grösseres σ bringt nichts gegen Fehlgriffe über 4 Zellen. Die kommen von fehlender Bedingung und Maske, nicht von der Verlustform.

**Label-Kanonisierung:** Gelernt wird auf `res_tile`, also das Ergebnis von canBuild bzw. targetTransportTile im Entscheidungstick. Bei einem Upgrade-Klick ist das die Kachel der aufgewerteten Einheit. Ungültige Klicks (canBuild false) fallen aus dem räumlichen Verlust; die Rate misst A bzw. C. So verschwindet Label-Rauschen, das nur aus Klickstreuung innerhalb des Einrast-Radius besteht.

**Mehrere Modi:** Das Kategorial ist schon mehrgipflig, ein Mixture- oder Diffusionsmodell braucht es nicht. **Speicherfreie Verlustvariante L-set:**
- Verlust `−log Σ_{c∈S} p_c`, wobei S = {c*} ∪ die aufgelösten gleichartigen Ziele desselben Spielers innerhalb von ±300 Ticks, die **zum Zeitpunkt t legal** sind.
- Kostet nichts, weil Shards pro Spiel sequentiell gelesen werden.
- Risiko: belohnt Stellen, die erst später sinnvoll werden. Deshalb Legalitätsprüfung zum Zeitpunkt t und kurzes Fenster.

**Entscheidungsregel beim Spielen:**
- **argmax** mit Top-5-Rückfall über die Engine.
- Kein Ziehen im BC-Einsatz. Bei einer breiten Verteilung (heute Top-1 2,69 %) landet eine gezogene Zelle meist ausserhalb der guten Menge; argmax nimmt den dichtesten Gipfel. Das ist eine Überlegung, nicht gemessen.
- Ziehen mit T = 1 und Top-p 0,9 erst in der RL-Phase zur Exploration.
- Ein Temperatur-Schalter kostet kein Training und kann im Viewer A/B-verglichen werden.

**Was bei 18 000 Partien und einer GPU realistisch ist** [S]:
- Räumlicher Anteil der materialisierten Samples etwa 37 %: THIN = 30 behält 45,9 % der Angriffe, der Spawn-Phasen-Filter ist berücksichtigt, der Filter für tote und nicht-menschliche Spieler nicht [M records_spatial.py].
- Bei 18 M Samples sind das rund 6,7 M räumliche: Boot ~2,4 M, City ~1,1 M, … MIRV ~5 000.
- Realistisch: geteilter FiLM-Kopf, Masken, weiches Ziel, L-set. Alles O(B·Zellen), ohne Zusatzdaten.
- Nicht realistisch bzw. unnötig: Mixture-Density- oder Diffusionsköpfe, eigene Köpfe je Typ.
- Seltene Typen brauchen einen stratifizierten Sampler. Den löst der User getrennt; hier heisst das mindestens rund 32 räumliche Samples pro Batch von 128.

---

## 5 Fein-Stufe

**Entscheidung: Der gelernte Fein-Kopf fällt in D0 weg. An seine Stelle tritt eine deterministische Auflösung je Typ.** Sie läuft nur beim Spielen und hat keine Parameter:
- **Bauwerke:** Liegt in der Zelle ein eigenes Bauwerk desselben Typs, Klick auf dessen Kachel (Upgrade). Sonst die nicht blockierte eigene Kachel, die der Zellmitte am nächsten liegt, danach das Einrasten der Engine (Radius 15).
- **Port:** eigene bebaubare Küstenkachel, die der Mitte am nächsten liegt.
- **Boot:** die Kachel des Ziels, die der Zellmitte am nächsten liegt; die Engine wählt dann die nächste erreichbare Küste.
- **Atom und Wasserstoff:** Zellmitte, geprüft mit `wouldNukeBreakAlliance`.
- **MIRV:** die Kachel des Ziels, die der Mitte am nächsten liegt.
- **Kriegsschiff:** die Wasserkachel der richtigen Komponente, die der Mitte am nächsten liegt.
- **Spawn:** die herrenlose Landkachel, die der Mitte am nächsten liegt.

**Die Latte:** Die heutige Zellmitte schafft 3,1 Gittereinheiten, das sind real etwa 4,4–6,7 Kacheln [S aus den Faktoren oben]. Bei einer 15-Kachel-Zelle ist der mittlere Abstand eines gleichverteilten Punkts zur Mitte 0,383 · 15 = 5,7 Kacheln (analytisch). Das liegt unter dem Einrast-Radius 15, unter `structureMinDist` 15 und unter dem Atom-Innenradius 12. Also genügt die Zellmitte fast überall.

**Gemessen werden muss die Einrast-Regel an zwei Grössen:**
1. Anteil der Fälle, in denen das Ergebnis der Engine gleich `res_tile` ist, bzw. dieselbe aufgewertete Einheit oder dieselbe Landeküste.
2. Abstand in echten Kacheln zu `res_tile`.

Beides ist ohne Training aus den Replays berechenbar. V1 muss die Regel auf beiden Grössen schlagen, sonst bleibt der Fein-Kopf draussen.

---

## 6 Daten pro Sample (C bemisst die Bytes; meine Rohgrössen sind grob)

| Feld | Form | dtype | welche Samples | nötig für |
|---|---|---|---|---|
| Kartenkanäle, bisher 18, **auf 144×144-Leinwand** | 18×144×144 | uint8 | alle | D0, V1, V2 (Rückfall: 18×90×180 wie heute) |
| `own_frac` (exakt, Vollscan) | 144×144 | uint8 | alle | D0, V1, V2 |
| `owner_major` (smallID des Mehrheitsbesitzers) | 144×144 | uint16 | räumliche; alle, falls billig | D0 (Ebene „Gebiet des Ziels", Boots- und Nuke-Masken), V1, V2 |
| `legal_bits` (exakt, 8 Bit, s. u.) | 144×144 | uint8 | räumliche (alle, falls auch als Eingangsebene gewünscht) | D0, V1, V2 |
| `cell_tiles` c, Leinwandlage | Skalar | uint8 | alle | D0, V1, V2 (auch aus W, H ableitbar) |
| `res_tile` (aufgelöste Zielkachel) | Skalar | int32 | räumliche | D0, V1, V2 |
| `res_kind` (neu / Upgrade / ungültig) und `res_unit_id` | Skalare | uint8, int32 | räumliche | D0, V1, V2 |
| `dst_owner` (smallID des Besitzers der Klickkachel im Tick) | Skalar | uint16 | räumliche | D0 (Ziel-Label für Boot und Nukes) |
| Spawn-Samples: letzter Spawn-Klick je Spieler, Beobachtung in diesem Tick | ganzes Sample | wie oben | neu, rund #Menschen pro Partie | D0 (Spawn-Kopf) |
| roher Klick, mapW/H, oppIds, opps[].id (smallID), turn, clientID | vorhanden | — | alle | alles (L-set braucht clientID + turn + res_tile, schon vorhanden) |
| V1-Ausschnitt: 48×48 Kacheln 1:1 um die Mitte der **echten** Zelle; Bits: Beziehung (2), blockiert (1), Fallout (1), Deckung durch Verteidigungsposten (1) | 48×48 + Ursprung 2×int16 | uint8 | räumliche | nur V1 (roh 2,3 KB/Sample) |
| V2-Ebenen: `own_count`, `legal_bits` (optional `owner_major`) | 2–3×288×288 | uint8 (uint16) | räumliche | nur V2 (roh 166–332 KB/Sample vor Kompression; teuerster Posten, streichbar) |
| statisches Gelände (Land, Höhe, Küste, on_map) für jede Auflösung | — | — | nicht speichern | zur Trainingszeit aus Kartenname + `map.bin`/`map4x.bin` (Engine-Ressourcen) |

`legal_bits`, je Zelle, exakt aus einem Vollscan in der Engine:

| Bit | Bedeutung |
|---|---|
| 0 | ≥1 eigene Kachel |
| 1 | ≥1 eigene Kachel ≥15 Kacheln von jedem Bauwerk (Neubau möglich) |
| 2 | ≥1 eigene Ozeanküstenkachel, bebaubar (Port) |
| 3 | ≥1 fremde, nicht verbündete bzw. nicht Team-Küstenkachel, per Wasser von eigener Küste erreichbar (Boot) |
| 4 | Ozeanwasser in einer Komponente mit eigenem aktivem Port (Kriegsschiff bauen) |
| 5 | Wasser (move_warship) |
| 6 | herrenloses passierbares Land (Spawn) |
| 7 | nukebar (nicht unpassierbar, nicht Teamkamerad) |

Die Upgrade-Legalität je Typ ergibt sich aus den Bauwerkskanälen (eigenes Vorzeichen, Typ). Bit 1 kostet je Tick ein Rastern der Blockierscheiben um alle Bauwerke (Radius 15) plus O(eigene Kacheln) je Sample. Den Mehraufwand gegenüber dem Replay schätze ich klein [S]; A bzw. C prüfen das.

---

## 7 Abhängigkeiten von A-Prämissen (mit Zweig, falls eine fällt)

1. **Angriffe tragen keine Kachel.** Von mir in INTENTS.md und HEAD_SCHEMA geprüft; das ist eine Eigenschaft der Daten. Der Entwurf hängt daran nur insofern, als er Angriffe gar nicht abdeckt.
2. **Auflösungsregeln der Engine** (Einrasten 15 / Port 20 / Boot an nächster erreichbarer Küste des Besitzers / Nukes exakt), gelesen am HEAD-Commit. Die Records liefen auf älteren Commits.
   - *Zweig „kein Einrasten auf alten Commits":* Die Einrast-Regel beim Spielen bleibt, sie läuft auf unserer Live-Engine. σ für Bauwerke sinkt auf 8 Kacheln.
   - *Zweig „`res_tile` nicht treu berechenbar":* auf den rohen Klick lernen, σ für Bauwerke 20 Kacheln.
3. **Netzdiagnose** (unbedingter 1×1-Kopf, rezeptives Feld 5×5 Zellen). Von mir in net.py Z. 46–49, 125, 159 geprüft. D0 behebt beides, unabhängig vom Verdikt.
4. **Grössen der legalen Mengen** [A-Simulation: kompaktes Gebiet, also optimistisch]. Begründen Ziel-zuerst beim Boot.
   - *Zweig „echte Boots-Maske mit Ziel noch ≥1 000 Zellen":* dann in V1 die Boots-Stufe 2 als Wahl der Landeküste über die Küstenkacheln des Ziels bauen (dichte Küstenmaske 1:1 im Ausschnitt).
5. **Spawn-Samples fehlen** (alle 8 194 Spawn-Intents liegen in der Spawn-Phase [M]; der Filter steht in materialize.ts). D0 braucht sie.
   - *Zweig „nicht behoben":* Spawn per Heuristik (die herrenlose Zelle mit dem grössten Abstand zum nächsten Spieler), kein gelernter Spawn-Kopf.
6. **Einheit „Kachel" in eval_spatial = Gittereinheit.** [A map_scale.py, M grid_geometry.py]. Der Entwurf rechnet in echten Kacheln; C sollte die Kennzahl umstellen.
7. **C3-Schwerpunkt ist verzerrt** [A]. Die Bemessung des Ausschnitts nutzt deshalb den Spawn-Ersatz.
   - *Zweig „Spawn ist ein schlechter Heimatersatz":* Die Aussage bleibt qualitativ, weil Nukes, Boote und Kriegsschiffe schon relativ zu jedem Punkt im eigenen Gebiet weit weg liegen.
8. **Die Verdikte zu Vermutung 3 und 4 gibt A.** D0 hängt davon nicht ab: Das weiche Ziel ist durch die Engine-Radien begründet, nicht durch die Histogrammform.

---

## 8 Rechenaufwand

- **Gemessen [M]** (flops.py/flops2.py, FlopCounterMode, B = 1, CPU; Matmul- und Faltungs-FLOPs, ohne Normen und Aktivierungen):
  - heutiges Netz: 3,875 M Parameter, 7,24 GFLOP pro Sample und Vorwärtslauf, davon 4,09 im Stem bei 90×180
  - D0-Kopf: 1,58 M Parameter, 3,30 GFLOP (180×90) bzw. 4,09–4,17 (144×144)
  - V1: +0,05 GFLOP, 70 k Parameter
  - V2: +0,93 GFLOP, 18 k Parameter
  - Kandidatenzeiger (verworfen) K = 256: 0,14 GFLOP
- **Kostenfaktoren** (Kopf nur auf 37 % räumlichen Samples, Vorwärts + Rückwärts proportional): D0 auf 180×90 ×1,18; auf 144 mit heutigem Stem ×1,50; **auf 144 mit Stem 64/96 ×1,07**; auf 180×180 ×2,35.
- **Geschätzt [S]:**
  - 800 Samples/s bei 7,24 GFLOP ×3 entsprechen rund 17 TFLOP/s effektiv. Ob GPU oder Loader limitiert, ist nicht gemessen. Liegt es am Loader (Python-JSON und zstd je Sample), kostet D0 fast keinen Durchsatz.
  - Die CPU-Wandzeit sagt, der reale Aufschlag kann rund 1,4-mal über dem FLOP-Wert liegen: ×1,10 bis ×1,25, also etwa 640–730 Samples/s.
  - Eine Epoche über 18 M Samples bei 700/s dauert 7,1 h, passt also in ein Werktagsfenster von 07 bis 17 Uhr.
- **Speicher:** Die D0-Aktivierungen auf 144×144 bei Batch 128 wurden nicht gemessen. Grössenordnung [S]: Decoder-Aktivierungen bis 64 × 20 736 × 4 B ≈ 5 MB pro Sample und Ebene, bei rund 47 räumlichen Samples je Batch unkritisch für 16 GB.

---
