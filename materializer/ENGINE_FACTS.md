# OpenFrontIO-Engine an den drei Record-Commits: Faktenblatt

Stand 2026-09-10. Quelle: blobless Klon `scratchpad/engine`, `src/core` je Commit exportiert nach `scratchpad/src_<sha8>/`. Methode: Code gelesen und `git diff` zwischen den Commits. **Nichts ausgeführt.** Pfade sind relativ zu `src/core/`, wenn nicht anders angegeben. Zeilenanker hat ein Skript bestimmt (`scratchpad/anchors.sh`).

Markierungen:
- **[V]** verifiziert, Code gelesen
- **[D]** verifiziert per Diff
- **[A]** Vermutung oder Ableitung
- **[OFFEN]** nicht geklärt

Kürzel: `@115` = 115da032, `@88` = 88cc95d8, `@8b` = 8b45be57.

---

## 0 Commits und Verwandtschaft

- **Commits:**
  - 115da032 vom 2026-08-26 auf `main`
  - 88cc95d8 vom 2026-08-27 auf `origin/v33`
  - 8b45be57 vom 2026-09-03 auf `origin/v33`

  [V] (`git log`, `git branch -r --contains`)
- **Linien:**
  - 88cc95d8 ist Vorfahr von 8b45be57, 5 Commits dazwischen.
  - 115da032 ist **kein** Vorfahr von 88cc95d8. Die gemeinsame Basis ist 7a7ca5be. 147 Commits gibt es nur in 115da032, 42 nur in 88cc95d8.
  - HEAD `4e88d78` liegt auf `main`: 115da032 ist sein Vorfahr, 8b45be57 nicht.
  - Das Datum täuscht also. Inhaltlich steht 115da032 näher an HEAD als die Mehrheits-Commits. [V]
- **88cc95d8 → 8b45be57, `src/core`:** nur zwei gelöschte Zeilen, das Feld `isOvertime` (Schemas.ts:413@88, Game.ts:154@88). `src/core` liest `isOvertime` nirgends; laut grep kommt es nur in Schema und Interface vor. Der Simulationscode ist also gleich. [D] Zur Laufzeit nicht geprüft.
- **88cc95d8 → 8b45be57, sonst:**
  - `resources/maps` unverändert.
  - `package.json` und `package-lock.json` identisch.
  - Übrige Änderungen betreffen nur `src/client`, `src/server`, Tests und `zbin/zb.ts`. zb.ts ändert nur `readCount` des Binärdecoders und betrifft das JSON-Parsing der Records nicht [A]. [D]
- **115da032 → 88cc95d8, `src/core`:** 40 Dateien, +640/−2100 Zeilen. Details in §9. Karten:
  - `sol/map.bin` und `sol/map4x.bin` geändert
  - `yangtzeriver` existiert nur an 115da032
  - rund 25 `manifest.json` geändert

  [D]

---

## 1 Intent-Schemas

**An allen drei gleich.** Der Diff 115↔88 in Schemas.ts betrifft nur die Username-Regex und `trusted`, der Diff 88↔8b nur `isOvertime`. [D]

- **Kacheln sind TileRef, keine x/y.** TileRef ist eine Zahl `y*W + x` (GameMap.ts:162-167@88). Im Schema: `zb.uint()`. [V]

| Intent | Felder | Anker @88 / @8b / @115 |
|---|---|---|
| spawn | `tile: uint` | Schemas.ts:524 / 523 / 541 |
| boat | `troops: float≥0`, `dst: uint` | 531 / 530 / 548 |
| build_unit | `unit: UnitType`, `tile: uint`, `rocketDirectionUp?: bool`, `amount?: uint 1..MAX_UPGRADE_AMOUNT` | 590 / 589 / 607 |
| upgrade_structure | `unit: UnitType`, `unitId: uint`, `amount?` (**keine Kachel**) | 598 / 597 / 615 |
| move_warship | `unitIds: int[]` (nicht leer), `tile: uint` | 615 / 614 / 632 |
| Turn | `turnNumber: uint`, `intents: StampedIntent[]` (mit `clientID`), `hash?: float\|null` | 711 / 710 / 728 |

- **UnitType-Strings, an allen drei gleich** (Game.ts:180@88, 179@8b, 194@115): `"Transport"`, `"Warship"`, `"Port"`, `"Atom Bomb"`, `"Hydrogen Bomb"`, `"Missile Silo"`, `"Defense Post"`, `"SAM Launcher"`, `"City"`, `"MIRV"`, `"Factory"`. Nicht baubar per Intent: Shell, SAMMissile, Trade Ship, MIRV Warhead, Train. [V]
- **Wie Atombombe, Wasserstoffbombe, MIRV, Kriegsschiff und Hafen gebaut werden:** alle über `build_unit` mit `unit` = Typ und `tile` = Klick. Der Klick bedeutet je Typ etwas anderes:
  - Nuke und MIRV: Zielkachel
  - Kriegsschiff: Patrouillenpunkt, eine Wasserkachel
  - Hafen: Klick auf das eigene Land

  Das Transportschiff läuft **nicht** über `build_unit`, sondern über `boat`. Verdrahtung: ExecutionManager.ts:100@88 → ConstructionExecution. [V]

---

## 2 Einrasten

### 2.1 Wann die Engine entscheidet (Zeitversatz)

Mechanik [V]:
- `GameRunner.executeNextTick` hängt die Executions von Turn T an `unInitExecs`.
- `GameImpl.executeNextTick` (481@88, 487@115) tickt **zuerst** alle bestehenden Executions und ruft **danach** `init()` der neuen auf.
- Neue Executions ticken erst im nächsten Tick.
- Der Materialisierer beobachtet vor `executeNextTick(T)`. Er sieht also den Zustand nach Tick T−1, mit `game.ticks() == T`.

