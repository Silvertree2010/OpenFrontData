"""B: how far apart are a player's alternative targets of the same type? (set-valued target sizing)
For each spatial label (build by unit type / boat / move_warship, outside spawn phase):
  - time gap and distance to the SAME player's NEXT same-type label
  - within windows of 300 / 1200 ticks: size of the set of same-type labels and min distance to another member
Distances in coarse cells of the CURRENT 180x90 grid (same unit as eval_spatial.py) and in real tiles."""
import glob, json, os, re, math
from collections import defaultdict
from compression import zstd
import numpy as np
RAW = "~/openfront-ai/data/raw"
MAPS = "/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps"
man = {d: json.load(open(os.path.join(MAPS, d, "manifest.json"))) for d in os.listdir(MAPS) if os.path.exists(os.path.join(MAPS, d, "manifest.json"))}
seqs = defaultdict(list)   # (game, cid, kind) -> [(tick, x, y, cw, ch)]
for f in sorted(glob.glob(os.path.join(RAW, "*", "*.json.zst"))):
    r = json.loads(zstd.decompress(open(f, "rb").read()))
    c = r["info"]["config"]; m = man[re.sub(r"[^a-z0-9]", "", c["gameMap"].lower())]
    mm = m["map4x"] if str(c.get("gameMapSize")).lower() == "compact" else m["map"]
    W, H = mm["width"], mm["height"]; cw, ch = W / 180, H / 90
    for t in r["turns"]:
        for i in t.get("intents") or []:
            ty = i.get("type")
            if ty == "build_unit": kind, tile = "build " + i.get("unit", "?"), int(i["tile"])
            elif ty == "boat": kind, tile = "boat", int(i["dst"])
            elif ty == "move_warship": kind, tile = "move_warship", int(i["tile"])
            else: continue
            seqs[(f, i["clientID"], kind)].append((t["turnNumber"], tile % W, tile // W, cw, ch))
res = defaultdict(lambda: defaultdict(list))
for (f, cid, kind), s in seqs.items():
    for k, (tk, x, y, cw, ch) in enumerate(s):
        if k + 1 < len(s):
            t2, x2, y2, _, _ = s[k + 1]
            res[kind]["gap"].append(t2 - tk)
            res[kind]["dnext_c"].append(math.hypot((x2 - x) / cw, (y2 - y) / ch))
            res[kind]["dnext_t"].append(math.hypot(x2 - x, y2 - y))
            res[kind]["dup"].append(x2 == x and y2 == y)
        for win in (300, 1200):
            others = [(xx, yy) for (tt, xx, yy, _, _) in s if tt != tk and abs(tt - tk) <= win and not (xx == x and yy == y)]
            res[kind][f"n{win}"].append(len(others))
            if others:
                res[kind][f"dmin{win}"].append(min(math.hypot((xx - x) / cw, (yy - y) / ch) for xx, yy in others))
def line(kind, d):
    g = np.array(d["gap"]); dn = np.array(d["dnext_c"]); dt = np.array(d["dnext_t"])
    n3 = np.array(d["n300"]); n12 = np.array(d["n1200"]); dm = np.array(d.get("dmin300", [np.nan]))
    print(f"{kind:<20s} n={len(d['n300']):>6d} | next same-type: gap med {np.median(g):5.0f} ticks, dist med {np.median(dn):5.1f} cells ({np.median(dt):4.0f} tiles), "
          f">4 cells {np.mean(dn>4):5.1%}, exact same tile {np.mean(d['dup']):5.1%} | "
          f"others within +-300 ticks: share with >=1 {np.mean(n3>0):5.1%}, med size {np.median(n3):3.0f}; nearest other med {np.nanmedian(dm):5.1f} cells, <=1 cell {np.nanmean(dm<=1):5.1%} | +-1200: share>=1 {np.mean(n12>0):5.1%}")
allk = defaultdict(list)
for kind in sorted(res, key=lambda k: -len(res[k]["n300"])):
    line(kind, res[kind])
    for k, v in res[kind].items(): allk[k] += list(v)
line("ALL", allk)
