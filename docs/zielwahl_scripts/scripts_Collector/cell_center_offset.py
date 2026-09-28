"""Mean distance of a uniform point in a w x h cell to the cell centre (Monte Carlo, 1e6)."""
import numpy as np
rng=np.random.default_rng(0)
for w,h,lab in ((10.0,15.29,"180x90 median (games)"),(11.4,17.2,"180x90 median (labels, 65 rec)"),(17.8,25.3,"180x90 p95"),(5.0,7.6,"360x180 median"),(15,15,"144 canvas median")):
    x=(rng.random(1_000_000)-.5)*w; y=(rng.random(1_000_000)-.5)*h; d=np.hypot(x,y)
    print(f"{lab:32s} {w:5.1f}x{h:5.1f}: mean {d.mean():5.2f}  p90 {np.quantile(d,.9):5.2f}  max {np.hypot(w,h)/2:5.2f}")
