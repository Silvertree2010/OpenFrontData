# Materialisierer v2 — Aufbau und Format

Stand 2026-09-10. Grundlage:
- `docs/ZIELWAHL_ENTWURF.md` §7 (Feldliste), §6 (Lernziel je Typ), §7.3 (Kanarienlauf)
- `ENGINE_FACTS.md`: Engine an den Record-Commits gelesen, mit Belegen

Wo dieses Dokument vom Entwurf abweicht, gilt dieses Dokument. Der Grund steht jeweils dabei.

Der alte Materialisierer (`env/materialize.ts`) wird nicht weiterentwickelt. Er dient nur noch als Referenz im Kanarienlauf (Byte-Tor). `src/obs.ts` ist `env/obs.ts`, geändert sind nur die zwei Importpfade. **Nicht anfassen**, sonst sind die `.maps` nicht mehr byte-gleich.

---

## 1 Ablage im Repo

```
materializer/
  DESIGN.md            dieses Dokument (Format ist hier verbindlich)
  ENGINE_FACTS.md      Engine-Befunde an 8b45be57 / 88cc95d8 / 115da032
  README.md            Bauen, Starten, Flotte, Kanarienlauf
  src/
    materialize.ts     Kern: eine Partie → Dateien. Aufruf: <record.json> <outdir>
    obs.ts             Kopie von env/obs.ts, NICHT verändern
    res.ts             P1: res_tile / res_kind / res_unit_id per Beobachtung
    tier1.ts           P2: Besitzwechsel-Log, Einheiten-Log, Checksummen
    cells.ts           P3: Zellfakten 180x90
    io.ts              atomare Dateien, .ok-Marke, Fertig-Prüfung
    timing.ts          Stufen-Zeitmessung
    dispatch.ts        Eingang des Containers: Liste, SHARD, Commit-Wahl, Worker-Pool
  py/
    reader.py          liest hdr/meta/maps/cells (Grundlage für dataset.py und Kanarienlauf)
    tier1.py           Rekonstruktor für own.zst/units.zst, prüft .chk bit-genau
  canary/  select.py, canary.sh, gates.py
  docker/  Dockerfile
  fleet/   hosts.example, launch.sh, status.sh
```

Importpfade in `src/`: `../../vendor/openfront/src/...`. Im Image und in der Dev-Umgebung liegt pro Commit ein Baum `<root>/w/<sha8>/` mit `vendor/openfront/` (Engine-Worktree) und `materializer/` daneben. Die tsconfig-Optionen spiegeln die der Engine; `useDefineForClassFields: false` ist Pflicht (siehe `tsconfig.json` im Repo-Wurzel).

**Ein Prozess pro Partie.** Statischer Engine-Zustand überlebt sonst den Wechsel zur nächsten Partie (ENGINE_FACTS §11 F2). `dispatch.ts` startet pro Partie einen Kindprozess. Im Image ist `materialize.ts` pro Commit mit esbuild zu einem Bündel vorgebaut, damit der Start keine Sekunden kostet (`useDefineForClassFields: false` muss dabei erhalten bleiben).

---

## 2 Ablauf je Partie

1. Record lesen, `GameRecordSchema.safeParse`, sonst roh (Namen mit `-` aus 115-Records scheitern am Schema von 88/8b). Dann `decompressGameRecord` (füllt leere Züge auf, an allen drei Commits gleich).
2. Commit prüfen: Der Record-Commit muss dem Commit des Engine-Baums entsprechen (erste 8 Zeichen), sonst `.err`.
3. Vorab-Scan der Züge: letzter **wirksamer** Spawn-Klick je Mensch in der Phase. Wirksam heisst Turn ≤ `numSpawnPhaseTurns() - 1` an 88/8b und ≤ `numSpawnPhaseTurns()` an 115 (ENGINE_FACTS §3). Die Grenze kommt aus einer Tabelle je Commit. Bei `randomSpawn` gibt es keine Spawn-Samples.
4. Engine aufsetzen wie im alten Materialisierer: Config, Terrain, `PseudoRandom(simpleHash(gameID))`, Menschen, Nationen, `createGame`, `GameRunner`. Den Runner-Callback so bauen, dass er pro Tick die `GameUpdateViewData` festhält (Tile-Updates, Unit-Updates, Hash-Update) und `errMsg` meldet.
5. Pro Zug T, **vor** `addTurn`/`executeNextTick` (Beobachtung = Zustand, den der Spieler sah, `game.ticks() == T`):
   - **Spawn:** Der vorgemerkte letzte wirksame Spawn-Klick eines Menschen wird emittiert, als `kind: "spawn"`, `spawn_final: 1`. Den Spieler über `game.playerByClientID(cid)` holen, denn `game.players()` enthält nur Lebende.
   - **Späte Spawns an 88/8b:** Ein Spawn-Intent nach der Phase von einem Menschen mit `!hasSpawned()` ist dort wirksam. Er wird emittiert, als `kind: "spawn"`, `spawn_late: 1`.
     - Je Mensch gibt es höchstens einen offenen späten Spawn, denn `hasSpawned()` wird erst in T+1 gesetzt.
     - Weitere Klicks, bevor er aufgelöst ist, werden nicht emittiert.
     - Alle anderen Spawn-Intents nach der Phase werden ebenfalls nicht emittiert und zählen nicht als „gehandelt“.
     - An 115 gibt es keine späten Spawns.
   - **Züge:** nur wenn `!game.inSpawnPhase()`, gleiches Tor wie bisher. Alle Intents von Menschen ausser `mark_disconnected` und `spawn`. Angriffe werden zusammengefasst (§4).
   - **Nichtstun:** Gleiches Tor wie bei den Zügen. Für jeden lebenden, verbundenen Menschen ohne Intent in diesem Zug, falls `(turnNumber + phase(cid)) % NOOP_EVERY == 0`. Die Phase ist dieselbe wie im alten Code, `abs(simpleHash(cid)) % NOOP_EVERY`.
