"""Kennzahlen des BC-Trainers.

Je Kopf: Trefferquote auf Zeilen mit Label (wie bc_fit.accuracy), dazu für den
Aktionstyp "welche" (argmax ohne Nichtstun, nur Aktions-Samples) und "ob"
(P(handeln), vorhergesagt gegen echt, beide mit w_tick gewichtet).

Kachel-Kopf (ZIELWAHL_ENTWURF §8), je Gruppe aus tabellen.GRUPPEN:
  M@R       Masse der maskierten Verteilung innerhalb R echter Kacheln um res_tile.
            Zellmasse gleichverteilt auf die Zellfläche (4×4 Stützpunkte je Zelle).
            Beim Boot zählen nur Zellen mit owner_major == dst_owner.
  M@R_gl    dasselbe für gleichverteilt-legal (Messlatte)
  M@r       für r = R/2, 2R, 4R
  H@R       argmax-Zelle (maskiert), deren Mitte ≤ R von res_tile
  dNLL      log2 p(c*) + log2 |L|: Bits besser als gleichverteilt über legale Zellen
  Abstände  wie env/eval_spatial.py: Grobzellen-Abstand × 8 ("Gitterkacheln"),
            Anteil > 32, einmal für den unmaskierten argmax (vergleichbar mit den
            alten 83,6 %) und einmal maskiert; dazu Rang der echten Zelle.

Abweichung von §8, weil ohne Tier 1 bzw. Engine nicht machbar: M@R verteilt die
Zellmasse auf die ganze Zellfläche statt auf die legalen Kacheln, dNLL rechnet
auf Zellen statt auf Kacheln, H@R nimmt die Zellmitte statt der Auflösungsregel.
"""
from __future__ import annotations

import math
import time
from dataclasses import replace

import numpy as np
import torch
import torch.nn.functional as F

import daten as D
import tabellen as T
import verlust as V

CORE_HEADS = ("atype", "target", "coarse", "magnitude")      # wie bc_fit.CORE_HEADS
GW, GH = 180, 90
RADIEN = (0.5, 1.0, 2.0, 4.0)


# ---------------------------------------------------------------- je Kopf
@torch.no_grad()
def kopf_zaehler(out, b, info) -> dict:
    """Treffer und Anzahl je Kopf als Tensor (B-unabhängig), ein Sync beim Auslesen."""
    lab = b["labels"]
    namen, hits, ns = [], [], []
    for h in D.HEADS:
        if h not in out or h == "coarse":
            continue
        y = lab[h]
        m = y != D.IGNORE
        namen.append(h)
        hits.append(((out[h].argmax(1) == y) & m).sum())
        ns.append(m.sum())
    a = lab["atype"]
    act = a != 0
    namen.append("welche")
    hits.append((((out["atype"][:, 1:].argmax(1) + 1) == a) & act).sum())
    ns.append(act.sum())
    ok = info["ok"]
    namen.append("coarse")
    hits.append(((info["logits_maskiert"].argmax(1) == lab["coarse"]) & ok).sum())
    ns.append(ok.sum())
    p_act = 1.0 - torch.softmax(out["atype"].float(), 1)[:, 0]
    wt = info["wt"]
    extra = torch.stack([(p_act * wt).sum(), (act.float() * wt).sum(), wt.sum(),
                         info["gueltig"].sum().float(), (info["gueltig"] & ~info["in_maske"]).sum().float(),
                         info["ueberlauf"].sum().float()])
    vals = torch.cat([torch.stack(hits).float(), torch.stack(ns).float(), extra]).tolist()
    k = len(namen)
    res = {n: (vals[i], vals[k + i]) for i, n in enumerate(namen)}
    res["_ob"] = tuple(vals[2 * k:2 * k + 3])
    res["_maske"] = tuple(vals[2 * k + 3:2 * k + 6])     # gültig, verletzt, Zeiger-Überlauf
    return res


def treffer(z: dict) -> dict:
    # "_ob"/"_maske" haben andere Längen: erst filtern, dann entpacken
    return {h: v[0] / v[1] for h, v in z.items() if not h.startswith("_") and v[1] > 0}


