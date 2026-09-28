"""B: parameter and FLOP count of the current Net and of the proposed spatial heads.
CPU only, batch 1 (FLOPs are per sample). torch.utils.flop_counter counts matmul/conv FLOPs
(2 per MAC); elementwise ops (norms, activations, softmax) are NOT counted -> a lower bound.
"""
import sys, os, time
sys.path.insert(0, "~/openfront-ai/env")
os.chdir("~/openfront-ai")
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.flop_counter import FlopCounterMode
import net as N
import actions as AC

torch.manual_seed(0)
def count(mod, *args, **kw):
    with FlopCounterMode(display=False) as fc:
        out = mod(*args, **kw)
    return fc.get_total_flops(), out

def nparams(m):
    return sum(p.numel() for p in m.parameters())

B = 1
map_t = torch.randn(B, N.NUM_MAP_CH, N.GH, N.GW)
own_t = torch.randn(B, N.OWN_DIM); opp_t = torch.randn(B, N.MAX_OPP, N.OPP_DIM)
mask = torch.ones(B, N.MAX_OPP, dtype=torch.bool); cfg = torch.randn(B, N.CONFIG_DIM)
net = N.Net().train()
fl_net, _ = count(net, map_t, own_t, opp_t, mask, config_t=cfg)
fl_enc, _ = count(net.map, map_t)
fl_stem, _ = count(net.map.stem, map_t)
print(f"current Net: params {nparams(net)/1e6:.3f} M, forward {fl_net/1e9:.3f} GFLOP/sample "
      f"(MapEncoder {fl_enc/1e9:.3f}, of which stem@90x180 {fl_stem/1e9:.3f})")
print(f"  coarse_head params {nparams(net.coarse_head)}, fine_head params {nparams(net.fine_head)}")

# ----------------------------------------------------------------- proposed D0 head
class Enc(nn.Module):
    """Same encoder as net.MapEncoder but returns the skip features."""
    def __init__(self, base):
        super().__init__(); self.b = base
    def forward(self, m):
        s0 = self.b.stem(m)
        d = self.b.down
        d1 = d[2](d[1](d[0](s0)))
        d2 = d[5](d[4](d[3](d1)))
        d3 = d[8](d[7](d[6](d2)))
        return s0, d1, d2, d3

class SpatialHeadD0(nn.Module):
    """Query-conditioned U-Net-lite pointer over the 90x180 cells.
    q = MLP(core + emb(atype) + emb(unit_type) + target embedding)
    FiLM at the 12x23 bottleneck + one global self-attention layer (276 tokens),
    decoder 12x23 -> 23x45 -> 45x90 -> 90x180 with encoder skips,
    2 extra input planes at 90x180 (target-territory, legality), pointer logits = <Pq, f_c>."""
    def __init__(self, core=768, emb=160, dq=256, n_atype=21, n_unit=10, extra=2):
        super().__init__()
        self.ea = nn.Embedding(n_atype, 32); self.eu = nn.Embedding(n_unit + 1, 32)
        self.q = nn.Sequential(nn.Linear(core + 64 + emb, dq), nn.ReLU(), nn.Linear(dq, dq))
        self.film = nn.Linear(dq, 2 * 320)
        self.att_in = nn.Conv2d(320, 192, 1)
        self.att = nn.TransformerEncoderLayer(192, 4, 384, batch_first=True, dropout=0.0)
        self.up3 = nn.Sequential(nn.Conv2d(192 + 256, 128, 3, padding=1), nn.GroupNorm(8, 128), nn.ReLU())
        self.up2 = nn.Sequential(nn.Conv2d(128 + 192, 64, 3, padding=1), nn.GroupNorm(8, 64), nn.ReLU())
        self.up1 = nn.Sequential(nn.Conv2d(64 + 128 + extra, 64, 1), nn.ReLU(), nn.Conv2d(64, 64, 1))
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

enc = Enc(net.map)
feats = enc(map_t)
head = SpatialHeadD0()
core = torch.randn(B, 768); at = torch.zeros(B, dtype=torch.long); un = torch.zeros(B, dtype=torch.long)
te = torch.randn(B, 160); ex = torch.randn(B, 2, 90, 180)
fl_h, lg = count(head, feats, core, at, un, te, ex)
assert lg.shape == (B, 16200)
print(f"D0 spatial head: params {nparams(head)/1e6:.3f} M, forward {fl_h/1e9:.3f} GFLOP/sample, logits {tuple(lg.shape)}")