6. `addTurn`, `executeNextTick`, Zeit messen. Danach mit den Updates dieses Ticks:
   - offene `res`-Auflösungen weiterführen (§5.2)
   - Tier-1-Hook füttern (§8)
   - Hash prüfen (Punkt 7)
7. **Desync-Prüfung:** `gu.updates[GameUpdateType.Hash]` enthält 0 oder 1 Eintrag.
   - Der Eintrag mit `tick == T` wird mit `record.turns[T].hash` verglichen (strikt `===`). Nicht `gu.tick` verwenden, das ist T+1.
   - Fehlt der Hash im Record, ist das kein Fehler.
   - Zählen: bestätigte und fehlende Hashes. Dazu kommt `hash.digest`, ein CRC32 über die Folge (Tick, Hash) aller bestätigten Hashes. Damit prüft das Treue-Tor, dass zusätzliche Abfragen die Simulation nicht verändern.
   - **Schnitt bei `last_ok`** = letzter bestätigter Hash-Tick. Er gilt beim ersten Mismatch (`desync`) und ebenso bei einem Tick-Fehler (`tick_error`, §3). Hat der Record gar keine Hashes, liegt `last_ok` einen Tick vor dem Fehler. Nach dem Schnitt stoppt das Replay und schneidet alles ab, was danach liegt:
     - Samples mit `tick > last_ok` fallen weg. `.maps` und `.cells` werden am Byte-Offset des ersten verworfenen Samples abgeschnitten; die Samples liegen in Tick-Reihenfolge, der Kern führt die Offsets mit.
     - Zusammengefasste Folgeklicks zählen nur bis `last_ok`.
     - Eine `res`-Auflösung, deren Fenster über `last_ok` hinausreicht, wird `res_kind 2`.
     - Tier 1 bekommt `valid_until = last_ok` (in `.ok` und `hdr`). `tier1.py` liefert danach keinen Zustand mehr. `.chk`-Ticks nach `last_ok` werden gestrichen und gezählt.
8. Ende: Offene Auflösungen gelten als ungültig (`res_kind 2`). Dann die Dateien fertig schreiben (§5), die `.ok` zuletzt.

**Umsetzung, abweichend vom ersten Entwurf (Kern-Bericht 10.09.):**
- Eine `.ok` mit 0 Samples hat keine `.maps` und keine `.meta.zst`.
- Ein Spawn gilt, wenn `spawnTile == klick` und der Spieler danach eine Kachel im 9×9-Quadrat um den Klick besitzt.
- Fasst ein Spieler in einem Zug ohne eigene Samples Klicks in k Fenster zusammen, teilen sich diese k Fenster das `w_tick` von 1.
- `res_unit_id` wird bei jeder neuen Einheit gesetzt.
- `last_ok` ist um einen Tick vorsichtig. Das ist gewollt, es kostet nur Samples am Rand.
- Kandidaten für die `.chk`-Ticks sind alle Menschen-Intents ausser `mark_disconnected`.

**Tick-Konvention, für alle Dateien gleich:** „Tick t“ heisst immer der Zustand mit `game.ticks() == t`, also vor `executeNextTick` für Zug t. Das ist derselbe Zustand, den ein Sample mit `tick: t` sieht. Keyframes, Checksummen und `valid_until` verwenden diese Konvention. `spawnEndTick` ist der kleinste t, für den `!game.inSpawnPhase()` gilt.

