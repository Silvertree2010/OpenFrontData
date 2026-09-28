# Kanarienlauf (DESIGN.md §9)

Drei Läufe auf denselben 50 Records im selben Image, danach sechs harte Tore. Jedes Nein heisst: den Volllauf nicht starten.

| Datei | Zweck |
|---|---|
| `select.py` | wählt deterministisch 50 Records aus dem Inventar und begründet jede Wahl |
| `canary.sh` | führt die Läufe A/B/C aus und ruft danach `gates.py` auf |
| `gates.py` | Tore 1–6, Messwerte, Hochrechnung, Zweigempfehlung. Schreibt `gates.md` und `gates.json` |
| `../tests/canary/test_gates.py` | Manipulationstests: Jedes Tor muss bei seiner Manipulation rot werden |

## Ablauf auf arch

```bash
M=~/mat-dev/canary-src/materializer        # Kopie des Repo-Ordners materializer/
python3 $M/canary/select.py ~/mat-dev/records.tsv ~/projects/openfront-ai/data ~/mat-dev/canary/liste50.tsv
bash $M/canary/canary.sh of-mat2:test ~/projects/openfront-ai/data ~/mat-dev/canary/liste50.tsv \
     ~/mat-dev/canary/voll 8 unbekannt ~/mat-dev/records.tsv
```

Die Parameter von `canary.sh` sind: Image, Records-Wurzel, Liste, Ausgabe-Wurzel, CPUs, freier Platz in GB (oder `unbekannt`) und optional das Inventar für die Hochrechnung. Exit 0 gibt es nur bei GO.

Die drei Läufe laufen nacheinander, alle mit gleichem `--cpus` und `WORKERS = cpus`. Jeder schreibt in einen eigenen Ordner.
- **A** (alt): `MAT_ENTRY=materialize_old.mjs NOOP_EVERY=200 THIN=30`
- **B** (neu mit allem): `CELLS=1 TIER1_PCT=100`
- **C** (neu ohne Zusatzfelder): `CELLS=0 TIER1_PCT=0`

Weitere Umgebungsvariablen:
- `NICE` (Standard 10). Der Entrypoint wird mit `nice` umwickelt, denn ein `nice docker run` würde die Container-Prozesse nicht treffen.
- `RUNS="B C"`: nur einzelne Läufe.
- `SKIP_RUNS=1`: nur die Tore auf vorhandener Ausgabe.
- `RUN_TIMEOUT`: Obergrenze je Lauf in Sekunden.
- `CANARY_DOCKER_ARGS`: zusätzliche docker-Argumente, **nur für Entwicklung**, zum Beispiel ein anderes Bündel darüberlegen. Der Bericht vermerkt sie.

`dispatch` erwartet in `LIST` gids, keine Pfade. `canary.sh` erzeugt deshalb `gids.txt` aus Spalte 2 der Liste. Vorher bricht es ab, wenn eine gid unter der Wurzel mehrfach vorkommt, denn `dispatch` nimmt den ersten Fund. Ebenso bricht es ab, wenn das Image kein `materialize_old.mjs` hat oder `dispatch.mjs` den Schalter `MAT_ENTRY` nicht kennt.

## Tore (gates.py)

Jedes Tor braucht eine Mindestmenge geprüfter Elemente, sonst ist es rot. „0 Fehler bei 0 geprüften“ zählt nicht.

1. **Byte-Gleichheit A gegen B.** Geprüft über `tests/core/compare_ref.py` je Partie.
   - Jede Partie, bei der A Samples hat, muss verglichen werden und bestehen.
   - Die Abdeckung gesamt muss mindestens 95 % sein.
   - Mindestmenge: `--min-blocks` verglichene Blöcke (Standard 100).
2. **Checksummen.** `py/tier1.py verify` läuft für jede B-Partie mit Tier 1, alle müssen Exit 0 liefern.
   - Ist Tier 1 für eine Partie laut §5.3 fällig (`TIER1_PCT` aus `hdr.params`), muss es auch da sein, und `errors.tier1` muss 0 sein.
   - Mindestmenge: 1 bestandene Partie.
3. **Maskenverletzungen unter 1 %.**
   - Geprüft wird nur `res_kind 0`, mit der Typ → Bit-Tabelle aus §6.
   - Die Zelle berechnet sich wie in obs.ts: `(y*90/H)|0`, `(x*180/W)|0`. Das `legal`-Byte liegt am Offset 48'600 des Zellblocks.
   - Bei `legal_bit1: false` werden Bauwerk und Port gegen Bit 0 geprüft.
   - Aufgeschlüsselt je Typ und je `res_dt`.
   - Mindestmenge: `--min-mask` (Standard 50). Zusätzlich dürfen höchstens 1 % der Kandidaten ohne Zellblock bleiben.