def kern(ha: dict) -> float:
    ks = [ha[h] for h in CORE_HEADS if h in ha]
    return sum(ks) / max(1, len(ks))


@torch.no_grad()
def kopf_statistik(out, b, info):
    """Für das Board wie trainstats.head_stats: Entropie, normierte Entropie,
    Anteil der häufigsten Vorhersage, mittlerer Logit-Betrag; Aktionstyp-Histogramme."""
    lab = b["labels"]
    he, hen, htop, hlg = {}, {}, {}, {}
    for h in D.HEADS:
        if h not in out:
            continue
        if h == "atype":
            rows, lg = torch.ones_like(lab[h], dtype=torch.bool), out[h]
        elif h == "coarse":
            rows, lg = info["ok"], info["logits_maskiert"]
        else:
            rows, lg = lab[h] != D.IGNORE, out[h]
        sub = lg[rows].float()
        if sub.numel() == 0:
            continue
        logp = F.log_softmax(sub, -1)
        ent = -(logp.exp() * logp).nan_to_num(0.0).sum(-1)
        K = sub.shape[-1]
        he[h] = float(ent.mean())
        hen[h] = he[h] / math.log(max(K, 2))
        pred = sub.argmax(-1)
        htop[h] = float(torch.bincount(pred, minlength=K).max()) / pred.numel()
        fin = sub[torch.isfinite(sub) & (sub.abs() < 1e8)]
        hlg[h] = float(fin.abs().mean()) if fin.numel() else 0.0
    na = out["atype"].shape[1]
    pred_hist = torch.bincount(out["atype"].argmax(-1), minlength=na)[:na].tolist()
    lab_hist = torch.bincount(lab["atype"], minlength=na)[:na].tolist()
    return he, hen, htop, hlg, pred_hist, lab_hist


def adv_statistik(adv: torch.Tensor, win: torch.Tensor, beta: float) -> dict:
    q = torch.quantile(adv.float(), torch.tensor([0.1, 0.5, 0.9], device=adv.device)).tolist()
    return {"mean": float(adv.mean()), "p10": q[0], "p50": q[1], "p90": q[2],
            "max": float(adv.max())}


# ---------------------------------------------------------------- Kachel
def flaechenanteil(xy, wh, r, k: int = 4):
    """(S,16200): Anteil der Zellfläche innerhalb Radius r (Kacheln) um xy.
    k×k Stützpunkte je Zelle; die Zelle (i,j) deckt x ∈ [j·W/180, (j+1)·W/180)."""
    dev = xy.device
    off = (torch.arange(k, device=dev) + 0.5) / k
    px = ((torch.arange(GW, device=dev)[:, None] + off[None]).reshape(-1))[None] * wh[:, 0:1] / GW
    py = ((torch.arange(GH, device=dev)[:, None] + off[None]).reshape(-1))[None] * wh[:, 1:2] / GH
    dx2 = (px - xy[:, 0:1]) ** 2                                   # (S,180k)
    dy2 = (py - xy[:, 1:2]) ** 2                                   # (S,90k)
    innen = (dy2[:, :, None] + dx2[:, None, :]) <= (r ** 2)[:, None, None]
    S = xy.shape[0]
    return innen.view(S, GH, k, GW, k).float().mean((2, 4)).reshape(S, GH * GW)


