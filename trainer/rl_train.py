#!/usr/bin/env python3
"""RL-Update: advantage-gewichtete Regression (AWR) auf Arena-Trajektorien, mit Anker an bc3.

Eine Runde der RL-Schleife (rl_schleife.py, dort stehen die Entscheide und ihre Gründe).
Liest die abgeschlossenen Trajektorien (arena.ts --spur, trajektorie.py abschliessen) der
gewählten Iterationen, macht eine Epoche über alle Entscheidungen und schreibt einen neuen
Checkpoint im Format, das inf_d0 lädt (model, netz, gstep).

Verlust je Batch:
  AWR    −Σ w_t · log π(a_t | handeln), nur Zeilen mit gezogenem Aktionstyp (Ziehen Top-k, das
         einzige, was die Verhaltenspolitik zufällig wählt). w_t = min(exp(Â_t/τ), w_max)/Mittel,
         Â_t = (A_t − μ)/σ über alle Aktionszeilen der Runde, A_t = R − Φ_t (belohnung.py).
         Mit --awr-koepfe kommen weitere Köpfe dazu (z. B. target, magnitude): dann ist log π die
         Summe über die gewählten Köpfe, je Kopf nur auf Zeilen mit gültigem Label. Das lohnt nur,
         wenn inf_d0 diese Köpfe auch zieht — auf Argmax-Labels wäre es reines Schärfen.
  Anker  λ · KL(bc3 ‖ π) je Kopf auf denselben Zuständen, bedingt auf die aufgezeichneten
         Labels: Aktionstyp | handeln, alle bedingten Köpfe, Zellwahl (maskiert)
         + λ_ob · (logit P_π(handeln) − logit P_bc3(handeln))²
         P(handeln) bekommt KEINEN RL-Gradienten (die Schwelle ist deterministisch, das
         Label wäre nur die eigene Schwellenentscheidung) und wird auf der Log-Odds-Skala an
         bc3 gehalten — die binäre KL wäre bei P ≈ 0,02 fast blind (0,02 → 0,03 kostet 0,002 nats).
  Wert   value_w · (V(s) − R)², R = Endplatz-Belohnung der Episode. Der Eingang des Wertkopfs
         ist abgekoppelt (detach): er lernt mit, formt aber die Politik nicht.

  python trainer/rl_train.py --init CKPT --anker bc3.pt --spur spuren/rl1 --iterationen rl1i03,rl1i02 \\
      --aus checkpoints/rl1_i03.pt --reputation …/reputation.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from multiprocessing import get_context

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import reader as R  # noqa: E402
import verlust as V  # noqa: E402

FORMAT = "rl-awr-1"
BEDINGT = [h for h in D.HEADS if h not in ("atype", "coarse", "fine")]
GROSS_NEG = -1e9
GESCHUETZT = {"bc2.pt", "bc3.pt"}


def argumente(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", required=True, help="Politik vor dem Update (bc3.pt oder rl1_iNN.pt)")
    ap.add_argument("--anker", required=True, help="eingefrorener Anker (bc3.pt)")
    ap.add_argument("--spur", required=True, help="Ordner mit den abgeschlossenen Trajektorien")
    ap.add_argument("--iterationen", required=True, help="Komma-Liste der gid-Präfixe (Saat je Iteration)")
    ap.add_argument("--aus", required=True)
    ap.add_argument("--reputation", default=None, help="dieselbe Tabelle wie die Inferenz-Server")
    ap.add_argument("--name", default="rl")
    ap.add_argument("--iteration", type=int, default=0)
    ap.add_argument("--geraet", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--epochen", type=int, default=1)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--warmup", type=int, default=100, help="Schritte, über alle Iterationen gezählt")
    ap.add_argument("--wd", type=float, default=0.0, help="0: der Anker ist die KL, nicht der Ursprung")
    ap.add_argument("--clip", type=float, default=25.0)
    ap.add_argument("--tau", type=float, default=1.0, help="AWR-Temperatur auf dem standardisierten Vorteil")
    ap.add_argument("--awr-koepfe", default="atype",
                    help="Köpfe, die den AWR-Gradienten bekommen: atype plus beliebige bedingte Köpfe "
                         "(target, magnitude, unit_type, amount, …). Ein Kopf hilft nur, wenn die "
                         "Verhaltenspolitik ihn auch zieht (inf_d0 --ziehen-koepfe), sonst ist es Schärfen.")
    ap.add_argument("--w-max", type=float, default=5.0)
    ap.add_argument("--lam", type=float, default=1.0, help="Gewicht der KL zu bc3")
    ap.add_argument("--lam-ob", type=float, default=1.0, help="Gewicht der Log-Odds-Bindung von P(handeln)")
    ap.add_argument("--value-w", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--puffer", type=int, default=16384)
    ap.add_argument("--offen", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--log-alle", type=int, default=20)
    ap.add_argument("--max-schritte", type=int, default=0, help="0 = ganze Epoche(n)")
    ap.add_argument("--fortschritt", default=None, help="kleine JSON für die Schleife (Schritt, Gesamt)")
    ap.add_argument("--bericht", default=None, help="JSON mit Kennzahlen der Runde")
    ap.add_argument("--ueberschreiben", action="store_true")
    return ap.parse_args(argv)


def schreibe_json(pfad, obj):
    if not pfad:
        return
    tmp = pfad + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, pfad)


# ---------------------------------------------------------------- Daten der Runde
def waehle_gids(spur: str, praefixe: list[str]) -> dict:
    alle = D.finde_partien([spur])
    return {g: v for g, v in alle.items() if any(g.startswith(p + "-") for p in praefixe)}


def _meta_zeilen(arg):
    d, gid = arg
    with open(R.path_of(d, gid, "meta.zst"), "rb") as f:
        zeilen = [json.loads(x) for x in R.zdec(f.read()).decode("utf-8").split("\n") if x]
    out = []
    for m in zeilen:
        rl = m.get("rl") or {}
        out.append((float(rl.get("A", 0.0)), float(rl.get("R", 0.0)), float(rl.get("value", 0.0) or 0.0),
                    float(rl.get("p_handeln", 0.0) or 0.0), int((m.get("lab") or {}).get("atype", 0)),
                    m.get("clientID"), rl.get("ergebnis")))
    return gid, out


def statistik(sel: dict, workers: int, tau: float, w_max: float) -> dict:
    """Konstanten fürs AWR-Gewicht (μ, σ, Mittel der Gewichte) und beschreibende Kennzahlen."""
    args = [(d, g) for g, (d, _n) in sorted(sel.items())]
    with get_context("fork").Pool(max(1, workers)) as p:
        res = p.map(_meta_zeilen, args, chunksize=2)
    A, Rr, Vv, ph, at, erg, ep = [], [], [], [], [], Counter(), {}
    for gid, rows in res:
        for a_, r_, v_, p_, t_, cid, e_ in rows:
            A.append(a_); Rr.append(r_); Vv.append(v_); ph.append(p_); at.append(t_)
            erg[e_] += 1
            ep[(gid, cid)] = r_
    A, Rr, Vv, ph, at = (np.asarray(x, np.float64) for x in (A, Rr, Vv, ph, at))
    act = at > 0
    if act.sum() < 10:
        raise SystemExit(f"[abbruch] nur {int(act.sum())} Aktionszeilen — zu wenig für ein Update")
    mu, sd = float(A[act].mean()), float(A[act].std() + 1e-6)
    roh = np.exp((A[act] - mu) / sd / tau)
    w = np.minimum(roh, w_max)
    var_r = float(Rr.var()) if Rr.size else 0.0
    return {"partien": len(res), "zeilen": int(A.size), "aktionszeilen": int(act.sum()),
            "episoden": len(ep), "R_mittel": float(np.mean(list(ep.values()))),
            "mu": mu, "sd": sd, "w_mittel": float(w.mean()), "w_gekappt": float((roh > w_max).mean()),
            # Wie gut sagt der aufgezeichnete Wertkopf bzw. Φ den Endplatz voraus? (1 = perfekt)
            "erkl_var_wert": float(1 - np.var(Rr - Vv) / var_r) if var_r > 0 else None,
            "erkl_var_phi": float(1 - np.var(A) / var_r) if var_r > 0 else None,
            "p_handeln_median": float(np.median(ph)), "handelt_anteil": float(act.mean()),
            "ergebnis": {str(k): v for k, v in erg.items()},
            "atype": {str(int(k)): int(v) for k, v in sorted(Counter(at[act].astype(int).tolist()).items())}}


# ---------------------------------------------------------------- Netze
def lade(pfad: str, dev):
    z = torch.load(pfad, map_location="cpu", weights_only=True)
    ad = N.baue_netz(z.get("netz", "d1"))
    ad.modul.load_state_dict(z["model"])
    ad.modul.to(dev)
    return ad, z


# ---------------------------------------------------------------- Verlust
def _kl(lg_a, lg_p, klassen=None):
    """KL(Anker ‖ Politik) je Zeile, fp32. Masken als grosse negative Zahl, nie −inf (0·nan)."""
    lg_a, lg_p = lg_a.float(), lg_p.float()
    if klassen is not None:
        lg_a = lg_a.masked_fill(~klassen, GROSS_NEG)
        lg_p = lg_p.masked_fill(~klassen, GROSS_NEG)
    lpa = F.log_softmax(lg_a.clamp(min=GROSS_NEG), 1)
    lpp = F.log_softmax(lg_p.clamp(min=GROSS_NEG), 1)
    return (lpa.exp() * (lpa - lpp)).sum(1)


def _mittel(x, m):
    m = m.float()
    return (x * m).sum() / m.sum().clamp(min=1.0)


def awr_koepfe(o) -> list[str]:
    """Köpfe mit AWR-Gradient (--awr-koepfe), ohne atype; unbekannte Namen brechen ab."""
    aus = []
    for h in (x.strip() for x in getattr(o, "awr_koepfe", "atype").split(",")):
        if not h or h == "atype":
            continue
        if h not in BEDINGT:
            raise SystemExit(f"--awr-koepfe: {h!r} ist kein bedingter Kopf, erlaubt {BEDINGT}")
        aus.append(h)
    return aus


def rl_verlust(out: dict, ank: dict, g: dict, st: dict, o) -> tuple[torch.Tensor, dict]:
    lab = g["labels"]
    a = lab["atype"]
    act = a > 0
    lg, la = out["atype"].float(), ank["atype"].float()
    lq = F.log_softmax(lg[:, 1:], 1)
    logq = lq.gather(1, (a - 1).clamp(min=0)[:, None]).squeeze(1)
    for h in awr_koepfe(o):
        if h not in out:
            continue
        gueltig = lab[h] != D.IGNORE
        lp = F.log_softmax(out[h].float(), 1)
        lph = lp.gather(1, lab[h].clamp(min=0)[:, None]).squeeze(1)
        logq = logq + torch.where(gueltig, lph, torch.zeros_like(lph))
    ah = (g["win"] - st["mu"]) / st["sd"]
    w = torch.exp(ah / o.tau).clamp(max=o.w_max) / st["w_mittel"]
    l_awr = -_mittel(w * logq, act)

    kl_at = _kl(la[:, 1:], lg[:, 1:]).mean()
    lo = torch.logsumexp(lg[:, 1:], 1) - lg[:, 0]
    lo_a = torch.logsumexp(la[:, 1:], 1) - la[:, 0]
    l_ob = ((lo - lo_a) ** 2).mean()
    kl_k = lg.new_zeros(())
    for h in BEDINGT:
        if h in out and h in ank:
            kl_k = kl_k + _mittel(_kl(ank[h], out[h]), lab[h] != D.IGNORE)
    y, gg = lab["coarse"], g["g"]
    maske = V.zellmaske(g)
    ok = (y != D.IGNORE) & (gg >= 0) & maske.gather(1, y.clamp(min=0)[:, None]).squeeze(1)
    if out.get("coarse_da") is not None:
        ok = ok & out["coarse_da"]
    kl_c = _mittel(_kl(ank["coarse"], out["coarse"], maske | ~ok[:, None]), ok)
    lv = F.mse_loss(out["value"].float(), g["ret"])
    total = l_awr + o.lam * (kl_at + kl_k + kl_c) + o.lam_ob * l_ob + o.value_w * lv
    with torch.no_grad():
        H = -(lq.exp() * lq).sum(1).mean()
        d_ob = (lo - lo_a).abs().mean()
    return total, {"awr": l_awr, "kl_at": kl_at, "kl_k": kl_k, "kl_c": kl_c, "ob": l_ob, "d_ob": d_ob,
                   "value": lv, "H_at": H}


# ---------------------------------------------------------------- Main
def main(argv=None):
    a = argumente(argv)
    if os.path.basename(a.aus) in GESCHUETZT or os.path.abspath(a.aus) in {os.path.abspath(a.init),
                                                                         os.path.abspath(a.anker)}:
        sys.exit(f"[abbruch] {a.aus} ist geschützt (bc2/bc3/Init/Anker werden nie überschrieben)")
    if os.path.exists(a.aus) and not a.ueberschreiben:
        sys.exit(f"[abbruch] {a.aus} existiert schon")
    torch.manual_seed(a.seed)
    dev = a.geraet
    cuda = str(dev).startswith("cuda")
    if cuda:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    t0 = time.time()
    praefixe = [x.strip() for x in a.iterationen.split(",") if x.strip()]
    sel = waehle_gids(a.spur, praefixe)
    if not sel:
        sys.exit(f"[abbruch] keine abgeschlossenen Partien mit Präfix {praefixe} in {a.spur}")
    st = statistik(sel, a.workers, a.tau, a.w_max)
    print(f"[daten] {st['partien']} Partien, {st['episoden']} Episoden, {st['zeilen']} Entscheidungen, "
          f"{st['aktionszeilen']} mit Aktionstyp; A: μ={st['mu']:.4f} σ={st['sd']:.4f}, Gewicht-Mittel "
          f"{st['w_mittel']:.3f}, gekappt {st['w_gekappt']:.1%}; erkl. Varianz Φ {st['erkl_var_phi']}, "
          f"Wert {st['erkl_var_wert']} ({time.time() - t0:.1f}s)", flush=True)

    pol, zi = lade(a.init, dev)
    ank, _za = lade(a.anker, dev)
    for p in ank.modul.parameters():
        p.requires_grad_(False)
    pol.modul.train()
    ank.modul.train()                       # wie Entscheider/Trainer; GroupNorm, kein Dropout
    pol.modul.h_value.register_forward_pre_hook(lambda _m, inp: (inp[0].detach(),))
    params = [p for p in pol.modul.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=a.wd, fused=cuda)
    rl_alt = zi.get("rl") or {}
    schritt0 = 0
    if zi.get("format") == FORMAT and zi.get("opt") is not None:
        opt.load_state_dict(zi["opt"])
        schritt0 = int(rl_alt.get("schritte_gesamt", 0))
        print(f"[init] {a.init}: RL-Stand, Optimierer übernommen, {schritt0} RL-Schritte bisher", flush=True)
    else:
        print(f"[init] {a.init}: frischer Optimierer (BC-Zustand wird nicht geladen)", flush=True)

    stt = {"mu": st["mu"], "sd": st["sd"], "w_mittel": st["w_mittel"]}
    lopt = D.LaderOpt(reputation=a.reputation, zusatz=a.spur)
    gesamt = max(1, math.ceil(sum(n for _d, n in sel.values()) * a.epochen / a.batch))
    if a.max_schritte:
        gesamt = min(gesamt, a.max_schritte)
    s, uebersprungen = 0, 0
    verlauf, fenster = [], {}
    t_lauf, t_fenster, n_fenster = time.time(), time.time(), 0
    fertig = False
    for ep in range(a.epochen):
        ds = D.Strom([(g, d) for g, (d, _n) in sorted(sel.items())], a.batch,
                     max(1, a.puffer // max(1, a.workers)), a.offen, a.seed * 1000 + a.iteration * 10 + ep, lopt)
        dl = DataLoader(ds, batch_size=None, num_workers=a.workers, pin_memory=cuda,
                        prefetch_factor=4 if a.workers > 0 else None)
        for b in dl:
            if b.get("leer"):
                continue
            g = D.auf_geraet(b, dev)
            with torch.autocast(device_type="cuda" if cuda else "cpu", dtype=torch.bfloat16, enabled=cuda):
                out = pol.vorwaerts(g)
                with torch.no_grad():
                    ao = ank.vorwaerts(g)
            loss, logs = rl_verlust(out, ao, g, stt, a)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            gn = torch.nn.utils.clip_grad_norm_(params, a.clip)
            if not (torch.isfinite(loss) and torch.isfinite(gn)):
                uebersprungen += 1
                if uebersprungen > 20:
                    sys.exit("[abbruch] mehr als 20 nicht endliche Schritte")
                continue
            lr = a.lr * min(1.0, (schritt0 + s + 1) / max(1, a.warmup))
            for pg in opt.param_groups:
                pg["lr"] = lr
            opt.step()
            s += 1
            n_fenster += int(b["lab"].shape[0])
            for k, v in logs.items():
                fenster[k] = fenster.get(k, 0.0) + float(v.detach())
            fenster["gn"] = fenster.get("gn", 0.0) + float(gn)
            fenster["n"] = fenster.get("n", 0) + 1
            if s % a.log_alle == 0 or s >= gesamt:
                k = fenster.pop("n")
                m = {x: y / k for x, y in fenster.items()}
                dt = time.time() - t_fenster
                m.update(schritt=s, sps=n_fenster / max(dt, 1e-9), lr=lr, t=time.time())
                verlauf.append(m)
                print(f"S {s:>5}/{gesamt}  AWR {m['awr']:.4f}  KL at {m['kl_at']:.4f} köpfe {m['kl_k']:.4f} "
                      f"zelle {m['kl_c']:.4f}  |Δlogit ob| {m['d_ob']:.4f}  Wert {m['value']:.4f}  "
                      f"H {m['H_at']:.3f}  gn {m['gn']:.2f}  {m['sps']:.0f} Smp/s", flush=True)
                schreibe_json(a.fortschritt, {"schritt": s, "gesamt": gesamt, "t": time.time(),
                                              "start": t_lauf, **{x: m[x] for x in ("kl_at", "awr", "d_ob")}})
                fenster, t_fenster, n_fenster = {}, time.time(), 0
            if a.max_schritte and s >= a.max_schritte:
                fertig = True
                break
        del dl
        if fertig:
            break

    hinten = verlauf[-max(1, len(verlauf) // 4):] if verlauf else []

    def ende(k):
        return float(np.mean([v[k] for v in hinten])) if hinten else None

    rl = {"name": a.name, "iteration": a.iteration, "schritte": s, "schritte_gesamt": schritt0 + s,
          "init": a.init, "anker": a.anker, "iterationen": praefixe, "stat": st, "lam": a.lam,
          "lam_ob": a.lam_ob, "tau": a.tau, "w_max": a.w_max, "lr": a.lr, "value_w": a.value_w,
               "awr_koepfe": a.awr_koepfe,
          "kl_ende": ende("kl_at"), "d_ob_ende": ende("d_ob"), "vorgaenger": rl_alt.get("name"),
          "args": {k: v for k, v in vars(a).items()}}
    zust = {"format": FORMAT, "netz": pol.name, "model": pol.modul.state_dict(), "opt": opt.state_dict(),
            "gstep": int(zi.get("gstep", 0)) + s, "rl": rl}
    os.makedirs(os.path.dirname(os.path.abspath(a.aus)), exist_ok=True)
    tmp = a.aus + ".tmp"
    torch.save(zust, tmp)
    os.replace(tmp, a.aus)
    bericht = {"aus": a.aus, "schritte": s, "uebersprungen": uebersprungen, "sekunden": round(time.time() - t0, 1),
               "stat": st, "kl_ende": rl["kl_ende"], "d_ob_ende": rl["d_ob_ende"], "awr_ende": ende("awr"),
               "value_ende": ende("value"), "H_ende": ende("H_at"), "kl_k_ende": ende("kl_k"),
               "kl_c_ende": ende("kl_c"), "lam": a.lam, "verlauf": verlauf}
    schreibe_json(a.bericht, bericht)
    print(f"fertig. Checkpoint: {a.aus} ({s} Schritte, {time.time() - t0:.0f}s, KL at Ende {rl['kl_ende']})",
          flush=True)


if __name__ == "__main__":
    main()