| Intent | Die Engine entscheidet in | Anker @88 |
|---|---|---|
| boat | `init`, Tick T, nach allen Executions von T | TransportShipExecution.ts:109 (dst), :119 (src) |
| upgrade_structure | `init`, Tick T | UpgradeStructureExecution.ts:14, :26 |
| move_warship | `init`, Tick T | MoveWarshipExecution.ts:17-38 |
| Bauwerk (City, Defense Post, SAM, Silo, Port, Factory) | `ConstructionExecution.tick`, Tick T+1 | ConstructionExecution.ts:59 |
| Kriegsschiff bauen | `ConstructionExecution.tick` T+1 → `WarshipExecution.init` im selben Tick T+1 | ConstructionExecution.ts:130, WarshipExecution.ts:40-54 |
| Atom, Wasserstoff, MIRV | `ConstructionExecution.tick` T+1 → Nuke- bzw. MirvExecution.`init` T+1 → `.tick` **T+2** ruft `canBuild` | NukeExecution.ts:186; MIRVExecution.ts:73@88 (82@115) |
| spawn | `SpawnExecution.tick`, Tick T+1 | SpawnExecution.ts:40ff |

- **An allen drei gleich.** GameRunner, ConstructionExecution, TransportShipExecution, Upgrade, Warship und MoveWarship sind 115↔88 identisch, die Anker stehen an denselben Zeilen. [D]
- **Folge [A]:** Eine Abfrage vor `executeNextTick(T)` ist nur eine Näherung. Sie kann abweichen, weil zwischen Abfrage und Engine-Entscheidung liegen:
  - Goldeinkommen in T und T+1
  - Gebietsverlust
  - ein Silo-Cooldown, der abläuft
  - **zwei Bauklicks desselben Spielers im selben Turn:** Der zweite sieht das erste Bauwerk und dessen Mindestabstand, eine Vorab-Abfrage liefert für beide dieselbe Kachel.

### 2.2 `canBuild`

Signatur: `canBuild(unitType, targetTile, validTiles = null): TileRef | false`. Anker: PlayerImpl.ts:1411@88 (=@8b, 1462@115).

Ablauf:
1. `canBuildUnitType`: Typ nicht deaktiviert, Gold ≥ Kosten, Spieler lebt.
2. `canSpawnUnitType` (1423@88), je nach Typ:

- **City, Defense Post, SAM, Silo, Factory** → `landBasedStructureSpawn` → `validStructureSpawnTiles(klick)[0]` (1570-1608@88) [V]:
  - Liefert `[]`, wenn `owner(klick)` nicht der Spieler ist. **Die Klickkachel muss eigene sein.**
  - Kandidaten: BFS ab dem Klick über eigene Kacheln mit euklid² < 15², also nur über eigene Kacheln zusammenhängend. `searchRadius = 15` ist hartkodiert (1574).
  - Entfernt jede Kachel mit euklid² < `structureMinDist`² (15²) zu **irgendeinem** aktiven Bauwerk. Das gilt für jeden Besitzer und auch für Bauwerke im Bau (`nearbyUnits(klick, 30, Structures, undefined, true)`).
  - Sortiert stabil nach euklid² zum Klick. Bei Gleichstand gilt die BFS-Reihenfolge. Rückgabe ist die erste Kachel.
- **Port** → `portSpawn` (1513@88) [V]:
  - BFS mit Manhattan ≤ `radiusPortSpawn` (20) ab dem Klick, Terrain wird ignoriert.
  - Filter: eigene Kachel und `isShore`, also Land mit Shoreline-Bit. Das Bit bedeutet „grenzt an irgendein Wasser“, **auch an einen See** (map_generator.go:309@88). Die Engine prüft **nicht** `isOceanShore`.
  - Sortiert nach Manhattan-Abstand. Rückgabe ist die erste Kachel, die zugleich in `validStructureSpawnTiles(klick)` liegt.
  - **Effektiv:** Klick auf eigenes Land, Hafen euklid < 15 und Manhattan ≤ 20 vom Klick, mindestens 15 von jedem Bauwerk.
- **Kriegsschiff** → `warshipSpawn` (1536@88) [V]:
  - Der Klick muss Wasser sein.
  - Rückgabe ist die **Kachel des nächsten eigenen Hafens** (nach Manhattan), der aktiv und fertig ist und in derselben Wasserkomponente liegt. Nicht die Klickkachel.
- **Atom- und Wasserstoffbombe** → `nukeSpawn` (1463@88) [V]:
  - Rückgabe ist die **Silo-Kachel**: das nächste bereite eigene Silo nach Manhattan. Bereit heisst: aktiv, kein Cooldown, nicht im Bau.
  - `false`, wenn eines davon zutrifft:
    - Spawn-Immunität aktiv (`inSpawnPhase` oder `ticksSinceStart < spawnImmunityDuration`, GameImpl.ts:872)
    - Klickkachel unpassierbar
    - Besitzer der Klickkachel ist Teamkamerad (ausser das Spiel ist vorbei)
    - im Teammodus: ein Bauwerk eines Teamkameraden im Radius `outer`
    - kein bereites Silo
  - **Keine Reichweitengrenze.**
  - Der Befund „`canBuild` liefert bei Nukes das Silo“ ist an allen drei Commits bestätigt.
- **MIRV** → wie Nuke, zusätzlich muss die Klickkachel einen Besitzer haben (1430@88). [V]
- **Transportschiff** → `canBuildTransportShip(klick)` (TransportShipUtils.ts:5). Rückgabe ist die **Startküste** (`closestShoreByWater`), nicht die Landekachel. [V]

**Folge:** `canBuild` liefert nur für Bauwerke und Hafen die `res_tile`. Für Kriegsschiff, Nuke, MIRV und Boot liefert es die Abschuss- bzw. Startstelle.

### 2.3 Je Typ: Ergebnis, Upgrade, Boot, Patrouille

