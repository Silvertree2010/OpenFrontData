#!/usr/bin/env python3
"""Erzeugt metrics.jsonl + host.json exakt nach SCHEMA.md, fuer Board-Tests
ohne echtes Training. Keine Bibliotheken ausserhalb der Standardbibliothek."""
import argparse
import json
import math
import os
import random
import time

ATYPE_NAMES = [
    "NO_OP", "ATTACK", "BUILD_UNIT", "BOAT", "ALLIANCE_REQUEST", "SPAWN",
    "DONATE_TROOPS", "UPGRADE_STRUCTURE", "ALLIANCE_EXTENSION", "EMOJI",
    "MOVE_WARSHIP", "CANCEL_ATTACK", "BREAK_ALLIANCE", "TARGET_PLAYER",
    "QUICK_CHAT", "CANCEL_BOAT", "ALLIANCE_REJECT", "EMBARGO",
    "DONATE_GOLD", "EMBARGO_ALL", "DELETE_UNIT",
]
assert len(ATYPE_NAMES) == 21

HEADS = ["atype", "target", "coarse", "fine", "unit_type", "own_ref", "magnitude",
         "emoji", "quickchat", "embargo_start", "rocket_up", "amount"]
HEAD_SIZES = {"atype": 21, "target": 26, "coarse": 16200, "fine": 64, "unit_type": 10,
              "own_ref": 16, "magnitude": 8, "emoji": 64, "quickchat": 35,
              "embargo_start": 2, "rocket_up": 2, "amount": 51}
CORE_HEADS = ["atype", "target", "coarse", "magnitude"]

LAYERS = [
    {"id": "map_in", "name": "Karten-Eingang", "n": 18},
    {"id": "map_stem", "name": "Karten-CNN", "n": 128},
    {"id": "map_down", "name": "Karten-Tiefe", "n": 320},
    {"id": "opp", "name": "Gegner-Transformer", "n": 160},
    {"id": "own", "name": "Eigene Zahlen", "n": 128},
    {"id": "cfg", "name": "Spielmodus", "n": 32},
    {"id": "core1", "name": "Kern 1", "n": 768},
    {"id": "core2", "name": "Kern 2", "n": 768},
]
BYTES_PER_SAMPLE = 1166400

# schiefe label-Verteilung: ATTACK und BOAT dominieren
LABEL_WEIGHTS = [2, 30, 6, 22, 3, 1, 5, 4, 2, 3, 4, 3, 1, 6, 3, 2, 1, 2, 3, 1, 1]
assert len(LABEL_WEIGHTS) == 21


def r4(x):
    return round(x, 4)


def make_quantiles(rng, level, spread):
    """12 aufsteigend sortierte Quantile um `level` herum."""
    base = sorted(rng.random() for _ in range(12))
    return [r4(max(0.0, level + (b - 0.5) * 2 * spread)) for b in base]