Die Beobachtung ist exakt die alte:
- `scanTick` einmal pro Tick
- `encodeVec(game, player, 24)`
- `encodeMap(player, allies, buf)`, wobei `allies` ein Set der smallIDs aus `player.allies()` ist
- `round((clamp(x,-1,1)+1)*127.5)`
- `zstdCompressSync` mit Standardlevel

Kontext und Karte werden je Spieler und Tick einmal gebaut und von allen Samples dieses Spielers in diesem Tick geteilt.

---

## 3 Fehlerbehandlung

- **Pro Sample:**
  - Jeder `emit` steckt in try/catch.
  - Fehler werden je Art gezählt (`act`, `noop`, `spawn`, `res`, `cells`), zusammen mit der ersten Meldung.
  - Der Kartenblock wird erst geschrieben, wenn Metaobjekt und Block fertig gebaut sind. Kein halbes Sample, `.maps` und Metazeilen bleiben im Gleichschritt.
  - Scheitert nur `cells` für ein Sample, bleibt das Sample erhalten, mit `cell: -1`.
- **Pro Tick:** Ein Fehler in `executeNextTick` oder ein `errMsg` im Runner beendet das Replay. Er wird behandelt wie ein Desync: Schnitt bei `last_ok` (§2.7), die `.ok` bekommt `tick_error` mit der Meldung, die Samples davor bleiben erhalten.
- **Pro Partie:**
  - `.err` bei allem, was vor dem ersten Tick scheitert (Record unlesbar, falscher Commit, Setup), und bei einem Absturz des Prozesses (schreibt der Dispatcher). Die Liste läuft weiter.
  - `RETRY_ERR=1` rechnet `.err`-Partien neu.
  - `.none` nur, wenn die Partie **ehrlich leer** ist: 0 Samples, 0 Fehler, kein Desync, kein `tick_error`. Sonst gibt es eine `.ok` mit 0 Samples und dem Grund.

---

## 4 Samples und Gewichte

| Art (`kind`) | wann | `w` |
|---|---|---|
| `act` | Intent eines Menschen nach der Spawnphase | 1 |
| `act`, Angriff | erster Klick auf `targetID` je Spieler. Folgeklicks auf **dasselbe** `targetID` mit `t − t0 < MERGE_TICKS` (30) zählen hinein; t0 ist der Zug des ersten Klicks. Danach öffnet der nächste Klick ein neues Fenster | Zahl der Klicks, ≥ 1 |
| `noop` | siehe §2.5 | `NOOP_EVERY` (200) |
| `spawn` | letzter wirksamer Spawn-Klick in der Phase, oder später Spawn | 1 |

`w` ist der Kehrwert der Aufnahmewahrscheinlichkeit bzw. die Zahl der vertretenen Klicks. Es gewichtet die Frage **welche** Aktion. Ein Angriff auf ein anderes Ziel ist ein eigenes Sample, `targetID` null (herrenlos) zählt als eigenes Ziel.

Beim Zusammenfassen bekommt die gepufferte Metazeile des ersten Klicks `w += 1`, `merged += 1`, und der Zug landet in `merged_turns`. Es entsteht kein neuer Kartenblock. Ein zusammengefasster Klick gilt als „gehandelt“, in diesem Zug gibt es also kein Nichtstun-Sample für den Spieler.

**`w_tick`** gewichtet die Frage **ob** gehandelt wird, in der Einheit „Spieler-Ticks“. Damit ist der Anteil Σ w_tick(act) / Σ w_tick(alle) eine erwartungstreue Schätzung von P(handeln | Tick):
- `noop`: `NOOP_EVERY`.
- `act` und `spawn`: Die Samples eines Spieler-Ticks teilen sich 1, also `1 / Zahl der Samples dieses Spielers in diesem Tick`. Ein zusammengefasster Angriff trägt zusätzlich je Zug aus `merged_turns` 1 bei, sofern der Spieler in diesem Zug kein eigenes Sample hat.

---

## 5 Dateien je Partie (flach in `/out`)

Alle Datendateien entstehen als `<name>.tmp` und werden erst am Ende umbenannt. Die `.ok` wird zuletzt geschrieben (tmp + rename).

| Datei | Inhalt | Prio |
|---|---|---|
| `<gid>.hdr.json` | Spielkopf (§5.1) | P1 |
| `<gid>.maps` | wie bisher: `[u32 LE Länge][zstd(u8[18·90·180])]` je Sample, 1:1 in der Reihenfolge der Metazeilen | P1 |
| `<gid>.meta.zst` | zstd(JSONL), eine Zeile je Sample (§5.2) | P1 |
| `<gid>.cells` | je räumlichem Sample ein Block `[u32 LE Länge][zstd(owner_major u16 LE[16200] ‖ own_frac u8[16200] ‖ legal u8[16200])]`, Zeilenhaupt 90×180 wie die Karten. Die Metazeile trägt `cell` = Blockindex | P3 |
| `<gid>.own.zst`, `<gid>.units.zst`, `<gid>.chk` | Tier 1 (§5.3), nur für ausgewählte Partien | P2 |
| `<gid>.ok` | Fertig-Marke, Statistik, Zeitmessung (§5.4) | P1 |
| `<gid>.none` | ehrlich leer | P1 |
| `<gid>.err` | Fehler, Text | P1 |

