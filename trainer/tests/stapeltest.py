#!/usr/bin/env python3
"""Test für inf_gpu.StapelEntscheider: GPU im Stapel gegen CPU einzeln, dieselben Anfragen.

  python trainer/tests/stapeltest.py --ckpt CKPT --anfragen '/tmp/req_*.json' [--geraet cuda]

Anfragen: echte POST-Körper der Arena (Mitschnitt mit einem Zwischenserver).
1. Kopf-Ausgaben: P(handeln) und Wertkopf im Stapel gegen einzeln (fp32 beide): Abweichung klein.
2. Entscheidung: mit gekoppeltem Ziehen (wahl_schluessel) gleicher Aktionstyp und gleicher Intent-Typ.
3. Reihenfolge im Stapel ändert nichts (gemischt gegen sortiert).
4. Eine kaputte Anfrage im Stapel bricht die anderen nicht.
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

import daten as D  # noqa: E402
import inf_gpu as G  # noqa: E402
import netze as N  # noqa: E402
import spielen as SP  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", required=True)
ap.add_argument("--anfragen", required=True)
ap.add_argument("--geraet", default="cuda")
ap.add_argument("--reputation", default=None)
A = ap.parse_args()

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


torch.backends.cudnn.allow_tf32 = False
torch.backends.cuda.matmul.allow_tf32 = False
z = torch.load(A.ckpt, map_location="cpu", weights_only=True)
rep = D._rep_tabelle(A.reputation) if A.reputation else {}
S = 0.017338573932192958


def baue(dev, klasse):
    ad = N.baue_netz("d1")
    ad.modul.load_state_dict(z["model"])
    ad.modul.to(dev)
    e = klasse(ad, dev, rep, wahl=SP.Wahl(ziehen=True, top_k=4, saat=1))
    e.autocast_an = False
    return e


reqs = [json.load(open(p)) for p in sorted(glob.glob(A.anfragen))]
for i, o in enumerate(reqs):
    o["wahl_schluessel"] = f"test|ki{i:06d}|{640 + 32 * i}"
print(f"{len(reqs)} Anfragen")
cpu = baue("cpu", SP.Entscheider)
gpu = baue(A.geraet, G.StapelEntscheider)

ref = [cpu.entscheide(copy.deepcopy(o), S) for o in reqs]
stap = gpu.entscheide_stapel([copy.deepcopy(o) for o in reqs], S)
pruefe("alle Anfragen beantwortet", all(isinstance(r, dict) for r in stap),
       str([type(r).__name__ for r in stap if not isinstance(r, dict)]))

dp = max(abs(a["p_handeln"] - b["p_handeln"]) for a, b in zip(ref, stap))
dv = max(abs(a["value"] - b["value"]) for a, b in zip(ref, stap))
pruefe("P(handeln) im Stapel = einzeln", dp < 2e-4, f"max|Δ|={dp:.1e}")
pruefe("Wertkopf im Stapel = einzeln", dv < 2e-3, f"max|Δ|={dv:.1e}")
gl = sum(1 for a, b in zip(ref, stap) if a["atype"] == b["atype"])
pruefe("gleicher Aktionstyp (gekoppeltes Ziehen)", gl == len(reqs), f"{gl}/{len(reqs)}")
gl2 = sum(1 for a, b in zip(ref, stap) if (a.get("intent") or {}).get("type") == (b.get("intent") or {}).get("type"))
pruefe("gleicher Intent-Typ", gl2 == len(reqs), f"{gl2}/{len(reqs)}")

# 3. Reihenfolge
perm = list(range(len(reqs)))
random.Random(3).shuffle(perm)
st2 = gpu.entscheide_stapel([copy.deepcopy(reqs[i]) for i in perm], S)
inv = {p: k for k, p in enumerate(perm)}
gl3 = sum(1 for i in range(len(reqs)) if st2[inv[i]]["atype"] == stap[i]["atype"]
          and abs(st2[inv[i]]["p_handeln"] - stap[i]["p_handeln"]) < 1e-5)
pruefe("Reihenfolge im Stapel egal", gl3 == len(reqs), f"{gl3}/{len(reqs)}")

# 4. Kaputte Anfrage
kaputt = copy.deepcopy(reqs[1])
kaputt.pop("zusatz_b64", None)
mix = [copy.deepcopy(reqs[0]), kaputt, copy.deepcopy(reqs[2])]
st3 = gpu.entscheide_stapel(mix, S)
pruefe("kaputte Anfrage wird zur Exception", isinstance(st3[1], Exception))
pruefe("die anderen kommen durch", isinstance(st3[0], dict) and isinstance(st3[2], dict)
       and st3[0]["atype"] == stap[0]["atype"] and st3[2]["atype"] == stap[2]["atype"])

print(f"\n{'ALLES OK' if not FEHLER else 'FEHLER: ' + ', '.join(FEHLER)}")
sys.exit(1 if FEHLER else 0)
