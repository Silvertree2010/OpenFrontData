"""Durchsatz des Laders allein (ohne GPU): Samples/s aus dem DataLoader.

    python trainer/tests/laderbench.py ~/of-mat2-out --workers 4 --sekunden 60
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from torch.utils.data import DataLoader  # noqa: E402

import daten as D  # noqa: E402
import reader as R  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("daten", nargs="+")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--batch", type=int, default=128)
ap.add_argument("--puffer", type=int, default=32768)
ap.add_argument("--offen", type=int, default=6)
ap.add_argument("--sekunden", type=float, default=60)
ap.add_argument("--reputation", default=None)
a = ap.parse_args()

alle = D.finde_partien(a.daten)
gids = sorted(g for g in alle if not R.is_val(g))
random.Random(0).shuffle(gids)
ds = D.Strom([(g, alle[g][0]) for g in gids], a.batch, max(1, a.puffer // max(1, a.workers)),
             a.offen, 0, D.LaderOpt(reputation=a.reputation))
dl = DataLoader(ds, batch_size=None, num_workers=a.workers, pin_memory=False, prefetch_factor=4)
t0 = time.time()
erster = None
n = 0
for b in dl:
    if b.get("leer"):
        continue
    if erster is None:
        erster = time.time()
        print(f"erster Batch nach {erster - t0:.1f}s", flush=True)
        continue
    n += int(b["lab"].shape[0])
    if time.time() - erster > a.sekunden:
        break
dt = time.time() - erster
print(f"{n} Samples in {dt:.1f}s nach dem Anlauf: {n / dt:.0f} Samples/s mit {a.workers} Workern", flush=True)