def gen(out_dir, steps, log_every, epochs, finished, seed=7):
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    run = time.strftime("%Y%m%d-%H%M%S")
    t0 = time.time() - steps * 6.4
    metrics_path = os.path.join(out_dir, "metrics.jsonl")
    host_path = os.path.join(out_dir, "host.json")

    games_total = 12217
    games_val = 488
    games_train = games_total - games_val
    games_epoch = games_train
    batch = 128
    lr0 = 0.0003

    lines = []

    lines.append(json.dumps({
        "run": run, "t": round(t0, 1), "kind": "start",
        "label": "BC-AWR gesamter Shard-Satz", "device": "cuda",
        "epochs": epochs, "batch": batch, "lr": lr0,
        "games_total": games_total, "games_train": games_train, "games_val": games_val,
        "adv_beta": 1.5, "value_w": 1.0, "params": 3903000,
        "heads": HEADS, "head_sizes": HEAD_SIZES, "core_heads": CORE_HEADS,
        "atype_names": ATYPE_NAMES, "layers": LAYERS,
        "bytes_per_sample": BYTES_PER_SAMPLE,
    }))

    steps_per_epoch = max(1, steps // max(epochs, 1))
    samples = 0
    t = t0
    snap_every = max(1, steps_per_epoch // 3)

    # Layer-Aktivierungsniveaus, je Schicht unterschiedlich (fuer Netzanzeige-Test)
    layer_levels = {l["id"]: 0.15 + 0.09 * i for i, l in enumerate(LAYERS)}

    for gstep in range(1, steps + 1):
        ep = min(epochs - 1, (gstep - 1) // steps_per_epoch)
        step_in_epoch = (gstep - 1) % steps_per_epoch
        prog = gstep / steps  # 0..1 ueber den ganzen Lauf
        t += 6.4 + rng.uniform(-0.6, 0.6)
        samples = gstep * batch
        games_done = min(games_epoch, int((step_in_epoch + 1) / steps_per_epoch * games_epoch))

        if gstep % log_every != 0 and gstep != steps:
            continue

        loss = 5.2 * math.exp(-2.4 * prog) + 2.6 * (1 - math.exp(-2.4 * prog)) + rng.uniform(-0.08, 0.08)
        acc = 0.44 * (1 - math.exp(-3.0 * prog)) + rng.uniform(-0.01, 0.01)
        acc = max(0.02, min(0.9, acc))

        hl = {"atype": r4(loss * 0.35 + rng.uniform(0, 0.1)),
              "target": r4(loss * 0.28 + rng.uniform(0, 0.1)),
              "coarse": r4(loss * 1.6 + rng.uniform(0, 0.3)),
              "magnitude": r4(loss * 0.2 + rng.uniform(0, 0.05))}
        ha = {"atype": r4(acc + rng.uniform(-0.03, 0.03)),
              "target": r4(acc * 0.7 + rng.uniform(-0.03, 0.03)),
              "coarse": r4(acc * 0.4 + rng.uniform(-0.02, 0.02)),
              "magnitude": r4(acc * 0.6 + rng.uniform(-0.02, 0.02))}
        ha = {k: max(0.0, min(1.0, v)) for k, v in ha.items()}

        # Entropie sinkt langsam, htop steigt langsam -> Mode-Collapse sichtbar am Ende
        ent_lvl = 0.85 - 0.35 * prog
        top_lvl = 0.25 + 0.45 * prog
        he = {}
        hen = {}
        htop = {}
        hlg = {}
        for h in CORE_HEADS:
            K = HEAD_SIZES[h]
            en = max(0.05, min(0.98, ent_lvl + rng.uniform(-0.05, 0.05)))
            hen[h] = r4(en)
            he[h] = r4(en * math.log(max(K, 2)))
            htop[h] = r4(max(0.02, min(0.97, top_lvl + rng.uniform(-0.05, 0.05))))
            hlg[h] = r4(2.0 + 4.0 * prog + rng.uniform(-0.3, 0.3))

        # pred/lab Histogramme: lab folgt fester schiefer Verteilung mit Rauschen,
        # pred startet aehnlich und konzentriert sich zunehmend auf wenige Typen
        lab_hist = [0] * 21
        pred_hist = [0] * 21
        n_lab = 400
        wsum = sum(LABEL_WEIGHTS)
        for i, w in enumerate(LABEL_WEIGHTS):
            lab_hist[i] = int(n_lab * w / wsum + rng.uniform(-2, 2))
            lab_hist[i] = max(0, lab_hist[i])
        # pred: mische lab-Verteilung mit einer Kollaps-Spitze auf ATTACK (index 1)
        collapse = min(0.85, 0.15 + 0.6 * prog)
        for i in range(21):
            base = lab_hist[i] * (1 - collapse) / max(1, n_lab) * n_lab
            pred_hist[i] = int(base + rng.uniform(-1, 1))
        pred_hist[1] = pred_hist[1] + int(n_lab * collapse)
        pred_hist = [max(0, v) for v in pred_hist]

        adv_mean = 1.0 + rng.uniform(-0.05, 0.05)
        adv_p50 = r4(0.9 + 0.1 * math.sin(prog * 6) + rng.uniform(-0.05, 0.05))
        adv_p10 = r4(max(0.1, adv_p50 - 0.35 + rng.uniform(-0.05, 0.05)))
        adv_p90 = r4(adv_p50 + 0.6 + rng.uniform(-0.05, 0.05))
        adv_max = r4(adv_p90 + 1.2 + rng.uniform(0, 0.4))
        adv_clip = r4(max(0.0, min(0.2, 0.02 + 0.05 * prog + rng.uniform(-0.01, 0.01))))

        vmse = r4(max(0.02, 0.5 * math.exp(-2.0 * prog) + rng.uniform(-0.02, 0.02)))
        winfrac = r4(max(0.1, min(0.9, 0.48 + rng.uniform(-0.05, 0.05))))
        lr = round(max(1e-5, lr0 * (0.5 * (1 + math.cos(math.pi * prog)) * 0.9 + 0.1)), 6)
        gnorm = r4(max(0.1, 2.2 * math.exp(-1.2 * prog) + rng.uniform(-0.2, 0.2) + 0.3))
        sps = r4(max(50, 420 + rng.uniform(-40, 40)))
        dt = r4(log_every * batch / sps)

        act = {}
        for l in LAYERS:
            lvl = layer_levels[l["id"]] + 0.05 * math.sin(prog * 4 + hash(l["id"]) % 7)
            act[l["id"]] = {
                "m": r4(max(0.01, lvl)),
                "a": r4(max(0.05, min(0.95, 0.4 + 0.3 * math.sin(prog * 3 + len(l["id"])))))
                , "q": make_quantiles(rng, max(0.02, lvl), max(0.02, lvl * 0.6)),
            }
        hact = {h: r4(1.5 + 3.0 * prog + rng.uniform(-0.3, 0.3)) for h in HEADS}

        rec = {"run": run, "t": round(t, 1), "kind": "step",
               "ep": ep, "step": gstep, "samples": samples,
               "games_done": games_done, "games_epoch": games_epoch,
               "loss": r4(loss), "acc": r4(acc),
               "hl": hl, "ha": ha, "he": he, "hen": hen, "htop": htop, "hlg": hlg,
               "pred": pred_hist, "lab": lab_hist,
               "adv": {"mean": r4(adv_mean), "p10": adv_p10, "p50": adv_p50,
                       "p90": adv_p90, "max": adv_max, "clip": adv_clip},
               "vmse": vmse, "winfrac": winfrac,
               "lr": lr, "gnorm": gnorm,
               "sps": sps, "dt": dt,
               "act": act, "hact": hact}
        lines.append(json.dumps(rec))

        # Snapshot gelegentlich
        if gstep % snap_every == 0:
            lines.append(json.dumps({
                "run": run, "t": round(t, 1), "kind": "snap", "ep": ep, "step": gstep,
                "name": f"bc_real_s{gstep}.pt", "mb": r4(38 + rng.uniform(0, 12)),
            }))

        # Val am Ende jeder Epoche
        if step_in_epoch == steps_per_epoch - 1:
            vloss = r4(loss + 0.15 + rng.uniform(0, 0.1))
            vacc = r4(max(0.0, acc - 0.03 - rng.uniform(0, 0.03)))
            vha = {h: r4(max(0.0, ha.get(h, 0.3) - 0.03)) for h in CORE_HEADS}
            lines.append(json.dumps({
                "run": run, "t": round(t, 1), "kind": "val", "ep": ep, "step": gstep,
                "vloss": vloss, "vacc": vacc, "vha": vha,
                "batches": 60, "secs": r4(30 + rng.uniform(0, 20)),
            }))

    if finished:
        lines.append(json.dumps({
            "run": run, "t": round(t, 1), "kind": "end",
            "ep": epochs - 1, "step": steps, "samples": samples,
            "secs": round(t - t0, 1),
        }))

    with open(metrics_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    host = {
        "t": round(t, 1),
        "gpu": {"name": "NVIDIA GeForce RTX 5080", "util": rng.randint(85, 99),
                "mem_used": rng.randint(9000, 11000), "mem_total": 16303,
                "temp": rng.randint(62, 74), "power": rng.randint(260, 380), "power_cap": 400},
        "load": [round(rng.uniform(1.5, 3.0), 2), round(rng.uniform(1.4, 2.8), 2),
                 round(rng.uniform(1.2, 2.5), 2)],
        "cores": 16,
        "ram_used_gb": round(rng.uniform(16, 22), 1), "ram_total_gb": 64.0,
        "disk_free_gb": round(rng.uniform(380, 430), 1),
        "train_alive": not finished,
    }
    with open(host_path, "w") as f:
        json.dump(host, f)

    print(f"geschrieben: {metrics_path} ({len(lines)} Zeilen), {host_path}")


def gen_empty(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    # bewusst keine metrics.jsonl / host.json -> Server soll state="wartet" liefern
    print(f"leeres Verzeichnis angelegt: {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=900)
    ap.add_argument("--log-every", type=int, default=50)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--finished", action="store_true")
    ap.add_argument("--empty", action="store_true")
    args = ap.parse_args()

    if args.empty:
        gen_empty(args.out)
        return
    gen(args.out, args.steps, args.log_every, args.epochs, args.finished)


if __name__ == "__main__":
    main()
