"""eval_spatial C3/C4 use map channel 2 ('eigen') after dequantize. materialize.ts quantises
x -> round((x+1)*127.5) (JS Math.round(127.5)=128), dataset.dequantize: u8/127.5-1.
So a NON-own cell (x=0) comes back as 128/127.5-1 = +0.00392, and clamp(min=0) keeps it.
This script shows the resulting weight of the 'map centre' in the C3 centroid."""
import numpy as np
x=np.array([-1.0,0.0,0.5,1.0])
u8=np.floor((x+1)*127.5+0.5)          # JS Math.round semantics for positives
deq=u8/127.5-1
print("x", x, "-> u8", u8, "-> dequant", np.round(deq,5))
eps=deq[1]; N=16200
for own_cells in (10,20,50,100,200,500,1000,3000):
    w_own=own_cells*deq[3]; w_bg=(N-own_cells)*eps
    print(f"own cells {own_cells:5d}: share of centroid weight from own cells {w_own/(w_own+w_bg):6.1%}  (rest = uniform map centre)")
