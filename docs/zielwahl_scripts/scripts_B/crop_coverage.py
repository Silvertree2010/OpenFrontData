"""B: share of spatial targets inside a square crop of half-width R real tiles (Chebyshev distance),
centred on the acting player's last spawn tile (proxy for the home region; the eval's C3 centroid
is biased toward the map centre by the +0.0039 dequantisation offset, see scripts_A/baseline_quant_bug.py)."""
import glob, json, os, re
from collections import defaultdict
from compression import zstd
import numpy as np
RAW = "~/openfront-ai/data/raw"
MAPS = "/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps"
man = {d: json.load(open(os.path.join(MAPS, d, "manifest.json"))) for d in os.listdir(MAPS) if os.path.exists(os.path.join(MAPS, d, "manifest.json"))}
D = defaultdict(list)
for f in sorted(glob.glob(os.path.join(RAW, "*", "*.json.zst"))):
    r = json.loads(zstd.decompress(open(f, "rb").read()))
    c = r["info"]["config"]; m = man[re.sub(r"[^a-z0-9]", "", c["gameMap"].lower())]
    mm = m["map4x"] if str(c.get("gameMapSize")).lower() == "compact" else m["map"]
    W = mm["width"]; spawn = {}
    for t in r["turns"]:
        for i in t.get("intents") or []:
            ty = i.get("type"); cid = i.get("clientID")
            if ty == "spawn": spawn[cid] = int(i["tile"]); continue
            if ty == "build_unit": k = i.get("unit"); tile = int(i["tile"])
            elif ty == "boat": k = "Boat"; tile = int(i["dst"])
            elif ty == "move_warship": k = "MoveWarship"; tile = int(i["tile"])
            else: continue
            if cid not in spawn: continue
            s = spawn[cid]
            d = max(abs(tile % W - s % W), abs(tile // W - s // W))
            D[k].append(d); D["ALL"].append(d)
            grp = "structures" if k in ("City", "Defense Post", "Port", "Factory", "SAM Launcher", "Missile Silo") else ("nukes" if k in ("Atom Bomb", "Hydrogen Bomb", "MIRV") else None)
            if grp: D[grp].append(d)
Rs = (32, 64, 128, 256, 512)
print(f"{'type':<14s} {'n':>6s}  " + "  ".join(f"R<={R:>3d}" for R in Rs) + "   (crop side = 2R tiles, Chebyshev)")
for k in ["ALL", "structures", "Boat", "nukes", "Warship", "MoveWarship", "City", "Defense Post", "Port", "Factory", "SAM Launcher", "Missile Silo", "Atom Bomb", "Hydrogen Bomb"]:
    a = np.array(D[k])
    print(f"{k:<14s} {a.size:>6d}  " + "  ".join(f"{np.mean(a<=R):6.1%}" for R in Rs) + f"   median {np.median(a):5.0f} tiles")
