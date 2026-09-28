"""A/B im selben Prozess: Gegner-Featurisierung alt (featurize.featurize_opps mit
_rep-Injektion wie im ersten Lader) gegen daten.gegner_features. Abwechselnd auf
denselben Metazeilen, damit schwankende Last beide gleich trifft.

    python trainer/tests/featurebench.py ~/of-mat2-out/s3 --reputation …/reputation.json
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import daten as D  # noqa: E402
import featurize as FZ  # noqa: E402
import reader as R  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("ordner")
ap.add_argument("--reputation", default=None)
ap.add_argument("--samples", type=int, default=6000)
a = ap.parse_args()
rep = D._rep_tabelle(a.reputation)
alle = D.finde_partien([a.ordner])
metas = []
for g in sorted(alle):
    metas += R.Game(alle[g][0], g).meta()
    if len(metas) >= a.samples:
        break
metas = metas[:a.samples]
kopien = [copy.deepcopy(m.get("opps") or []) for m in metas]


def alt():
    for opps in kopien:
        for o in opps:
            o["_rep"] = rep.get(f"{o.get('user') or '?'}\x1f{o.get('clan') or ''}", 0.5)
        mat, mask = FZ.featurize_opps(opps)
        np.asarray(mat, np.float32), np.asarray(mask, bool)


def neu():
    for m in metas:
        D.gegner_features(m.get("opps") or [], rep)


zeiten = {"alt": [], "neu": []}
for _ in range(3):
    for name, f in (("alt", alt), ("neu", neu)):
        t = time.perf_counter()
        f()
        zeiten[name].append((time.perf_counter() - t) / len(metas) * 1e6)
ma, mn = min(zeiten["alt"]), min(zeiten["neu"])
print(f"{len(metas)} Samples: alt {ma:.0f} µs, neu {mn:.0f} µs je Sample (bestes von 3), Faktor {ma / mn:.1f}")
