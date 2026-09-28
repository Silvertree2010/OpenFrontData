"""B: FLOPs for the grid options: 180x90 (today), 144x144 isotropic canvas with today's stem,
144x144 with a slimmer stem (64/96), plus the D0 head at 144x144 and the V2 288x288 output layer."""
import sys, os
sys.path.insert(0, "~/openfront-ai/env"); os.chdir("~/openfront-ai")
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.flop_counter import FlopCounterMode
sys.path.insert(0, "/tmp/openfront-ai/arbeit/amssp/scripts_B")
import net as N
def fl(mod, *a, **k):
    with FlopCounterMode(display=False) as fc: out = mod(*a, **k)
    return fc.get_total_flops() / 1e9, out
def npar(m): return sum(p.numel() for p in m.parameters()) / 1e6
class Stem(nn.Module):
    def __init__(s, c1, c2, cin=N.NUM_MAP_CH + 4):
        super().__init__(); s.s = nn.Sequential(nn.Conv2d(cin, c1, 3, padding=1), nn.GroupNorm(8, c1), nn.ReLU(), nn.Conv2d(c1, c2, 3, padding=1), nn.GroupNorm(8, c2), nn.ReLU())
        s.d = nn.Sequential(nn.Conv2d(c2, 192, 3, 2, 1), nn.GroupNorm(8, 192), nn.ReLU(), nn.Conv2d(192, 256, 3, 2, 1), nn.GroupNorm(8, 256), nn.ReLU(), nn.Conv2d(256, 320, 3, 2, 1), nn.GroupNorm(8, 320), nn.ReLU())
    def forward(s, x):
        s0 = s.s(x); d1 = s.d[2](s.d[1](s.d[0](s0))); d2 = s.d[5](s.d[4](s.d[3](d1))); d3 = s.d[8](s.d[7](s.d[6](d2))); return s0, d1, d2, d3


class SpatialHeadD0(nn.Module):
    """Query-conditioned U-Net-lite pointer over the 90x180 cells.
    q = MLP(core + emb(atype) + emb(unit_type) + target embedding)
    FiLM at the 12x23 bottleneck + one global self-attention layer (276 tokens),
    decoder 12x23 -> 23x45 -> 45x90 -> 90x180 with encoder skips,
    2 extra input planes at 90x180 (target-territory, legality), pointer logits = <Pq, f_c>."""
    def __init__(self, core=768, emb=160, dq=256, n_atype=21, n_unit=10, extra=2, s0c=128):
        super().__init__()
        self.ea = nn.Embedding(n_atype, 32); self.eu = nn.Embedding(n_unit + 1, 32)
        self.q = nn.Sequential(nn.Linear(core + 64 + emb, dq), nn.ReLU(), nn.Linear(dq, dq))
        self.film = nn.Linear(dq, 2 * 320)
        self.att_in = nn.Conv2d(320, 192, 1)
        self.att = nn.TransformerEncoderLayer(192, 4, 384, batch_first=True, dropout=0.0)
        self.up3 = nn.Sequential(nn.Conv2d(192 + 256, 128, 3, padding=1), nn.GroupNorm(8, 128), nn.ReLU())
        self.up2 = nn.Sequential(nn.Conv2d(128 + 192, 64, 3, padding=1), nn.GroupNorm(8, 64), nn.ReLU())
        self.up1 = nn.Sequential(nn.Conv2d(64 + s0c + extra, 64, 1), nn.ReLU(), nn.Conv2d(64, 64, 1))
        self.pq = nn.Linear(dq, 64)
        self.bias = nn.Conv2d(64, 1, 1)
    def forward(self, feats, core, atype, unit, tgt_emb, extra):
        s0, d1, d2, d3 = feats
        q = self.q(torch.cat([core, self.ea(atype), self.eu(unit), tgt_emb], 1))
        g, b = self.film(q).chunk(2, 1)
        x = d3 * (1 + g[:, :, None, None]) + b[:, :, None, None]
        x = self.att_in(x); Bn, C, h, w = x.shape
        x = self.att(x.flatten(2).transpose(1, 2)).transpose(1, 2).reshape(Bn, C, h, w)
        x = F.interpolate(x, size=d2.shape[-2:], mode="nearest")
        x = self.up3(torch.cat([x, d2], 1))
        x = F.interpolate(x, size=d1.shape[-2:], mode="nearest")
        x = self.up2(torch.cat([x, d1], 1))
        x = F.interpolate(x, size=s0.shape[-2:], mode="nearest")
        f = self.up1(torch.cat([x, s0, extra], 1))                 # (B,64,90,180)
        logits = torch.einsum("bchw,bc->bhw", f, self.pq(q)) + self.bias(f)[:, 0]
        return logits.flatten(1)


B = 1
core = torch.randn(B, 768); at = torch.zeros(B, dtype=torch.long); un = torch.zeros(B, dtype=torch.long); te = torch.randn(B, 160)
rows = []
for name, (h, w), (c1, c2) in [("180x90 today-stem", (90, 180), (96, 128)), ("144x144 today-stem", (144, 144), (96, 128)),
                               ("144x144 slim-stem", (144, 144), (64, 96)), ("180x180 today-stem", (180, 180), (96, 128))]:
    enc = Stem(c1, c2); x = torch.randn(B, N.NUM_MAP_CH + 4, h, w)
    fe, feats = fl(enc, x)
    head = SpatialHeadD0(s0c=c2); fh, lg = fl(head, feats, core, at, un, te, torch.randn(B, 2, h, w))
    rows.append((name, fe, fh, npar(enc), npar(head), lg.shape[1]))
base = 7.239   # measured current full Net forward GFLOP/sample (flops.out); non-map parts ~0.04
for name, fe, fh, pe, ph, nl in rows:
    tot = fe + 0.04
    print(f"{name:<22s} encoder {fe:6.2f} GFLOP ({pe:.2f} M)  D0 head {fh:5.2f} GFLOP ({ph:.2f} M, {nl} logits)  "
          f"train-cost ratio vs today (head on 37% spatial): x{(tot + 0.37*fh)/base:.2f}")
# V2: 288x288 output layer fed by D0 decoder at 144 upsampled x2 + 4 stored planes at 288
class V2Out(nn.Module):
    def __init__(s): super().__init__(); s.c = nn.Sequential(nn.Conv2d(64 + 4, 48, 1), nn.ReLU(), nn.Conv2d(48, 48, 1)); s.pq = nn.Linear(256, 48)
    def forward(s, f144, planes, q):
        f = s.c(torch.cat([F.interpolate(f144, scale_factor=2, mode="nearest"), planes], 1))
        return torch.einsum("bchw,bc->bhw", f, s.pq(q)).flatten(1)
v2 = V2Out(); fv, lv = fl(v2, torch.randn(B, 64, 144, 144), torch.randn(B, 4, 288, 288), torch.randn(B, 256))
print(f"V2 288x288 output layer: +{fv:.2f} GFLOP/sample, {npar(v2)*1e3:.1f} k params, {lv.shape[1]} logits")
