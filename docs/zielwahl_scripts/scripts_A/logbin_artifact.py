"""Is 'near or far, nothing in between' evidence of multimodality? Put smooth UNIMODAL 2D error
distributions through eval_spatial's log-spaced tile bins (0,1,2,3-4,5-8,9-16,17-32,>32 grid units)
and compare with the measured head histogram (2.7% exact ... 83.6% >32)."""
import numpy as np
rng=np.random.default_rng(0); N=400000
edges=[0,1,2,4,8,16,32]; labels=["0","1","2","3-4","5-8","9-16","17-32",">32"]
def hist(d):
    out=[];prev=-1.0
    for e in edges: out.append(((d>prev)&(d<=e)).mean()); prev=e
    out.append((d>32).mean()); return out
def show(name,d): print(f"{name:52s}"+"".join(f"{x:7.1%}" for x in hist(d))+f"   median {np.median(d):6.1f}")
print(" "*52+"".join(f"{l:>7s}" for l in labels))
print(f"{'MEASURED head (user, A in tiles=coarse*8)':52s}  2.7%  (bins 1,2,3-4 empty by construction)  3.5%   2.8%   7.4%  83.6%   median  136")
# errors are coarse-cell distances *8 -> only multiples of 8 on axes; emulate with cell-quantised gaussian
for s_cells in (5,10,17/1.1774,30):
    e=np.round(rng.normal(0,s_cells,(N,2)))            # unimodal isotropic, integer coarse cells
    show(f"unimodal Gaussian sigma={s_cells:5.1f} cells (x8)", np.hypot(e[:,0],e[:,1])*8)
# mixture: p right cell region else uniform over a territory-sized disk
for p in (0.03,0.10):
    k=rng.random(N)<p
    far=rng.normal(0,15,(N,2)); near=rng.normal(0,0.6,(N,2))
    e=np.round(np.where(k[:,None],near,far)); show(f"mixture {p:.0%} near(0.6) + rest wide(15 cells)", np.hypot(e[:,0],e[:,1])*8)
