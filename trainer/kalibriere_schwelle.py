#!/usr/bin/env python3
"""Kalibriert die Schwelle für P(handeln) der D0-Spielregel auf Val-Partien.

Ziel: Menschen handeln im Median alle 73 Ticks (viewer/README.md, p25 55, p75 100).
Der Viewer fragt alle k Ticks (DECIDE_EVERY). Handelt das Modell, wenn P(handeln) > s,
ist sein Handlungsabstand je Spieler k / f(s), mit f(s) = w_tick-gewichteter Anteil der
Spieler-Ticks, an denen P(handeln) > s (w_tick macht die Nichtstun-Stichprobe erwartungs-
treu, DESIGN §4). Gesucht ist je k die Schwelle s(k), bei der der Median über die Spieler
73 Ticks ist. Die Zustände sind die der echten Menschen in den Val-Partien; das ist die
bestmögliche Näherung ohne Viewer-Lauf.

Kontrolle: derselbe Median für die Menschen selbst (Σw_tick / Σw_tick der Aktionen).

  python trainer/kalibriere_schwelle.py --ckpt checkpoints/bc2.pt --daten ~/of-mat2-out \\
      --reputation …/reputation.json --aus kalibrierung.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import numpy as np  # noqa: E402
import torch  # noqa: E402

import daten as D  # noqa: E402
import featurize as FZ  # noqa: E402
import netze as N  # noqa: E402
import reader as R  # noqa: E402
import zusatz_felder as ZF  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--daten", nargs="+", default=["~/of-mat2-out"])
    ap.add_argument("--reputation", default=None)
    ap.add_argument("--partien", type=int, default=20, help="Val-Partien (sortiert nach gid)")
    ap.add_argument("--act-anteil", type=float, default=0.15,
                    help="Anteil der Aktions-Samples, die gerechnet werden (Gewicht wird korrigiert)")
    ap.add_argument("--min-ticks", type=float, default=3000, help="Spieler mit weniger Spieler-Ticks weglassen")
    ap.add_argument("--ziel", type=float, default=73.0)
    ap.add_argument("--k", default="1,4,8,16,32,64")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--aus", default="kalibrierung.json")
    ap.add_argument("--zusatz", default=None,
                    help="Ordner mit <gid>.zusatz.zst; Pflicht für D1 (wie train.py --zusatz)")
    a = ap.parse_args(argv)
    torch.set_num_threads(a.threads)

    z = torch.load(a.ckpt, map_location="cpu", weights_only=True)
    netz = z.get("netz", "d0")
    if netz == "d1" and not a.zusatz:
        sys.exit("[abbruch] D1-Checkpoint braucht --zusatz")
    netz_opt = ({"opp_dim": FZ.OPP_DIM + ZF.DIM_OPP, "own_dim": FZ.OWN_DIM + ZF.DIM_GLOBAL}
                if a.zusatz else {})                   # wie train.py
    ad = N.baue_netz(netz, **netz_opt)
    ad.modul.load_state_dict(z["model"])
    net = ad.modul.train()
    alle = D.finde_partien(a.daten)
    val = sorted(g for g in alle if R.is_val(g))
    if a.zusatz:                                    # nur Val-Partien mit Zusatzdatei (Lader überspringt sonst)
        zd = os.path.expanduser(a.zusatz)
        val = [g for g in val if os.path.exists(os.path.join(zd, f"{g}.zusatz.zst"))]
    val = val[:a.partien]
    lopt = D.LaderOpt(reputation=a.reputation, zusatz=a.zusatz)
    rng = random.Random(0)
    spieler, pw, ww, akt = [], [], [], []
    batch: list[dict] = []
    t0 = time.time()

    @torch.no_grad()
    def rechne():
        b = D.auf_geraet(D.sammle(batch), "cpu")
        out = net.koepfe_aus(b, net.kodiere(b, b["map"]), zeiger=False)
        p = (1.0 - torch.softmax(out["atype"].float(), 1)[:, 0]).tolist()
        for s, pi in zip(batch, p):
            spieler.append(s["spieler"]); pw.append(pi); ww.append(s["wt_k"]); akt.append(s["akt"])
        batch.clear()

    for n, gid in enumerate(val):
        lz = D.PartieLeser(alle[gid][0], gid, lopt, Counter())
        while True:
            s = lz.naechstes()
            if s is D.ENDE:
                break
            if s is None:
                continue
            ist_akt = bool(s["lab"][D.HI["atype"]] != 0)
            if ist_akt and rng.random() > a.act_anteil:
                continue
            s["wt_k"] = s["wt"] / (a.act_anteil if ist_akt else 1.0)
            s["akt"], s["spieler"] = ist_akt, (gid, s["sid"])
            batch.append(s)
            if len(batch) == 64:
                rechne()
        lz.schliessen()
        print(f"[kalibrierung] {n + 1}/{len(val)} Partien, {len(pw)} Samples, {time.time() - t0:.0f}s", flush=True)
    if batch:
        rechne()

    pw, ww, akt = np.array(pw), np.array(ww), np.array(akt)
    je = defaultdict(list)
    for i, sp in enumerate(spieler):
        je[sp].append(i)
    gruppen = [np.array(ix) for ix in je.values() if ww[ix].sum() >= a.min_ticks]
    mensch = np.array([ww[ix].sum() / max(ww[ix][akt[ix]].sum(), 1e-9) for ix in gruppen])

    def abstaende(s, k):
        f = np.array([ww[ix][pw[ix] > s].sum() / ww[ix].sum() for ix in gruppen])
        return np.where(f > 0, k / np.maximum(f, 1e-12), np.inf)

    q = lambda x, p: float(np.percentile(x, p))  # noqa: E731
    ergebnis = {"ziel_median": a.ziel, "ckpt": os.path.abspath(a.ckpt), "gstep": int(z.get("gstep", 0)),
                "partien": len(val), "spieler": len(gruppen), "samples": int(len(pw)),
                "mensch": {"median": q(mensch, 50), "p25": q(mensch, 25), "p75": q(mensch, 75)},
                "p_handeln": {"gewichtetes_mittel": float((pw * ww).sum() / ww.sum()),
                              "median": q(pw, 50), "p90": q(pw, 90), "p99": q(pw, 99)},
                "schwelle": {}, "modell": {}}
    for k in [int(x) for x in a.k.split(",")]:
        lo, hi = 0.0, 1.0                  # Median-Abstand steigt mit der Schwelle
        for _ in range(40):
            mid = (lo + hi) / 2
            if np.median(abstaende(mid, k)) < a.ziel:
                lo = mid
            else:
                hi = mid
        s = (lo + hi) / 2
        d = abstaende(s, k)
        ergebnis["schwelle"][str(k)] = s
        ergebnis["modell"][str(k)] = {"median": q(d, 50), "p25": q(d, 25), "p75": q(d, 75),
                                      "nie_handelnd": float(np.isinf(d).mean())}
    with open(a.aus, "w") as f:
        json.dump(ergebnis, f, indent=1)
    print(json.dumps(ergebnis, indent=1), flush=True)


if __name__ == "__main__":
    main()
