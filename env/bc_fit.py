"""
Echter Behavior-Cloning-Trainer über den GESAMTEN Shard-Satz.

Warum eigenständig (nicht bc_train): bc_train überfittet einen festen Satz im RAM
(synthetisch / ein Spiel). Hier streamen wir hunderttausende Samples aus vielen
Spielen, die nie zusammen in den RAM passen (~200 GB Karten). Deshalb:

  • Streaming-Shuffle über Spiele: ein Buffer mischt Samples spielübergreifend,
    ohne je alles zu laden (Karten bleiben uint8 → 4× kleiner, ~0.3 MB statt 1.2).
  • Train/Val-Split je SPIEL (nicht je Sample): Val misst Generalisierung auf
    ungesehene Partien, nicht auf ungesehene Ticks derselben Partie.
  • Prefetch-Thread: zstd+featurize (CPU) überlappt mit dem GPU-Schritt, sonst
    verhungert die 5080 an der Python-Dekompression.
  • Atomarer Checkpoint + Resume (Epoche+Schritt): Arch läuft nur sporadisch,
    ein Abschalten darf nie Fortschritt verlieren.

  python env/bc_fit.py --shards data/shards --device cuda --epochs 3 --batch 256
"""
from __future__ import annotations
import os, sys, glob, time, random, argparse, threading, queue
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(__file__))
import actions as AC
import dataset as D
from net import Net, NUM_MAP_CH, GH, GW, EMB, CORE
from bc_train import masked_loss, accuracy, IGNORE_INDEX, ADV_BETA, VALUE_W

try:                                  # Instrumentierung ist optional: fehlt sie
    import metrics as MET             # oder bricht sie, laeuft das Training trotzdem.
    import trainstats as TS
    HAVE_METRICS = True
except ImportError:
    MET = TS = None
    HAVE_METRICS = False

HEADS = list(AC.HEAD_SIZES.keys())
CORE_HEADS = ("atype", "target", "coarse", "magnitude")


# ---------------------------------------------------------------- Daten-Stream
def list_games(shard_dir):
    """Fertige Spiele = vorhandenes .meta.zst (der Materializer schreibt es ganz
    zuletzt; ein abgebrochenes Spiel hat keins). Deterministisch sortiert."""
    return sorted(os.path.basename(p)[:-9] for p in glob.glob(os.path.join(shard_dir, "*.meta.zst")))


def split_games(gids, val_frac, seed):
    """Stabiler Train/Val-Split je Spiel (seed-fest → über Resumes identisch)."""
    g = list(gids)
    random.Random(seed).shuffle(g)
    nval = max(1, int(len(g) * val_frac)) if len(g) > 1 else 0
    return g[nval:], g[:nval]


def sample_stream(shard_dir, gids, shuffle_buf, rng, records_dirs=None, progress=None):
    """Samples spielübergreifend gemischt (Reservoir-artiger Shuffle-Buffer).
    progress: optionales dict, "games_done" wird nach jedem fertigen Spiel erhoeht."""
    buf = []
    for gid in gids:
        try:
            for s in D.load_game(shard_dir, gid, raw=True, records_dirs=records_dirs):
                if len(buf) < shuffle_buf:
                    buf.append(s)
                else:
                    j = rng.randrange(shuffle_buf)
                    yield buf[j]; buf[j] = s
        except Exception as e:            # ein kaputtes Spiel darf den Lauf nicht kippen
            print(f"[warn] Spiel {gid} übersprungen: {e}", flush=True)
        if progress is not None:
            progress["games_done"] = progress.get("games_done", 0) + 1
    rng.shuffle(buf)
    yield from buf