def _zellabstand(a, b):
    return torch.hypot((a % GW - b % GW).float(), (a // GW - b // GW).float())


@torch.no_grad()
def raum_einzel(out, b, info) -> dict:
    """Kennzahlen je Zeile mit Kachel-Verlust (info["ok"]). Rückgabe: numpy je Feld."""
    rows = info["ok"].nonzero().squeeze(1)
    if rows.numel() == 0:
        return {}
    lgm = info["logits_maskiert"][rows]
    mk = info["maske"][rows]
    roh = out["coarse"].float()[rows]
    y = b["labels"]["coarse"][rows]
    g = b["g"][rows]
    xy, wh = b["xy"][rows], b["wh"][rows]
    p = torch.softmax(lgm, 1)
    pred = lgm.argmax(1)
    ly = lgm.gather(1, y[:, None])
    nL = mk.sum(1).float()
    R = V.r_kacheln(g)
    px = ((pred % GW).float() + 0.5) * wh[:, 0] / GW
    py = ((pred // GW).float() + 0.5) * wh[:, 1] / GH
    d = torch.hypot(px - xy[:, 0], py - xy[:, 1])
    boot = g == T.GID["boot"]
    gebiet = torch.where(boot[:, None], b["owner"][rows].long() == b["dst"][rows][:, None],
                         torch.ones_like(mk))
    pu = mk.float() / nL[:, None]
    e = {"g": g, "exakt": (pred == y).float(), "rang": ((lgm > ly) & mk).sum(1).float(),
         "dnll": (torch.log(p.gather(1, y[:, None]).squeeze(1).clamp(min=1e-30)) + torch.log(nL)) / math.log(2),
         "abst": d, "treffer_R": (d <= R).float(), "n_legal": nL,
         "alt_unmaskiert": _zellabstand(roh.argmax(1), y) * 8, "alt_maskiert": _zellabstand(pred, y) * 8}
    for f in RADIEN:
        fr = flaechenanteil(xy, wh, R * f) * gebiet.float()
        e[f"M{f:g}"] = (p * fr).sum(1)
        e[f"Mgl{f:g}"] = (pu * fr).sum(1)
    return {k: v.detach().cpu().numpy() for k, v in e.items()}


def _fasse(e: dict, sel) -> dict:
    n = int(sel.sum())
    if n == 0:
        return {"n": 0}
    x = {k: v[sel] for k, v in e.items()}
    r = {"n": n, "M@R": float(x["M1"].mean()), "M@R_gl": float(x["Mgl1"].mean()),
         "M@R/2": float(x["M0.5"].mean()), "M@2R": float(x["M2"].mean()), "M@4R": float(x["M4"].mean()),
         "H@R": float(x["treffer_R"].mean()), "exakt": float(x["exakt"].mean()),
         "rang_median": float(np.median(x["rang"])), "top100": float((x["rang"] < 100).mean()),
         "dnll_bits": float(x["dnll"].mean()), "abst_median_kacheln": float(np.median(x["abst"])),
         "legal_median": float(np.median(x["n_legal"])),
         "alt_ueber32_unmaskiert": float((x["alt_unmaskiert"] > 32).mean()),
         "alt_median_unmaskiert": float(np.median(x["alt_unmaskiert"])),
         "alt_ueber32_maskiert": float((x["alt_maskiert"] > 32).mean())}
    return r


# ---------------------------------------------------------------- Eval-Lauf
@torch.no_grad()
def bewerte(adapter, es: dict, dev, vopt: V.VerlustOpt, batch: int = 128, amp: bool = True) -> dict:
    """Festes Eval-Set (daten.waehle_eval). Verlust mit adv_beta 0, sonst wie im
    Training. Das Netz rechnet im train()-Modus: env/net.py hat weder Dropout noch
    BatchNorm, und eval() stürzt im Gegner-Transformer bei leerer Gegnermaske ab
    (siehe env/eval_spatial.py)."""
    t0 = time.time()
    net = adapter.modul
    war = net.training
    net.train()
    for mod in net.modules():               # BatchNorm (falls vorhanden) mit festen Statistiken
        if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm):
            mod.eval()
    vo = replace(vopt, adv_beta=0.0)
    dtyp = dev.split(":")[0] if isinstance(dev, str) else dev.type

    def laeufe(samples):
        for i in range(0, len(samples), batch):
            b = D.auf_geraet(D.sammle(samples[i:i + batch]), dev)
            b["zeiger_voll"] = True             # D0: Zeiger für alle Zeilen, kein Überlauf im Eval
            with torch.autocast(device_type=dtyp, dtype=torch.bfloat16, enabled=amp):
                out = adapter.vorwaerts(b)
            tot, logs, info = V.berechne(out, b, vo)
            yield b, out, tot, logs, info

    # allgemein: Verlust und Treffer je Kopf
    sum_l, sum_n, zsum = 0.0, 0, {}
    tl = {}
    for b, out, tot, logs, info in laeufe(es["allg"]):
        n = b["w"].shape[0]
        sum_l += float(tot) * n
        sum_n += n
        for k, v in logs.items():
            tl[k] = tl.get(k, 0.0) + float(v) * n
        for h, v in kopf_zaehler(out, b, info).items():
            zsum[h] = tuple(x + y for x, y in zip(zsum.get(h, (0.0,) * len(v)), v))
    ha = treffer(zsum)
    ob = zsum.get("_ob", (0.0, 0.0, 1.0))
    res = {"n_allg": sum_n, "vloss": sum_l / max(1, sum_n), "vacc": kern(ha), "vha": ha,
           "vteile": {k: v / max(1, sum_n) for k, v in tl.items()},
           "p_handeln": {"vorhergesagt": ob[0] / max(ob[2], 1e-9), "echt": ob[1] / max(ob[2], 1e-9)}}

    # räumlich
    teile, verletzt, gueltig = [], 0.0, 0.0
    for b, out, tot, logs, info in laeufe(es["raum"]):
        gueltig += float(info["gueltig"].sum())
        verletzt += float((info["gueltig"] & ~info["in_maske"]).sum())
        e = raum_einzel(out, b, info)
        if e:
            teile.append(e)
    raum = {}
    if teile:
        e = {k: np.concatenate([t[k] for t in teile]) for k in teile[0]}
        for gi, gname in enumerate(T.GRUPPEN):
            r = _fasse(e, e["g"] == gi)
            if r["n"]:
                raum[gname] = r
        # gewichtetes Mittel mit dem Anteil der Gruppe in den Val-Partien
        ant = {g: es["anteile"].get(g, 0) for g in raum}
        s = sum(ant.values())
        if s > 0:
            keys = [k for k in next(iter(raum.values())) if k != "n"]
            raum["mittel"] = {k: sum(raum[g][k] * ant[g] for g in raum) / s for k in keys}
            raum["mittel"]["n"] = int(sum(raum[g]["n"] for g in ant))
    res["raum"] = raum
    res["maske_verletzt"] = {"n": verletzt, "von": gueltig}
    res["secs"] = time.time() - t0
    net.train(war)                           # auch BatchNorm zurück in den vorigen Modus
    return res


def drucke(res: dict, schritt: int):
    ha = res["vha"]
    kopf = "  ".join(f"{h} {v:.1%}" for h, v in ha.items())
    ph = res["p_handeln"]
    print(f"[eval S{schritt}] allg {res['n_allg']} Samples  Verlust {res['vloss']:.3f}  "
          f"Kern {res['vacc']:.1%}  ({res['secs']:.0f}s)", flush=True)
    print(f"  Treffer: {kopf}", flush=True)
    print(f"  Teilverluste: " + "  ".join(f"{k} {v:.3f}" for k, v in res["vteile"].items()), flush=True)
    print(f"  P(handeln) vorhergesagt {ph['vorhergesagt']:.2%}, echt {ph['echt']:.2%} (w_tick)", flush=True)
    mv = res["maske_verletzt"]
    print(f"  Kachel-Labels ausserhalb der Maske: {mv['n']:.0f} von {mv['von']:.0f}", flush=True)
    kol = ["n", "M@R", "M@R_gl", "M@2R", "H@R", "exakt", "rang_median", "dnll_bits",
           "abst_median_kacheln", "alt_ueber32_unmaskiert", "alt_ueber32_maskiert"]
    print("  " + f"{'Gruppe':<15s}" + "".join(f"{k[:11]:>12s}" for k in kol), flush=True)
    for g, r in res["raum"].items():
        zeile = []
        for k in kol:
            v = r.get(k, float("nan"))
            zeile.append(f"{v:>12d}" if k == "n" else
                         (f"{v:>12.1%}" if k.startswith(("M@", "H@", "exakt", "alt_")) else f"{v:>12.2f}"))
        print("  " + f"{g:<15s}" + "".join(zeile), flush=True)