# ----------------------------------------------------------------- V1: fine stage on a full-res crop
class FineCrop(nn.Module):
    """Crop 24x24 tiles (true/pred coarse cell + 8 tile margin), C_f channels at 1:1,
    3 conv layers, FiLM from q, logits over the central 8x8 grid sub-cells (64) or, on maps with
    >8 tiles per cell, over the central cell's tiles pooled to 8x8."""
    def __init__(self, cf=8, dq=256, ch=48):
        super().__init__()
        self.c1 = nn.Conv2d(cf, ch, 3, padding=1); self.c2 = nn.Conv2d(ch, ch, 3, padding=1)
        self.c3 = nn.Conv2d(ch, ch, 3, padding=1)
        self.film = nn.Linear(dq, 2 * ch); self.out = nn.Conv2d(ch, 1, 1)
    def forward(self, crop, q):
        x = F.relu(self.c1(crop)); x = F.relu(self.c2(x))
        g, b = self.film(q).chunk(2, 1)
        x = F.relu(self.c3(x * (1 + g[:, :, None, None]) + b[:, :, None, None]))
        return self.out(x)[:, 0, 8:16, 8:16].flatten(1)            # central 8x8
fc = FineCrop()
fl_f, lf = count(fc, torch.randn(B, 8, 24, 24), torch.randn(B, 256))
print(f"V1 fine-crop stage: params {nparams(fc)/1e3:.1f} k, forward {fl_f/1e6:.2f} MFLOP/sample, logits {tuple(lf.shape)}")

# ----------------------------------------------------------------- V2: candidate pointer
class CandPtr(nn.Module):
    """K candidate tiles x Fc engineered features, 2-layer set transformer, pointer with q."""
    def __init__(self, fc_=24, d=128, dq=256):
        super().__init__()
        self.inp = nn.Linear(fc_ + 64, d)          # + 64-d sampled cell feature from the decoder
        self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, 4, 2 * d, batch_first=True, dropout=0.0), 2)
        self.pq = nn.Linear(dq, d)
    def forward(self, cand, q):
        h = self.enc(self.inp(cand))
        return torch.einsum("bkd,bd->bk", h, self.pq(q))
for K in (128, 256, 512):
    cp = CandPtr()
    fl_c, _ = count(cp, torch.randn(B, K, 24 + 64), torch.randn(B, 256))
    print(f"V2 candidate pointer K={K}: params {nparams(cp)/1e3:.1f} k, forward {fl_c/1e6:.1f} MFLOP/sample")

# ----------------------------------------------------------------- relative cost estimate
for share in (0.25, 0.35, 0.45):
    rel = (fl_net + share * fl_h) / fl_net
    print(f"training cost ratio vs current (spatial head only on spatial sub-batch, share {share:.0%}): x{rel:.2f} "
          f"-> if compute-bound: ~{800/rel:.0f} samples/s from 800")
print(f"training cost ratio if head ran on ALL samples: x{(fl_net+fl_h)/fl_net:.2f}")

# rough CPU wall-clock ratio (M-series CPU, NOT the 5080; only a sanity check of the FLOP ratio)
torch.set_num_threads(4)
xb = torch.randn(32, N.NUM_MAP_CH, N.GH, N.GW)
def wall(fn, n=3):
    fn(); t = time.time()
    for _ in range(n): fn()
    return (time.time() - t) / n
with torch.no_grad():
    t_net = wall(lambda: net(xb, own_t.expand(32, -1), opp_t.expand(32, -1, -1), mask.expand(32, -1), config_t=cfg.expand(32, -1)))
    fb = enc(xb)
    t_head = wall(lambda: head(fb, core.expand(32, -1), at.expand(32), un.expand(32), te.expand(32, -1), ex.expand(32, -1, -1, -1)))
print(f"CPU wall (batch 32, 4 threads, fwd only): net {t_net*1000:.0f} ms, D0 head {t_head*1000:.0f} ms, ratio {t_head/t_net:.2f}")
