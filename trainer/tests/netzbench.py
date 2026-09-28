"""GPU-Durchsatz der Netze wie ~/mat-dev/gpu_bench.py: Zufallseingaben, Vorwärts +
Rückwärts + AdamW, bf16-Autocast. Anders als dort mit dem echten Verlust
(verlust.berechne, Maske, weiches Ziel) und einem Anteil räumlicher Zeilen, der
von Schritt zu Schritt schwankt wie in echten Batches. Kein Lader.

    python trainer/tests/netzbench.py --netze alt,d0 --batches 128,256,512 --kompilieren 0,1 --cl 0,1
    Varianten: --netze "d0[c1=48;c2=96;tiefe=1-1-1]"   (Semikolon zwischen den Werten)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import train as TR  # noqa: E402
import tabellen as T  # noqa: E402
import verlust as V  # noqa: E402
import actions as AC  # noqa: E402
import featurize as F  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--netze", default="alt,d0")
ap.add_argument("--batches", default="128,256,512")
ap.add_argument("--kompilieren", default="0")
ap.add_argument("--cl", default="0")
ap.add_argument("--schritte", type=int, default=30)
ap.add_argument("--raum", type=float, default=0.35, help="Anteil räumlicher Zeilen")
ap.add_argument("--lader", default=None, help="Datenordner: echte Batches statt Zufall (Ende-zu-Ende)")
ap.add_argument("--reputation", default=None)
ap.add_argument("--zusatz", default=None, help="Ordner mit Zusatzdateien (für d1 mit --lader Pflicht)")
a = ap.parse_args()
dev = "cuda"
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True
RAUM_G = [T.GID[g] for g in T.GRUPPEN if T.R_KACHELN[g] is not None]


def batch(B, gen):
    r = lambda *s: torch.rand(*s, device=dev, generator=gen)  # noqa: E731
    ri = lambda lo, hi, s: torch.randint(lo, hi, s, device=dev, generator=gen)  # noqa: E731
    raum = r(B) < a.raum
    lab = {h: torch.full((B,), D.IGNORE, dtype=torch.long, device=dev) for h in D.HEADS}
    lab["atype"] = torch.where(raum, torch.full((B,), 2, device=dev), ri(0, 21, (B,)))
    g = torch.where(raum, torch.tensor(RAUM_G, device=dev)[ri(0, len(RAUM_G), (B,))], torch.full((B,), -1, device=dev))
    c = ri(0, 16200, (B,))
    lab["coarse"] = torch.where(raum, c, lab["coarse"])
    lab["unit_type"] = torch.where(raum, ri(0, 10, (B,)), lab["unit_type"])
    lab["target"] = torch.where(r(B) < 0.5, ri(0, 12, (B,)), lab["target"])
    lab["magnitude"] = torch.where(r(B) < 0.5, ri(0, 10, (B,)), lab["magnitude"])
    bit = torch.tensor([T.BIT[x] for x in T.GRUPPEN], device=dev)[g.clamp(min=0)]
    legal = ri(0, 256, (B, 16200)).to(torch.uint8)
    legal.scatter_(1, c[:, None], 255)
    mask = torch.zeros(B, 24, dtype=torch.bool, device=dev)
    mask[:, :12] = True
    b = {"map": r(B, 18, 90, 180) * 2 - 1, "own": torch.randn(B, F.OWN_DIM, device=dev),
         "opp": torch.randn(B, 24, F.OPP_DIM, device=dev), "opp_mask": mask,
         "config": torch.randn(B, F.CONFIG_DIM, device=dev), "labels": lab,
         "w": torch.ones(B, device=dev), "wt": torch.ones(B, device=dev), "win": (r(B) < 0.5).float(),
         "g": g, "bit": bit, "legal": legal, "owner": ri(0, 30, (B, 16200)).to(torch.int16),
         "own_frac": ri(0, 256, (B, 16200)).to(torch.uint8),
         "xy": r(B, 2) * torch.tensor([1800.0, 900.0], device=dev),
         "wh": torch.tensor([[1800.0, 900.0]], device=dev).repeat(B, 1),
         "dst": ri(0, 30, (B,)), "lset": torch.full((B, 16), -1, device=dev)}
    if D1_AN[0]:                         # Zusatzfelder wie aus dem Lader (normiert, art/ziel als Index)
        import actions as AC
        import zusatz_felder as ZF
        n = ri(0, 60, (B,))              # besetzte Einheitenplätze je Zeile
        belegt = torch.arange(ZF.MAX_EINHEIT, device=dev)[None] < n[:, None]
        e = r(B, ZF.MAX_EINHEIT, ZF.DIM_EINHEIT) * belegt[..., None]
        e[..., 0] = ri(1, 17, (B, ZF.MAX_EINHEIT)).float() * belegt
        na = ri(0, 6, (B,))
        ba = torch.arange(ZF.MAX_ANGRIFF, device=dev)[None] < na[:, None]
        an = r(B, ZF.MAX_ANGRIFF, ZF.DIM_ANGRIFF) * ba[..., None]
        an[..., 1] = ri(0, 26, (B, ZF.MAX_ANGRIFF)).float() * ba
        b["einheiten"], b["angriffe"] = e, an
        b["own"] = torch.randn(B, F.OWN_DIM + ZF.DIM_GLOBAL, device=dev)
        b["opp"] = torch.randn(B, 24, F.OPP_DIM + ZF.DIM_OPP, device=dev)
        mit_ref = (r(B) < 0.05) & (n > 0) & (lab["atype"] != int(AC.A.CANCEL_ATTACK))
        lab["own_ref"] = torch.where(mit_ref, torch.zeros_like(n), lab["own_ref"])
    return b


D1_AN = [False]                          # lauf() setzt das je Netz: d1 braucht Zusatzfelder im Batch


def baue(name):
    """"alt", "d0" oder eine D0-Variante wie d0[c1=48,c2=96,tiefe=1-1-1]."""
    if "[" not in name:
        return N.baue_netz(name)
    import netz_d0
    kw = {}
    for teil in name[name.index("[") + 1:-1].split(";"):
        k, _, v = teil.partition("=")
        kw[k] = tuple(int(x) for x in v.split("-")) if k == "tiefe" else (int(v) if v.isdigit() else v)
    return netz_d0.D0Adapter(**kw)


def lader_batches(B):
    """Echte Batches aus dem Streaming-Lader (4 Worker), für die Messung Ende-zu-Ende."""
    import random
    from torch.utils.data import DataLoader
    import reader as R
    alle = D.finde_partien([a.lader])
    gids = sorted(g for g in alle if not R.is_val(g))
    random.Random(0).shuffle(gids)
    ds = D.Strom([(g, alle[g][0]) for g in gids], B, 32768 // 4, 6, 0,
                 D.LaderOpt(reputation=a.reputation, zusatz=a.zusatz))
    for b in DataLoader(ds, batch_size=None, num_workers=4, pin_memory=True, prefetch_factor=4):
        if not b.get("leer"):
            yield b


def lauf(name, B, komp, cl):
    torch.cuda.empty_cache()
    torch.manual_seed(0)
    ad = baue(name)
    D1_AN[0] = name.startswith("d1")
    net = ad.modul.to(dev)
    ad.speicherformat(cl)
    if komp:
        ad.kompiliere()
    opt = TR.optimierer(net, types.SimpleNamespace(lr=3e-4, wd=0.01), dev)
    st = TR.Schritt(ad, opt, V.VerlustOpt(), 0.0, dev)     # derselbe Schritt wie im Trainer
    warm = (40 if komp else 10) if a.lader else (12 if komp else 5)
    if a.lader:
        quelle = lader_batches(B)
        naechster = lambda i: D.auf_geraet(next(quelle), dev)  # noqa: E731
    else:
        gen = torch.Generator(device=dev).manual_seed(1)
        bs = [batch(B, gen) for _ in range(4)]
        naechster = lambda i: bs[i % 4]  # noqa: E731
    try:
        for i in range(warm):
            st(naechster(i), 3e-4)
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        warten, t = 0.0, time.perf_counter()
        for i in range(a.schritte):
            tw = time.perf_counter()
            b = naechster(i)
            warten += time.perf_counter() - tw
            st(b, 3e-4)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t
        s, n = st.lies()
        print(f"{name:4s} B={B:5d} kompiliert={komp} cl={cl}{' lader' if a.lader else ''}: "
              f"{B * a.schritte / dt:8,.0f} Samples/s, Spitze {torch.cuda.max_memory_allocated() / 2 ** 30:5.1f} GB, "
              f"{sum(p.numel() for p in net.parameters()) / 1e6:.2f} Mio Parameter"
              f"{f', warten {warten / dt:.0%}' if a.lader else ''}, übersprungen {n:.0f}", flush=True)
    except torch.OutOfMemoryError:
        print(f"{name:4s} B={B:5d} kompiliert={komp} cl={cl}: Speicher voll", flush=True)
    del ad, net, opt, st


print(torch.cuda.get_device_name(0), torch.__version__, flush=True)
for name in a.netze.split(","):
    for komp in map(int, a.kompilieren.split(",")):
        for cl in map(int, a.cl.split(",")):
            for B in map(int, a.batches.split(",")):
                lauf(name, B, bool(komp), bool(cl))