**Fertig** heisst:
- `.none` existiert und ist JSON mit `"format": 2` (eine leere v1-Marke zählt nicht), oder
- `.ok` existiert, ist JSON mit `"format": 2`, **und** jede dort gelistete Datei hat genau die gelistete Grösse.

`.err` gilt als erledigt, ausser mit `RETRY_ERR=1`.

**Beim Start einer Partie** werden alle ihre Dateien gelöscht, `.tmp`-Reste ebenso wie Dateien mit Endnamen, `.ok`, `.none` und `.err`. So kann nie eine neue Meta neben einer alten `.maps` liegen.

**Beim Abschluss** gilt diese Reihenfolge: `fsync` aller tmp-Dateien, dann Umbenennen in der Folge `maps`, `cells`, Tier 1, `hdr.json`, `meta.zst`, dann `.ok` (tmp, fsync, rename).

**Leser** (`reader.py`, Trainer, Kanarienlauf) nehmen nur Partien, deren `.ok` die Grössenprüfung besteht, und nie „vorhandene `.meta.zst`“.

So kann eine leere oder winzige `.meta.zst` nie als fertig gelten. `.none` enthält `{"format": 2, "reason": ..., "params": {...}}`.

### 5.1 `hdr.json`
```
{ "format": 2, "gid", "commit" (voll), "materializer" (Repo-Commit, aus Env MAT_VERSION),
  "map", "mapSize", "W", "H", "landTiles" (game.numLandTiles()), "numTurns",
  "spawnPhaseTurns", "spawnEndTick" (erster Tick mit !inSpawnPhase()), "randomSpawn",
  "config": {...roh aus info.config...},
  "players": [ {"sid","clientID"|null,"playerID","name","team"|null,"type"} ... ],
  "winners": [clientID...], "tier1": true|false,
  "params": {"NOOP_EVERY","MERGE_TICKS","TIER1_PCT","CELLS"} }
```
`players` enthält alle, auch Nationen. Bots (Tribes) entstehen erst ab Tick 1, deshalb wird die Tabelle am Ende der Partie aus `game.allPlayers()` geschrieben.

### 5.2 Metazeile
Alle alten Felder bleiben mit gleicher Bedeutung: `turn, clientID, mapW, mapH, troops, gold, oppIds, ownUnitIds, ownAttackIds, own, opps, intent, w, win`. Dazu kommen:

| Feld | Typ | für |
|---|---|---|
| `tick` | int, `game.ticks()` beim Emit | alle |
| `sid` | int, eigene smallID | alle |
| `allies` | int[], smallIDs aller Allianzpartner (`player.allies()`, ohne Team) | alle |
| `team` | string\|null | alle |
| `cfg` | Objekt, rohes `info.config`. Der Trainer rechnet `featurize_config(cfg)`, ohne den Record nachzuschlagen | alle |
| `kind` | `"act"`, `"noop"` oder `"spawn"` | alle |
| `merged` | int, Zahl der zusammengefassten Folgeklicks | Angriff |
| `merged_turns` | int[], Züge der Folgeklicks | Angriff |
| `w_tick` | float, Gewicht für „handeln ja/nein“ (§4) | alle |
| `click` | int, rohe Klickkachel | räumlich |
| `dst_owner` | int, smallID des Besitzers der Klickkachel im Entscheidungstick, 0 = herrenlos oder Wasser | räumlich |
| `res_tile` | int, -1 falls ungültig | räumlich + Upgrade |
| `res_kind` | 0 neu, 1 Upgrade, 2 ungültig | räumlich + Upgrade |
| `res_unit_id` | int, -1 falls keine | räumlich + Upgrade |
| `res_dt` | int, Ticks vom Emit bis zur Beobachtung | räumlich + Upgrade |
| `cell` | int, Index in `.cells`, -1 falls nicht berechnet | räumlich |
| `spawn_final` / `spawn_late` | 1 | Spawn |

Räumlich sind `build_unit` (alle Typen), `boat`, `move_warship` und `spawn`. `upgrade_structure` hat keine Kachel, bekommt aber die `res_*`-Felder.

