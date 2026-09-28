# Spielmechanik — quellenverifiziert

Alles hier gegen `vendor/openfront/src/core/` auf Commit `8b45be575` geprüft.
Wo Wiki und Quellcode sich widersprechen, gilt der Quellcode.
Stand 2026-09-06.

## Zahlen

```
nukeMagnitudes         Atombombe   innen 12  außen 30
                       Wasserstoff innen 80  außen 100
                       MIRV-Kopf   innen 12  außen 18
nukeAllianceBreakThreshold   100   (streng größer)
traitorDefenseDebuff   0.5    traitorSpeedDebuff 0.8   traitorDuration 300 Ticks
defensePostRange       30     defenseBonus ×5          speedBonus ×3
falloutDefenseModifier 5 − r×2   → Bereich [5, 3]   (NICHT 2.5 — der
                                   Kommentar IM QUELLCODE ist selbst falsch)
boatMaxNumber          3
malusForRetreat        25 %
```

## Allianzbruch durch Nuke — ODER aus zwei Zweigen

1. **Kachelzweig** — gewichtete Verbündetenfelder, innen ×1 / außen ×0,5,
   bricht bei **> 100**.
2. **Gebäudezweig** — **kein** Schwellenwert. Ein einziges verbündetes Gebäude
   im **äußeren** Radius reicht.

Geprüft **beim Start**, auf dem vollen Kreis — nicht am tatsächlichen Schaden.
Der äußere Ring zerstört nur mit ~50 % Wahrscheinlichkeit, die Prüfung ist also
strenger als der reale Schaden. MIRV-Sprengköpfe sind in beiden Zweigen ausgenommen.
Die Menge enthält auch Nicht-Verbündete: fremde Gebäude streifen kostet −100 Beziehung.

## Korrektur: „atom bomb chipping"

Der Wiki-Begriff meinte etwas anderes als angenommen — nicht Landestreifen freiräumen,
sondern **den Hafen eines Verbündeten zerstören**, ohne Verräter zu werden, indem man
die Bombe so legt, dass sie den Hafen gerade eben streift.

**Das ist gepatcht.** Der Gebäudezweig oben bricht die Allianz bei jedem verbündeten
Gebäude im Radius 30. Der Client zeigt beim Platzieren einen roten Warnkreis
(`BuildPreviewController.ts` ruft `wouldNukeBreakAlliance()` live).

**Fallout hilft dem Angreifer nicht** — `Config.ts:730` multipliziert die Verluste
des ANGREIFERS mit 3 bis 5. Frischer Krater kostet mehr als sauberes Ödland.

**Aber zwei Mechaniken retten die Idee teilweise:**
- Eine Bootslandung zahlt **gar keine Kampfkosten**. `TransportShipExecution` ruft
  `conquer(dst)` bedingungslos, bevor überhaupt ein `AttackExecution` entsteht.
  Landen auf Fallout ist gratis, nur das Weitervorrücken zahlt den Aufschlag.
- `GameImpl.conquer()` ruft `setFallout(tile, false)` — Fallout ist eine einmalige
  Maut pro Feld, kein Dauerzustand.

**Der echte Kern:** Detonation ruft `relinquish(tile)` → die Felder werden herrenlos.
`canBuildTransportShip` lehnt eigene und **verbündete** Ziele ab. Herrenloses Land
besteht beide Prüfungen. Feindliche oder verbündete Küste in neutrale Küste zu
verwandeln **öffnet Landeziele, die es vorher nicht gab**. Das ist die Mechanik —
nur heißt sie in der Community nicht so.

Team-Variante, im Wiki belegt: nahe an Verbündete nuken, um eine **Landgrenze**
zum Gegner zu öffnen, wenn die eigenen Leute einen einmauern.

## Nicht belegbar

- **„boat bombing"** — null Treffer in 313 Steam-Beiträgen, Wiki, zwei Guide-Seiten,
  Forum, Websuche. Existiert als Begriff nicht.
- **„stack overwhelming"** — ebenfalls nicht belegt.

## Die 1,67×-Wand — wichtig fürs Mengen-Kopf-Design

Aus `attackLogic`: beide Verhältnisterme werden geklemmt. Ab
**Angriffstruppen ≈ 1,67 × Verteidigertruppen** bringt jede weitere Truppe **nichts**.
Der Rest hängt nur an der Verteidiger**dichte** (Truppen pro Feld).

Ödland hat eine eigene Decke: ab ~6.600 Truppen (Ebene), 8.000 (Hochland),
10.000 (Gebirge) gewinnt man keine Felder mehr dazu.

