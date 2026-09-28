"""Integration: Kern + echtes tier1.ts/cells.ts (+ py/tier1.py). Läuft auf arch.

    python3 run_int_tests.py [pairs] [cut]      # ohne Argument: beides

Layout: ~/mat-dev/w/<sha8>/mat-int (Kern + echte Hooks). Ausgabe ~/mat-dev/out/int/.
Records: die 9 Referenz-Records plus AgmJxf2p (Water-Nukes, 88cc95d8).

pairs  je Record ein Paar "alles" (CELLS=1 TIER1_PCT=100) und "ohne" (CELLS=0 TIER1_PCT=0),
       direkt nacheinander, Reihenfolge je Record abwechselnd, 3 Paare parallel. So laufen
       beide Hälften eines Paars bei gleicher Last. Prüft:
         1 .maps und hash.digest identisch, 0 Desyncs, 0 Tick-Fehler, 0 Fehler
         2 python py/tier1.py verify = Exit 0
         3 reader check grün (enthält: .cells-Blöcke == Zeilen mit cell ≥ 0)
         4 Maskentor (DESIGN §9 Tor 3): legal-Bit des Typs an der Zelle von res_tile,
           nur res_kind 0, je Typ und je res_dt; gesamt < 1 %
         5 CPU-Aufschlag cpu_ms (user+system) alles gegen ohne, je Record und gesamt
cut    Schnitt über den echten Pfad: ein Record-Hash wird so verfälscht, dass mindestens
       ein aufgelöstes res-Fenster über last_ok reicht und ein .chk-Tick danach liegt.
       Prüft res_kind 2 für die Betroffenen, valid_until in .ok und .chk, verify grün mit
       chkDropped > 0, .cells als Byte-Präfix des vollen Laufs.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "py"))
sys.path.insert(0, HERE)
import reader  # noqa: E402
import run_core_tests as rc  # noqa: E402

OUT = os.path.join(rc.DEV, "out", "int")
VARIANT = "mat-int"
GIDS = [g for gs in rc.REFS.values() for g in gs] + ["AgmJxf2p"]
ALL = {"CELLS": "1", "TIER1_PCT": "100"}
NONE = {"CELLS": "0", "TIER1_PCT": "0"}
TIER1_PY = os.path.join(HERE, "..", "..", "py", "tier1.py")
R: dict = {}


def verify(d: str, gid: str):
    p = subprocess.run(["nice", "-n", "10", "python3", TIER1_PY, "verify", d, gid], capture_output=True, text=True, timeout=1800)
    return p.returncode, (p.stdout + p.stderr).strip()[-300:]


def bit_of(m: dict, lb1: bool) -> int:
    t = rc.rtype(m)
    if t == "Port":
        return 2 if lb1 else 0
    if t in rc.STRUCT:
        return 1 if lb1 else 0
    return {"boat": 3, "Warship": 4, "move_warship": 5, "spawn": 6}.get(t, 7 if t in rc.NUKES else -1)


def mask(d: str, gid: str) -> dict:
    """Maskentor: je Typ und res_dt (n, Verletzungen)."""
    g = reader.Game(d, gid)
    W, H = g.hdr["W"], g.hdr["H"]
    lb1 = bool(g.ok.get("legal_bit1"))
    legal = [c[2] for c in g.cells()]
    out: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for m in g.meta():
        if m.get("res_kind") != 0 or m.get("cell", -1) < 0 or m.get("res_tile", -1) < 0:
            continue
        b = bit_of(m, lb1)
        if b < 0:
            continue
        rt = m["res_tile"]
        ci = ((rt // W) * 90 // H) * 180 + ((rt % W) * 180 // W)
        s = out[rc.rtype(m)][m["res_dt"]]
        s[0] += 1
        if not (legal[m["cell"]][ci] >> b) & 1:
            s[1] += 1
    return {t: dict(v) for t, v in out.items()}


def pair(i: int, gid: str):
    order = ["all", "none"] if i % 2 == 0 else ["none", "all"]
    res = {}
    for mode in order:
        res[mode] = rc.run_mat(gid, os.path.join(OUT, mode), env=ALL if mode == "all" else NONE, variant=VARIANT)
    return gid, res, order


def step_pairs():
    print("== pairs", flush=True)
    for m in ("all", "none"):
        shutil.rmtree(os.path.join(OUT, m), ignore_errors=True)
    gids = sorted(GIDS, key=lambda g: -os.path.getsize(rc.REC[g][0]))
    runs = {}
    with cf.ThreadPoolExecutor(3) as ex:
        for gid, res, order in ex.map(lambda a: pair(*a), enumerate(gids)):
            runs[gid] = res
            print(f"  {gid} ({'/'.join(order)}): " + ", ".join(f"{m} rc {r['rc']} {r['wall']} s" for m, r in res.items()), flush=True)
    da, dn = os.path.join(OUT, "all"), os.path.join(OUT, "none")
    cpu = {}
    masks = {}
    for gid in GIDS:
        ra, rn = runs[gid]["all"], runs[gid]["none"]
        if not rc.check(f"{gid} beide Läufe rc 0", ra["rc"] == 0 and rn["rc"] == 0, (ra["err"][-300:], rn["err"][-300:])):
            continue
        oa, on = rc.ok_of(da, gid), rc.ok_of(dn, gid)
        rc.check(f"{gid} .maps identisch", rc.sha(os.path.join(da, f"{gid}.maps")) == rc.sha(os.path.join(dn, f"{gid}.maps")))
        rc.check(f"{gid} hash.digest identisch", oa["hash"]["digest"] == on["hash"]["digest"] and oa["hash"]["checked"] > 0,
                 (oa["hash"], on["hash"]["digest"]))
        rc.check(f"{gid} 0 Desync, 0 Tick-Fehler, 0 Fehler",
                 oa["desync"] is None and oa["tick_error"] is None and sum(oa["errors"].values()) == 0 and oa["tier1_bytes"] > 0,
                 (oa["desync"], oa["tick_error"], oa["errors"], oa["first_error"]))
        code, msg = verify(da, gid)
        rc.check(f"{gid} tier1.py verify Exit 0", code == 0, msg)
        pr = reader.check_dir(da, [gid])[gid]
        rc.check(f"{gid} reader check (inkl. cells-Gleichschritt)", pr == "ok", pr)
        ncell = sum(1 for m in reader.Game(da, gid).meta() if m.get("cell", -1) >= 0)
        rc.check(f"{gid} Zellblöcke = räumliche Samples", ncell == oa["spatial"], (ncell, oa["spatial"]))
        masks[gid] = mask(da, gid)
        ca = oa["cpu_ms"]["user"] + oa["cpu_ms"]["system"]
        cn = on["cpu_ms"]["user"] + on["cpu_ms"]["system"]
        cpu[gid] = {"all": ca, "none": cn, "tm_all": oa["time_ms"], "cells_bytes": oa["cells_bytes"],
                    "tier1_bytes": oa["tier1_bytes"], "spatial": oa["spatial"], "rss": (oa["rss_max_mb"], on["rss_max_mb"])}
    R["cpu"], R["mask"] = cpu, masks
    # Maskentor gesamt und je Typ
    per_t: dict = defaultdict(lambda: [0, 0])
    per_dt: dict = defaultdict(lambda: [0, 0])
    for mg in masks.values():
        for t, dts in mg.items():
            for dt, (n, v) in dts.items():
                per_t[t][0] += n; per_t[t][1] += v
                per_dt[(t, dt)][0] += n; per_dt[(t, dt)][1] += v
    n = sum(v[0] for v in per_t.values()); v = sum(v[1] for v in per_t.values())
    rc.check("Maskentor gesamt < 1 %", n > 0 and v / n < 0.01, f"{v}/{n} = {100*v/max(n,1):.2f} %")
    print("\nMaskentor (res_kind 0, Bit des Typs an der Zelle von res_tile)")
    print("Typ               n    verletzt   %      je res_dt (n/verletzt)")
    for t, (nn, vv) in sorted(per_t.items(), key=lambda kv: -kv[1][0]):
        dts = " ".join(f"dt{dt}:{a}/{b}" for (tt, dt), (a, b) in sorted(per_dt.items(), key=lambda kv: str(kv[0])) if tt == t)
        print(f"{t:16s} {nn:5d} {vv:6d}  {100*vv/nn:6.2f}   {dts}")
    print("\nCPU (cpu_ms user+system), alles gegen ohne, Paar bei gleicher Last")
    print("gid        ohne_s   alles_s  Aufschlag  cells_s tier1_s  cells_KB/Sample  tier1_MB  rss(alles/ohne)")
    sa = sn = 0
    for gid in GIDS:
        if gid not in cpu:
            continue
        c = cpu[gid]
        sa += c["all"]; sn += c["none"]
        kb = c["cells_bytes"] / 1024 / max(c["spatial"], 1)
        print(f"{gid}  {c['none']/1000:7.1f}  {c['all']/1000:7.1f}  {100*(c['all']/c['none']-1):7.1f} %  "
              f"{c['tm_all']['cells']/1000:6.1f} {c['tm_all']['tier1']/1000:6.1f}  {kb:10.2f}  {c['tier1_bytes']/1e6:8.2f}  {c['rss']}")
    if sn:
        print(f"gesamt     {sn/1000:7.1f}  {sa/1000:7.1f}  {100*(sa/sn-1):7.1f} %")
        R["cpu_total"] = {"all": sa, "none": sn, "pct": 100 * (sa / sn - 1)}


def step_cut():
    print("== cut", flush=True)
    da = os.path.join(OUT, "all")
    for gid in ("dJtnLxJA", "JwFNdofK", "omTgUNMh"):
        g = reader.Game(da, gid)
        meta = g.meta()
        chosen = json.load(open(os.path.join(da, f"{gid}.chk")))["chosen"]
        rec = json.load(open(rc.REC[gid][0]))
        hts = rc.hash_turns(rec)
        pick = None
        for i in range(len(hts) // 3, len(hts)):
            last_ok, x = hts[i - 1], hts[i]
            aff = [m for m in meta if m.get("res_kind", 2) != 2 and m["tick"] <= last_ok < m["tick"] + rc.win_end(m)]
            cells_after = any(m.get("cell", -1) >= 0 and m["tick"] > last_ok for m in meta)
            if aff and cells_after and any(c > last_ok for c in chosen) and any(c <= last_ok for c in chosen):
                pick = (last_ok, x, aff)
                break
        if pick:
            break
    if not rc.check("cut: passender Hash gefunden", pick is not None):
        return
    last_ok, x, aff = pick
    print(f"  {gid}: verfälsche Hash bei {x}, last_ok {last_ok}, betroffene res: "
          f"{[(m['tick'], rc.rtype(m), m['res_kind'], m['res_dt']) for m in aff]}, chosen {chosen}", flush=True)
    for t in rec["turns"]:
        if t["turnNumber"] == x:
            t["hash"] = t["hash"] + 1.0
    rd = os.path.join(OUT, "rec_cut")
    os.makedirs(rd, exist_ok=True)
    rp = os.path.join(rd, f"{gid}.json")
    json.dump(rec, open(rp, "w"))
    dc = os.path.join(OUT, "cut")
    shutil.rmtree(dc, ignore_errors=True)
    r = rc.run_mat(gid, dc, env=ALL, variant=VARIANT, record=rp)
    rc.check("cut: rc 0", r["rc"] == 0, r["err"][-300:])
    ok = rc.ok_of(dc, gid)
    chk = json.load(open(os.path.join(dc, f"{gid}.chk")))
    rc.check("cut: desync erkannt, valid_until = last_ok in .ok",
             ok["desync"] == {"tick": x, "last_ok_tick": last_ok} and ok["valid_until"] == last_ok, (ok["desync"], ok["valid_until"]))
    rc.check("cut: valid_until in .chk (Tier 1) und hdr", chk.get("valid_until") == last_ok and reader.Game(dc, gid).hdr["valid_until"] == last_ok,
             chk.get("valid_until"))
    rc.check("cut: chkDropped > 0 und gleich in .ok/.chk", ok["chk_dropped"] > 0 and ok["chk_dropped"] == chk.get("dropped"),
             (ok["chk_dropped"], chk.get("dropped"), chk.get("ticks")))
    code, msg = verify(dc, gid)
    rc.check("cut: tier1.py verify Exit 0", code == 0, msg)
    rc.check("cut: reader check", reader.check_dir(dc, [gid])[gid] == "ok")
    probs, patched = rc.cut_check(da, dc, gid, last_ok)
    rc.check("cut: Zeilen = voller Lauf bis last_ok mit Schnitt-Regeln", not probs, probs[:3])
    mx = reader.Game(dc, gid).meta()
    keyset = {(m["tick"], m["clientID"], json.dumps(m["intent"], sort_keys=True)) for m in aff}
    now = [m for m in mx if (m["tick"], m["clientID"], json.dumps(m["intent"], sort_keys=True)) in keyset]
    rc.check("cut: betroffene res jetzt res_kind 2", len(now) == len(aff) and all(m["res_kind"] == 2 for m in now),
             [(m["tick"], m["res_kind"], m["res_tile"]) for m in now])
    fa = open(os.path.join(da, f"{gid}.cells"), "rb").read()
    fc = open(os.path.join(dc, f"{gid}.cells"), "rb").read()
    rc.check("cut: .cells ist Byte-Präfix des vollen Laufs und kürzer", 0 < len(fc) < len(fa) and fa[:len(fc)] == fc, (len(fc), len(fa)))
    R["cut"] = {"gid": gid, "x": x, "last_ok": last_ok, "affected": len(aff), "patched": patched,
                "chk_dropped": ok["chk_dropped"], "samples": ok["samples"]}


def main(argv):
    steps = argv or ["pairs", "cut"]
    os.makedirs(OUT, exist_ok=True)
    for s in steps:
        try:
            {"pairs": step_pairs, "cut": step_cut}[s]()
        except Exception as e:
            import traceback
            traceback.print_exc()
            rc.check(f"{s} ohne Ausnahme", False, e)
        json.dump(R, open(os.path.join(OUT, "results.json"), "w"), default=str)
    print(f"\n{len(rc.FAILS)} Prüfungen durchgefallen: {rc.FAILS}")
    return 1 if rc.FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
