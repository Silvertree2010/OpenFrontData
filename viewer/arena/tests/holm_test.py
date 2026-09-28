#!/usr/bin/env python3
"""Holm-Korrektur in auswertung.py (ohne numpy, ohne Daten).

  python3 arena/tests/holm_test.py

1. Handbeispiel mit bekanntem Ergebnis, None bleibt None, Monotonie.
2. Unter H0 (A/A: gleiche Verteilung, die Seiten tauschbar) mit 38 korrelierten Kennzahlen und
   120 Paaren: "irgendein p < 0,05" passiert unkorrigiert in einem grossen Teil der Läufe, mit Holm
   höchstens in ~5 % (plus Simulationsfehler). Die Messung muss durchfallen können: unkorrigiert
   muss sie über 5 % liegen.
3. Unter H1 (eine Kennzahl mit echtem Unterschied) findet Holm den Effekt.
4. vergleiche() rechnet p_holm über die Spielweise-Familie aus echten Zeilenformen.
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auswertung as AW  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


# 1. Handbeispiel: sortiert 0,005·4=0,02; 0,01·3=0,03; 0,03·2=0,06; 0,04·1=0,04 → max 0,06
h = AW.holm([0.01, 0.04, 0.03, 0.005])
pruefe("Handbeispiel", [round(x, 12) for x in h] == [0.03, 0.06, 0.06, 0.02], str(h))
pruefe("None bleibt None, zählt nicht mit", AW.holm([0.02, None, 0.04]) == [0.04, None, 0.04])
pruefe("nie kleiner als roh, nie über 1", all(a >= b and a <= 1 for a, b in zip(AW.holm([0.5, 0.9, 0.001]), [0.5, 0.9, 0.001])))

# 2./3. Simulation: je Paar ein gemeinsamer Partiefaktor (korreliert die Kennzahlen wie im Spiel)
rng = random.Random(4)
K, N, SIM = 38, 120, 400


def lauf(effekt: float):
    diffs = [[] for _ in range(K)]
    for _ in range(N):
        g = rng.gauss(0, 1)
        for k in range(K):
            x = 0.6 * g + rng.gauss(0, 1)
            y = 0.6 * g + rng.gauss(0, 1)
            if k < 0.5 * K:                   # Zählgrössen mit Bindungen (oft 0)
                x, y = max(0, round(x)), max(0, round(y))
            diffs[k].append(x - y + (effekt if k == K - 1 else 0))
    p = []
    for d in diffs:
        plus, minus = sum(v > 0 for v in d), sum(v < 0 for v in d)
        p.append(AW.binom_p(min(plus, minus), plus + minus))
    return p


roh = korr = 0
for _ in range(SIM):
    p = lauf(0.0)
    roh += min(p) < 0.05
    korr += min(AW.holm(p)) < 0.05
fwer_roh, fwer_holm = roh / SIM, korr / SIM
pruefe("H0 unkorrigiert: familienweise Fehlerrate deutlich über 5 %", fwer_roh > 0.3, f"({fwer_roh:.1%})")
pruefe("H0 mit Holm: höchstens 5 % + 2,5 Punkte Simulationsfehler", fwer_holm <= 0.075, f"({fwer_holm:.1%})")
# Erwartung: Differenz-SD √2·1 → Effekt 0,8/√2 = 0,57 SD, P(d > 0) ≈ Φ(0,57) = 0,71; Vorzeichentest
# mit n = 120 bei α/38 (erster Holm-Schritt) hat nach Normalnäherung Macht ≈ 0,88. Schwelle 75 %.
treffer = sum(AW.holm(lauf(0.8))[K - 1] < 0.05 for _ in range(60))
pruefe("H1 (Effekt 0,8 SD auf einer Kennzahl): Holm findet ihn meist (erwartet ~88 %)", treffer >= 45,
       f"({treffer} von 60)")

# 4. vergleiche() mit Zeilen in Arena-Form
def zeile(i, seite, verschiebung):
    s = {k: (i % 7) + (verschiebung if k == "gold_min" else 0) for k, *_ in AW.SPIELWEISE}
    return {"seite": seite, "spiel_id": f"p{i}", "platz": 10 + i % 5, "ueberleben_ticks": 5000,
            "gebiet": {}, "verlauf": [], "handlungen_je_1000": 20, "abbruchgrund": "tot",
            "spielweise": {"summe": s, "verlauf": {"t": [250, 500], "gold_min": [1.0, 2.0]}}}


a = {f"p{i}": zeile(i, "netz", 5) for i in range(40)}
b = {f"p{i}": zeile(i, "netz", 0) for i in range(40)}
v = AW.vergleiche(a, b)
sw = v.get("spielweise") or {}
pruefe("vergleiche: Spielweise-Familie vollständig", v.get("spielweise_familie") == len(AW.SPIELWEISE),
       f"({v.get('spielweise_familie')} von {len(AW.SPIELWEISE)})")
pruefe("vergleiche: echter Unterschied bei gold_min, p_holm klein",
       sw["gold_min"]["a_hoeher"] == 40 and sw["gold_min"]["p_holm"] < 1e-6)
pruefe("vergleiche: gleiche Kennzahlen p_holm = 1", sw["verrat"]["p_holm"] == 1.0)
pruefe("kurven: Median je Tick", AW.kurven(list(a.values()))["gold_min"][0][:2] == [250, 1.0])
print("\n" + ("ALLES OK" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
