"""SYNTHETIC Tier-2 sizes: 3-level crop pyramid (160x160 at stride 1/2/4, owner u16) +
four 90x180 kind-bitmasks per spatial sample. Real terrain masks, synthetic owners."""
import sys, json, numpy as np
sys.path.insert(0, "/tmp/openfront-ai/arbeit/amssp/scripts_C")
from synth_storage import load, territories, Z
out = {}
for name, which in (("world", "map"), ("europe", "map"), ("korea", "map4x")):
    rng = np.random.default_rng(1)
    W, H, land, shore = load(name, which)
    own_k, tclaim, seeds = territories(W, H, land, 40, rng)
    sid = rng.choice(np.arange(1, 400), 40, replace=False).astype(np.uint16)
    li = np.flatnonzero(land.ravel()); tc = tclaim[li]
    st = np.zeros(H * W, np.uint16); keep = li[tc <= np.quantile(tc, 0.8)]
    st[keep] = sid[own_k.ravel()[keep] - 1]
    S2 = st.reshape(H, W); water = ~land
    gy = (np.arange(H) * 90 // H)[:, None]; gx = (np.arange(W) * 180 // W)[None, :]
    cell = (gy * 180 + gx).ravel()
    def cm(sel):
        m = np.zeros(16200, bool); m[np.unique(cell[sel])] = True; return Z(np.packbits(m))
    pyr, masks = [], []
    for c in rng.choice(np.flatnonzero(st), 200, replace=False):
        cy, cx = c // W, c % W; me = st[c]; tot = 0
        for stride in (1, 2, 4):
            half = 80 * stride
            ys = np.clip(np.arange(cy - half, cy + half, stride), 0, H - 1)
            xs = np.clip(np.arange(cx - half, cx + half, stride), 0, W - 1)
            tot += Z(S2[np.ix_(ys, xs)])
        pyr.append(tot)
    for k in rng.choice(40, 12, replace=False):
        me = sid[k]; mine = st == me
        if mine.sum() < 50: continue
        sh = shore.ravel(); l = land.ravel()
        masks.append(cm(mine) + cm(mine & sh) + cm(sh & l & ~mine) + cm(water.ravel()))
    out[f"{name}/{which}"] = {"pyramid3_B_mean": round(float(np.mean(pyr))), "pyramid3_B_p90": round(float(np.percentile(pyr, 90))),
                              "4masks_B_mean": round(float(np.mean(masks)))}
print(json.dumps(out))