**`res_*` per Beobachtung, nicht per Vorab-Abfrage.** Die Engine entscheidet erst in T, T+1 oder T+2 (ENGINE_FACTS §2.1). Eine Abfrage im Entscheidungstick wäre nur eine Näherung, zwei Bauklicks im selben Zug bekämen dieselbe Kachel. Beobachtet wird nur, der Spielzustand wird nie verändert.

Neue Einheiten sind Unit-Updates mit einer `id`, die noch nie gesehen wurde; die IDs werden fortlaufend vergeben.

**Zuordnung:** gierig nach aufsteigender Einheiten-ID zum ältesten offenen Sample desselben Spielers und Typs, das die geometrische Bedingung erfüllt:
- Bauwerk und Port: euklid² zum Klick < 225 (Suchradius 15)
- Boot: Besitzer der `targetTile` ist `dst_owner` und Manhattan ≤ 50 zum Klick
- Nukes und Warship: `targetTile` bzw. `patrolTile` ist der Klick

Ein gescheiterter Klick erzeugt keine Einheit und bleibt offen, bis sein Fenster endet. Er kann also keine fremde Einheit schlucken. Bei Nukes mit `amount` > 1 bekommt das Sample die erste Einheit, die weiteren werden gezählt (`res.extra_units`).

**Fenster strikt** laut Tabelle (erster Wert). Eine Einheit, die erst im tolerierten Zusatz-Tick erscheint, wird zugeordnet und als `late` gezählt.

| Intent / Typ | `res_tile` | gültig (`res_kind` 0/1), wenn | Fenster |
|---|---|---|---|
| build_unit City, Defense Post, SAM Launcher, Missile Silo, Factory, Port | `pos` der neuen Einheit dieses Typs mit `ownerID == sid` | sie erscheint | Updates von Tick T+1 (bis T+2 tolerieren) |
| build_unit Warship | Klick (Patrouillenpunkt, Entwurf §6) | neue Warship-Einheit von `sid` mit `warshipState.patrolTile == klick`; `res_unit_id` = ihre id | T+1 (bis T+2) |
| build_unit Atom Bomb, Hydrogen Bomb, MIRV | Klick (Detonationszentrum) | neue Einheit dieses Typs von `sid` mit `targetTile == klick` | T+1 bis T+3 |
| boat | `targetTile` des neuen Transportschiffs (Landekachel) | neues Transportschiff von `sid` | Tick T (bis T+1) |
| upgrade_structure | `game.unit(unitId).tile()` im Entscheidungstick, `res_unit_id = unitId` | Level der Einheit steigt in Tick T (`res_kind 1`) | T (bis T+1) |
| move_warship | Klick | nach Tick T hat mindestens eines der Schiffe `warshipState().patrolTile == klick` | T |
| spawn | Klick (kein Einrasten) | nach Tick T+1 ist `player.spawnTile() == klick` **und** der Spieler besitzt Kacheln, die er vorher nicht besass. Scheitert `getSpawn`, bleibt `spawnTile` alt, deshalb reicht der Vergleich allein nicht | T+1 |

- Nicht beobachtet: `res_kind 2`, `res_tile -1`, `res_unit_id -1`. Ausnahme: Bei Warship und Nukes bleibt `res_tile = klick`, damit die Kennzahl den Klick trotzdem kennt; `res_kind` sagt dann 2.
- Fenster und Zuordnung legt `res.ts` fest und begründet sie. Eine Abweichung vom Fenster (später gefunden) wird gezählt.

### 5.3 Tier 1
- **Auswahl:** `sha1(gid) % 100 < TIER1_PCT` **oder** `sha1(gid) % 25 == 0` (Val-Partien immer dabei). Standard ist `TIER1_PCT=100`. `TIER1_PCT=0` schaltet Tier 1 ganz ab, auch für Val-Partien. Das braucht der Kanarienlauf als Vergleichslauf ohne Zusatzfelder. `sha1` über den ASCII-String der gid, gelesen als Big-Endian-Zahl der ersten 8 Hex-Zeichen.
- **`own.zst`:** Quelle sind die `packedTileUpdates` des Ticks, Paare `[ref, (state & 0xffff) | terrain << 16]`, in Reihenfolge angewendet.
  - Gespeichert werden die unteren 16 Bit (`state`) und bei Wasserumwandlung das Terrain-Byte.
  - Keyframes mit vollem u16-Zustand W·H: am ersten Tick nach der Spawnphase und danach alle 4096 Ticks.
  - Dazwischen je Tick nur Kacheln, deren Wert sich gegenüber einem Spiegelpuffer wirklich geändert hat.
  - Frames zu 512 Ticks, zstd Level 3, sofort in die Datei streamen.
  - Das exakte Byte-Layout legt `tier1.ts` fest und dokumentiert es im Kopf von `tier1.py`.
