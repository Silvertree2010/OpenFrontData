# Materialisierer v2 — Bauen, Starten, Flotte

Verbindlich ist `DESIGN.md`. Dieses README ist die Kurzfassung fuer den Alltag.

## Bauen

Build-Kontext ist die **Repo-Wurzel** (nicht `materializer/`):

```bash
cd OpenFrontData
docker build -f materializer/docker/Dockerfile \
  --build-arg MAT_VERSION=$(git rev-parse HEAD) \
  -t of-mat2 .
```

Das Image enthaelt drei vorgebuendelte Engine-Baeume (`88cc95d8`, `8b45be57`,
`115da032`, siehe `docker/Dockerfile`) — je Baum ein `dist/materialize.mjs`
(neuer Kern), ein `dist/materialize_old.mjs` (alter Materialisierer, nur fuer
den Kanarienlauf) und die zugehoerigen `resources/maps`. Kein `node_modules`,
keine `.git`, keine Engine-Quellen zur Laufzeit — esbuild buendelt alles
Noetige selbst ein.

Solange `materializer/src/materialize.ts` (der neue Kern) noch nicht existiert,
ueberspringt der Build diesen Schritt je Baum mit einer Meldung und baut nur
`materialize_old.mjs`. `dispatch.ts` faellt dann automatisch auf `.err`
("Buendel fehlt") zurueck, wenn `MAT_ENTRY` nicht gesetzt ist — zum Testen
`MAT_ENTRY=materialize_old.mjs` setzen (siehe unten).

## Einzelstart

```bash
docker run --rm --cpus 3 --user $(id -u):$(id -g) \
  -v /pfad/zu/records:/in:ro \
  -v /pfad/zu/out:/out \
  -e SHARD=0/1 \
  of-mat2
```

Wichtige Umgebungsvariablen (DESIGN.md §7):

| Variable | Bedeutung | Default |
|---|---|---|
| `SHARD` | `i/n`, Auswahl `sha1(gid) % n == i` | alle |
| `WORKERS` | parallele Partien | aus cgroup (CPU, RAM / `MEM_PER_WORKER_MB`) |
| `MEM_PER_WORKER_MB` | RAM-Annahme je Worker fuer den WORKERS-Default | 2000 |
| `GAME_TIMEOUT_S` | Zeitlimit je Partie, danach Kill + `.err` "timeout" | 7200 |
| `LIMIT` | nur die ersten n Partien | alle |
| `LIST` | Datei mit gids statt allem unter `/in` | — |
| `RETRY_ERR` | `1`: `.err`-Partien neu rechnen | aus |
| `NOOP_EVERY`, `MERGE_TICKS`, `TIER1_PCT`, `CELLS`, `LEGAL_BIT1` | an den Kern durchgereicht, siehe DESIGN §7 | Kern-Defaults |
| `MAT_ENTRY` | welches Buendel je Baum laeuft: `materialize.mjs` (neu) oder `materialize_old.mjs` (Kanarien-Referenz/Uebergang) | `materialize.mjs` |
| `IN_DIR`, `OUT_DIR`, `MAT_ROOT` | Mountpunkte ueberschreiben (fuer Tests ausserhalb des Containers) | `/in`, `/out`, `/app/w` |

`dispatch.ts` findet Records rekursiv unter `/in`, dedupliziert nach der
8-stelligen gid, liest `gitCommit` aus den ersten ~4 KB jedes Records und
waehlt so den passenden Baum. Ein unbekannter Commit ergibt `<gid>.err`.

## Flottenstart

```bash
cp materializer/fleet/hosts.example materializer/fleet/hosts.local
# hosts.local ausfuellen (Format im Kommentarkopf der Datei)
materializer/fleet/launch.sh materializer/fleet/hosts.local
# zusaetzliche docker-run-Variablen anhaengen:
materializer/fleet/launch.sh materializer/fleet/hosts.local -e TIER1_PCT=50
```

`launch.sh` vergibt `SHARD=i/n` nach Zeilenposition, baut das Image auf jedem
Host selbst (`build`) oder verteilt ein lokal gebautes Image per
`docker save | ssh ... docker load` (`load`). Jeder `ssh`-Aufruf ist in
`timeout` gewickelt. Flottenregeln (node-3l nie, Mac-Knoten nie, apollo
hoechstens halbe Kerne und nur `load`) stehen im Kommentarkopf von
`hosts.example`.