**Upgrade**
- An **keinem** der drei Commits wandelt die Engine einen `build_unit` in ein Upgrade um. ConstructionExecution baut neu oder bricht ab (59-63). [V]
- Die Umwandlung passiert **im Client**: `findAndUpgradeNearestBuilding` (ClientGameRunner.ts:1121@88). BuildMenu und BuildPreviewController senden `SendUpgradeStructureIntentEvent`, wenn `buildableUnits().canUpgrade` gesetzt ist. [V]
- Im Record steht ein Upgrade deshalb als `upgrade_structure` mit `unitId`.
- MECHANIK.md:182-185 („Bauklick … wird zur Aufwertung“) beschreibt dieses Client-Verhalten, nicht die Engine.
- `UpgradeStructureExecution.init` (14-35@88) [V]:
  - `unit = game.unit(unitId)`
  - ungültig, wenn die Einheit fehlt oder der Besitzer ein anderer ist
  - sonst bis zu `amount`-mal: `canUpgradeUnit`, dann `upgradeUnit`
  - Für den Materialisierer: `res_unit_id = unitId`, `res_tile = game.unit(unitId).tile()`.
- `canUpgradeUnit(unit)` (1321@88) [V] prüft:
  - Typ ist aufwertbar: Port, Missile Silo, SAM Launcher, City, Factory. Defense Post ist **nicht** aufwertbar (Config.ts:410-505@88).
  - Gold ≥ Kosten, Spieler lebt
  - Einheit nicht im Bau und nicht zur Löschung markiert
  - Einheit gehört dem Spieler
- `findUnitToUpgrade(type, tile)` (1261@88) ist öffentlich, wird aber von keiner Execution benutzt. Es steckt hinter `canUpgrade` im Client (`buildableUnits`, 1367). Ablauf [V]:
  - nächste Einheit dieses Typs mit distSquared ≤ 225, **von jedem Besitzer**, auch im Bau
  - danach `canUpgradeUnit`
  - Liegt eine fremde Einheit am nächsten, ist das Ergebnis `false`.

**Boot:** `TransportShipExecution.init` (55-167@88) [V]
- Ziel ist `owner(klick)` (64).
- Abbruch, wenn eines davon zutrifft:
  - der Spieler hat schon 3 Boote (`boatMaxNumber`)
  - das Ziel ist der Spieler selbst
  - `!canAttackPlayer(ziel)`
- Landekachel: `dst = targetTransportTile(game, attacker, klick)` (TransportShipUtils.ts:33). Das ruft `SpatialQuery.closestReachableShore(owner(klick), attacker, klick, maxDist = 50)` (SpatialQuery.ts:115):
  - Tiefensuche ab dem Klick über **alle** Kacheln, Terrain ignoriert, Manhattan ≤ 50.
  - Treffer ist eine Landkachel mit `isShore`, deren Besitzer `owner(klick)` ist und deren Wasserkomponente zu den Komponenten der Küsten-Grenzkacheln des Angreifers gehört.
  - Gewählt wird die nächste nach Manhattan. Bei Gleichstand gewinnt die zuerst gefundene (striktes `<`).
- Startkachel: `src = canBuild(TransportShip, dst)`.
- `owner(klick)` kann TerraNullius sein, denn Wasser hat Besitzer 0. Ein Klick auf Wasser landet also an herrenloser Küste in der Nähe, und `dst_owner` wird 0. [V für den Code; Häufigkeit OFFEN]

**Kriegsschiff bauen** [V]
- Die Einheit entsteht an der Hafenkachel (`warshipSpawn`), `patrolTile` ist der Klick.
- Bewegung: `patrol()` → `randomTile()`:
  - Klick plus `nextInt(-50, 50)` je Achse. Das ergibt [−50, 49] (PseudoRandom.ts:46; angenommen, `next()` liegt in [0,1)).
  - Nur Wasserkacheln ohne Shoreline, in derselben Komponente.
  - Nach 500 Fehlversuchen wird der Bereich um das 1,5-Fache vergrössert (WarshipExecution.ts:757-824@88).
- Zufall: `PseudoRandom(mg.ticks())` pro WarshipExecution (36).

**Kriegsschiff bewegen:** `MoveWarshipExecution.init` (11-42@88) [V]
- Für jedes eigene aktive Kriegsschiff aus `unitIds` mit `hasWaterComponent(schiff, comp(klick))`: `patrolTile = klick` und `targetTile = undefined`.
- Kein Einrasten. Schiffe in einer anderen Wasserkomponente bleiben unverändert (No-op).

**Atom- und Wasserstoffbombe** [V]
- Das Detonationszentrum ist der Klick (`dst`).
- Innenradius 12 bzw. 80 wird sicher zerstört. Zwischen innen und aussen (30 bzw. 100) entscheidet `rand.chance(2)` mit `PseudoRandom(mg.ticks())` (NukeExecution.ts:63, 117-124).
- Mit `waterNukes` ist die Form anders (67-115).

**MIRV** [V/D]
- An 88 und 8b: Die Sprengköpfe gehen auf Land von `owner(klick)`. Der Besitz wird bei `init` in Tick T+1 festgestellt. Parameter: Umkreis 1500, Mindestabstand 55 Manhattan, bis zu 350 Sprengköpfe. Zufall: `PseudoRandom(ticks + simpleHash(playerId))` (MIRVExecution.ts:50, 149-196@88).
- **An 115 anders:** Ziele werden 20 bis 11 Ticks vor der Trennung gestaffelt gewählt, der Besitz wird neu geprüft, `targetPlayer` hängt an der Einheit, es gibt `cancel`.

**Spawn** [V]
- Kein Einrasten. `getSpawnTiles(klick, false)` liefert die herrenlosen, passierbaren Landkacheln in einer Scheibe mit Radius 4 um den Klick (execution/Util.ts:144).
- Die Klickkachel selbst darf Wasser oder besetzt sein. `spawnTile` wird trotzdem auf den Klick gesetzt.

### 2.4 Von aussen aufrufbar, ohne Zustand oder Zufall zu verändern?

**Zufall:**
- Keine der Abfragefunktionen berührt eine PseudoRandom-Instanz.
- Alle Zufallsgeneratoren der Engine sind Instanzen pro Execution oder Spieler:
  - Warship 36
  - Nuke 63
  - MIRV 50
  - Spawn 31-33
  - `PlayerImpl._pseudo_random` nur in `createAttack` (1661@88)
