# OpenFront-KI — Arbeitskopie auf dem M4

Diese Sitzung wurde vom Arch-PC hierher kopiert. Fortsetzen:
    cd ~/projects/private/openfront-ai && claude --resume    → diese Sitzung wählen

## Was hier liegt (Arbeitskopie, ~216 MB)
- data/index.sqlite   die destillierte DB: 8.648 Partien, 44k Spieler
- data/ratings.sqlite eigenes Elo
- docs/               MECHANIK.md + INTENTS.md (quellenverifiziert)
- env/ scraper/ viz/  Code (obs.ts, collect.py, rate.py, Betrachter)
NICHT hier: vendor/ (1,7G Engine, regenerierbar) und viz/data (Frames).

## Arch-PC — die Trainingsmaschine
- Tailscale 100.120.102.64, user netter, RTX 5080
- Volles Projekt: ~/projects/openfront-ai
- Direktzugriff von hier:  ssh arch   (Schlüssel ODER Passwort, beides läuft)
- Muss an sein, um zu trainieren / auf ~/projects/openfront-ai zuzugreifen

## node-1 (100.81.143.39)
- ~/openfront-night/records: der Nachtlauf mit ~5000 guten vollen Records

## Erledigt auf dem M4 (2026-09-07)
- Aktionsübersetzung env/actions.py FERTIG — 286.298 echte Züge, 100% Round-Trip.
  Vertrag in docs/ACTIONS.md. Kopfgrößen stehen fest.

## Nächste Schritte
1. [braucht Arch/Engine] obs.ts füllt actions.Context beim Replay:
   Gegner-Reihenfolge, eigene Einheiten, Truppen/Gold, Kartenmaße → echte Labels
2. [braucht Arch/Engine] Nachtausbeute (node-1, ~5000 Records) replay-verifizieren
3. Netzarchitektur (CNN + Set-Transformer + Köpfe) → Behavior Cloning


## ⚠️ M4-Arbeit muss morgen nach Arch zurück
Diese Arbeitskopie ist NICHT mit Arch synchronisiert. Heute auf dem M4 neu:
  env/actions.py · env/test_actions.py · docs/ACTIONS.md · docs/ELO_ANALYSIS.md
Morgen zuerst: diese Dateien nach arch:~/projects/openfront-ai kopieren,
sonst fehlen sie im Trainings-Repo. (rsync M4→Arch, sobald Arch Strom hat.)

## Erledigt M4-Nacht (2026-09-07) — Lern-Pipeline gebaut & verifiziert
- net.py: Netz (CNN+Set-Transformer+13 Köpfe+Wert), 3,9 Mio Param. Formtest ok,
  Zeiger-Maskierung dicht, läuft CPU+MPS. (Frühere "5-15 Mio"-Schätzung war zu hoch.)
- bc_train.py: BC-Trainer, maskierter Multi-Kopf-Verlust, atomarer Checkpoint/Resume.
  Auf synthetischem Satz: Verlust 43→0,9, Kern-Treffer →96%, Resume bewiesen.
- Bekannt: MPS verrechnet -inf → in net.py auf -1e9 umgestellt (portabel).
- venv auf M4: python3.14 + torch 2.14 (MPS). Heavy Compute NICHT auf dem Laptop
  (Bett/Thermik, siehe Vault feedback-m4-thermik-warnen) — gehört auf Arch/5080.

