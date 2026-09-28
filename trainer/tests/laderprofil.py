"""Profil eines einzelnen Lader-Prozesses (ohne DataLoader, ohne GPU): wo geht
die Zeit hin (JSON, Featurisierung, zstd, Stapeln)?

    python trainer/tests/laderprofil.py ~/of-mat2-out/s3 --sekunden 20
"""
from __future__ import annotations

import argparse
import cProfile
import os
import pstats
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daten as D  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("ordner")
ap.add_argument("--sekunden", type=float, default=20)
ap.add_argument("--reputation", default=None)
ap.add_argument("--lset", action="store_true")
a = ap.parse_args()

alle = D.finde_partien([a.ordner])
gids = sorted(alle)
random.Random(0).shuffle(gids)
opt = D.LaderOpt(reputation=a.reputation, lset=a.lset)


def lauf():
    n, t0, cur = 0, time.time(), []
    for g in gids:
        z = Counter()
        lz = D.PartieLeser(alle[g][0], g, opt, z)
        while True:
            s = lz.naechstes()
            if s is D.ENDE:
                break
            if s is None:
                continue
            cur.append(s)
            if len(cur) == 128:
                D.sammle(cur)
                n += len(cur)
                cur = []
        lz.schliessen()
        if time.time() - t0 > a.sekunden:
            break
    return n, time.time() - t0


n, dt = lauf()                          # ohne Profiler: echte Rate
print(f"{n} Samples in {dt:.1f}s: {n / dt:.0f} Samples/s in einem Prozess", flush=True)
pr = cProfile.Profile()
pr.enable()
lauf()
pr.disable()
pstats.Stats(pr).sort_stats("tottime").print_stats(14)
