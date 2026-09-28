#!/usr/bin/env python3
"""Test für --awr-koepfe (rl_train): welche Köpfe den AWR-Gradienten bekommen.

  python trainer/tests/awrkoepfetest.py

1. awr_koepfe: atype wird übersprungen, bedingte Köpfe kommen durch, Unsinn bricht ab.
2. Gradient: mit --awr-koepfe atype bekommt der Kopf magnitude keinen Gradienten aus dem AWR,
   mit atype,magnitude schon — und nur auf Zeilen mit gültigem Label.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

import daten as D  # noqa: E402
import rl_train as RT  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


# 1. Auswahl der Köpfe
pruefe("atype fällt raus", RT.awr_koepfe(SimpleNamespace(awr_koepfe="atype")) == [])
pruefe("bedingte Köpfe kommen durch",
       RT.awr_koepfe(SimpleNamespace(awr_koepfe="atype,magnitude,target")) == ["magnitude", "target"])
try:
    RT.awr_koepfe(SimpleNamespace(awr_koepfe="atype,coarse"))
    pruefe("coarse bricht ab", False)
except SystemExit:
    pruefe("coarse bricht ab", True)
try:
    RT.awr_koepfe(SimpleNamespace(awr_koepfe="quatsch"))
    pruefe("unbekannter Kopf bricht ab", False)
except SystemExit:
    pruefe("unbekannter Kopf bricht ab", True)


# 2. Gradient nur mit dem Kopf in der Liste
def lauf(koepfe):
    torch.manual_seed(0)
    n, na, nm = 4, 5, 7
    out = {"atype": torch.randn(n, na, requires_grad=True),
           "magnitude": torch.randn(n, nm, requires_grad=True)}
    lab = {"atype": torch.tensor([1, 2, 0, 3]),
           "magnitude": torch.tensor([2, D.IGNORE, 1, 4])}
    a = lab["atype"]
    act = a > 0
    w = torch.tensor([1.0, 2.0, 0.5, 1.5])
    lq = F.log_softmax(out["atype"][:, 1:], 1)
    logq = lq.gather(1, (a - 1).clamp(min=0)[:, None]).squeeze(1)
    for h in RT.awr_koepfe(SimpleNamespace(awr_koepfe=koepfe)):
        gueltig = lab[h] != D.IGNORE
        lp = F.log_softmax(out[h].float(), 1)
        lph = lp.gather(1, lab[h].clamp(min=0)[:, None]).squeeze(1)
        logq = logq + torch.where(gueltig, lph, torch.zeros_like(lph))
    verlust = -((w * logq * act.float()).sum() / act.float().sum())
    verlust.backward()
    return out["magnitude"].grad


g_ohne, g_mit = lauf("atype"), lauf("atype,magnitude")
# Fliesst nichts, lässt torch grad auf None — das zählt hier als "kein Gradient".
pruefe("ohne magnitude: kein Gradient", g_ohne is None or float(g_ohne.abs().sum()) == 0.0)
pruefe("mit magnitude: Gradient da", float(g_mit.abs().sum()) > 0.0)
pruefe("Zeile ohne Aktionstyp bleibt aussen vor", float(g_mit[2].abs().sum()) == 0.0)
pruefe("Zeile mit IGNORE-Label bleibt aussen vor", float(g_mit[1].abs().sum()) == 0.0)
pruefe("Zeilen mit Label bekommen Gradient",
       float(g_mit[0].abs().sum()) > 0 and float(g_mit[3].abs().sum()) > 0)

print(f"\n{'ALLES OK' if not FEHLER else 'FEHLER: ' + ', '.join(FEHLER)}")
sys.exit(1 if FEHLER else 0)