- In `src/core` gibt es an keinem der drei Commits `Math.random` (grep).

[V]

| Funktion | Zustand | Zufall | Urteil |
|---|---|---|---|
| `canBuild` für Bauwerk oder Port | rein lesend (`mg.bfs` baut ein neues Set, `nearbyUnits` liest nur) | nein | sicher [V] |
| `canBuild` für Atom, Wasserstoff, MIRV | rein lesend | nein | sicher [V] |
| `canBuild` für Kriegsschiff, `getWaterComponent`, `hasWaterComponent` | rein lesend (WaterManager.ts:115-183) | nein | sicher [V] |
| `targetTransportTile` / `closestReachableShore` | beschreibt den geteilten Traversal-Scratch (Generationszähler, SpatialQuery.ts:45); läuft der Zähler über, wird er zurückgesetzt | nein | Code [V]; Ergebnis neutral [A] |
| `canBuild(TransportShip)` / `closestShoreByWater` | füllt den **geteilten HPA-Pfadcache am Graphen** (AStar.WaterHierarchical.ts:259, `setCachedPath`) und den Chain-Cache (PathFinder.ts:42) | nein | Risiko, vermutlich neutral [A] |
| `buildableUnits` | rechnet zusätzlich Rail-Ghost-Pfade (1399-1404) | nein | nicht geprüft, meiden |
| `findUnitToUpgrade`, `canUpgradeUnit`, `game.unit(id)`, `unit.tile()` | rein lesend | nein | sicher [V] |
| `wouldNukeBreakAlliance` (execution/Util.ts:50) | rein lesend (`circleSearch`, `anyUnitNearby`), braucht `allySmallIds` | nein | sicher [V] |

- **Beleg für „vermutlich neutral“ beim Pathfinding:** Die Live-Clients rufen genau diese Funktionen im Worker zu Zeitpunkten auf, die von Client zu Client verschieden sind, und bleiben trotzdem synchron. Aufrufer sind `GameRunner.playerActions` (242) → `buildableUnits` sowie `GameRunner.bestTransportShipSpawn` (314).
- **Optionen, keine Entscheidung:**
  - **Vorab-Abfrage:** billig, aber mit dem Zeitversatz aus §2.1.
  - **Beobachtung:** exakt und ebenfalls ohne Eingriff. Im Tick der Engine-Entscheidung aus `gu.updates[GameUpdateType.Unit]` die neue Einheit herauslesen, erkennbar an der ersten UnitUpdate einer neuen `id` mit `ownerID = sid` und passendem `unitType`:
    - Nuke: `targetTile == klick`
    - Kriegsschiff: `warshipState.patrolTile == klick`
    - Boot: neues Transportschiff, `targetTile` = Landekachel, schon in Tick T
    - Bauwerk: neue Einheit dieses Typs in Tick T+1

---

## 3 Spawnphase

- **Länge:** `numSpawnPhaseTurns()` ist 300 im Mehrspieler-Spiel, 150 bei `isRandomSpawn` und 100 im Singleplayer (Config.ts:673@88, 673@8b, 680@115). An allen drei gleich, an HEAD ebenso. [V]
- **Ende der Phase** [V]:
  - `SpawnTimerExecution` gibt es nur ausserhalb von Singleplayer (GameRunner.ts:109). Sie ruft `endSpawnPhase()`, sobald `mg.ticks() > 300` ist. Das passiert erstmals in Tick 301, also `startTick = 301`.
  - SpawnTimer ist die erste Execution in der Liste und beendet die Phase gleich zu Beginn von Tick 301.
  - Im Singleplayer endet die Phase mit dem Spawn des Menschen (SpawnExecution.ts:96-103).
- **`inSpawnPhase()`** ist `startTick === null` (GameImpl.ts:462@88) [V/A]:
  - Vor `executeNextTick(T)` liefert es `true` für T ≤ 301.
  - Deshalb überspringt materialize.ts:197 auch Turn 301, obwohl dessen Intents schon nach Phasenende initialisiert werden.
- **Andere Intents in der Phase** (bauen, angreifen, Boot, …; alle mit `activeDuringSpawnPhase = false`): Sie werden **nicht verworfen**. Sie bleiben in `unInitExecs` und werden in Tick 301 initialisiert (GameImpl.ts:492-506). [V]

**Spawn-Intent.** Die Wirkung tritt in `SpawnExecution.tick` in Tick T+1 ein.

- **An 88cc95d8 und 8b45be57** [V]:
  - Sperre: `if (!inSpawnPhase() && player.hasSpawned()) return;` (SpawnExecution.ts:64).
  - In der Phase gibt der Spieler alle Kacheln ab und spawnt neu am Klick (73-94). Mehrfaches Klicken heisst umziehen; der letzte wirksame Klick gewinnt.
  - Wirksam für Turn T ≤ **299**, denn Tick T+1 muss ≤ 300 sein.
  - Ab Turn 300 ist der Intent ein No-op, **aber nur für Spieler, die schon gespawnt sind.**
  - **Wer noch nie gespawnt hat, spawnt auch nach der Phase** und steigt spät ein.
  - Mit Random-Spawn ist der Intent ein No-op, sobald der Spieler gespawnt ist (69).
- **An 115da032** [V/D]:
  - Sperre: `if (this.fromIntent && !this.queuedDuringSpawnPhase) return;` (74). ExecutionManager übergibt `fromIntent = true`. `queuedDuringSpawnPhase` ist der Wert von `inSpawnPhase()` bei `init` in Tick T.
  - Wirksam für T ≤ **300**. Alles danach ist No-op, auch für Spieler, die nie gespawnt haben.
  - Zusätzlich `RELAX_MIN_DIST_AT = 750` (26), betrifft nur Zufallsspawns.
  - Das entspricht dem Verhalten an HEAD.