**Folge:** die richtige Spielweise ist das Gegenteil von Stapeln — mehrere Fronten,
weil die Vorrückrate an der Grenzlänge hängt.

## Wachstum

```
maxTroops = 2 × (felder^0.6 × 1000 + 50000) + Σ(Stadtstufen) × 250_000
zuwachs  ∝ truppen^0.73 × (1 − truppen/max)
```
Optimum bei **42,2 %** der Maximalbevölkerung (0.73/1.73), nicht 50 %.
Der Exponent 0,6 auf Fläche heißt: Land hat scharf abnehmenden Ertrag —
deshalb sind Städte früh stärker als Expansion.

## Handel

Wiki-Formel ist veraltet. Aktuell `Config.ts:354`:
```
tradeShipGold = 75_000 / (1 + exp(−0.03 × (dist − debuff))) + 50 × dist
```
Sigmoid, keine Potenz. **Einkommensklippe bei ~300 Kacheln.**
100 Kacheln → ~5.185 Gold · 300 → ~52.500 · 1.000 → ~125.000.
Häfen dicht beieinander verdienen fast nichts. Häfen handeln nur mit **fremden**
Häfen. Verbündete Häfen haben doppelte Auswahlchance.

## Verrat

Verräterzustand 30 s: Angreifer verlieren **×0,5** gegen dich — und zwar
**alle auf der Karte**, nicht nur der Betrogene. Allianz läuft nach 5 Minuten
von selbst aus, und **Auslaufen ist kein Verrat**. Daraus die Kernregel der Meta:
Timer beobachten, direkt nach Ablauf zuschlagen, Kosten null.

## Einkreisung — laut Quellen der stärkste Einzelzug

`PlayerExecution.ts:168-260`:
- Absorbierte Klumpen kosten **null Truppen**, bei vollständiger Eliminierung
  bekommt man **sein gesamtes Gold**.
- **Ein einziges Feld an Meeresküste oder Kartenrand macht den ganzen Klumpen immun.**
- Muss von **genau einem** nicht-befreundeten Spieler umschlossen sein —
  ein Verbündeter kann dich also **nicht** absorbieren. Undokumentierte Schutzwirkung.
- **Ein herrenloses Nachbarfeld bricht die Einkreisung.** Folge: neben einer Tasche
  zu nuken **hebt die Einkreisung auf**, weil Krater herrenlos sind.
- Die Beute geht an den **größten laufenden Angriff**, nicht an die längste Grenze —
  ein Nachzügler mit mehr Truppen schnappt sie weg.

Wiki, belegt: mehrere kleine Seelandungen auf eine ansonsten umschlossene
Küstenregion, um sie einkreisbar zu machen. **Das** ist die dokumentierte
Boots-Landeplatz-Taktik — nicht nuke-getrieben.

## Was daraus für Beobachtung und Aktionen folgt

| Merkmal | Warum |
|---|---|
| `wouldNukeBreakAlliance(ziel)` als Maske | exakter Orakel-Aufruf, kein Lernen nötig |
| Nächstes verbündetes Gebäude + Abstand | der Gebäudezweig ist gnadenlos |
| Herrenlose Küste, per Boot erreichbar | das echte Landeplatz-Signal |
| Verteidigerdichte (Truppen/Feld) pro Gegner | die 1,67×-Wand hängt daran, nicht an Truppen |
| Bündnis-Restlaufzeit pro Verbündetem | „nach Ablauf zuschlagen" ist die Kernmeta |
| Verräter-Timer | ×0,5 Verteidigung gegen ALLE, 30 s |
| Küste/Rand-Flag pro Gegnerklumpen | entscheidet Einkreisbarkeit vollständig |
| Handelsdistanz zu fremden Häfen | Sigmoid-Klippe bei 300 |

---

# Nachtrag — zweite Recherche, alles gegen v0.33.13 geprüft

## ⚠️ Die wichtigste Falle: Truppen werden im Bild durch 10 geteilt

```ts
// src/client/Utils.ts:280
renderTroops(t) { return renderNumber(t / 10); }
```

**Jede Truppenzahl auf dem Bildschirm ist ein Zehntel des Simulationswerts.**
Startruppen sind intern 25.000, angezeigt „2.5K". Stadtbonus intern 250.000,
angezeigt „+25.0K". **Gold wird NICHT geteilt, nur Truppen.**

