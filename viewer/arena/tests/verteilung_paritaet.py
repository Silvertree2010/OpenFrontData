#!/usr/bin/env python3
"""Parität der Gebietsverteilung: spielweise.ts (Arena) gegen verteilung.py (Menschen-Referenz).

  python arena/tests/verteilung_paritaet.py --client <client mit arena/>

Zufallsfälle (Kreise, verstreute Punkte, Schlangen am Kartenrand, leer, eine Kachel) auf
Karten verschiedener Form. Ganzzahlen müssen gleich sein, Gleitkomma bis 1e-9 relativ
(Summationsreihenfolge). Dazu Handfälle mit bekanntem Ergebnis, damit die Prüfung auch dann
etwas sagt, wenn beide Seiten denselben Denkfehler hätten.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import verteilung as V  # noqa: E402

FEHLER: list[str] = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


def fall(rng, W, H, art):
    terr = np.where(rng.random(W * H) < 0.65, 0x80, 0).astype(np.uint8)
    terr |= np.where(rng.random(W * H) < 0.25, 0x40, 0).astype(np.uint8)   # Uferbit, auch auf Wasser
    m = np.zeros(W * H, bool)
    yy, xx = np.divmod(np.arange(W * H), W)
    if art == "kreise":
        for _ in range(int(rng.integers(1, 6))):
            cx, cy, r = rng.integers(0, W), rng.integers(0, H), rng.integers(1, max(2, min(W, H) // 3))
            m |= (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    elif art == "punkte":
        m = rng.random(W * H) < 0.03
    elif art == "rand":                          # Streifen links und rechts: darf nicht verbunden sein
        m = (xx == 0) | (xx == W - 1) | ((yy == H // 2) & (xx < W // 3))
    elif art == "eins":
        m[int(rng.integers(0, W * H))] = True
    return terr, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--faelle", type=int, default=60)
    a = ap.parse_args()
    rng = np.random.default_rng(11)
    faelle, py = [], []
    formen = [(37, 23), (64, 40), (200, 100), (17, 91), (250, 125)]
    arten = ["kreise", "punkte", "rand", "eins", "leer"]
    for i in range(a.faelle):
        W, H = formen[i % len(formen)]
        terr, m = fall(rng, W, H, arten[i % len(arten)])
        lz = V.land_zellen(terr, W, H)
        faelle.append({"W": W, "H": H, "terrain": terr.tolist(), "refs": np.flatnonzero(m).tolist()})
        py.append({"landzellen": lz, **V.verteilung(m, W, H, terr, lz)})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(faelle, f)
        pfad = f.name
    p = subprocess.run(["npx", "tsx", "arena/tests/verteilung_ts.ts", pfad], cwd=a.client,
                       capture_output=True, text=True)
    os.unlink(pfad)
    if p.returncode != 0:
        print(p.stderr[-2000:])
        pruefe("TS läuft", False)
        return 1
    ts = json.loads(p.stdout.strip().splitlines()[-1])
    pruefe("gleich viele Fälle", len(ts) == len(py), f"{len(ts)} / {len(py)}")
    ganz = ["landzellen", "komponenten", "raster"]
    fliess = ["groesste", "raster_anteil", "streuung", "streuung_rel", "kueste"]
    abw_g, abw_f, wo = 0, 0.0, None
    for i, (t, q) in enumerate(zip(ts, py)):
        abw_g += sum(int(t[k] != q[k]) for k in ganz)
        for k in fliess:
            d = abs(t[k] - q[k]) / abs(q[k]) if q[k] else abs(t[k])
            if d > abw_f:
                abw_f, wo = d, (i, k, t[k], q[k], faelle[i]["W"], faelle[i]["H"], len(faelle[i]["refs"]))
    pruefe("Ganzzahlen gleich (Landzellen, Komponenten, Raster)", abw_g == 0, f"({abw_g} Abweichungen)")
    pruefe("Gleitkomma bis 1e-9 relativ", abw_f < 1e-9,
           f"(max. {abw_f:.2e}" + (f", Fall {wo[0]} Feld {wo[1]}: TS {wo[2]} / Py {wo[3]}, {wo[4]}x{wo[5]}, "
                                   f"{wo[6]} Kacheln" if wo else "") + ")")

    # Handfälle mit bekanntem Ergebnis
    W, H = 32, 32
    terr = np.full(W * H, 0x80, np.uint8)
    m = np.zeros(W * H, bool)
    m[0] = m[W - 1] = True                         # zwei Ecken derselben Zeile: nicht verbunden
    v = V.verteilung(m, W, H, terr, V.land_zellen(terr, W, H))
    pruefe("Randkacheln ohne Umlauf: 2 Komponenten", v["komponenten"] == 2)
    pruefe("Raster: zwei Ecken = 2 Felder von 256", v["raster"] == 2 and abs(v["raster_anteil"] - 2 / 256) < 1e-12)
    yy, xx = np.divmod(np.arange(200 * 200), 200)
    scheibe = (xx - 100) ** 2 + (yy - 100) ** 2 <= 60 * 60
    v = V.verteilung(scheibe, 200, 200, None, 256)
    pruefe("Kreisscheibe: streuung_rel ≈ 1", abs(v["streuung_rel"] - 1) < 0.01, f"({v['streuung_rel']:.4f})")
    zwei = ((xx - 20) ** 2 + (yy - 20) ** 2 <= 100) | ((xx - 180) ** 2 + (yy - 180) ** 2 <= 100)
    v2 = V.verteilung(zwei, 200, 200, None, 256)
    pruefe("zwei ferne Stücke streuen mehr als eine Scheibe", v2["streuung_rel"] > 5 and v2["komponenten"] == 2,
           f"({v2['streuung_rel']:.2f})")
    print("\n" + ("ALLES OK" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
    return 1 if FEHLER else 0


if __name__ == "__main__":
    sys.exit(main())