- **Falle** [V]: Der Spieler gibt seine Kacheln ab, **bevor** `getSpawn` sucht. Findet `getSpawn` nichts, weil die Scheibe keine herrenlose Landkachel enthält, steht der Spieler ohne Land da. `spawnTile` bleibt der alte Wert (73-81).
- **Spieler-Objekt vor dem ersten Klick** [V]:
  - Existiert ab `createGame` für alle Menschen und Nationen (GameImpl.addPlayers 197-225).
  - smallID und Team sind gesetzt.
  - Keine Kacheln, keine Grenze, `spawnTile` ist `undefined`, `isAlive()` ist `false`.
  - **Fehlt in `game.players()`**, weil das nur lebende Spieler liefert (635@88). Vorhanden in `allPlayers()` und `playerByClientID()`.
  - Spieler, die im Teammodus als „kicked“ nicht angelegt werden, erzeugen NoOp-Executions (ExecutionManager.ts:52).
  - Tribes und Bots entstehen erst in Tick 1, über `SpawnExecution.addPlayer` (53-57).
- **Beobachtung im Spawnphasen-Tick kodieren** [A]:
  - materialize.ts:205 findet den Spieler über `game.players()` nicht. Stattdessen `game.playerByClientID(cid)` verwenden.
  - Die eigenen Kanäle sind leer.
  - `encodeVec`/`encodeMap` können bei Spielern ohne Grenze werfen (vgl. materialize.ts:222-225).
  - Truppen = `startManpower`.
  - Die Spawn-Immunität ist aktiv; Nukes sind illegal.
  - Die Gebiete der anderen sind Scheiben mit Radius 4.

---

## 4 Desync-Erkennung (Hash)

**Berechnung** (an allen drei gleich per Diff) [V]:
- In `GameImpl.executeNextTick`: Wenn `ticks() % 10 == 0`, fügt die Engine `{type: Hash, tick: ticks(), hash: this.hash()}` hinzu (GameImpl.ts:514-519@88, 520@115).
- Zeitpunkt: nach dem Tick der bestehenden Executions und dem `init` der neuen, **vor** `WaterManager.tick` und vor `_ticks++`.
- Formel: `hash = 1 + Σ` über alle Spieler, in der Reihenfolge der Map, von
  `simpleHash(id)·(troops + numTilesOwned) + Σ_units(tile + simpleHash(type)·id)`
  (GameImpl.ts:605, PlayerImpl.ts:1627, UnitImpl.ts:448).
- Das Ergebnis ist ein float.

**Wie der Hash in den Record kommt** [V]:
- Der Client sendet `{turnNumber: hu.tick, hash}` (ClientGameRunner.ts:875@88, Transport.ts:656@88).
- Der Server setzt `turnNumber = turns.length` (GameServer.ts:1620@88).
- Er speichert `turns[N−10].hash` nur, wenn **mehr als ein aktiver Client** da ist **und alle übereinstimmen** (2100, 2114@88).
- Der Replay-Check im Client vergleicht mit `replayTurns[turnNumber].hash` (LocalServer.ts:210@88).
- Records behalten leere Turns, die einen Hash tragen (Util.ts:308). `decompressGameRecord` füllt Lücken auf (339).

**Zuordnung Turn ↔ Tick:**
- Der GameRunner verbraucht pro `executeNextTick` genau einen Turn; Tick 0 gehört zu Turn 0.
- Daraus folgt: `record.turns[T].hash` (T durch 10 teilbar) ist gleich dem `HashUpdate` mit `tick == T` aus dem `executeNextTick` für Turn T.
- **Off-by-one:** Das Callback-Feld `gu.tick` ist dort T+1 (GameRunner.ts:211, nach `_ticks++`). Vergleichen mit `HashUpdate.tick`, nicht mit `gu.tick`.

[V Code; A Laufzeit]

**Headless:**
- Im Runner-Callback `gu.updates[GameUpdateType.Hash]` lesen: ein Array mit 0 oder 1 Eintrag. Der Enum-Wert ist an allen drei Commits 13.
- Prüfen mit `=== record.turns[T].hash`.
- Ein fehlender Hash (ein einziger aktiver Client oder uneinige Clients) ist kein Fehler.
- materialize.ts:94 wertet heute nur `errMsg` aus.

[V]

---

## 5 Kachelzustand

- **GameMap `state`**, Uint16 pro Kachel (GameMap.ts:133-136, an allen drei gleich) [V/D]:
  - Bits 0–11: smallID des Besitzers (`PLAYER_ID_MASK 0xfff`)
  - Bit 12: unbenutzt
  - Bit 13: Fallout
  - Bit 14: Verteidigungsbonus
  - Bit 15: reserviert
- **`terrain`**, Uint8 (122-130):
  - Bit 7: Land
  - Bit 6: Shoreline
  - Bit 5: Ozean
  - Bits 0–4: Magnitude; 31 heisst unpassierbar
- **`setDefenseBonus`** wird in `src/core` nirgends aufgerufen (grep an allen drei). Headless ist Bit 14 also immer 0. [V]
- **„Untere 16 Bit“** heisst genau dieses `state`: Besitz, Fallout, Bonus.
- **`packedTileUpdates`** [V]:
  - Typ `Uint32Array`, flache Paare `[tileRef, packed]`.
  - `packed = (state & 0xffff) | (terrainByte << 16)` (GameImpl.ts:530-536@88).
  - Der Doc-Kommentar in GameUpdates.ts:23-28 („state uint16“) ist **veraltet**: Die Bits 16–23 tragen das Terrain.
- **Vollständigkeit.** `recordTileUpdate` wird aufgerufen in [V]:
  - `conquer` (743-762; setzt auch Fallout zurück)
  - `relinquish` (764-780)
  - `setFallout` (258-267; nur beim Setzen, das Löschen per `setFallout(false)` kehrt früh zurück)
  - `setWater` (269-280)
  - Terrainänderungen im WaterManager (522-525)

  `setOwnerID` wird nur in `conquer` und `relinquish` aufgerufen (grep). Die Liste ist also **vollständig** für Besitz, Fallout und Wasserumwandlung:
  - Nuke: `relinquish` plus `setFallout` bzw. `queueWaterConversion` (NukeExecution.ts:368-394)
  - Annexion und Enklaven: über `conquer`
  - Spawn: über `conquer`/`relinquish`

  Dieselbe Kachel kann in einem Tick mehrfach vorkommen. Die Einträge in Reihenfolge anwenden. [V]
