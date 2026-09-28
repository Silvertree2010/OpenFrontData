"""SYNTHETIC storage estimates on REAL terrain masks (engine map.bin), synthetic ownership.
Territories: domain-warped weighted Voronoi, claim fronts expand from seeds; war phase
re-conquers whole neighbours front-by-front. All sizes are estimates, not pool measurements."""
import json, time, sys
import numpy as np
from compression import zstd
MAPS = "/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps"
Z = lambda a, lvl=3: len(zstd.compress(np.ascontiguousarray(a).tobytes(), level=lvl))

def load(name, which):
    man = json.load(open(f"{MAPS}/{name}/manifest.json"))
    W, H = man[which]["width"], man[which]["height"]
    t = np.fromfile(f"{MAPS}/{name}/{which}.bin", dtype=np.uint8).reshape(H, W)
    land = ((t & 0x80) != 0) & ((t & 0x1f) != 31)
    shore = (t & 0x40) != 0
    return W, H, land, shore

def smooth(H, W, scale, rng):
    h, w = H // scale + 3, W // scale + 3
    g = rng.standard_normal((h, w))
    yi = np.linspace(0, h - 2.001, H); xi = np.linspace(0, w - 2.001, W)
    y0 = yi.astype(int); x0 = xi.astype(int)
    fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
    return (g[y0][:, x0] * (1 - fy) * (1 - fx) + g[y0][:, x0 + 1] * (1 - fy) * fx
            + g[y0 + 1][:, x0] * fy * (1 - fx) + g[y0 + 1][:, x0 + 1] * fy * fx)

