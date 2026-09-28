#!/usr/bin/env python3
"""Ende-zu-Ende-Parität der Zusatzfelder bis an den Netzeingang.

zusatz/src/paritaet.ts (--aus) schreibt je Stichprobe die Nutzlast, die Arena bzw.
Erweiterung an den Server schicken würden (zusatz_b64, zusatz_sig, ctx), samt Partie und
Zeile. Hier wird daraus genau das gebaut, was der Server ins Netz gibt
(spielen.zusatz_aus_anfrage), und bitweise mit dem verglichen, was der Lader im Training
aus der Offline-Datei macht (daten.PartieLeser mit --zusatz: zus_o, zus_g, zus_e, zus_a).
Dazu der Fingerabdruck der Feldlisten (TS gegen Python).

  python trainer/tests/zusatz_paritaet.py nutzlast.jsonl --zusatz ~/zusatz/alle
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))

import numpy as np  # noqa: E402

import daten as D  # noqa: E402
import spielen as SP  # noqa: E402


def gleich(a: np.ndarray, b: np.ndarray) -> bool:
    a, b = np.ascontiguousarray(a, np.float32), np.ascontiguousarray(b, np.float32)
    return a.shape == b.shape and np.array_equal(a.view(np.uint32), b.view(np.uint32))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nutzlast", nargs="+")
    ap.add_argument("--zusatz", required=True)
    a = ap.parse_args(argv)
    zeilen = [json.loads(l) for p in a.nutzlast for l in open(p) if l.strip()]
    je_partie: dict[tuple, list] = {}
    for z in zeilen:
        je_partie.setdefault((z["pool"], z["gid"]), []).append(z)
    z_abw: Counter = Counter()
    n = 0
    sigs = {z["zusatz_sig"] for z in zeilen}
    for (pool, gid), liste in sorted(je_partie.items()):
        leser = D.PartieLeser(pool, gid, D.LaderOpt(zusatz=a.zusatz), Counter())
        for z in liste:
            i = z["zeile"]
            zo, zg, ze, za = SP.zusatz_aus_anfrage(z)
            n += 1
            for name, ist, soll in (("opp", zo, leser.zus_o[i]), ("global", zg, leser.zus_g[i]),
                                    ("einheit", ze, leser.zus_e[i]), ("angriff", za, leser.zus_a[i])):
                if not gleich(ist, soll):
                    z_abw[name] += 1
                    if sum(z_abw.values()) <= 5:
                        d = np.argwhere(np.asarray(ist, np.float32) != np.asarray(soll, np.float32))[:3]
                        print(f"  ABW {gid} Zeile {i} {name}: {d.tolist()}", flush=True)
        leser.schliessen()
    erg = {"stichproben": n, "partien": len(je_partie), "abweichungen": dict(z_abw),
           "sig_ts": sorted(sigs), "sig_py": SP.ZUSATZ_SIG, "sig_gleich": sigs == {SP.ZUSATZ_SIG}}
    print(json.dumps(erg, ensure_ascii=False))
    return 0 if n and not z_abw and erg["sig_gleich"] else 1


if __name__ == "__main__":
    sys.exit(main())