- **`units.zst`:** Ereignisse, abgeleitet aus den Unit-Updates:
  - Gebäude: Entstehen, Level, Besitzer, Bau fertig, Zerstörung
  - Kriegsschiffe: Entstehen, Ende, Position alle 8 Ticks
  - Transportschiffe: Entstehen mit Ziel, Ende, Position alle 8 Ticks per `unit.tile()`
  - Nukes und MIRV: Start, Ziel, Ende
- **`.chk`:** JSON `{ "ticks": [t1, t2], "crc32": [c1, c2] }`, CRC32 über den u16-Zustandspuffer (Little Endian) in Tick t (Tick-Konvention §2).
  - Die zwei Ticks stehen **vor** dem Replay fest. Sie werden deterministisch aus sha1(gid) unter den Zügen mit Menschen-Intent nach `numSpawnPhaseTurns()+1` gewählt, allein aus dem Record (Vorab-Scan). `chkTicksFor(gid, kandidaten)` bekommt diese Kandidaten.
  - Ticks nach `valid_until` werden gestrichen.
  - `tier1.py` muss die übrigen bit-genau treffen.
  - **Umgesetzt (Tier-1-Bericht 10.09.):**
    - `chkTicksFor` nutzt die sha1-Hex-Zeichen 24–39. So hängt die Wahl weder mit der Auswahl (0–7) noch mit SHARD (8–15) zusammen.
    - `.chk` trägt zusätzlich `units_crc32`, `terrain_crc32`, `valid_until`, `chosen` und `dropped`, dazu den letzten gültigen Tick als dritten Prüfpunkt.
    - `own.zst` beginnt ab Tick 1, auch in der Spawnphase. Das Byte-Layout steht im Kopf von `py/tier1.py`.
    - Daten nach `valid_until` bleiben in der Datei, der Leser verweigert sie.
    - Der Writer muss bei `game.ticks()==0` gebaut werden.

### 5.4 `.ok`
```
{ "format": 2, "files": {"<name>": bytes, ...},
  "samples": n, "spatial": n, "by_kind": {...}, "by_intent": {...}, "merged_clicks": n,
  "res": {"<typ>": {"ok": n, "invalid": n, "late": n}},
  "errors": {"act": n, "noop": n, "spawn": n, "res": n, "cells": n}, "first_error": "...",
  "hash": {"checked": n, "missing": n, "digest": crc32}, "desync": null | {"tick": t, "last_ok_tick": t},
  "tick_error": null | {"tick": t, "msg": "..."}, "valid_until": t | null,
  "chk_dropped": n, "legal_bit1": true|false,
  "time_ms": {"sim","scan","vec","map","json","res","cells","tier1","io","total"},
  "cpu_ms": {"user","system"}   // process.resourceUsage() am Ende, echte CPU-Zeit
  "tier1_bytes": n, "cells_bytes": n, "ticks": n, "rss_max_mb": n }
```

---

## 6 Zellfakten (P3, `cells.ts`)

Je räumlichem Sample, im Entscheidungstick, Raster 90×180 (Zeile = `(y*90/H)|0`, Spalte = `(x*180/W)|0`, genau wie obs.ts).

- **`owner_major`:** das `ownerG` aus `obs.ts` (Mehrheitsbesitzer der Stichprobe), gelesen, nicht neu berechnet.
- **`own_frac`:** `round(255 · eigene Kacheln / Kacheln der Zelle)`, exakt aus `player.tiles()`. Kein Vollscan der Karte.
- **`legal`:** 8 Bit, Bit gesetzt heisst: mindestens eine Kachel der Zelle erfüllt die Bedingung. Bedingungen laut ENGINE_FACTS §8, mit diesen Präzisierungen:
  - Bit 0: eigene Kachel.
  - Bit 1: eigene Kachel mit euklid² ≥ 225 zu jedem aktiven Bauwerk jedes Besitzers, auch im Bau.
  - Bit 2: wie Bit 1 und zusätzlich `isShore`, also auch Seeufer. Der Entwurf verlangte Ozeanküste, die Engine prüft das nicht.
  - Bit 3: Landkachel mit `isShore` und Besitzer X ≠ sid, wobei X herrenlos ist oder angreifbar (`canAttackPlayer`). Ihre Wasserkomponente muss unter den Komponenten der eigenen Küstengrenzkacheln sein.
  - Bit 4: Wasser in der Komponente eines eigenen aktiven, fertigen Hafens.
  - Bit 5: Wasser.
  - Bit 6: passierbares Land, das herrenlos ist **oder dem Spieler selbst gehört**. Beim Umziehen in der Phase gibt die Engine die eigenen Kacheln vor der Suche frei, deshalb zählen sie hier mit.
  - Bit 7: nukebar. Die Kachel ist passierbar, kein Teamkamerad besitzt sie, kein Teamkameraden-Bauwerk steht im Aussenradius der Atombombe (30), und der Spieler hat ein bereites Silo und keine Spawn-Immunität. Gold wird nicht geprüft, das hängt vom Typ ab.