4. **CPU-Aufschlag höchstens 15 %.**
   - Verglichen wird `cpu_ms` (user+system) von B gegen C, summiert über alle Partien.
   - Fehlt `cpu_ms` oder ist die Zahl der Samples in B und C verschieden, ist das Tor rot.
   - A gegen B steht nur als Information im Bericht, als Wandzeit, weil der alte Materialisierer kein `cpu_ms` schreibt.
5. **Treue.**
   - Die Zahl der Hashes wird aus dem Record gezählt. Hat ein Record Hashes, muss in B und C `hash.checked > 0` sein, ohne Desync und ohne `tick_error`.
   - `hash.digest` muss in B und C je Partie gleich sein.
   - Mindestmenge: 1 Partie mit geprüften Hashes.
6. **Vollständigkeit.**
   - Jede Partie hat in A, B und C `.ok` (Grössenprüfung bestanden), `.none` oder `.err`. Bei A zählt statt `.ok` eine `.meta.zst` ab 64 Byte.
   - Keine `.meta.zst` ist kleiner als 64 Byte.

Die Punkte, in denen die Tore strenger sind als der Wortlaut von §9, sind eigene Präzisierungen:
- Tor 1: jede Partie wird verglichen.
- Tor 2: fälliges Tier 1 muss vorhanden sein.
- Tor 3: Anteil ungeprüfter Kandidaten.
- Tor 4: gleiche Samples in B und C.
- Tor 5: gilt für B und C.

**Messwerte:**
- t1, z, s (Sim-Anteil, aus C; B steht daneben) und o
- Abstand Klick → `res_tile` je Typ (Median, p90)
- `res_kind` je Typ
- Hashes, Desyncs, Fehler
- Samples je Partie nach `kind`
- RAM-Spitze

**Hochrechnung auf 18'018** (Schätzung): Verhältnisschätzer je Grössenquartil des Inventars, also Messgrösse je Record-Byte der Kanarienpartien mal Record-Bytes des Inventars, skaliert von der Inventargrösse auf 18'018. Die Anteile für 25 % und 15 % plus Val werden exakt aus den gids des Inventars gerechnet. V1-Ausschnitte werden nicht gemessen, dafür gilt die Spanne aus dem Entwurf (0,5–1,3 KB/Sample).

**Zweig A/B/C** nach Entwurf §7.3:
- Grundlage ist der Bedarf plus 20 % Puffer gegen den freien Platz.
- Ist der Platz unbekannt, gilt t1 ≤ 6 MB.
- s ≥ 50 % übersteuert auf A.
- o > 15 % nennt die Rückfallstufen.

## Tests

```bash
python3 ../tests/canary/test_gates.py <kanarien_out> <liste.tsv> <records_root> [--gids a,b,c]
```

Der Test kopiert 2–3 echte Partien je Fall in einen Arbeitsordner und verändert sie dort. Er prüft zuerst, dass die unveränderte Grundlage ganz grün ist, danach je Manipulation, dass das Zieltor rot wird. Die Ausgabe ist eine Tabelle mit allen roten Toren je Fall.

## Laufzeit (Schätzung)

Die 50er-Liste umfasst 41,5 MB Records. Mit `--cpus 8` dauern die drei Läufe zusammen etwa 0,7 bis 3,3 h Wandzeit. Die längste Partie braucht 12 bis 59 min.
- Grundlage sind die Raten von 72 bis 344 s je MB Record aus `~/mat-dev/ref/results.tsv` (alt, thin), dazu B/A = 1,06 aus dem Mini-Lauf.
- Nicht mitgerechnet ist die Zeit der Tore, also `tier1.py verify` und `compare_ref` auf den grossen Partien. `compare_ref` hält alle Blöcke von A und B im RAM, bei 10 MB Record voraussichtlich mehrere GB.

## Bekannte Punkte (Stand 2026-09-10)

- Im Image scheitert das neue Bündel an jeder Partie mit `Record-Commit … ≠ Engine-Baum ?`. Die Ursache ist `src/materialize.ts:56`: `ROOT = MAT_ROOT ?? path.resolve(HERE, "../..")` zeigt im Bündel (`/app/w/<sha8>/dist/`) auf `/app/w` statt auf `/app/w/<sha8>`. Damit sind auch der Commit und `ENGINE` (die Karten) falsch. `dispatch.ts` setzt pro Kind weder `MAT_ROOT` noch `ENGINE_COMMIT`. Die Behebung gehört in `materialize.ts` (im Bündel `..`) oder in `dispatch.ts` (`MAT_ROOT=<treeDir>` je Kind). Sie liegt nicht in `canary/`.
- `of-mat2:test` (gebaut 10.09. 12:39) ist älter als `materialize.ts`: Im Bündel fehlen `cpu_ms`, `legal_bit1` und `chk_dropped`. Tor 4 ist damit immer rot. Vor dem Kanarienlauf muss das Image neu gebaut werden.
