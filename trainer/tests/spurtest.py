#!/usr/bin/env python3
"""Prüft aufgezeichnete Trajektorien (arena.ts --spur, danach trajektorie.py abschliessen).

  python trainer/tests/spurtest.py --ckpt checkpoints/bc3.pt --spur ORDNER \\
      --reputation …/reputation.json [--stichprobe 600 | --alle]

1. Lader: daten.finde_partien + PartieLeser (zusatz = derselbe Ordner) lesen jede Zeile, nichts
   verworfen, win = rl.A.
2. Labels: lab (Wahl der Verhaltenspolitik) gegen die Rückrechnung aus dem gesendeten Intent
   (actions.encode, res_tile → Zelle) — Abweichungen je Kopf.
3. log π (logp_n): Stichprobe neu gerechnet mit dem Trainer-Vorwärtslauf (adapter.vorwaerts auf
   dem Lader-Batch, Labels = lab, wie bewertung.bewerte) — |Δ| je Kopf.
4. log μ (logp_v, Aktionstyp): aus den neu gerechneten Logits mit Temperatur und Top-k des
   Servers; dazu Kalibrierung: gezogener Rang gegen die erwartete Häufigkeit (χ², 3 FG).
5. Belohnung: G_t = R − Φ_t, R aus dem Kopf nach --ziel (belohnung.endwert), Φ in den Grenzen.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import reader as R  # noqa: E402
import verlust as V  # noqa: E402
import belohnung as B  # noqa: E402
import tabellen as T  # noqa: E402
import actions as AC  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", required=True)
ap.add_argument("--spur", nargs="+", required=True)
ap.add_argument("--reputation", default=None)
ap.add_argument("--stichprobe", type=int, default=600)
ap.add_argument("--alle", action="store_true")
ap.add_argument("--batch", type=int, default=32)
ap.add_argument("--threads", type=int, default=4)
ap.add_argument("--tol", type=float, default=2e-3)
ap.add_argument("--json", default=None, help="Ergebnis zusätzlich als JSON hierhin")
ap.add_argument("--ziel", default="platz", help="Endbelohnung, mit der die Spuren abgeschlossen wurden")
A = ap.parse_args()
torch.set_num_threads(A.threads)
FEHLER, BERICHT = [], {}


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}", flush=True)
    if not ok:
        FEHLER.append(name)


# ------------------------------------------------------------ 1. Lader
alle = D.finde_partien(A.spur)
samples, metas, hdrs = [], {}, {}
z = Counter()
soll = 0
for gid, (d, n) in sorted(alle.items()):
    leser = D.PartieLeser(d, gid, D.LaderOpt(reputation=A.reputation, zusatz=d), z)
    k = 0
    while True:
        s = leser.naechstes()
        if s is D.ENDE:
            break
        if s is None:
            continue
        samples.append(s)
        k += 1
    leser.schliessen()
    g = R.Game(d, gid)
    metas[gid], hdrs[gid] = g.meta(), g.hdr
    soll += n
verworfen = {k: v for k, v in z.items() if k.startswith(("verworfen", "partie_fehler", "zusatz_fehlt", "erster"))}
pruefe("Lader liest jede Zeile", len(samples) == soll and not verworfen,
       f"{len(samples)}/{soll} Zeilen aus {len(alle)} Partien, verworfen {verworfen}")
win_d = max((abs(s["win"] - metas[s["id"][0]][s["id"][1]]["rl"]["A"]) for s in samples), default=0.0)
pruefe("win = rl.A im Lader", win_d < 1e-6, f"max|Δ|={win_d:.1e}")
BERICHT["lader"] = {"partien": len(alle), "zeilen": len(samples), "zaehler": dict(z)}

# ------------------------------------------------------------ 2. Labels gegen Intent
abw, n_lab = Counter(), Counter()
for s in samples:
    gid, i = s["id"]
    m = metas[gid][i]
    if m["rl"]["ergebnis"] != "ausgefuehrt":
        continue
    ctx = AC.Context(map_w=m["mapW"], map_h=m["mapH"], troops=m["troops"], gold=m["gold"],
                     opp_ids=m.get("oppIds") or [], own_unit_ids=m.get("ownUnitIds") or [],
                     own_attack_ids=m.get("ownAttackIds") or [])
    act = AC.encode(m["intent"], ctx)
    lab = m["lab"]
    n_lab["atype"] += 1
    abw["atype"] += int(act.atype) != lab["atype"]
    g = T.gruppe(m["intent"])
    for h in AC.HEAD_SCHEMA.get(AC.A(int(act.atype)), ()):
        if h in ("coarse", "fine") or (h == "target" and g in T.ZIEL_GRUPPEN):
            continue                               # räumlich: Zelle aus res_tile; Ziel: aus dst_owner
        v = getattr(act, h)
        if h in lab:
            n_lab[h] += 1
            abw[h] += (v if v is not None else -1) != lab[h]
    if "coarse" in lab:
        n_lab["coarse"] += 1
        abw["coarse"] += AC.tile_encode(int(m["res_tile"]), m["mapW"], m["mapH"])[0] != lab["coarse"]
BERICHT["labels"] = {h: f"{abw[h]}/{n_lab[h]}" for h in n_lab}
pruefe("lab = Rückrechnung aus dem Intent", sum(abw.values()) == 0, json.dumps(BERICHT["labels"]))

# ------------------------------------------------------------ 3./4. Neu rechnen
z_ck = torch.load(A.ckpt, map_location="cpu", weights_only=True)
ad = N.baue_netz(z_ck.get("netz", "d0"))
ad.modul.load_state_dict(z_ck["model"])
net = ad.modul
net.train()
for mod in net.modules():
    if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm):
        mod.eval()

rnd = random.Random(0)
# Kalibrierung (Punkt 4) nur auf einer GLEICHVERTEILTEN Auswahl: räumliche Zeilen sind nach dem
# Ergebnis der Ziehung ausgewählt (gezogen wurde ein Bau-/Boot-Typ, der selten Rang 1 ist) und
# verfälschen die Rangverteilung — gemessen am 14.09.: mit bevorzugten räumlichen Zeilen fiel χ²
# durch (beob [294, 111, 30, 6] gegen erw [358, 62, …]). Für log π kommen sie extra dazu.
if A.alle:
    auswahl = list(samples)
    gleich = {s["id"] for s in samples}
else:
    idx = list(range(len(samples)))
    rnd.shuffle(idx)
    uni = [samples[i] for i in idx[:max(1, A.stichprobe * 2 // 3)]]
    gleich = {s["id"] for s in uni}
    raum = [s for s in samples if s["cell"] is not None and s["id"] not in gleich]
    rnd.shuffle(raum)
    auswahl = uni + raum[:max(0, A.stichprobe - len(uni))]
server = next(iter(hdrs.values()))["rl"]["server"]
temp, top_k = float(server.get("temp", 1.0)), int(server.get("top_k", 0))
delta = defaultdict(list)
fehlt, extra = Counter(), Counter()
rang_obs, rang_erw = np.zeros(max(top_k, 1)), np.zeros(max(top_k, 1))
lv_delta, ausser_topk = [], 0
with torch.no_grad():
    for a0 in range(0, len(auswahl), A.batch):
        chunk = auswahl[a0:a0 + A.batch]
        b = D.auf_geraet(D.sammle(chunk), "cpu")
        b["zeiger_voll"] = True
        out = ad.vorwaerts(b)
        lab = b["labels"]
        maske = V.zellmaske(b)
        la = out["atype"].float()
        for i, s in enumerate(chunk):
            gid, j = s["id"]
            m = metas[gid][j]
            neu = {}
            a = int(lab["atype"][i])
            if a == 0:
                neu["ob"] = float(torch.log_softmax(la[i], 0)[0])
            else:
                neu["ob"] = float(torch.logsumexp(la[i, 1:], 0) - torch.logsumexp(la[i], 0))
                neu["atype"] = float(torch.log_softmax(la[i, 1:], 0)[a - 1])
            for h in D.HEADS:
                if h in ("atype", "coarse", "fine") or h not in out:
                    continue
                y = int(lab[h][i])
                if y != D.IGNORE:
                    neu[h] = float(torch.log_softmax(out[h][i].float(), 0)[y])
            y = int(lab["coarse"][i])
            if y != D.IGNORE:
                neu["coarse"] = float(torch.log_softmax(out["coarse"][i].float().masked_fill(~maske[i], float("-inf")), 0)[y])
            alt = m["rl"]["logp_n"]
            for h, v in alt.items():
                if h in neu:
                    delta[h].append(abs(neu[h] - v))
                else:
                    fehlt[h] += 1
            for h in neu:
                if h not in alt:
                    extra[h] += 1
            # log μ des Aktionstyps: dieselbe Top-k-Verteilung wie spielen.Entscheider._waehle_lp
            if a >= 1 and m["rl"].get("gezogen"):
                lg = la[i, 1:] / max(1e-3, temp)
                if 0 < top_k < lg.numel():
                    werte, idx = lg.topk(top_k)
                    pos = (idx == a - 1).nonzero()
                    if len(pos) == 0:
                        ausser_topk += 1
                        continue
                    p = int(pos[0])
                    lv = float(torch.log_softmax(werte, 0)[p])
                    if s["id"] in gleich:
                        rang_obs[p] += 1
                        rang_erw += torch.softmax(werte, 0).numpy()
                else:
                    lv = float(torch.log_softmax(lg, 0)[a - 1])
                lv_delta.append(abs(lv - m["rl"]["logp_v"]["atype"]))

BERICHT["logp_n"] = {h: {"n": len(v), "max": float(max(v)), "median": float(np.median(v))} for h, v in delta.items()}
worst = max((max(v) for v in delta.values()), default=float("inf"))
pruefe("log π je Kopf neu gerechnet = aufgezeichnet", worst < A.tol and not fehlt and not extra,
       f"{len(auswahl)} Zeilen, max|Δ|={worst:.2e}, fehlt {dict(fehlt)}, extra {dict(extra)}")
for h, v in sorted(BERICHT["logp_n"].items()):
    print(f"       {h:<12} n={v['n']:<5} max|Δ|={v['max']:.2e}  median={v['median']:.1e}")
lv_max = max(lv_delta, default=float("inf"))
pruefe("log μ(Aktionstyp) aus Top-k/Temperatur = aufgezeichnet", lv_max < A.tol and ausser_topk == 0,
       f"n={len(lv_delta)}, max|Δ|={lv_max:.2e}, ausserhalb Top-{top_k}: {ausser_topk}")
if rang_erw.sum() > 0 and top_k > 0:
    chi2 = float(((rang_obs - rang_erw) ** 2 / np.maximum(rang_erw, 1e-9)).sum())
    fg = top_k - 1
    # Überlebensfunktion χ² mit 3 FG (Top-4): erfc(√(x/2)) + √(2x/π)·e^(−x/2)
    p = (math.erfc(math.sqrt(chi2 / 2)) + math.sqrt(2 * chi2 / math.pi) * math.exp(-chi2 / 2)) if fg == 3 else None
    BERICHT["kalibrierung"] = {"beobachtet": rang_obs.tolist(), "erwartet": [round(x, 1) for x in rang_erw],
                               "chi2": round(chi2, 2), "fg": fg, "p": p}
    pruefe("gezogene Ränge passen zur Verteilung (χ²)", p is None or p > 0.001,
           f"beob {rang_obs.astype(int).tolist()} erw {[round(x, 1) for x in rang_erw]} χ²={chi2:.2f} p={p}")

# ------------------------------------------------------------ 5. Belohnung
g_d, phi_bad, r_bad, n_ep = 0.0, 0, 0, 0
gw = B.gewichte_fuer(A.ziel)
for gid, meta in metas.items():
    erg = {k["clientID"]: k for k in hdrs[gid]["ki"]}
    je = defaultdict(list)
    for m in meta:
        je[m["clientID"]].append(m["rl"])
    for cid, rls in je.items():
        n_ep += 1
        R_ = B.endwert(erg[cid], A.ziel)
        r_bad += any(abs(x["R"] - R_) > 1e-5 for x in rls)
        for x in rls:
            g_d = max(g_d, abs(x["G"] - (R_ - x["phi"])))
            phi_bad += not (0 <= x["phi"] <= gw.c + gw.w_u + gw.w_g + 1e-6)
pruefe(f"G_t = R − Φ_t, R nach Ziel {A.ziel}, Φ in Grenzen", g_d < 1e-4 and phi_bad == 0 and r_bad == 0,
       f"{n_ep} Episoden, max|Δ|={g_d:.1e}, Φ ausserhalb {phi_bad}, R falsch {r_bad}")

print(f"\n{'ALLES OK' if not FEHLER else 'FEHLER: ' + ', '.join(FEHLER)}")
if A.json:
    with open(A.json, "w") as f:
        json.dump({"fehler": FEHLER, **BERICHT}, f, indent=1)
sys.exit(1 if FEHLER else 0)
