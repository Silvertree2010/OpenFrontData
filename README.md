# OpenFront-KI

Ein Reinforcement-Learning-Agent für [OpenFront.io](https://openfront.io), gebaut von September 2026 an
und im September 2026 eingestellt. Das Repository ist als Startrampe gedacht: Wer an einer Spiel-KI für
OpenFront arbeiten will, muss die Datenaufbereitung, den Simulator-Zugriff und die Messtechnik nicht
noch einmal von vorn bauen.

Der Agent ist ungefähr so stark wie ein durchschnittlicher menschlicher Spieler. Er schlägt seine eigene
Ausgangsversion deutlich, aber er gewinnt keine Partien gegen starke Menschen. Was gemessen wurde und was
nicht funktioniert hat, steht in [ERGEBNISSE.md](ERGEBNISSE.md).

## Aufbau

Vier Stufen, jede für sich benutzbar:

1. **Sammeln** (`scraper/`, `night/`) — öffentliche Partien von OpenFront holen, dazu gezielt die Partien
   der Spitzenspieler aus dem Leaderboard. Ergebnis sind Replay-Dateien (GameRecords).
2. **Materialisieren** (`materializer/`) — ein Replay wird in der echten Spiel-Engine nachgespielt und
   bei jeder Entscheidung eine Beobachtung samt gewählter Aktion herausgeschrieben. Format v2:
   `<gid>.meta.zst` (eine JSON-Zeile je Entscheidung), `.cells`, `.maps`, `.zusatz.zst`.
3. **Verhaltensklonen** (`trainer/train.py`, `env/`) — daraus wird ein Netz trainiert, das menschliche
   Entscheidungen nachahmt. Diese Netze heissen hier `bc*` und `ur1`; `ur1` ist die Basis aller
   RL-Läufe.
4. **RL-Schleife** (`trainer/rl_schleife.py`) — der Agent spielt gegen die eingebauten Nations und Bots,
   die Partien werden bewertet und das Netz per advantage-gewichteter Regression (AWR) nachgezogen,
   mit KL-Anker an `ur1`, damit es nicht wegdriftet.

Die Arena (`viewer/arena/arena.ts`) startet Partien in der echten Engine ohne Browser, mit bis zu 200
KI-Spielern gleichzeitig. Die Netze antworten über einen HTTP-Inferenzserver
(`trainer/inf_gpu.py` für Stapelbetrieb auf der GPU, `trainer/inf_d0.py` für einzelne Anfragen).

## Schnellstart

Voraussetzungen: Node 22+, Python 3.12+ mit PyTorch, eine NVIDIA-GPU für das Training.
Die Spiel-Engine selbst liegt nicht hier; sie wird als `vendor/openfront` daneben ausgecheckt
(Version v0.34 der Engine, siehe `viewer/arena/README` und die Patches in `viewer/*.patch`).

```bash
# Inferenzserver mit einem Checkpoint aus dem Release
python trainer/inf_gpu.py --ckpt checkpoints/ur1.pt --port 8681 --device cuda \
    --schwelle 0.0173 --reputation data/reputation.json --ziehen --top-k 4

# eine Arena-Partie, 25 KI-Spieler, 100 Bots
cd viewer && npx tsx arena/arena.ts --inf http://127.0.0.1:8681/act \
    --partien 1 --ki 25 --bots 100 --karte zufall --groesse Compact --ticks 12000 \
    --aus /tmp/arena.jsonl

# eine RL-Schleife starten
python trainer/rl_schleife.py --name lauf1 --start checkpoints/ur1.pt --anker checkpoints/ur1.pt \
    --ziel platz --phi 0.75,0,0.25 --gae-lambda 0.95 --karte zufall --ki auto --bots auto \
    --server-art gpu --server 5 --partien 40 --iterationen 200
```

## Checkpoints

Im Release liegen die Netze, damit niemand die Vorstufen nachbauen muss:

| Datei | Was es ist |
|---|---|
| `bc3.pt` | Verhaltensklon auf 32 Mio Entscheidungen, D1-Netz, 6.0 Mio Parameter |
| `ur1.pt` | die bereinigte Basis, Nullpunkt aller Elo-Angaben |
| `rl7_i61.pt`, `rl8_i40.pt` | frühe RL-Läufe, Einzel-KI gegen Nations |
| `rl10_i80.pt` | bester Lauf mit vollen Lobbys, Selbstspiel |
| `rl11_i40.pt`, `rl11_i64.pt` | letzter Lauf, Training gegen einen Pool alter Netze |

## Verzeichnisse

| Ordner | Inhalt |
|---|---|
| `trainer/` | Training, RL-Schleife, Inferenzserver, Belohnung, Tests |
| `viewer/arena/` | Arena: Partien in der Engine, ohne Browser |
| `viewer/ki-beobachter/` | Browser-Erweiterung, die ein Netz in einer Live-Partie spielen lässt |
| `materializer/` | Replay zu Trainingsdaten, Format v2 |
| `scraper/`, `night/` | Partien sammeln, täglicher Sammel- und Materialisierlauf |
| `env/` | Beobachtungscodierung, Datensatz, Netzdefinitionen |
| `docs/` | Spielmechanik, Aktionsraum, Zielwahl-Entwurf, Elo-Analyse |
| `scripts/` | Hilfsskripte für Läufe und Auswertung |
| `data/` | Reputationstabelle, Leaderboard-Auszug, Beispiel-Replays |

## Sprache

Code, Kommentare und Dokumentation sind auf Deutsch. Die Bezeichner im Spiel selbst
(`atype`, `BUILD_UNIT`, `tick`) bleiben englisch.

## Lizenz

MIT, siehe [LICENSE](LICENSE). Die Patches unter `viewer/` verändern Dateien von OpenFront und stehen
unter der Lizenz dieses Projekts; die Engine selbst gehört nicht zu diesem Repository.
