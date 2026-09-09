"""
Auswertung der raeumlichen Koepfe (coarse/fine) eines BC-Checkpoints.

Frage: trifft der Kachel-Zeiger daneben, weil er nichts kann, oder weil er
raeumlich unpraezise ist? Antwort = Abstandsverteilung, nicht nur Trefferquote.

Gemessen wird ausschliesslich auf dem VALIDIERUNGS-Anteil, exakt demselben
Split wie in bc_fit.py (dieselbe Spieleliste, derselbe Seed, dieselbe
Aufteilung) - also auf Partien, die das Netz nie gesehen hat.

  A  Grob-Kopf allein          argmax der 90x180-Hitzekarte vs. echte Grobzelle
  B  Zusammengesetzt           (grob,fein) -> Kachel im 1440x720-Gitter
       B1  fein bedingt auf die VORHERGESAGTE Grobzelle  (Einsatzpfad)
       B2  fein bedingt auf die ECHTE Grobzelle          (Fehler nur aus Stufe 2)
  C  Vergleichsmassstaebe      Zufall, Haeufigkeitsprior, eigenes Gebiet

Abstand ist immer euklidisch im Gitter, nie eine Indexdifferenz.
Eine Grobzelle ist 8x8 Gitterkacheln gross, deshalb Grobabstand x8 = Kachelabstand.

Ressourcen: laeuft absichtlich auf CPU mit wenigen Threads, damit ein
gleichzeitig laufendes Training nicht ausgebremst wird. Es wird NICHTS
geschrieben ausser der --out-JSON. Der Checkpoint wird nur gelesen (vorher
extern in eine temporaere Kopie legen, falls der Trainer ihn ueberschreibt).

  OMP_NUM_THREADS=4 nice -n 15 .venv/bin/python env/eval_spatial.py \
      --ckpt /tmp/bc_big_snapshot.pt --n 4000 --device cpu
"""
from __future__ import annotations

import argparse
import json
import os
import random
import struct
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import torch
from compression import zstd

import actions as AC
import dataset as D
import featurize as F
import bc_fit as BF                      # nur list_games/split_games/collate -> identischer Split
from net import Net, GH, GW              # GH,GW = 90,180 (Grobraster)
from bc_train import IGNORE_INDEX

TILE_HEADS = ("coarse", "fine")
C_OWN = 2                                # obs.ts: Kanal 2 = "eigen"
FW, FH = AC.FINE_W, AC.FINE_H            # 8, 8


# ────────────────────────────────────────────────── Datenstrom (kartensparsam)
def _labels_of(s):
    """Label-Dict wie dataset.load_game es baut - ohne die Karte anzufassen."""
    ctx = AC.Context(map_w=s["mapW"], map_h=s["mapH"], troops=s["troops"], gold=s["gold"],
                     opp_ids=s["oppIds"], own_unit_ids=s["ownUnitIds"],
                     own_attack_ids=s["ownAttackIds"])
    act = AC.encode(s["intent"], ctx)
    label = {"atype": int(act.atype)}
    for h in AC.HEAD_SCHEMA.get(AC.A(act.atype), ()):
        v = getattr(act, h)
        if v is not None and v >= 0:
            label[h] = int(v)
    return int(act.atype), label


