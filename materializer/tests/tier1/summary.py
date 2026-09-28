#!/usr/bin/env python3
"""Auswertung der Harness-Läufe unter ~/mat-dev/t1out: Tabelle je Record und eine
vorsichtige Hochrechnung der Tier-1-Grösse auf den Pool (~/mat-dev/t1/pool_land.tsv).

Hochrechnung (SCHÄTZUNG): log(Bytes) = a + b·log(Züge) + c·log(Landkacheln) + d·log(Spieler),
kleinste Quadrate über die gemessenen Records, dann auf jede Pool-Partie angewendet.
Der Leave-one-out-Fehler zeigt, wie wenig 11 Punkte tragen.
"""
import csv
import glob
import json
import math
import os
import statistics as stt

O = os.path.expanduser("~/mat-dev/t1out")
POOL = os.path.expanduser("~/mat-dev/t1/pool_land.tsv")

pool = {r["gid"]: r for r in csv.DictReader(open(POOL), delimiter="\t")}
runs: dict[str, dict] = {}
for mode in ("full", "tier1", "replay"):
    for f in glob.glob(f"{O}/*/*/*.t1run.{mode}.json"):
        if "/brk/" in f:
            continue
        r = json.load(open(f))
        runs.setdefault(r["gid"], {})[mode] = r

print("gid      commit   Karte               WxH        Land     Züge  Sp | own MB units MB  MB/Partie  Wechsel    Terr.w.  Keys"
      " | sim ms  t1 ms  t1/sim | RSS replay tier1 MB | Hash ok/bad")
pts = []
for gid, m in sorted(runs.items(), key=lambda x: x[1]["full"]["turns"]):
    f = m["full"]
    t1 = m.get("tier1", f)
    rp = m.get("replay")
    s = f["tier1"]["stats"]
    mb = f["tier1"]["bytes"] / 1e6
    pl = int(pool[gid]["players"]) if gid in pool else 0
    sim, t1ms = t1["time_ms"]["sim"], t1["time_ms"]["tier1"]
    print(f"{gid} {f['commit'][:8]} {f['map'][:18]:18} {f['W']}x{f['H']:<5} {f['landTiles']:>8} {f['turns']:>6} {pl:>3} |"
          f" {f['tier1']['own']/1e6:6.2f} {f['tier1']['units']/1e6:8.3f} {mb:9.2f} {s['stateChanges']:>9} {s['terrainChanges']:>9}"
          f" {s['keyframes']:>4} | {sim:>6} {t1ms:>6} {100*t1ms/max(1,sim):5.1f}% |"
          f" {rp['rss_max_mb'] if rp else '-':>6} {t1['rss_max_mb']:>6} | {f['hash']['checked']}/{f['hash']['mismatch']}")
    if pl:
        pts.append((math.log(f["turns"]), math.log(f["landTiles"]), math.log(pl), math.log(f["tier1"]["bytes"])))

tot_mb = [r["full"]["tier1"]["bytes"] / 1e6 for r in runs.values()]
print(f"\nMittel über {len(tot_mb)} Records: {stt.mean(tot_mb):.2f} MB/Partie, Median {stt.median(tot_mb):.2f}")
t1s = [r["tier1"]["time_ms"]["tier1"] for r in runs.values() if "tier1" in r]
sims = [r["tier1"]["time_ms"]["sim"] for r in runs.values() if "tier1" in r]
if sims:
    print(f"Tier-1-Zeit gesamt {sum(t1s)} ms auf {sum(sims)} ms Sim = {100*sum(t1s)/sum(sims):.1f} %")


def solve(X, y):
    k = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(k)] + [sum(r[i] * v for r, v in zip(X, y))] for i in range(k)]
    for i in range(k):
        p = max(range(i, k), key=lambda q: abs(A[q][i]))
        A[i], A[p] = A[p], A[i]
        for q in range(k):
            if q != i:
                fct = A[q][i] / A[i][i]
                A[q] = [a - fct * b for a, b in zip(A[q], A[i])]
    return [A[i][k] / A[i][i] for i in range(k)]


if len(pts) >= 6:
    X = [[1, a, b, c] for a, b, c, _ in pts]
    y = [d for *_, d in pts]
    beta = solve(X, y)
    loo = []
    for i in range(len(pts)):
        bi = solve(X[:i] + X[i + 1:], y[:i] + y[i + 1:])
        loo.append(abs(sum(u * v for u, v in zip(bi, X[i])) - y[i]))
    print(f"\nRegression log(Bytes) = {beta[0]:.2f} + {beta[1]:.2f}·log(Züge) + {beta[2]:.2f}·log(Land) + {beta[3]:.2f}·log(Spieler)")
    print(f"Leave-one-out: Median Faktor {math.exp(stt.median(loo)):.2f}, schlechtester Faktor {math.exp(max(loo)):.2f}")
    pred = []
    for r in pool.values():
        t, la, p = int(r["turns"]), int(r["land"]), max(1, int(r["players"]))
        if t <= 0:
            continue
        pred.append(math.exp(beta[0] + beta[1] * math.log(t) + beta[2] * math.log(la) + beta[3] * math.log(p)) / 1e6)
    pred.sort()
    n = len(pred)
    print(f"SCHÄTZUNG Pool ({n} Records in records.tsv): Mittel {stt.mean(pred):.2f} MB/Partie, Median {pred[n//2]:.2f},"
          f" p90 {pred[int(0.9*n)]:.2f}, Summe {sum(pred)/1e3:.1f} GB")
    print(f"  hochgerechnet auf 18'018 Partien: {18018*stt.mean(pred)/1e3:.0f} GB"
          f" (Spanne mit LOO-Median-Faktor: {18018*stt.mean(pred)/1e3/math.exp(stt.median(loo)):.0f}"
          f"–{18018*stt.mean(pred)/1e3*math.exp(stt.median(loo)):.0f} GB)")
    ins = sum(1 for r in pool.values() if int(r["turns"]) <= 35561) / max(1, len(pool))
    print(f"  Anteil Pool-Partien im gemessenen Zugbereich (<= 35'561 Züge): {100*ins:.1f} %")
