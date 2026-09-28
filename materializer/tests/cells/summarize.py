#!/usr/bin/env python3
"""Fasst Harness-Ergebnisse (~/mat-dev/cells-out/*.json) zu Tabellen zusammen. Nur Tests.
   summarize.py <modus> [tag] [dateien...]   (ohne Dateien: alle <modus>_*_<tag>.json)
"""
import csv, glob, json, os, statistics, sys

OUT = os.path.expanduser("~/mat-dev/cells-out")
mode = sys.argv[1]
tag = sys.argv[2] if len(sys.argv) > 2 else "a"
files = sys.argv[3:] or sorted(glob.glob(f"{OUT}/{mode}_*_{tag}.json"))


def load(f):
    """Ergebniszeile ist die (letzte) Zeile, die mit {"mode": beginnt."""
    try:
        lines = [l for l in open(f) if l.startswith('{"mode":')]
        return json.loads(lines[-1])
    except Exception as e:
        print(f"# {os.path.basename(f)}: kein Ergebnis ({e!r})")
        return None


def old_seconds():
    """Gesamtlaufzeit alter thin-Lauf je gid (Mittel ueber Wiederholungen)."""
    res = {}
    p = os.path.expanduser("~/mat-dev/ref/results.tsv")
    for r in csv.DictReader(open(p), delimiter="\t"):
        if r["mode"] == "thin" and r["rc"] == "0":
            res.setdefault(r["gameID"], []).append(float(r["seconds"]))
    return {k: statistics.mean(v) for k, v in res.items()}


rows = [d for d in map(load, files) if d]
if mode == "compare":
    print("gid commit map mode WxH pairs alive spawnph bad | mism b0..b7 frac owner | set-pairs b0..b7 | spot(mism) | hash ok/bad/rec | ms cells/ref")
    for d in rows:
        c = d["compare"]
        print(d["gid"], d["commit8"], d["map"].replace(" ", "_"), d["gameMode"].replace(" ", ""), f'{d["W"]}x{d["H"]}',
              c["pairs"], c["pairsAlive"], c["pairsSpawnPhase"], c["pairsBad"], "|",
              " ".join(map(str, c["bitMism"])), c["fracMism"], c["ownerMism"], "|",
              " ".join(map(str, c["pairsWithBit"])), "|", f'{d["refStats"]["spot"]}({d["refStats"]["spotMismatch"]})', "|",
              f'{d["hash"]["ok"]}/{d["hash"]["bad"]}/{d["hash"]["recordHashes"]}', "|",
              c["msCells"]["median"], c["msRef"]["median"], "impl=" + d["impl"])
        for e in c["examples"][:3]:
            print("   bsp", e)
elif mode == "hash":
    print("gid commit compute computes hash_ok hash_bad record_hashes missing first_bad sim_s")
    for d in rows:
        h = d["hash"]
        print(d["gid"], d["commit8"], d["withCompute"], d["computes"], h["ok"], h["bad"], h["recordHashes"], h["missing"],
              h["first"], round(d["sim_ms"] / 1000, 1))
elif mode == "mask":
    for d in rows:
        print(f'== {d["gid"]} {d["commit8"]} bit1={d["bit1"]} hash {d["hash"]["ok"]}/{d["hash"]["bad"]}')
        print("   typ bit n klick% | gueltig klick%|gueltig res%|gueltig res_andere_zelle")
        for k, b in list(d["mask"].items()) + [("#" + g, b) for g, b in d.get("maskGroups", {}).items()]:
            n, v = b["n"], b["valid"]
            f = lambda a, m: f"{100*a/m:.1f}" if m else "-"
            print(f'   {k:14s} {b["bit"]} {n:5d} {f(b["clickBit"], n):>6s} | {v:5d} {f(b["clickBitValid"], v):>6s} '
                  f'{f(b["resBitValid"], b["resKnown"]):>6s} {b["resOtherCell"]}')
elif mode == "cost":
    old = old_seconds()
    print("gid commit bit1 pairs calls ms_med ms_p95 ms_max sum_s old_s anteil% zstd_s kb_mean kb_med ctor_ms rebuilds")
    for d in rows:
        c = d["cost"]
        o = old.get(d["gid"], float("nan"))
        s = c["ms_sum"] / 1000
        print(d["gid"], d["commit8"], int(d["bit1"]), c["pairs"], c["calls"], c["ms_median"], c["ms_p95"], c["ms_max"],
              round(s, 2), round(o, 1), round(100 * s / o, 2), round(c["zstd_ms_sum"] / 1000, 2), c["kb_mean"], c["kb_median"],
              d["ctor_ms"], d["cellStats"])