**Von einem Mac aus starten:** macOS hat kein `timeout`-Kommando. Die
Fleet-Skripte (`materializer/fleet/launch.sh`, `status.sh`, `stop.sh`) sind fuer einen
Linux-Kontrollrechner geschrieben (z. B. `arch`), wo GNU `timeout` Standard
ist — dort ausfuehren, nicht direkt vom Mac. Wer trotzdem vom Mac aus
steuern will:
- `brew install coreutils` gibt `gtimeout`; die Skripte dann mit
  `TIMEOUT_BIN=gtimeout materializer/fleet/launch.sh ...` aufrufen (oder `timeout` im
  eigenen `PATH` auf `gtimeout` verlinken), **oder**
- ganz ohne `timeout` auskommen und stattdessen `ssh` selbst begrenzen:
  `ssh -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=4
  <host> 'timeout 900 docker build ...'` — das Zeitlimit fuer den entfernten
  Befehl liegt dann im per SSH übertragenen Kommando selbst (auf dem
  Zielrechner ausgefuehrt, der hat `timeout`), nicht in der lokalen Shell.
  `ServerAliveInterval`/`-CountMax` sorgen dafuer, dass eine haengende
  Verbindung trotzdem erkannt wird, ohne dass der Mac ein eigenes `timeout`
  braucht.

## Status

```bash
materializer/fleet/status.sh materializer/fleet/hosts.local
```

Ein `ssh` je Host (mit `timeout`): Anzahl `.ok`/`.none` (fertig), `.err`
(Fehler), Rest (Records minus fertig/Fehler), Containerstatus, letzte 5
Logzeilen.

## Stoppen

```bash
materializer/fleet/stop.sh materializer/fleet/hosts.local
```

`docker stop -t 60` schickt SIGTERM. `dispatch.ts` faengt das ab: keine neuen
Partien mehr, laufende Kindprozesse werden beendet, `.tmp`-Reste bleiben liegen
und werden beim naechsten Start geloescht (DESIGN §5) — es gibt also nie eine
halbe Datei, die als fertig durchgeht.

## Wiederaufnahme

Einfach denselben `docker run`/`launch.sh`-Aufruf wiederholen (gleicher
Mount fuer `/out`). `dispatch.ts` prueft vor jedem Start, ob eine Partie schon
fertig ist (DESIGN §5 "Fertig heisst": `.none` **oder** `.ok` mit exakt
passenden Dateigroessen; Uebergangsregel fuer `materialize_old.mjs`: eine
`.meta.zst` ab 64 Byte gilt als fertig). `.err` gilt als erledigt, ausser mit
`RETRY_ERR=1`.

## Kanarienlauf

Der Kanarienlauf selbst (Auswahl der 50 Records, Tore, Messwerte) ist
Gegenstand von `materializer/canary/` und wird dort spezifiziert — nicht Teil
dieses README-Abschnitts. Fuer den Container reicht es zu wissen: Beide
Materialisierer (`materialize.mjs`, `materialize_old.mjs`) liegen im selben
Image, je Baum, und laufen mit demselben CPU-Deckel; `canary.sh` startet
beide gegen dieselben Records und vergleicht die Ausgaben.

## Fehlerbilder

| Symptom | Ursache | Was tun |
|---|---|---|
| `<gid>.err` "unbekannter Commit: ..." | Record stammt von einem vierten Commit, der nicht im Image liegt | Baum ergaenzen (Dockerfile, `SHA_D`/`SHA8_D`) oder Record ignorieren |
| `<gid>.err` "Buendel fehlt: ..." | `materialize.ts` existiert noch nicht / Bundle-Schritt fehlgeschlagen | Build-Log pruefen, `MAT_ENTRY=materialize_old.mjs` als Uebergang |
| `<gid>.err` "timeout" | Partie hat `GAME_TIMEOUT_S` (Default 7200) ueberschritten | Ursache pruefen (haengt die Engine? riesige Partie?), ggf. Limit erhoehen |
| `<gid>.err` "exit 0 ohne Fertig-Marke" | Kern ist sauber durchgelaufen, hat aber weder `.ok` noch `.none` geschrieben | Bug im Kern — `io.ts` pruefen |
| Container startet, `dispatch` meldet `WARNUNG: keine Engine-Baeume unter /app/w gefunden` | falsches Image oder `MAT_ROOT` falsch gesetzt | Image-Tag pruefen |
| `status.sh` zeigt "ssh/Abfrage fehlgeschlagen" | Host nicht erreichbar oder `docker` dort nicht im PATH der Login-Shell | manuell `ssh <host> docker ps` pruefen |
| Dateien im `/out`-Mount gehoeren `root` | `--user $(id -u):$(id -g)` fehlt beim `docker run` | `launch.sh` benutzt es automatisch; bei Handstart nicht vergessen |
| Build auf apollo dauert lange / Luefter hoch | Docker-Build ist kurz CPU-intensiv auf allen Kernen | auf apollo NICHT bauen, `hosts.local` dort auf `load` stellen |
