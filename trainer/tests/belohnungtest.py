#!/usr/bin/env python3
"""Tests für belohnung.py (ohne torch, ohne Daten).

  python trainer/tests/belohnungtest.py

1. Teleskop: G_t = γ^(T−1−t)·R − Φ_t für Zufalls-Φ, γ = 1 und γ = 0,97.
2. Politik bleibt optimal: Zufalls-MDPs mit endlichem Horizont, Endbelohnung je Endzustand.
   Mit PBRS (Φ(Ende) = 0) ist Q_geformt = Q − Φ(s), die argmax-Politik identisch.
3. Gegenprobe (die Messung muss durchfallen können): ein Überlebensbonus je Schritt ist keine
   Potenzialdifferenz und kippt in einem Totstell-MDP die optimale Politik.
4. Φ: Tote haben 0, f(0)=0, f(1)=1, monoton; Φ in [0, c+w_u+w_g]; Platzwert an den Rändern.
5. fuer_partie: zwei KIs gemischt in einer Partie werden sauber getrennt.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import belohnung as B  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


rng = np.random.default_rng(7)

# 1. Teleskop
for gamma in (1.0, 0.97):
    worst = 0.0
    for _ in range(200):
        T = int(rng.integers(1, 60))
        phi = rng.uniform(0, 1.5, T)
        R = float(rng.uniform(0, 1))
        r, G = B.rueckgaben(phi, R, gamma)
        soll = gamma ** (T - 1 - np.arange(T)) * R - phi
        worst = max(worst, float(np.abs(G - soll).max()))
        # Summe der Formungsterme = −Φ_0 (γ=1): die Formung verschiebt nur um eine Konstante je Start
    pruefe(f"Teleskop γ={gamma}", worst < 1e-9, f"max|Δ|={worst:.2e}")


# 2. Politik-Invarianz auf Zufalls-MDPs
def loese(P, r_schritt, R_ende, H, gamma):
    """Rückwärtsinduktion. P (S,A,S'), r_schritt (H,S,A,S'), R_ende (S,) am Horizont."""
    S, A_, _ = P.shape
    V = R_ende.copy()
    Qs = []
    for h in range(H - 1, -1, -1):
        Q = (P * (r_schritt[h] + gamma * V[None, None, :])).sum(2)
        Qs.append(Q)
        V = Q.max(1)
    return Qs[::-1]


gleich, n_mdp, max_q = 0, 300, 0.0
for _ in range(n_mdp):
    S, A_, H = 6, 3, 5
    gamma = float(rng.choice([1.0, 0.95]))
    P = rng.dirichlet(np.ones(S), size=(S, A_))
    R_ende = rng.uniform(0, 1, S)                          # Endplatz je Endzustand
    r0 = np.zeros((H, S, A_, S))                            # eigentliche Belohnung nur am Ende
    phi = rng.uniform(0, 1, (H + 1, S))
    phi[H] = 0.0                                            # Φ(Ende) = 0
    F = gamma * phi[1:, None, None, :] - phi[:-1, :, None, None]
    Q0 = loese(P, r0, R_ende, H, gamma)
    Q1 = loese(P, r0 + F, R_ende, H, gamma)
    ok = all((q0.argmax(1) == q1.argmax(1)).all() for q0, q1 in zip(Q0, Q1))
    max_q = max(max_q, max(float(np.abs(q1 - (q0 - phi[h][:, None])).max()) for h, (q0, q1) in enumerate(zip(Q0, Q1))))
    gleich += ok
pruefe("PBRS lässt die optimale Politik gleich", gleich == n_mdp, f"{gleich}/{n_mdp}")
pruefe("Q_geformt = Q − Φ(s)", max_q < 1e-9, f"max|Δ|={max_q:.2e}")

# 3. Gegenprobe: Totstell-MDP. Zustand 0 = am Leben. Aktion 0 "angreifen": sofort Ende mit
# Platzwert 0,8. Aktion 1 "abwarten": bleibt am Leben, am Horizont Platzwert 0,5.
# Ein Bonus b je überlebtem Schritt (keine Potenzialdifferenz) macht Abwarten besser.
H = 5
P = np.zeros((3, 2, 3))
P[0, 0, 1] = 1.0            # angreifen → Zustand 1 (Ende gut, absorbierend)
P[0, 1, 0] = 1.0            # abwarten → bleibt 0
P[1, :, 1] = 1.0
P[2, :, 2] = 1.0
R_ende = np.array([0.5, 0.8, 0.0])
r0 = np.zeros((H, 3, 2, 3))
bonus = r0.copy()
bonus[:, 0, 1, 0] = 0.1     # "überlebt" je Schritt
Q0 = loese(P, r0, R_ende, H, 1.0)
Q_bonus = loese(P, r0 + bonus, R_ende, H, 1.0)
phi = np.zeros((H + 1, 3))
phi[:H, 0] = 0.6            # Überleben als Potenzial statt als Bonus
F = phi[1:, None, None, :] - phi[:-1, :, None, None]
Q_pbrs = loese(P, r0 + F, R_ende, H, 1.0)
pruefe("Gegenprobe: ohne Formung greift sie an", int(Q0[0][0].argmax()) == 0)
pruefe("Gegenprobe: Überlebensbonus kippt die Politik (Totstellen)", int(Q_bonus[0][0].argmax()) == 1)
pruefe("Gegenprobe: Überleben als Potenzial kippt nichts", int(Q_pbrs[0][0].argmax()) == 0)

# 4. Φ-Eigenschaften
g = np.linspace(0, 1, 101)
f = B.f_gebiet(g, 90)
pruefe("f(0)=0, f(1)=1", abs(f[0]) < 1e-12 and abs(f[-1] - 1) < 1e-12)
pruefe("f monoton", bool((np.diff(f) > 0).all()))
gw = B.GEWICHTE
phi = B.potenzial(rng.uniform(0, 1, 1000), rng.integers(0, 90, 1000), 90, True)
pruefe("Φ in [0, c+w_u+w_g]", bool(phi.min() >= 0 and phi.max() <= gw.c + gw.w_u + gw.w_g + 1e-12))
pruefe("Φ(tot) = 0", float(B.potenzial(0.3, 10, 90, False)) == 0.0)
pruefe("Platzwert 1 → 1, von → 0", float(B.platz_wert(1, 90)) == 1.0 and float(B.platz_wert(90, 90)) == 0.0)

# 5. fuer_partie mit zwei KIs
zeilen = []
for t in range(10):
    for cid, g0 in (("ki000001", 0.01), ("ki000002", 0.02)):
        if cid == "ki000002" and t > 6:
            continue                                        # KI 2 stirbt nach Tick 6
        zeilen.append({"clientID": cid, "tick": t, "rl": {"gebiet": g0 * (1 + t / 10), "tot": t, "spieler": 90}})
erg = {"ki000001": {"platz": 3, "von": 90}, "ki000002": {"platz": 60, "von": 90}}
eps = B.fuer_partie(zeilen, erg)
e1, e2 = eps["ki000001"], eps["ki000002"]
pruefe("fuer_partie: Längen je KI", len(e1["zeilen"]) == 10 and len(e2["zeilen"]) == 7)
pruefe("fuer_partie: G_0 = R − Φ_0",
       abs(e1["G"][0] - (e1["R"] - e1["phi"][0])) < 1e-12 and abs(e2["G"][0] - (e2["R"] - e2["phi"][0])) < 1e-12)
pruefe("fuer_partie: besser platziert, höheres R", e1["R"] > e2["R"])

# 6. Ziele sieg und gebiet (Endwert)
def fast(a, b):
    return abs(a - b) < 1e-12


sieger = {"platz": 5, "von": 473, "platz_ohne_bots": 1, "von_ohne_bots": 73, "sieg": True,
          "land_ende": 40000, "land_max_ende": 40000}
zweiter = {"platz": 6, "von": 473, "platz_ohne_bots": 2, "von_ohne_bots": 73, "sieg": False,
           "land_ende": 30000, "land_max_ende": 40000}
toter = {"platz": 300, "von": 473, "platz_ohne_bots": 60, "von_ohne_bots": 73, "sieg": False,
         "land_ende": 0, "land_max_ende": 40000}
pruefe("platz: wie Version 2 (ohne Bots)", fast(B.endwert(zweiter), 71 / 72))
pruefe("sieg: Sieger 1, Zweiter 0,5·p", fast(B.endwert(sieger, "sieg"), 1.0)
       and fast(B.endwert(zweiter, "sieg"), 0.5 * 71 / 72))
pruefe("sieg: Sieg zählt mehr als Platz 2 (Abstand ≥ 0,5)",
       B.endwert(sieger, "sieg") - B.endwert(zweiter, "sieg") >= 0.5)
pruefe("gebiet: Sieger 1, Zweiter 0,5·0,75, Toter 0", fast(B.endwert(sieger, "gebiet"), 1.0)
       and fast(B.endwert(zweiter, "gebiet"), 0.375) and fast(B.endwert(toter, "gebiet"), 0.0))
pruefe("gebiet: land_max 0 ergibt 0", fast(B.endwert({**toter, "land_max_ende": 0}, "gebiet"), 0.0))
pruefe("gebiet: Anteil höchstens 1", fast(B.endwert({**zweiter, "land_ende": 50000}, "gebiet"), 0.5))
for name, e, ziel in (("gebiet ohne land_ende", {k: v for k, v in zweiter.items() if k != "land_ende"}, "gebiet"),
                      ("sieg ohne sieg", {k: v for k, v in zweiter.items() if k != "sieg"}, "sieg"),
                      ("unbekanntes Ziel", zweiter, "gold")):
    try:
        B.endwert(e, ziel)
        pruefe(f"Abbruch: {name}", False)
    except ValueError:
        pruefe(f"Abbruch: {name}", True)
try:
    B.gewichte_fuer("gold")
    pruefe("gewichte_fuer: unbekanntes Ziel bricht ab", False)
except ValueError:
    pruefe("gewichte_fuer: unbekanntes Ziel bricht ab", True)
for ziel in B.ZIELE:
    pruefe(f"gewichte_fuer({ziel}) liefert Gewichte", isinstance(B.gewichte_fuer(ziel), B.Gewichte))
erg2 = {"ki000001": sieger, "ki000002": zweiter}
eps2 = B.fuer_partie(zeilen, erg2, B.GEWICHTE, B.GAMMA, "sieg")
pruefe("fuer_partie(ziel=sieg): R je KI", fast(eps2["ki000001"]["R"], 1.0)
       and fast(eps2["ki000002"]["R"], 0.5 * 71 / 72))
pruefe("platz behält die rl3-Gewichte", B.gewichte_fuer("platz") == B.GEWICHTE)
pruefe("sieg: w_u = 0", B.gewichte_fuer("sieg").w_u == 0.0)
pruefe("beschreibung nennt das Ziel", B.beschreibung(ziel="gebiet")["ziel"] == "gebiet"
       and "land_ende" in B.beschreibung(ziel="gebiet")["formel"])

# 7. GAE aus den aufgezeichneten Werten
werte = [0.5, 0.6, 0.55, 0.7]
R7 = 0.9
d1, a1 = B.gae(werte, R7, 1.0, 1.0)
pruefe("GAE λ=1 ist R − V_t", all(fast(a1[t], R7 - werte[t]) for t in range(len(werte))),
       str(np.round(a1, 4).tolist()))
d0, a0 = B.gae(werte, R7, 1.0, 0.0)
pruefe("GAE λ=0 ist der Ein-Schritt-Fehler", all(fast(a0[t], d0[t]) for t in range(len(werte))))
pruefe("letztes δ ist R − V_(T−1)", fast(d0[-1], R7 - werte[-1]))
d5, a5 = B.gae(werte, R7, 1.0, 0.5)
pruefe("Rekursion A_t = δ_t + γλ·A_(t+1)",
       all(fast(a5[t], d5[t] + 0.5 * a5[t + 1]) for t in range(len(werte) - 1)) and fast(a5[-1], d5[-1]))
pruefe("GAE auf leerer Episode", len(B.gae([], R7, 1.0, 0.95)[1]) == 0)
# Mit V = Φ und λ = 1 muss GAE genau die alte Monte-Carlo-Rendite geben
zeilen7 = [{"clientID": "k", "rl": {"gebiet": 0.01 * (t + 1), "tot": t, "spieler": 90}} for t in range(6)]
ep_mc = B.episode(zeilen7, R7)
for z, ph in zip(zeilen7, ep_mc["phi"]):
    z["rl"]["value"] = float(ph)
ep_gae = B.episode(zeilen7, R7, lam=1.0)
pruefe("V = Φ und λ = 1 gibt die alte Rendite",
       all(fast(ep_gae["A"][t], ep_mc["A"][t]) for t in range(len(zeilen7))))
pruefe("G bleibt die Monte-Carlo-Rendite", all(fast(ep_gae["G"][t], ep_mc["G"][t]) for t in range(len(zeilen7))))
ohne = [{"clientID": "k", "rl": {"gebiet": 0.01, "tot": 1, "spieler": 90}}]
try:
    B.episode(ohne, R7, lam=0.95)
    pruefe("ohne rl.value bricht GAE ab", False)
except ValueError:
    pruefe("ohne rl.value bricht GAE ab", True)
b = B.beschreibung(lam=0.95)
pruefe("beschreibung nennt GAE", b["vorteil"] == "gae" and b["lam_gae"] == 0.95
       and B.beschreibung()["vorteil"] == "mc")

print(f"\n{'ALLES OK' if not FEHLER else 'FEHLER: ' + ', '.join(FEHLER)}")
sys.exit(1 if FEHLER else 0)