def game_samples(shard_dir, gid, records_dirs, want, per_game, rng, only=None):
    """Bis zu per_game zufaellig gezogene Samples EINES Spiels, bei denen alle
    Koepfe in `want` ein gueltiges Label haben.

    Zwei Durchgaenge ueber dieselbe Datei: erst Labels (billig, Meta-Zeilen),
    dann nur fuer die gezogenen Indizes den Kartenblock dekomprimieren. Die
    Karte ist 291600 Byte je Sample - sie fuer verworfene Samples auszupacken
    waere der teuerste Teil des Laufs.
    Reihenfolge und Feldbedeutung sind identisch zu dataset.load_game.
    """
    meta_path = os.path.join(shard_dir, f"{gid}.meta.zst")
    maps_path = os.path.join(shard_dir, f"{gid}.maps")
    cfg_vec = D.config_vec_for(gid, records_dirs)
    lines = zstd.decompress(open(meta_path, "rb").read()).decode().split("\n")

    parsed = {}                      # index -> (dict, label)
    for i, line in enumerate(lines):
        if not line:
            continue
        s = json.loads(line)
        atype, label = _labels_of(s)
        if all(h in label for h in want) and (only is None or atype in only):
            parsed[i] = (s, label)
    if not parsed:
        return []
    idxs = sorted(parsed)
    if per_game and len(idxs) > per_game:
        idxs = sorted(rng.sample(idxs, per_game))
    keep = set(idxs)

    out = []
    with open(maps_path, "rb") as mf:
        for i, line in enumerate(lines):
            if not line:
                continue
            ln = struct.unpack("<I", mf.read(4))[0]
            if i not in keep:
                mf.seek(ln, os.SEEK_CUR)          # Block ueberspringen, nicht auspacken
                continue
            block = zstd.decompress(mf.read(ln))
            u8 = np.frombuffer(block, dtype=np.uint8).copy()
            assert u8.size == D.MAPLEN, f"{u8.size} != {D.MAPLEN}"
            s, label = parsed[i]
            opps = s["opps"]
            for o in opps:
                o["_rep"] = D._rep_for(o)
            opp_mat, mask = F.featurize_opps(opps)
            out.append({"map_u8": u8,
                        "own": np.array(F.featurize_own(s["own"]), np.float32),
                        "opp": np.array(opp_mat, np.float32),
                        "mask": np.array(mask, bool),
                        "label": label, "atype": label["atype"],
                        "config": cfg_vec, "win": float(s.get("win", 0)),
                        "gid": gid, "idx": i})
    return out


def label_counts(shard_dir, gid, head):
    """Nur Labels zaehlen, Karten werden komplett uebersprungen (fuer den Prior)."""
    meta_path = os.path.join(shard_dir, f"{gid}.meta.zst")
    c = Counter()
    for line in zstd.decompress(open(meta_path, "rb").read()).decode().split("\n"):
        if not line:
            continue
        _, label = _labels_of(json.loads(line))
        if head in label:
            c[label[head]] += 1
    return c


# ────────────────────────────────────────────────── Selbsttest des Lesers
def selfcheck_reader(shard_dir, gid, records_dirs):
    """Beweist, dass der kartensparsame Leser dieselben Samples liefert wie
    dataset.load_game. Ohne diesen Test waere jede Zahl unten unbelegt."""
    ref = []
    for i, s in enumerate(D.load_game(shard_dir, gid, raw=True, records_dirs=records_dirs)):
        if all(h in s["label"] for h in TILE_HEADS):
            ref.append((i, s["atype"], s["label"].get("coarse"), s["label"].get("fine"),
                        hash(s["map_u8"].tobytes()), hash(s["own"].tobytes())))
    mine = []
    for s in game_samples(shard_dir, gid, records_dirs, TILE_HEADS, 0, random.Random(0)):
        mine.append((s["idx"], s["atype"], s["label"].get("coarse"), s["label"].get("fine"),
                     hash(s["map_u8"].tobytes()), hash(s["own"].tobytes())))
    # dataset.load_game zaehlt nur nicht-leere Zeilen, der eigene Leser den Zeilenindex
    ok = len(ref) == len(mine) and all(a[1:] == b[1:] for a, b in zip(ref, mine))
    return ok, len(ref), len(mine)


# ────────────────────────────────────────────────── Kennzahlen
def grid_xy(coarse, fine):
    """(Grobzelle, Feinzelle) -> (gx, gy) im 1440x720-Gitter. Umkehrung von
    actions.tile_encode, ohne Kartenmasse."""
    cx, cy = coarse % AC.COARSE_W, coarse // AC.COARSE_W
    fx, fy = fine % FW, fine // FW
    return cx * FW + fx, cy * FH + fy


