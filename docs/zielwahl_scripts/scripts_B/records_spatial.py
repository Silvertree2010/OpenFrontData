"""B: spatial label statistics from the 65 local raw records (no replay, no engine run).

Measures what can be measured without territory state:
  - intent/unit-type counts, share of spatial intents, spawn-phase loss, attack thinning (THIN=30)
  - map sizes -> tiles per coarse cell (W/180, H/90), share of spatial labels on maps finer/coarser than 1440x720
  - distance of each spatial target to the acting player's (last) spawn tile, per type
  - distance to the same player's previous target of the same type (locality)
All distances Euclidean in real tiles and in coarse cells of the 180x90 grid (actions.tile_encode).
"""
import glob, json, math, os, re, sys
from collections import Counter, defaultdict
from compression import zstd
import numpy as np

RAW = "~/openfront-ai/data/raw"
MAPS = "/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps"
THIN = 30

man = {}
for d in os.listdir(MAPS):
    p = os.path.join(MAPS, d, "manifest.json")
    if os.path.exists(p):
        m = json.load(open(p))
        man[d] = m
def dims(name, size):
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    m = man.get(key)
    if m is None:
        return None
    mm = m["map4x"] if str(size).lower() == "compact" else m["map"]
    return mm["width"], mm["height"]

files = sorted(glob.glob(os.path.join(RAW, "*", "*.json.zst")))
cnt = Counter(); unit = Counter(); miss = Counter()
spawn_phase_drop = Counter()
attack_kept = 0; attack_total = 0
rows = []          # (type, unit, dist_spawn_tiles, dist_spawn_cells, dprev_tiles, dprev_cells, W, H, turn)
map_rows = []
per_game_spatial = []
for f in files:
    r = json.loads(zstd.decompress(open(f, "rb").read()))
    cfg = r["info"]["config"]
    wh = dims(cfg["gameMap"], cfg.get("gameMapSize", "Normal"))
    if wh is None:
        miss[cfg["gameMap"]] += 1
        continue
    W, H = wh
    map_rows.append((cfg["gameMap"], cfg.get("gameMapSize"), W, H))
    humans = {p["clientID"] for p in r["info"]["players"]}
    spawn = {}
    last_spawn_turn = -1
    for t in r["turns"]:
        for i in t.get("intents") or []:
            if i.get("type") == "spawn":
                last_spawn_turn = max(last_spawn_turn, t["turnNumber"])
    last_att = {}; prev = {}
    gsp = 0
    for t in r["turns"]:
        tn = t["turnNumber"]
        for i in t.get("intents") or []:
            ty = i.get("type"); cid = i.get("clientID")
            if ty == "mark_disconnected":
                continue
            cnt[ty] += 1
            in_spawn = tn <= last_spawn_turn
            if in_spawn:
                spawn_phase_drop[ty] += 1
            if ty == "spawn":
                spawn[cid] = int(i["tile"])
                continue
            if ty == "attack":
                attack_total += 1
                if not in_spawn:
                    la = last_att.get(cid)
                    if la is None or tn - la >= THIN:
                        attack_kept += 1; last_att[cid] = tn
                continue
            if ty == "build_unit":
                tile = int(i["tile"]); u = i.get("unit")
            elif ty == "boat":
                tile = int(i["dst"]); u = "-"
            elif ty == "move_warship":
                tile = int(i["tile"]); u = "-"
            else:
                continue
            if in_spawn:
                continue
            gsp += 1
            unit[(ty, u)] += 1
            x, y = tile % W, tile // W
            cx, cy = W / 180.0, H / 90.0          # tiles per coarse cell
            ds = dsc = float("nan")
            if cid in spawn:
                sx, sy = spawn[cid] % W, spawn[cid] // W
                ds = math.hypot(x - sx, y - sy)
                dsc = math.hypot((x - sx) / cx, (y - sy) / cy)
            key = (cid, ty, u if ty == "build_unit" else "-")
            dp = dpc = float("nan")
            if key in prev:
                px, py = prev[key]
                dp = math.hypot(x - px, y - py)
                dpc = math.hypot((x - px) / cx, (y - py) / cy)
            prev[key] = (x, y)
            rows.append((ty, u, ds, dsc, dp, dpc, W, H, tn))
    per_game_spatial.append(gsp)