- **Pro Tick** wird die Liste am Tickanfang geleert (GameImpl.ts:483) und vom GameRunner nach dem Tick abgeholt (GameRunner.ts:202). [V]

---

## 6 Spielertabelle

- **Spieler-API** [V]:
  - `smallID()` (PlayerImpl.ts:359@88)
  - `clientID()`: `null` bei Nationen und Bots
  - `id()`: PlayerID als String
  - `team()`: Team oder `null` (1151)
  - `type()`: PlayerType `"HUMAN"`, `"NATION"` oder `"BOT"`
  - `allies()`: **nur Allianzpartner, keine Teamkameraden** (646)
  - `isOnSameTeam` (1155); Bot-Teams zählen nicht
  - `isFriendly`: Team oder Allianz
  - `isAlive()`: mindestens eine Kachel (614)
  - `hasSpawned()`, `spawnTile()`, `isDisconnected()`
- **Game-API** [V]:
  - `playerBySmallID(id)`: 0 ist TerraNullius (GameImpl.ts:235)
  - `playerByClientID` (695), `player(id)`, `allPlayers()`
  - `players()`: nur lebende Spieler
- **smallID-Vergabe** [V]:
  - `nextPlayerID` beginnt bei 1 und zählt in `addPlayer` hoch (662).
  - FFA: Menschen in der Reihenfolge von `gameStart.players`, danach Nationen.
  - Teammodus: in der Reihenfolge von `assignTeams`, nicht „Menschen zuerst“.
  - Tribes ab Tick 1.
  - **Maximum 4095**: `setOwnerID` wirft darüber (GameMap.ts:283-291).
- **PlayerIDs der Menschen:** `random.nextID()` aus `PseudoRandom(simpleHash(gameID))`, in Reihenfolge (GameRunner.ts:48-61). materialize.ts:85-88 macht es genauso. Nationen und Tribes bekommen ihre IDs ebenfalls per `nextID()` (NationCreation.ts:40, TribeSpawner.ts:85). [V]
- **Teamzuteilung 115 ≠ 88:** Duos, Trios und Quads werden verschieden verteilt (`isDuosTriosQuads` gibt es nur an 115, TeamAssignment.ts:9@115). Team und smallID-Reihenfolge können an 115 also anders ausfallen. [D]

---

## 7 Einheiten

- **Lesen pro Tick** [V]:
  - `game.units(...types)`: alle aktiven Einheiten aller Spieler (GameImpl.ts:308-348)
  - `game.unit(id)`: nur aktive; `removeUnit` löscht den Eintrag (1043)
  - `player.units()`
  - Unit-Methoden: `id()`, `type()`, `level()`, `owner()`, `tile()`, `isActive()`, `isUnderConstruction()`, `targetTile()`, `warshipState().patrolTile`
  - Unit-IDs laufen ab 1 fortlaufend (`nextUnitID`).
- **UnitUpdate** (GameUpdates.ts:176@88) [V]:
  - Felder: `id`, `unitType`, `ownerID` (smallID), `lastOwnerID`, `pos`, `lastPos`, `isActive`, `level`, `underConstruction`, `targetTile`, `warshipState`, `transportShipState`, `troops`, `health`, …
  - Wird gesendet bei:
    - Bau (`buildUnit`, PlayerImpl.ts:1255)
    - Wechsel des Bau-Status (UnitImpl.ts:441)
    - Level hoch oder runter (625-642)
    - Besitzwechsel (223)
    - `delete` mit `isActive = false` (297)
    - `move`, aber nur bei nicht planbasierten Einheiten (168-175 → `GameImpl.onUnitMoved`)
    - Truppen, HP, Löschmarke, Silo-Start
- **Planbasierte Einheiten** senden keine Positionsupdates; ihre Position steckt in `packedMotionPlans` [V]:
  - Transportschiff (TransportShipExecution.ts:149)
  - Handelsschiff, Zug
  - Atom, Wasserstoff, MIRV-Sprengkopf (`NukeExecution.recordMotionPlan`)

  Kriegsschiff und MIRV-Träger senden Positionsupdates.
- **Reicht das für ein billiges Einheiten-Log?** [A]
  - Entstehen, Level, Besitzer und Tod: ja, direkt aus den Updates.
  - Position von Transportschiffen und Nukes: nur über das Dekodieren der Motion Plans oder durch direktes Lesen von `unit.tile()`. Headless ist das Lesen billig.

---

## 8 Karten- und Legalitäts-APIs

**Werte, an allen drei gleich** (Config-Diff 115↔88 nur Spectator und SAM-Upgrade; 88↔8b keiner; an HEAD dieselben Werte) [V/D]:

| Wert | Grösse | Anker |
|---|---|---|
| `structureMinDist` | 15 | Config.ts:1025@88, 1051@115 |
| `radiusPortSpawn` | 20 | 836@88, 843@115 |
| Upgrade-Radius | = `structureMinDist` = 15 | PlayerImpl.ts:1276 |
| Suchradius für Bauwerke | 15, hartkodiert | 1574 |
| Landesuche Boot | `maxDist` 50 Manhattan | SpatialQuery.ts:119 |
| `warshipPatrolRange` | 100 | 1033@88 |
| `minDistanceBetweenPlayers` | 30, nur für Zufallsspawns | 640@88 |
| Spawnscheibe | Radius 4 | execution/Util.ts:144 |
| `boatMaxNumber` | 3 | Config.ts:667@88 |
| Nuke-Radien innen/aussen | Atom 12/30, Wasserstoff 80/100, Sprengkopf 12/18 | 956@88 |

