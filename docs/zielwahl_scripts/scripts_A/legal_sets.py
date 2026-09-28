"""Legal-set sizes on REAL terrain (HEAD map files; recorded commits may use older map versions).
Own territory is SIMULATED as a BFS blob over passable land from a random land tile (compact
territory = optimistic for masking; real territories are less compact -> MORE cells).
Per blob size: structure-legal cells (cells with own tiles), port-legal cells, reachable foreign
ocean-shore cells (boat landing candidates, allied shores not removed = upper bound), cells within
50 tiles of such shore (where a boat CLICK still snaps to a landing tile), water cells (warship),
nuke cells. Plus 'mask + uniform pick' baselines in coarse cells (same metric as eval_spatial)."""
import numpy as np, json, collections, sys
from collections import deque
import torch, torch.nn.functional as Fn
ROOT="/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps/"
def load(mid, size):
    man=json.load(open(ROOT+mid+"/manifest.json"))
    d=man["map"] if size=="Normal" else man["map4x"]; fn="map.bin" if size=="Normal" else "map4x.bin"
    W,H=d["width"],d["height"]
    t=np.fromfile(ROOT+mid+"/"+fn,dtype=np.uint8); assert t.size==W*H,(t.size,W,H)
    return t.reshape(H,W),W,H
def nb_any(m):
    o=np.zeros_like(m); o[1:]|=m[:-1]; o[:-1]|=m[1:]; o[:,1:]|=m[:,:-1]; o[:,:-1]|=m[:,1:]; return o
def cells_of(mask,W,H):
    ys,xs=np.nonzero(mask); c=np.zeros((90,180),bool); c[(ys*90)//H,(xs*180)//W]=True; return c
def dilate(c,rx,ry):
    t=torch.from_numpy(c.astype(np.float32))[None,None]
    return (Fn.max_pool2d(t,(2*ry+1,2*rx+1),1,(ry,rx))[0,0]>0).numpy()
def blob(passable,W,H,N,rng):
    ys,xs=np.nonzero(passable); i=rng.integers(len(ys)); s=(ys[i],xs[i])
    own=np.zeros_like(passable); own[s]=True; q=deque([s]); n=1
    while q and n<N:
        y,x=q.popleft()
        for yy,xx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
            if 0<=yy<H and 0<=xx<W and passable[yy,xx] and not own[yy,xx]:
                own[yy,xx]=True; n+=1; q.append((yy,xx))
                if n>=N: break
    return own,n
def pair_stats(mask,W,H,rng,k=20000):
    ys,xs=np.nonzero(mask)
    if len(ys)==0: return (np.nan,np.nan)
    a=rng.integers(len(ys),size=k); b=rng.integers(len(ys),size=k)
    ca=((ys[a]*90)//H, (xs[a]*180)//W); cb=((ys[b]*90)//H,(xs[b]*180)//W)
    d=np.hypot(ca[0]-cb[0],ca[1]-cb[1])
    return float(np.median(d)), float((d==0).mean())
rng=np.random.default_rng(0)
MAPS=[("world","Normal"),("world","Compact"),("europe","Normal"),("branchingpaths","Normal"),("korea","Normal"),("giantworldmap","Normal")]
for mid,size in MAPS:
    t,W,H=load(mid,size)
    land=(t>>7)&1==1; ocean=((t>>5)&1==1)&~land; mag=t&31
    imp=land&(mag==31); passable=land&~imp
    oshore=passable&nb_any(ocean)                   # land touching ocean water
    cw,ch=W/180,H/90
    print(f"\n== {mid} {size} {W}x{H}  cell {cw:.1f}x{ch:.1f} tiles  land tiles {land.sum()}  impassable {imp.sum()}")
    print(f"   cells: any-land {cells_of(land,W,H).sum()}  ocean-water {cells_of(ocean,W,H).sum()}  ocean-shore-land {cells_of(oshore,W,H).sum()}  any-non-impassable {cells_of(~imp,W,H).sum()} of 16200")
    for N in (2000,20000,100000):
        if N>passable.sum()*0.8: continue
        res=collections.defaultdict(list)
        for s in range(3):
            own,n=blob(passable,W,H,N,rng)
            oc=cells_of(own,W,H)
            ownshore=own&oshore
            portc=dilate(cells_of(ownshore,W,H),int(np.ceil(20/cw)),int(np.ceil(20/ch))) & oc if ownshore.any() else np.zeros_like(oc)
            fshore=oshore&~own
            fsc=cells_of(fshore,W,H)
            click50=dilate(fsc,int(np.ceil(50/cw)),int(np.ceil(50/ch)))
            res["own"].append(oc.sum()); res["port"].append(portc.sum()); res["bshore"].append(fsc.sum()); res["bclick"].append(click50.sum())
            md,ph=pair_stats(own,W,H,rng); res["own_med"].append(md); res["own_hit"].append(ph)
            md2,ph2=pair_stats(fshore,W,H,rng); res["bs_med"].append(md2); res["bs_hit"].append(ph2)
        m=lambda k: np.median(res[k])
        print(f"   own {N:>6} tiles: structure-legal cells {m('own'):6.0f} | port-legal {m('port'):5.0f} | boat landing-shore cells {m('bshore'):5.0f} | boat click cells(<=50t) {m('bclick'):6.0f}"
              f" || mask+uniform: structure median {m('own_med'):5.1f} cells, P(exact) {m('own_hit'):5.1%}; boat-shore median {m('bs_med'):5.1f} cells, P(exact) {m('bs_hit'):5.2%}")