## Morgen auf Arch (Reihenfolge)
1. M4-Arbeit rüber: env/{actions,net,bc_train,test_actions}.py + docs/*  → arch:~/projects/openfront-ai
2. Lern-Check auf 5080 (Sekunden, kalt) — Verlust muss fallen
3. obs.ts füllt actions.Context beim Replay → ECHTE (Beobachtung,Zug)-Paare
4. Nacht-Records (node-1, 2.971 Stück) einbinden + BC auf echten Daten
## Nacht-Job M4 (läuft): Metadaten-Ausbau
collect.py meta, ~0.8/s, caffeinate+nohup (logs/meta_night.log, .pid). Pool 8.648→~24k.
node-1 Voll-Records: 5.051 FERTIG. Morgen: angereicherte data/index.sqlite MIT nach Arch,
dann Elo neu rechnen → mehr hohe-Elo-Games für Voll-Record-Fetch.

## 2026-09-07 auf Arch — Fundament auf echter Hardware verifiziert
- M4-Code → Arch. torch 2.11+cu128 installiert, RTX 5080 rechnet (Blackwell/sm_120 ok).
- BC-Lern-Check auf 5080: Verlust 43→0.05, Kern-Treffer 100%, 120 Schritte/10s, 4GB VRAM @Batch64.
  → echtes BC: Batch 128 + bf16 locker drin. (Batch 256 sprengt 16GB — 90×180-Aktivierung.)
- 5.051 Nacht-Records auf Arch (data/night_raw). Zwei Engine-Commits:
  4.211 auf 88cc95d8, 840 auf 8b45be57. BEIDE Gruppen 3/3 replay-in-sync → alle trainierbar.
  Extraktion muss je Record den PASSENDEN Commit auschecken (2 Vendor-Checkouts sinnvoll).
- M4 Metadaten-Job läuft weiter (~22k → ~24k).

## NÄCHSTER SCHRITT (der große Brocken)
obs.ts erweitern: beim Replay pro Entscheidungs-Tick emittieren:
  Beobachtungs-Tensoren + actions.Context (Gegner-Reihenfolge, Truppen/Gold, Kartenmaße,
  eigene Einheiten) + roher Intent → auf Platte. Dann Python: actions.encode → Label,
  paart mit Obs → echte Trainingsbeispiele. DANN BC auf echten Daten.
## trackerfront (2026-09-07): externes Ranking als Auswahl-Label
trackerfront.com/api/public/leaderboard (FastAPI, paginierbar) → 2.897 gerankte Spieler,
deckt 38% unserer Partien ab. data/trackerfront_ladder.json. Nutzen: schärferes
Auswahl-Label + validiert unser Elo (trennt Top-Ladder sauber). Offizielle API NICHT hämmern (403).

## Extraktion — Brücke Engine→Label verifiziert (2026-09-07)
env/extract.ts: replayt Record, emittiert pro Zug JSONL (Kontext + Intent).
KRITISCHER FUND: Intent-Zielfelder (targetID/recipient/target/requestor) sind
PlayerIDs (player.id()), NICHT clientID. Der Handelnde dagegen = clientID
(playerByClientID). Erst mit clientID emittiert → 87% Ziele unauflösbar; nach Fix
auf id() → **100% saubere Labels** (1409 Samples/Partie, 0 unauflösbar), auf echtem
8b45be57-Record. Nationen/Bots haben id() aber keine clientID → jetzt adressierbar.

## Extraktion — was noch fehlt
1. Skalieren: alle ~5.100 Records, je passender Commit-Checkout (2 Durchläufe).
2. Obs-TENSOREN NICHT auf Platte (25TB) → beim Training on-the-fly per Replay.
   D.h. Node-Replay-Worker streamt (Obs, Label) an den Python-Trainer (das ist auch
   die spätere Self-Play-Architektur). Das ist der nächste Bau.
3. own_attack_ids/move_warship-Multi noch v1-vereinfacht (selten).

## obs.ts erweitert (2026-09-07, autonom) — 6/8 Strategie-Spec-Features
Neue Gegner-Felder: allyTicksLeft (Allianz-Restlaufzeit, normiert), isLeader,
hasSilo, hasSam. Neue own-Felder: troopsRatio (S-Kurve), boardShare, boatsOut, isLeader.
Verifiziert auf echtem Record (verify_obs.ts): alle Werte in erwarteten Bereichen,
Timer in [0,1], Leader eindeutig. Engine-API (config.maxTroops/allianceDuration/
allianceInfo/numLandTiles) löst sauber auf.
OFFEN (brauchen scanTick-Umbau): Struktur-LEVEL im Karten-Bild, SAM-Abdeckungs-Kanal.
TODO beim Trainer-Wiring: net.py OPP_DIM/OWN_DIM + Featurizer (dict→Tensor) an die
neuen Felder anpassen — aktuell nur als dict vorhanden, noch nicht vektorisiert.

## obs.ts KOMPLETT — 8/8 Strategie-Spec-Features (2026-09-07)
Zusätzlich zu den 6 Vektor-Features jetzt auch die 2 Karten-Features:
- Struktur-LEVEL in den bau_*-Kanälen (Vorzeichen=Beziehung, Betrag 0.4→1.0 je Level).
- Neuer Kanal "sam_cover" (SAM-Abdeckung, eigen +1 / verbündet +0.5 / feindlich -1).
NUM_CHANNELS jetzt 18 (net.py NUM_MAP_CH=18 angepasst). Auf echtem Record verifiziert:
bau_City Betrag 0.4 (=Level1), sam_cover 239 Zellen. Beobachtung ist damit VOLLSTÄNDIG.

## TODO Trainer-Wiring (Featurizer/Dims) — exakte neue Größen
- NUM_MAP_CH = 18 (erledigt in net.py)
- OPP_DIM: 14 → 18 (neu: allyTicksLeft, isLeader, hasSilo, hasSam)
- OWN_DIM: die own-Dict-Felder vektorisieren; neu: troopsRatio, boardShare, boatsOut, isLeader
- Featurizer (dict→Tensor) muss diese Felder in fester Reihenfolge ausgeben. Erst DANN
  passt obs an net.py. Aktuell liefert encodeVec Dicts, encodeMap den 18-Kanal-Tensor.
## Gegner-Modellierung in Spec aufgenommen (2026-09-07)
Gegner-Vorhersage-Kopf (Hilfsverlust, Label aus Replay) + Liga-Vielfalt (BC-Klone,
Skript-Bots, Störer-Agenten, Menschen-Eval). Details docs/STRATEGY_SPEC.md §E. NIE reines Self-Play.
## Identitäts-Signal in Spec §F (2026-09-07)
inClan + reputation (trackerfront-Rang/Elo, neutraler Fallback) als Gegner-Features.
KEINE Namens-Text-Semantik, KEINE Individuen-Memorierung. Verhalten>Reputation>Name.
OPP_DIM wächst weiter — beim Featurizer/Trainer-Wiring finalisieren.

## Featurizer + net.py-Dims fertig (2026-09-07)
env/featurize.py: kanonische Dict→Tensor-Umrechnung, feste Feld-Reihenfolge, Normalisierung
(log für Truppen/Gold/Felder, Rest 0..1). OWN_DIM=19, OPP_DIM=19 (single source of truth,
net.py importiert sie). NUM_MAP_CH=18. Reputation (§F) als Prior mit Fallback 0.5.
Verifiziert: own-Vek 19, opp-Matrix 24×19, Maske korrekt, Netz-Vorwärtslauf mit neuen
Dims ok (13 Köpfe, 3.9 Mio Param). obs↔net-Brücke steht.

## Vor BC auf echten Daten noch:
1. Reputationstabelle bauen (ratings.sqlite + trackerfront_ladder.json → {(user,clan):0..1}),
   und extract.ts pro Gegner username/clan mitgeben, damit der Featurizer sie nachschlägt.
2. On-the-fly-Streaming: Node-Replay-Worker emittiert (Map-Tensor + own/opp-Dicts + Label)
   → Python featurized + trainiert. Der grosse verbleibende Plumbing-Bau.
3. Volle Extraktion (2 Commit-Checkouts) + BC.

## Datenfluss ENTSCHIEDEN + validiert (2026-09-07)
Reputationstabelle gebaut: data/reputation.json, 19.864 Spieler (2.897 trackerfront-Rang
+ 16.967 Elo-Fallback), deckt 78% der Partien ab. Featurizer schlägt sie nach.

Kartentensor-Kompression GEMESSEN: 285KB roh uint8 → ~11KB zstd (Faktor 27x), stabil
über alle Spielphasen. Hochrechnung: ALLE 18,4 Mio Samples = ~205 GB → passt auf 816 GB.
ENTSCHEIDUNG: kompletten Datensatz materialisieren (kein Subset nötig), dann normales
gemischtes Mehr-Epochen-BC. On-the-fly-Streaming erst für Self-Play.

## Nächster Bau: Materialisierungs-Pipeline
1. extract.ts erweitern: pro Decision-Sample map-uint8 (quantisiert) + own/opp-Dicts +
   Label + Gegner-username (für Reputation-Lookup) → binär/shard schreiben.
2. Python: Shards lesen, featurize (inkl. reputation.json), als Trainings-Tensoren.
3. Volle Extraktion (2 Commit-Checkouts, Fleet-parallel) → ~205 GB.
4. BC-Trainer (bc_train.py) auf echte Daten umstellen (statt synthetisch) → trainieren.

## MATERIALISIERUNGS-PIPELINE FERTIG + auf echten Daten verifiziert (2026-09-07)
- env/materialize.ts: Record→Shards. Pro Sample: Karte 18×90×180 float→uint8→zstd
  (~10.7 KB, gemessen) in <id>.maps ([uint32 len][zstd] je Block), + <id>.meta.zst
  (ctx+own+opps+intent+win). 21s/Spiel. win = Sieger-clientIDs für den Wert-Kopf.
- env/dataset.py: liest Shard, dequant Karte→[-1,1], featurize own/opp (+Reputation §F),
  actions.encode→Label. 100% vollständige Labels auf echtem Spiel.
- env/bc_real.py: echter BC-Schritt auf 5080 — Verlust 22.8→1.4, Kern-Treffer 3.7%→90%
  auf 64 echten Samples. GESAMTE Kette Record→Shard→Loader→Netz→Training BEWIESEN.

## Nur noch RUNS (kein Bau mehr) bis zum ersten BC-Netz:
1. Volle Materialisierung ~5.116 Records, 2 Commit-Checkouts, fleet-parallel → ~205 GB (~2-4h).
2. bc_real.py → echten Trainer: Multi-Spiel-Shuffle-Loader + Epochen + Checkpoint/Resume
   (bc_train.py hat Resume schon) → BC-Lauf auf der 5080.

## Container-Image gebaut + verifiziert (2026-09-07)
Flotte: 44 Kerne/58 Threads — Arch 8/16, M4 14, apollo-m2 8, apollo 6/12, node-1/2 je 4.
Docker auf allen Linux-Knoten (29.x). Deps über beide Commits IDENTISCH → ein Image.
env/Dockerfile → of-mat (3.5GB): Node+Engine+env, ENTRYPOINT-Wrapper wählt COMMIT zur
Laufzeit, cairo-Libs für canvas. materialize.ts --list (viele Records/Prozess, resume-fest).
Im Container getestet: --cpus=4, identische Shards (1397 Samples/22s). 
User-Vorgaben: apollo-m2 NATIV (kein Container), M4 NICHT anfassen, apollo CPU-gedeckelt (Jellyfin).

## Für den vollen Lauf (nach GO + du-Scan):
1. Image an node-1/node-2/apollo verteilen (docker save|ssh load, ~3.5GB).
2. Fetch alle gerankten (12.967, ~4.6h node-1). 3. Records→Arch.
4. Materialisieren: Liste nach Commit gruppieren, je Knoten Container --cpus + --list;
   apollo-m2 nativ. Filter K=30 (Angriff max 1/Spieler/30 Ticks) → ~470GB.
5. bc_train auf echte Shards.

## VOLLER LAUF GESTARTET (2026-09-07, GO)
- FETCH: node-1 systemd of-fetch, alle gerankten 12.967 (night/allranked.txt), ~0.8/s ~4.6h
  → Ziel ~18.083 Records in ~/openfront-night/records.
- PHASE-1 MATERIALISIERUNG: Arch systemd of-mat1, die 5.050 vorhandenen Records, 28 Container
  (2 Commits × 14), --cpus=1, resume-fest → data/shards. ~2h.
- Image of-mat auf node-2 + apollo verteilt (node-1 nach Fetch).
- scraper/mat_launch.py: Launcher je Knoten (gruppiert nach Commit, N parallele Container).
  Merke: N so wählen, dass N×Commits ≈ Kerne (Phase1 hat 28 auf 16 Threads → leicht über, ok).
- Monitor b4k67hy5l wacht über beide (Abschluss/Fehler).

## Phase 2 (nach Fetch): die neuen ~13k materialisieren, fleet-verteilt
Records auf node-1 → an Arch/node-2/apollo verteilen (oder je Knoten Chunk), mat_launch je Knoten:
Arch n=7, node-2 n=2, apollo n=3 (Jellyfin-schonend), node-1 n=2. Dann bc_train auf alle Shards.
FILTER K=30 ist noch NICHT im Materializer — aktuell wird JEDE Entscheidung gespeichert
(→ ~205GB für 5116, hochgerechnet ~700GB für 18k). Vor Phase 2 K=30 einbauen ODER Platte prüfen.