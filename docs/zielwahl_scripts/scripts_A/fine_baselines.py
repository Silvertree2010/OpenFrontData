"""Theoretical fine-head baselines inside one 8x8 coarse cell, label uniform over 64 fine cells.
Reproduces eval_spatial C5 (random) and C6 (centre=(4,4)) analytically and shows what
a CONSTANT argmax at each fine cell would score. If B2 (4.8) equals some fixed off-centre
cell, 'worse than random' is explained by argmax-of-a-flat-distribution, not by harm."""
import numpy as np
g=np.array([(x,y) for y in range(8) for x in range(8)],float)
D=np.hypot(g[:,None,0]-g[None,:,0], g[:,None,1]-g[None,:,1])   # 64x64
rand=D.mean()
per_cell=D.mean(1).reshape(8,8)
print(f"random fine vs uniform label: {rand:.3f}")
print(f"centre (4,4) idx36: {per_cell[4,4]:.3f}   true centre (3.5,3.5): {np.hypot(g[:,0]-3.5,g[:,1]-3.5).mean():.3f}")
print("constant-prediction mean distance per fine cell (rows=y):")
print(np.round(per_cell,2))
print("cells with mean dist in [4.6,5.0]:", [(i%8,i//8,round(v,2)) for i,v in enumerate(per_cell.ravel()) if 4.6<=v<=5.0])
print(f"corner (0,0): {per_cell[0,0]:.3f}  (7,7): {per_cell[7,7]:.3f}")