**Die 8 Bits je Zelle**

- **Bit 0, mindestens eine eigene Kachel:** `ownerID(t) == sid`; `player.tiles()`.
- **Bit 1, eigene Kachel ≥ 15 von jedem Bauwerk:**
  - Kein aktives Bauwerk mit euklid² < 225, egal welcher Besitzer, auch im Bau.
  - API: `game.nearbyUnits(t, 15, Structures.types, undefined, true)`. Das liefert distSquared **≤** 225; die Grenze 225 selbst muss man ausfiltern.
  - Zum Bauen kommt hinzu: Die Klickkachel ist eigene, und das Ziel ist innerhalb Radius 15 über eigene Kacheln erreichbar.
- **Bit 2, eigene bebaubare Küste:**
  - Die Engine verlangt `isShore` (Shoreline-Bit, jedes Wasser, **auch See**), nicht `isOceanShore`.
  - Dazu die Bedingung aus Bit 1.
- **Bit 3, fremde, nicht verbündete Küste per Wasser erreichbar.** Alle Bedingungen zusammen:
  - `isShore(t)`
  - Besitzer X ≠ `sid`
  - `canAttackPlayer(X)`: nicht Team, nicht Allianz, nicht immun
  - `getWaterComponent(t)` liegt in `{getWaterComponent(b) | b ∈ player.borderTiles(), isShore(b)}`
  - **Herrenlose Küste** (Besitzer 0) ist ebenfalls ein legales Bootsziel.
- **Bit 4, Wasser in der Komponente eines eigenen aktiven Hafens.** Alle Bedingungen zusammen:
  - `isWater(t)`
  - `comp = getWaterComponent(t)` ist nicht `null`
  - es gibt einen eigenen Hafen, aktiv und nicht im Bau, mit `hasWaterComponent(port.tile(), comp)`
- **Bit 5, Wasser:** `isWater = !isLand`. Ozean und See.
- **Bit 6, herrenloses passierbares Land:** `!hasOwner && isLand && !isImpassable`. Ein Spawn-Klick ist legal, wenn die Scheibe mit Radius 4 mindestens eine solche Kachel enthält.
- **Bit 7, nukebar.** Alle Bedingungen zusammen:
  - `!isImpassable`
  - Besitzer ist kein Teamkamerad
  - im Teammodus kein Bauwerk eines Teamkameraden im Radius `outer`; das hängt vom Typ ab (30 bzw. 100)
  - mindestens ein bereites Silo
  - keine Spawn-Immunität
  - genug Gold

  **Keine Reichweite, kein Ortsbezug zum Silo.** MIRV verlangt zusätzlich einen Besitzer der Zielkachel. Ein Allianzbruch ist Folge, keine Bedingung (NukeExecution.ts:133-182).

**Wasserkomponenten**
- API: `game.getWaterComponent(tile): number | null`, `hasWaterComponent(tile, id)`, `getWaterComponentSize(tile)` (GameImpl.ts:1254-1262 → WaterManager.ts:115-198@88). [V]
- Grundlage ist der Graph auf der 2×-Minimap.
- Landkacheln bekommen die Komponente eines Wasser-Nachbarn (1 oder 2 Schritte). Eine Küste an zwei Gewässern erhält die zuerst gefundene.
- `hasWaterComponent` prüft alle Nachbarn: Ein Hafen zwischen zwei Gewässern zählt für beide.
- Der Graph wird nur nach Wasserumwandlung (Water-Nukes) neu gebaut, gedrosselt nach `WATER_GRAPH_REBUILD_INTERVAL` (WaterManager.ts:9@88). Ohne `waterNukes` bleibt er stabil.
- Ob die IDs nach einem Neubau stabil bleiben: **[OFFEN]**.
- Mit `disableNavMesh` liefert `getWaterComponent` 0 und `hasWaterComponent` `true`.

---

## 9 Unterschiede zwischen den Commits (Punkte 1–8) und Pakete

**88cc95d8 ↔ 8b45be57**
- Nichts Simulationsrelevantes. `isOvertime` steht nur im Schema und wird nicht gelesen.
- Karten und Pakete identisch.

[D]

**115da032 ↔ 88cc95d8.** Die APIs und Werte aus §1, §2, §4–§8 sind gleich:
- `canBuild`, `validStructureSpawnTiles`, `portSpawn`, `warshipSpawn`, `nukeSpawn`
- `targetTransportTile`/`SpatialQuery`
- Hash-Formel und -Takt, Tile-Packing
- alle Config-Werte

Anders ist [D]:
- Spawn-Sperre (§3) und `RELAX_MIN_DIST_AT`
- MIRV-Staffelung, `targetPlayer`, `cancel`
- SAMLauncherExecution (+289 Zeilen), `dynamicSamRange`, `samLauncherState` in UnitImpl
- WaterManager und ConnectedComponents (Neubau-Logik)
- AttackExecution:
  - an 115 `startTroops = removeTroops(...)`, an 88 nicht
  - Annexionsschleife bis 100 Durchläufe mit Fortschrittsprüfung statt fest 10
- PlayerExecution: `isEnclosed` gibt es nur an 115
- `GameMap.isOnEdgeOfMap`: Unpassierbares zählt an 115 als Rand
- TeamAssignment (Duos, Trios, Quads)
- `UsernameSchema`: 115 erlaubt `-`. 88 kann Namen mit Bindestrich aus 115-Records nicht parsen; der Materialisierer fällt dann auf die rohen Daten zurück.
- PortExecution: `tradeShipSpawnRate` wird an 115 pro Level neu berechnet, vermutlich gleichwertig [A].

**`package.json`/`package-lock.json`**
- Gleich zwischen 88 und 8b. [D]
- 115 ↔ 88 [D]:
  - `package.json`: `dompurify` ^3.4.13 → ^3.4.12, `nanoid` ^5.1.16 → ^5.1.11
  - Lock zusätzlich: `brace-expansion`, `ip-address`, `undici`, `libc`-Felder
