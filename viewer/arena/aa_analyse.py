#!/usr/bin/env python3
"""A/A-Analyse: zwei Läufe DESSELBEN Netzes auf denselben Saaten, gepaart wie auswertung.py.

  python3 arena/aa_analyse.py aa.a.jsonl aa.b.jsonl [--effekt neu.jsonl alt.jsonl] [--sim 5000]

Unter H0 (gleiches Netz) ist jede Differenz reiner Zufall — beim Ziehen mit Top-k ist das der
Sampling-Zufall, weil Engine und Arena deterministisch sind. Ausgabe je Kennzahl:
  - Mittel/SD der gepaarten Differenz, Korrelation a/b über die Saaten und der Varianzgewinn der
    Paarung (Var ungepaart / Var gepaart; 1 heisst: die Paarung bringt nichts)
  - p (Vorzeichentest exakt, Wilcoxon) wie auswertung.py
Dazu die familienweite Falsch-Positiv-Rate, wenn man alle Kennzahlen anschaut und "irgendein
p < 0,05" als Befund nimmt: Vorzeichen-Umkehr-Simulation unter H0 auf den echten Differenzen
(erhält die Korrelation zwischen den Kennzahlen). Und die nötige Partienzahl:
  - Vorzeichentest für einen Anteil q "A besser" (α = 0,05 zweiseitig, Macht 0,8)
  - gepaarter Mittelwertvergleich für einen Effekt δ bei der gemessenen SD der A/A-Differenz;
    δ aus --effekt (z. B. bc3 gegen bc2) und dessen Hälfte.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auswertung import KENNZAHLEN, binom_p, lade, nach_seite, wilcoxon_p  # noqa: E402

ND = statistics.NormalDist()


def paare(pa: str, pb: str, seite: str = "netz"):
    a, b = nach_seite(lade(pa), seite), nach_seite(lade(pb), seite)
    ids = sorted(set(a) & set(b))
    aus = {}
    for name, hol, gross in KENNZAHLEN:
        x, y = [], []
        for i in ids:
            u, v = hol(a[i]), hol(b[i])
            if u is None or v is None:
                continue
            x.append(float(u))
            y.append(float(v))
        aus[name] = (x, y, gross)
    return aus, len(ids)


def n_vorzeichen(q: float, alpha: float = 0.05, macht: float = 0.8) -> float:
    za, zb = ND.inv_cdf(1 - alpha / 2), ND.inv_cdf(macht)
    return ((za * 0.5 + zb * math.sqrt(q * (1 - q))) / (q - 0.5)) ** 2


def n_mittel(delta: float, sd: float, alpha: float = 0.05, macht: float = 0.8) -> float:
    if delta == 0:
        return float("inf")
    za, zb = ND.inv_cdf(1 - alpha / 2), ND.inv_cdf(macht)
    return ((za + zb) * sd / abs(delta)) ** 2


def p_werte(d: list[float]) -> tuple[float, float | None]:
    plus = sum(1 for x in d if x > 0)
    minus = sum(1 for x in d if x < 0)
    return binom_p(min(plus, minus), plus + minus), wilcoxon_p(d)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--effekt", nargs=2, default=None, help="zwei JSONL (neu, alt) für die Effektgrösse")
    ap.add_argument("--sim", type=int, default=5000)
    ap.add_argument("--json", default=None)
    A = ap.parse_args(argv)

    daten, n_paare = paare(A.a, A.b)
    eff = paare(*A.effekt)[0] if A.effekt else {}
    bericht = {"paare": n_paare, "kennzahlen": {}}
    print(f"A/A: {n_paare} Paare\n")
    print(f"  {'Kennzahl':<20}{'n':>4}{'Mittel Δ':>11}{'SD Δ':>11}{'r(a,b)':>8}{'Paarung':>9}"
          f"{'p Vorz.':>9}{'p Wilc.':>9}{'Bindung':>8}")
    diffs = {}
    for name, (x, y, gross) in daten.items():
        d = [u - v for u, v in zip(x, y)]
        diffs[name] = d
        n = len(d)
        if n < 3:
            continue
        sd = statistics.stdev(d)
        r = statistics.correlation(x, y) if statistics.pstdev(x) > 0 and statistics.pstdev(y) > 0 else float("nan")
        var_ung = statistics.variance(x) + statistics.variance(y)
        gewinn = var_ung / max(sd ** 2, 1e-18)
        pv, pw = p_werte(d)
        bind = sum(1 for v in d if v == 0)
        q = {"n": n, "mittel": statistics.fmean(d), "sd": sd, "r": r, "paarungsgewinn": gewinn,
             "p_vorzeichen": pv, "p_wilcoxon": pw, "bindungen": bind}
        if name in eff:
            ex, ey, _ = eff[name]
            ed = [u - v for u, v in zip(ex, ey)]
            if len(ed) >= 3:
                delta = statistics.fmean(ed)
                besser = sum(1 for v in ed if (v > 0) == gross and v != 0)
                nz = sum(1 for v in ed if v != 0)
                q.update(effekt_mittel=delta, effekt_q=besser / nz if nz else None,
                         n_fuer_effekt=n_mittel(delta, sd), n_fuer_halben_effekt=n_mittel(delta / 2, sd))
        bericht["kennzahlen"][name] = q
        print(f"  {name:<20}{n:>4}{q['mittel']:>11.4g}{sd:>11.4g}{r:>8.2f}{gewinn:>9.2f}"
              f"{pv:>9.3f}{(pw if pw is not None else float('nan')):>9.3f}{bind:>8}")

    # Familienweise Falsch-Positiv-Rate unter H0: Vorzeichen je Paar zufällig umkehren, für alle
    # Kennzahlen gemeinsam (dieselbe Umkehr je Partie, die Kennzahlen bleiben korreliert).
    namen = [n for n in diffs if len(diffs[n]) >= 6]
    ids = list(range(n_paare))
    rng = random.Random(1)
    treffer_v = treffer_w = 0
    einzeln = {n: 0 for n in namen}
    for _ in range(A.sim):
        s = [rng.choice((-1, 1)) for _ in ids]
        min_v = min_w = 1.0
        for n in namen:
            d = [v * s[i] for i, v in enumerate(diffs[n])]
            pv, pw = p_werte(d)
            min_v = min(min_v, pv)
            if pw is not None:
                min_w = min(min_w, pw)
            einzeln[n] += pv < 0.05 or (pw is not None and pw < 0.05)
        treffer_v += min_v < 0.05
        treffer_w += min_w < 0.05
    fwer = {"kennzahlen": len(namen), "vorzeichen": treffer_v / A.sim, "wilcoxon": treffer_w / A.sim,
            "je_kennzahl_vorz_oder_wilc": {n: round(c / A.sim, 3) for n, c in einzeln.items()}}
    beob = sum(1 for n in namen if min(p for p in p_werte(diffs[n]) if p is not None) < 0.05)
    bericht["fwer"] = fwer
    bericht["beobachtet_p_unter_005"] = beob
    print(f"\n  Kennzahlen mit p < 0,05 in diesem A/A: {beob} von {len(namen)}")
    print(f"  Familienweise Falsch-Positiv-Rate unter H0 ({A.sim} Umkehrungen): "
          f"Vorzeichen {fwer['vorzeichen']:.3f}, Wilcoxon {fwer['wilcoxon']:.3f}")

    bericht["n_vorzeichen"] = {str(q): math.ceil(n_vorzeichen(q)) for q in (0.55, 0.60, 0.65, 0.70, 0.75)}
    print("\n  Nötige Paare, Vorzeichentest (α 0,05, Macht 0,8): "
          + ", ".join(f"q={q} → {n}" for q, n in bericht["n_vorzeichen"].items()))
    if eff:
        print("\n  Nötige Paare, gepaarter Mittelwert mit SD aus diesem A/A:")
        for name, q in bericht["kennzahlen"].items():
            if "n_fuer_effekt" in q:
                print(f"    {name:<20} Effekt {q['effekt_mittel']:>11.4g} (Anteil besser {q['effekt_q']:.2f}) "
                      f"→ {math.ceil(q['n_fuer_effekt'])}, halber Effekt → {math.ceil(q['n_fuer_halben_effekt'])}")
    if A.json:
        with open(A.json, "w") as f:
            json.dump(bericht, f, indent=1, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())