def collate(samples):
    """Liste Sample-dicts → CPU-Tensoren. Karte hier dequantisiert (uint8→float).
    Labels: je Kopf ein voller Batch-Vektor (IGNORE wo unaufgelöst → im Loss ignoriert)."""
    B = len(samples)
    mp = np.stack([D.dequantize(s["map_u8"]) for s in samples])
    own = np.stack([s["own"] for s in samples])
    opp = np.stack([s["opp"] for s in samples])
    mask = np.stack([s["mask"] for s in samples])
    atypes = np.array([s["atype"] for s in samples], np.int64)
    cfg = np.stack([s["config"] for s in samples])   # (B, CONFIG_DIM) Spiel-Modifier
    # Klassen-Köpfe auf IGNORE vorbelegen: ein Kopf, der zwar zum Aktionstyp gehört,
    # aber kein aufgelöstes Label hat, wird so ignoriert statt auf Klasse 0 gelernt.
    labels = {h: np.full(B, IGNORE_INDEX, np.int64) for h in HEADS}
    labels["atype"] = np.zeros(B, np.int64)     # atype ist immer aufgelöst
    for i, s in enumerate(samples):
        for h, v in s["label"].items():
            if h in labels:
                labels[h][i] = v
    value = np.array([s["win"] for s in samples], np.float32)
    out = dict(map=torch.from_numpy(mp), own=torch.from_numpy(own),
               opp=torch.from_numpy(opp), mask=torch.from_numpy(mask),
               atypes=torch.from_numpy(atypes), config=torch.from_numpy(cfg),
               labels={h: torch.from_numpy(v) for h, v in labels.items()})
    out["labels"]["value"] = torch.from_numpy(value)
    return out


def batch_stream(shard_dir, gids, batch, shuffle_buf, rng, records_dirs=None, progress=None):
    """Fasst den Sample-Stream zu collateten CPU-Batches zusammen."""
    cur = []
    for s in sample_stream(shard_dir, gids, shuffle_buf, rng, records_dirs, progress=progress):
        cur.append(s)
        if len(cur) == batch:
            yield collate(cur); cur = []
    if cur:
        yield collate(cur)


def prefetched(gen, depth=4):
    """Hintergrund-Thread füllt eine Queue → CPU-Dekode überlappt GPU-Schritt."""
    q: queue.Queue = queue.Queue(maxsize=depth)
    STOP = object()
    def worker():
        try:
            for b in gen:
                q.put(b)
        finally:
            q.put(STOP)
    threading.Thread(target=worker, daemon=True).start()
    while True:
        b = q.get()
        if b is STOP:
            break
        yield b


def to_dev(b, dev):
    b["map"] = b["map"].to(dev, non_blocking=True)
    b["own"] = b["own"].to(dev, non_blocking=True)
    b["opp"] = b["opp"].to(dev, non_blocking=True)
    b["mask"] = b["mask"].to(dev, non_blocking=True)
    b["atypes"] = b["atypes"].to(dev, non_blocking=True)
    b["config"] = b["config"].to(dev, non_blocking=True)
    b["labels"] = {h: t.to(dev, non_blocking=True) for h, t in b["labels"].items()}
    return b


# ---------------------------------------------------------------- Checkpoint
def save_ckpt(path, net, opt, epoch, gstep):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    torch.save({"model": net.state_dict(), "opt": opt.state_dict(),
                "epoch": epoch, "gstep": gstep}, tmp)
    os.replace(tmp, path)     # atomar — nie ein halber Checkpoint


def load_ckpt(path, net, opt, dev):
    if not os.path.exists(path):
        return 0, 0
    c = torch.load(path, map_location=dev, weights_only=True)
    net.load_state_dict(c["model"]); opt.load_state_dict(c["opt"])
    return c.get("epoch", 0), c.get("gstep", 0)


