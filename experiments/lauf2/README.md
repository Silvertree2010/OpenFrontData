# Vorbereiteter Lauf 2 — räumlicher Verlust

Stand 09.09.2026, **nie ausgeführt**. Zwei Änderungen am Trainer, beide hinter
Schaltern mit neutralen Vorgaben, also ohne Schalter exakt das alte Verhalten.

## Warum

Gemessen mit `env/eval_spatial.py` auf dem Validierungsanteil (488 ungesehene
Partien, 4122 Kachel-Samples, Checkpoint Epoche 0 Schritt 47000):

| | Wert |
|---|---|
| Grob-Kachel exakt (16200 Zellen) | 2,69 % |
| echte Zelle unter den Top 100 | 36,7 % |
| Median-Rang der echten Zelle | 260 von 16200 |
| **weiter als 32 Kacheln daneben** | **83,6 %** |

Der Fein-Kopf ist schlechter als eine Konstante: bedingt auf die **echte**
Grobzelle liegt er im Mittel 4,8 Kacheln daneben, eine zufällige Feinzelle
schafft 4,1, die blosse Zellmitte 3,1.

Diagnose: Der Verlust ist reine Kreuzentropie auf exakte Übereinstimmung. Eine
Vorhersage eine Kachel daneben wird genauso hart bestraft wie eine 500 Kacheln
daneben. Das gibt dem Netz kein Signal in Richtung „näher dran ist besser".

## Die Schalter

- `--coarse-sigma` — Breite eines weichen Gauss-Ziels über dem 180×90-Gitter,
  Nachbarschaft zählt in **beiden** Achsen, nicht im flachen Index.
- `--coarse-soft-mix` — Mischung zwischen hartem und weichem Ziel.
- `--fine-off` — nimmt den Fein-Kopf aus Verlust und Vorhersage und setzt
  stattdessen die Mitte der gewählten Grobzelle. Der Kopf bleibt im Netz, damit
  bestehende Checkpoints weiter laden.

Entsprechend `COARSE_SIGMA`, `FINE_OFF` als Umgebungsvariablen in `bc_train.py`.

## Was fehlt

Der Nachweis, dass der abstandsbewusste Verlust den Kachelabstand tatsächlich
senkt. Der Vorversuch lief, wurde aber vom Stromausfall am 09.09. unterbrochen.
Vor einem echten Lauf: erst messen, dann trainieren.

## Der wahrscheinlich wichtigere Befund

Am 10.09. nachgesehen: **der Kachel-Kopf ist überhaupt nicht maskiert.** Der
Gegner-Zeiger wird maskiert, der Kachel-Kopf nicht. Das Netz wählt frei aus
16200 Zellen, obwohl für einen Angriff nur die Kacheln an der eigenen Grenze in
Frage kommen, für ein Gebäude nur eigenes Gebiet, für ein Boot nur Küste. Das
sind je nach Lage ein paar hundert.

Das erklärt die 83,6 Prozent besser als der Verlust: das Netz darf Antworten
geben, die im Spiel gar nicht möglich sind. Eine Maskierung auf mögliche Ziele
wäre vermutlich der grössere Hebel als jede Änderung am Verlust — und beides
schliesst sich nicht aus.