Für uns: durchgehend interne Einheiten verwenden. Wer Beobachtungen aus der
Simulation nimmt, aber Schwellen aus Wiki oder Videos, liegt um Faktor 10 daneben.

## Korrekturen an meinen eigenen Angaben oben

- `falloutDefenseModifier` ergibt **[3, 5]**, nicht [2,5, 5]. Der Kommentar im
  Quellcode selbst behauptet 2,5 — er ist falsch.
- **Man KANN auf verstrahltem Boden landen.** Die Landeprüfung testet Küste,
  Land, Besitzer und Wasserkomponente — Fallout nie. Der Brückenkopf wird gratis
  erobert und sein Fallout dabei gelöscht. Deine Taktik funktioniert also.

## Annexion — zwei verschiedene Codepfade

Das hatte ich vereinfacht. Es sind zwei Regeln:

- **Größter Klumpen** (`surroundedBySamePlayer`): genau **ein** Feind, keine
  Meeresküste, kein Kartenrand, **kein herrenloser Nachbar** irgendwelcher Art.
- **Alle anderen Klumpen** (`isSurrounded`): **mehrere Feinde erlaubt**, scheitert
  aber an jeder Küste — **auch an einem See** — und am Kartenrand.

Klumpen sind 8-verbundene Flutfüllungen **nur über Grenzfelder**, neu berechnet
alle 20 Ticks (unter 100 Feldern jeden Tick). Die Beute geht an den Feind mit dem
größten **laufenden Angriff**; ohne Angriff an den mit der längsten Grenze.

Wiki sagt „sofort" — es ist ein Zustandswechsel beim nächsten Klumpen-Tick.

## Aktionsraum-Fallen

- **Ein Bauklick innerhalb von 15 Feldern eines gleichartigen Gebäudes wird zur
  Aufwertung**, nicht zum Neubau (`findExistingUnitToUpgrade`). Unsere Aktion
  „Stadt bauen" kann sich also still in „Stadt aufwerten" verwandeln. Muss in
  die Übersetzung.
- **Verteidigungsposten werden bei Eroberung zerstört**, alle anderen Gebäude
  gehen intakt an den Eroberer über.
- `amount` bis 50 auf `BuildUnitIntent` — Nukes lassen sich stapelweise bauen.

## Zahlen, die ich falsch oder gar nicht hatte

```
SAM-Reichweite     150 − 480/(Stufe+5)     Stufe 1 = 70
SAM-Abklingzeit    90 Ticks = 9 s
Silo-Stufe         = gleichzeitige Raketenplätze, NICHT schnelleres Nachladen
Zugzahlung         35.000 verbündet / 25.000 Team / 10.000 selbst
Bahnhofsreichweite 15 bis 110, Schiene max ~155,6
Handelsschiffe     keine harte Obergrenze, weiche Sigmoide bei 400 Schiffen
Gelände            Ebene 80 / Hochland 100 / Gebirge 120
Nationen-Start     12.500 / 18.750 / 25.000 / 31.250 je Schwierigkeit
Granatschaden      200/225/250/275/300 (Würfel 1-5)
Nuke-Tempo         Atom & Wasserstoff 10 · MIRV 15 · Sprengkopf 22
MIRV-Streuung      mindestens 55 Manhattan (nicht 25)
MIRV-Trennpunkt    500 Felder nördlich des Ziels, am Kartenrand gekappt
Embargo temporär   3.000 Ticks = 5 Minuten
Gebäudeabstand     15 → max 7 Gebäude in einem Sprengkopf, 19 in einer Atombombe,
                   163 in einer Wasserstoffbombe
```

## Nationen-KI — nützlich zum Modellieren der Bedrohung

Nuke-Zielbewertung je Gebäudestufe: Silo 50.000 · Stadt 25.000 ·
Hafen/Fabrik 15.000 · Verteidigungsposten 5.000 · **SAM 0**.
Abstandsstrafe `dist × 30`. Auf Mittel wird alles im Umkreis 50 um ein SAM verworfen.

## Regel fürs Projekt

**`vendor/openfront/src/core/` ist die einzige Autorität für Zahlen.**
Die Konstantendatei wird **aus `Config.ts` generiert**, nicht abgeschrieben —
genau durch Abschreiben sind die Wikis auseinandergelaufen.
Fandom ist durchgehend veraltet und unbrauchbar.

## Offen, vor dem Training zu klären

- Ist `waterNukes` in öffentlichen Lobbys an? Code-Standard ist `false`.
- Kriegsschiff sofort nachgeladen nach Transporter-Abschuss? Im Code nicht gefunden.