def coarse_xy(coarse):
    return coarse % AC.COARSE_W, coarse // AC.COARSE_W


def stats(d, thresholds):
    """Die sechs Kennzahlen zu einem Abstandsvektor."""
    d = np.asarray(d, np.float64)
    if d.size == 0:
        return {"n": 0}
    return {"n": int(d.size),
            "exact": float((d == 0).mean()),
            "within": {str(t): float((d <= t).mean()) for t in thresholds},
            "mean": float(d.mean()),
            "median": float(np.median(d))}


HIST_LABELS = ["0", "1", "2", "3-4", "5-8", "9-16", "17-32", ">32"]
HIST_EDGES = [0, 1, 2, 4, 8, 16, 32]


def hist(d):
    d = np.asarray(d, np.float64)
    out = []
    prev = -1.0
    for e in HIST_EDGES:
        out.append(int(((d > prev) & (d <= e)).sum()))
        prev = e
    out.append(int((d > HIST_EDGES[-1]).sum()))
    n = max(1, d.size)
    return {"counts": dict(zip(HIST_LABELS, out)),
            "frac": {k: v / n for k, v in zip(HIST_LABELS, out)}}


def coarse_dist(pred, true_c):
    px, py = coarse_xy(np.asarray(pred))
    tx, ty = coarse_xy(np.asarray(true_c))
    return np.hypot(px - tx, py - ty)


def block(dc, label):
    """Ein Grob-Ergebnis in beiden Einheiten (Grobzellen und Kacheln)."""
    return {"label": label,
            "coarse_cells": stats(dc, (1, 2, 4)),
            "tiles": stats(dc * FW, (1, 2, 4, 8, 16, 32)),
            "hist_tiles": hist(dc * FW)}


