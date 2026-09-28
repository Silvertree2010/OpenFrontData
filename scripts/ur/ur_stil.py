#!/usr/bin/env python3
"""Stilvergleich Ultimus_Rex: seine echten Aktionen gegen Modelle (ur1, bc3).

  echt     Seine Partien aus spieler_ur.tsv (alle Splits): Aktionsverteilung und Handlungen
           je 1000 Ticks aus den meta.zst (nur seine Zeilen, Spawn ausgenommen).
  offline  Gleiche Zustände: auf seinen VAL-Partien P(Aktionstyp | handeln) je Modell gegen
           seine echten Labels (Treffer, mittlere NLL, TVD der Verteilungen), dazu
           unit_type gegeben BUILD_UNIT (Kopf ist auf das Label bedingt, netz_d0.koepfe_aus).
           Netz wie im Spiel: train()-Modus, BatchNorm eval, no_grad (spielen.py).
  spiel    Eigene Zustände: Aktionsverteilung und Handlungen je 1000 Überlebensticks aus
           Arena-JSONL (Feld "aktionen", Spawn ausgenommen), TVD gegen seine echte Verteilung.
           Vorsicht: im Spiel hängt die Verteilung auch vom Verlauf ab (wer früh stirbt,
           greift fast nur an); die Offline-Zahl trennt Stil von Spielstärke.

Aufruf auf arch, aus ~/ur-dev/code (Pfade mit = benennen, mehrere Dateien mit Komma):
  python ~/ur-dev/ur_stil.py --basis ~/of-ur --reputation …/reputation.json \\
      --ckpt ur1=~/ur-dev/ckpt/ur1.pt --ckpt bc3=~/mat-dev/netz-dev/checkpoints/bc3.pt \\
      --arena ur1=~/ur-dev/logs/ur1_bc3.a.jsonl --arena bc3=~/ur-dev/logs/ur1_bc3.b.jsonl \\
      --aus ~/ur-dev/logs/stil.json
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.getcwd(), "trainer"))
MAX_VAL = 0


def lade_liste(basis):
    sp = {}
    for z in open(os.path.join(basis, "spieler_ur.tsv")):
        t = z.rstrip("\n").split("\t")
        if z.startswith("#") or len(t) < 3:
            continue
        sp[t[0]] = (int(t[1]), t[2])
    return sp


def schluessel(it: dict) -> str | None:
    """Intent → Name wie in der Arena (arena.ts: ATYPE[/einheit|/gruppe])."""
    import actions as AC
    typ = it.get("type")
    if typ not in AC.TYPE_TO_A:
        return None
    n = AC.TYPE_TO_A[typ].name
    return f"BUILD_UNIT/{it.get('unit')}" if n == "BUILD_UNIT" else n


# Nur im Spielvergleich weggelassen: Chat ist keine Spielaktion (die Arena führt ihn nicht aus)
OHNE = ("SPAWN", "EMOJI", "QUICK_CHAT")


def norm(k: str) -> str:
    """Arena-Name → Vergleichsname: Einheit nur bei BUILD_UNIT behalten (BOAT/boot → BOAT)."""
    return k if k.startswith("BUILD_UNIT/") else k.split("/")[0]


def echt(basis, sp):
    import reader as R
    z, ticks_w, spanne = collections.Counter(), 0.0, 0
    for g, (sid, _) in sp.items():
        f = glob.glob(os.path.join(basis, "pool", "*", f"{g}.meta.zst"))
        if not f:
            continue
        tk = []
        for zl in R.zdec(open(f[0], "rb").read()).decode("utf-8").split("\n"):
            if not zl:
                continue
            m = json.loads(zl)
            if m.get("sid") != sid:
                continue
            ticks_w += float(m.get("w_tick", 1.0))
            tk.append(int(m.get("tick", 0)))
            if m.get("kind") == "act":
                k = schluessel(m.get("intent") or {})
                if k and not k.startswith(OHNE):
                    z[k] += 1
        if tk:
            spanne += max(tk) - min(tk)
    n = sum(z.values())
    return {"partien": len(sp), "handlungen": n, "ticks_w_tick": ticks_w, "ticks_spanne": spanne,
            "je_1000_w_tick": 1000 * n / max(ticks_w, 1), "je_1000_spanne": 1000 * n / max(spanne, 1),
            "zaehlung": dict(z)}


def spiel(pfade):
    z, ticks, partien, platz, siege = collections.Counter(), 0, 0, [], 0
    for p in pfade.split(","):
        for zl in open(os.path.expanduser(p)):
            d = json.loads(zl)
            partien += 1
            ticks += d.get("ueberleben_ticks", 0)
            platz.append(d.get("platz"))
            siege += bool(d.get("sieg"))
            for k, v in (d.get("aktionen") or {}).items():
                if not k.startswith(OHNE):
                    z[norm(k)] += v
    n = sum(z.values())
    platz = sorted(x for x in platz if x is not None)
    return {"partien": partien, "handlungen": n, "ueberleben_ticks": ticks, "siege": siege,
            "platz_median": platz[len(platz) // 2] if platz else None,
            "je_1000": 1000 * n / max(ticks, 1), "zaehlung": dict(z)}


def offline(ckpt, basis, sp, reputation, geraet):
    import torch
    import daten as D
    import featurize as FZ
    import netze as N
    import actions as AC
    import zusatz_felder as ZF
    z = torch.load(os.path.expanduser(ckpt), map_location="cpu", weights_only=True)
    ad = N.baue_netz(z.get("netz", "d0"), opp_dim=FZ.OPP_DIM + ZF.DIM_OPP, own_dim=FZ.OWN_DIM + ZF.DIM_GLOBAL)
    ad.modul.load_state_dict(z["model"])
    net = ad.modul.to(geraet).train()
    for mod in net.modules():
        if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm):
            mod.eval()
    val = {g: s for g, (s, v) in sp.items() if v == "val"}
    if MAX_VAL:
        val = dict(sorted(val.items())[:MAX_VAL])
    alle = D.finde_partien([os.path.join(basis, "pool")], nur=set(val))
    lopt = D.LaderOpt(reputation=reputation, zusatz=os.path.join(basis, "zusatz"),
                      spieler={g: frozenset({s}) for g, s in val.items()})
    ia, iu, bu = D.HI["atype"], D.HI["unit_type"], int(AC.A.BUILD_UNIT)
    r = {"n": 0, "treffer": 0, "nll": 0.0, "p": collections.Counter(), "lab": collections.Counter(),
         "unit_n": 0, "unit_treffer": 0, "unit_p": collections.Counter(), "unit_lab": collections.Counter()}
    batch = []

    @torch.no_grad()
    def rechne():
        b = D.auf_geraet(D.sammle(batch), geraet)
        out = net.koepfe_aus(b, net.kodiere(b, b["map"]), zeiger=False)
        lg = out["atype"].float()[:, 1:]
        p = torch.softmax(lg, 1)
        a = torch.tensor([int(s["lab"][ia]) - 1 for s in batch], device=lg.device)
        r["n"] += len(batch)
        r["treffer"] += int((p.argmax(1) == a).sum())
        r["nll"] += float(-torch.log(p[torch.arange(len(batch)), a].clamp_min(1e-12)).sum())
        for j, v in enumerate(p.sum(0).tolist()):
            r["p"][AC.A(j + 1).name] += v
        for x in a.tolist():
            r["lab"][AC.A(x + 1).name] += 1
        ist_bau = (a == bu - 1)
        if ist_bau.any():
            pu = torch.softmax(out["unit_type"].float()[ist_bau], 1)
            u = torch.tensor([int(s["lab"][iu]) for s in batch], device=lg.device)[ist_bau]
            r["unit_n"] += int(ist_bau.sum())
            r["unit_treffer"] += int((pu.argmax(1) == u).sum())
            for j, v in enumerate(pu.sum(0).tolist()):
                r["unit_p"][AC.UNIT_TYPES[j]] += v
            for x in u.tolist():
                r["unit_lab"][AC.UNIT_TYPES[x]] += 1
        batch.clear()

    t0 = time.time()
    for g in sorted(alle):
        lz = D.PartieLeser(alle[g][0], g, lopt, collections.Counter())
        while True:
            s = lz.naechstes()
            if s is D.ENDE:
                break
            if s is None or int(s["lab"][ia]) in (0, int(AC.A.SPAWN)):
                continue
            batch.append(s)
            if len(batch) == 64:
                rechne()
        lz.schliessen()
    if batch:
        rechne()
    n = max(r["n"], 1)
    return {"ckpt": ckpt, "gstep": int(z.get("gstep", 0)), "partien": len(alle), "samples": r["n"],
            "treffer_atype": r["treffer"] / n, "nll_atype": r["nll"] / n,
            "tvd_atype": 0.5 * sum(abs(r["p"][k] / n - r["lab"][k] / n) for k in set(r["p"]) | set(r["lab"])),
            "verteilung_modell": {k: v / n for k, v in r["p"].items()},
            "verteilung_echt": {k: v / n for k, v in r["lab"].items()},
            "unit_samples": r["unit_n"], "treffer_unit": r["unit_treffer"] / max(r["unit_n"], 1),
            "unit_modell": {k: v / max(r["unit_n"], 1) for k, v in r["unit_p"].items()},
            "unit_echt": {k: v / max(r["unit_n"], 1) for k, v in r["unit_lab"].items()},
            "sekunden": round(time.time() - t0, 1)}


def anteile(z):
    n = max(sum(z.values()), 1)
    return {k: v / n for k, v in z.items()}


def tvd(a, b):
    return 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--basis", default="~/of-ur")
    ap.add_argument("--reputation", default=None)
    ap.add_argument("--ckpt", action="append", default=[], help="name=pfad (offline)")
    ap.add_argument("--arena", action="append", default=[], help="name=pfad[,pfad] (spiel)")
    ap.add_argument("--geraet", default=None)
    ap.add_argument("--aus", default=None)
    ap.add_argument("--max-val", type=int, default=0, help="nur die ersten N Val-Partien (Test)")
    a = ap.parse_args()
    global MAX_VAL
    MAX_VAL = a.max_val
    import daten  # noqa: F401  (setzt sys.path auf env/ und materializer/py/)
    import torch
    geraet = a.geraet or ("cuda" if torch.cuda.is_available() else "cpu")
    basis = os.path.expanduser(a.basis)
    sp = lade_liste(basis)
    e = echt(basis, sp)
    ea = anteile(collections.Counter(e["zaehlung"]))
    erg = {"echt": e, "spiel": {}, "offline": {}}
    for spec in a.arena:
        name, pfade = spec.split("=", 1)
        s = spiel(pfade)
        s["tvd_zu_echt"] = tvd(anteile(collections.Counter(s["zaehlung"])), ea)
        erg["spiel"][name] = s
    for spec in a.ckpt:
        name, pfad = spec.split("=", 1)
        erg["offline"][name] = offline(pfad, basis, sp, a.reputation, geraet)

    # Bericht
    print(f"echt: {e['partien']} Partien, {e['handlungen']} Handlungen, "
          f"{e['je_1000_w_tick']:.1f} je 1000 Ticks (w_tick; über Tickspanne {e['je_1000_spanne']:.1f})")
    quellen = [("echt", ea)] + [(n, anteile(collections.Counter(s["zaehlung"]))) for n, s in erg["spiel"].items()]
    keys = sorted({k for _, q in quellen for k, v in q.items() if v >= 0.005}, key=lambda k: -ea.get(k, 0))
    print("im Spiel, Anteil je Aktion (ohne Spawn und Chat):")
    print(f"  {'Aktion':<28}" + "".join(f"{n:>10}" for n, _ in quellen))
    for k in keys:
        print(f"  {k:<28}" + "".join(f"{q.get(k, 0):>10.1%}" for _, q in quellen))
    for n, s in erg["spiel"].items():
        print(f"  {n}: {s['partien']} Partien, {s['je_1000']:.1f} Handlungen je 1000 Ticks, "
              f"TVD zu echt {s['tvd_zu_echt']:.3f}, Platz-Median {s['platz_median']}, Siege {s['siege']}")
    for n, o in erg["offline"].items():
        print(f"offline {n} (Schritt {o['gstep']}) auf {o['partien']} Val-Partien, {o['samples']} seiner "
              f"Handlungen: Treffer atype {o['treffer_atype']:.1%}, NLL {o['nll_atype']:.3f}, "
              f"TVD {o['tvd_atype']:.3f}; unit_type bei Bau {o['treffer_unit']:.1%} von {o['unit_samples']}")
    if erg["offline"]:
        ks = sorted({k for o in erg["offline"].values() for k, v in o["verteilung_echt"].items() if v >= 0.005},
                    key=lambda k: -next(iter(erg["offline"].values()))["verteilung_echt"].get(k, 0))
        o0 = next(iter(erg["offline"].values()))
        print(f"  {'Aktionstyp':<22}{'echt':>10}" + "".join(f"{n:>10}" for n in erg["offline"]))
        for k in ks:
            print(f"  {k:<22}{o0['verteilung_echt'].get(k, 0):>10.1%}"
                  + "".join(f"{o['verteilung_modell'].get(k, 0):>10.1%}" for o in erg["offline"].values()))
    if a.aus:
        with open(os.path.expanduser(a.aus), "w") as f:
            json.dump(erg, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