def territories(W, H, land, P, rng):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    A = 0.04 * max(W, H)
    wx = xx + A * smooth(H, W, 120, rng) + 2.0 * rng.standard_normal((H, W))   # ragged borders
    wy = yy + A * smooth(H, W, 120, rng) + 2.0 * rng.standard_normal((H, W))
    li = np.flatnonzero(land)
    seeds = rng.choice(li, P, replace=False)
    sx, sy = (seeds % W).astype(np.float32), (seeds // W).astype(np.float32)
    speed = rng.uniform(0.6, 1.6, P).astype(np.float32)
    best = np.full(H * W, np.inf, np.float32); own = np.zeros(H * W, np.int32)
    fx, fy = wx.ravel(), wy.ravel()
    for k in range(P):
        d = np.hypot(fx - sx[k], fy - sy[k]) / speed[k]
        m = d < best; best[m] = d[m]; own[m] = k + 1
    best[~land.ravel()] = np.inf; own[~land.ravel()] = 0
    return own, best, seeds

def run(name, which, P=40, seed=0):
    rng = np.random.default_rng(seed)
    W, H, land, shore = load(name, which)
    nland = int(land.sum())
    own_k, tclaim, seeds = territories(W, H, land, P, rng)
    sid = rng.choice(np.arange(1, 400), P, replace=False).astype(np.uint16)   # smallIDs up to 400
    owner_final = np.where(own_k > 0, sid[np.maximum(own_k - 1, 0)], 0).astype(np.uint16)
    li = np.flatnonzero(land.ravel())
    # --- claim phase: 90 % of land claimed over 1500 ticks, front order
    tc = tclaim[li]; q = np.quantile(tc, 0.9)
    keep = tc <= q
    ticks_claim = np.minimum(1499, (tc[keep] / q * 1500).astype(np.int32))
    refs_claim = li[keep]; st_claim = owner_final.ravel()[refs_claim]
    state = np.zeros(H * W, np.uint16); state[refs_claim] = st_claim
    snap_mid = state.copy()
    # --- war phase: attackers take whole neighbours front-by-front until 1.5*nland flips
    upd_t, upd_r, upd_s = [ticks_claim], [refs_claim], [st_claim]
    alive = list(range(P)); tick = 1500; flips = 0; target = int(1.5 * nland)
    cur_k = np.zeros(H * W, np.int32); cur_k[refs_claim] = own_k.ravel()[refs_claim]
    while flips < target and len(alive) > 1:
        v = alive[rng.integers(len(alive))]
        vs = seeds[v]; others = [a for a in alive if a != v]
        dd = [np.hypot(seeds[a] % W - vs % W, seeds[a] // W - vs // W) for a in others]
        a = others[int(np.argmin(dd))]
        vt = np.flatnonzero(cur_k == v + 1)
        if vt.size == 0: alive.remove(v); continue
        d = np.hypot(vt % W - seeds[a] % W, vt // W - seeds[a] // W)
        order = vt[np.argsort(d)]
        dur = max(50, min(600, order.size // 400))
        tk = tick + (np.arange(order.size) * dur // order.size)
        upd_t.append(tk.astype(np.int32)); upd_r.append(order); upd_s.append(np.full(order.size, sid[a], np.uint16))
        cur_k[order] = a + 1; flips += order.size; tick += dur // 3; alive.remove(v)
    T = np.concatenate(upd_t); R = np.concatenate(upd_r).astype(np.uint32); S = np.concatenate(upd_s)
    o = np.lexsort((R, T)); T, R, S = T[o], R[o], S[o]
    nupd = T.size; nt = int(T.max()) + 1
    counts = np.bincount(T, minlength=nt).astype(np.uint32)
    # naive columns and engine-like interleaved pairs
    naive = Z(counts) + Z(R) + Z(S)
    pairs = np.empty(2 * nupd, np.uint32); pairs[0::2] = R; pairs[1::2] = S
    inter = Z(pairs)
    # sorted-delta within tick
    first = np.r_[True, T[1:] != T[:-1]]
    dR = np.where(first, R.astype(np.int64), np.diff(R.astype(np.int64), prepend=0)).astype(np.int32)
    delta3 = Z(counts) + Z(dR) + Z(S); delta19 = Z(counts, 19) + Z(dR, 19) + Z(S, 19)
    # keyframes
    kf3, kf19 = Z(snap_mid), Z(snap_mid, 19)
    # python reconstruction speed: walk all ticks, apply updates
    t0 = time.perf_counter(); st = np.zeros(H * W, np.uint16)
    bounds = np.r_[0, np.cumsum(counts)]
    for k in range(nt):
        a_, b_ = bounds[k], bounds[k + 1]
        if b_ > a_: st[R[a_:b_]] = S[a_:b_]
    rec_s = time.perf_counter() - t0
    # --- crops from mid-game snapshot around random OWNED tiles
    S2 = snap_mid.reshape(H, W)
    owned = np.flatnonzero(snap_mid)
    cs = rng.choice(owned, 300, replace=False)
    res = {}
    for size, step in ((64, 1), (128, 1), (256, 1), (256, 2), (512, 4)):
        tot16 = tot8 = 0
        for c in cs:
            cy, cx = c // W, c % W; h = size // 2
            y0, x0 = cy - h, cx - h
            pad = np.zeros((size, size), np.uint16)
            ys, xs = max(0, y0), max(0, x0); ye, xe = min(H, y0 + size), min(W, x0 + size)
            pad[ys - y0:ye - y0, xs - x0:xe - x0] = S2[ys:ye, xs:xe]
            crop = pad[::step, ::step]
            me = S2[cy, cx]
            rel = np.where(crop == 0, 0, np.where(crop == me, 1, 3)).astype(np.uint8)
            tot16 += Z(crop); tot8 += Z(rel)
        res[f"crop{size}/{step}"] = {"side": size // step, "raw_u16": (size // step) ** 2 * 2,
                                     "zstd_owner_u16": round(tot16 / len(cs)), "zstd_rel_u8": round(tot8 / len(cs))}
    # --- 90x180 bitpacked masks (build: cells with own land; boat: cells with non-own shore)
    gy = (np.arange(H) * 90 // H)[:, None]; gx = (np.arange(W) * 180 // W)[None, :]
    cell = (gy * 180 + gx).ravel()
    mb, mbo, legal_counts, mass16_build, mass16_boat = [], [], [], [], []
    shore_f = shore.ravel()
    for k in rng.choice(P, 20, replace=False):
        me = sid[k]; mine = snap_mid == me
        if mine.sum() < 50: continue
        m = np.zeros(16200, bool); m[np.unique(cell[mine])] = True
        mb.append(Z(np.packbits(m)))
        lb = shore_f & (snap_mid != me) & land.ravel()
        m2 = np.zeros(16200, bool); m2[np.unique(cell[lb])] = True
        mbo.append(Z(np.packbits(m2)))
        legal_counts.append((int(mine.sum()), int(m.sum()), int(lb.sum()), int(m2.sum())))
        # uniform-over-legal baseline: mass within r=16 of a label drawn uniformly from the legal set
        for legal, store in ((np.flatnonzero(mine), mass16_build), (np.flatnonzero(lb), mass16_boat)):
            if legal.size == 0: continue
            lab = rng.choice(legal, min(200, legal.size), replace=False)
            lx, ly = legal % W, legal // W
            for L in lab:
                store.append(float((np.hypot(lx - L % W, ly - L // W) <= 16).mean()))
    return {"map": f"{name}/{which}", "W": W, "H": H, "land": nland, "updates": int(nupd),
            "upd_per_land": round(nupd / nland, 2), "ticks": nt,
            "B_per_upd": {"naive_cols_z3": round(naive / nupd, 2), "engine_pairs_z3": round(inter / nupd, 2),
                          "sorted_delta_z3": round(delta3 / nupd, 2), "sorted_delta_z19": round(delta19 / nupd, 2)},
            "stream_MB_z3_delta": round(delta3 / 1e6, 2), "stream_MB_z19_delta": round(delta19 / 1e6, 2),
            "keyframe_KB_z3": round(kf3 / 1024, 1), "keyframe_KB_z19": round(kf19 / 1024, 1),
            "py_reconstruct_s": round(rec_s, 2), "crops": res,
            "mask90x180_build_B": round(float(np.mean(mb))), "mask90x180_boat_B": round(float(np.mean(mbo))),
            "legal_counts(own_tiles,own_cells,boat_tiles,boat_cells)": legal_counts[:6],
            "uniform_legal_M16_build_median": round(float(np.median(mass16_build)), 4),
            "uniform_legal_M16_build_mean": round(float(np.mean(mass16_build)), 4),
            "uniform_legal_M16_boat_median": round(float(np.median(mass16_boat)), 4),
            "uniform_legal_M16_boat_mean": round(float(np.mean(mass16_boat)), 4)}

if __name__ == "__main__":
    for name, which in (("world", "map"), ("europe", "map"), ("korea", "map4x")):
        t = time.time(); r = run(name, which); r["wall_s"] = round(time.time() - t, 1)
        print(json.dumps(r))