# ────────────────────────────────────────────────── Hauptlauf
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="checkpoints/bc_big.pt")
    ap.add_argument("--n", type=int, default=4000, help="gueltige Samples (Kachel-Koepfe)")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out", default=None, help="JSON-Pfad (Standard: logs/eval_spatial_<step>.json)")
    ap.add_argument("--shards", default="data/pool_shards")
    ap.add_argument("--records-dir", nargs="*", default=["data/pool_records"])
    ap.add_argument("--reputation", default="data/reputation.json")
    ap.add_argument("--val-frac", type=float, default=0.04)
    ap.add_argument("--seed", type=int, default=1, help="MUSS dem Trainings-Seed entsprechen")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--per-game", type=int, default=12, help="max. Samples je Val-Spiel (0 = alle)")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--prior-games", type=int, default=150, help="Trainingsspiele fuer den Haeufigkeitsprior")
    ap.add_argument("--only-atype", default=None,
                    help="Komma-Liste von Aktionstypen (z.B. SPAWN) - fuer seltene Typen")
    ap.add_argument("--no-selfcheck", action="store_true")
    ap.add_argument("--force-gpu", action="store_true")
    a = ap.parse_args()

    torch.set_num_threads(max(1, a.threads))
    torch.set_grad_enabled(False)
    dev = a.device
    if dev.startswith("cuda"):
        free, total = torch.cuda.mem_get_info()
        print(f"[gpu] frei {free/2**20:.0f} MiB von {total/2**20:.0f} MiB")
        if free < 2 * 2**30 and not a.force_gpu:
            sys.exit("[abbruch] zu wenig freier VRAM neben dem Training (--force-gpu ueberstimmt)")

    only = None
    if a.only_atype:
        only = {int(AC.A[x.strip().upper()]) for x in a.only_atype.split(",")}
        print(f"[filter] nur Aktionstypen: {sorted(AC.A(x).name for x in only)}", flush=True)

    D.load_reputation(a.reputation)
    all_games = BF.list_games(a.shards)
    train_g, val_g = BF.split_games(all_games, a.val_frac, a.seed)
    print(f"[daten] {len(all_games)} Spiele: {len(train_g)} train / {len(val_g)} val "
          f"(Split identisch zu bc_fit.py, seed={a.seed})", flush=True)

    # --- Netz ---
    ck = torch.load(a.ckpt, map_location="cpu", weights_only=True)
    step = int(ck.get("gstep", 0)); epoch = int(ck.get("epoch", 0))
    net = Net().to(dev)
    net.load_state_dict(ck["model"])
    # ACHTUNG, bewusst train()-Modus: das Netz enthaelt weder Dropout noch
    # BatchNorm (GroupNorm/Linear/Conv), Modus aendert also nichts an den Zahlen
    # (wird unten je Lauf nachgemessen: selfcheck_eval_vs_train_maxdiff).
    # eval() aktiviert aber den Nested-Tensor-Schnellpfad des Gegner-
    # Transformers, und der stuerzt bei Samples OHNE gueltigen Gegner ab
    # ("to_padded_tensor: at least one constituent tensor should have non-zero
    # numel"). Solche Samples gibt es real (~1%). Der Trainer rechnet sie im
    # train()-Modus, also rechnen wir sie genauso.
    net.train()
    torch.set_grad_enabled(False)
    print(f"[netz] {a.ckpt}  Epoche {epoch}  Schritt {step}  Geraet {dev}", flush=True)

    cap = {}
    net.map.register_forward_hook(lambda m, i, o: cap.__setitem__("spatial", o[0]))
    net.core.register_forward_hook(lambda m, i, o: cap.__setitem__("core", o))

    # --- Leser-Selbsttest ---
    check = None
    if not a.no_selfcheck:
        # Ein Spiel OHNE Kachel-Samples wuerde den Test bestehen, ohne etwas zu
        # pruefen -> nur Spiele mit mindestens einem Sample zaehlen als Test.
        for gid in val_g[:40]:
            try:
                ok, nref, nmine = selfcheck_reader(a.shards, gid, a.records_dir)
            except Exception as e:
                print(f"[selbsttest] {gid} uebersprungen: {e}", flush=True)
                continue
            if nref == 0 and ok:
                continue
            check = {"gid": gid, "ok": bool(ok), "n_ref": nref, "n_mine": nmine}
            print(f"[selbsttest] Leser vs. dataset.load_game auf {gid}: "
                  f"{'IDENTISCH' if ok else 'ABWEICHUNG'} ({nref} Kachel-Samples)", flush=True)
            break
        if check is None:
            sys.exit("[abbruch] kein Val-Spiel mit Kachel-Samples fuer den Selbsttest gefunden")
        if not check["ok"]:
            sys.exit("[abbruch] Leser weicht von dataset.load_game ab")

    # --- Sammeln + Vorwaerts ---
    rec = {k: [] for k in ("atype", "c_true", "f_true", "c_pred", "f_pred_p", "f_pred_t",
                           "own_cent", "own_max", "c_rank")}
    fine_hook_maxdiff = 0.0
    eval_vs_train = {"maxdiff": None, "checked": False}
    n_no_opp = 0
    rng = random.Random(0)
    games_used = 0
    t0 = time.time()
    buf = []
    done = False

    def flush(buf):
        nonlocal fine_hook_maxdiff, n_no_opp
        b = BF.collate(buf)
        b = BF.to_dev(b, dev)
        lab_c = b["labels"]["coarse"]
        lab_f = b["labels"]["fine"]
        assert (lab_c != IGNORE_INDEX).all() and (lab_f != IGNORE_INDEX).all(), \
            "gefiltert wurde falsch: maskiertes Kachel-Label im Batch"
        n_no_opp += int((~b["mask"].any(1)).sum().item())
        if not eval_vs_train["checked"] and bool(b["mask"].any(1).all()):
            # Beweis, dass train() vs. eval() hier dieselben Zahlen liefert.
            # Nur auf einem Batch OHNE leere Gegnermaske moeglich, weil eval()
            # bei leerer Maske abstuerzt - genau der Grund fuer train().
            net.eval()
            o_e = net(b["map"], b["own"], b["opp"], b["mask"], config_t=b["config"], coarse_idx=None)
            net.train()
            o_t = net(b["map"], b["own"], b["opp"], b["mask"], config_t=b["config"], coarse_idx=None)
            eval_vs_train["maxdiff"] = float((o_e["coarse"] - o_t["coarse"]).abs().max().item())
            eval_vs_train["checked"] = True
        out = net(b["map"], b["own"], b["opp"], b["mask"], config_t=b["config"], coarse_idx=None)
        spatial, core = cap["spatial"], cap["core"]
        B = lab_c.shape[0]
        ar = torch.arange(B, device=spatial.device)

        def fine_for(idx):
            cy = (idx // GW).clamp(0, GH - 1)
            cx = (idx % GW).clamp(0, GW - 1)
            feat = spatial[ar, :, cy, cx]
            return net.fine_head(torch.cat([core, feat], dim=1))

        c_pred = out["coarse"].argmax(1)
        # Rang der ECHTEN Zelle in der Hitzekarte: 0 = getroffen, 16199 = schlechteste.
        # Trennt "Kopf weiss nichts" (Rang ~ 8100) von "Kopf ist knapp daneben".
        lg = out["coarse"]
        c_rank = (lg > lg.gather(1, lab_c[:, None])).sum(1)
        f_pred_p = fine_for(c_pred)
        # Beweis, dass der Hook-Pfad exakt der Pfad aus net.forward ist:
        fine_hook_maxdiff = max(fine_hook_maxdiff,
                                float((f_pred_p - out["fine"]).abs().max().item()))
        f_pred_t = fine_for(lab_c)

        own = b["map"][:, C_OWN].clamp(min=0)                  # (B,90,180)
        tot = own.sum((1, 2))
        wx = own.sum(1); wy = own.sum(2)
        ax = torch.arange(GW, dtype=own.dtype, device=own.device)
        ay = torch.arange(GH, dtype=own.dtype, device=own.device)
        cx = (wx * ax).sum(1) / tot.clamp(min=1e-9)
        cy = (wy * ay).sum(1) / tot.clamp(min=1e-9)
        cent = (cy.round().clamp(0, GH - 1).long() * GW + cx.round().clamp(0, GW - 1).long())
        amax = own.flatten(1).argmax(1)
        empty = tot <= 1e-6
        cent = torch.where(empty, torch.full_like(cent, -1), cent)
        amax = torch.where(empty, torch.full_like(amax, -1), amax)

        rec["atype"] += b["atypes"].tolist()
        rec["c_true"] += lab_c.tolist()
        rec["f_true"] += lab_f.tolist()
        rec["c_pred"] += c_pred.tolist()
        rec["f_pred_p"] += f_pred_p.argmax(1).tolist()
        rec["f_pred_t"] += f_pred_t.argmax(1).tolist()
        rec["own_cent"] += cent.tolist()
        rec["own_max"] += amax.tolist()
        rec["c_rank"] += c_rank.tolist()

    for gid in val_g:
        try:
            got = game_samples(a.shards, gid, a.records_dir, TILE_HEADS, a.per_game, rng, only)
        except Exception as e:
            print(f"[warn] Spiel {gid} uebersprungen: {e}", flush=True)
            continue
        games_used += 1
        for s in got:
            buf.append(s)
            if len(buf) == a.batch:
                flush(buf); buf = []
                if len(rec["atype"]) >= a.n:
                    done = True; break
        if done:
            break
        if games_used % 25 == 0:
            print(f"[fortschritt] {games_used} Spiele, {len(rec['atype'])} Samples, "
                  f"{len(rec['atype'])/max(1e-9, time.time()-t0):.1f} Smp/s", flush=True)
    if buf and not done:
        flush(buf)
    secs = time.time() - t0
    N = len(rec["atype"])
    if N == 0:
        sys.exit("[abbruch] keine gueltigen Kachel-Samples gefunden")
    arr = {k: np.array(v) for k, v in rec.items()}
    print(f"[gemessen] {N} Samples aus {games_used} Val-Spielen in {secs/60:.1f} min "
          f"({N/secs:.1f} Smp/s)", flush=True)
    print(f"[selbsttest] Fein-Kopf ueber Hook vs. net.forward: max. Abweichung "
          f"{fine_hook_maxdiff:.2e} (0 = derselbe Pfad)", flush=True)
    print(f"[selbsttest] train() vs. eval(): max. Abweichung der Grob-Logits "
          f"{eval_vs_train['maxdiff']} (0 = Modus egal)", flush=True)
    print(f"[hinweis] {n_no_opp} von {N} Samples haben keinen gueltigen Gegner "
          f"({n_no_opp/N:.2%}) - sie stuerzen in eval() ab, siehe Kommentar im Kopf", flush=True)

    # --- A: Grob-Kopf ---
    dA = coarse_dist(arr["c_pred"], arr["c_true"])
    res = {"meta": {"ckpt": os.path.abspath(a.ckpt), "step": step, "epoch": epoch,
                    "only_atype": a.only_atype,
                    "device": dev, "n": N, "games_used": games_used,
                    "per_game": a.per_game, "secs": secs,
                    "val_games": len(val_g), "train_games": len(train_g),
                    "seed": a.seed, "val_frac": a.val_frac, "shards": a.shards,
                    "selfcheck_reader": check,
                    "selfcheck_fine_hook_maxdiff": fine_hook_maxdiff,
                    "selfcheck_eval_vs_train_maxdiff": eval_vs_train["maxdiff"],
                    "samples_without_opponent": n_no_opp},
           "A_coarse": block(dA, "A Grob-Kopf allein")}

    ranks = arr["c_rank"]
    pc = Counter(arr["c_pred"].tolist())
    res["A_coarse"]["rank"] = {
        "topk": {str(k): float((ranks < k).mean()) for k in (1, 5, 25, 100, 1000)},
        "median": float(np.median(ranks)), "mean": float(ranks.mean()),
        "cells_total": AC.NUM_COARSE,
        "distinct_pred_cells": len(pc),
        "top_pred_cell": int(pc.most_common(1)[0][0]),
        "top_pred_share": pc.most_common(1)[0][1] / N,
    }

    # --- B: zusammengesetzt ---
    gxt, gyt = grid_xy(arr["c_true"], arr["f_true"])
    gxp, gyp = grid_xy(arr["c_pred"], arr["f_pred_p"])
    gxt2, gyt2 = grid_xy(arr["c_true"], arr["f_pred_t"])
    dB1 = np.hypot(gxp - gxt, gyp - gyt)
    dB2 = np.hypot(gxt2 - gxt, gyt2 - gyt)
    res["B1_composite_pred_coarse"] = {"label": "B1 grob(vorhergesagt) + fein|grob_vorhergesagt",
                                       "tiles": stats(dB1, (1, 2, 4, 8, 16, 32)),
                                       "hist_tiles": hist(dB1)}
    res["B2_composite_true_coarse"] = {"label": "B2 grob(echt) + fein|grob_echt (nur Stufe 2)",
                                       "tiles": stats(dB2, (1, 2, 4, 8, 16, 32)),
                                       "hist_tiles": hist(dB2)}

    # --- C5/C6: Massstaebe fuer den FEIN-Kopf (immer in der ECHTEN Grobzelle,
    #     also direkt mit B2 vergleichbar) ---
    rr = np.random.default_rng(12345)
    fr = rr.integers(0, AC.NUM_FINE, size=N)
    gxr, gyr = grid_xy(arr["c_true"], fr)
    dF1 = np.hypot(gxr - gxt, gyr - gyt)
    gxc, gyc = grid_xy(arr["c_true"], np.full(N, (FH // 2) * FW + FW // 2))
    dF2 = np.hypot(gxc - gxt, gyc - gyt)
    res["C5_fine_random"] = {"label": "C5 grob(echt) + zufaellige Feinzelle",
                             "tiles": stats(dF1, (1, 2, 4, 8, 16, 32)), "hist_tiles": hist(dF1)}
    res["C6_fine_center"] = {"label": "C6 grob(echt) + Mitte der Grobzelle",
                             "tiles": stats(dF2, (1, 2, 4, 8, 16, 32)), "hist_tiles": hist(dF2)}

    # --- C: Vergleichsmassstaebe (Grobebene) ---
    dR = coarse_dist(rr.integers(0, AC.NUM_COARSE, size=N), arr["c_true"])
    res["C1_random"] = block(dR, "C1 Zufall, gleichverteilt ueber 16200 Grobzellen")

    prior_counter = Counter()
    prior_games = 0
    t1 = time.time()
    for gid in train_g[:a.prior_games]:
        try:
            prior_counter += label_counts(a.shards, gid, "coarse")
            prior_games += 1
        except Exception:
            continue
    if prior_counter:
        top_cell, top_n = prior_counter.most_common(1)[0]
        dP = coarse_dist(np.full(N, top_cell), arr["c_true"])
        res["C2_prior"] = block(dP, "C2 haeufigste Grobzelle im Trainingsanteil")
        res["C2_prior"]["cell"] = int(top_cell)
        res["C2_prior"]["cell_xy"] = [int(top_cell % AC.COARSE_W), int(top_cell // AC.COARSE_W)]
        res["C2_prior"]["share_in_train"] = top_n / max(1, sum(prior_counter.values()))
        res["C2_prior"]["games_scanned"] = prior_games
        res["C2_prior"]["labels_scanned"] = int(sum(prior_counter.values()))
        res["C2_prior"]["secs"] = time.time() - t1

    for key, name, src in (("C3_own_centroid", "C3 Schwerpunkt des eigenen Gebiets", "own_cent"),
                           ("C4_own_argmax", "C4 staerkste eigene Kachel", "own_max")):
        m = arr[src] >= 0
        if m.sum():
            d = coarse_dist(arr[src][m], arr["c_true"][m])
            res[key] = block(d, name)
            res[key]["coverage"] = float(m.mean())

    # --- Aufschluesselung nach Aktionstyp ---
    per = {}
    for at in sorted(set(arr["atype"].tolist())):
        m = arr["atype"] == at
        per[AC.A(at).name] = {"n": int(m.sum()),
                              "A_coarse": block(dA[m], "A"),
                              "B1_tiles": {"tiles": stats(dB1[m], (1, 2, 4, 8, 16, 32)),
                                           "hist_tiles": hist(dB1[m])},
                              "B2_tiles": {"tiles": stats(dB2[m], (1, 2, 4, 8, 16, 32))}}
    res["per_atype"] = per

    res["samples"] = {k: arr[k].tolist() for k in
                      ("atype", "c_true", "f_true", "c_pred", "f_pred_p", "f_pred_t",
                       "own_cent", "own_max", "c_rank")}

    out_path = a.out or os.path.join("logs", f"eval_spatial_{step}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(res, f, indent=1)

    # ────────────────────────── Konsolentabelle
    def row(name, st, unit):
        w = st.get("within", {})
        keys = list(w.keys())
        cells = "  ".join(f"{w[k]:6.1%}" for k in keys)
        print(f"  {name:<44s} {st['n']:>6d} {st['exact']:7.2%}  {cells}  "
              f"{st['mean']:8.1f} {st['median']:8.1f}  [{unit}]")

    print()
    print("=" * 118)
    print(f"RAEUMLICHE KOEPFE - {os.path.basename(a.ckpt)}  Epoche {epoch}, Schritt {step}  "
          f"| {N} Val-Samples aus {games_used} Spielen")
    print("=" * 118)
    print(f"  {'':<44s} {'n':>6s} {'exakt':>7s}  " +
          "  ".join(f"{'<=' + str(t):>6s}" for t in (1, 2, 4)) +
          f"  {'Mittel':>8s} {'Median':>8s}")
    print("-- A) Grob-Kopf allein, Abstand in GROBZELLEN " + "-" * 60)
    row("A Grob-Kopf", res["A_coarse"]["coarse_cells"], "Grobzellen")
    for k in ("C1_random", "C2_prior", "C3_own_centroid", "C4_own_argmax"):
        if k in res:
            row(res[k]["label"], res[k]["coarse_cells"], "Grobzellen")
    print()
    print(f"  {'':<44s} {'n':>6s} {'exakt':>7s}  " +
          "  ".join(f"{'<=' + str(t):>6s}" for t in (1, 2, 4, 8, 16, 32)) +
          f"  {'Mittel':>8s} {'Median':>8s}")
    print("-- A/B/C in GITTERKACHELN (1440x720; Grobzelle = 8 Kacheln) " + "-" * 45)
    row("A Grob-Kopf (Zellmitte)", res["A_coarse"]["tiles"], "Kacheln")
    row(res["B1_composite_pred_coarse"]["label"], res["B1_composite_pred_coarse"]["tiles"], "Kacheln")
    row(res["B2_composite_true_coarse"]["label"], res["B2_composite_true_coarse"]["tiles"], "Kacheln")
    row(res["C5_fine_random"]["label"], res["C5_fine_random"]["tiles"], "Kacheln")
    row(res["C6_fine_center"]["label"], res["C6_fine_center"]["tiles"], "Kacheln")
    for k in ("C1_random", "C2_prior", "C3_own_centroid", "C4_own_argmax"):
        if k in res:
            row(res[k]["label"], res[k]["tiles"], "Kacheln")
    r = res["A_coarse"]["rank"]
    print()
    print("-- Grob-Kopf: Rang der ECHTEN Zelle in der Hitzekarte (0 = Treffer, "
          f"{AC.NUM_COARSE} Zellen) " + "-" * 20)
    print("   Top-k-Treffer:  " + "  ".join(f"k={k}: {v:6.2%}" for k, v in r["topk"].items()))
    print(f"   Rang Median {r['median']:.0f}, Mittel {r['mean']:.0f}   |   "
          f"verschiedene vorhergesagte Zellen: {r['distinct_pred_cells']} von {N} Samples, "
          f"haeufigste Vorhersage {r['top_pred_share']:.1%}")
    print()
    print("-- Abstandsklassen in Kacheln (Anteil) " + "-" * 68)
    print(f"  {'':<44s} " + "  ".join(f"{h:>7s}" for h in HIST_LABELS))
    for nm, k in (("A Grob-Kopf", "A_coarse"), ("B1 zusammengesetzt", "B1_composite_pred_coarse"),
                  ("B2 nur Fein-Kopf", "B2_composite_true_coarse"),
                  ("C5 zufaellige Feinzelle", "C5_fine_random"),
                  ("C1 Zufall", "C1_random"), ("C2 Prior", "C2_prior"),
                  ("C3 Gebietsschwerpunkt", "C3_own_centroid")):
        if k in res:
            h = res[k]["hist_tiles"]["frac"]
            print(f"  {nm:<44s} " + "  ".join(f"{h[x]:6.1%}" for x in HIST_LABELS))
    print()
    print("-- nach Aktionstyp " + "-" * 88)
    print(f"  {'Typ':<16s} {'n':>6s} | A exakt  A<=1Z  A Median | B1 exakt  B1<=8K  B1 Median | B2 exakt B2 Median")
    for name, p in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
        A, B1, B2 = p["A_coarse"]["coarse_cells"], p["B1_tiles"]["tiles"], p["B2_tiles"]["tiles"]
        print(f"  {name:<16s} {p['n']:>6d} | {A['exact']:7.2%} {A['within']['1']:6.1%} "
              f"{A['median']:9.1f} | {B1['exact']:8.2%} {B1['within']['8']:7.1%} "
              f"{B1['median']:10.1f} | {B2['exact']:8.2%} {B2['median']:9.1f}")
    print()
    print(f"JSON: {out_path}")


if __name__ == "__main__":
    main()
