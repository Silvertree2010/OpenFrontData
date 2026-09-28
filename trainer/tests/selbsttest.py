"""Selbsttest ohne Daten und ohne GPU: Verlust, Maske, weiches Ziel, Flächenanteil.

    python trainer/tests/selbsttest.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

import daten as D  # noqa: E402
import tabellen as T  # noqa: E402
import verlust as V  # noqa: E402
import bewertung as B  # noqa: E402
import actions as AC  # noqa: E402

torch.manual_seed(0)
NZ = 16200
fehler = []


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}")
    if not bed:
        fehler.append(name)


def batch(Bn, atype, g=None, coarse=None, legal=None, bit=None):
    lab = {h: torch.full((Bn,), D.IGNORE, dtype=torch.long) for h in D.HEADS}
    lab["atype"] = atype
    if coarse is not None:
        lab["coarse"] = coarse
    return {"labels": lab, "w": torch.ones(Bn), "wt": torch.ones(Bn), "win": torch.zeros(Bn),
            "g": g if g is not None else torch.full((Bn,), -1),
            "bit": bit if bit is not None else torch.zeros(Bn, dtype=torch.long),
            "legal": legal if legal is not None else torch.zeros(Bn, NZ, dtype=torch.uint8),
            "xy": torch.full((Bn, 2), 50.0), "wh": torch.tensor([[1800.0, 900.0]]).repeat(Bn, 1),
            "owner": torch.zeros(Bn, NZ, dtype=torch.int16), "dst": torch.zeros(Bn, dtype=torch.long)}


def ausgabe(Bn, grad=False):
    out = {h: torch.randn(Bn, k, requires_grad=grad) for h, k in AC.HEAD_SIZES.items()}
    out["value"] = torch.randn(Bn, requires_grad=grad)
    return out


o0 = V.VerlustOpt(adv_beta=0.0)

# 1) faktor = flach, wenn alle Zeilen Aktionen bzw. alle Nichtstun sind (gleiche Gewichte)
for name, at in (("nur Aktionen", torch.randint(1, 21, (64,))), ("nur Nichtstun", torch.zeros(64, dtype=torch.long))):
    out, b = ausgabe(64), batch(64, at)
    _, lf, _ = V.berechne(out, b, o0)
    _, ll, _ = V.berechne(out, b, V.VerlustOpt(adv_beta=0.0, atype="flach"))
    pruefe(f"faktor == flach ({name})", abs(float(lf["atype"]) - float(ll["atype"])) < 1e-4,
           f"{float(lf['atype']):.5f} / {float(ll['atype']):.5f}")

# 2) Zerlegung je Sample: -log p(a) = -log P(handeln) - log p(a|handeln)
lg = torch.randn(8, 21)
a = torch.randint(1, 21, (8,))
lp = F.log_softmax(lg, 1).gather(1, a[:, None]).squeeze(1)
zer = (torch.logsumexp(lg[:, 1:], 1) - torch.logsumexp(lg, 1)) + F.log_softmax(lg[:, 1:], 1).gather(1, (a - 1)[:, None]).squeeze(1)
pruefe("log p(a) zerfällt exakt", torch.allclose(lp, zer, atol=1e-5))

# 3) Maske: richtiges Bit je Zeile
legal = torch.zeros(3, NZ, dtype=torch.uint8)
legal[0, 5] = 0b00000010; legal[1, 5] = 0b10000000; legal[2, 5] = 0b00000001
m = V.zellmaske({"legal": legal, "bit": torch.tensor([1, 7, 1])})
pruefe("zellmaske Bit", m[0, 5].item() and m[1, 5].item() and not m[2, 5].item() and m.sum().item() == 2)

# 4) weiches Ziel: Summe 1, 0 ausserhalb der Maske, Mischung 0,5 am Label
mk = torch.zeros(2, NZ, dtype=torch.bool); mk[:, 100:400] = True
xy = torch.tensor([[150.0, 30.0], [150.0, 30.0]]); wh = torch.tensor([[1800.0, 900.0]] * 2)
c = torch.tensor([3 * 180 + 15, 3 * 180 + 15])        # Kachel (150,30) liegt in Zeile 3, Spalte 15
mk[:, c[0]] = True
z = V.weiches_ziel(mk, xy, wh, torch.tensor([7.5, 7.5]), c, 0.5)
pruefe("weiches Ziel Summe 1", torch.allclose(z.sum(1), torch.ones(2), atol=1e-5))
pruefe("weiches Ziel 0 ausserhalb", float(z[~mk].abs().max()) == 0.0)
pruefe("weiches Ziel am Label >= 0,5", bool((z.gather(1, c[:, None]) >= 0.5).all()))
z0 = V.weiches_ziel(mk, xy, wh, torch.tensor([7.5, 7.5]), c, 0.0)
pruefe("Anteil 0 = onehot", float(z0.max()) == 1.0 and float(z0.sum()) == 2.0)

# 5) Kachel-Verlust: nicht-räumliche Zeilen (legal 0) machen keine NaN, Label ausserhalb
#    der Maske fällt raus, Gradient nur auf legale Logits
Bn = 4
legal = torch.zeros(Bn, NZ, dtype=torch.uint8)
legal[0, 1000:1100] = 2          # Bit 1
legal[1, 2000:2010] = 2
lab_c = torch.tensor([1050, 5000, D.IGNORE, D.IGNORE])   # Zeile 1: Label ausserhalb der Maske
b = batch(Bn, torch.tensor([2, 2, 1, 0]), g=torch.tensor([0, 0, -1, -1]), coarse=lab_c,
          legal=legal, bit=torch.tensor([1, 1, 0, 0]))
b["xy"] = torch.tensor([[500.0, 55.0]] * Bn)
out = ausgabe(Bn, grad=True)
tot, logs, info = V.berechne(out, b, o0)
pruefe("Gesamtverlust endlich", math.isfinite(float(tot)))
tot.backward()
gc = out["coarse"].grad
pruefe("Gradient endlich", bool(torch.isfinite(gc).all()))
pruefe("Gradient nur auf legalen Logits", float(gc[0, :1000].abs().max()) == 0.0 and float(gc[0, 1000:1100].abs().sum()) > 0)
pruefe("Label ausserhalb der Maske: kein Verlust", float(gc[1].abs().max()) == 0.0 and not bool(info["ok"][1]))
pruefe("Zähler Maskenverletzung", int((info["gueltig"] & ~info["in_maske"]).sum()) == 1)

# 6) Flächenanteil gegen Brute Force und gegen π r² (1 Kachel je Zelle)
xy = torch.tensor([[90.3, 45.7]]); wh = torch.tensor([[180.0, 90.0]])
for r in (3.0, 10.0):
    fr = B.flaechenanteil(xy, wh, torch.tensor([r]), k=4).view(90, 180)
    # Brute Force für ein paar Zellen
    ok = True
    for (i, j) in ((45, 90), (45, 93), (40, 90), (49, 99)):
        pts = [((j + (kx + .5) / 4), (i + (ky + .5) / 4)) for kx in range(4) for ky in range(4)]
        bf = sum((px - 90.3) ** 2 + (py - 45.7) ** 2 <= r * r for px, py in pts) / 16
        ok &= abs(bf - float(fr[i, j])) < 1e-6
    pruefe(f"Flächenanteil Brute Force r={r}", ok)
    pruefe(f"Σ Anteil ≈ π r² r={r}", abs(float(fr.sum()) - math.pi * r * r) < 0.05 * math.pi * r * r,
           f"{float(fr.sum()):.1f} gegen {math.pi * r * r:.1f}")

# 7) tile_encode = Zellindex von cells.ts auf schiefen Kartengrössen
ok = True
for W, H in ((1360, 1360), (6000, 2400), (256, 777), (1800, 2120)):
    for t in torch.randint(0, W * H, (2000,)).tolist():
        x, y = t % W, t // W
        ok &= AC.tile_encode(t, W, H)[0] == (y * 90 // H) * 180 + (x * 180 // W)
pruefe("tile_encode == cells.ts-Zelle", ok)

# 8) L-set: leer = harte NLL; S = alle legalen Zellen → 0; Zellen ausserhalb der Maske zählen nicht
legal = torch.zeros(1, NZ, dtype=torch.uint8)
legal[0, 100:117] = 2                                     # genau 17 legale Zellen, Bit 1
b = batch(1, torch.tensor([2]), g=torch.tensor([0]), coarse=torch.tensor([100]), legal=legal,
          bit=torch.tensor([1]))
out = ausgabe(1)
ol = V.VerlustOpt(adv_beta=0.0, raum="lset")
b["lset"] = torch.full((1, 16), -1)
_, lg0, _ = V.berechne(out, b, ol)
pruefe("L-set leer = NLL", abs(float(lg0["coarse"]) - float(lg0["coarse_nll"])) < 1e-5)
b["lset"] = torch.arange(101, 117)[None]
_, lg1, _ = V.berechne(out, b, ol)
pruefe("L-set = alle legalen Zellen → 0", abs(float(lg1["coarse"])) < 1e-5, f"{float(lg1['coarse']):.2e}")
b["lset"] = torch.arange(5000, 5016)[None]
_, lg2, _ = V.berechne(out, b, ol)
pruefe("L-set ausserhalb der Maske zählt nicht", abs(float(lg2["coarse"]) - float(lg0["coarse"])) < 1e-5)

# 9) LR-Plan
import types  # noqa: E402
import train as TR  # noqa: E402
a = types.SimpleNamespace(warmup=10, lr_plan="cosinus", lr=1e-3, lr_min=1e-5)
f = [TR.lr_faktor(s, a, 110) for s in range(0, 111)]
pruefe("Warmup linear", abs(f[0] - 0.1) < 1e-9 and abs(f[9] - 1.0) < 1e-9)
pruefe("Cosinus endet bei lr_min", abs(f[110] * 1e-3 - 1e-5) < 1e-9 and all(x >= y for x, y in zip(f[10:], f[11:])))
a.lr_plan = "konstant"
pruefe("konstant nach Warmup", TR.lr_faktor(50, a, 110) == 1.0)

print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