print(f"records: {len(files)}, unresolved maps: {dict(miss)}")
tot = sum(cnt.values())
print(f"intents (excl. mark_disconnected): {tot}")
for k, v in cnt.most_common():
    print(f"  {k:<20s} {v:>7d} {v/tot:6.1%}  in spawn phase: {spawn_phase_drop[k]}")
print(f"attack kept after THIN={THIN} (outside spawn phase): {attack_kept}/{attack_total} = {attack_kept/max(1,attack_total):.1%}")
nonatt_nonspawn = sum(v for k, v in cnt.items() if k not in ("attack", "spawn")) - sum(spawn_phase_drop[k] for k in cnt if k not in ("attack", "spawn"))
spatial = len(rows)
est_samples = nonatt_nonspawn + attack_kept
print(f"spatial (build/boat/warship, outside spawn phase): {spatial}")
print(f"est. materialised samples (non-attack outside spawn + thinned attacks; ignores dead/non-human filter): {est_samples}")
print(f"  -> spatial share of materialised samples ~ {spatial/est_samples:.1%}")
print(f"spatial per game: median {np.median(per_game_spatial):.0f}, mean {np.mean(per_game_spatial):.0f}")

print("\nunit types (build_unit, outside spawn phase):")
bt = sum(v for (t, u), v in unit.items() if t == "build_unit")
for (t, u), v in sorted(unit.items(), key=lambda kv: -kv[1]):
    print(f"  {t:<13s} {u:<14s} {v:>6d} {v/spatial:6.1%} of spatial")

W_arr = np.array([r[6] for r in rows]); H_arr = np.array([r[7] for r in rows])
print("\nmap sizes of spatial labels: tiles per coarse cell (W/180):")
tpc = W_arr / 180.0
for q in (0.1, 0.25, 0.5, 0.75, 0.9):
    print(f"  q{int(q*100):02d}: {np.quantile(tpc, q):.2f} tiles/cell (W={np.quantile(W_arr, q):.0f})")
print(f"  share of spatial labels on maps with W<1440 (grid finer than map): {(W_arr < 1440).mean():.1%}")
print(f"  share with W>1440: {(W_arr > 1440).mean():.1%};  aspect W/H median {np.median(W_arr/H_arr):.2f}, range {np.min(W_arr/H_arr):.2f}-{np.max(W_arr/H_arr):.2f}")
cw = W_arr / 180.0; ch = H_arr / 90.0
print(f"  cell anisotropy (cell_w/cell_h) median {np.median(cw/ch):.2f}, q10 {np.quantile(cw/ch,.1):.2f}, q90 {np.quantile(cw/ch,.9):.2f}")

def summ(name, sel):
    a = np.array([r[3] for r in rows if sel(r)], float); a = a[~np.isnan(a)]
    b = np.array([r[5] for r in rows if sel(r)], float); b = b[~np.isnan(b)]
    t = np.array([r[2] for r in rows if sel(r)], float); t = t[~np.isnan(t)]
    if a.size == 0:
        return
    print(f"  {name:<22s} n={a.size:>6d}  dist->spawn cells: med {np.median(a):5.1f} q25 {np.quantile(a,.25):5.1f} q75 {np.quantile(a,.75):5.1f}"
          f" | <=4c {np.mean(a<=4):5.1%} <=8c {np.mean(a<=8):5.1%} <=16c {np.mean(a<=16):5.1%}"
          f" | tiles med {np.median(t):6.0f}"
          + (f" || dist->prev same type cells: med {np.median(b):5.1f}, <=1c {np.mean(b<=1):5.1%} <=4c {np.mean(b<=4):5.1%} <=8c {np.mean(b<=8):5.1%} (n={b.size})" if b.size else ""))

print("\ndistance to own spawn tile (proxy for home region) and to previous same-type target, coarse cells:")
summ("ALL spatial", lambda r: True)
summ("boat", lambda r: r[0] == "boat")
summ("move_warship", lambda r: r[0] == "move_warship")
for u in sorted({r[1] for r in rows if r[0] == "build_unit"}):
    summ(f"build {u}", lambda r, u=u: r[0] == "build_unit" and r[1] == u)
