"""
Behavior-Cloning-Trainer.

Maskierter Multi-Kopf-Verlust: pro Beispiel wird nur auf den Köpfen gelernt,
die der Aktionstyp verwendet (HEAD_SCHEMA). Ein Angriff lernt target+magnitude,
kein Kachel-Kopf. So kämpfen die Köpfe nicht gegeneinander.

Checkpoint/Resume ist eingebaut und getestet — Pflicht, weil der Trainings-PC
(Arch) nur sporadisch läuft: ein Abschalten darf nie Fortschritt verlieren.

Dieser Lauf trainiert auf SYNTHETISCHEN Daten (echte kommen aus dem Replay auf
Arch). Zweck: beweisen, dass die Lernmaschine funktioniert, BEVOR echte Daten
da sind — auf einem kleinen festen Satz muss der Verlust gegen null gehen.
"""
from __future__ import annotations

import os, sys, time, argparse
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, "env")
import actions as AC
from net import Net, GH, GW, NUM_MAP_CH, OWN_DIM, OPP_DIM, MAX_OPP, head_masks_for

CKPT = "checkpoints/bc.pt"
HEADS = list(AC.HEAD_SIZES.keys())
IGNORE_INDEX = -100     # gedropptes/unaufloesbares Label → nicht supervidieren

# Advantage-Weighted BC: Aktionen von Gewinnern (win=1) staerker lernen als von
# Verlierern (win=0). Gewicht = exp(BETA*(win - batch_mean)), geklemmt + auf
# Mittel 1 normiert (Loss-Skala bleibt stabil). BETA=0 -> reines BC.
ADV_BETA = float(os.environ.get("ADV_BETA", "1.5"))
ADV_WMIN, ADV_WMAX = 0.25, 4.0
VALUE_W = float(os.environ.get("VALUE_W", "1.0"))   # Gewicht des Wert-Kopf-Verlusts

# ----------------------------------------------------- Kachel-Zeiger, Schalter
# Gemessen (env/eval_spatial.py, Val-Anteil, 4122 Samples, Schritt 60000):
#   Grob-Kopf 2.69% exakt, aber 83.6% der Vorhersagen > 32 Kacheln daneben.
#   Fein-Kopf auf der ECHTEN Grobzelle 4.8 Kacheln Mittel — SCHLECHTER als die
#   feste Zellmitte (3.1) und schlechter als Zufall (4.1).
# Ursache beim Grob-Kopf: reine Kreuzentropie auf exakte Uebereinstimmung. Eine
# Kachel daneben kostet genauso viel wie 500 daneben, also gibt es kein Signal
# in Richtung "naeher dran ist besser".
#
# COARSE_SIGMA > 0 ersetzt das harte Ziel durch eine 2D-Gauss-Zielverteilung
# ueber dem 180x90-Grobgitter (Breite in GROBZELLEN, beide Achsen). 0 = aus,
# dann laeuft exakt der alte Pfad (bitgleich).
COARSE_SIGMA = float(os.environ.get("COARSE_SIGMA", "0"))
# Anteil des weichen Ziels am Grob-Verlust; der Rest bleibt hartes CE.
# 1.0 = nur weich. Wirkt nur, wenn COARSE_SIGMA > 0.
COARSE_SOFT_MIX = float(os.environ.get("COARSE_SOFT_MIX", "1.0"))