- Schalter `LEGAL_BIT1=0` lässt Bit 1 und damit Bit 2 weg, beide sind dann 0. Das ist Rückfallstufe 1, falls das CPU-Tor reisst. Die `.ok` vermerkt `legal_bit1: false`. Das Maskentor prüft dann Bauwerke und Port gegen Bit 0. Rückfallstufe 2 ist `CELLS=0`: keine Zellfakten, `cell: -1`.
- Kosten zählen in `time_ms.cells`. Einmal pro Partie vorrechnen: Küstenliste mit Wasserkomponente. Pro Tick cachen: gilt für alle Samples desselben Spielers im selben Tick.

**Typ → Bit** (für das Tor Maskenverletzung, wird am Zell-Index von `res_tile` geprüft, nur bei `res_kind 0`):
- Bauwerk: Bit 1
- Port: Bit 2
- Boot: Bit 3
- Atom, Wasserstoff, MIRV: Bit 7
- Warship bauen: Bit 4
- Warship bewegen: Bit 5
- Spawn: Bit 6

---

## 7 Container

- Ein Image für x86_64 (`node:24-bookworm-slim`), ARM ist bewusst nicht vorgesehen. Es wird auf jedem Host selbst gebaut oder per `docker save | docker load` verteilt.
- Alle drei Engine-Commits liegen als Bäume `/app/w/<sha8>/`, 88 und 8b teilen `node_modules`. Der Commit wird **pro Record** aus `gitCommit` gewählt, es gibt keinen Checkout zur Laufzeit. Ein unbekannter Commit ergibt `.err`.
- Einhängen:
  - `/in`: Records, nur lesbar, beliebig tief, `*.json` mit 8-Zeichen-ID; Dubletten nach gid nur einmal
  - `/out`: Ausgabe
- Umgebungsvariablen:
  - `SHARD=i/n`: Auswahl `h % n == i`, wobei h die Hex-Zeichen 8–15 von sha1(gid) als Zahl sind. Das ist bewusst **nicht** dieselbe Zahl wie in §5.3, sonst lägen alle Val-Partien auf einem Shard.
  - `WORKERS`: Standard min(CPUs laut cgroup, RAM laut cgroup / `MEM_PER_WORKER_MB`)
  - `NOOP_EVERY`, `MERGE_TICKS`, `TIER1_PCT`, `CELLS` (1), `LEGAL_BIT1` (1), `RETRY_ERR`
  - `LIMIT`: nur die ersten n, zum Testen
  - `LIST`: Datei mit gids, statt alles
- Start: `docker run --rm --cpus N --user $(id -u):$(id -g) -v <records>:/in:ro -v <out>:/out -e SHARD=0/5 of-mat2`.
- Läuft auf NixOS und Arch identisch (cgroup v2, Docker 29). Keine Host-Abhängigkeit ausser Docker.
- Ausgabe von `dispatch.ts`: eine Zeile pro Partie, alle 60 s eine Zusammenfassung (fertig/leer/Fehler/Rest, Samples/s, ETA).

---

## 8 Hook-Schnittstellen (damit Kern, Tier 1 und Zellfakten getrennt gebaut werden können)

```ts
// tier1.ts
export class Tier1Writer {
  constructor(opts: { dir: string; gid: string; game: Game; spawnEndTick: number; chkTicks: number[] });
  // chkTicks stehen vorab fest (Vorab-Scan, §5.3). Tick-Konvention §2: Zustand mit game.ticks()==t.
  onTick(tick: number /* = T, der gerade ausgeführte Zug; danach gilt game.ticks()==T+1 */, gu: GameUpdateViewData, game: Game): void;
  finish(validUntil: number | null): { files: Record<string, string /* tmp-Pfad */>; bytes: number; chkDropped: number }; // Kern benennt um
  abort(): void; // tmp-Dateien löschen
}
export function tier1Selected(gid: string, pct: number): boolean;
export function chkTicksFor(gid: string, candidateTicks: number[]): number[]; // Kandidaten aus dem Vorab-Scan

// cells.ts
export class CellFacts {
  constructor(game: Game, opts: { legalBit1: boolean });   // einmal pro Partie
  compute(game: Game, player: Player, ownerG: Uint16Array): Buffer; // roh 64'800 Byte: u16 LE owner ‖ u8 own_frac ‖ u8 legal
}
export const CELL_BYTES = 16200 * 4;
```
Der Kern liest `ownerG` über `(enc as any).ownerG` aus dem ObsEncoder, obs.ts bleibt dafür unverändert. Er komprimiert den Block selbst (zstd Standardlevel) und schreibt ihn nach `.cells`.