- Keines davon liegt vermutlich im Simulationspfad [A]. `nanoid` wird in Util.ts für `generateID` verwendet; ob das im Tick aufgerufen wird, habe ich nicht geprüft.

---

## 10 Abweichungen vom Entwurf (ZIELWAHL_ENTWURF.md)

1. **Spawn nach der Phase** (§1.2, §6, §7.2 P1: „No-op an HEAD“):
   - Gilt nur an 115da032.
   - An 88cc95d8 und 8b45be57, also den 11k Partien, **spawnen Menschen, die nie gespawnt haben, auch nach der Phase**. No-op ist der Intent dort nur für Spieler, die schon gespawnt sind.
   - Der letzte wirksame Umzug liegt bei Turn ≤ 299 (88/8b) bzw. ≤ 300 (115), nicht bei „letzter Klick in der Phase“ mit der Grenze 301.
2. **Port** (§6, „eigene Küste im Manhattan-Radius 20“, R = 20): Effektiv gilt euklid < 15 um einen **eigenen** Klick, über eigene Kacheln zusammenhängend; ausgewählt wird nach Manhattan. Die Küste kann auch Seeküste sein. „Ozeanküste“ in Bit 2 ist strenger als die Engine.
3. **Upgrade** (§6, „bei Upgrade die Kachel der aufgewerteten Einheit“ für Bauklicks): Die Engine wandelt `build_unit` nie in ein Upgrade um. Upgrades sind eigene `upgrade_structure`-Intents; `res_kind` ergibt sich aus dem Intent-Typ, `res_tile` aus `unit(unitId).tile()`.
4. **`canBuild` liefert nicht nur bei Nukes eine Fremdkachel:**
   - Kriegsschiff: Hafenkachel
   - Boot: Startküste
   - MIRV: Silo

   Die Wahl „Klickkachel als Lernziel“ für Kriegsschiff und Nuke bleibt richtig.
5. **`res_tile` „im Entscheidungstick per Engine-Abfrage“** (§7.2, §11 Punkt 8): Die Engine entscheidet in T, T+1 oder T+2, siehe §2.1. Eine Vorab-Abfrage ist nur eine Näherung.
6. **Tier 1 „untere 16 Bit (Besitz + Fallout)“:** korrekt. Dazu kommen Bit 14 (headless immer 0) und in den Updates das Terrain-Byte in den Bits 16–23, das bei Water-Nukes nötig ist.
7. **§7.4, „`tickExecutionDuration` gibt es nur sicher an HEAD“:** Das Feld gibt es an allen drei Commits (GameRunner.ts:153-159, 219).
8. **Boot „Manhattan ≤ 50“:** bestätigt. Die Suche ignoriert aber das Terrain, und die Wasserkomponenten kommen aus der Minimap.
9. **§10 „Record-Commits fehlen im Klon“:** erledigt. Alle drei liegen im Klon, Boot-Einrasten, Spawn-Sperre und Tile-Packing sind jetzt an den Record-Commits gelesen.
10. **Implizite Annahme „115da032 ist eine ältere Version derselben Linie“:** falsch, siehe §0.

---

## 11 Fallen für den Determinismus

- **F1:** 115da032-Partien mit der Engine von 88/8b nachspielen führt fast sicher zum Desync: Truppenabzug beim Angriff, Annexion, Spawn, MIRV, SAM, Karte `sol`. Diese 5 Partien brauchen eigene Engine und Karten oder werden ausgeschlossen. [A, stark durch Diff gestützt]
- **F2:** Statischer Zustand überlebt den Wechsel zur nächsten Partie, wenn mehrere Partien im selben Prozess laufen (`--list`) [A]:
  - `TransportShipExecution._staggerCounter` (TransportShipExecution.ts:32)
  - `TradeShipExecution._staggerCounter` (TradeShipExecution.ts:25)
  - `NationMIRVBehavior.recentMirvTargets` (NationMIRVBehavior.ts:30)

  Einschätzung:
  - Der Stagger steuert, wann Schiffe nach einem Neubau des Wassergraphen neu planen (PathFinder.ts ~127-190). Betroffen wären nur Partien mit Water-Nukes.
  - `recentMirvTargets` ist nach PlayerID geschlüsselt, und die IDs sind pro Partie zufällig. Die Wirkung ist praktisch null.

  Ob der Browser-Worker pro Partie neu startet: **[OFFEN]**.
- **F3:** Abfragen mit Pfadsuche (`canBuild(TransportShip)`, `closestShoreByWater`, `buildableUnits`) füllen geteilte Caches. Vermutlich neutral, siehe §2.4. [A]
- **F4:** Zeitversatz der Vorab-Abfrage, siehe §2.1.
- **F5:** `game.players()` filtert Tote und Nicht-Gespawnte heraus. Das betrifft Spawn-Samples und materialize.ts:205.
- **F6:** Hash-Vergleich mit `HashUpdate.tick` statt `gu.tick`. Ein fehlender Hash im Record ist kein Fehler.
- **Kein Risiko:** Keine Abfragefunktion verbraucht Zufall. Es gibt kein `Math.random`, und alle RNGs sind Instanzen pro Execution. [V]

---

## 12 Offen

- **Nichts ausgeführt.** Diese Punkte sind nur aus dem Code abgeleitet:
  - Hash-Zuordnung T ↔ `HashUpdate.tick`
  - Neutralität der Abfragen
  - Wirkung von F2
- **Vorschlag für die Messung:** 1 Record von 88cc95d8 mit gesetzten Hashes, Vergleich der Turn-Hashes in drei Läufen:
  - (a) nackt
  - (b) mit allen `res_*`-Abfragen in jedem Tick
  - (c) als zweite Partie im selben Prozess
- Stabilität der Wasserkomponenten-IDs nach einem Neubau des Graphen.
- Ob einer der 98 Spawns nach der Phase von Menschen stammt, die nie gespawnt haben. An 88/8b wären das echte Spawns.
- `nanoid` im Tickpfad.
