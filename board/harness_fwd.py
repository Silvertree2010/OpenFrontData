"""
Pruef-Harness fuer die Trainer-Instrumentierung.

Beweist, dass ActivationProbe / head_stats / adv_stats / grad_norm / MetricsLog
zusammenspielen und misst ihren Mehraufwand - OHNE env/bc_fit.py zu starten
und OHNE einen Optimizer-Schritt. Schreibt NICHTS nach checkpoints/ oder data/.

  python board/harness_fwd.py --source synth --n 8 --runs 3
  python board/harness_fwd.py --source shards --shards data/shards --n 8 --runs 3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(os.path.dirname(HERE), "env")
sys.path.insert(0, ENV)

import torch

import actions as AC
from net import Net
from bc_train import make_synthetic, masked_loss
import metrics as MET
import trainstats as TS

# Feldliste aus SCHEMA.md Abschnitt 1.2 (kind:"step") - gemeinsame Felder + step-Felder.
SCHEMA_STEP_FIELDS = [
    "run", "t", "kind", "ep", "step", "samples", "games_done", "games_epoch",
    "loss", "acc", "hl", "ha", "he", "hen", "htop", "hlg", "pred", "lab",
    "adv", "vmse", "winfrac", "lr", "gnorm", "sps", "dt", "act", "hact",
]


def synth_batch(n, device, seed=1):
    return make_synthetic(n, seed=seed, device=device)


def shards_batch(shard_dir, n, device):
    """Echte Batches ueber bc_fit.batch_stream (nur fuer den Trainingsrechner
    mit echten Shards - liest, schreibt nichts)."""
    import bc_fit as BF
    games = BF.list_games(shard_dir)
    if not games:
        raise SystemExit(f"[harness] keine fertigen Spiele in {shard_dir}")
    rng = random.Random(0)
    gen = BF.batch_stream(shard_dir, games, n, shuffle_buf=64, rng=rng)
    b = next(gen)
    return BF.to_dev(b, device)


def forward(net, batch, use_config):
    kwargs = {}
    if use_config:
        kwargs["config_t"] = batch["config"]
    return net(batch["map"], batch["own"], batch["opp"], batch["mask"],
               coarse_idx=batch["labels"]["coarse"].clamp(min=0), **kwargs)


def find_nan_inf(obj, path=""):
    """Rekursiv nach NaN/Inf suchen, gibt Liste betroffener Pfade zurueck."""
    bad = []
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            bad.append(path or "<wert>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            bad += find_nan_inf(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad += find_nan_inf(v, f"{path}[{i}]")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["synth", "shards"], default="synth")
    ap.add_argument("--shards", default="data/shards")
    ap.add_argument("--n", type=int, default=8, help="Batchgroesse")
    ap.add_argument("--runs", type=int, default=5, help="Wiederholungen fuer Timing/Stats")
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()

    dev = a.device
    torch.manual_seed(0)
    net = Net().to(dev)
    net.eval()

    if a.source == "synth":
        batch = synth_batch(a.n, dev)
        use_config = False
    else:
        batch = shards_batch(a.shards, a.n, dev)
        use_config = True

    print(f"[harness] Quelle={a.source}  Batch={a.n}  Wiederholungen={a.runs}  Geraet={dev}")

    # --- Zeit ohne Probe (Baseline), Median ueber --runs Wiederholungen ---
    times_plain = []
    with torch.no_grad():
        for _ in range(a.runs):
            t0 = time.perf_counter()
            forward(net, batch, use_config)
            times_plain.append(time.perf_counter() - t0)

    # --- Zeit MIT armierter Probe, Median ueber --runs Wiederholungen ---
    probe = TS.ActivationProbe(net)
    times_probed = []
    out = act = None
    with torch.no_grad():
        for _ in range(a.runs):
            t0 = time.perf_counter()
            probe.arm(opp_mask=batch["mask"])
            probe.note_map_in(batch["map"])
            out = forward(net, batch, use_config)
            act = probe.collect()
            times_probed.append(time.perf_counter() - t0)
    probe.remove()

    med_plain = statistics.median(times_plain)
    med_probed = statistics.median(times_probed)
    overhead_pct = (med_probed - med_plain) / med_plain * 100 if med_plain > 0 else float("nan")

    # --- head_stats / adv_stats (kein Grad noetig) ---
    with torch.no_grad():
        he, hen, htop, hlg, pred_hist, lab_hist = TS.head_stats(out, batch["labels"], batch["atypes"])
        adv = TS.adv_stats(batch["labels"]["value"])

    # --- grad_norm: eigenes Wegwerf-Netz, EIN backward(), KEIN opt.step() ---
    net2 = Net().to(dev)
    out2 = forward(net2, batch, use_config)
    loss2, hl2 = masked_loss(out2, batch["labels"], batch["atypes"])
    loss2.backward()
    gnorm = TS.grad_norm(net2.parameters())

    # --- echte JSONL-Zeile ueber MetricsLog (push=False, eigener Pfad) ---
    log_path = os.path.join(os.path.dirname(HERE), "logs", "harness_metrics.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)
    ml = MET.MetricsLog(path=log_path, label="HARNESS-TEST", device=dev, push=False)
    ml.event("start", label="HARNESS-TEST", device=dev, epochs=1, batch=a.n, lr=3e-4,
              games_total=0, games_train=0, games_val=0,
              adv_beta=TS.ADV_BETA, value_w=0.0,
              params=int(sum(p.numel() for p in net.parameters())),
              heads=list(AC.HEAD_SIZES.keys()), head_sizes=AC.HEAD_SIZES,
              core_heads=["atype", "target", "coarse", "magnitude"],
              atype_names=[x.name for x in AC.A],
              layers=[{"id": lid, "name": lid, "n": 0} for lid, _ in TS.ActivationProbe.LAYERS],
              bytes_per_sample=0)
    winfrac = float(batch["labels"]["value"].float().mean().item())
    ml.log(1, float(loss2.item()), acc=0.0, ep=0, samples=a.n, games_done=0, games_epoch=0,
           hl=hl2, ha={}, he=he, hen=hen, htop=htop, hlg=hlg, pred=pred_hist, lab=lab_hist,
           adv=adv, vmse=hl2.get("value"), winfrac=winfrac, lr=3e-4, gnorm=gnorm,
           sps=0.0, dt=med_probed, act=act, hact=hlg)
    ml.close()

    # --- Zeile zurueck einlesen und pruefen ---
    with open(log_path) as f:
        lines = [l for l in f if l.strip()]
    step_line = json.loads(lines[-1])
    line_bytes = len(lines[-1].encode("utf-8"))
    missing = [f for f in SCHEMA_STEP_FIELDS if f not in step_line]
    bad_vals = find_nan_inf(step_line)

    print()
    print(f"[harness] Median Vorwaerts ohne Probe:  {med_plain*1000:8.2f} ms")
    print(f"[harness] Median Vorwaerts mit Probe:   {med_probed*1000:8.2f} ms")
    print(f"[harness] Mehraufwand durch die Probe:  {overhead_pct:7.1f} %  "
          f"(fällt nur auf Log-Schritten an)")
    print(f"[harness] Bytes je JSONL-step-Zeile:    {line_bytes}")
    if missing:
        print(f"[harness] FEHLENDE Felder aus Schema 1.2: {missing}")
    else:
        print("[harness] alle Felder aus Schema 1.2 vorhanden")
    if bad_vals:
        print(f"[harness] NaN/Inf gefunden bei: {bad_vals}")
    else:
        print("[harness] keine NaN/Inf-Werte gefunden")
    print(f"[harness] JSONL-Datei: {log_path}")


if __name__ == "__main__":
    main()