---

## 9 Kanarienlauf (`canary/`)

- **50 Records:**
  - alle 5 von `115da032`
  - beide grossen Commits
  - die grössten Karten (Passage, Korea, Giant World Map)
  - Grössen über das ganze Spektrum, FFA und Team
- **Durchführung:** im selben Image, mit gleichem CPU-Deckel. Der alte Materialisierer (`env/`, mit `NOOP_EVERY=200 THIN=30`, ein Prozess pro Partie) und der neue laufen auf denselben Records.
- **Harte Tore.** Jedes Nein heisst: nicht starten.
  1. **Byte-Gleichheit.**
     - Verglichen wird je (turn, clientID). Innerhalb davon sind die Blöcke ohnehin gleich, weil sie aus dem Kontext-Cache stammen.
     - Jeder Block, den beide für denselben Schlüssel haben, muss identisch sein.
     - Die Abdeckung wird als Multimenge gezählt: Mindestens 95 % der alten Samples müssen im neuen Lauf einen Schlüssel haben. Ein alter Angriff, der jetzt in `merged_turns` eines anderen Samples steckt, zählt als erklärt.
     - Der Rest wird nach Grund ausgewiesen: Zusammenfassen, Schnitt, Fehler.
  2. **Checksummen:** 100 % der `.chk` vom Python-Rekonstruktor getroffen.
  3. **Maskenverletzungen** unter 1 %, gesamt (Typ → Bit, §6). Ausgewiesen je Typ und je `res_dt`, damit ein Zeitversatz sichtbar wird, etwa beim Nuke-Silo.
  4. **CPU-Aufschlag** höchstens 15 %.
     - Gemessen als CPU-Zeit (`cpu_ms`, rusage) des neuen Laufs mit allem gegen den neuen Lauf mit `CELLS=0 TIER1_PCT=0` auf denselben Records. Das misst genau die Kosten der neuen Felder bei gleichen Samples.
     - Alt gegen neu wird nur als Information ausgewiesen, dort unterscheiden sich die Samples.
     - Reisst das Tor: Rückfallstufe 1 (`LEGAL_BIT1=0`), dann 2 (`CELLS=0`), jeweils neu messen. Ausschnitte (P5) gibt es in diesem Lauf nicht.
  5. **Treue des Nachspielens:**
     - Jeder Record mit Hashes hat `hash.checked > 0`, 0 Desyncs und keinen `tick_error`.
     - Der Lauf mit allem und der Lauf ohne Zusatzfelder haben je Partie denselben `hash.digest`.
  6. **Vollständigkeit:** Jede Partie hat `.ok`, `.none` oder `.err`, und keine `.meta.zst` ist kleiner als 64 Byte.
- **Messwerte:**
  - t1: MB je Partie für Tier 1
  - z: KB je räumlichem Sample für die Zellfakten
  - s: Sim-Anteil
  - o: CPU-Aufschlag
  - Abstand Klick → `res_tile` je Typ in echten Kacheln (Median, p90)
  - `res_kind` je Typ
  - Hashes geprüft, Desyncs, Fehler
  - Samples je Partie, nach `kind`
  - RAM-Spitze je Worker
- **Zweigempfehlung** nach Entwurf §7.3 (A/B/C), mit dem als Parameter angegebenen freien Platz.

---

## 10 Dev-Umgebung (arch)

- `~/mat-dev/w/<sha8>/` enthält `vendor/openfront` (Worktree), `env/` (alter Materialisierer), `tsconfig.json` und `node_modules` (Symlink). 88 und 8b teilen node_modules, 115 hat eigene.
- Referenzausgaben des alten Materialisierers: `~/mat-dev/ref/{standard,thin}/<sha8>/<gid>/`, Messwerte in `~/mat-dev/ref/results.tsv`. `thin` ist `NOOP_EVERY=200 THIN=30`.
- Inventar aller Records auf arch: `~/mat-dev/records.tsv` (Pfad, gid, Commit, Grösse, Karte, Spieler, num_turns).
- Referenz-Records: 88cc95d8 `uLwSPQFK`, `dJtnLxJA`, `FCxzAh2Y`; 8b45be57 `H2NkRg5R`, `JwFNdofK`, `omTgUNMh`; 115da032 `bqfEEyVi`, `A9iejjLi`, `HzjyLWzY`.
- arch ist der Desktop des Nutzers: `nice -n 10`, höchstens 3 Prozesse pro Agent. Jeden längeren `ssh` in `timeout` wickeln.