# ---------------------------------------------------------------- Val
@torch.no_grad()
def evaluate(net, shard_dir, gids, batch, dev, amp, records_dirs=None, max_batches=60):
    """Rueckgabe: dict mit vloss/vacc/vha (Treffer je Kopf, gemittelt)/batches/secs,
    oder None, wenn keine Val-Spiele vorhanden sind."""
    if not gids:
        return None
    t0 = time.time()
    net.eval()
    rng = random.Random(0)
    losses, accs, nb = [], [], 0
    head_acc_sum, head_acc_n = {}, {}
    for b in batch_stream(shard_dir, gids, batch, shuffle_buf=1, rng=rng, records_dirs=records_dirs):
        b = to_dev(b, dev)
        with torch.autocast(device_type=dev.split(":")[0], dtype=torch.bfloat16, enabled=amp):
            out = net(b["map"], b["own"], b["opp"], b["mask"], config_t=b["config"], coarse_idx=b["labels"]["coarse"].clamp(min=0))
            loss, _ = masked_loss(out, b["labels"], b["atypes"], weighted=False)
        acc = accuracy(out, b["labels"], b["atypes"])
        losses.append(loss.item())
        accs.append(sum(acc[h] for h in CORE_HEADS if h in acc) /
                    max(1, sum(1 for h in CORE_HEADS if h in acc)))
        for h, v in acc.items():
            head_acc_sum[h] = head_acc_sum.get(h, 0.0) + v
            head_acc_n[h] = head_acc_n.get(h, 0) + 1
        nb += 1
        if nb >= max_batches:
            break
    net.train()
    vha = {h: head_acc_sum[h] / head_acc_n[h] for h in head_acc_sum}
    return {"vloss": float(np.mean(losses)), "vacc": float(np.mean(accs)),
            "vha": vha, "batches": nb, "secs": time.time() - t0}


