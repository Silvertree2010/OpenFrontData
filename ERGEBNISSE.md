# Ergebnisse und Sackgassen

Alle Zahlen sind gemessen, nicht geschätzt. Wo eine Messung nicht signifikant war, steht das dabei.

## Wie gemessen wird

Zwei Netze spielen dieselben Partien: gleiche Karte, gleiche Saat, gleiche Lobby. Aus den Paaren
entsteht ein Vorzeichentest über den erreichten Platz und ein exakter McNemar-Test über die Siege.
Aus dem Anteil der gewonnenen Paare wird eine Elo-Differenz gerechnet:

    Elo = 400 · log10(s / (1 − s)),  s = Anteil der Partien mit dem besseren Platz

Der Nullpunkt ist `ur1`. Mit 100 Paaren liegt die Unsicherheit bei etwa ±40 Elo, mit 200 Paaren bei
etwa ±30. Einzelne Messpunkte sagen deshalb wenig; erst mehrere in Folge sind ein Trend.

## Elo-Leiter gegen ur1

| Lauf | bester Messpunkt | Paare | Platz | p | ≈ Elo |
|---|---|---|---|---|---|
| rl8_i40 | z40 | 200 | 90 : 60 | 0.018 | +70 |
| rl10_i80 | z80 | 100 | 45 : 27 | 0.044 | +89 |
| rl11_i40 | z40 | 100 | 54 : 19 | 0.00005 | +181 |

rl11 ist der einzige Lauf, der gegen einen Pool alter Netze trainiert hat statt nur gegen sich selbst.
Er ist auch der einzige, der über +100 kommt.

Gegen Menschen heisst das: ungefähr Durchschnitt. Gegen die Spitze des Leaderboards ist der Abstand gross,
das Einkommen des Agenten liegt etwa Faktor 36 unter dem eines Spitzenspielers.

## Was funktioniert hat

- **Volle Lobbys statt Einzelpartien.** Ab rl9 spielen so viele KIs in einer Partie, wie im echten Spiel
  Menschen mitspielen (bis 125, je nach Karte). Der Durchsatz stieg von 12 auf über 50 Episoden je Minute,
  ohne dass sich an der Lernregel etwas änderte. Das war der grösste Einzelgewinn im ganzen Projekt.
- **Gegnerpool.** Je altem Checkpoint ein eigener Inferenzserver, die Arena verteilt die KI-Plätze reihum.
  Nur die Entscheidungen der aktuellen Politik kommen in die Trainingsdaten
  (`arena.ts --spur-nur-srv N`), weil die alten Netze eine andere Verhaltenspolitik sind.
- **Gepaarte Auswertung.** Gleiche Karte und Saat für beide Seiten senkt die Streuung so stark, dass
  100 Partien reichen, wo ungepaart mehrere hundert nötig wären.
- **KL-Anker an die Basis.** Ohne Anker driftet die Politik in wenigen Iterationen in einen Zustand ab,
  in dem sie fast nur noch eine Aktionsart wählt.

## Sackgassen

- **Siegbonus in der Belohnung** (`--ziel sieg`): kein messbarer Unterschied zum reinen Platzziel. Siege
  sind in einer Lobby mit 400 Bots zu selten, um als Signal zu taugen.
- **Gebietsziel** (`--ziel gebiet`): schlechter als Platz. Der Agent frisst Land und verliert danach.
- **Zufälliges Drehen der Formungsfaktoren Φ:** wirkungslos. Φ ist nur eine Baseline und ändert das
  Optimum nicht, nur die Varianz.
- **Reines Selbstspiel über viele Iterationen:** rl10 verbesserte sich bis Iteration 80 und blieb dann
  145 Iterationen lang auf der Stelle. Wer lange trainiert, braucht wechselnde Gegner.
- **Bauwerke:** Der Agent baut in kurzen Partien keine Raketensilos und keine SAM-Stellungen. Mit 8000
  Ticks Laufzeit baut dasselbe Netz 14 Silos und startet 49 Atombomben, SAM bleibt bei null. Das Problem
  ist also nicht das Wissen, sondern die Wirtschaft: Der Agent gibt 77 Prozent seines Einkommens aus,
  Menschen 86 bis 92 Prozent, und er hält 76 Prozent seiner Truppen statt der bei guten Spielern üblichen
  42 Prozent.

## Fallen, die Zeit gekostet haben

- **Zwei Kopien desselben Codes.** Läuft die Arena aus einem anderen Ordner als gedacht, misst man
  stundenlang das falsche Programm. Vor jedem Lauf die Prüfsummen von Laufzeit- und Arbeitskopie
  vergleichen.
- **Aufräumen mit `sort -u`** statt `sort -u -V`: löscht ab Iteration 100 die neuesten Spuren, weil
  `rl10i99` lexikographisch nach `rl10i104` kommt. Das Training lief danach auf unvollständigen Daten.
- **GPU-Speicher:** acht Inferenzserver und das Training gleichzeitig sprengen 16 GB. Die Schleife stoppt
  die Server, bevor sie trainiert.
- **`--bots auto` an ein Skript weiterreichen, das nur Zahlen kennt.** Die Bewertung scheiterte still,
  bis ein Mini-Testlauf vor dem echten Lauf eingeführt wurde.
- **Messfehler durch erzwungene Aktionen:** Wer beim Zählen von Bauwerken alle Zeilen auf `BUILD_UNIT`
  zwingt, misst seine eigene Vorgabe. Nur echte Bauentscheidungen zählen.

## Wo jemand ansetzen würde

1. Wirtschaft. Der Agent spart nicht. Eine Belohnung oder ein Kopf, der ausgegebenes Gold und gehaltene
   Truppen bewertet, ist der offensichtlichste nächste Schritt.
2. Längere Partien im Training. Silos und SAM erscheinen erst spät; mit 12000 Ticks sieht der Agent sie
   fast nie.
3. Mehr als eine Karte pro Bewertung und mehr Paare. Die Messung ist der Flaschenhals, nicht die GPU.
