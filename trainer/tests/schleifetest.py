#!/usr/bin/env python3
"""Tests für die Hilfsfunktionen der RL-Schleife (ohne Server, ohne Arena).

  python trainer/tests/schleifetest.py

1. mcnemar_p: exakte Werte an bekannten Stellen.
2. sieg_vergleich: Paarung über spiel_id, ungepaarte Partien zählen nicht.
3. partie_kennzahlen: R nach Ziel, R_platz unabhängig vom Ziel.
4. --ziel und --eval-gegner kommen in den Argumenten an.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rl_schleife as S  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


def fast(a, b):
    return abs(a - b) < 1e-12


# 1. McNemar exakt
pruefe("mcnemar_p(0, 0) = 1", S.mcnemar_p(0, 0) == 1.0)
pruefe("mcnemar_p(5, 5) = 1", S.mcnemar_p(5, 5) == 1.0)
pruefe("mcnemar_p(9, 0) = 2/512", fast(S.mcnemar_p(9, 0), 2 / 512))
pruefe("mcnemar_p symmetrisch", fast(S.mcnemar_p(3, 11), S.mcnemar_p(11, 3)))
# 3:11 → 2·P(X ≤ 3), X ~ Bin(14, 0,5) = 2·(1 + 14 + 91 + 364)/16384
pruefe("mcnemar_p(3, 11)", fast(S.mcnemar_p(3, 11), 2 * 470 / 16384))


# 2. Siege gepaart
def zeile(sid, sieg, **k):
    z = {"spiel_id": sid, "sieg": sieg, "platz": 5, "von": 473, "platz_ohne_bots": 2, "von_ohne_bots": 73,
         "land_ende": 100, "land_max_ende": 400, "ueberleben_ticks": 12000, "abbruchgrund": "tick_limit",
         "handlungen_je_1000": 20.0, "aktionen": {"ATTACK": 3}}
    z.update(k)
    return z


a_ = [zeile("p1", True), zeile("p2", True), zeile("p3", False), zeile("p4", True), zeile("nur_a", True)]
b_ = [zeile("p1", True), zeile("p2", False), zeile("p3", True), zeile("p4", False), zeile("nur_b", True)]
v = S.sieg_vergleich(a_, b_)
pruefe("sieg_vergleich: nur gemeinsame spiel_id", v["sieg_paare"] == 4, str(v))
pruefe("sieg_vergleich: Zählung", v["siege_a"] == 3 and v["siege_b"] == 2
       and v["sieg_nur_a"] == 2 and v["sieg_nur_b"] == 1, str(v))
pruefe("sieg_vergleich: p", fast(v["p_sieg_mcnemar"], S.mcnemar_p(2, 1)))

# 3. Kennzahlen je Ziel
zs = [zeile("p1", True, platz_ohne_bots=1, land_ende=400), zeile("p2", False)]
k_platz = S.Schleife.partie_kennzahlen(zs, "platz")
k_sieg = S.Schleife.partie_kennzahlen(zs, "sieg")
k_geb = S.Schleife.partie_kennzahlen(zs, "gebiet")
p2 = 71 / 72
pruefe("platz: R_mittel = R_platz_mittel", fast(k_platz["R_mittel"], k_platz["R_platz_mittel"])
       and fast(k_platz["R_mittel"], (1 + p2) / 2))
pruefe("sieg: R_mittel", fast(k_sieg["R_mittel"], (1.0 + 0.5 * p2) / 2), str(k_sieg["R_mittel"]))
pruefe("gebiet: R_mittel", fast(k_geb["R_mittel"], (1.0 + 0.5 * 0.25) / 2), str(k_geb["R_mittel"]))
pruefe("R_platz_mittel hängt nicht vom Ziel ab",
       fast(k_sieg["R_platz_mittel"], k_platz["R_platz_mittel"]) and fast(k_geb["R_platz_mittel"], k_platz["R_platz_mittel"]))
pruefe("siege gezählt", k_sieg["siege"] == 1)

# 4. Argumente
a = S.argumente(["--ziel", "sieg", "--eval-gegner", "/x/rl3_i100.pt"])
pruefe("Argumente: --ziel/--eval-gegner", a.ziel == "sieg" and a.eval_gegner == "/x/rl3_i100.pt")
a = S.argumente(["--phi", "0.75,0,0.25"])
pruefe("Argumente: --phi", a.phi == "0.75,0,0.25" and S.B.gewichte_aus_text(a.phi) == S.B.Gewichte(0.75, 0.0, 0.25))
try:
    S.B.gewichte_aus_text("0.75,0")
    pruefe("--phi mit zwei Zahlen bricht ab", False)
except ValueError:
    pruefe("--phi mit zwei Zahlen bricht ab", True)
a = S.argumente(["--ziehen-koepfe", "atype,magnitude", "--awr-koepfe", "atype,magnitude"])
pruefe("Argumente: Köpfe", a.ziehen_koepfe == "atype,magnitude" and a.awr_koepfe == "atype,magnitude")
pruefe("Argumente: Köpfe Standard atype",
       S.argumente([]).ziehen_koepfe == "atype" and S.argumente([]).awr_koepfe == "atype")
a = S.argumente(["--eval-spielweise"])
pruefe("Argumente: --eval-spielweise", a.eval_spielweise is True and S.argumente([]).eval_spielweise is False)
a = S.argumente(["--ki", "auto", "--bots", "auto", "--groesse", "Normal"])
pruefe("Argumente: auto", a.ki == "auto" and a.bots == "auto" and a.groesse == "Normal")
a = S.argumente(["--ki", "100", "--bots", "400"])
pruefe("Argumente: Zahlen bleiben Zahlen", a.ki == 100 and a.bots == 400 and S.argumente([]).groesse == "Compact")
a = S.argumente(["--gegner-pool", "/x/a.pt, /y/b.pt"])
pruefe("Argumente: --gegner-pool", a.gegner_pool == "/x/a.pt, /y/b.pt" and S.argumente([]).gegner_pool is None)
zs = [zeile("p1", True, srv=0), zeile("p2", False, srv=1), zeile("p3", False, srv=2)]
pruefe("partie_kennzahlen: nur eigene Server", S.Schleife.partie_kennzahlen(zs, "platz", 2)["episoden"] == 2)
pruefe("partie_kennzahlen: ohne nur_srv alle", S.Schleife.partie_kennzahlen(zs, "platz")["episoden"] == 3)
pruefe("partie_kennzahlen: leerer Filter fällt zurück",
       S.Schleife.partie_kennzahlen([zeile("p1", True, srv=3)], "platz", 2)["episoden"] == 1)
pruefe("eval_bots: auto wird 400", S.eval_bots(S.argumente(["--bots", "auto"])) == 400)
pruefe("eval_bots: Zahl bleibt", S.eval_bots(S.argumente(["--bots", "100"])) == 100)
pruefe("Argumente: Standard platz, Gegner leer", S.argumente([]).ziel == "platz" and S.argumente([]).eval_gegner is None)

print(f"\n{'ALLES OK' if not FEHLER else 'FEHLER: ' + ', '.join(FEHLER)}")
sys.exit(1 if FEHLER else 0)