# ---------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", default="data/shards")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--shuffle-buf", type=int, default=8192)
    ap.add_argument("--val-frac", type=float, default=0.04)
    ap.add_argument("--ckpt", default="checkpoints/bc_real.pt")
    ap.add_argument("--ckpt-every", type=int, default=500)
    ap.add_argument("--snapshot-every", type=int, default=20000,
                    help="behaltene Step-Snapshots bc_x_s<step>.pt (nie ueberschrieben, 0=aus)")
    ap.add_argument("--log-every", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-games", type=int, default=0, help="nur die ersten N Spiele (schneller Test)")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--reputation", default="data/reputation.json")
    ap.add_argument("--records-dir", nargs="*", default=["data/night_raw"],
                    help="Record-Dirs fuer Config/Modifier-Lookup (gameID->config)")
    ap.add_argument("--no-metrics", action="store_true", help="Live-Board-Instrumentierung abschalten")
    ap.add_argument("--metrics-path", default="logs/metrics.jsonl")
    a = ap.parse_args()

    records_dirs = a.records_dir
    D.load_reputation(a.reputation)
    dev = a.device
    amp = dev.startswith("cuda")
    if amp:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    all_games = list_games(a.shards)
    if a.max_games:
        all_games = all_games[:a.max_games]
    train_g, val_g = split_games(all_games, a.val_frac, a.seed)
    print(f"[daten] {len(all_games)} Spiele: {len(train_g)} train / {len(val_g)} val  "
          f"(Reputation: {len(D.REPUTATION)} Spieler)", flush=True)
    if not train_g:
        print("[abbruch] keine fertigen Shards gefunden"); return

    if a.fresh and os.path.exists(a.ckpt):
        os.remove(a.ckpt)
    net = Net().to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr)
    start_epoch, gstep = load_ckpt(a.ckpt, net, opt, dev)
    if gstep:
        print(f"[resume] weiter ab Epoche {start_epoch}, Schritt {gstep}", flush=True)
    net.train()

    # ---------------------------------------------------- Instrumentierung (optional)
    metrics_on = HAVE_METRICS and not a.no_metrics
    ml = probe = hostprobe = None
    progress = {"games_done": 0}
    total_samples = 0
    run_t0 = time.time()
    if metrics_on:
        try:
            params = sum(p.numel() for p in net.parameters())
            layers = [
                {"id": "map_in", "name": "Karten-Eingang", "n": NUM_MAP_CH},
                {"id": "map_stem", "name": "Karten-CNN", "n": 128},
                {"id": "map_down", "name": "Karten-Tiefe", "n": 320},
                {"id": "opp", "name": "Gegner-Transformer", "n": EMB},
                {"id": "own", "name": "Eigene Zahlen", "n": 128},
                {"id": "cfg", "name": "Spielmodus", "n": 32},
                {"id": "core1", "name": "Kern 1", "n": CORE},
                {"id": "core2", "name": "Kern 2", "n": CORE},
            ]
            ml = MET.MetricsLog(path=a.metrics_path, label=os.path.basename(a.shards),
                                 device=dev, steps_total=None)
            ml.event("start", label=os.path.basename(a.shards), device=dev,
                      epochs=a.epochs, batch=a.batch, lr=a.lr,
                      games_total=len(all_games), games_train=len(train_g),
                      games_val=len(val_g), adv_beta=ADV_BETA, value_w=VALUE_W,
                      params=int(params), heads=HEADS, head_sizes=AC.HEAD_SIZES,
                      core_heads=list(CORE_HEADS), atype_names=[x.name for x in AC.A],
                      layers=layers, bytes_per_sample=NUM_MAP_CH * GH * GW * 4)
            ml.push_now()
            probe = TS.ActivationProbe(net)
            hostprobe = TS.HostProbe(
                path=os.path.join(os.path.dirname(a.metrics_path) or "logs", "host.json"),
                project_dir=os.path.dirname(os.path.abspath(a.metrics_path)) or ".").start()
        except Exception as e:
            print(f"[warn] Metrik-Instrumentierung deaktiviert (Start): {e}", flush=True)
            metrics_on = False
            ml = probe = hostprobe = None

    for epoch in range(start_epoch, a.epochs):
        rng = random.Random(a.seed * 1000 + epoch)      # andere Reihenfolge je Epoche
        order = list(train_g); rng.shuffle(order)
        t0 = time.time(); seen = 0; run_loss = 0.0; run_n = 0
        last_log_t = t0
        progress["games_done"] = 0
        gen = prefetched(batch_stream(a.shards, order, a.batch, a.shuffle_buf, rng, records_dirs,
                                       progress=progress if metrics_on else None))
        for b in gen:
            b = to_dev(b, dev)
            will_log = metrics_on and probe is not None and (gstep + 1) % a.log_every == 0
            if will_log:
                try:
                    probe.arm(opp_mask=b["mask"])
                    probe.note_map_in(b["map"])
                except Exception:
                    pass
            with torch.autocast(device_type=dev.split(":")[0], dtype=torch.bfloat16, enabled=amp):
                out = net(b["map"], b["own"], b["opp"], b["mask"], config_t=b["config"], coarse_idx=b["labels"]["coarse"].clamp(min=0))
                loss, hl = masked_loss(out, b["labels"], b["atypes"])
            opt.zero_grad(set_to_none=True); loss.backward()
            gnorm = None
            if will_log:
                try:
                    gnorm = TS.grad_norm(net.parameters())
                except Exception:
                    gnorm = None
            opt.step()
            gstep += 1; seen += b["atypes"].size(0); run_loss += loss.item(); run_n += 1
            total_samples += b["atypes"].size(0)

            if gstep % a.log_every == 0:
                acc = accuracy(out, b["labels"], b["atypes"])
                key = sum(acc[h] for h in CORE_HEADS if h in acc) / \
                      max(1, sum(1 for h in CORE_HEADS if h in acc))
                sps = seen / max(1e-9, time.time() - t0)
                print(f"E{epoch} S{gstep:>7}  Verlust {run_loss/run_n:6.3f}  "
                      f"Kern {key:5.1%}  {sps:5.0f} Smp/s", flush=True)
                if metrics_on and ml is not None:
                    try:
                        now = time.time()
                        he, hen, htop, hlg, pred_hist, lab_hist = TS.head_stats(
                            out, b["labels"], b["atypes"])
                        adv = TS.adv_stats(b["labels"]["value"])
                        act = probe.collect() if probe is not None else {}
                        winfrac = float(b["labels"]["value"].float().mean().item())
                        ml.log(gstep, run_loss / run_n, acc=key,
                               ep=epoch, samples=total_samples,
                               games_done=progress.get("games_done", 0),
                               games_epoch=len(train_g),
                               hl=hl, ha=acc, he=he, hen=hen, htop=htop, hlg=hlg,
                               pred=pred_hist, lab=lab_hist, adv=adv,
                               vmse=hl.get("value"), winfrac=winfrac,
                               lr=opt.param_groups[0]["lr"], gnorm=gnorm,
                               sps=sps, dt=now - last_log_t,
                               act=act, hact=hlg)
                        last_log_t = now
                    except Exception as e:
                        print(f"[warn] Metrik-Instrumentierung deaktiviert (step): {e}", flush=True)
                        metrics_on = False
                run_loss = 0.0; run_n = 0
            if gstep % a.ckpt_every == 0:
                save_ckpt(a.ckpt, net, opt, epoch, gstep)
            if a.snapshot_every and gstep % a.snapshot_every == 0:
                snap = a.ckpt[:-3] + f"_s{gstep}.pt" if a.ckpt.endswith(".pt") else a.ckpt + f"_s{gstep}"
                save_ckpt(snap, net, opt, epoch, gstep)   # behaltener Step-Snapshot
                print(f"[snapshot] {snap}", flush=True)
                if metrics_on and ml is not None:
                    try:
                        ml.event("snap", ep=epoch, step=gstep, name=os.path.basename(snap),
                                  mb=os.path.getsize(snap) / 1024 / 1024)
                        ml.push_now()
                    except Exception:
                        pass

        save_ckpt(a.ckpt, net, opt, epoch + 1, gstep)    # Epoche fertig (rollend)
        ep_ckpt = a.ckpt[:-3] + f"_e{epoch}.pt" if a.ckpt.endswith(".pt") else a.ckpt + f"_e{epoch}"
        save_ckpt(ep_ckpt, net, opt, epoch + 1, gstep)   # versionierter Snapshot (nie ueberschrieben)
        print(f"[snapshot] {ep_ckpt}", flush=True)
        if metrics_on and ml is not None:
            try:
                ml.event("snap", ep=epoch, step=gstep, name=os.path.basename(ep_ckpt),
                          mb=os.path.getsize(ep_ckpt) / 1024 / 1024)
                ml.push_now()
            except Exception:
                pass
        ev = evaluate(net, a.shards, val_g, a.batch, dev, amp, records_dirs=records_dirs)
        vs = f"Val-Verlust {ev['vloss']:.3f}  Val-Kern {ev['vacc']:.1%}" if ev else "kein Val"
        print(f"[epoche {epoch} fertig] {seen} Samples in {(time.time()-t0)/60:.1f} min  {vs}", flush=True)
        if metrics_on and ml is not None and ev:
            try:
                ml.event("val", ep=epoch, step=gstep, vloss=ev["vloss"], vacc=ev["vacc"],
                          vha=ev["vha"], batches=ev["batches"], secs=ev["secs"])
                ml.push_now()
            except Exception:
                pass

    print(f"fertig. Checkpoint: {a.ckpt}", flush=True)

    if metrics_on and ml is not None:
        try:
            ml.event("end", ep=a.epochs - 1, step=gstep, samples=total_samples,
                      secs=time.time() - run_t0)
        except Exception:
            pass
        try:
            probe.remove()
        except Exception:
            pass
        try:
            hostprobe.stop()
        except Exception:
            pass
        try:
            ml.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
