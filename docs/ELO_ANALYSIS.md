# Spielstil nach Elo — validiert die Filter-Annahme (2026-09-07, M4)

241.388 Spieler-in-Partie-Zeilen, einsortiert nach Gesamt-Elo (≥5 Partien).
"%" = Anteil, der in der Partie ≥1 davon gebaut/genutzt hat (robust gegen die
mehrdeutige Array-Positionssemantik in stats.units).

| Tier      |    n   | Sieg | Verrat | Kills | Fläche | Atom | H  | MIRV | SAM | Silo | Marine | Boot |
|-----------|-------:|-----:|-------:|------:|-------:|-----:|---:|-----:|----:|-----:|-------:|-----:|
| <1300     | 22767  | 0.5% | 0.15   | 0.3   |  5627  | 13%  | 4% |  0%  | 17% | 19%  |  21%   | 63%  |
| 1300-1450 | 79979  | 0.7% | 0.19   | 0.4   |  8642  | 20%  | 7% |  1%  | 26% | 26%  |  30%   | 70%  |
| 1450-1600 | 93269  | 1.4% | 0.23   | 0.7   | 17594  | 29%  |13% |  2%  | 36% | 37%  |  40%   | 75%  |
| 1600-1750 | 40150  | 3.7% | 0.35   | 1.3   | 43992  | 42%  |26% |  5%  | 50% | 52%  |  51%   | 80%  |
| >=1750    |  5223  |14.1% | 0.61   | 3.1   |168205  | 59%  |51% | 20%  | 67% | 73%  |  64%   | 85%  |

## Konsequenzen fürs Training
1. **Auf hohe Elo filtern ist essenziell, nicht optional.** BC auf der ganzen
   Population sieht fast nie einen Nuke — schwache Spieler bauen kaum welche.
   Der Filter entscheidet, ob fortgeschrittene Taktik in den Daten vorkommt.
2. **MIRV bleibt selten (20% selbst an der Spitze).** BC wird es unterlernen;
   das ist ein RL-/Endspiel-Ding.
3. **Starke Klassen-Schieflage** — Nuke/SAM/Silo-Aktionen brauchen beim BC
   Gewichtung, sonst gehen sie im Angriffs-Rauschen unter.

## Vorbehalt
Korrelational, teils Überlebens-Effekt (Starke leben länger → mehr Bauzeit).
Aber 13%→59% bei Atombomben ist zu groß für reine Zeit — Könnens-Unterschied real.
Alliance-/Diplomatie-Verhalten steckt NICHT in stats — braucht Intents (Replay).
