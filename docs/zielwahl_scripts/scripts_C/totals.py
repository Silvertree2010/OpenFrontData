"""Pool-level totals from manifests + synthetic per-unit sizes. Estimates only."""
import json, glob, os
from compression import zstd
MAPS = "/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps"
land_n, land_c, wts = [], [], []
for m in glob.glob(MAPS + "/*/manifest.json"):
    j = json.load(open(m)); f = j.get("multiplayer_frequency") or 0
    land_n.append(j["map"]["num_land_tiles"]); land_c.append(j["map4x"]["num_land_tiles"]); wts.append(f)
W = sum(wts)
mean_n = sum(l * w for l, w in zip(land_n, wts)) / W if W else sum(land_n) / len(land_n)
mean_c = sum(l * w for l, w in zip(land_c, wts)) / W if W else sum(land_c) / len(land_c)
unw_n = sum(land_n) / len(land_n)
mix_n = 48 / 65   # Normal share in the 65 local records
land_game = mix_n * mean_n + (1 - mix_n) * mean_c
print(f"maps={len(land_n)} freq-weighted land: normal {mean_n/1e6:.2f}M compact {mean_c/1e6:.2f}M (unweighted normal {unw_n/1e6:.2f}M); per game (74% normal) {land_game/1e6:.2f}M")
G = 18018
print("Tier-1 tile-update stream, GB for 18018 games:")
for k in (2.5, 4, 6):
    for b in (1.25, 2.0, 2.5):
        print(f"  updates/land={k:<4} B/update={b:<4} -> {land_game*k*b/1e6:6.1f} MB/game  {G*land_game*k*b/1e9:6.1f} GB")
# meta extras: synthetic JSON fields added per sample
extra = json.dumps({"tick": 5123, "sid": 217, "allies": [12, 88], "team": None})
res = json.dumps({"res": {"ok": 1, "tile": 1234567, "dt": 3}})
print("meta extra bytes raw/sample:", len(extra), "+ spatial res:", len(res))
lines = "\n".join(json.dumps({"tick": 5000 + i, "sid": 217, "allies": [12, 88], "team": None, "res": {"ok": 1, "tile": 1234567 + 37 * i, "dt": 3}}) for i in range(2000))
print("zstd per sample (2000 lines, one game):", round(len(zstd.compress(lines.encode(), level=3)) / 2000, 1), "B")
print("Tier-2 (per spatial sample 3.0/4.0 KB) GB for N spatial samples:")
for n in (4.5e6, 10e6, 13e6):
    print(f"  N={n/1e6:.1f}M -> {n*3.0e3/1e9:.0f} / {n*4.0e3/1e9:.0f} GB")
