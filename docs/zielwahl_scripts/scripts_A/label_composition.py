"""Label composition of the 65 local records (data/raw): spatial intents by type,
build_unit by unit type, spawn-intent turn distribution, map/size/commit.
Read-only. Counts ALL human intents (incl. spawn phase) -> upper bound for spawn."""
import glob, json, collections
from compression import zstd
ROOT="~/openfront-ai/data/raw"
ty=collections.Counter(); unit=collections.Counter(); maps=collections.Counter(); commits=collections.Counter()
spawn_turns=[]; nrec=0; multi_ws=0; amount_gt1=collections.Counter()
for p in sorted(glob.glob(ROOT+"/*/*.json.zst")):
    j=json.loads(zstd.decompress(open(p,"rb").read())); nrec+=1
    cfg=j["info"]["config"]; maps[(cfg.get("gameMap"),cfg.get("gameMapSize"))]+=1; commits[(j.get("gitCommit") or "")[:9]]+=1
    for t in j["turns"]:
        for i in t.get("intents") or []:
            k=i.get("type"); ty[k]+=1
            if k=="build_unit":
                unit[i.get("unit")]+=1
                if (i.get("amount") or 1)>1: amount_gt1[i.get("unit")]+=1
            if k=="spawn": spawn_turns.append(t.get("turnNumber",0))
            if k=="move_warship" and len(i.get("unitIds") or [])>1: multi_ws+=1
spatial={k:ty[k] for k in ("build_unit","boat","spawn","move_warship")}
S=sum(spatial.values())
print("records",nrec,"commits",dict(commits))
print("maps",maps.most_common(12))
print("spatial intents",spatial,"total",S)
for k,v in spatial.items(): print(f"  {k:14s} {v:7d} {v/S:6.1%}")
B=sum(unit.values())
print("build_unit by unit (share of build / share of all spatial):")
for u,v in unit.most_common(): print(f"  {u:14s} {v:7d} {v/B:6.1%} {v/S:6.1%}")
nukes=sum(unit[u] for u in ("Atom Bomb","Hydrogen Bomb","MIRV"))
structs=sum(unit[u] for u in ("City","Port","Factory","Defense Post","Missile Silo","SAM Launcher"))
print(f"structures {structs} ({structs/S:.1%} of spatial), nukes {nukes} ({nukes/S:.1%}), warship-build {unit['Warship']} ({unit['Warship']/S:.1%})")
print("amount>1 by unit",dict(amount_gt1))
import numpy as np
st=np.array(spawn_turns); print("spawn intents turn quantiles",np.percentile(st,[5,50,95,99,100]) if len(st) else None, "n",len(st), ">=300:",int((st>=300).sum()), ">=600:", int((st>=600).sum()))
print("move_warship multi-unit",multi_ws)
