"""B: cell geometry in real tiles for the current 180x90 grid vs isotropic alternatives,
weighted by spatial labels (build/boat/warship outside spawn phase) of the 65 local records."""
import glob, json, os, re, math
from compression import zstd
import numpy as np
RAW = "~/openfront-ai/data/raw"
MAPS = "/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps"
man = {d: json.load(open(os.path.join(MAPS, d, "manifest.json"))) for d in os.listdir(MAPS) if os.path.exists(os.path.join(MAPS, d, "manifest.json"))}
Ws, Hs, Ns = [], [], []
games = []
for f in sorted(glob.glob(os.path.join(RAW, "*", "*.json.zst"))):
    r = json.loads(zstd.decompress(open(f, "rb").read()))
    c = r["info"]["config"]; m = man[re.sub(r"[^a-z0-9]", "", c["gameMap"].lower())]
    mm = m["map4x"] if str(c.get("gameMapSize")).lower() == "compact" else m["map"]
    W, H = mm["width"], mm["height"]
    n = sum(1 for t in r["turns"] for i in (t.get("intents") or []) if i.get("type") in ("build_unit", "boat", "move_warship"))
    games.append((c["gameMap"], c.get("gameMapSize"), W, H, n))
    Ws.append(W); Hs.append(H); Ns.append(n)
W = np.array(Ws, float); H = np.array(Hs, float); w = np.array(Ns, float)
def wq(x, qs=(0.1, 0.5, 0.9)):
    o = np.argsort(x); cw = np.cumsum(w[o]) / w.sum()
    return [x[o][np.searchsorted(cw, q)] for q in qs]
print("label-weighted quantiles q10/q50/q90")
print("  W:", wq(W), " H:", wq(H), " W/H:", [round(v, 2) for v in wq(W / H)])
print("  current 180x90 cell width  W/180 tiles:", [round(v, 1) for v in wq(W / 180)])
print("  current 180x90 cell height H/90  tiles:", [round(v, 1) for v in wq(H / 90)])
print("  current cell area tiles^2:", [round(v) for v in wq(W * H / 16200)])
print("  grid unit (1440x720) = W/1440 x H/720 tiles:", [round(v, 2) for v in wq(W / 1440)], [round(v, 2) for v in wq(H / 720)])
for S in (128, 144, 160, 180):
    c = np.ceil(np.maximum(W, H) / S)
    used = np.ceil(W / c) * np.ceil(H / c)
    print(f"  square canvas {S}x{S} ({S*S} cells), isotropic cell = ceil(max(W,H)/{S}) tiles: q10/50/90 {wq(c)}; "
          f"used-cell share of canvas q50 {np.median(used/(S*S)):.2f}")
# aspect-fit ~16200 cells, isotropic
c = np.sqrt(W * H / 16200)
print("  aspect-fit isotropic grid with ~16200 cells: cell side tiles q10/50/90", [round(v, 1) for v in wq(c)])
print("\nper game: map, size, W, H, spatial intents")
for g in sorted(games, key=lambda g: -g[4])[:12]:
    print("  ", g)
print("extreme aspect:", [g for g in games if g[2] / g[3] > 3 or g[2] / g[3] < 0.6])
