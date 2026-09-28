#!/usr/bin/env python3
"""Ziehen an die Saat gebunden (spielen.gleichverteilt / ziehe_invers, inf_d0 --wahl-saat).

  python trainer/tests/wahltest.py        (torch, kein Checkpoint, keine Daten)

1. Gleicher Schlüssel → gleiche Wahl und gleiches log μ, bitgleich, auch nach beliebigen
   anderen Anfragen dazwischen und in anderer Reihenfolge (ein Server bedient viele Partien).
2. Die Verteilung stimmt: über viele Schlüssel treffen die Häufigkeiten softmax(Top-k) (χ²).
3. Kopplung im Paar: gleiche Logits → immer gleiche Wahl; leicht verschobene Logits mit gleicher
   Rangfolge → fast immer gleich; unabhängiges Ziehen wäre deutlich seltener gleich.
4. Andere Saat → andere Wahl bei einem Teil der Schlüssel (die Saat wirkt).
5. Ohne Schlüssel bleibt alles beim Alten: torch.multinomial mit dem Server-Generator.
6. log μ ist log softmax über die Top-k an der gewählten Stelle; ohne Top-k über alle Klassen.
"""
from __future__ import annotations

import math
import os
import random
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

import spielen as SP  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


def entscheider(saat=1, top_k=4):
    return SP.Entscheider(types.SimpleNamespace(name="d0"), "cpu", {},
                          wahl=SP.Wahl(ziehen=True, temp=1.0, top_k=top_k, saat=saat))


def wahl(e, logits, schluessel, kopf="atype"):
    e._schluessel = schluessel
    try:
        return e._waehle_lp(logits, kopf, 1)
    finally:
        e._schluessel = None


g = torch.Generator().manual_seed(5)
L = [torch.randn(15, generator=g) * 2 for _ in range(40)]
schl = [f"partie-{i:04d}|ki000001|{32 * i}" for i in range(40)]

# 1. Bitgleich und reihenfolgeunabhängig
e1, e2 = entscheider(), entscheider()
a = [wahl(e1, l, k) for l, k in zip(L, schl)]
idx = list(range(40))
random.Random(3).shuffle(idx)
b = {}
for i in idx:
    wahl(e2, torch.randn(15), "stoerung|x|0")          # fremde Anfrage dazwischen
    b[i] = wahl(e2, L[i], schl[i])
pruefe("gleicher Schlüssel → gleiche Wahl und log μ (bitgleich)", all(a[i] == b[i] for i in range(40)))

# 2. Verteilung
e = entscheider(top_k=4)
lg = torch.tensor([2.0, 1.2, 0.3, -0.5, -3.0, 0.9])
w, ix = lg.topk(4)
p = torch.softmax(w.double(), 0).tolist()
n = 20000
z = [0] * 4
for i in range(n):
    j, _ = wahl(e, lg, f"s|{i}")
    z[ix.tolist().index(j - 1)] += 1
chi2 = sum((z[k] - n * p[k]) ** 2 / (n * p[k]) for k in range(4))
pruefe("Häufigkeiten = softmax(Top-4), χ² mit 3 FG < 16,3 (p > 0,001)", chi2 < 16.27,
       f"(χ²={chi2:.2f}, {[round(x / n, 3) for x in z]} vs {[round(x, 3) for x in p]})")

# 3. Kopplung
gleich_id = sum(wahl(e, l, k)[0] == wahl(e, l.clone(), k)[0] for l, k in zip(L, schl))
pruefe("gleiche Logits → immer gleiche Wahl", gleich_id == 40)
m = 4000
gl = unab = 0
rng = random.Random(9)
for i in range(m):
    base = torch.tensor([1.5, 1.0, 0.4, 0.0, -1.0])
    stoer = base + torch.tensor([rng.uniform(-0.1, 0.1) for _ in range(5)])
    gl += wahl(e, base, f"k|{i}")[0] == wahl(e, stoer, f"k|{i}")[0]
    unab += wahl(e, base, f"k|{i}")[0] == wahl(e, stoer, f"k2|{i}")[0]
pruefe("leicht andere Logits: gekoppelt deutlich öfter gleich als unabhängig", gl / m > 0.9 and gl > unab + 0.2 * m,
       f"(gekoppelt {gl / m:.1%}, unabhängig {unab / m:.1%})")

# 4. Saat wirkt
e_b = entscheider(saat=2)
versch = sum(wahl(e, l, k)[0] != wahl(e_b, l, k)[0] for l, k in zip(L, schl))
pruefe("andere Saat → andere Wahl bei manchen Schlüsseln", versch > 5, f"({versch} von 40)")

# 5. Ohne Schlüssel: Generator wie bisher
e_g, e_h = entscheider(saat=7), entscheider(saat=7)
f = [e_g._waehle_lp(l, "atype", 1) for l in L]
torch_ref = torch.Generator(device="cpu").manual_seed(7)
ref = []
for l in L:
    w_, i_ = l.float().topk(4)
    j = int(torch.multinomial(torch.softmax(w_, 0), 1, generator=torch_ref))
    ref.append((int(i_[j]) + 1, float(torch.log_softmax(w_, 0)[j])))
pruefe("ohne Schlüssel: torch.multinomial mit Server-Generator (unverändert)", f == ref)

# 6. log μ
ok = True
for l, k in zip(L, schl):
    j, lp = wahl(e, l, k)
    w_, i_ = l.float().topk(4)
    pos = i_.tolist().index(j - 1)
    ok &= abs(lp - float(torch.log_softmax(w_, 0)[pos])) < 1e-6
e0 = entscheider(top_k=0)
for l, k in zip(L, schl):
    j, lp = wahl(e0, l, k)
    ok &= abs(lp - float(torch.log_softmax(l.float(), 0)[j - 1])) < 1e-6
pruefe("log μ = log softmax an der Wahl (Top-4 und alle Klassen)", ok)
pruefe("ziehe_invers am Rand", SP.ziehe_invers([0.5, 0.5], 0.999999999) == 1 and SP.ziehe_invers([0.5, 0.5], 0.0) == 0
       and SP.ziehe_invers([0.3, 0.3, 0.3999999], 1 - 1e-12) == 2)
u = [SP.gleichverteilt(1, f"x|{i}", "atype") for i in range(20000)]
pruefe("u gleichverteilt in [0,1)", min(u) >= 0 and max(u) < 1 and abs(sum(u) / len(u) - 0.5) < 0.01)
print("\n" + ("ALLES OK" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