# FINE_OFF nimmt den Fein-Kopf aus Verlust UND Vorhersage; statt seines Argmax
# gilt die Mitte der gewaehlten Grobzelle (die gemessen bessere Konstante).
# Der Kopf bleibt im Netz und im Checkpoint, er bekommt nur keinen Gradienten
# mehr (kein Eintrag im Verlust -> p.grad bleibt None -> AdamW ueberspringt ihn).
FINE_OFF = os.environ.get("FINE_OFF", "0").strip().lower() not in ("", "0", "false", "no")
# Identisch zur Konstanten C6 in env/eval_spatial.py (dort mit 3.1 Kacheln gemessen).
FINE_CENTER = (AC.FINE_H // 2) * AC.FINE_W + (AC.FINE_W // 2)   # 36 = (x=4,y=4)


def coarse_soft_ce(logits, tgt):
    """Kreuzentropie des Grob-Kopfs gegen eine 2D-Gauss-Zielverteilung.

    Warum weiches Ziel und nicht "CE + Abstand des Erwartungswerts": der
    Erwartungswert einer mehrgipfligen Hitzekarte liegt zwischen den Gipfeln,
    oft auf einer Zelle, die niemand vorhersagt — dieser Term kann also belohnt
    werden, ohne dass die Vorhersage besser wird. Das weiche Ziel bestraft
    dagegen jede Wahrscheinlichkeitsmasse nach ihrem eigenen Abstand.

    Speicher/Rechnung: die Zielverteilung ist ein PRODUKT zweier 1D-Gaussen,
    q(y,x) = gy(y)*gx(x), weil der quadrierte euklidische Abstand in y und x
    zerfaellt. Damit ist
        CE = -sum_{y,x} q(y,x) log p(y,x) = -einsum("byx,by,bx->b", logp, gy, gx)
    also O(B*16200) ohne jede Distanzmatrix (eine 16200x16200-Matrix waere
    ~1 TB). softmax(-(d^2)/2s^2) je Achse ist exakt die am Rand abgeschnittene
    und neu normierte Gauss-Verteilung — Randzellen bekommen so kein zu kleines
    Gewicht.

    Rueckgabe ist KL(q||p) = CE - H(q) je Beispiel. H(q) = H(gy)+H(gx) haengt
    nicht von den Netzparametern ab, der Gradient ist also derselbe wie bei
    reinem CE; die geloggte Zahl bleibt aber mit dem harten CE vergleichbar
    (untere Schranke 0 statt "Entropie-Sockel").
    """
    B = logits.shape[0]
    W, H = AC.COARSE_W, AC.COARSE_H
    logp = torch.log_softmax(logits.float(), dim=1).view(B, H, W)
    ty = (tgt.div(W, rounding_mode="floor")).float().unsqueeze(1)      # (B,1)
    tx = (tgt % W).float().unsqueeze(1)                                # (B,1)
    ay = torch.arange(H, device=logits.device, dtype=torch.float32).unsqueeze(0)
    ax = torch.arange(W, device=logits.device, dtype=torch.float32).unsqueeze(0)
    s2 = 2.0 * COARSE_SIGMA * COARSE_SIGMA
    gy = torch.softmax(-(ay - ty).pow(2) / s2, dim=1)                  # (B,H)
    gx = torch.softmax(-(ax - tx).pow(2) / s2, dim=1)                  # (B,W)
    ce = -torch.einsum("byx,by,bx->b", logp, gy, gx)
    ent = -((gy * gy.clamp_min(1e-30).log()).sum(1) +
            (gx * gx.clamp_min(1e-30).log()).sum(1))                   # H(q)=H(gy)+H(gx)
    return ce - ent


def fine_predict(out):
    """Feinzelle fuer die Vorhersage. Bei FINE_OFF die Mitte der Grobzelle."""
    if FINE_OFF:
        return torch.full((out["fine"].shape[0],), FINE_CENTER,
                          dtype=torch.long, device=out["fine"].device)
    return out["fine"].argmax(1)


def tile_switches():
    """Aktuelle Schalterstellung (fuer Log/Metrik)."""
    return {"coarse_sigma": COARSE_SIGMA, "coarse_soft_mix": COARSE_SOFT_MIX,
            "fine_off": bool(FINE_OFF), "fine_center": FINE_CENTER}


def make_synthetic(n, seed=0, device="cpu"):
    """Fester Zufallssatz. Labels sind gültig je Aktionstyp (nur aktive Köpfe
    gesetzt). Auf einem kleinen festen Satz muss das Netz das auswendig lernen
    können → Trainingsverlust gegen 0. Das prüft Optimierung+Verlust+Maske."""
    g = torch.Generator().manual_seed(seed)
    map_t = torch.randn(n, NUM_MAP_CH, GH, GW, generator=g)
    own_t = torch.randn(n, OWN_DIM, generator=g)
    opp_t = torch.randn(n, MAX_OPP, OPP_DIM, generator=g)
    n_opp = torch.randint(1, MAX_OPP + 1, (n,), generator=g)
    opp_mask = torch.arange(MAX_OPP)[None, :] < n_opp[:, None]

    labels = {h: torch.zeros(n, dtype=torch.long) for h in HEADS}
    labels["value"] = torch.randn(n, generator=g)
    atypes = torch.randint(0, len(AC.A), (n,), generator=g)
    labels["atype"] = atypes
    for i in range(n):
        a = AC.A(atypes[i].item())
        for h in AC.HEAD_SCHEMA.get(a, ()):
            hi = HEADS.index(h)
            size = AC.HEAD_SIZES[h]
            if h == "target":
                # gültiges Ziel: vorhandener Gegner [0,n_opp) ODER Sonderziel
                # (NEUTRAL=MAX_OPP, ALLE=MAX_OPP+1) — NIE ein maskierter Slot
                no = int(n_opp[i].item())
                choices = list(range(no)) + [AC.TGT_NEUTRAL, AC.TGT_ALL]
                labels[h][i] = choices[torch.randint(0, len(choices), (1,), generator=g).item()]
            else:
                labels[h][i] = torch.randint(0, size, (1,), generator=g)
    data = dict(map=map_t, own=own_t, opp=opp_t, mask=opp_mask,
                labels=labels, atypes=atypes)
    return {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in data.items()} | \
           {"labels": {h: t.to(device) for h, t in labels.items()}}


def masked_loss(out, labels, atypes, weighted=True):
    """Cross-Entropy je Kopf, nur auf den Beispielen, wo der Kopf aktiv ist.
    Wert-Kopf: MSE. atype ist immer aktiv.

    weighted=True: Advantage-Weighting — jedes Beispiel wird nach Spielausgang
    (win) gewichtet, sodass Gewinner-Aktionen staerker imitiert werden als
    Verlierer-Aktionen. weighted=False (Eval): reines, vergleichbares BC."""
    masks = head_masks_for(atypes)
    total = out["atype"].new_zeros(())
    logs = {}

    # Pro-Beispiel-Gewicht aus dem Spielausgang (batch-zentriert, geklemmt, Mittel 1).
    win = labels["value"].float()
    if weighted and ADV_BETA > 0:
        w = torch.exp(ADV_BETA * (win - win.mean())).clamp(ADV_WMIN, ADV_WMAX)
        w = w / w.mean().clamp(min=1e-6)
    else:
        w = torch.ones_like(win)

    # atype (immer aktiv) — gewichtetes Mittel
    per = F.cross_entropy(out["atype"], labels["atype"], reduction="none")  # (B,)
    la = (per * w).mean()
    total = total + la
    logs["atype"] = la.item()

    for h in HEADS:
        if h == "atype":
            continue
        if h == "fine" and FINE_OFF:            # Kopf bleibt im Netz, lernt aber nicht mehr
            continue
        m = masks[h].to(out[h].device)
        if m.any():
            tgt = labels[h][m]
            wsub = w[m]
            valid = tgt != IGNORE_INDEX
            if valid.any():                     # sonst nan (alle im Batch gedroppt)
                lg = out[h][m][valid]
                per = F.cross_entropy(lg, tgt[valid], reduction="none")
                if h == "coarse" and COARSE_SIGMA > 0:      # abstandsbewusst statt exakt
                    soft = coarse_soft_ce(lg, tgt[valid]).to(per.dtype)
                    per = (1.0 - COARSE_SOFT_MIX) * per + COARSE_SOFT_MIX * soft
                l = (per * wsub[valid]).sum() / wsub[valid].sum().clamp(min=1e-6)
                total = total + l
                logs[h] = l.item()

    # Wert-Kopf: ungewichtet (soll die wahre Win-Prob fuer ALLE Zustaende lernen)
    lv = F.mse_loss(out["value"], win)
    total = total + VALUE_W * lv
    logs["value"] = lv.item()
    return total, logs


def accuracy(out, labels, atypes):
    """Wie oft trifft der Kopf das Label — nur auf aktiven Beispielen, gemittelt."""
    masks = head_masks_for(atypes)
    accs = {"atype": (out["atype"].argmax(1) == labels["atype"]).float().mean().item()}
    for h in HEADS:
        if h == "atype":
            continue
        m = masks[h].to(out[h].device)
        if m.any():
            tgt = labels[h][m]
            v = tgt != IGNORE_INDEX
            if v.any():
                if h == "fine" and FINE_OFF:    # Vorhersage ist jetzt die Zellmitte
                    accs[h] = (tgt[v] == FINE_CENTER).float().mean().item()
                else:
                    accs[h] = (out[h][m][v].argmax(1) == tgt[v]).float().mean().item()
    return accs


def save_ckpt(net, opt, step):
    os.makedirs(os.path.dirname(CKPT), exist_ok=True)
    tmp = CKPT + ".tmp"
    torch.save({"model": net.state_dict(), "opt": opt.state_dict(), "step": step}, tmp)
    os.replace(tmp, CKPT)   # atomar — nie ein halber Checkpoint


def load_ckpt(net, opt):
    if not os.path.exists(CKPT):
        return 0
    c = torch.load(CKPT, map_location="cpu", weights_only=True)
    net.load_state_dict(c["model"])
    opt.load_state_dict(c["opt"])
    return c["step"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--n", type=int, default=256)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--ckpt-every", type=int, default=50)
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()

    if a.fresh and os.path.exists(CKPT):
        os.remove(CKPT)

    dev = a.device
    net = Net().to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=3e-4)
    start = load_ckpt(net, opt)
    if start:
        print(f"[resume] weiter bei Schritt {start}")

    data = make_synthetic(a.n, device=dev)
    t0 = time.time()
    for step in range(start, a.steps):
        out = net(data["map"], data["own"], data["opp"], data["mask"],
                  coarse_idx=data["labels"]["coarse"])   # Lehrer-Vorgabe für Fein-Kopf
        loss, logs = masked_loss(out, data["labels"], data["atypes"])
        opt.zero_grad(); loss.backward(); opt.step()
        if (step + 1) % a.ckpt_every == 0 or step + 1 == a.steps:
            save_ckpt(net, opt, step + 1)
        if step % 25 == 0 or step + 1 == a.steps:
            acc = accuracy(out, data["labels"], data["atypes"])
            key = sum(acc[h] for h in ("atype", "target", "coarse", "magnitude") if h in acc) / \
                  sum(1 for h in ("atype", "target", "coarse", "magnitude") if h in acc)
            print(f"Schritt {step+1:>4}  Verlust {loss.item():6.3f}  "
                  f"Kern-Köpfe-Treffer {key:5.1%}  ({time.time()-t0:4.0f}s)")
    print(f"fertig. Checkpoint: {CKPT}")


if __name__ == "__main__":
    main()
