"""Echter BC-Schritt-Test: Samples aus einem Shard laden, überfitten → Verlust fällt."""
import torch, sys, os, numpy as np
sys.path.insert(0, "env")
import net as N, bc_train as B, actions as AC, dataset as D
D.load_reputation("data/reputation.json")
shard_dir, gid = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 64
samples = []
for s in D.load_game(shard_dir, gid):
    samples.append(s)
    if len(samples) >= n: break
dev = "cuda" if torch.cuda.is_available() else "cpu"
mp = torch.tensor(np.stack([s["map"] for s in samples]), device=dev)
own = torch.tensor(np.stack([s["own"] for s in samples]), device=dev)
opp = torch.tensor(np.stack([s["opp"] for s in samples]), device=dev)
mask = torch.tensor(np.stack([s["mask"] for s in samples]), device=dev)
atypes = torch.tensor([s["atype"] for s in samples], dtype=torch.long, device=dev)
HEADS = list(AC.HEAD_SIZES.keys())
labels = {h: torch.zeros(len(samples), dtype=torch.long, device=dev) for h in HEADS}
for i, s in enumerate(samples):
    for h, v in s["label"].items():
        if h in labels: labels[h][i] = v
labels["value"] = torch.tensor([s["win"] for s in samples], dtype=torch.float32, device=dev)
net = N.Net().to(dev); opt = torch.optim.AdamW(net.parameters(), lr=3e-4)
print(f"{len(samples)} echte Samples auf {dev}, win={int(labels['value'].sum())}/{len(samples)}")
for step in range(80):
    out = net(mp, own, opp, mask, coarse_idx=labels["coarse"])
    loss, _ = B.masked_loss(out, labels, atypes)
    opt.zero_grad(); loss.backward(); opt.step()
    if step % 20 == 0 or step == 79:
        acc = B.accuracy(out, labels, atypes)
        key = sum(acc[h] for h in ("atype","target","coarse","magnitude") if h in acc) / 4
        print(f"  Schritt {step+1:>3}  Verlust {loss.item():7.3f}  Kern-Treffer {key:5.1%}")
