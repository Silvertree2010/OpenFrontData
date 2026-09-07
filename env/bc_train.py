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


def masked_loss(out, labels, atypes):
    """Cross-Entropy je Kopf, nur auf den Beispielen, wo der Kopf aktiv ist.
    Wert-Kopf: MSE. atype ist immer aktiv."""
    masks = head_masks_for(atypes)
    total = out["atype"].new_zeros(())
    logs = {}
    # atype immer
    total = total + F.cross_entropy(out["atype"], labels["atype"])
    logs["atype"] = total.item()
    for h in HEADS:
        if h == "atype":
            continue
        m = masks[h].to(out[h].device)
        if m.any():
            l = F.cross_entropy(out[h][m], labels[h][m])
            total = total + l
            logs[h] = l.item()
    lv = F.mse_loss(out["value"], labels["value"])
    total = total + lv
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
            accs[h] = (out[h][m].argmax(1) == labels[h][m]).float().mean().item()
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
